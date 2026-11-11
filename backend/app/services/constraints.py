from dataclasses import dataclass
import re

from app.models.product import Product
from app.domain.catalogue_facts import FactFamily
from app.schemas.intent import Enforcement, IntentSignalPolarity, ShoppingIntent
from app.services.catalogue_evidence import CatalogueEvidenceService, EvidenceState


@dataclass(frozen=True)
class ConstraintResult:
    eligible: list[Product]
    excluded_count: int


class ConstraintEngine:
    """The deterministic eligibility authority before and after relevance.

    Relevance is allowed to discover candidates, never to relax this policy.
    Both gates intentionally evaluate the same hard rules so a future dense or
    reranking stage cannot resurrect an ineligible product.
    """

    def __init__(self, evidence: CatalogueEvidenceService | None = None) -> None:
        self._evidence = evidence or CatalogueEvidenceService()

    def pre_retrieval(self, candidates: list[Product], intent: ShoppingIntent) -> ConstraintResult:
        # Stock, price, exclusions and compatibility are cheap structured
        # pre-retrieval gates. Role eligibility remains a final safety gate so
        # it cannot distort the candidate-relative relevance score used by the
        # established ranker/Compiler paths.
        return self._apply_hard_constraints(candidates, intent, enforce_roles=False)

    def apply(self, candidates: list[Product], intent: ShoppingIntent) -> ConstraintResult:
        """Final safety gate retained under the v1 method name for compatibility."""
        return self._apply_hard_constraints(candidates, intent, enforce_roles=True)

    def _apply_hard_constraints(
        self, candidates: list[Product], intent: ShoppingIntent, *, enforce_roles: bool
    ) -> ConstraintResult:
        # ProductFacts are the authoritative role vocabulary. A factless
        # historical/fixture catalogue has no canonical negative role
        # evidence, so preserve its established relevance path rather than
        # turning legacy tags into a newly introduced hard filter.
        role_gate_active = (
            enforce_roles
            and bool(intent.resolved_roles)
            and any(product.catalogue_facts for product in candidates)
        )
        eligible = [
            product for product in candidates if self._is_eligible(product, intent, enforce_roles=role_gate_active)
        ]
        return ConstraintResult(
            eligible=eligible,
            excluded_count=len(candidates) - len(eligible),
        )

    def _is_eligible(self, product: Product, intent: ShoppingIntent, *, enforce_roles: bool) -> bool:
        if not product.catalogue_active or product.stock <= 0:
            return False
        if intent.strict_min_price is not None and product.price < intent.strict_min_price:
            return False
        if intent.strict_max_price is not None and product.price > intent.strict_max_price:
            return False

        # Only deterministic RoleClauses establish role eligibility. Soft
        # model language may improve retrieval/ranking but cannot turn a role
        # into an eligibility or compiler/offer authority.
        requested_roles = intent.resolved_roles
        # An individual product may satisfy one explicit requested role in a
        # multi-role request, but must not leak in from an unrelated role.
        # Compiler-owned propositions can still combine eligible products to
        # cover the requested roles jointly.
        if enforce_roles and requested_roles and not any(
            self._has_role(product, role) for role in requested_roles
        ):
            return False

        exclusions = [signal.value for signal in intent.hard_negative_signals]
        if any(self._directly_conflicts(product, exclusion) for exclusion in exclusions):
            return False

        hard_compatibility = [
            signal.value
            for signal in intent.compatibility_requirements
            if signal.enforcement == Enforcement.HARD
            and signal.polarity == IntentSignalPolarity.POSITIVE
        ]
        excluded_compatibility = [
            signal.value
            for signal in intent.compatibility_requirements
            if signal.enforcement == Enforcement.HARD
            and signal.polarity == IntentSignalPolarity.NEGATIVE
        ]
        return (
            all(self._has_structured_value(product, requirement) for requirement in hard_compatibility)
            and not any(self._has_structured_value(product, requirement) for requirement in excluded_compatibility)
        )

    def _has_role(self, product: Product, value: str) -> bool:
        evidence = self._evidence.evaluate(product, FactFamily.ROLE, value)
        if evidence.state != EvidenceState.UNKNOWN:
            return evidence.state == EvidenceState.SUPPORTED
        if product.catalogue_facts:
            return False
        return any(self._normalized(value) == self._normalized(candidate) for candidate in product.tags)

    def _has_structured_value(self, product: Product, value: str) -> bool:
        evidence = self._evidence.evaluate(product, FactFamily.COMPATIBILITY, value)
        if evidence.state != EvidenceState.UNKNOWN:
            return evidence.state == EvidenceState.SUPPORTED
        if product.catalogue_facts:
            return False
        return any(
            self._normalized(value) == self._normalized(candidate)
            for candidate in product.tags
        )

    def _directly_conflicts(self, product: Product, exclusion: str) -> bool:
        for family in (FactFamily.ATTRIBUTE, FactFamily.SUITABILITY, FactFamily.USE_CASE, FactFamily.COMPATIBILITY, FactFamily.ROLE):
            evidence = self._evidence.evaluate(product, family, exclusion)
            if evidence.state != EvidenceState.UNKNOWN:
                return evidence.state == EvidenceState.SUPPORTED
        if product.catalogue_facts:
            return False
        return any(
            self._normalized(exclusion) == self._normalized(candidate)
            for candidate in product.tags
        )

    @staticmethod
    def _normalized(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
