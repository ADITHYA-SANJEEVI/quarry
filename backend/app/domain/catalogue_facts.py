"""Small, validated vocabulary for durable catalogue facts.

This is intentionally a controlled commerce vocabulary, not a generic ontology
engine. The evaluator consumes family/concept pairs without category branches.
"""

from collections.abc import Iterable
from enum import StrEnum
import re

from pydantic import BaseModel, field_validator


class FactFamily(StrEnum):
    ROLE = "role"
    ATTRIBUTE = "attribute"
    SUITABILITY = "suitability"
    USE_CASE = "use_case"
    COMPATIBILITY = "compatibility"


class FactAssertion(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"


class FactProvenance(StrEnum):
    CATALOGUE_FIXTURE = "catalogue_fixture"
    MERCHANT_ASSERTED = "merchant_asserted"
    STRUCTURED_IMPORT = "structured_import"


CATALOGUE_SCHEMA_VERSION = "v1"

# A deliberately compact controlled vocabulary for the current category packs.
# Adding a concept is an explicit reviewed change, not a merchant-supplied
# arbitrary string. The evidence engine does not branch by category.
CANONICAL_CONCEPTS: dict[FactFamily, frozenset[str]] = {
    FactFamily.ROLE: frozenset({
        "cleanser", "moisturiser", "sunscreen", "serum", "toner", "lip-care",
        "body-care", "hand-care", "gift-set", "charger", "power-bank", "cable",
        "phone", "headphones", "laptop", "wearable", "case", "speaker", "keyboard",
        "monitor", "hub",
    }),
    FactFamily.ATTRIBUTE: frozenset({
        "gentle", "fragrance-free", "fragranced", "gel", "oil-free", "lightweight",
        "mineral", "tinted", "premium", "giftable", "usb-c", "pd", "fast-charging",
        "noise-cancelling", "wireless", "over-ear", "large-screen", "non-gaming",
        "clear", "mechanical", "budget", "travel", "hydrating", "makeup-remover",
    }),
    FactFamily.SUITABILITY: frozenset({
        "sensitive-skin", "dry-skin", "oily-skin", "combination-skin", "daily-use",
        "commute", "study-work", "travel-use",
    }),
    FactFamily.USE_CASE: frozenset({
        "gift-ready", "daily-routine", "cleanser-moisturiser-routine", "sun-protection",
        "makeup-removal", "device-charging", "laptop-charging", "phone-charging",
    }),
    FactFamily.COMPATIBILITY: frozenset({"usb-c", "pd", "macbook", "laptop", "phone", "pixel-9"}),
}

MUTUALLY_EXCLUSIVE: dict[FactFamily, frozenset[frozenset[str]]] = {
    FactFamily.ATTRIBUTE: frozenset({frozenset(("fragrance-free", "fragranced"))}),
}


class CanonicalProductFact(BaseModel):
    family: FactFamily
    concept: str
    assertion: FactAssertion = FactAssertion.SUPPORTED
    provenance: FactProvenance = FactProvenance.CATALOGUE_FIXTURE
    schema_version: str = CATALOGUE_SCHEMA_VERSION

    @field_validator("concept")
    @classmethod
    def validate_concept_shape(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalized):
            raise ValueError("concept must be a lowercase canonical identifier.")
        return normalized

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != CATALOGUE_SCHEMA_VERSION:
            raise ValueError(f"unsupported catalogue schema version: {value}")
        return value

    def model_post_init(self, __context: object) -> None:
        if self.concept not in CANONICAL_CONCEPTS[self.family]:
            raise ValueError(f"unsupported {self.family.value} concept: {self.concept}")


def facts_are_mutually_exclusive(left: CanonicalProductFact, right: CanonicalProductFact) -> bool:
    return (
        left.family == right.family
        and frozenset((left.concept, right.concept)) in MUTUALLY_EXCLUSIVE.get(left.family, frozenset())
    )


def validate_fact_set(facts: Iterable[CanonicalProductFact]) -> tuple[CanonicalProductFact, ...]:
    """Reject duplicate or internally contradictory authoritative fixture data."""
    validated = tuple(facts)
    seen: set[tuple[FactFamily, str]] = set()
    for fact in validated:
        key = (fact.family, fact.concept)
        if key in seen:
            raise ValueError(f"duplicate canonical fact: {fact.family.value}:{fact.concept}")
        seen.add(key)
    for index, left in enumerate(validated):
        for right in validated[index + 1 :]:
            if (
                left.assertion == FactAssertion.SUPPORTED
                and right.assertion == FactAssertion.SUPPORTED
                and facts_are_mutually_exclusive(left, right)
            ):
                raise ValueError(
                    f"mutually exclusive canonical facts: {left.concept}, {right.concept}"
                )
    return validated
