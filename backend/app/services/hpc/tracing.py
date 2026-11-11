"""Request-local spans; process intervals are reported by the actual child."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from hashlib import sha256
import os
from time import perf_counter_ns, thread_time_ns
from uuid import uuid4

from app.schemas.research import ExecutionTrace, StageSpan
from app.services.hpc.snapshot import canonical_json

active_trace = ContextVar("hpc_research_trace", default=None)


def utc():
    return datetime.now(UTC).isoformat()


class TraceCollector:
    def __init__(self, catalogue, query, mode, source, *, run_id=None, concurrency=1):
        self.origin = perf_counter_ns()
        self.stack = []
        self.sequence = 0
        self.trace = ExecutionTrace(trace_id=str(uuid4()), run_id=run_id,
            dataset_id=catalogue.manifest["dataset_id"], dataset_hash=catalogue.manifest["sha256"],
            query_id=sha256(query.encode()).hexdigest(), source_revision=source,
            mode_requested=mode, timestamp_start=utc(), concurrency=concurrency)

    def add(self, name, start, end, *, pid=None, parent=None, worker=False, status="ok", **attributes):
        if start < self.origin or end < start:
            raise ValueError("invalid_trace_clock_interval")
        self.sequence += 1
        span = StageSpan(span_id=f"{self.trace.trace_id}:{self.sequence}",
            parent_id=parent if parent is not None else (self.stack[-1] if self.stack else None),
            stage_name=name, start_offset_ns=start-self.origin, duration_ns=end-start,
            process_id=pid or os.getpid(), status=status, attributes=attributes)
        (self.trace.worker_spans if worker else self.trace.stage_spans).append(span)
        return span

    @contextmanager
    def span(self, name, **attributes):
        start, cpu = perf_counter_ns(), thread_time_ns()
        # Reserve the parent ID before collecting nested child spans.
        span = self.add(name, start, start, **attributes)
        self.stack.append(span.span_id)
        try:
            yield span
        except BaseException:
            span.status = "error"
            raise
        finally:
            self.stack.pop()
            span.duration_ns = perf_counter_ns()-start
            span.attributes["coordinator_thread_cpu_ns"] = thread_time_ns()-cpu

    def intent(self, intent):
        self.trace.accepted_intent_hash = sha256(canonical_json(intent.model_dump(mode="json")).encode()).hexdigest()

    def finish(self):
        self.trace.timestamp_end = utc()
        return self.trace


@contextmanager
def stage(name, **attributes):
    trace = active_trace.get()
    if trace is None:
        yield None
    else:
        with trace.span(name, **attributes) as span:
            yield span


def union_ns(spans):
    intervals = sorted((s[0], s[1]) for s in spans if s[1] >= s[0])
    total, end = 0, None
    for start, stop in intervals:
        total += max(0, stop-max(start, end if end is not None else start))
        end = max(stop, end if end is not None else stop)
    return total


def overhead(trace):
    stages, workers = trace["stage_spans"], trace["worker_spans"]
    execution = [s for s in workers if s["stage_name"] == "worker_features"]
    busy = union_ns([(s["start_offset_ns"], s["start_offset_ns"]+s["duration_ns"]) for s in execution])
    total = next((s["duration_ns"] for s in stages if s["stage_name"] == "total_search"), 0)
    durations = [s["duration_ns"] for s in execution]
    # Residual includes coordinator work, queueing, startup and IPC; it is not
    # a measured network/pipe transfer time or an exclusive serial fraction.
    residual = max(0, total-busy)
    communication = [s for s in workers if s["stage_name"] in ("worker_queue", "result_return_residual")]
    communication_union = union_ns([(s["start_offset_ns"],s["start_offset_ns"]+s["duration_ns"]) for s in communication])
    exclusive = []
    for span in stages:
        child_intervals = [(s["start_offset_ns"],s["start_offset_ns"]+s["duration_ns"]) for s in stages
                           if s["parent_id"] == span["span_id"]]
        exclusive.append({"stage":span["stage_name"],"coordinator_exclusive_ns":max(0,span["duration_ns"]-union_ns(child_intervals))})
    idle = []
    feature_span = next((s for s in stages if s["stage_name"] == "parallel_features"),None)
    if feature_span:
        for pid in trace["worker_pids"]:
            own = sorted((s["start_offset_ns"],s["start_offset_ns"]+s["duration_ns"]) for s in execution if s["process_id"] == pid)
            cursor = feature_span["start_offset_ns"]
            for start, stop in own:
                if start > cursor:
                    idle.append({"pid":pid,"start_offset_ns":cursor,"duration_ns":start-cursor})
                cursor = max(cursor,stop)
            end = feature_span["start_offset_ns"]+feature_span["duration_ns"]
            if end > cursor:
                idle.append({"pid":pid,"start_offset_ns":cursor,"duration_ns":end-cursor})
    return {"trace_id": trace["trace_id"], "worker_busy_union_ns": busy,
            "worker_execution_sum_ns": sum(durations), "total_search_ns": total,
            "non_worker_wall_residual_ns": residual, "residual_kind": "inferred_includes_coordinator_and_queue",
            "residual_to_compute_ratio": residual/busy if busy else None,
            "imbalance_max_over_mean": max(durations)/(sum(durations)/len(durations)) if durations and sum(durations) else None,
            "worker_wall_occupancy": sum(durations)/(total*trace["physical_pool_size"]) if total and trace["physical_pool_size"] else None,
            "occupancy_kind": "execution_wall_fraction_not_cpu_utilization",
            "communication_compute_ratio": communication_union/busy if busy else None,
            "communication_kind": "inferred_queue_plus_return_residual_interval_union; overlaps_compute; not_pipe_transfer_time",
            "inferred_non_feature_intervals": idle,
            "idle_kind": "not_executing_this_query_features; includes_startup_queue_serialization_and_other_requests; not_OS_idle",
            "bottleneck_attribution": exclusive,
            "measurements": trace.get("measurements", {}),
            "stages": [{"name": s["stage_name"], "duration_ns": s["duration_ns"], "inclusive": True} for s in stages]}
