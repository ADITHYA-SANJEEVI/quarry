"""Bounded, non-authoritative linguistic evidence for assisted intent parsing.

The service deliberately exposes serializable summaries instead of spaCy
objects. Its spelling and syntax observations can help an optional language
provider interpret a difficult utterance, but never change commerce state on
their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
import time
from typing import Any

from symspellpy import SymSpell, Verbosity

from app.schemas.intent import IntentFactFamily
from app.services.intent_normalization import (
    ControlledLexicalNormalizer,
    commerce_vocabulary_phrases,
    normalize_surface,
)


_MAX_TOKENS = 48
_MAX_RELATIONS = 24
_MAX_NORMALIZATIONS = 4
_MAX_TEXT = 500
_SUSPICIOUS_TOKEN = re.compile(r"^[a-z][a-z-]{4,}$")
_PROTECTED_TOKEN = re.compile(r"^(?:\d|rs\b|inr\b|usb[- ]?[ac]\b|[a-z]*\d)[a-z0-9.-]*$", re.I)
_RELATIONS = frozenset({"compound", "dobj", "nsubj", "prep", "pobj", "neg", "amod"})


@dataclass(frozen=True)
class LanguageToken:
    text: str
    lemma: str
    pos: str
    dep: str
    head: str
    span: tuple[int, int]


@dataclass(frozen=True)
class LanguageRelation:
    source: str
    relation: str
    target: str


@dataclass(frozen=True)
class NormalizationCandidate:
    original: str
    candidate: str
    provenance: str
    span: tuple[int, int]


@dataclass(frozen=True)
class LanguageConceptEvidence:
    """Canonical concept found inside one bounded, traceable candidate."""

    concept: str
    family: IntentFactFamily
    candidate_original: str
    candidate: str
    provenance: str


@dataclass(frozen=True)
class LanguageAnalysis:
    raw_text: str
    tokens: tuple[LanguageToken, ...]
    relations: tuple[LanguageRelation, ...]
    negated_targets: tuple[str, ...]
    normalizations: tuple[NormalizationCandidate, ...]
    concepts: tuple[LanguageConceptEvidence, ...]
    warnings: tuple[str, ...]
    latency_ms: int

    @property
    def available(self) -> bool:
        return "spacy_unavailable" not in self.warnings

    def prompt_evidence(self) -> dict[str, Any]:
        """Small allowlisted evidence supplied only to an opted-in provider."""
        return {
            "normalization_candidates": [
                {"original": item.original, "candidate": item.candidate, "provenance": item.provenance}
                for item in self.normalizations
            ],
            "recognized_concepts": [
                {"concept": item.concept, "family": item.family.value, "provenance": item.provenance}
                for item in self.concepts
            ],
            "syntax": [
                {"source": item.source, "relation": item.relation, "target": item.target}
                for item in self.relations
            ],
            "negated_targets": list(self.negated_targets),
            "warnings": list(self.warnings),
        }


class LanguageAnalysisService:
    """Lazy, process-local spaCy and SymSpell evidence extraction."""

    def analyze(self, raw_text: str) -> LanguageAnalysis:
        started = time.perf_counter()
        text = raw_text[:_MAX_TEXT]
        try:
            nlp = _load_spacy_model()
        except Exception:
            return LanguageAnalysis(
                raw_text=text,
                tokens=(),
                relations=(),
                negated_targets=(),
                normalizations=(),
                concepts=(),
                warnings=("spacy_unavailable",),
                latency_ms=_elapsed_ms(started),
            )

        doc = nlp(text)
        tokens = tuple(
            LanguageToken(
                text=token.text[:80], lemma=(token.lemma_ or token.text).lower()[:80],
                pos=token.pos_[:32], dep=token.dep_[:32],
                head=token.head.text[:80], span=(token.idx, token.idx + len(token.text)),
            )
            for token in doc[:_MAX_TOKENS]
        )
        relations = tuple(
            LanguageRelation(source=token.text[:80], relation=token.dep_[:32], target=token.head.text[:80])
            for token in doc[:_MAX_TOKENS]
            if token.dep_ in _RELATIONS and token.head != token
        )[:_MAX_RELATIONS]
        negated = _negated_targets(doc)
        normalizations = _normalization_candidates(doc)
        return LanguageAnalysis(
            raw_text=text,
            tokens=tokens,
            relations=relations,
            negated_targets=negated,
            normalizations=normalizations,
            concepts=_canonical_candidate_concepts(normalizations),
            warnings=(),
            latency_ms=_elapsed_ms(started),
        )


@lru_cache(maxsize=1)
def _load_spacy_model():
    # No download is attempted here: startup/search remains safe on minimal
    # deployments and installation is an explicit build/deploy step.
    import spacy

    return spacy.load("en_core_web_sm", disable=["ner", "textcat"])


@lru_cache(maxsize=1)
def _domain_symspell() -> SymSpell:
    spell = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
    for phrase in commerce_vocabulary_phrases():
        for word in normalize_surface(phrase).split():
            if word.isalpha():
                spell.create_dictionary_entry(word, 100)
    # A small set of glue words lets segmentation expose a useful candidate,
    # without creating an English correction authority.
    for word in ("need", "for", "my", "a", "an", "and", "not", "with", "under", "below"):
        spell.create_dictionary_entry(word, 10)
    return spell


def _normalization_candidates(doc) -> tuple[NormalizationCandidate, ...]:
    spell = _domain_symspell()
    candidates: list[NormalizationCandidate] = []
    for token in doc[:_MAX_TOKENS]:
        value = token.text.lower()
        if not _SUSPICIOUS_TOKEN.fullmatch(value) or _PROTECTED_TOKEN.fullmatch(value):
            continue
        suggestions = spell.lookup(value, Verbosity.TOP, max_edit_distance=2, include_unknown=False)
        if suggestions and suggestions[0].term != value:
            candidates.append(NormalizationCandidate(value, suggestions[0].term, "symspell_token", (token.idx, token.idx + len(token.text))))
        elif len(value) >= 12:
            segmented = spell.word_segmentation(value, max_edit_distance=2).corrected_string
            if segmented and segmented != value:
                candidates.append(NormalizationCandidate(value, segmented[:160], "symspell_segmentation", (token.idx, token.idx + len(token.text))))
        if len(candidates) >= _MAX_NORMALIZATIONS:
            break
    return tuple(candidates)


def _canonical_candidate_concepts(
    candidates: tuple[NormalizationCandidate, ...]
) -> tuple[LanguageConceptEvidence, ...]:
    normalizer = ControlledLexicalNormalizer()
    concepts: list[LanguageConceptEvidence] = []
    for candidate in candidates:
        # Parse only the bounded candidate, then retain only finite ontology
        # concepts. The original uncorrected token remains attached as
        # provenance; unsupported tails such as "forum" cannot contribute.
        for term in normalizer.normalize(candidate.candidate).terms:
            if term.family not in {
                IntentFactFamily.ROLE,
                IntentFactFamily.ATTRIBUTE,
                IntentFactFamily.SUITABILITY,
            }:
                continue
            evidence = LanguageConceptEvidence(
                concept=term.concept,
                family=term.family,
                candidate_original=candidate.original,
                candidate=candidate.candidate,
                provenance=candidate.provenance,
            )
            if evidence not in concepts:
                concepts.append(evidence)
    return tuple(concepts)


def _negated_targets(doc) -> tuple[str, ...]:
    """Expose nearby syntactic targets, never a semantic constraint verdict."""
    targets: list[str] = []
    for negator in doc[:_MAX_TOKENS]:
        if negator.dep_ != "neg" or negator.head == negator:
            continue
        head = negator.head
        # ``not for phone`` usually attaches ``not`` to a verb/adjective;
        # preserve the governed prepositional object rather than claiming the
        # verb itself is a commerce exclusion target.
        objects = [
            grandchild
            for child in head.children
            for grandchild in child.children
            if child.dep_ == "prep" and grandchild.dep_ in {"pobj", "pcomp"}
        ]
        direct = [child for child in head.children if child.dep_ in {"dobj", "attr", "oprd"}]
        values = objects or direct or [head]
        targets.extend(value.text.lower()[:80] for value in values)
    return tuple(dict.fromkeys(targets))[:_MAX_RELATIONS]


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
