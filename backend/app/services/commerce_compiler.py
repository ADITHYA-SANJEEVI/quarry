"""Bounded, deterministic generation of sellable multi-product propositions."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import combinations
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.commerce_proposition import (
    CommercePropositionItemRecord,
    CommercePropositionRecord,
    GeneratedPropositionStatus,
)
from app.models.product import Product
from app.domain.catalogue_facts import FactFamily
from app.schemas.intent import IntentSignalKind, ShoppingIntent
from app.services.commerce_ranker import RankedCandidate
from app.services.constraints import ConstraintEngine
from app.services.merchant_commerce_policy import Compatibility, MerchantCommercePolicy
from app.services.catalogue_evidence import CatalogueEvidenceService, EvidenceState
from app.services.intent_normalization import has_composition_cue


class CompilerState(StrEnum):
    NOT_NEEDED = "not_needed"
    AVAILABLE = "available"
    NO_FEASIBLE_PROPOSITION = "no_feasible_proposition"
    CLARIFICATION_REQUIRED = "clarification_required"


@dataclass(frozen=True)
class CompilerWeights:
    coverage: float = 0.35
    relevance: float = 0.25
    price_fit: float = 0.15
    compatibility: float = 0.15
    context_fit: float = 0.10
    overlap: float = 0.20
    max_candidates: int = 12
    top_ranked_candidates: int = 8
    complementary_candidates: int = 4
    max_combinations: int = 286
    validity_minutes: int = 15
    minimum_utility: float = 0.30


DEFAULT_COMPILER_WEIGHTS = CompilerWeights()


@dataclass(frozen=True)
class CompilerEvidence:
    kind: str
    label: str
    evidence_source: str
    evidence_value: str


@dataclass(frozen=True)
class CompiledProposition:
    record: CommercePropositionRecord
    evidence: list[CompilerEvidence]


@dataclass(frozen=True)
class CompilerResult:
    state: CompilerState
    reason_code: str | None = None
    proposition: CompiledProposition | None = None


class ReferenceBundlePolicy(MerchantCommercePolicy):
    """Compatibility name retained for the established compiler/test boundary."""


class CommerceCompiler:
    """Creates at most one persisted proposition from final eligible products."""

    def __init__(
        self,
        *,
        policy: MerchantCommercePolicy = ReferenceBundlePolicy(),
        weights: CompilerWeights = DEFAULT_COMPILER_WEIGHTS,
    ) -> None:
        self._policy = policy
        self._weights = weights
        self._catalogue_evidence = CatalogueEvidenceService()

    def compile(
        self,
        session: Session,
        *,
        intent: ShoppingIntent,
        ranked: list[RankedCandidate],
        eligible_products: list[Product],
        adequate: bool,
        commercial_option_adequate: bool = False,
    ) -> CompilerResult:
        if intent.clarification_needed:
            return CompilerResult(CompilerState.CLARIFICATION_REQUIRED, "clarification_required")
        if commercial_option_adequate:
            return CompilerResult(CompilerState.NOT_NEEDED, "merchant_offer_adequate")

        # Defence in depth: callers should pass the final search gate, but the
        # compiler never trusts that boundary implicitly.
        eligible_products = ConstraintEngine().apply(eligible_products, intent).eligible
        roles = self._policy.requested_roles(intent)
        explicit_bundle = has_composition_cue(intent.raw_query)
        top_coverage = self._coverage_for_product(ranked[0].product, intent, roles) if ranked else 0.0
        if (adequate and top_coverage >= 1.0) or (len(roles) < 2 and not explicit_bundle):
            return CompilerResult(CompilerState.NOT_NEEDED)
        if len(eligible_products) < 2:
            return CompilerResult(
                CompilerState.NO_FEASIBLE_PROPOSITION, "insufficient_complementary_items"
            )

        pool = self._candidate_pool(ranked, eligible_products, roles)
        feasible: list[tuple[float, float, Decimal, tuple[Product, ...], list[CompilerEvidence]]] = []
        examined = 0
        for size in (2, 3):
            for items in combinations(pool, size):
                examined += 1
                if examined > self._weights.max_combinations:
                    break
                if not self._feasible(items, intent):
                    continue
                coverage = self._coverage_for_items(items, intent, roles)
                # A proposition is an all-or-nothing commercial promise: a
                # partial role combination may be relevant, but cannot be
                # persisted as the answer to a multi-role request.
                if not self._covers_requested_roles(items, roles):
                    continue
                if coverage <= top_coverage:
                    continue
                utility, evidence = self._utility(items, intent, ranked, roles, coverage)
                if utility >= self._weights.minimum_utility:
                    feasible.append((utility, coverage, sum((item.price for item in items), Decimal("0")), items, evidence))
            if examined > self._weights.max_combinations:
                break

        if not feasible:
            return CompilerResult(
                CompilerState.NO_FEASIBLE_PROPOSITION,
                self._failure_reason(pool, intent),
            )

        feasible.sort(key=lambda item: (-item[1], item[2], len(item[3]), tuple(product.id for product in item[3])))
        _, _, _, items, evidence = feasible[0]
        return CompilerResult(
            CompilerState.AVAILABLE,
            proposition=self._persist(session, intent, items, evidence),
        )

    def _candidate_pool(
        self,
        ranked: list[RankedCandidate],
        eligible_products: list[Product],
        roles: tuple[str, ...],
    ) -> list[Product]:
        selected: list[Product] = [candidate.product for candidate in ranked[: self._weights.top_ranked_candidates]]
        selected_ids = {product.id for product in selected}
        role_additions = 0
        for product in sorted(eligible_products, key=lambda item: (item.price, item.id)):
            if len(selected) >= self._weights.max_candidates:
                break
            role = self._policy.product_role(product)
            if (
                product.id not in selected_ids
                and role in roles
                and role_additions < self._weights.complementary_candidates
            ):
                selected.append(product)
                selected_ids.add(product.id)
                role_additions += 1
        for product in sorted(eligible_products, key=lambda item: (item.price, item.id)):
            if len(selected) >= self._weights.max_candidates:
                break
            if product.id not in selected_ids:
                selected.append(product)
                selected_ids.add(product.id)
        return selected

    def _covers_requested_roles(self, items: tuple[Product, ...], roles: tuple[str, ...]) -> bool:
        return set(roles).issubset({self._policy.product_role(product) for product in items})

    def _feasible(self, items: tuple[Product, ...], intent: ShoppingIntent) -> bool:
        if not 2 <= len(items) <= 3 or any(
            not product.catalogue_active or product.stock <= 0 for product in items
        ):
            return False
        total = sum((product.price for product in items), Decimal("0"))
        if intent.strict_min_price is not None and total < intent.strict_min_price:
            return False
        if intent.strict_max_price is not None and total > intent.strict_max_price:
            return False
        exclusions = {signal.value for signal in intent.hard_negative_signals}
        if any(exclusion in {product.category, *product.tags} for product in items for exclusion in exclusions):
            return False
        return all(self._policy.pair_is_allowed(left, right) for left, right in combinations(items, 2))

    def _coverage_for_product(self, product: Product, intent: ShoppingIntent, roles: tuple[str, ...]) -> float:
        return self._coverage_for_items((product,), intent, roles)

    def _coverage_for_items(
        self, items: tuple[Product, ...], intent: ShoppingIntent, roles: tuple[str, ...]
    ) -> float:
        targets = set(roles)
        targets.update(
            signal.value
            for signal in intent.positive_signals
            if signal.kind == IntentSignalKind.PREFERENCE and signal.value not in roles
        )
        if not targets:
            return 0.0
        supported_values = {
            fact.concept
            for product in items
            for fact in product.catalogue_facts
            if fact.assertion == "supported"
        }
        # Existing rows without canonical facts keep legacy tag coverage during
        # additive rollout; category never substitutes for role coverage.
        supported_values.update(value for product in items if not product.catalogue_facts for value in product.tags)
        item_roles = {self._policy.product_role(product) for product in items}
        return sum(target in supported_values or target in item_roles for target in targets) / len(targets)

    def _utility(
        self,
        items: tuple[Product, ...],
        intent: ShoppingIntent,
        ranked: list[RankedCandidate],
        roles: tuple[str, ...],
        coverage: float,
    ) -> tuple[float, list[CompilerEvidence]]:
        rank_by_id = {candidate.product.id: candidate.score for candidate in ranked}
        relevance = sum(rank_by_id.get(item.id, 0.0) for item in items) / len(items)
        total = sum((item.price for item in items), Decimal("0"))
        price_fit = self._price_fit(total, intent)
        relationships = [self._policy.relationship(left, right) for left, right in combinations(items, 2)]
        compatibility = (
            sum(value == Compatibility.COMPLEMENTARY for value in relationships) / len(relationships)
            if relationships
            else 0.0
        )
        context_fit = float(
            intent.gifting_intent and any({"giftable", "gift-set"} & set(item.tags) for item in items)
        )
        item_roles = [self._policy.product_role(item) for item in items]
        overlap = (len(item_roles) - len(set(item_roles))) / len(item_roles)
        utility = (
            self._weights.coverage * coverage
            + self._weights.relevance * relevance
            + self._weights.price_fit * price_fit
            + self._weights.compatibility * compatibility
            + self._weights.context_fit * context_fit
            - self._weights.overlap * overlap
        )
        evidence = self._evidence(items, intent, roles, total, context_fit > 0)
        return utility, evidence

    @staticmethod
    def _price_fit(total: Decimal, intent: ShoppingIntent) -> float:
        if intent.target_price is None:
            return 1.0 if intent.strict_max_price is not None else 0.5
        return float(max(Decimal("0"), Decimal("1") - abs(total - intent.target_price) / intent.target_price))

    def _evidence(
        self,
        items: tuple[Product, ...],
        intent: ShoppingIntent,
        roles: tuple[str, ...],
        total: Decimal,
        gift_ready: bool,
    ) -> list[CompilerEvidence]:
        evidence: list[CompilerEvidence] = []
        for role in roles:
            product = next((item for item in items if self._policy.product_role(item) == role), None)
            if product:
                state = self._catalogue_evidence.evaluate(product, FactFamily.ROLE, role)
                evidence.append(CompilerEvidence(
                    "coverage",
                    f"Covers: {role.replace('-', ' ')}",
                    state.source if state.state == EvidenceState.SUPPORTED else "product.tags",
                    state.value if state.state == EvidenceState.SUPPORTED else role,
                ))
        for signal in intent.positive_signals:
            signal_evidence = None
            for item in items:
                signal_evidence = self._signal_evidence(item, signal.value)
                if signal_evidence is not None:
                    break
            if signal_evidence and signal.value not in roles:
                evidence.append(
                    CompilerEvidence(
                        "coverage",
                        f"Covers: {signal.value.replace('-', ' ')}",
                        signal_evidence[0],
                        signal_evidence[1],
                    )
                )
        if gift_ready:
            product = next(item for item in items if {"giftable", "gift-set"} & set(item.tags))
            tag = "gift-set" if "gift-set" in product.tags else "giftable"
            evidence.append(CompilerEvidence("context", "Gift-ready combination", "product.tags", tag))
        if intent.strict_max_price is not None:
            evidence.append(
                CompilerEvidence("price", f"Within your ₹{intent.strict_max_price:,.0f} maximum", "proposition.total", str(total))
            )
        return evidence

    def _signal_evidence(self, product: Product, value: str) -> tuple[str, str] | None:
        for family in FactFamily:
            state = self._catalogue_evidence.evaluate(product, family, value)
            if state.state == EvidenceState.SUPPORTED:
                return (state.source, state.value)
        if not product.catalogue_facts and value in product.tags:
            return ("product.tags", value)
        return None

    def _persist(
        self,
        session: Session,
        intent: ShoppingIntent,
        products: tuple[Product, ...],
        evidence: list[CompilerEvidence],
    ) -> CompiledProposition:
        proposition_id = str(uuid4())
        total = sum((product.price for product in products), Decimal("0.00"))
        record = CommercePropositionRecord(
            id=proposition_id,
            type="generated_bundle",
            source="compiler",
            title=self._title(intent, products),
            currency="INR",
            subtotal=total,
            discount=Decimal("0.00"),
            total=total,
            intent_reference=str(uuid4()),
            intent_json=intent.model_dump(mode="json"),
            evidence_json=[item.__dict__ for item in evidence],
            metadata_json={"compiler_policy_version": "v1", "candidate_limit": str(self._weights.max_candidates)},
            status=GeneratedPropositionStatus.AVAILABLE,
            expires_at=datetime.now(UTC) + timedelta(minutes=self._weights.validity_minutes),
        )
        record.items = [
            CommercePropositionItemRecord(
                product_id=product.id,
                product_name_snapshot=product.name,
                quantity=1,
                unit_price_snapshot=product.price,
                line_total=product.price,
            )
            for product in products
        ]
        session.add(record)
        session.commit()
        session.refresh(record)
        return CompiledProposition(record=record, evidence=evidence)

    def _title(self, intent: ShoppingIntent, products: tuple[Product, ...]) -> str:
        roles = {self._policy.product_role(product) for product in products}
        if intent.gifting_intent and {"cleanser", "moisturiser"} <= roles:
            return "Calm Skin Gift Set"
        if {"cleanser", "moisturiser"} <= roles:
            return "Everyday Skin Pair"
        if "charger" in roles and {"cable", "power-bank"} & roles:
            return "Travel Charging Kit"
        role = next((item for item in self._policy._ROLE_PRIORITY if item in roles), "selected")
        return f"Your selected {role.replace('-', ' ')} bundle"

    def _failure_reason(self, pool: list[Product], intent: ShoppingIntent) -> str:
        if intent.strict_max_price is not None and all(
            sum((product.price for product in pair), Decimal("0")) > intent.strict_max_price
            for pair in combinations(pool, 2)
        ):
            return "budget"
        if len(pool) < 2:
            return "insufficient_complementary_items"
        return "incompatible"
