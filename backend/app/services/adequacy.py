from dataclasses import dataclass

from app.schemas.intent import IntentSignalKind, ShoppingIntent
from app.services.commerce_ranker import RankedCandidate
from app.services.merchant_commerce_policy import MerchantCommercePolicy


@dataclass(frozen=True)
class AdequacyResult:
    adequate: bool
    score: float
    reason: str
    adequacy_state: str
    required_roles: tuple[str, ...]
    covered_roles: tuple[str, ...]
    missing_roles: tuple[str, ...]
    hard_constraints_satisfied: bool
    evidence_completeness: float
    top_result_strength: float
    alternative_strength: float
    reason_codes: tuple[str, ...]
    policy_version: str = "commerce_adequacy_policy_v1"


class AdequacyEvaluator:
    """Typed, deterministic catalogue sufficiency policy; never a probability.

    ``0.25`` retains the established minimum useful ranking strength. Evidence
    completeness is a bounded ratio of direct requested-preference evidence.
    Multi-role catalogue sufficiency deliberately requires one ordinary result
    to cover the complete role set; otherwise composition belongs to the
    MerchantOffer/Compiler path rather than a misleading single SKU result.
    """

    policy_version = "commerce_adequacy_policy_v1"
    minimum_top_strength = 0.25

    def evaluate(
        self, recommendations: list[RankedCandidate], intent: ShoppingIntent,
        *, eligible_candidates: list[RankedCandidate] | None = None,
    ) -> AdequacyResult:
        roles = MerchantCommercePolicy.requested_roles(intent)
        if not recommendations:
            state = "incomplete_role_coverage" if roles and eligible_candidates else "no_feasible_commerce"
            return AdequacyResult(
                False, 0.0, "No available catalogue result meets the request.", state,
                roles, (), roles, False, 0.0, 0.0, 0.0,
                ("no_ranked_eligible_result",), self.policy_version,
            )

        top = recommendations[0]
        ranked_pool = eligible_candidates or recommendations
        covered = tuple(sorted({
            role for candidate in ranked_pool for role in roles
            if MerchantCommercePolicy.product_role(candidate.product) == role
        }))
        missing = tuple(role for role in roles if role not in covered)
        top_roles = {MerchantCommercePolicy.product_role(top.product)}
        multi_role_complete = not roles or set(roles).issubset(top_roles)
        requested_preferences = {
            signal.value
            for signal in intent.positive_signals
            if signal.kind == IntentSignalKind.PREFERENCE
        }
        matched_preferences = {
            item.signal.removeprefix("preference:")
            for item in top.evidence
            if item.signal.startswith("preference:")
        }
        missing_preferences = requested_preferences - matched_preferences
        evidence_completeness = (
            (len(requested_preferences) - len(missing_preferences)) / len(requested_preferences)
            if requested_preferences else 1.0
        )
        alternative_strength = recommendations[1].score if len(recommendations) > 1 else 0.0
        score = round(
            0.45 * top.score + 0.30 * evidence_completeness
            + 0.15 * float(not missing) + 0.10 * float(multi_role_complete), 4
        )
        if missing:
            return AdequacyResult(False, score, "Available products do not cover every requested role.", "incomplete_role_coverage", roles, covered, missing, True, evidence_completeness, top.score, alternative_strength, ("missing_required_roles",), self.policy_version)
        if not multi_role_complete:
            return AdequacyResult(False, score, "No single catalogue result covers the complete multi-role request.", "incomplete_role_coverage", roles, covered, (), True, evidence_completeness, top.score, alternative_strength, ("multi_role_requires_composition",), self.policy_version)
        if missing_preferences:
            return AdequacyResult(False, score, "Available results do not have direct evidence for every requested preference.", "weak_catalogue", roles, covered, (), True, evidence_completeness, top.score, alternative_strength, ("incomplete_preference_evidence",), self.policy_version)
        if top.score < self.minimum_top_strength:
            return AdequacyResult(False, score, "The closest result has limited evidence for this request.", "weak_catalogue", roles, covered, (), True, evidence_completeness, top.score, alternative_strength, ("top_result_below_minimum_strength",), self.policy_version)
        return AdequacyResult(True, score, "The top result has sufficient direct catalogue evidence for this request.", "adequate_catalogue", roles, covered, (), True, evidence_completeness, top.score, alternative_strength, ("complete_catalogue_coverage",), self.policy_version)
