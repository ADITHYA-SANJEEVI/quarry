"""Per-request compute strategy; existing commerce execution happens once."""
from copy import copy
from time import perf_counter

from app.schemas.hpc import SearchExecutionMetadata
from app.services.hpc.context import build_context
from app.services.hpc.contracts import ComputeError
from app.services.hpc.engine import fuse_features
from app.services.hpc.features import score_partition
from app.services.hpc.snapshot import capture_catalogue
from app.services.hpc.tracing import stage
from app.services.relevance import DeterministicRelevanceAdapter, HybridRelevanceAdapter, SemanticPrimaryRelevanceAdapter
from app.services.search_service import CatalogueSearchService, get_default_relevance_adapter


class ComputeRelevanceAdapter(DeterministicRelevanceAdapter):
    def __init__(self, execution, runtime=None, *, auto_threshold=256, reference=None, immutable_state=None):
        super().__init__((reference or DeterministicRelevanceAdapter())._document_adapter)
        self.execution, self.runtime = execution, runtime
        self.auto_threshold = auto_threshold
        self.immutable_state = immutable_state
        self.snapshot = None
        self.behavior = None
        self.rows = ()
        self.context = None
        self.metadata = SearchExecutionMetadata(requested_mode=execution.mode,
            actual_mode="not_run", requested_workers=execution.workers)

    def bind_catalogue(self, products):
        started = perf_counter()
        with stage("snapshot_binding"):
            self.snapshot = self.immutable_state.snapshot if self.immutable_state is not None else capture_catalogue(products, self._document_adapter)
        self.snapshot_bind_ms = (perf_counter()-started)*1000

    def retrieve(self, products, intent):
        started = perf_counter()
        snapshot = self.snapshot if self.snapshot is not None else capture_catalogue(products, self._document_adapter)
        profile = {}
        with stage("query_context", candidate_in=len(products)) as span:
            context = (self.immutable_state.context(products, intent, self.behavior, profile=profile) if self.immutable_state is not None
                       else build_context(snapshot, products, intent, self.behavior, profile=profile))
            if span is not None:
                span.attributes.update(profile, terms=list(context.terms), repaired_terms=list(context.repaired_terms),
                                       corpus_n=context.n, average_length=context.average_length)
        mode = self.execution.mode
        if mode == "auto":
            mode = "parallel" if len(products) >= self.auto_threshold else "serial"
        self.metadata = SearchExecutionMetadata(requested_mode=self.execution.mode,
            actual_mode=mode, requested_workers=self.execution.workers,
            catalogue_generation=snapshot.generation, feature_count=len(products),
            context_ms=(perf_counter() - started) * 1000)
        for key, value in profile.items():
            setattr(self.metadata, key, value)
        self.metadata.snapshot_bind_ms = getattr(self, "snapshot_bind_ms", 0)
        if not products:
            rows = ()
            self.metadata.actual_mode = "serial"
            self.metadata.fallback_reason = "empty_candidates"
        elif mode == "parallel":
            attempted = perf_counter()
            try:
                if self.runtime is None:
                    raise ComputeError("runtime_unavailable")
                workers = self.execution.workers or min(4, max(w for w in (1, 2, 4, 8) if w <= self.runtime.max_workers))
                with stage("parallel_features", candidate_in=len(products)):
                    rows, metrics = self.runtime.score(snapshot, context, workers)
                for key, value in metrics.items():
                    setattr(self.metadata, key, value)
            except ComputeError as error:
                if not (self.execution.allow_fallback or self.execution.mode == "auto"):
                    raise
                started = perf_counter()
                rows = score_partition(snapshot, context, context.ordinals)
                self.metadata.actual_mode = "serial"
                self.metadata.fallback_reason = str(error)
                self.metadata.fallback_overhead_ms = (started - attempted) * 1000
                self.metadata.feature_wall_ms = (perf_counter() - started) * 1000
        else:
            started = perf_counter()
            with stage("serial_features", candidate_in=len(products)):
                rows = score_partition(snapshot, context, context.ordinals)
            self.metadata.feature_wall_ms = (perf_counter() - started) * 1000
            if self.execution.mode == "auto":
                self.metadata.fallback_reason = "below_auto_threshold"
        self.rows, self.context = rows, context
        started = perf_counter()
        with stage("global_rrf", candidate_in=len(rows)):
            result = fuse_features(snapshot, context, rows, products)
        self.metadata.fusion_ms = (perf_counter() - started) * 1000
        return result


class ExecutionSearchService(CatalogueSearchService):
    def __init__(self, execution, *, runtime=None, relevance=None, retriever=None, ranker=None, adequacy=None, constraints=None, auto_threshold=256, immutable_state=None):
        reference = relevance or get_default_relevance_adapter()
        lexical = reference._lexical if isinstance(reference, (HybridRelevanceAdapter, SemanticPrimaryRelevanceAdapter)) else reference
        if not isinstance(lexical, DeterministicRelevanceAdapter):
            raise ComputeError("unsupported_relevance_strategy")
        self.compute = ComputeRelevanceAdapter(execution, runtime, auto_threshold=auto_threshold, reference=lexical, immutable_state=immutable_state)
        if isinstance(reference, (HybridRelevanceAdapter, SemanticPrimaryRelevanceAdapter)):
            strategy = copy(reference)  # Request-local diagnostics/lexical adapter; model cache remains shared and locked.
            strategy._lexical = self.compute
        else:
            strategy = self.compute
        super().__init__(relevance=strategy, retriever=retriever, ranker=ranker, adequacy=adequacy, constraints=constraints)
        original = self._retriever
        compute = self.compute
        class BoundRetriever:
            def retrieve(self, session, intent):
                result = original.retrieve(session, intent)
                compute.bind_catalogue(result.candidates)
                return result
        self._retriever = BoundRetriever()

    def search(self, session, intent, behavioral_intent=None):
        self.compute.behavior = behavioral_intent
        started = perf_counter()
        result = super().search(session, intent, behavioral_intent)
        self.compute.metadata.search_service_ms = (perf_counter() - started) * 1000
        return result
