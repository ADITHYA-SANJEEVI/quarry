"""Observe original algorithms without replacing their scoring or decisions."""
from dataclasses import asdict
from functools import lru_cache
from hashlib import sha256
import json
import os
from time import perf_counter_ns

from app.schemas.hpc import SearchExecutionRequest
from app.services.hpc.demo_search import make_service, downstream, result_digest
from app.services.hpc.tracing import TraceCollector, active_trace, stage
from app.services.hpc.contracts import ComputeError
from app.services.intent_normalization import ControlledLexicalNormalizer
from app.services.intent_parser import FallbackIntentParser
from app.services.relevance import DeterministicRelevanceAdapter

WORKLOADS = {
    "L1": ["headphones", "writing desk", "steel water bottle"],
    "L2": ["usb c charger under 1000", "cleanser without fragrance under 1000"],
    "L3": ["gentle cleanser and fragrance-free moisturiser under 1500", "usb c charger and cable under 2500"],
    "L4": [],
}


class ResearchExecutionError(ComputeError):
    def __init__(self, category, trace):
        super().__init__(category)
        self.trace = trace


def characteristics(intent):
    roles = list(intent.resolved_roles)
    constraints = len(intent.hard_negative_signals) + len(intent.compatibility_requirements)
    constraints += int(intent.strict_min_price is not None) + int(intent.strict_max_price is not None)
    level = "L3" if len(roles) > 1 else "L2" if constraints else "L1"
    return {"class": level, "classifier_version": "accepted_intent_v1", "roles": roles,
            "hard_constraint_count": constraints, "desired_attributes": len(intent.desired_attributes),
            "input_characters": len(intent.raw_query), "input_words": len(intent.raw_query.split()),
            "clarification_needed": intent.clarification_needed,
            "semantic_actual": "disabled_deterministic_lexical"}


class Normalizer(ControlledLexicalNormalizer):
    def normalize(self, *args, **kwargs):
        with stage("input_normalization"):
            return super().normalize(*args, **kwargs)


class ReferenceRelevance(DeterministicRelevanceAdapter):
    """Boundary hooks execute the inherited retrieve method unchanged.

    The first BM25/rank calls mark boundaries and then install the original
    static methods, avoiding an extra timing call for every document.
    """
    def index(self, products):
        with stage("document_preparation", candidate_in=len(products)):
            return super().index(products)

    def _build_query(self, intent, documents):
        self.context_scope = stage("query_context", candidate_in=len(documents))
        self.context_scope.__enter__()
        with stage("query_expansion", candidate_in=len(documents)) as span:
            query = super()._build_query(intent, documents)
            span.attributes.update(terms=list(query.terms),repaired_terms=list(query.repaired_terms))
        self.count = len(documents)
        return query

    def _bm25(self, *args):
        # These are the exact inherited BM25 call arguments, not a separately
        # recomputed approximation to the corpus statistics.
        active_trace.get().trace.measurements["global_statistics"] = {"n":args[3],"average_length":args[4],
            "document_frequency_sha256":sha256(json.dumps(sorted(args[2].items())).encode()).hexdigest()}
        self.context_scope.__exit__(None,None,None)
        self.context_scope = None
        self.feature_start = perf_counter_ns()
        self._bm25 = DeterministicRelevanceAdapter._bm25
        return self._bm25(*args)

    def _ranks(self, *args):
        self.fusion_start = perf_counter_ns()
        active_trace.get().add("serial_features", self.feature_start, self.fusion_start, candidate_in=self.count)
        self._ranks = DeterministicRelevanceAdapter._ranks
        return self._ranks(*args)

    def retrieve(self, products, intent):
        try:
            return super().retrieve(products, intent)
        finally:
            if getattr(self,"context_scope",None) is not None:
                self.context_scope.__exit__(None,None,None)
                self.context_scope = None
            if hasattr(self, "fusion_start"):
                active_trace.get().add("global_rrf", self.fusion_start, perf_counter_ns(), candidate_in=self.count)


class Observed:
    """Request-local object proxy; only explicitly named calls are observed."""
    def __init__(self, target, methods):
        self.target, self.methods = target, methods

    def __getattr__(self, name):
        value = getattr(self.target, name)
        if name not in self.methods:
            return value
        def call(*args, **kwargs):
            with stage(self.methods[name], candidate_in=len(args[0]) if args and isinstance(args[0], (list, tuple)) else None,
                       limit=kwargs.get("limit", 3)) as span:
                result = value(*args, **kwargs)
                if span is not None:
                    if hasattr(result, "eligible"):
                        span.attributes["candidate_out"] = len(result.eligible)
                    elif isinstance(result, (list, tuple)):
                        span.attributes["candidate_out"] = len(result)
                return result
        return call


@lru_cache(maxsize=1)
def semantic_observer(model_id, local_files_only):
    from app.services.semantic_retrieval import MiniLMSemanticRetriever
    class TracedMiniLM(MiniLMSemanticRetriever):
        def _get_model(self):
            # The original loader, dimension checks, embedding cache and lock
            # remain in charge. Only actual encoder calls emit inference spans.
            return Observed(super()._get_model(), {"encode":"semantic_inference"})
    return TracedMiniLM(model_id,local_files_only=local_files_only)


def execute_research(catalogue, query, mode, workers, runtime, source, *, run_id=None,
                     concurrency=1, scope="full", validate=True, keep_trace=True, semantic="disabled"):
    from app.services.constraints import ConstraintEngine
    from app.services.hpc.integration import ComputeRelevanceAdapter
    from app.services.hpc.experiments import relevance_digest
    collector = TraceCollector(catalogue, query, mode, source, run_id=run_id, concurrency=concurrency)
    token = active_trace.set(collector)
    trace = collector.trace
    try:
        with stage("total_search"):
            with stage("intent_parsing"):
                intent = FallbackIntentParser(Normalizer()).parse(query)
            collector.intent(intent)
            execution = SearchExecutionRequest(mode="parallel", workers=workers)
            if scope == "full":
                service, ranker = make_service(catalogue, execution if mode == "parallel" else None, runtime)
                if mode == "reference":
                    service._relevance = ReferenceRelevance()
                if semantic == "server_configured":
                    from copy import copy
                    from app.services.search_service import get_default_relevance_adapter
                    from app.services.relevance import SemanticPrimaryRelevanceAdapter
                    from app.core.config import get_settings
                    configured = get_default_relevance_adapter()
                    if not isinstance(configured,SemanticPrimaryRelevanceAdapter) or not configured._enabled:
                        raise ComputeError("semantic_research_requires_enabled_server_model")
                    strategy = copy(configured)
                    strategy._lexical = service._relevance
                    settings = get_settings()
                    strategy._semantic_retriever = Observed(semantic_observer(settings.semantic_retrieval_model,settings.semantic_retrieval_local_files_only),
                        {"prepare_catalogue":"semantic_catalogue_preparation", "retrieve":"semantic_retrieval_including_inference"})
                    service._relevance = strategy
                service._constraints = Observed(service._constraints, {"pre_retrieval": "eligibility_pre", "apply": "eligibility_final"})
                service._ranker = Observed(ranker, {"rank": "commerce_ranking"})
                service._adequacy = Observed(service._adequacy, {"evaluate": "adequacy"})
                service._retriever = Observed(service._retriever, {"retrieve": "catalogue_retrieval"})
                result = service.search(None, intent)
                with stage("downstream"):
                    decisions = downstream(result, intent)
                metadata = service.compute.metadata.model_dump() if mode == "parallel" else {}
                metadata["commerce_calls"] = ranker.calls
                if semantic == "server_configured":
                    diagnostics = service._relevance.diagnostics
                    metadata["semantic_actual_mode"] = diagnostics.mode
                    metadata["semantic_fallback_reason"] = diagnostics.fallback_reason
                trace.candidate_counts = {"catalogue": len(catalogue.products), "eligible_final": len(result.eligible_products),
                                          "visible": len(result.recommendations), "unbounded_ranked": len(ranker.all_rows)}
            else:
                if semantic != "disabled":
                    raise ComputeError("semantic_research_requires_full_scope")
                with stage("eligibility_pre"):
                    eligible = ConstraintEngine().pre_retrieval(catalogue.products, intent).eligible
                adapter = ComputeRelevanceAdapter(execution, runtime, immutable_state=catalogue) if mode == "parallel" else ReferenceRelevance()
                if mode == "parallel":
                    adapter.bind_catalogue(catalogue.products)
                result = adapter.retrieve(eligible, intent)
                metadata = adapter.metadata.model_dump() if mode == "parallel" else {}
                trace.candidate_counts = {"catalogue": len(catalogue.products), "preeligible": len(eligible), "relevance": len(result)}
        # Digest and export are deliberately outside the measured search span.
        signature = (result_digest(result, ranker, decisions) if scope == "full" else relevance_digest(result)) if validate else None
        trace.result_hash = signature
        trace.mode_actual = metadata.get("actual_mode", "reference")
        trace.fallback_reason = metadata.get("fallback_reason")
        for span in trace.stage_spans:
            if span.stage_name in ("eligibility_pre","eligibility_final") and "candidate_out" in span.attributes:
                trace.candidate_counts[span.stage_name] = span.attributes["candidate_out"]
        trace.measurements.update(workload=characteristics(intent), timing_scope=f"nlp_plus_{scope}_in_memory",
                                  semantic_status="disabled_no_model_inference", context_cache_hit=metadata.get("context_cache_hit", False))
        if semantic == "server_configured":
            trace.measurements["semantic_status"] = metadata["semantic_actual_mode"]
            trace.measurements["workload"]["class_requested"] = "L4"
            trace.measurements["workload"]["semantic_actual"] = metadata["semantic_actual_mode"]
            if metadata["semantic_actual_mode"] == "semantic_primary":
                trace.measurements["workload"]["class"] = "L4"
            trace.measurements["semantic_fallback_reason"] = metadata["semantic_fallback_reason"]
        trace.environment_metadata["coordinator_pid"] = os.getpid()
        collector.finish()
        total = next(s.duration_ns for s in trace.stage_spans if s.stage_name == "total_search")
        output = {"latency_ms": total/1e6, "execution": metadata,
                  "eligible_count": trace.candidate_counts.get("eligible_final", len(result) if isinstance(result, list) else 0),
                  "digest": signature, "workload": trace.measurements["workload"],
                  "stage_timings_ns": [{"name": s.stage_name, "duration_ns": s.duration_ns, "inclusive": True} for s in trace.stage_spans]}
        if keep_trace:
            output["trace"] = trace.model_dump(mode="json")
        if validate:
            if scope == "full":
                output["result_preview"] = {"recommendations": [
                    {"product_id":r.product.id,"name":r.product.name,"price":str(r.product.price),
                     "score_hex":float(r.score).hex(),"score":r.score,"evidence":[asdict(e) for e in r.evidence],
                     "decomposition":asdict(r.decomposition),"reason":r.reason,"label":r.presentation_label}
                    for r in result.recommendations], "adequacy":asdict(result.adequacy), "downstream":decisions,
                    "unbounded_preview":[{"product_id":r.product.id,"score_hex":float(r.score).hex()} for r in ranker.all_rows[:100]],
                    "unbounded_count":len(ranker.all_rows),"preview_limit":100,
                    "full_result_contract":"digest covers all ordered rows, float bits, evidence, eligibility, adequacy and decisions"}
            else:
                output["result_preview"] = {"candidates":[{"product_id":r.product.id,"lexical":r.lexical_score,
                    "fuzzy":r.fuzzy_score,"character":r.character_score,"phrase":r.phrase_score,"fused":r.fused_score,
                    "fused_hex":r.fused_score.hex(),"matched_terms":r.matched_terms,"repaired_terms":r.repaired_terms}
                    for r in result[:100]],"total":len(result),"preview_limit":100}
        return output
    except Exception as error:
        category = str(error) if isinstance(error,ComputeError) else type(error).__name__
        trace.errors.append(category)
        trace.correctness_status = "execution_failed"
        collector.finish()
        raise ResearchExecutionError(category,trace.model_dump(mode="json")) from error
    finally:
        active_trace.reset(token)
