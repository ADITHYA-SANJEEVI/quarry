import logging
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy.orm import Session

from app.schemas.intent import ShoppingIntent
from app.services.adequacy import AdequacyEvaluator, AdequacyResult
from app.services.commerce_ranker import CommerceRanker, RankedCandidate
from app.services.commerce_events import BehavioralIntentSummary
from app.services.constraints import ConstraintEngine
from app.services.relevance import SemanticPrimaryRelevanceAdapter, RelevanceAdapter
from app.services.retrieval import PostgresCatalogueRetriever

logger = logging.getLogger(__name__)


@lru_cache
def get_default_relevance_adapter() -> RelevanceAdapter:
    """Keep one process-local model/index lifecycle across API requests."""
    return SemanticPrimaryRelevanceAdapter.from_settings()


@dataclass(frozen=True)
class CatalogueSearchResult:
    recommendations: list[RankedCandidate]
    adequacy: AdequacyResult
    eligible_products: list
    discovery_count: int
    filtered_counts_by_reason: dict[str, int]


class CatalogueSearchService:
    def __init__(
        self,
        retriever: PostgresCatalogueRetriever | None = None,
        constraints: ConstraintEngine | None = None,
        relevance: RelevanceAdapter | None = None,
        ranker: CommerceRanker | None = None,
        adequacy: AdequacyEvaluator | None = None,
    ) -> None:
        self._retriever = retriever or PostgresCatalogueRetriever()
        self._constraints = constraints or ConstraintEngine()
        self._relevance = relevance or get_default_relevance_adapter()
        self._ranker = ranker or CommerceRanker()
        self._adequacy = adequacy or AdequacyEvaluator()

    def search(
        self,
        session: Session,
        intent: ShoppingIntent,
        behavioral_intent: BehavioralIntentSummary | None = None,
    ) -> CatalogueSearchResult:
        retrieval = self._retriever.retrieve(session, intent)
        prepare_catalogue = getattr(self._relevance, "prepare_catalogue", None)
        if prepare_catalogue is not None:
            prepare_catalogue(retrieval.candidates)
        prefiltered = self._constraints.pre_retrieval(retrieval.candidates, intent)
        discovered = self._relevance.retrieve(prefiltered.eligible, intent)
        final = self._constraints.apply(
            [candidate.product for candidate in discovered], intent
        )
        relevance_by_product = {candidate.product.id: candidate for candidate in discovered}
        recommendations = self._ranker.rank(
            final.eligible,
            intent,
            relevance_by_product=relevance_by_product,
            behavioral_intent=behavioral_intent,
        )
        all_ranked = self._ranker.rank(final.eligible, intent, relevance_by_product=relevance_by_product, behavioral_intent=behavioral_intent, limit=None)
        adequacy = self._adequacy.evaluate(recommendations, intent, eligible_candidates=all_ranked)
        diagnostics = getattr(self._relevance, "diagnostics", None)
        logger.info(
            "event=search_candidates available=%s retrieved=%s eligible=%s recommendations=%s adequate=%s retrieval_mode=%s lexical_candidates=%s semantic_candidates=%s fused_candidates=%s semantic_latency_ms=%s semantic_fallback=%s",
            retrieval.available_count,
            len(retrieval.candidates),
            len(final.eligible),
            len(recommendations),
            adequacy.adequate,
            getattr(diagnostics, "mode", "custom"),
            getattr(diagnostics, "lexical_candidate_count", None),
            getattr(diagnostics, "semantic_candidate_count", None),
            getattr(diagnostics, "fused_candidate_count", None),
            getattr(diagnostics, "semantic_latency_ms", None),
            getattr(diagnostics, "fallback_reason", None),
        )
        return CatalogueSearchResult(
            recommendations=recommendations,
            adequacy=adequacy,
            eligible_products=final.eligible,
            discovery_count=len(retrieval.candidates),
            filtered_counts_by_reason={
                "pre_retrieval_hard_constraints": prefiltered.excluded_count,
                "final_eligibility_hard_constraints": final.excluded_count,
                "ranking_minimum_strength": max(0, len(final.eligible) - len(recommendations)),
            },
        )
