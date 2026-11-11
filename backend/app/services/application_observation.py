"""Optional application diagnostics. Never enabled by benchmark entry points."""
from contextvars import ContextVar
from functools import lru_cache

application_observation = ContextVar("application_observation", default=False)


@lru_cache(maxsize=1)
def application_source():
    from app.services.hpc.experiments import source_identity
    return source_identity()["source_sha256"]


def observe_service(service, ranker):
    if application_observation.get():
        from app.services.hpc.research_execution import Observed
        service._constraints = Observed(service._constraints, {"pre_retrieval": "eligibility_pre", "apply": "eligibility_final"})
        service._ranker = Observed(ranker, {"rank": "commerce_ranking"})
        service._adequacy = Observed(service._adequacy, {"evaluate": "adequacy"})
        service._retriever = Observed(service._retriever, {"retrieve": "catalogue_retrieval"})


def capture_application(execute, dataset, body):
    from types import SimpleNamespace
    from app.api.hpc_experiments import catalogue
    from app.services.hpc.tracing import TraceCollector, active_trace, stage
    from app.schemas.intent import ShoppingIntent
    manifest = catalogue(dataset).manifest if dataset != "live" else {"dataset_id": "live", "sha256": "snapshot-pending"}
    collector = TraceCollector(SimpleNamespace(manifest=manifest), body.query, body.execution.mode, application_source())
    token, observing = active_trace.set(collector), application_observation.set(True)
    try:
        with stage("total_search"):
            result = execute(dataset, body)
        collector.intent(ShoppingIntent.model_validate(result["intent"]))
        execution = result.get("execution", {})
        trace = collector.trace
        trace.mode_actual = execution.get("actual_mode", "serial")
        trace.fallback_reason = execution.get("fallback_reason")
        # Include lanes only for children that actually supplied feature events.
        trace.worker_pids = sorted({s.process_id for s in trace.worker_spans if s.stage_name == "worker_features"})
        trace.physical_pool_size = execution.get("worker_count", 0)
        trace.dataset_hash = result["dataset"].get("sha256", manifest["sha256"])
        trace.candidate_counts = {"features": execution.get("feature_count", 0), "eligible_final": result["eligible_count"], "visible": len(result["recommendations"])}
        trace.measurements.update(purpose="ephemeral_application_diagnostic", timing_scope="application_search_excluding_registry_binding_and_trace_export", instrumentation="Request instrumentation is enabled. Its overhead is included, not isolated or subtracted. This request is not a benchmark sample or parity validation.", retention="In-memory, at most 100 requests, 10 minutes; durable only after explicit operator save")
        result["latest_trace"] = collector.finish().model_dump(mode="json")
        return result
    finally:
        application_observation.reset(observing)
        active_trace.reset(token)
