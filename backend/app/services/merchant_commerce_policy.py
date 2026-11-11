"""Shared deterministic commercial policy for compiler and merchant offers."""

from itertools import combinations

from app.models.product import Product
from app.schemas.intent import IntentSignalKind, IntentSignalSource, ShoppingIntent
from app.services.catalogue_evidence import CatalogueEvidenceService


class Compatibility:
    COMPLEMENTARY = "complementary"
    NEUTRAL = "neutral"
    REDUNDANT = "redundant"
    INCOMPATIBLE = "incompatible"


class MerchantCommercePolicy:
    """The small, explicit policy boundary for fixed commercial combinations."""

    policy_version = "v1"
    max_component_count = 3
    min_component_count = 2
    _ROLE_PRIORITY = (
        "cleanser",
        "moisturiser",
        "sunscreen",
        "serum",
        "toner",
        "lip-care",
        "body-care",
        "hand-care",
        "charger",
        "power-bank",
        "cable",
        "hub",
        "phone",
        "headphones",
        "laptop",
        "wearable",
        "case",
        "speaker",
        "keyboard",
        "monitor",
        "gift-set",
    )
    _PAIR_RULES = {
        frozenset(("cleanser", "moisturiser")): Compatibility.COMPLEMENTARY,
        frozenset(("charger", "cable")): Compatibility.COMPLEMENTARY,
        frozenset(("charger", "power-bank")): Compatibility.COMPLEMENTARY,
        frozenset(("power-bank", "cable")): Compatibility.COMPLEMENTARY,
        frozenset(("charger", "hub")): Compatibility.COMPLEMENTARY,
    }
    _evidence = CatalogueEvidenceService()

    @classmethod
    def product_role(cls, product: Product) -> str | None:
        values = cls._evidence.roles(product)
        return next((role for role in cls._ROLE_PRIORITY if role in values), None)

    @classmethod
    def requested_roles(cls, intent: ShoppingIntent) -> tuple[str, ...]:
        # IntentState v3 preserves commercial composition explicitly. The
        # legacy signal view remains a fallback for historical/test intents.
        values = set(intent.resolved_roles) or {
            signal.value
            for signal in intent.positive_signals
            if signal.kind == IntentSignalKind.PREFERENCE
            and signal.source in {
                IntentSignalSource.EXPLICIT,
                IntentSignalSource.NORMALIZED_EXPLICIT,
            }
        }
        return tuple(role for role in cls._ROLE_PRIORITY if role in values)

    @classmethod
    def relationship(cls, left: Product, right: Product) -> str:
        left_role, right_role = cls.product_role(left), cls.product_role(right)
        if left_role is None or right_role is None:
            return Compatibility.NEUTRAL
        if left_role == right_role:
            return Compatibility.REDUNDANT
        return cls._PAIR_RULES.get(frozenset((left_role, right_role)), Compatibility.NEUTRAL)

    @classmethod
    def pair_is_allowed(cls, left: Product, right: Product) -> bool:
        return cls.relationship(left, right) not in {
            Compatibility.REDUNDANT,
            Compatibility.INCOMPATIBLE,
        }

    @classmethod
    def components_are_allowed(cls, products: tuple[Product, ...] | list[Product]) -> bool:
        return (
            cls.min_component_count <= len(products) <= cls.max_component_count
            and len({product.id for product in products}) == len(products)
            and all(cls.pair_is_allowed(left, right) for left, right in combinations(products, 2))
        )
