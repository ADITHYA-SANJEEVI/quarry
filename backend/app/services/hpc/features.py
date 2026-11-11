"""Pure scoring over immutable records; exact reference arithmetic/order.

Reuse the reference's pure scalar primitives instead of maintaining a second
BM25/fuzzy/trigram algorithm. No model/provider/database or pool operations.
"""
from collections import Counter

from app.services.hpc.contracts import ComputeError, FeatureRow, VERSION
from app.services.relevance import DeterministicRelevanceAdapter, normalize_text, tokenize


def score_partition(snapshot, context, ordinals) -> tuple[FeatureRow, ...]:
    if snapshot.version != VERSION or context.version != VERSION or context.generation != snapshot.generation:
        raise ComputeError("generation_mismatch")
    if (context.n != len(context.ordinals) or len(set(ordinals)) != len(ordinals)
            or not set(ordinals).issubset(context.ordinals)
            or any(i < 0 or i >= len(snapshot.products) for i in ordinals)):
        raise ComputeError("invalid_partition")
    frequency = Counter(dict(context.document_frequency))
    rows = []
    for ordinal in ordinals:
        product = snapshot.products[ordinal]
        document = product.document.document()
        tokens = tokenize(document.searchable_text)
        lexical = DeterministicRelevanceAdapter._bm25(context.terms, tokens, frequency, context.n, context.average_length)
        title = DeterministicRelevanceAdapter._bm25(context.terms, tokenize(document.title), frequency, context.n, context.average_length)
        lexical += title * 1.8
        normalized_document = normalize_text(document.searchable_text)
        normalized_query = normalize_text(context.raw_query)
        phrase = 1.0 if normalized_query and normalized_query in normalized_document else 0.0
        matched = tuple(sorted(set(context.terms).intersection(tokens)))
        fuzzy = DeterministicRelevanceAdapter._fuzzy(context.terms, document)
        character = DeterministicRelevanceAdapter._character_similarity(context.terms, tokens)
        rows.append(FeatureRow(ordinal, product.id, lexical, fuzzy, character, phrase, matched))
    return tuple(rows)
