from dataclasses import dataclass, replace
from decimal import Decimal

from app.models.product import Product
from app.domain.catalogue_facts import FactFamily
from app.schemas.intent import IntentSignalKind, ShoppingIntent
from app.services.relevance import RelevanceCandidate
from app.services.commerce_events import BehavioralIntentSummary
from app.services.catalogue_evidence import CatalogueEvidenceService, EvidenceState


@dataclass(frozen=True)
class RankingWeights:
    category: float = 0.25
    preferences: float = 0.35
    lexical: float = 0.05
    relevance: float = 0.25
    # Role fit is exposed in the decomposition; eligibility and adequacy own
    # role authority, so it must not dilute established preference ranking.
    role: float = 0.0
    gifting: float = 0.35
    use_case: float = 0.25
    contextual_conflict: float = 0.30
    gift_set_without_gifting: float = 0.35
    target_price: float = 0.15
    keywords: float = 0.05
    behavioral: float = 0.08
    minimum_relevance: float = 0.18
    max_recommendations: int = 3


DEFAULT_RANKING_WEIGHTS = RankingWeights()


@dataclass(frozen=True)
class UseCaseProfile:
    positive_phrases: tuple[str, ...]
    conflicting_tags: tuple[str, ...]
    positive_label: str
    conflict_label: str


# Reference-merchant configuration. The scoring mechanism is generic: an
# explicit use-case signal gets only product-backed positive or conflict
# evidence. No product identity is used.
_REFERENCE_USE_CASE_PROFILES = {
    "gentle-daily-cleansing": UseCaseProfile(
        positive_phrases=("gentle", "low-foam", "comfortable", "not tight", "daily"),
        conflicting_tags=("makeup-remover",),
        positive_label="Gentle daily-use fit",
        conflict_label="Less suited to an everyday cleansing routine",
    ),
}


@dataclass(frozen=True)
class RankingEvidence:
    signal: str
    score: float
    weight: float
    evidence_source: str
    evidence_value: str
    label: str

    @property
    def contribution(self) -> float:
        return self.score * self.weight


@dataclass(frozen=True)
class RankingDecomposition:
    """Safe normalized ranking inputs; unavailable inputs stay absent."""

    final_rank_score: float
    lexical_component: float | None
    semantic_component: float | None
    role_component: float | None
    evidence_component: float | None
    preference_component: float | None
    budget_component: float | None
    policy_version: str = "eligible_ranking_policy_v1"


@dataclass(frozen=True)
class RankedCandidate:
    product: Product
    score: float
    evidence: list[RankingEvidence]
    presentation_label: str = "Alternative"
    decomposition: RankingDecomposition | None = None

    @property
    def reason(self) -> str:
        positive = sorted(
            (item for item in self.evidence if item.contribution > 0),
            key=lambda item: (-item.contribution, item.label),
        )[:3]
        labels = [item.label for item in positive]
        limit = next((item for item in self.evidence if item.signal == "strict_max_price"), None)
        if limit and limit.label not in labels:
            labels.append(limit.label)
        return " · ".join(labels) if labels else "Available in the current catalogue"


class CommerceRanker:
    def __init__(self, weights: RankingWeights = DEFAULT_RANKING_WEIGHTS) -> None:
        self._weights = weights
        self._catalogue_evidence = CatalogueEvidenceService()

    def rank(
        self,
        candidates: list[Product],
        intent: ShoppingIntent,
        *,
        relevance_by_product: dict[int, RelevanceCandidate] | None = None,
        behavioral_intent: BehavioralIntentSummary | None = None,
        limit: int | None = DEFAULT_RANKING_WEIGHTS.max_recommendations,
    ) -> list[RankedCandidate]:
        ranked = [
            self._rank(
                product,
                intent,
                (relevance_by_product or {}).get(product.id),
                behavioral_intent,
            )
            for product in candidates
        ]
        ranked.sort(key=lambda item: (-item.score, item.product.price, item.product.id, item.product.name))
        selected = ranked if limit is None else [
            item for item in ranked if item.score >= self._weights.minimum_relevance
        ][:limit]
        return [
            replace(
                item,
                presentation_label=(
                    "Best match"
                    if index == 0
                    else "Gift-ready option"
                    if any(evidence.signal == "gifting" for evidence in item.evidence)
                    else "Alternative"
                ),
            )
            for index, item in enumerate(selected)
        ]

    def _rank(
        self,
        product: Product,
        intent: ShoppingIntent,
        relevance: RelevanceCandidate | None = None,
        behavioral_intent: BehavioralIntentSummary | None = None,
    ) -> RankedCandidate:
        evidence: list[RankingEvidence] = []
        tags = set(product.tags)
        searchable_text = " ".join([product.name, product.description, *product.tags]).lower()

        if intent.category == product.category:
            evidence.append(
                RankingEvidence(
                    signal="category",
                    score=1.0,
                    weight=self._weights.category,
                    evidence_source="product.category",
                    evidence_value=product.category,
                    label=f"{product.category.replace('-', ' ').title()} category",
                )
            )

        requested_roles = set(intent.resolved_roles)
        product_role = self._policy_role(product)
        if requested_roles and product_role in requested_roles:
            evidence.append(
                RankingEvidence(
                    signal="role_fit", score=1.0, weight=self._weights.role,
                    evidence_source="product.fact" if product.catalogue_facts else "product.tags",
                    evidence_value=f"role:{product_role}" if product.catalogue_facts else product_role,
                    label=f"{product_role.replace('-', ' ').title()} match",
                )
            )

        preferences = [
            signal
            for signal in intent.positive_signals
            if signal.kind == IntentSignalKind.PREFERENCE
        ]
        preference_weight = self._weights.preferences / len(preferences) if preferences else 0
        for signal in preferences:
            fact_evidence = self._evidence_for_concept(product, signal.value)
            if fact_evidence is not None:
                evidence.append(
                    RankingEvidence(
                        signal=f"preference:{signal.value}",
                        score=1.0,
                        weight=preference_weight,
                        evidence_source=fact_evidence.source,
                        evidence_value=fact_evidence.value,
                        label=_preference_label(signal.value),
                    )
                )

        keyword_hits = [keyword for keyword in intent.keywords if keyword in searchable_text]
        if keyword_hits:
            evidence.append(
                RankingEvidence(
                    signal="lexical_relevance",
                    score=min(1.0, len(keyword_hits) / max(1, len(intent.keywords))),
                    weight=self._weights.lexical,
                    evidence_source="product.text",
                    evidence_value=keyword_hits[0],
                    label=f"Matches {keyword_hits[0].replace('-', ' ')}",
                )
            )

        retrieval_prior = relevance.retrieval_prior if relevance and relevance.retrieval_prior is not None else (relevance.fused_score if relevance else 0.0)
        # Retrieval relevance enters scoring exactly once. Textual matches can
        # explain that single prior; semantic-only discovery cannot.
        if relevance and relevance.matched_terms:
            evidence.append(
                RankingEvidence(
                    signal="relevance",
                    score=retrieval_prior,
                    weight=self._weights.relevance,
                    evidence_source="product.text",
                    evidence_value=relevance.matched_terms[0],
                    label=f"Relevant to {relevance.matched_terms[0].replace('-', ' ')}",
                )
            )

        self._add_use_case_evidence(evidence, product, intent)

        # Behavioral state is a conservative, current-session soft boost. It
        # is applied only after deterministic eligibility has already removed
        # stock, price, merchant, and explicit-exclusion violations.
        behavioral_boost = behavioral_intent.boost_for(product) if behavioral_intent else 0.0
        if behavioral_boost:
            evidence.append(
                RankingEvidence(
                    signal="behavioral_interest",
                    score=behavioral_boost,
                    weight=self._weights.behavioral,
                    evidence_source="commerce_events",
                    evidence_value="current_session",
                    label="Current-session interest",
                )
            )

        gift_evidence = self._gift_evidence(product)
        if intent.gifting_intent and gift_evidence is not None:
            requested_values = {
                signal.value
                for signal in intent.positive_signals
                if signal.kind == IntentSignalKind.PREFERENCE
            }
            coverage = (
                sum(value.replace("-", " ") in searchable_text for value in requested_values)
                / len(requested_values)
                if requested_values
                else 0.0
            )
            evidence.append(
                RankingEvidence(
                    signal="gifting",
                    score=(0.4 + 0.6 * coverage)
                    * (1.0 if intent.recipient or intent.occasion else 0.8),
                    weight=self._weights.gifting,
                    evidence_source=gift_evidence.source,
                    evidence_value=gift_evidence.value,
                    label="Gift-ready",
                )
            )
        elif gift_evidence is not None and "gift-set" not in intent.resolved_roles:
            # A gift set can remain a retrieval candidate because it contains
            # related components, but without stated gifting it conflicts with
            # a direct single-product request. This is a generic contextual
            # fit signal, not a product-specific exclusion.
            evidence.append(
                RankingEvidence(
                    signal="contextual_conflict:gift_set_without_gifting",
                    score=-1.0,
                    weight=self._weights.gift_set_without_gifting,
                    evidence_source=gift_evidence.source,
                    evidence_value=gift_evidence.value,
                    label="Gift set not requested",
                )
            )

        if intent.target_price is not None:
            proximity = _price_proximity(product.price, intent.target_price)
            evidence.append(
                RankingEvidence(
                    signal="target_price",
                    score=proximity,
                    weight=self._weights.target_price,
                    evidence_source="product.price",
                    evidence_value=str(product.price),
                    label=_target_price_label(product.price, intent.target_price),
                )
            )

        if intent.strict_max_price is not None:
            evidence.append(
                RankingEvidence(
                    signal="strict_max_price",
                    score=0.0,
                    weight=0.0,
                    evidence_source="product.price",
                    evidence_value=str(product.price),
                    label=_maximum_price_label(product.price, intent.strict_max_price),
                )
            )

        score = sum(item.contribution for item in evidence)
        if relevance and not relevance.matched_terms and relevance.retrieval_prior:
            # Non-factual discovery prior; deliberately absent from shopper
            # evidence but consumed once as a soft ranking input.
            score += retrieval_prior * self._weights.relevance
        score = min(1.0, score)
        return RankedCandidate(
            product=product, score=score, evidence=evidence,
            decomposition=self._decomposition(product, intent, relevance, evidence, score),
        )

    def _decomposition(
        self,
        product: Product,
        intent: ShoppingIntent,
        relevance: RelevanceCandidate | None,
        evidence: list[RankingEvidence],
        score: float,
    ) -> RankingDecomposition:
        """Expose only bounded inputs that were actually available.

        Ranking itself only receives final-eligible products. Missing semantic
        retrieval is therefore omitted, not converted to a negative score.
        """
        by_signal = {item.signal: item for item in evidence}
        lexical = by_signal.get("lexical_relevance")
        semantic = (
            min(1.0, 1.0 / relevance.semantic_rank)
            if relevance is not None and relevance.semantic_rank else None
        )
        requested_roles = set(intent.resolved_roles)
        product_role = self._policy_role(product)
        role = 1.0 if requested_roles and product_role in requested_roles else None
        preferences = [item.score for item in evidence if item.signal.startswith("preference:")]
        direct_evidence = [
            item.score for item in evidence
            if item.evidence_source not in {"product.text", "commerce_events"}
            and item.score > 0
        ]
        target = by_signal.get("target_price")
        return RankingDecomposition(
            final_rank_score=score,
            lexical_component=lexical.score if lexical else None,
            semantic_component=semantic,
            role_component=role,
            evidence_component=sum(direct_evidence) / len(direct_evidence) if direct_evidence else None,
            preference_component=sum(preferences) / len(preferences) if preferences else None,
            budget_component=target.score if target else (1.0 if intent.strict_max_price is not None else None),
        )

    @staticmethod
    def _policy_role(product: Product) -> str | None:
        from app.services.merchant_commerce_policy import MerchantCommercePolicy
        return MerchantCommercePolicy.product_role(product)

    def _evidence_for_concept(self, product: Product, concept: str):
        for family in FactFamily:
            evidence = self._catalogue_evidence.evaluate(product, family, concept)
            if evidence.state == EvidenceState.SUPPORTED:
                return evidence
        if not product.catalogue_facts and concept in product.tags:
            return _LegacyEvidence("product.tags", concept)
        return None

    def _gift_evidence(self, product: Product):
        for family, concept in ((FactFamily.USE_CASE, "gift-ready"), (FactFamily.ATTRIBUTE, "giftable"), (FactFamily.ROLE, "gift-set")):
            evidence = self._catalogue_evidence.evaluate(product, family, concept)
            if evidence.state == EvidenceState.SUPPORTED:
                return evidence
        if not product.catalogue_facts and ({"gift-set", "giftable"} & set(product.tags)):
            return _LegacyEvidence("product.tags", "gift-set" if "gift-set" in product.tags else "giftable")
        return None

    def _add_use_case_evidence(
        self,
        evidence: list[RankingEvidence],
        product: Product,
        intent: ShoppingIntent,
    ) -> None:
        use_cases = [
            signal.value
            for signal in intent.positive_signals
            if signal.kind == IntentSignalKind.CONTEXT and signal.field == "use_case"
        ]
        if not use_cases:
            return
        searchable_text = " ".join([product.description, *product.tags]).lower()
        for use_case in use_cases:
            profile = _REFERENCE_USE_CASE_PROFILES.get(use_case)
            if profile is None:
                continue
            canonical_positive = (
                self._catalogue_evidence.evaluate(product, FactFamily.ATTRIBUTE, "gentle")
                if use_case == "gentle-daily-cleansing"
                else None
            )
            positive_phrase = next((phrase for phrase in profile.positive_phrases if phrase in searchable_text), None)
            if canonical_positive and canonical_positive.state == EvidenceState.SUPPORTED:
                evidence.append(
                    RankingEvidence(
                        signal=f"use_case:{use_case}",
                        score=1.0,
                        weight=self._weights.use_case / len(use_cases),
                        evidence_source=canonical_positive.source,
                        evidence_value=canonical_positive.value,
                        label=profile.positive_label,
                    )
                )
            elif not product.catalogue_facts and positive_phrase:
                evidence.append(
                    RankingEvidence(
                        signal=f"use_case:{use_case}", score=1.0,
                        weight=self._weights.use_case / len(use_cases),
                        evidence_source="product.text", evidence_value=positive_phrase,
                        label=profile.positive_label,
                    )
                )
            canonical_conflict = (
                self._catalogue_evidence.evaluate(product, FactFamily.ATTRIBUTE, "makeup-remover")
                if use_case == "gentle-daily-cleansing"
                else None
            )
            conflict_tag = next((tag for tag in product.tags if tag in profile.conflicting_tags), None)
            if canonical_conflict and canonical_conflict.state == EvidenceState.SUPPORTED:
                evidence.append(
                    RankingEvidence(
                        signal=f"contextual_conflict:{use_case}",
                        score=-1.0,
                        weight=self._weights.contextual_conflict / len(use_cases),
                        evidence_source=canonical_conflict.source,
                        evidence_value=canonical_conflict.value,
                        label=profile.conflict_label,
                    )
                )
            elif not product.catalogue_facts and conflict_tag:
                evidence.append(
                    RankingEvidence(
                        signal=f"contextual_conflict:{use_case}", score=-1.0,
                        weight=self._weights.contextual_conflict / len(use_cases),
                        evidence_source="product.tags", evidence_value=conflict_tag,
                        label=profile.conflict_label,
                    )
                )


@dataclass(frozen=True)
class _LegacyEvidence:
    source: str
    value: str


def _price_proximity(price: Decimal, target: Decimal) -> float:
    return float(max(Decimal("0"), Decimal("1") - abs(price - target) / target))


def _target_price_label(price: Decimal, target: Decimal) -> str:
    difference = abs(price - target)
    if difference <= target * Decimal("0.15"):
        return f"Close to your ₹{_format_price(target)} target"
    if price <= target:
        return f"₹{_format_price(difference)} below your ₹{_format_price(target)} target"
    return f"₹{_format_price(difference)} above your ₹{_format_price(target)} target"


def _maximum_price_label(price: Decimal, maximum: Decimal) -> str:
    difference = maximum - price
    if difference <= 0:
        return f"Within your ₹{_format_price(maximum)} budget"
    if difference <= min(Decimal("200"), maximum * Decimal("0.10")):
        return f"₹{_format_price(difference)} below your maximum"
    return f"Within your ₹{_format_price(maximum)} budget"


def _preference_label(value: str) -> str:
    labels = {
        "sensitive-skin": "Sensitive-skin friendly",
        "fragrance-free": "Fragrance-free",
        "moisturiser": "Moisturiser match",
        "cleanser": "Cleanser match",
        "sunscreen": "Sunscreen match",
        "premium": "Premium",
        "lightweight": "Lightweight",
    }
    return labels.get(value, value.replace("-", " ").title())


def _format_price(price: Decimal) -> str:
    return f"{price:,.0f}"
