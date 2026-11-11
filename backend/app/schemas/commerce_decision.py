from typing import Literal

from pydantic import BaseModel, Field


CommerceDecisionKind = Literal[
    "clarification_required",
    "catalogue_recommendations",
    "merchant_offer",
    "generated_proposition",
    "no_feasible_commerce",
]
CommerceDecisionSource = Literal[
    "intent_parser",
    "catalogue",
    "merchant_offer",
    "commerce_compiler",
]
CatalogueAdequacyState = Literal[
    "not_evaluated",
    "adequate",
    "partial",
    "no_eligible_result",
]
CommercialObjectSource = Literal["merchant_offer", "commerce_compiler"]


class CommercialObjectReference(BaseModel):
    """Reference to an existing response payload; never a duplicate snapshot."""

    type: Literal["commerce_proposition"]
    id: str = Field(min_length=1, max_length=36)
    source: CommercialObjectSource


class CommerceDecisionV1(BaseModel):
    """Additive, authoritative summary of an already-computed search outcome."""

    schema_version: Literal["commerce_decision_v1"] = "commerce_decision_v1"
    kind: CommerceDecisionKind
    source: CommerceDecisionSource
    reason_codes: list[str] = Field(min_length=1, max_length=4)
    catalogue_adequacy: CatalogueAdequacyState
    commercial_object: CommercialObjectReference | None = None
