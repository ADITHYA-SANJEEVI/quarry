"""One bounded persistent spawn pool; reconfigure only for generation/size changes.

Feature batches are serialized at admission to keep generation transitions
atomic and CPU/memory use bounded. Concurrent HTTP requests queue explicitly.
"""
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
import os
import pickle
from dataclasses import asdict
from queue import Empty
from threading import BoundedSemaphore, Lock
from threading import Event
from contextlib import contextmanager
from time import perf_counter, perf_counter_ns
from uuid import uuid4

from app.services.hpc.contracts import ComputeError, FeatureTask, VERSION
from app.services.hpc.engine import validate_rows
from app.services.hpc.fair_gate import FairGate
from app.services.hpc.workers import initialize_worker, score_task, worker_ready
from app.services.hpc.tracing import active_trace, stage


def effective_cpu_count():
    from app.services.hpc.metrics import cpu_allocation
    return cpu_allocation()["effective_cpus"]


def balanced_partitions(ordinals, workers):
    if workers not in (1, 2, 4, 8):
        raise ComputeError("unsupported_worker_count")
    quotient, remainder = divmod(len(ordinals), workers)
    result, offset = [], 0
    for i in range(workers):
        size = quotient + (i < remainder)
        result.append(tuple(ordinals[offset:offset + size]))
        offset += size
    return tuple(result)


def validate_batches(snapshot, context, tasks, batches, pids):
    if len(tasks) != len(batches):
        raise ComputeError("batch_count_mismatch")
    expected = {t.partition: t for t in tasks}
    seen, rows = set(), []
    for batch in batches:
        task = expected.get(batch.partition)
        if task is None or batch.partition in seen:
            raise ComputeError("duplicate_or_unknown_partition")
        seen.add(batch.partition)
        if (batch.version != VERSION or batch.generation != snapshot.generation
                or batch.context_id != context.context_id or batch.request_id != task.request_id):
            raise ComputeError("stale_batch")
        if batch.pid not in pids or tuple(r.ordinal for r in batch.rows) != task.ordinals:
            raise ComputeError("invalid_batch_membership")
        rows.extend(batch.rows)
    validate_rows(snapshot, context, rows)
    by_ordinal = {r.ordinal: r for r in rows}
    return tuple(by_ordinal[i] for i in context.ordinals)


class ProcessRuntime:
    def __init__(self, max_workers=None, *, timeout_seconds=30.0, queue_timeout_seconds=10.0, max_pending=8):
        self.max_workers = min(8, max(1, effective_cpu_count() - 1)) if max_workers is None else max_workers
        if not 1 <= self.max_workers <= effective_cpu_count():
            raise ValueError("cpu_budget_exceeds_available_cpus")
        self.timeout_seconds = timeout_seconds
        self.queue_timeout_seconds = queue_timeout_seconds
        self._pending = BoundedSemaphore(max_pending)
        self._lock = FairGate()
        self._pool = self._ready_queue = None
        self._key = None
        self._pids = ()
        self._ready_timestamps = {}
        self._closed = False

    def validate_workers(self, workers):
        if workers not in (1, 2, 4, 8) or workers > self.max_workers:
            raise ComputeError("worker_count_exceeds_cpu_budget")

    def _discard_pool(self, force=False):
        pool, ready_queue = self._pool, self._ready_queue
        self._pool = self._ready_queue = None
        self._key, self._pids = None, ()
        self._ready_timestamps = {}
        if pool is not None:
            if force:
                # CPython 3.12 lacks public terminate_workers. Only terminate
                # children owned by this executor; never kill by process name.
                for process in list((getattr(pool, "_processes", None) or {}).values()):
                    if process.is_alive():
                        process.terminate()
            pool.shutdown(wait=True, cancel_futures=True)
        if ready_queue is not None:
            ready_queue.close()
            ready_queue.join_thread()

    def _ensure_pool(self, snapshot, workers):
        if self._key == (snapshot.generation, workers):
            return 0.0
        self._discard_pool()
        started = perf_counter()
        context = multiprocessing.get_context("spawn")
        self._ready_queue = context.Queue()
        startup_barrier = context.Barrier(workers)
        self._pool = ProcessPoolExecutor(max_workers=workers, mp_context=context,
            initializer=initialize_worker,
            initargs=(snapshot, self._ready_queue, startup_barrier, self.timeout_seconds))
        probes = [self._pool.submit(worker_ready) for _ in range(workers)]
        deadline = started + self.timeout_seconds
        ready = [self._ready_queue.get(timeout=max(0.0, deadline - perf_counter())) for _ in range(workers)]
        for future in probes:
            future.result(timeout=max(0.0, deadline - perf_counter()))
        if len({p[0] for p in ready}) != workers or any(p[1:3] != (snapshot.generation, VERSION) for p in ready):
            raise ComputeError("worker_initialization_mismatch")
        self._pids = tuple(sorted(p[0] for p in ready))
        self._ready_timestamps = {p[0]:p[3] for p in ready}
        trace = active_trace.get()
        if trace:
            for pid, _, _, timestamp in ready:
                trace.add("worker_ready", timestamp, timestamp, pid=pid, worker=True,
                          generation=snapshot.generation, clock_domain="same_host_perf_counter_ns")
        self._key = (snapshot.generation, workers)
        return (perf_counter() - started) * 1000

    def score(self, snapshot, context, workers):
        trace = active_trace.get()
        admission_ns = perf_counter_ns()
        self.validate_workers(workers)
        if snapshot.version != VERSION or context.version != VERSION:
            raise ComputeError("contract_version_mismatch")
        if context.generation != snapshot.generation:
            raise ComputeError("generation_mismatch")
        admitted = perf_counter()
        if not self._pending.acquire(timeout=self.queue_timeout_seconds):
            raise ComputeError("admission_timeout")
        acquired = False
        try:
            remaining = max(0.0, self.queue_timeout_seconds - (perf_counter() - admitted))
            acquired = self._lock.acquire(timeout=remaining)
            if not acquired:
                raise ComputeError("queue_timeout")
            if self._closed:
                raise ComputeError("runtime_closed")
            queue_ms = (perf_counter() - admitted) * 1000
            if trace:
                trace.add("admission_queue", admission_ns, perf_counter_ns())
            with stage("pool_initialization", cache_hit=self._key == (snapshot.generation, workers)):
                startup_ms = self._ensure_pool(snapshot, workers)
            started = perf_counter()
            request_id = uuid4().hex
            tasks = [FeatureTask(request_id, i, snapshot.generation, context, partition, perf_counter_ns(), instrument=bool(trace))
                     for i, partition in enumerate(balanced_partitions(context.ordinals, workers)) if partition]
            if trace:
                with stage("task_serialization_probe") as span:
                    span.attributes["payload_bytes"] = [len(pickle.dumps(t, protocol=pickle.HIGHEST_PROTOCOL)) for t in tasks]
                    span.attributes["kind"] = "measured_probe_not_executor_transport"
            with stage("dispatch"):
                futures = [self._pool.submit(score_task, task) for task in tasks]
            batches = []
            with stage("gather_wait"):
                for future in as_completed(futures, timeout=self.timeout_seconds):
                    batch = future.result()
                    received = perf_counter_ns()
                    batches.append(batch)
                    if trace:
                        task = next(t for t in tasks if t.partition == batch.partition)
                        if not task.submitted_ns <= batch.started_ns <= batch.ended_ns <= received:
                            raise ComputeError("worker_clock_interval_invalid")
                        trace.add("worker_queue", task.submitted_ns, batch.started_ns, pid=batch.pid, worker=True,
                                  partition=batch.partition, kind="submit_to_execution_includes_probe_and_transport")
                        trace.add("worker_features", batch.started_ns, batch.ended_ns, pid=batch.pid, worker=True,
                                  partition=batch.partition, ordinals=list(task.ordinals), candidate_in=len(task.ordinals),
                                  candidate_out=len(batch.rows), cpu_ns=batch.cpu_ns, rss_bytes=batch.rss_bytes,
                                  result_serialized_bytes=batch.result_serialized_bytes,
                                  result_serialization_ns=batch.result_serialization_ns,
                                  result_size_kind="protocol5_probe_before_measurement_fields; not_executor_wire_bytes",
                                  feature_preview=[asdict(r) for r in batch.rows[:8]])
                        trace.add("result_return_residual", batch.ended_ns, received, worker=True, pid=batch.pid,
                                  kind="inferred_includes_serialization_transport_and_scheduling", partition=batch.partition)
            with stage("result_validation"):
                rows = validate_batches(snapshot, context, tasks, batches, self._pids)
            if trace:
                trace.trace.worker_pids = list(self._pids)
                trace.trace.physical_pool_size = workers
                trace.trace.measurements["worker_ready_absolute_perf_ns"] = self._ready_timestamps.copy()
                trace.trace.measurements["clock_normalization"] = "same-host perf_counter_ns shared clock; offset=0; every child interval validated within submission/receipt bounds"
            return rows, {
                "worker_count": workers, "worker_pids": list(self._pids),
                "executing_pids": sorted({b.pid for b in batches}),
                "startup_ms": startup_ms, "admission_queue_ms": queue_ms,
                "feature_wall_ms": (perf_counter() - started) * 1000,
                "dispatch_queue_ms": [b.dispatch_queue_ns / 1e6 for b in sorted(batches, key=lambda b: b.partition)],
                "worker_execution_ms": [b.execution_ns / 1e6 for b in sorted(batches, key=lambda b: b.partition)],
            }
        except (TimeoutError, Empty) as error:
            if acquired:
                self._discard_pool(force=True)
            raise ComputeError("worker_timeout") from error
        except ComputeError:
            if acquired:
                self._discard_pool(force=True)
            raise
        except Exception as error:
            if acquired:
                self._discard_pool(force=True)
            raise ComputeError("worker_failure") from error
        finally:
            if acquired:
                self._lock.release()
            self._pending.release()

    def shutdown(self):
        with self._lock:
            self._closed = True
            self._discard_pool()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.shutdown()


_default_runtime = None
_default_lock = Lock()
_benchmark_lock = Lock()
_benchmark_active = Event()


@contextmanager
def benchmark_capacity():
    """Drain the ordinary pool; avoid extra HTTP worker pools in measurements.

    Omitted-execution serial commerce remains available. Explicit process
    search receives a capacity error while the operator benchmark owns CPUs.
    Separate OS processes still require operator/environment isolation.
    """
    if not _benchmark_lock.acquire(blocking=False):
        raise ComputeError("benchmark_capacity_busy")
    _benchmark_active.set()
    try:
        shutdown_runtime()
        yield
    finally:
        _benchmark_active.clear()
        _benchmark_lock.release()


def get_runtime():
    global _default_runtime
    with _default_lock:
        if _benchmark_active.is_set():
            raise ComputeError("experiment_capacity_reserved")
        if _default_runtime is None:
            from app.core.config import get_settings
            settings = get_settings()
            _default_runtime = ProcessRuntime(min(settings.hpc_max_workers, effective_cpu_count()),
                timeout_seconds=settings.hpc_timeout_seconds,
                queue_timeout_seconds=settings.hpc_queue_timeout_seconds,
                max_pending=settings.hpc_max_pending)
        return _default_runtime


def shutdown_runtime():
    global _default_runtime
    with _default_lock:
        if _default_runtime is not None:
            _default_runtime.shutdown()
            _default_runtime = None
