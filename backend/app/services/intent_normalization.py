"""Bounded, deterministic customer-language normalization for IntentState v3.

This module deliberately matches only a reviewed commerce vocabulary. It never
examines catalogue descriptions, chooses products, or creates catalogue facts.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from rapidfuzz import fuzz, process

from app.schemas.intent import IntentFactFamily, IntentRequirementProvenance


@dataclass(frozen=True)
class NormalizedTerm:
    concept: str
    family: IntentFactFamily
    provenance: IntentRequirementProvenance
    span: tuple[int, int]


@dataclass(frozen=True)
class NormalizationResult:
    text: str
    terms: tuple[NormalizedTerm, ...]
    repaired_terms: tuple[str, ...]


@dataclass(frozen=True)
class _VocabularyEntry:
    concept: str
    family: IntentFactFamily
    aliases: tuple[str, ...]
    fuzzy: bool = True


# This is a small vocabulary declared separately from ProductFacts: aliases
# translate customer language; ProductFacts remain the sole product truth.
_VOCABULARY: tuple[_VocabularyEntry, ...] = (
    _VocabularyEntry("cleanser", IntentFactFamily.ROLE, ("cleanser", "face wash", "facewash", "face-wash", "facial wash", "facial cleanser", "cleanse")),
    _VocabularyEntry("moisturiser", IntentFactFamily.ROLE, ("moisturiser", "moisturizer", "moistusr", "moisturizr", "face moisturiser", "face moisturizer", "moisturising", "moisturizing")),
    _VocabularyEntry("sunscreen", IntentFactFamily.ROLE, ("sunscreen", "sun screen", "sun protect", "sunblock", "spf", "sun protection", "protection from sun")),
    _VocabularyEntry("serum", IntentFactFamily.ROLE, ("serum",)),
    _VocabularyEntry("toner", IntentFactFamily.ROLE, ("toner",)),
    _VocabularyEntry("body-care", IntentFactFamily.ROLE, ("body care", "body wash", "body lotion", "hand cream")),
    _VocabularyEntry("lip-care", IntentFactFamily.ROLE, ("lip balm", "lip oil", "lip mask", "lip care")),
    _VocabularyEntry("gift-set", IntentFactFamily.ROLE, ("gift set", "gift box")),
    _VocabularyEntry("charger", IntentFactFamily.ROLE, ("charger", "chrgr", "charging brick", "charging plug", "power adapter", "juice up")),
    _VocabularyEntry("hub", IntentFactFamily.ROLE, ("usb c hub", "usb-c hub", "hdmi adapter", "display adapter", "video adapter")),
    _VocabularyEntry("cable", IntentFactFamily.ROLE, ("cable", "cord", "wire")),
    _VocabularyEntry("power-bank", IntentFactFamily.ROLE, ("power bank", "powerbank", "battery pack")),
    _VocabularyEntry("speaker", IntentFactFamily.ROLE, ("speaker", "speakers", "portable speaker", "desktop speaker")),
    _VocabularyEntry("headphones", IntentFactFamily.ROLE, ("headphones", "headphone", "earbuds", "earphones")),
    _VocabularyEntry("phone", IntentFactFamily.ROLE, ("phone", "mobile")),
    _VocabularyEntry("gentle", IntentFactFamily.ATTRIBUTE, ("gentle", "soft", "mild", "halka")),
    _VocabularyEntry("fragrance-free", IntentFactFamily.ATTRIBUTE, ("fragrance free", "fragrance-free", "fragrancefree", "unscented")),
    _VocabularyEntry("gel", IntentFactFamily.ATTRIBUTE, ("gel",)),
    _VocabularyEntry("lightweight", IntentFactFamily.ATTRIBUTE, ("lightweight", "light weight")),
    _VocabularyEntry("premium", IntentFactFamily.ATTRIBUTE, ("premium", "luxury")),
    _VocabularyEntry("fast-charging", IntentFactFamily.ATTRIBUTE, ("fast charging", "fast-charging")),
    _VocabularyEntry("noise-cancelling", IntentFactFamily.ATTRIBUTE, ("noise cancelling", "noise-cancelling")),
    _VocabularyEntry("fragranced", IntentFactFamily.ATTRIBUTE, ("fragranced", "fragrance")),
    _VocabularyEntry("oily-skin", IntentFactFamily.SUITABILITY, ("oily skin", "oily-skin")),
    _VocabularyEntry("dry-skin", IntentFactFamily.SUITABILITY, ("dry skin", "dry-skin", "dry face", "face feels dry", "skin feels dry")),
    _VocabularyEntry("sensitive-skin", IntentFactFamily.SUITABILITY, ("sensitive skin", "sensitive-skin")),
    _VocabularyEntry("commute", IntentFactFamily.SUITABILITY, ("metro", "commute")),
    _VocabularyEntry("daily-use", IntentFactFamily.SUITABILITY, ("daily use", "daily", "every morning", "morning")),
    _VocabularyEntry("macbook", IntentFactFamily.COMPATIBILITY, ("macbook",)),
    _VocabularyEntry("laptop", IntentFactFamily.COMPATIBILITY, ("compatible with laptop", "laptop compatible")),
    _VocabularyEntry("usb-a", IntentFactFamily.COMPATIBILITY, ("usb-a", "usb a")),
    _VocabularyEntry("usb-c", IntentFactFamily.COMPATIBILITY, ("usb-c", "usb c", "type-c", "type c", "c type")),
)

_AMBIGUITIES = {"lotion": ("moisturiser", "body-care")}
_FUZZY_THRESHOLD = 82
_FUZZY_MARGIN = 7
_COMPOSITION_CUE = re.compile(r"\b(?:package|bundle|kit|setup|routine|set|pair|both)\b")
_EXACT_ONLY_FUZZY_ALIASES = frozenset({"moistusr", "moisturizr", "chrgr"})


def has_composition_cue(text: str) -> bool:
    """Recognise bounded shopper packaging language without choosing products."""
    return bool(_COMPOSITION_CUE.search(normalize_surface(text)))


def commerce_vocabulary_phrases() -> tuple[str, ...]:
    """Reviewed language aliases available to bounded language tooling.

    This is customer-language vocabulary only; it does not inspect or derive
    product facts.
    """
    return tuple(
        dict.fromkeys(alias for entry in _VOCABULARY for alias in (*entry.aliases, entry.concept))
    )


def normalize_surface(text: str) -> str:
    """Idempotent formatting normalization before controlled vocabulary lookup."""
    value = text.lower().replace("â‚¹", " rs ")
    value = re.sub(r"[^a-z0-9.+,\-]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


class ControlledLexicalNormalizer:
    """Exact aliases first; high-confidence fuzzy repair only when unambiguous."""

    def normalize(self, text: str) -> NormalizationResult:
        normalized = normalize_surface(text)
        terms = self._exact_terms(normalized)
        covered = [term.span for term in terms]
        fuzzy_terms, repaired = self._fuzzy_terms(normalized, covered)
        combined = sorted(
            {*terms, *fuzzy_terms}, key=lambda term: (term.span[0], term.span[1], term.concept)
        )
        return NormalizationResult(normalized, tuple(combined), tuple(repaired))

    @staticmethod
    def ambiguities(
        text: str, terms: tuple[NormalizedTerm, ...] = ()
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        normalized = normalize_surface(text)
        results: list[tuple[str, tuple[str, ...]]] = []
        for term, candidates in _AMBIGUITIES.items():
            matches = list(re.finditer(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", normalized))
            unresolved = any(
                not any(
                    item.concept in candidates
                    and item.span[0] <= match.start()
                    and item.span[1] >= match.end()
                    for item in terms
                )
                for match in matches
            )
            if unresolved:
                results.append((term, candidates))
        return tuple(results)

    def _exact_terms(self, text: str) -> list[NormalizedTerm]:
        matches: list[NormalizedTerm] = []
        for entry in _VOCABULARY:
            for alias in entry.aliases:
                phrase = normalize_surface(alias)
                for match in re.finditer(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text):
                    provenance = (
                        IntentRequirementProvenance.EXPLICIT
                        if phrase == normalize_surface(entry.concept)
                        else IntentRequirementProvenance.NORMALIZED_ALIAS
                    )
                    matches.append(NormalizedTerm(entry.concept, entry.family, provenance, match.span()))
        return self._prefer_longest(matches)

    def _fuzzy_terms(
        self, text: str, covered: list[tuple[int, int]]
    ) -> tuple[list[NormalizedTerm], list[str]]:
        aliases: dict[str, _VocabularyEntry] = {
            _compact(alias): entry
            for entry in _VOCABULARY if entry.fuzzy
            for alias in entry.aliases
            if len(_compact(alias)) >= 5 and _compact(alias) not in _EXACT_ONLY_FUZZY_ALIASES
        }
        choices = tuple(aliases)
        terms: list[NormalizedTerm] = []
        repaired: list[str] = []
        tokens = list(re.finditer(r"[a-z][a-z0-9-]*", text))
        for start, token in enumerate(tokens):
            for width in range(1, min(3, len(tokens) - start) + 1):
                end = tokens[start + width - 1]
                span = (token.start(), end.end())
                if any(_overlaps(span, existing) for existing in covered):
                    continue
                fragment = text[span[0] : span[1]]
                compact = _compact(fragment)
                if len(compact) < 5 or fragment in _AMBIGUITIES:
                    continue
                best = process.extract(compact, choices, scorer=fuzz.ratio, limit=6, score_cutoff=_FUZZY_THRESHOLD)
                if not best:
                    continue
                candidate, score, _ = best[0]
                # Fuzzy repair is for misspelled controlled language, not for
                # arbitrary English words that happen to be close to an alias.
                # Reject truncations/antonyms (charge/charger, scented/
                # unscented) and require stronger evidence for one-word input.
                if _is_truncation_or_extension(compact, candidate):
                    continue
                if " " not in fragment and "-" not in fragment and score < 86:
                    continue
                competing = next(
                    (item for item in best[1:] if aliases[item[0]].concept != aliases[candidate].concept),
                    None,
                )
                if competing is not None and score - competing[1] < _FUZZY_MARGIN:
                    continue
                entry = aliases[candidate]
                terms.append(
                    NormalizedTerm(
                        entry.concept,
                        entry.family,
                        IntentRequirementProvenance.FUZZY_NORMALIZED,
                        span,
                    )
                )
                repaired.append(fragment)
        return self._prefer_longest(terms), repaired

    @staticmethod
    def _prefer_longest(terms: list[NormalizedTerm]) -> list[NormalizedTerm]:
        accepted: list[NormalizedTerm] = []
        for term in sorted(terms, key=lambda item: (-(item.span[1] - item.span[0]), item.span[0], item.concept)):
            if not any(
                _overlaps(term.span, existing.span)
                and term.family == existing.family
                and (term.span != existing.span or term.concept == existing.concept)
                for existing in accepted
            ):
                accepted.append(term)
        return accepted


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _is_truncation_or_extension(value: str, candidate: str) -> bool:
    return value != candidate and (candidate.startswith(value) or value.startswith(candidate) or candidate.endswith(value) or value.endswith(candidate))
