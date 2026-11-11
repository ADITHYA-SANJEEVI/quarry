from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IntentSignalSource(StrEnum):
    EXPLICIT = "explicit"
    NORMALIZED_EXPLICIT = "normalized_explicit"
    MODEL_INTERPRETED = "model_interpreted"
    LANGUAGE_ANALYSIS = "language_analysis"
    INFERRED_BEHAVIOR = "inferred_behavior"
    HISTORICAL = "historical"
    POPULATION = "population"


class ConfidenceBand(StrEnum):
    EXPLICIT_DETERMINISTIC = "explicit_deterministic"
    EXPLICIT_SEMANTIC_HIGH = "explicit_semantic_high"
    INFERRED_HIGH = "inferred_high"
    INFERRED_MEDIUM = "inferred_medium"
    INFERRED_LOW = "inferred_low"


class Enforcement(StrEnum):
    HARD = "hard"
    SOFT = "soft"
    CLARIFY = "clarify"
    IGNORE = "ignore"


class IntentSignalPolarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class IntentSignalKind(StrEnum):
    PREFERENCE = "preference"
    EXCLUSION = "exclusion"
    COMPATIBILITY = "compatibility"
    CONTEXT = "context"
    FREEFORM = "freeform"


class IntentRequirementStrength(StrEnum):
    """Commercial strength, deliberately distinct from model confidence."""

    REQUIRED = "required"
    PREFERRED = "preferred"


class IntentRequirementProvenance(StrEnum):
    """How a deterministic parser obtained an interpreted element."""

    EXPLICIT = "explicit"
    NORMALIZED_ALIAS = "normalized_alias"
    FUZZY_NORMALIZED = "fuzzy_normalized"
    INFERRED_CONTEXT = "inferred_context"
    AMBIGUOUS = "ambiguous"


class IntentFactFamily(StrEnum):
    ROLE = "role"
    ATTRIBUTE = "attribute"
    SUITABILITY = "suitability"
    USE_CASE = "use_case"
    COMPATIBILITY = "compatibility"


class BudgetOperator(StrEnum):
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    TARGET = "target"
    RANGE = "range"
    STRETCH_CEILING = "stretch_ceiling"


class BudgetScope(StrEnum):
    PER_ITEM = "per_item"
    PROPOSITION_TOTAL = "proposition_total"
    UNKNOWN = "unknown"


class RoleCompositionOperator(StrEnum):
    AND = "and"
    OR = "or"


class IntentRequirement(BaseModel):
    """A canonical, typed customer requirement for IntentState v3."""

    concept: str = Field(min_length=1, max_length=120)
    family: IntentFactFamily
    polarity: IntentSignalPolarity = IntentSignalPolarity.POSITIVE
    strength: IntentRequirementStrength = IntentRequirementStrength.PREFERRED
    provenance: IntentRequirementProvenance = IntentRequirementProvenance.EXPLICIT
    target_role: str | None = Field(default=None, min_length=1, max_length=120)
    evidence_span: tuple[int, int] | None = None

    model_config = ConfigDict(extra="forbid")


class RoleClause(BaseModel):
    """A bounded commercial role group; no arbitrary Boolean expression tree."""

    roles: list[str] = Field(min_length=1, max_length=3)
    operator: RoleCompositionOperator = RoleCompositionOperator.AND
    alternatives: list[list[str]] = Field(default_factory=list, max_length=3)
    owner: str | None = Field(default=None, max_length=80)
    scope: BudgetScope = BudgetScope.UNKNOWN
    provenance: IntentRequirementProvenance = IntentRequirementProvenance.EXPLICIT

    model_config = ConfigDict(extra="forbid")


class IntentBudget(BaseModel):
    """Canonical money semantics. Paise avoids float authority at boundaries."""

    operator: BudgetOperator
    amount_paise: int = Field(ge=0)
    scope: BudgetScope = BudgetScope.UNKNOWN
    approximate: bool = False
    provenance: IntentRequirementProvenance = IntentRequirementProvenance.EXPLICIT

    model_config = ConfigDict(extra="forbid")

    @property
    def amount(self) -> Decimal:
        return Decimal(self.amount_paise) / Decimal(100)


class IntentAmbiguity(BaseModel):
    term: str = Field(min_length=1, max_length=120)
    candidates: list[str] = Field(min_length=2, max_length=4)
    affected_family: IntentFactFamily
    commerce_material: bool = True

    model_config = ConfigDict(extra="forbid")


class IntentSignal(BaseModel):
    """A typed, provenance-carrying unit of customer intent.

    ``confidence`` remains an additive v1 diagnostic for API compatibility. The
    confidence band and enforcement policy are the durable decision inputs.
    """

    value: str = Field(min_length=1, max_length=120)
    source: IntentSignalSource = IntentSignalSource.EXPLICIT
    confidence: float = Field(default=1.0, ge=0, le=1)
    confidence_band: ConfidenceBand = ConfidenceBand.EXPLICIT_DETERMINISTIC
    polarity: IntentSignalPolarity
    kind: IntentSignalKind
    enforcement: Enforcement = Enforcement.SOFT
    field: str = Field(default="desired_attribute", min_length=1, max_length=80)
    evidence_span: tuple[int, int] | None = None

    model_config = ConfigDict(extra="forbid")


class IntentDetails(BaseModel):
    """Category-agnostic IntentState v3 with additive v1/v2 compatibility views."""

    # V1 adapter fields retained for the existing /api/search response and
    # reference storefront. No new platform behavior should depend on them.
    category: str | None = None
    strict_min_price: Decimal | None = Field(default=None, ge=0)
    strict_max_price: Decimal | None = Field(default=None, ge=0)
    target_price: Decimal | None = Field(default=None, gt=0)
    recipient: str | None = None
    occasion: str | None = None
    gifting_intent: bool = False
    signals: list[IntentSignal] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    clarification_needed: bool = False
    clarification_question: str | None = None

    # V2 fields. Values are merchant/catalogue supplied; core field names are
    # deliberately category-neutral.
    product_concepts: list[IntentSignal] = Field(default_factory=list)
    desired_attributes: list[IntentSignal] = Field(default_factory=list)
    excluded_attributes: list[IntentSignal] = Field(default_factory=list)
    hard_exclusions: list[IntentSignal] = Field(default_factory=list)
    price_constraints: list[IntentSignal] = Field(default_factory=list)
    stretch_ceiling: Decimal | None = Field(default=None, gt=0)
    quantity: int | None = Field(default=None, gt=0)
    brand_preferences: list[IntentSignal] = Field(default_factory=list)
    compatibility_requirements: list[IntentSignal] = Field(default_factory=list)
    freeform_constraints: list[IntentSignal] = Field(default_factory=list)
    clarification_state: Enforcement = Enforcement.IGNORE

    # V3 authoritative deterministic interpretation. Existing scalar and
    # signal fields above remain compatibility views while consumers migrate.
    intent_schema_version: str = "intent_state_v3"
    role_clauses: list[RoleClause] = Field(default_factory=list)
    requirements: list[IntentRequirement] = Field(default_factory=list)
    budgets: list[IntentBudget] = Field(default_factory=list)
    budget_ambiguity: str | None = Field(default=None, max_length=120)
    ambiguities: list[IntentAmbiguity] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_intent(self) -> "IntentDetails":
        if (
            self.strict_min_price is not None
            and self.strict_max_price is not None
            and self.strict_min_price > self.strict_max_price
        ):
            raise ValueError("strict_min_price must be less than or equal to strict_max_price.")
        if self.clarification_needed and not self.clarification_question:
            raise ValueError("clarification_question is required when clarification is needed.")
        return self

    @property
    def positive_signals(self) -> list[IntentSignal]:
        return [signal for signal in self.signals if signal.polarity == IntentSignalPolarity.POSITIVE]

    @property
    def negative_signals(self) -> list[IntentSignal]:
        return [signal for signal in self.signals if signal.polarity == IntentSignalPolarity.NEGATIVE]

    @property
    def hard_negative_signals(self) -> list[IntentSignal]:
        return [
            signal
            for signal in self.negative_signals
            if signal.enforcement == Enforcement.HARD
        ]

    @property
    def resolved_roles(self) -> tuple[str, ...]:
        """Stable role view for new consumers without changing event snapshots."""
        return tuple(
            dict.fromkeys(role for clause in self.role_clauses for role in clause.roles)
        )


class ShoppingIntent(IntentDetails):
    raw_query: str = Field(min_length=1, max_length=500)
