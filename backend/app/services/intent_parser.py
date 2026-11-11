import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.core.config import get_settings
from app.schemas.intent import (
    BudgetOperator,
    BudgetScope,
    ConfidenceBand,
    Enforcement,
    IntentAmbiguity,
    IntentBudget,
    IntentFactFamily,
    IntentRequirement,
    IntentRequirementProvenance,
    IntentRequirementStrength,
    IntentSignal,
    IntentSignalKind,
    IntentSignalPolarity,
    IntentSignalSource,
    RoleClause,
    RoleCompositionOperator,
    ShoppingIntent,
)
from app.services.intent_normalization import ControlledLexicalNormalizer, NormalizedTerm, has_composition_cue, normalize_surface
from app.services.language_analysis import LanguageAnalysis, LanguageAnalysisService
from app.services.intent_interpreter import (
    IntentInterpretationGate,
    IntentReconciler,
    LLMIntentInterpreter,
    OpenAICompatibleIntentProvider,
)

logger = logging.getLogger(__name__)


class IntentParser(Protocol):
    def parse(self, query: str) -> ShoppingIntent:
        ...


class IntentProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedIntent:
    intent: ShoppingIntent
    source: str
    interpretation: "IntentInterpretationDiagnostics | None" = None


@dataclass(frozen=True)
class IntentInterpretationDiagnostics:
    used: bool
    gate_reason: str
    latency_ms: int | None = None
    success: bool | None = None
    provider_kind: str | None = None
    model_identifier: str | None = None
    prompt_version: str | None = None
    proposal_schema_version: str | None = None
    schema_valid: bool | None = None
    fallback_category: str | None = None
    accepted_signals: int = 0
    rejected_signals: int = 0
    clarification_proposed: bool = False
    provider_outcome: str = "deterministic_sufficient"
    proposed_signal_count: int = 0
    model_improved_intent: bool = False
    final_interpretation_source: str = "deterministic"
    language_analysis_latency_ms: int | None = None
    language_analysis_available: bool | None = None


class FallbackIntentParser:
    """Deterministic IntentState v3 parser with v1/v2 compatibility views.

    The parser has a deliberately narrow job: normalize controlled customer
    vocabulary and preserve what is unresolved. It does not inspect products,
    make recommendations, or infer catalogue facts.
    """

    _stop_words = frozenset({
        "a", "an", "and", "as", "at", "but", "can", "for", "give", "i", "it", "me",
        "my", "need", "of", "on", "or", "please", "show", "that", "the", "to", "under", "want",
        "with", "you",
    })
    _money = r"(?:₹|rs\.?|inr)?\s*(?P<number>\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)\s*(?P<k>k)?\s*(?:rs\.?)?"

    def __init__(self, normalizer: ControlledLexicalNormalizer | None = None) -> None:
        self._normalizer = normalizer or ControlledLexicalNormalizer()

    def parse(self, query: str, *, language_analysis: LanguageAnalysis | None = None) -> ShoppingIntent:
        normalized = self._normalizer.normalize(query)
        terms = list(normalized.terms)
        terms.extend(self._contextual_role_terms(normalized.text, terms))
        requirements = self._requirements(normalized.text, terms, language_analysis)
        requirements.extend(
            self._relationship_compatibility_requirements(language_analysis, requirements)
        )
        requirements = _dedupe_requirements(requirements)
        roles = [item for item in requirements if item.family == IntentFactFamily.ROLE and item.polarity == IntentSignalPolarity.POSITIVE]
        role_clauses = self._role_clauses(normalized.text, roles)
        ambiguities = [
            IntentAmbiguity(term=term, candidates=list(candidates), affected_family=IntentFactFamily.ROLE)
            for term, candidates in self._normalizer.ambiguities(normalized.text, normalized.terms)
        ]
        budgets, budget_ambiguity = self._budgets(normalized.text)
        strict_min, strict_max, target, stretch_ceiling = self._compatibility_prices(budgets)
        recipient = self._recipient(normalized.text)
        occasion = "birthday" if re.search(r"\b(?:birthday|bday)\b", normalized.text) else None
        # A recipient is useful ranking context but is not itself proof that
        # the shopper wants a gift set. Gift intent needs an explicit gift /
        # present signal or a stated gifting occasion.
        gifting_intent = bool(re.search(r"\b(?:gift|present)\b", normalized.text) or occasion)
        quantity = self._quantity(normalized.text)
        signals = self._signals(requirements)
        compatibility_requirements = [
            signal for signal in signals
            if signal.kind == IntentSignalKind.COMPATIBILITY
        ]
        category = self._category(role_clauses, normalized.text)
        contradiction = strict_min is not None and strict_max is not None and strict_min > strict_max
        has_resolved_commerce_intent = bool(role_clauses or requirements or budgets)
        ambiguity_only = bool(ambiguities) and not role_clauses
        clarification_needed = contradiction or bool(budget_ambiguity) or ambiguity_only or not has_resolved_commerce_intent
        clarification_question = (
            "What price range should I use?" if contradiction
            else "Could you clarify the budget amount or range?" if budget_ambiguity
            else "By lotion, do you mean face moisturiser or body lotion?" if ambiguity_only
            else "Could you share the product type or budget?" if not has_resolved_commerce_intent
            else None
        )
        if contradiction:
            # Preserve the typed conflict for a later clarification flow, but
            # never send contradictory hard bounds to eligibility consumers.
            strict_min = strict_max = None
        product_concepts = [
            IntentSignal(
                value=category,
                source=IntentSignalSource.NORMALIZED_EXPLICIT,
                confidence_band=ConfidenceBand.EXPLICIT_SEMANTIC_HIGH,
                polarity=IntentSignalPolarity.POSITIVE,
                kind=IntentSignalKind.PREFERENCE,
                enforcement=Enforcement.SOFT,
                field="product_concept",
            )
        ] if category else []
        price_constraints = self._price_signal_views(budgets)
        return ShoppingIntent(
            raw_query=query,
            category=category,
            strict_min_price=strict_min,
            strict_max_price=strict_max,
            target_price=target,
            recipient=recipient,
            occasion=occasion,
            gifting_intent=gifting_intent,
            signals=signals,
            keywords=self._keywords(normalized.text, requirements),
            confidence=min(0.95, 0.2 + 0.1 * (len(requirements) + len(budgets))),
            clarification_needed=clarification_needed,
            clarification_question=clarification_question,
            product_concepts=product_concepts,
            desired_attributes=[signal for signal in signals if signal.polarity == IntentSignalPolarity.POSITIVE],
            excluded_attributes=[signal for signal in signals if signal.polarity == IntentSignalPolarity.NEGATIVE],
            hard_exclusions=[
                signal for signal in signals
                if signal.polarity == IntentSignalPolarity.NEGATIVE and signal.enforcement == Enforcement.HARD
            ],
            price_constraints=price_constraints,
            stretch_ceiling=stretch_ceiling,
            quantity=quantity,
            compatibility_requirements=compatibility_requirements,
            clarification_state=Enforcement.CLARIFY if clarification_needed else Enforcement.IGNORE,
            role_clauses=role_clauses,
            requirements=requirements,
            budgets=budgets,
            budget_ambiguity=budget_ambiguity,
            ambiguities=ambiguities,
        )

    def _requirements(
        self, text: str, terms, language_analysis: LanguageAnalysis | None = None
    ) -> list[IntentRequirement]:
        requirements: list[IntentRequirement] = []
        recognised_role_tokens = {
            text[term.span[0]:term.span[1]].lower()
            for term in terms
            if term.family == IntentFactFamily.ROLE
        }
        for term in terms:
            if term.family == IntentFactFamily.ROLE and self._role_is_syntactic_target(
                term, language_analysis, recognised_role_tokens
            ):
                continue
            polarity = IntentSignalPolarity.NEGATIVE if self._is_negated(text, term) else IntentSignalPolarity.POSITIVE
            strength = (
                IntentRequirementStrength.REQUIRED
                if term.family in {IntentFactFamily.ROLE, IntentFactFamily.COMPATIBILITY} or polarity == IntentSignalPolarity.NEGATIVE
                else IntentRequirementStrength.PREFERRED
            )
            requirements.append(IntentRequirement(
                concept=term.concept,
                family=term.family,
                polarity=polarity,
                strength=strength,
                provenance=term.provenance,
                evidence_span=term.span,
            ))
        # Existing direct exclusion behavior remains available for merchant
        # terms that are not yet part of the compact canonical vocabulary.
        for match in re.finditer(r"\b(?:not|no|nothing|without|avoid)\s+(?:any\s+)?(makeup|white)\b", text):
            value = match.group(1).replace(" ", "-")
            requirements.append(IntentRequirement(
                concept=value,
                family=IntentFactFamily.ATTRIBUTE,
                polarity=IntentSignalPolarity.NEGATIVE,
                strength=IntentRequirementStrength.REQUIRED,
                provenance=IntentRequirementProvenance.EXPLICIT,
                evidence_span=match.span(1),
            ))
        for match in re.finditer(r"\b(?:doesn(?:'|\s)?t|does not)\s+need\s+([a-z][a-z -]{0,30})", text):
            requirements.append(IntentRequirement(
                concept=match.group(1).strip(" -"),
                family=IntentFactFamily.ATTRIBUTE,
                polarity=IntentSignalPolarity.NEGATIVE,
                strength=IntentRequirementStrength.PREFERRED,
                provenance=IntentRequirementProvenance.EXPLICIT,
                evidence_span=match.span(1),
            ))
        return _dedupe_requirements(requirements)

    @staticmethod
    def _contextual_role_terms(text: str, terms: list[NormalizedTerm]) -> list[NormalizedTerm]:
        """Promote reviewed functional outcomes to canonical roles with context.

        These mappings are controlled language rules, not catalogue lookup or
        product selection. Each outcome needs a corroborating context token so
        generic words such as ``charge`` cannot gain commerce authority alone.
        """
        concepts = {(term.concept, term.family) for term in terms}
        inferred: list[NormalizedTerm] = []

        def add(concept: str, span: tuple[int, int]) -> None:
            if (concept, IntentFactFamily.ROLE) not in concepts:
                inferred.append(NormalizedTerm(
                    concept, IntentFactFamily.ROLE,
                    IntentRequirementProvenance.INFERRED_CONTEXT, span,
                ))
                concepts.add((concept, IntentFactFamily.ROLE))

        has_sunscreen = ("sunscreen", IntentFactFamily.ROLE) in concepts
        dry_term = next((term for term in terms if term.concept == "dry-skin"), None)
        hydration = re.search(r"\b(?:hydration|hydrate|hydrating)\b", text)
        if (has_sunscreen and dry_term is not None) or (
            hydration is not None and (has_sunscreen or dry_term is not None)
        ):
            add("moisturiser", hydration.span() if hydration is not None else dry_term.span)
        charge = re.search(r"\b(?:charge|charging)\b", text)
        if charge is not None and re.search(r"\b(?:laptop|macbook)\b", text):
            add("charger", charge.span())
        connection = re.search(r"\b(?:connect|adapter|display)\b", text)
        if connection is not None and re.search(r"\bhdmi\b", text):
            add("hub", connection.span())
        return inferred

    @staticmethod
    def _role_is_syntactic_target(term, analysis: LanguageAnalysis | None, role_tokens: set[str]) -> bool:
        """Avoid promoting a relation target to a requested product role.

        This consumes spaCy's syntactic observation conservatively: it can
        remove an obviously scoped noun from role coverage, but it never adds
        a compatibility requirement, exclusion, or other hard authority.
        """
        if analysis is None or not analysis.available:
            return False
        token = next((item for item in analysis.tokens if item.span == term.span), None)
        if token is None:
            return False
        # ``of facewash`` names the requested item ("a bottle of facewash"),
        # whereas ``for laptop`` scopes how a requested product is used.
        if token.dep == "pobj" and token.head.lower() in {"for", "to"}:
            return True
        if token.dep == "appos":
            head = next((item for item in analysis.tokens if item.text == token.head), None)
            if head is not None and head.dep == "pobj" and head.head.lower() in {"for", "to"}:
                return True
        if token.text.lower() in analysis.negated_targets and token.head.lower() in role_tokens:
            return True
        return token.dep == "compound" and token.head.lower() in role_tokens

    def _relationship_compatibility_requirements(
        self,
        analysis: LanguageAnalysis | None,
        requirements: list[IntentRequirement],
    ) -> list[IntentRequirement]:
        """Promote only corroborated negated relationship targets to compatibility.

        A literal negation, a finite canonical target and a spaCy dependency
        path to an already requested role are all required. This is a narrow
        deterministic proof, not a model-derived constraint.
        """
        if analysis is None or not analysis.available:
            return []
        role_tokens = {
            token.text.lower()
            for token in analysis.tokens
            if any(
                item.family == IntentFactFamily.ROLE
                and item.polarity == IntentSignalPolarity.POSITIVE
                and item.evidence_span == token.span
                for item in requirements
            )
        }
        if not role_tokens:
            return []
        results: list[IntentRequirement] = []
        for token in analysis.tokens:
            if token.text.lower() not in analysis.negated_targets:
                continue
            if not self._is_related_to_requested_role(token, analysis, role_tokens):
                continue
            canonical = self._normalizer.normalize(token.text)
            for term in canonical.terms:
                if term.family not in {IntentFactFamily.ROLE, IntentFactFamily.COMPATIBILITY}:
                    continue
                results.append(IntentRequirement(
                    concept=term.concept,
                    family=IntentFactFamily.COMPATIBILITY,
                    polarity=IntentSignalPolarity.NEGATIVE,
                    strength=IntentRequirementStrength.REQUIRED,
                    provenance=IntentRequirementProvenance.EXPLICIT,
                    evidence_span=token.span,
                ))
        return results

    @staticmethod
    def _is_related_to_requested_role(token, analysis: LanguageAnalysis, role_tokens: set[str]) -> bool:
        """Bounded dependency-chain/sibling check; no semantic guessing."""
        by_text = {item.text.lower(): item for item in analysis.tokens}
        current = token
        for _ in range(4):
            if current.head.lower() in role_tokens:
                return True
            parent = by_text.get(current.head.lower())
            if parent is None or parent is current:
                break
            current = parent
        return any(
            item.text.lower() in role_tokens and item.head.lower() == token.head.lower()
            for item in analysis.tokens
        )

    @staticmethod
    def _is_negated(text: str, term) -> bool:
        """Bind a negator to the immediately following controlled term only."""
        prefix = text[max(0, term.span[0] - 32) : term.span[0]]
        return bool(re.search(
            r"\b(?:not|no|nothing|without|avoid)\s+(?:any\s+)?(?:a\s+|an\s+)?$",
            prefix,
        ))

    def _role_clauses(self, text: str, roles: list[IntentRequirement]) -> list[RoleClause]:
        ordered_roles = sorted(roles, key=lambda item: item.evidence_span or (0, 0))
        ordered = list(dict.fromkeys(item.concept for item in ordered_roles))
        if not ordered:
            return []
        operator = RoleCompositionOperator.OR if re.search(r"\bor\b", text) else RoleCompositionOperator.AND
        owned_roles = [(role, self._owner_after_role(text, role)) for role in ordered_roles]
        distinct_owners = {owner for _, owner in owned_roles if owner is not None}
        if operator == RoleCompositionOperator.AND and distinct_owners and (
            len(distinct_owners) > 1 or any(owner is None for _, owner in owned_roles)
        ):
            return [
                RoleClause(roles=[role.concept], operator=operator, owner=owner, provenance=role.provenance)
                for role, owner in owned_roles
            ]
        owner = None
        if len(ordered) == 1:
            owner = self._owner_after_role(text, roles[0])
        elif len(ordered) > 1:
            owners = [self._owner_after_role(text, role) for role in roles]
            owner = next((value for value in owners if value), None)
        alternatives = [[role.concept for role in roles]] if operator == RoleCompositionOperator.OR else []
        provenance = (
            IntentRequirementProvenance.FUZZY_NORMALIZED
            if any(role.provenance == IntentRequirementProvenance.FUZZY_NORMALIZED for role in roles)
            else IntentRequirementProvenance.NORMALIZED_ALIAS
            if any(role.provenance == IntentRequirementProvenance.NORMALIZED_ALIAS for role in roles)
            else IntentRequirementProvenance.INFERRED_CONTEXT
            if any(role.provenance == IntentRequirementProvenance.INFERRED_CONTEXT for role in roles)
            else IntentRequirementProvenance.EXPLICIT
        )
        return [RoleClause(
            roles=ordered,
            operator=operator,
            alternatives=alternatives,
            owner=owner,
            scope=BudgetScope.PROPOSITION_TOTAL if len(ordered) > 1 or has_composition_cue(text) else BudgetScope.UNKNOWN,
            provenance=provenance,
        )]

    @staticmethod
    def _owner_after_role(text: str, role: IntentRequirement) -> str | None:
        if role.evidence_span is None:
            return None
        tail = text[role.evidence_span[1] : role.evidence_span[1] + 24]
        match = re.match(r"\s+for\s+(her|him|me|myself|mum|mom|mother|mummy|amma|dad|father|papa)\b", tail)
        if not match:
            return None
        value = match.group(1)
        if value in {"me", "myself"}:
            return "self"
        if value in {"mum", "mom", "mother", "mummy", "amma"}:
            return "mother"
        if value in {"dad", "father", "papa"}:
            return "father"
        return "recipient"

    def _budgets(self, text: str) -> tuple[list[IntentBudget], str | None]:
        budgets: list[IntentBudget] = []
        range_pattern = re.compile(
            rf"(?:\bbetween\s+)?{self._money}\s*(?:-|to|\band\b)\s*"
            rf"(?:₹|rs\.?|inr)?\s*(?P<number2>\d{{1,3}}(?:,\d{{3}})+|\d+(?:\.\d+)?)\s*(?P<k2>k)?"
        )
        consumed: list[tuple[int, int]] = []
        malformed = self._malformed_budget(text)
        for match in range_pattern.finditer(text):
            lower, upper = self._amount(match.group("number"), match.group("k")), self._amount(match.group("number2"), match.group("k2"))
            if lower <= upper:
                scope = self._budget_scope(text, match.span())
                budgets.extend((
                    IntentBudget(operator=BudgetOperator.RANGE, amount_paise=lower, scope=scope),
                    IntentBudget(operator=BudgetOperator.RANGE, amount_paise=upper, scope=scope),
                ))
                consumed.append(match.span())
            else:
                malformed = "reversed_range"
        patterns = (
            (BudgetOperator.TARGET, rf"\b(?:around|about|roughly|approximately|approx|ideally|target(?:ing)?)\s*{self._money}"),
            (BudgetOperator.MINIMUM, rf"\b(?:above|over|at least|min(?:imum)?)\s*{self._money}"),
            (BudgetOperator.MAXIMUM, rf"\b(?:under|below|less than|at most|max(?:imum)?|up to|upto|budget(?:\s+of)?|limit)\s*{self._money}"),
            (BudgetOperator.STRETCH_CEILING, rf"\b(?:stretch(?:\s+(?:to|up to))?|can\s+stretch\s+to)\s*{self._money}"),
            (BudgetOperator.MAXIMUM, rf"\b{self._money}\s*(?:max|budget|limit)\b"),
            (BudgetOperator.MINIMUM, rf"\b{self._money}\+"),
            (BudgetOperator.MAXIMUM, rf"\b{self._money}\s+ke\s+andar\b"),
        )
        for operator, pattern in patterns:
            for match in re.finditer(pattern, text):
                if any(_spans_overlap(match.span(), span) for span in consumed):
                    continue
                budgets.append(IntentBudget(
                    operator=operator,
                    amount_paise=self._amount(match.group("number"), match.group("k")),
                    scope=self._budget_scope(text, match.span()),
                    approximate=operator == BudgetOperator.TARGET,
                ))
        # An invalid commercial amount must never leave a partially parsed hard
        # bound behind. Preserve only the structured clarification state.
        return ([] if malformed else _dedupe_budgets(budgets)), malformed

    @staticmethod
    def _amount(number: str, suffix: str | None) -> int:
        amount = Decimal(number.replace(",", "").replace(" ", "")) * (Decimal("1000") if suffix else Decimal("1"))
        return int(amount * 100)

    @staticmethod
    def _malformed_budget(text: str) -> str | None:
        if re.search(r"\b(?:under|below|less than|at most|maximum|minimum|above|over|at least)\s+(?:₹|rs\.?|inr)?\s*-", text):
            return "negative_amount"
        if re.search(r"\b(?:under|below|less than|at most|maximum|minimum|above|over|at least)\s+(?:₹|rs\.?|inr)?\s*\d{1,3}\s+\d{1,2}(?:\D|$)", text):
            return "malformed_amount"
        return None

    @staticmethod
    def _budget_scope(text: str, span: tuple[int, int]) -> BudgetScope:
        nearby = text[max(0, span[0] - 18) : span[1] + 24]
        if re.search(r"\b(?:each|per item|per product)\b", nearby):
            return BudgetScope.PER_ITEM
        if re.search(r"\b(?:total|combined|altogether)\b", nearby):
            return BudgetScope.PROPOSITION_TOTAL
        return BudgetScope.UNKNOWN

    @staticmethod
    def _compatibility_prices(budgets: list[IntentBudget]) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
        minimum = next((item.amount for item in budgets if item.operator == BudgetOperator.MINIMUM), None)
        maximum = next((item.amount for item in budgets if item.operator == BudgetOperator.MAXIMUM), None)
        ranges = [item.amount for item in budgets if item.operator == BudgetOperator.RANGE]
        if len(ranges) >= 2:
            minimum, maximum = ranges[0], ranges[1]
        target = next((item.amount for item in budgets if item.operator == BudgetOperator.TARGET), None)
        stretch = next((item.amount for item in budgets if item.operator == BudgetOperator.STRETCH_CEILING), None)
        # Historical consumers treat a declared stretch ceiling as their final
        # eligibility cap. The typed operator remains distinct from a normal
        # maximum; this is only its legacy scalar projection.
        if maximum is None and stretch is not None:
            maximum = stretch
        return minimum, maximum, target, stretch

    def _signals(self, requirements: list[IntentRequirement]) -> list[IntentSignal]:
        signals: list[IntentSignal] = []
        for item in requirements:
            source, band = self._signal_source(item.provenance)
            kind = (
                IntentSignalKind.COMPATIBILITY if item.family == IntentFactFamily.COMPATIBILITY
                else IntentSignalKind.EXCLUSION if item.polarity == IntentSignalPolarity.NEGATIVE
                else IntentSignalKind.CONTEXT if item.family == IntentFactFamily.SUITABILITY
                else IntentSignalKind.PREFERENCE
            )
            signals.append(IntentSignal(
                value=item.concept,
                source=source,
                confidence_band=band,
                polarity=item.polarity,
                kind=kind,
                enforcement=(
                    Enforcement.HARD
                    if (item.polarity == IntentSignalPolarity.NEGATIVE and item.strength == IntentRequirementStrength.REQUIRED)
                    or item.family == IntentFactFamily.COMPATIBILITY
                    else Enforcement.SOFT
                ),
                field=(
                    "compatibility_requirement"
                    if kind == IntentSignalKind.COMPATIBILITY
                    else "hard_exclusion"
                    if item.polarity == IntentSignalPolarity.NEGATIVE and item.strength == IntentRequirementStrength.REQUIRED
                    else "excluded_attribute"
                    if item.polarity == IntentSignalPolarity.NEGATIVE
                    else "desired_attribute"
                ),
                evidence_span=item.evidence_span,
            ))
        values = {signal.value for signal in signals if signal.polarity == IntentSignalPolarity.POSITIVE}
        if "cleanser" in values and "gentle" in values:
            signals.append(IntentSignal(
                value="gentle-daily-cleansing",
                source=IntentSignalSource.NORMALIZED_EXPLICIT,
                confidence_band=ConfidenceBand.EXPLICIT_SEMANTIC_HIGH,
                polarity=IntentSignalPolarity.POSITIVE,
                kind=IntentSignalKind.CONTEXT,
                enforcement=Enforcement.SOFT,
                field="use_case",
            ))
        return signals

    @staticmethod
    def _signal_source(provenance: IntentRequirementProvenance) -> tuple[IntentSignalSource, ConfidenceBand]:
        if provenance == IntentRequirementProvenance.EXPLICIT:
            return IntentSignalSource.EXPLICIT, ConfidenceBand.EXPLICIT_DETERMINISTIC
        if provenance in {IntentRequirementProvenance.NORMALIZED_ALIAS, IntentRequirementProvenance.FUZZY_NORMALIZED}:
            return IntentSignalSource.NORMALIZED_EXPLICIT, ConfidenceBand.EXPLICIT_SEMANTIC_HIGH
        return IntentSignalSource.EXPLICIT, ConfidenceBand.INFERRED_LOW

    @staticmethod
    def _category(clauses: list[RoleClause], text: str) -> str | None:
        roles = {role for clause in clauses for role in clause.roles}
        if "skincare" in text:
            return "skincare"
        if roles & {"cleanser", "moisturiser", "serum", "toner"}:
            return "skincare"
        if "body-care" in roles:
            return "body-care"
        if "lip-care" in roles:
            return "lip-care"
        if "gift-set" in roles:
            return "gift-sets"
        if "sunscreen" in roles:
            return "sun-care"
        return None

    @staticmethod
    def _recipient(text: str) -> str | None:
        groups = {
            "mother": ("amma", "mom", "mother", "mum", "mummy"),
            "father": ("dad", "father", "papa"),
            "student": ("student",),
        }
        for recipient, aliases in groups.items():
            if any(re.search(rf"\b{alias}\b", text) for alias in aliases):
                return recipient
        return "her" if re.search(r"\bfor her\b", text) else None

    @staticmethod
    def _quantity(text: str) -> int | None:
        digit = re.search(r"\b(\d+)\s*(?:pack|packs|pieces|pcs|units?|products?)\b", text)
        if digit:
            return int(digit.group(1))
        words = re.search(r"\b(two|three)\s+(?:products?|items?)\b", text)
        return {"two": 2, "three": 3}.get(words.group(1)) if words else None

    @staticmethod
    def _price_signal_views(budgets: list[IntentBudget]) -> list[IntentSignal]:
        result: list[IntentSignal] = []
        names = {BudgetOperator.MINIMUM: "minimum_price", BudgetOperator.MAXIMUM: "maximum_price", BudgetOperator.TARGET: "target_price", BudgetOperator.RANGE: "range_price", BudgetOperator.STRETCH_CEILING: "stretch_ceiling"}
        for budget in budgets:
            result.append(IntentSignal(
                value=str(budget.amount),
                source=IntentSignalSource.EXPLICIT,
                confidence_band=ConfidenceBand.EXPLICIT_DETERMINISTIC,
                polarity=IntentSignalPolarity.POSITIVE,
                kind=IntentSignalKind.FREEFORM,
                enforcement=Enforcement.SOFT if budget.approximate or budget.operator == BudgetOperator.STRETCH_CEILING else Enforcement.HARD,
                field=names[budget.operator],
            ))
        return result

    def _keywords(self, text: str, requirements: list[IntentRequirement]) -> list[str]:
        words = [word for word in re.findall(r"[a-z]{3,}", text) if word not in self._stop_words and word != "not"]
        canonical = [item.concept for item in requirements if item.polarity == IntentSignalPolarity.POSITIVE]
        return list(dict.fromkeys([*canonical, *words]))[:8]


def _spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _dedupe_requirements(requirements: list[IntentRequirement]) -> list[IntentRequirement]:
    unique: dict[tuple[str, IntentFactFamily, IntentSignalPolarity], IntentRequirement] = {}
    for item in requirements:
        key = (item.concept, item.family, item.polarity)
        current = unique.get(key)
        if current is None or (item.evidence_span or (10_000, 10_000)) < (current.evidence_span or (10_000, 10_000)):
            unique[key] = item
    return list(unique.values())


def _dedupe_budgets(budgets: list[IntentBudget]) -> list[IntentBudget]:
    unique: dict[tuple[BudgetOperator, int, BudgetScope], IntentBudget] = {}
    for item in budgets:
        unique[(item.operator, item.amount_paise, item.scope)] = item
    return list(unique.values())


class IntentParsingService:
    def __init__(
        self,
        fallback: IntentParser | None = None,
        provider: IntentParser | None = None,
        interpreter: LLMIntentInterpreter | None = None,
        gate: IntentInterpretationGate | None = None,
        reconciler: IntentReconciler | None = None,
        language_analysis: LanguageAnalysisService | None = None,
    ) -> None:
        self._fallback = fallback or FallbackIntentParser()
        # `provider` is retained only as a test/backward-compatibility seam for
        # the former parser adapter. Runtime configuration uses `interpreter`.
        self._provider = provider
        self._interpreter = interpreter
        self._gate = gate or IntentInterpretationGate()
        self._reconciler = reconciler or IntentReconciler()
        self._language_analysis = language_analysis or LanguageAnalysisService()

    def parse(self, query: str) -> ParsedIntent:
        analysis = self._language_analysis.analyze(query)
        deterministic = (
            self._fallback.parse(query, language_analysis=analysis)
            if isinstance(self._fallback, FallbackIntentParser)
            else self._fallback.parse(query)
        )
        deterministic = self._merge_language_concepts(deterministic, analysis)
        if self._interpreter is not None:
            decision = self._gate.decide(query, deterministic, enabled=True)
            if not decision.should_interpret:
                diagnostics = IntentInterpretationDiagnostics(
                    False, decision.reason.value, provider_outcome="deterministic_sufficient"
                )
                self._log_summary(deterministic, diagnostics)
                return ParsedIntent(intent=deterministic, source="fallback", interpretation=diagnostics)
            attempt = self._interpreter.interpret(query, deterministic, analysis)
            if attempt.success and attempt.proposal is not None:
                reconciled = self._reconciler.reconcile(deterministic, attempt.proposal)
                improved = (
                    reconciled.accepted_signals > 0
                    or reconciled.intent.clarification_needed != deterministic.clarification_needed
                )
                provider_outcome = (
                    "proposal_rejected"
                    if (attempt.proposal.signals or attempt.proposal.budgets) and not improved
                    else "provider_success"
                )
                diagnostics = IntentInterpretationDiagnostics(
                    True,
                    decision.reason.value,
                    latency_ms=attempt.latency_ms,
                    success=True,
                    provider_kind=attempt.provider_kind,
                    model_identifier=attempt.model_identifier,
                    prompt_version=attempt.prompt_version,
                    proposal_schema_version=attempt.schema_version,
                    schema_valid=True,
                    accepted_signals=reconciled.accepted_signals,
                    rejected_signals=reconciled.rejected_signals,
                    clarification_proposed=reconciled.clarification_proposed,
                    provider_outcome=provider_outcome,
                    proposed_signal_count=len(attempt.proposal.signals),
                    model_improved_intent=improved,
                    final_interpretation_source="hybrid" if improved else "deterministic",
                    language_analysis_latency_ms=analysis.latency_ms,
                    language_analysis_available=analysis.available,
                )
                self._log_summary(reconciled.intent, diagnostics)
                return ParsedIntent(intent=reconciled.intent, source="hybrid", interpretation=diagnostics)
            diagnostics = IntentInterpretationDiagnostics(
                True,
                decision.reason.value,
                latency_ms=attempt.latency_ms,
                success=False,
                provider_kind=attempt.provider_kind,
                model_identifier=attempt.model_identifier,
                prompt_version=attempt.prompt_version,
                proposal_schema_version=attempt.schema_version,
                schema_valid=False,
                fallback_category=attempt.failure_category,
                provider_outcome=attempt.failure_category or "provider_unavailable",
                final_interpretation_source=self._final_local_source(deterministic),
                language_analysis_latency_ms=analysis.latency_ms,
                language_analysis_available=analysis.available,
            )
            logger.warning(
                "event=intent_interpreter_fallback reason=%s category=%s",
                decision.reason.value,
                attempt.failure_category,
            )
            self._log_summary(deterministic, diagnostics)
            return ParsedIntent(intent=deterministic, source="fallback", interpretation=diagnostics)

        if self._provider is not None:
            try:
                intent = self._provider.parse(query)
                logger.info("event=intent_parser_success source=provider")
                self._log_summary(intent, None)
                return ParsedIntent(intent=intent, source="provider")
            except IntentProviderError:
                logger.warning("event=intent_parser_failure source=provider fallback=true")

        intent = deterministic
        diagnostics = IntentInterpretationDiagnostics(
            False,
            "not_configured",
            provider_outcome="deterministic_sufficient",
            language_analysis_latency_ms=analysis.latency_ms,
            language_analysis_available=analysis.available,
            final_interpretation_source=self._final_local_source(deterministic),
        )
        logger.info("event=intent_parser_success source=fallback")
        self._log_summary(intent, diagnostics)
        return ParsedIntent(intent=intent, source="fallback", interpretation=diagnostics)

    @staticmethod
    def _final_local_source(intent: ShoppingIntent) -> str:
        return (
            "language_analysis"
            if any(signal.source == IntentSignalSource.LANGUAGE_ANALYSIS for signal in intent.signals)
            else "deterministic"
        )

    @staticmethod
    def _merge_language_concepts(intent: ShoppingIntent, analysis: LanguageAnalysis) -> ShoppingIntent:
        """Retain only grounded segmentation evidence as soft local context.

        Token-level spelling suggestions are advisory input for the optional
        interpreter only: a generic word such as ``setup`` must not become a
        shopper-visible commerce requirement.  A segmented compact utterance
        is different: its recovered multi-token controlled vocabulary remains
        useful soft evidence when an optional provider times out.
        """
        signals = list(intent.signals)
        product_concepts = list(intent.product_concepts)
        desired_attributes = list(intent.desired_attributes)
        identities = {(item.field, item.value, item.polarity) for item in signals}
        for evidence in analysis.concepts:
            if evidence.provenance != "symspell_segmentation":
                continue
            field = "product_concept" if evidence.family == IntentFactFamily.ROLE else "desired_attribute"
            kind = IntentSignalKind.CONTEXT if evidence.family == IntentFactFamily.SUITABILITY else IntentSignalKind.PREFERENCE
            identity = (field, evidence.concept, IntentSignalPolarity.POSITIVE)
            if identity in identities:
                continue
            signal = IntentSignal(
                value=evidence.concept,
                source=IntentSignalSource.LANGUAGE_ANALYSIS,
                confidence_band=ConfidenceBand.INFERRED_LOW,
                polarity=IntentSignalPolarity.POSITIVE,
                kind=kind,
                enforcement=Enforcement.SOFT,
                field=field,
            )
            identities.add(identity)
            signals.append(signal)
            if field == "product_concept":
                product_concepts.append(signal)
            else:
                desired_attributes.append(signal)

        generic_empty_intent = (
            intent.clarification_needed
            and not intent.role_clauses
            and not intent.requirements
            and not intent.budgets
            and not intent.ambiguities
            and intent.budget_ambiguity is None
        )
        has_local_role = any(item.source == IntentSignalSource.LANGUAGE_ANALYSIS and item.field == "product_concept" for item in signals)
        clarification = False if generic_empty_intent and has_local_role else intent.clarification_needed
        return intent.model_copy(update={
            "signals": signals,
            "product_concepts": product_concepts,
            "desired_attributes": desired_attributes,
            "clarification_needed": clarification,
            "clarification_question": None if not clarification else intent.clarification_question,
            "clarification_state": Enforcement.CLARIFY if clarification else Enforcement.IGNORE,
        })

    @staticmethod
    def _log_summary(
        intent: ShoppingIntent, diagnostics: IntentInterpretationDiagnostics | None
    ) -> None:
        logger.info(
            "event=intent_interpreter_lifecycle category=%s signal_count=%s clarification=%s llm_requested=%s assistance_reason=%s provider_outcome=%s provider_kind=%s model_identifier=%s prompt_version=%s proposal_schema_version=%s latency_ms=%s language_analysis_latency_ms=%s language_analysis_available=%s success=%s schema_valid=%s proposed=%s accepted=%s rejected=%s model_improved_intent=%s final_interpretation_source=%s",
            intent.category,
            len(intent.signals),
            intent.clarification_needed,
            diagnostics.used if diagnostics else False,
            diagnostics.gate_reason if diagnostics else "not_configured",
            diagnostics.provider_outcome if diagnostics else "deterministic_sufficient",
            diagnostics.provider_kind if diagnostics else None,
            diagnostics.model_identifier if diagnostics else None,
            diagnostics.prompt_version if diagnostics else None,
            diagnostics.proposal_schema_version if diagnostics else None,
            diagnostics.latency_ms if diagnostics else None,
            diagnostics.language_analysis_latency_ms if diagnostics else None,
            diagnostics.language_analysis_available if diagnostics else None,
            diagnostics.success if diagnostics else None,
            diagnostics.schema_valid if diagnostics else None,
            diagnostics.proposed_signal_count if diagnostics else 0,
            diagnostics.accepted_signals if diagnostics else 0,
            diagnostics.rejected_signals if diagnostics else 0,
            diagnostics.model_improved_intent if diagnostics else False,
            diagnostics.final_interpretation_source if diagnostics else "deterministic",
        )


def get_intent_parsing_service() -> IntentParsingService:
    settings = get_settings()
    if (
        settings.ai_intent_enabled
        and settings.ai_base_url
        and settings.ai_api_key
        and settings.ai_model
    ):
        return IntentParsingService(
            interpreter=LLMIntentInterpreter(
                OpenAICompatibleIntentProvider(
                    base_url=settings.ai_base_url,
                    api_key=settings.ai_api_key,
                    model=settings.ai_model,
                    timeout_seconds=settings.ai_timeout_seconds,
                )
            )
        )
    return IntentParsingService()
