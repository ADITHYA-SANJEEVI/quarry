"""Bounded, resumable matrix runner shared by HTTP and offline CLI.

Scoring includes global context, all lexical features and global RRF. Full
includes both Commerce Ranker calls, adequacy and isolated compiler/decision.
Untimed exact preflight is kept separate from timed throughput measurements.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict
from hashlib import sha256
import csv
import io
import json
import os
from pathlib import Path
import pickle
import subprocess
from threading import Lock
from time import perf_counter

import psutil

from app.schemas.experiments import ExperimentConfig
from app.schemas.hpc import SearchExecutionRequest
from app.services.constraints import ConstraintEngine
from app.services.hpc.contracts import ComputeError, VERSION
from app.services.hpc.demo_search import downstream, make_service, result_digest
from app.services.hpc.experiment_store import ExperimentStore, now
from app.services.hpc.integration import ComputeRelevanceAdapter
from app.services.hpc.metrics import ResourceSampler, aggregate, environment
from app.services.hpc.runtime import ProcessRuntime, benchmark_capacity
from app.services.hpc.snapshot import canonical_json
from app.services.intent_parser import FallbackIntentParser
from app.services.relevance import DeterministicRelevanceAdapter


def source_identity():
    backend = Path(__file__).resolve().parents[3]
    digest = sha256()
    for path in sorted((backend / "app").rglob("*.py")):
        # Git's Windows CRLF checkout must not make identical Linux source
        # appear to be a different algorithm. Paths and LF text are hashed.
        digest.update(path.relative_to(backend).as_posix().encode()); digest.update(path.read_text(encoding="utf-8").encode())
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=backend, stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.SubprocessError):
        revision = "unavailable"
    return {"algorithm_version": VERSION, "source_sha256": digest.hexdigest(), "git_revision": revision,
            "semantic_mode": "disabled_deterministic_lexical", "benchmark_version": "hpc_experiment_v1"}


@contextmanager
def owner_lock(root):
    """One OS process owns a storage root; stale ownership is recoverable."""
    path = Path(root) / ".owner.json"
    identity = {"pid": os.getpid(), "created": psutil.Process().create_time()}
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as stream:
                json.dump(identity, stream)
            break
        except FileExistsError:
            try:
                previous = json.loads(path.read_text())
                alive = psutil.Process(previous["pid"]).create_time() == previous["created"]
            except psutil.NoSuchProcess:
                alive = False
            except (ValueError, KeyError, psutil.AccessDenied):
                # An unreadable owner is never assumed dead.
                raise RuntimeError("experiment_store_owner_unknown")
            if alive:
                raise RuntimeError("experiment_store_busy")
            path.unlink()
    else:
        raise RuntimeError("experiment_store_busy")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def relevance_digest(rows):
    digest = sha256()
    for r in rows:
        digest.update(canonical_json([r.product.id, r.lexical_score.hex(), r.fuzzy_score.hex(),
            r.character_score.hex(), r.phrase_score.hex(), r.fused_score.hex(), r.matched_terms,
            r.repaired_terms, r.semantic_rank, r.retrieval_prior, r.retrieval_mode]).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def invoke(catalogue, intent, mode, scope, workers, runtime, validate=False):
    started = perf_counter()
    execution = SearchExecutionRequest(mode="parallel", workers=workers)
    if scope == "full":
        service, ranker = make_service(catalogue, execution if mode == "parallel" else None, runtime)
        result = service.search(None, intent)
        decisions = downstream(result, intent)
        elapsed = (perf_counter()-started)*1000
        metadata = service.compute.metadata.model_dump() if mode == "parallel" else {}
        metadata["commerce_calls"] = ranker.calls
        count = len(result.eligible_products)
        signature = result_digest(result, ranker, decisions) if validate else None
    else:
        eligible = ConstraintEngine().pre_retrieval(catalogue.products, intent).eligible
        if mode == "reference":
            result = DeterministicRelevanceAdapter().retrieve(eligible, intent)
            metadata = {}
        else:
            adapter = ComputeRelevanceAdapter(execution, runtime, immutable_state=catalogue)
            adapter.bind_catalogue(catalogue.products)
            result = adapter.retrieve(eligible, intent)
            metadata = adapter.metadata.model_dump()
        elapsed = (perf_counter()-started)*1000
        count = len(result)
        signature = relevance_digest(result) if validate else None
    partitions = metadata.get("worker_execution_ms", [])
    metadata["partition_imbalance_max_over_mean"] = max(partitions)/(sum(partitions)/len(partitions)) if partitions and sum(partitions) else None
    return {"latency_ms": elapsed, "execution": metadata, "eligible_count": count, "digest": signature}


def export_csv(run):
    output = io.StringIO(newline="")
    fields = ["run_id", "cell", "size", "workers", "concurrency", "scope", "phase", "mode", "trial", "query_index", "latency_ms", "eligible_count", "parity", "error"]
    writer = csv.DictWriter(output, fieldnames=fields); writer.writeheader()
    for cell in run["cells"]:
        for sample in cell["observations"]:
            row = {k: sample.get(k) for k in fields}
            row.update(run_id=run["id"], cell=cell["key"], size=cell["size"], workers=cell["workers"], concurrency=cell["concurrency"], scope=cell["scope"])
            writer.writerow(row)
    return output.getvalue()


def compatible(a, b):
    keys = ("algorithm_version", "source_sha256", "semantic_mode", "benchmark_version")
    return (a["workload_sha256"] == b["workload_sha256"]
            and [(d["dataset_id"], d["sha256"]) for d in a["datasets"]] == [(d["dataset_id"], d["sha256"]) for d in b["datasets"]]
            and all(a["environment"].get(k) == b["environment"].get(k) for k in keys)
            and a["environment"]["python"].split(".")[:2] == b["environment"]["python"].split(".")[:2]
            and a["environment"]["packages"] == b["environment"]["packages"]
            and all(a["config"][k] == b["config"][k] for k in ("trials", "warmups", "queries"))
            and a["config"].get("research") == b["config"].get("research"))


def approved_telemetry_continuation(previous, current):
    """One explicit release bridge, guarded by business source + golden hashes.

    Old completed cells keep their source epoch. This never makes observations
    from different source epochs comparable or admits later arbitrary edits.
    """
    backend = Path(__file__).resolve().parents[3]
    path = backend / "data/hpc/research-upgrade-v1.json"
    if not path.exists():
        return False
    guard = json.loads(path.read_text())
    return (previous == guard["legacy_source"] and current == guard.get("release_source")
            and all(sha256((backend / "app" / name).read_text(encoding="utf-8").encode()).hexdigest() == digest
                    for name, digest in guard["guarded_files"].items())
            and sha256((backend / "tests/fixtures/serial_search_golden.json").read_bytes()).hexdigest() == guard["golden_sha256"])


class ExperimentRunner:
    def __init__(self, store, registry):
        self.store, self.registry = store, registry

    def create(self, config):
        manifests = {m["count"]: m for m in self.registry.manifests()}
        if any(n not in manifests for n in config.sizes):
            raise ValueError("dataset_not_generated")
        metadata = environment() | source_identity()
        if config.research is not None and config.research.semantic == "server_configured":
            from app.core.config import get_settings
            from importlib.metadata import version, PackageNotFoundError
            settings = get_settings()
            if not settings.semantic_retrieval_enabled or config.scopes != ["full"]:
                raise ValueError("semantic_research_requires_enabled_server_model_and_full_scope")
            metadata.update(semantic_mode="server_configured_optional", semantic_model=settings.semantic_retrieval_model,
                            semantic_local_files_only=settings.semantic_retrieval_local_files_only,
                            semantic_candidate_k=settings.semantic_retrieval_candidate_k)
            try:
                metadata["semantic_package"] = version("sentence-transformers")
            except PackageNotFoundError:
                metadata["semantic_package"] = "unavailable"
        return self.store.create(config, metadata, [manifests[n] for n in config.sizes])

    def _save(self, run):
        with self.store.lock:
            # Do not overwrite a cancellation accepted while computation ran.
            current = self.store.get(run["id"])
            run["cancel_requested"] = current["cancel_requested"]
            run["updated_at"] = now()
            run["progress"]["completed_cells"] = sum(c["status"] == "completed" for c in run["cells"])
            self.store.save(run)

    def execute(self, run_id):
        with owner_lock(self.store.root), benchmark_capacity():
            self.store.recover()
            run = self.store.get(run_id)
            if run["status"] in ("completed", "cancelled"):
                return run
            config = ExperimentConfig(**run["config"])
            identity = source_identity()
            previous_source = run.get("continuation_source", run["environment"]["source_sha256"])
            if identity["source_sha256"] != previous_source and approved_telemetry_continuation(previous_source, identity["source_sha256"]):
                run.setdefault("source_epochs", []).append({"from": previous_source, "to": identity["source_sha256"],
                    "at": now(), "reason": "guarded_research_instrumentation_upgrade"})
                run["continuation_source"] = identity["source_sha256"]
                for cell in run["cells"]:
                    cell.setdefault("source_sha256", previous_source)
                    if cell["status"] != "completed" and (cell["observations"] or cell["trials"]):
                        cell.setdefault("previous_attempts", []).append({"source_sha256": previous_source,
                            "observations": cell["observations"], "trials": cell["trials"], "validation": cell.get("validation", {})})
                        cell["observations"], cell["trials"], cell["validation"] = [], [], {}
            if identity["source_sha256"] != run.get("continuation_source", run["environment"]["source_sha256"]):
                run["status"] = "failed"; run["finished_at"] = now()
                run["errors"].append({"category": "source_changed_create_new_run", "at": now()})
                self.store.save(run)
                return run
            current_environment = environment()
            if config.research is not None and config.research.semantic == "server_configured":
                from app.core.config import get_settings
                settings = get_settings()
                if (not settings.semantic_retrieval_enabled or settings.semantic_retrieval_model != run["environment"].get("semantic_model")
                        or settings.semantic_retrieval_local_files_only != run["environment"].get("semantic_local_files_only")
                        or settings.semantic_retrieval_candidate_k != run["environment"].get("semantic_candidate_k")):
                    run["status"] = "failed"; run["finished_at"] = now()
                    run["errors"].append({"category":"semantic_configuration_changed_create_new_run","at":now()})
                    self.store.save(run)
                    return run
            stable_keys = ("python","packages","platform","logical_cpus","physical_cpus","effective_cpus","affinity","ram_total_mb")
            if any(current_environment[k] != run["environment"][k] for k in stable_keys):
                run["status"] = "failed"; run["finished_at"] = now()
                run["errors"].append({"category": "environment_changed_create_new_run", "at": now()})
                self.store.save(run)
                return run
            run["status"] = "running"; run["started_at"] = run["started_at"] or now()
            run["finished_at"] = None
            session = {"started_at": now(), "environment": current_environment, "cells_completed": 0}
            run["sessions"].append(session); self._save(run)
            deadline = perf_counter() + config.session_budget_seconds
            intents = [FallbackIntentParser().parse(q) for q in config.queries]
            run["accepted_intents"] = [i.model_dump(mode="json") for i in intents]
            effective = session["environment"]["effective_cpus"]
            try:
                for cell in run["cells"]:
                    self._save(run)
                    if run["cancel_requested"] or cell["status"] == "completed":
                        continue
                    if perf_counter() >= deadline or session["cells_completed"] >= config.max_cells_per_session:
                        break
                    if cell["workers"] > effective:
                        cell["status"] = "failed"
                        cell["errors"].append({"category": "worker_count_exceeds_effective_cpu_allocation", "at": now()})
                        continue
                    if cell["status"] == "failed":
                        cell.setdefault("previous_attempts", []).append({"at": now(), "observations": cell["observations"],
                            "trials": cell["trials"], "errors": cell["errors"], "validation": cell.get("validation", {})})
                        cell["observations"], cell["trials"], cell["errors"], cell["validation"] = [], [], [], {}
                    self._cell(run, cell, config, intents, deadline)
                    if cell["status"] == "completed":
                        session["cells_completed"] += 1
                    if cell["status"] == "failed":
                        break
                if run["cancel_requested"]:
                    run["status"] = "cancelled"; run["finished_at"] = now()
                elif all(c["status"] == "completed" for c in run["cells"]):
                    run["status"] = "completed"; run["finished_at"] = now()
                elif any(c["status"] == "failed" for c in run["cells"]):
                    run["status"] = "failed"; run["finished_at"] = now()
                else:
                    run["status"] = "queued"  # Requires explicit resume, not silent background continuation.
            except Exception as error:
                run["status"] = "failed"; run["finished_at"] = now()
                category = str(error) if isinstance(error, ComputeError) else type(error).__name__
                run["errors"].append({"category": category, "at": now()})
                for cell in run["cells"]:
                    if cell["status"] == "running":
                        cell["status"] = "failed"
                        if hasattr(error,"trace"):
                            cell["failure_trace"] = error.trace
                        cell["errors"].append({"category": category, "at": now()})
            finally:
                session["finished_at"] = now(); self._save(run)
            return run

    def _cell(self, run, cell, config, intents, deadline):
        cell["source_sha256"] = run.get("continuation_source", run["environment"]["source_sha256"])
        cell["status"] = "running"; self._save(run)
        catalogue = self.registry.get(f"studio-{cell['size']}-v1")
        expected_manifest = next(d for d in run["datasets"] if d["count"] == cell["size"])
        if catalogue.manifest != expected_manifest:
            raise ValueError("dataset_changed_create_new_run")
        started = perf_counter(); snapshot = catalogue.snapshot
        snapshot_access_ms = (perf_counter()-started)*1000
        started = perf_counter(); encoded = pickle.dumps(snapshot, protocol=pickle.HIGHEST_PROTOCOL)
        serialization_ms, serialized_bytes = (perf_counter()-started)*1000, len(encoded)
        del encoded
        # Snapshot copies plus interpreter overhead are estimated conservatively.
        # Exact process-tree RSS and provider initialization are sampled below.
        started = perf_counter()
        largest_eligible = max(len(ConstraintEngine().pre_retrieval(catalogue.products,intent).eligible) for intent in intents)
        eligibility_ms = (perf_counter()-started)*1000
        # Include simultaneous coordinator result/evidence state, not just the
        # worker pool. Reject unsafe c=32 cells rather than relabel fewer callers.
        per_query_mb = largest_eligible * (4096 if cell["scope"] == "full" else 2048) / 2**20
        coordinator_mb = per_query_mb * cell["concurrency"]
        estimated_mb = psutil.Process().memory_info().rss/2**20 + cell["workers"] * (serialized_bytes/2**20*5 + 55) + coordinator_mb
        cell["profile"] = {"catalogue_load_ms": catalogue.load_ms, "snapshot_generation_hash_ms": catalogue.snapshot_ms,
                           "snapshot_access_ms": snapshot_access_ms, "serialization_ms": serialization_ms,
                           "serialized_bytes": serialized_bytes, "estimated_pool_tree_mb": estimated_mb,
                           "largest_preeligible_count": largest_eligible, "eligibility_preflight_ms": eligibility_ms,
                           "estimated_concurrent_coordinator_mb": coordinator_mb}
        needed_mb = max(0, estimated_mb - psutil.Process().memory_info().rss/2**20) + 512
        if estimated_mb > config.memory_limit_mb or psutil.virtual_memory().available/2**20 < needed_mb:
            cell["status"] = "failed"
            cell["errors"].append({"category": "memory_preflight_limit", "at": now(), "estimated_mb": estimated_mb})
            self._save(run); return
        # One pool for the whole cell. c concurrent callers share p physical workers.
        with ProcessRuntime(max_workers=cell["workers"], timeout_seconds=config.query_timeout_seconds,
                            queue_timeout_seconds=config.query_timeout_seconds*32, max_pending=32) as runtime:
            def execute(index, mode, validate=False):
                if config.research is None:
                    return invoke(catalogue, intents[index], mode, cell["scope"], cell["workers"], runtime, validate=validate)
                from app.services.hpc.research_execution import execute_research
                return execute_research(catalogue, config.queries[index], mode, cell["workers"], runtime,
                    cell["source_sha256"], run_id=run["id"], concurrency=cell["concurrency"], scope=cell["scope"],
                    validate=validate, keep_trace=validate, semantic=config.research.semantic)
            for mode in ("reference", "parallel"):
                for index, intent in enumerate(intents):
                    if self._stop(run, deadline):
                        cell["status"] = "queued"; return
                    validation_key = f"{mode}/{index}"
                    # Re-validate on every session: a replacement pool must be
                    # warm and verified even when earlier trials were saved.
                    with ResourceSampler(config.memory_limit_mb) as sampler:
                        row = execute(index, mode, validate=True)
                    cell.setdefault("validation", {})[validation_key] = row
                    row["resources"] = sampler.summary
                    row["resource_samples"] = sampler.samples
                    if sampler.exceeded.is_set():
                        raise MemoryError("memory_limit")
                    if mode == "parallel" and row["digest"] != cell["validation"][f"reference/{index}"]["digest"]:
                        cell["status"] = "failed"
                        cell["errors"].append({"category": "exact_result_parity_failed", "query_index": index, "at": now()})
                        self._save(run); return
                    if "trace" in row:
                        row["trace"]["correctness_status"] = "exact_match" if mode == "parallel" else "reference_captured"
                        row["trace"]["measurements"]["snapshot"] = cell["profile"]
                        row["trace"]["measurements"]["resources"] = row["resources"]
                        row["trace"]["environment_metadata"].update(run["environment"])
                    self._save(run)
                # Preflight acts as the first warmup. Additional requested
                # warmups are untimed and explicit, including on a resume.
                for _ in range(max(0, config.warmups-1)):
                    for index, intent in enumerate(intents):
                        if self._stop(run, deadline):
                            cell["status"] = "queued"; return
                        execute(index, mode)
                for trial in range(config.trials):
                    if any(t["mode"] == mode and t["trial"] == trial for t in cell["trials"]):
                        continue
                    if self._stop(run, deadline):
                        cell["status"] = "queued"; return
                    count = max(len(intents), cell["concurrency"])
                    count = ((count + len(intents)-1)//len(intents))*len(intents)
                    def query(job):
                        index = job % len(intents)
                        try:
                            if sampler.exceeded.is_set():
                                raise ComputeError("memory_limit")
                            result = execute(index, mode)
                            result.pop("digest")
                            return result | {"phase": "timed", "mode": mode, "trial": trial, "query_index": index,
                                             "job": job, "error": None, "parity": True,
                                             "parity_scope": "exact_untimed_preflight_same_immutable_inputs"}
                        except Exception as error:
                            return {"phase": "timed", "mode": mode, "trial": trial, "query_index": index, "job": job,
                                    "latency_ms": None, "error": str(error) if isinstance(error, ComputeError) else type(error).__name__, "parity": False,
                                    "failure_trace":getattr(error,"trace",None)}
                    with ResourceSampler(config.memory_limit_mb) as sampler:
                        started = perf_counter()
                        with ThreadPoolExecutor(max_workers=cell["concurrency"]) as callers:
                            futures = [callers.submit(query, job) for job in range(count)]
                            samples = [future.result() for future in as_completed(futures)]
                        wall = perf_counter()-started
                    cell["observations"].extend(sorted(samples, key=lambda s: s["job"]))
                    cell["trials"].append({"mode": mode, "trial": trial, "wall_seconds": wall, "query_count": count,
                                           "resources": sampler.summary, "resource_samples": sampler.samples})
                    cell["statistics"] = aggregate(cell["observations"], cell["trials"], cell["workers"])
                    self._save(run)
                    if sampler.exceeded.is_set() or any(s["error"] for s in samples):
                        cell["status"] = "failed"
                        cell["errors"].append({"category": "memory_limit" if sampler.exceeded.is_set() else "query_errors", "at": now()})
                        return
        cell["status"] = "completed"; self._save(run)

    def _stop(self, run, deadline):
        self._save(run)
        return run["cancel_requested"] or perf_counter() >= deadline


class ExperimentManager:
    """One bounded background job at a time; server uses one ASGI worker."""
    def __init__(self, runner):
        self.runner = runner
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hpc-experiment")
        self.lock, self.pending = Lock(), {}
        try:
            with owner_lock(runner.store.root):
                runner.store.recover()
        except RuntimeError:
            pass  # Another live owner may be running the offline CLI.

    def submit(self, run_id):
        with self.lock:
            self.pending = {key:value for key,value in self.pending.items() if not value.done()}
            if run_id in self.pending and not self.pending[run_id].done():
                raise ValueError("run_already_scheduled")
            if sum(not f.done() for f in self.pending.values()) >= 4:
                raise ValueError("experiment_queue_full")
            self.pending[run_id] = self.executor.submit(self.runner.execute, run_id)
            def failed(future):
                error = future.exception() if not future.cancelled() else None
                if error is not None:
                    with self.runner.store.lock:
                        run = self.runner.store.get(run_id)
                        run["status"] = "failed"; run["finished_at"] = now()
                        run["errors"].append({"category": type(error).__name__, "at": now()})
                        self.runner.store.save(run)
            self.pending[run_id].add_done_callback(failed)

    def shutdown(self):
        # Checkpoint/cancel accepted active runs before joining bounded tasks.
        with self.lock:
            for run_id, future in self.pending.items():
                if not future.done():
                    self.runner.store.cancel(run_id)
        self.executor.shutdown(wait=True, cancel_futures=True)
