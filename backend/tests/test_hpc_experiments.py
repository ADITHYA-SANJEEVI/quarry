from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import sleep, monotonic

import pytest
from pydantic import ValidationError

from app.schemas.experiments import ExperimentConfig
from app.services.hpc.catalogues import CatalogueRegistry
from app.services.hpc.experiment_store import ExperimentStore
from app.services.hpc.experiments import ExperimentRunner, compatible, export_csv, owner_lock
from app.services.hpc.fair_gate import FairGate
from app.services.hpc.metrics import aggregate, percentile
from app.services.hpc.contracts import ComputeError
from app.services.hpc.runtime import benchmark_capacity, get_runtime


def config(**kwargs):
    return ExperimentConfig(sizes=[1000], workers=[1], concurrency=[1], scopes=["scoring"],
                            queries=["usb c charger under 1000"], trials=1, **kwargs)


def test_complete_matrix_is_explicit_and_bounded():
    run = ExperimentConfig()
    assert len(run.sizes)*len(run.workers)*len(run.concurrency)*len(run.scopes) == 72
    with pytest.raises(ValidationError):
        ExperimentConfig(workers=[4,4])
    with pytest.raises(ValidationError):
        ExperimentConfig(concurrency=[128])
    with pytest.raises(ValidationError):
        ExperimentConfig(queries=["x"*301])


def test_statistics_use_nearest_rank_wall_qps_and_invalidate_errors():
    observations = [{"mode": m, "latency_ms": t, "parity": True, "error": None}
                    for m, values in (("reference",[100,200,300,400]),("parallel",[50,100,150,200])) for t in values]
    trials = [{"mode":"reference","wall_seconds":1}, {"mode":"parallel","wall_seconds":.5}]
    result = aggregate(observations,trials,2)
    assert percentile([1,2,3,4],.95) == 4
    assert result["reference"]["p50_ms"] == 200 and result["reference"]["p95_ms"] == 400
    assert result["latency_speedup"] == result["throughput_speedup"] == 2
    assert result["parallel_efficiency"] == 1
    observations[-1]["error"] = "worker_timeout"
    assert aggregate(observations,trials,2)["latency_speedup"] is None
    assert aggregate([],[],2)["parallel_efficiency"] is None


def test_atomic_persistence_recovery_cancellation_and_comparison(tmp_path):
    store = ExperimentStore(tmp_path)
    runner = ExperimentRunner(store,CatalogueRegistry())
    run = runner.create(config())
    assert len(store.history()) == 1 and store.get(run["id"]) == run
    with owner_lock(tmp_path):
        assert len(store.history()) == 1  # Owner metadata is never an experiment.
        with pytest.raises(RuntimeError,match="busy"):
            with owner_lock(tmp_path):
                pass
    run["status"] = "running"; run["cells"][0]["status"] = "running"; store.save(run)
    store.recover()
    assert store.get(run["id"])["status"] == "failed"
    assert store.get(run["id"])["cells"][0]["status"] == "queued"
    other = runner.create(config())
    assert compatible(run,other)
    other["workload_sha256"] = "different"
    assert not compatible(run,other)
    cancelled = store.cancel(runner.create(config())["id"])
    assert cancelled["status"] == "cancelled"
    assert runner.execute(cancelled["id"])["status"] == "cancelled"
    with pytest.raises(ValueError):
        store.get("../../secrets")
    assert "run_id,cell,size" in export_csv(run)


def test_bounded_session_resume_preserves_raw_completed_trials(tmp_path):
    store = ExperimentStore(tmp_path)
    runner = ExperimentRunner(store, CatalogueRegistry())
    configuration = config(session_budget_seconds=120,max_cells_per_session=1)
    configuration.scopes = ["scoring","full"]
    run = runner.create(configuration)
    first = runner.execute(run["id"])
    assert first["status"] == "queued" and first["progress"]["completed_cells"] == 1
    raw = first["cells"][0]["observations"]
    second = runner.execute(run["id"])
    assert second["status"] == "completed" and second["progress"]["completed_cells"] == 2
    assert second["cells"][0]["observations"] == raw
    assert second["cells"][1]["statistics"]["comparable"]
    assert len(second["sessions"]) == 2


def test_fifo_gate_orders_waiters_and_removes_timed_out_ticket():
    gate = FairGate(); gate.acquire()
    order, events = [], [Event() for _ in range(4)]
    def waiter(i):
        events[i].set()
        if gate.acquire(timeout=.05 if i == 1 else 2):
            order.append(i); gate.release()
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = []
        for i in range(4):
            futures.append(executor.submit(waiter,i)); events[i].wait(); sleep(.01)
        sleep(.06); gate.release()
        for f in futures:
            f.result()
    assert order == [0,2,3]


def test_failed_query_never_completes_and_retry_keeps_failed_attempt(tmp_path,monkeypatch):
    from app.services.hpc import experiments
    store = ExperimentStore(tmp_path)
    runner = ExperimentRunner(store,CatalogueRegistry())
    run = runner.create(config(session_budget_seconds=120))
    original = experiments.invoke
    def broken(*args, **kwargs):
        if args[2] == "parallel":
            raise ComputeError("worker_timeout")
        return original(*args, **kwargs)
    monkeypatch.setattr(experiments,"invoke",broken)
    failed = runner.execute(run["id"])
    assert failed["status"] == "failed" and failed["progress"]["completed_cells"] == 0
    assert failed["cells"][0]["errors"][-1]["category"] == "worker_timeout"
    monkeypatch.setattr(experiments,"invoke",original)
    completed = runner.execute(run["id"])
    assert completed["status"] == "completed"
    assert completed["cells"][0]["previous_attempts"][0]["errors"][-1]["category"] == "worker_timeout"
    assert completed["cells"][0]["statistics"]["comparable"]


def test_source_drift_is_persisted_failure_and_capacity_is_exclusive(tmp_path,monkeypatch):
    from app.services.hpc import experiments
    runner = ExperimentRunner(ExperimentStore(tmp_path),CatalogueRegistry())
    run = runner.create(config())
    identity = experiments.source_identity()
    monkeypatch.setattr(experiments,"source_identity",lambda: identity | {"source_sha256":"changed"})
    failed = runner.execute(run["id"])
    assert failed["status"] == "failed"
    assert failed["errors"][-1]["category"] == "source_changed_create_new_run"
    with benchmark_capacity():
        with pytest.raises(ComputeError,match="experiment_capacity_reserved"):
            get_runtime()
        with pytest.raises(ComputeError,match="benchmark_capacity_busy"):
            with benchmark_capacity():
                pass


@pytest.mark.parametrize("quota_kind,expected",[("v2",2),("v1",3),("none",12)])
def test_effective_cpu_budget_honors_affinity_and_both_cgroup_versions(monkeypatch,quota_kind,expected):
    from pathlib import Path
    from app.services.hpc import metrics, runtime
    class Process:
        def cpu_affinity(self):
            return list(range(12))
    monkeypatch.setattr(metrics.psutil,"Process",Process)
    monkeypatch.setattr(metrics.os,"cpu_count",lambda:64)
    def read(path,*args,**kwargs):
        if quota_kind == "v2" and path.name == "cpu.max":
            return "200000 100000"
        if quota_kind == "v1" and path.name == "cpu.cfs_quota_us":
            return "300000"
        if quota_kind == "v1" and path.name == "cpu.cfs_period_us":
            return "100000"
        raise FileNotFoundError
    monkeypatch.setattr(Path,"read_text",read)
    assert metrics.cpu_allocation()["effective_cpus"] == expected
    assert runtime.effective_cpu_count() == expected
    if expected < 4:
        with pytest.raises(ValueError,match="available_cpus"):
            runtime.ProcessRuntime(max_workers=4)


def test_cpu_sampling_retains_observed_work_of_exited_child(monkeypatch):
    from types import SimpleNamespace
    from app.services.hpc import metrics
    class Process:
        def __init__(self,pid=1,cpu=5):
            self.pid,self.cpu,self.descendants=pid,cpu,[]
        def children(self,**kwargs):
            return self.descendants
        def cpu_times(self):
            return SimpleNamespace(user=self.cpu,system=0)
        def create_time(self):
            return self.pid*100
    parent = Process()
    monkeypatch.setattr(metrics.psutil,"Process",lambda:parent)
    sampler = metrics.ResourceSampler(4096)
    parent.descendants = [Process(pid=2,cpu=2)]
    assert sampler._cpu()-sampler.cpu_start == 2
    parent.descendants = []
    assert sampler._cpu()-sampler.cpu_start == 2


def test_unsafe_concurrency_is_failed_without_reducing_workload(tmp_path):
    runner = ExperimentRunner(ExperimentStore(tmp_path),CatalogueRegistry())
    configuration = ExperimentConfig(sizes=[1000],workers=[4],concurrency=[32],scopes=["full"],queries=["book"],
                                     trials=1,memory_limit_mb=256)
    run = runner.execute(runner.create(configuration)["id"])
    assert run["status"] == "failed"
    cell = run["cells"][0]
    assert cell["errors"][-1]["category"] == "memory_preflight_limit"
    assert cell["concurrency"] == 32 and cell["workers"] == 4
    assert cell["profile"]["estimated_concurrent_coordinator_mb"] > 100
    assert not cell.get("validation")  # No unsafe processes/queries were started.


def test_resume_refuses_runtime_or_hardware_drift(tmp_path,monkeypatch):
    from app.services.hpc import experiments
    runner = ExperimentRunner(ExperimentStore(tmp_path),CatalogueRegistry())
    run = runner.create(config())
    recorded = experiments.environment()
    monkeypatch.setattr(experiments,"environment",lambda:recorded | {"effective_cpus":99})
    result = runner.execute(run["id"])
    assert result["status"] == "failed" and result["progress"]["completed_cells"] == 0
    assert result["errors"][-1]["category"] == "environment_changed_create_new_run"


def test_atomic_replace_retries_transient_windows_contention_and_preserves_old_on_failure(tmp_path,monkeypatch):
    from app.services.hpc import storage_io
    source, destination = tmp_path/'new.tmp',tmp_path/'run.json'
    source.write_text('new'); destination.write_text('old')
    original, attempts = storage_io.os.replace, []
    def transient(a,b):
        attempts.append(1)
        if len(attempts) < 3:
            assert destination.read_text() == 'old'
            raise PermissionError('simulated sharing violation')
        return original(a,b)
    monkeypatch.setattr(storage_io,'sleep',lambda _:None)
    monkeypatch.setattr(storage_io.os,'replace',transient)
    storage_io.atomic_replace(source,destination)
    assert destination.read_text() == 'new' and len(attempts) == 3
    source.write_text('next')
    attempts.clear()
    def permanent(a,b):
        attempts.append(1)
        raise PermissionError('simulated permanent failure')
    monkeypatch.setattr(storage_io.os,'replace',permanent)
    with pytest.raises(PermissionError):
        storage_io.atomic_replace(source,destination)
    assert len(attempts) == 10 and destination.read_text() == 'new' and source.read_text() == 'next'
