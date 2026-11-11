from collections import Counter
from hashlib import sha256
from time import perf_counter

from app.services.hpc.contracts import QueryCorpusContext
from app.services.hpc.snapshot import canonical_json, capture_behavior, capture_intent
from app.services.relevance import DeterministicRelevanceAdapter, tokenize
from app.services.hpc.tracing import stage


def build_context(snapshot, products, intent, behavior=None, *, profile=None) -> QueryCorpusContext:
    started = perf_counter()
    by_id = {p.id: p for p in snapshot.products}
    records = [by_id[p.id] for p in products]
    ordinals = tuple(p.ordinal for p in records)
    if len(set(ordinals)) != len(ordinals):
        raise ValueError("duplicate_query_candidates")
    documents = [p.document.document() for p in records]
    with stage("query_expansion", candidate_in=len(documents)):
        query = DeterministicRelevanceAdapter()._build_query(intent, documents)
    tokens = [tokenize(d.searchable_text) for d in documents]
    frequency = Counter(t for values in tokens for t in set(values))
    vocabulary = tuple(sorted({t for values in tokens for t in values if len(t) >= 3}))
    statistics_ms = (perf_counter()-started)*1000
    started = perf_counter()
    accepted, behavioral = capture_intent(intent), capture_behavior(behavior)
    identifier = sha256(canonical_json([snapshot.generation, ordinals, accepted.canonical_json, behavioral.canonical_json]).encode()).hexdigest()
    if profile is not None:
        profile.update(global_statistics_ms=statistics_ms, query_hash_ms=(perf_counter()-started)*1000)
    return QueryCorpusContext(snapshot.generation, identifier, ordinals, query.raw_query,
        query.terms, query.repaired_terms, vocabulary, len(records),
        sum(map(len, tokens)) / len(tokens) if tokens else 0.0,
        tuple(sorted(frequency.items())), accepted, behavioral)
