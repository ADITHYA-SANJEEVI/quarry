"""Importable Windows-spawn worker entry points. No ORM instances in state."""
import os
from time import perf_counter_ns, process_time_ns
from dataclasses import replace
import pickle

from app.services.hpc.contracts import ComputeError, FeatureBatch, VERSION
from app.services.hpc.features import score_partition

_catalogue = None


def initialize_worker(snapshot, ready_queue, startup_barrier, startup_timeout):
    global _catalogue
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    if snapshot.version != VERSION:
        raise ComputeError("contract_version_mismatch")
    _catalogue = snapshot
    ready_queue.put((os.getpid(), snapshot.generation, VERSION, perf_counter_ns()))
    # A fast probe must not return an idle token before every requested
    # process has spawned: Executor otherwise may legitimately create fewer.
    startup_barrier.wait(timeout=startup_timeout)


def worker_ready():
    return os.getpid()


def score_task(task):
    started = perf_counter_ns()
    cpu = process_time_ns() if task.instrument else 0
    if task.version != VERSION or _catalogue is None or task.generation != _catalogue.generation:
        raise ComputeError("generation_mismatch")
    rows = score_partition(_catalogue, task.context, task.ordinals)
    ended = perf_counter_ns()
    result = FeatureBatch(task.request_id, task.partition, task.generation,
        task.context.context_id, rows, os.getpid(), max(0, started - task.submitted_ns),
        ended - started)
    if task.instrument:
        import psutil
        result = replace(result, started_ns=started, ended_ns=ended,
                         cpu_ns=process_time_ns()-cpu, rss_bytes=psutil.Process().memory_info().rss)
        serialization_start = perf_counter_ns()
        # This is an actual protocol-5 serialization probe. Executor performs
        # its own transport serialization; probe time is explicitly separate.
        size = len(pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL))
        result = replace(result, result_serialized_bytes=size,
                         result_serialization_ns=perf_counter_ns()-serialization_start)
    return result
