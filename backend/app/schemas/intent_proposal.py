"""Strict, provider-neutral language proposals for Agentic Intent Intelligence.

These schemas intentionally describe only a customer's words.  They have no
product, pricing, stock, order, payment, merchant, or recommendation fields.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.intent import (
    ConfidenceBand,
    Enforcement,
    IntentSignalKind,
    IntentSignalPolarity,
    IntentSignalSource,
)


class IntentProposalSignalV1(BaseModel):
    """A bounded, non-authoritative interpretation of one utterance span."""

    value: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9 -]{0,119}$")
    source: Literal[IntentSignalSource.MODEL_INTERPRETED] = IntentSignalSource.MODEL_INTERPRETED
    confidence_band: Literal[
        ConfidenceBand.INFERRED_HIGH,
        ConfidenceBand.INFERRED_MEDIUM,
        ConfidenceBand.INFERRED_LOW,
        ConfidenceBand.EXPLICIT_SEMANTIC_HIGH,
    ] = ConfidenceBand.INFERRED_MEDIUM
    polarity: IntentSignalPolarity = IntentSignalPolarity.POSITIVE
    kind: IntentSignalKind = IntentSignalKind.PREFERENCE
    enforcement: Enforcement = Enforcement.SOFT
    field: Literal[
        "product_concept",
        "functional_role",
        "desired_attribute",
        "use_case",
        "hard_exclusion",
        "excluded_attribute",
        "recipient",
        "occasion",
        "context",
        "compatibility_requirement",
    ]
    evidence_span: tuple[int, int] | None = None

    # Extra fields are rejected; JSON numeric values remain valid for bounded
    # currency quantities returned by OpenAI-compatible providers.
    model_config = ConfigDict(extra="forbid")

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        return " ".join(value.lower().split())


class IntentProposalBudgetV1(BaseModel):
    kind: Literal["strict_min", "strict_max", "target", "stretch_ceiling"]
    amount: Decimal = Field(gt=0, le=10_000_000)
    source: Literal[IntentSignalSource.MODEL_INTERPRETED] = IntentSignalSource.MODEL_INTERPRETED
    confidence_band: Literal[
        ConfidenceBand.INFERRED_HIGH,
        ConfidenceBand.INFERRED_MEDIUM,
        ConfidenceBand.INFERRED_LOW,
        ConfidenceBand.EXPLICIT_SEMANTIC_HIGH,
    ] = ConfidenceBand.INFERRED_MEDIUM
    evidence_span: tuple[int, int] | None = None

    model_config = ConfigDict(extra="forbid")


class IntentProposalV1(BaseModel):
    """Versioned model output accepted at the language-interpretation boundary."""

    schema_version: Literal["intent_proposal_v1"] = "intent_proposal_v1"
    signals: list[IntentProposalSignalV1] = Field(default_factory=list, max_length=24)
    budgets: list[IntentProposalBudgetV1] = Field(default_factory=list, max_length=4)
    quantity: int | None = Field(default=None, gt=0, le=100)
    gifting_intent: bool | None = None
    clarification_needed: bool = False
    clarification_question: str | None = Field(default=None, max_length=180)

    model_config = ConfigDict(extra="forbid")
