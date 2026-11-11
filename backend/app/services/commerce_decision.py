"""Pure v1 summary of authoritative search outcomes.

This module deliberately receives results that have already been decided by
the parser, search service, MerchantOffer service, and Compiler. It must never
query or recompute commerce facts.
"""

from app.schemas.commerce_decision import CommerceDecisionV1, CommercialObjectReference
from app.services.commerce_compiler import CompilerState


def build_commerce_decision(
    *,
    clarification_needed: bool,
    catalogue_adequate: bool,
    recommendation_count: int,
    no_match: bool,
    compiler_state: CompilerState,
    compiler_reason_code: str | None,
    merchant_offer_proposition_id: str | None,
    generated_proposition_id: str | None,
) -> CommerceDecisionV1:
    """Summarize the existing result without altering outcome precedence."""
    if clarification_needed:
        return CommerceDecisionV1(
            kind="clarification_required",
            source="intent_parser",
            reason_codes=["clarification_required"],
            catalogue_adequacy="not_evaluated",
        )

    adequacy = (
        "adequate"
        if catalogue_adequate
        else "no_eligible_result"
        if no_match
        else "partial"
    )
    if merchant_offer_proposition_id is not None:
        return CommerceDecisionV1(
            kind="merchant_offer",
            source="merchant_offer",
            reason_codes=["merchant_offer_adequate"],
            catalogue_adequacy=adequacy,
            commercial_object=CommercialObjectReference(
                type="commerce_proposition",
                id=merchant_offer_proposition_id,
                source="merchant_offer",
            ),
        )
    if generated_proposition_id is not None:
        return CommerceDecisionV1(
            kind="generated_proposition",
            source="commerce_compiler",
            reason_codes=["compiler_feasible"],
            catalogue_adequacy=adequacy,
            commercial_object=CommercialObjectReference(
                type="commerce_proposition",
                id=generated_proposition_id,
                source="commerce_compiler",
            ),
        )
    if compiler_state == CompilerState.NO_FEASIBLE_PROPOSITION:
        return CommerceDecisionV1(
            kind="no_feasible_commerce",
            source="commerce_compiler",
            reason_codes=[_no_feasible_reason(compiler_reason_code)],
            catalogue_adequacy=adequacy,
        )
    if no_match or recommendation_count == 0:
        return CommerceDecisionV1(
            kind="no_feasible_commerce",
            source="catalogue",
            reason_codes=["no_eligible_candidates"],
            catalogue_adequacy="no_eligible_result",
        )
    return CommerceDecisionV1(
        kind="catalogue_recommendations",
        source="catalogue",
        reason_codes=[
            "single_product_adequate" if catalogue_adequate else "catalogue_partial_coverage"
        ],
        catalogue_adequacy=adequacy,
    )


def _no_feasible_reason(reason_code: str | None) -> str:
    return {
        "budget": "strict_budget_unsatisfied",
        "incompatible": "no_feasible_combination",
        "insufficient_complementary_items": "insufficient_complementary_catalogue",
    }.get(reason_code, "no_feasible_combination")
