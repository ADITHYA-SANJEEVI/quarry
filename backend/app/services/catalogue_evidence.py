"""Deterministic, category-neutral evaluation of canonical catalogue facts."""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.catalogue_facts import (
    CANONICAL_CONCEPTS,
    FactAssertion,
    FactFamily,
    CanonicalProductFact,
    facts_are_mutually_exclusive,
)
from app.models.product import Product


class EvidenceState(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CatalogueEvidence:
    family: FactFamily
    concept: str
    state: EvidenceState
    provenance: str | None = None

    @property
    def source(self) -> str:
        return "product.fact"

    @property
    def value(self) -> str:
        return f"{self.family.value}:{self.concept}"


class CatalogueEvidenceService:
    """Reads sparse product truth; absence is always an explicit UNKNOWN."""

    def evaluate(self, product: Product, family: FactFamily, concept: str) -> CatalogueEvidence:
        if concept not in CANONICAL_CONCEPTS[family]:
            return CatalogueEvidence(family, concept, EvidenceState.UNKNOWN)
        requested = CanonicalProductFact(family=family, concept=concept)
        facts = tuple(product.catalogue_facts)
        direct = next(
            (fact for fact in facts if fact.family == family.value and fact.concept == concept),
            None,
        )
        if direct is not None:
            return CatalogueEvidence(
                family,
                concept,
                EvidenceState(direct.assertion),
                direct.provenance,
            )

        for fact in facts:
            candidate = CanonicalProductFact(
                family=FactFamily(fact.family),
                concept=fact.concept,
                assertion=FactAssertion(fact.assertion),
                provenance=fact.provenance,
                schema_version=fact.schema_version,
            )
            if candidate.assertion == FactAssertion.SUPPORTED and facts_are_mutually_exclusive(requested, candidate):
                return CatalogueEvidence(family, concept, EvidenceState.CONTRADICTED, fact.provenance)
        return CatalogueEvidence(family, concept, EvidenceState.UNKNOWN)

    def roles(self, product: Product) -> set[str]:
        roles = {
            fact.concept
            for fact in product.catalogue_facts
            if fact.family == FactFamily.ROLE.value and fact.assertion == FactAssertion.SUPPORTED.value
        }
        if product.catalogue_facts:
            return roles
        # Additive rollout compatibility for pre-11B rows. Category is never a
        # role fallback: broad taxonomy cannot satisfy a requested role.
        return {tag for tag in product.tags if tag in _LEGACY_ROLE_CONCEPTS}

    def supports(self, product: Product, family: FactFamily, concept: str) -> bool:
        return self.evaluate(product, family, concept).state == EvidenceState.SUPPORTED


_LEGACY_ROLE_CONCEPTS = {
    "cleanser", "moisturiser", "sunscreen", "serum", "toner", "lip-care", "body-care",
    "hand-care", "gift-set", "charger", "power-bank", "cable", "phone", "headphones",
    "laptop", "wearable", "case", "speaker", "keyboard", "monitor", "hub",
}
