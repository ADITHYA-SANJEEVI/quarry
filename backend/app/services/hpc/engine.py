"""Complete global rank/RRF fusion; never shard-local candidate pruning."""
from math import isfinite

from app.services.hpc.contracts import ComputeError
from app.services.relevance import RelevanceCandidate


def validate_rows(snapshot, context, rows):
    if len(rows) != len(context.ordinals) or {r.ordinal for r in rows} != set(context.ordinals):
        raise ComputeError("missing_or_duplicate_rows")
    for row in rows:
        if row.product_id != snapshot.products[row.ordinal].id:
            raise ComputeError("wrong_product_id")
        if not all(isfinite(v) for v in (row.lexical, row.fuzzy, row.character, row.phrase)):
            raise ComputeError("non_finite_feature")


def fuse_features(snapshot, context, rows, products):
    validate_rows(snapshot, context, rows)
    by_ordinal = {r.ordinal: r for r in rows}
    ordered = [by_ordinal[i] for i in context.ordinals]
    ranks = [{r.product_id: i for i, r in enumerate(sorted(ordered,
        key=lambda r: (-getattr(r, component), r.product_id)), 1)}
        for component in ("lexical", "fuzzy", "character")]
    by_id = {p.id: p for p in products}
    result = []
    for row in ordered:
        rrf = sum(1 / (60 + rank[row.product_id]) for rank in ranks)
        fused = min(1.0, rrf * 22 + row.phrase * 0.25)
        result.append(RelevanceCandidate(by_id[row.product_id],
            snapshot.products[row.ordinal].document.document(), row.lexical,
            row.fuzzy, row.character, row.phrase, fused, row.matched_terms,
            context.repaired_terms))
    return sorted(result, key=lambda r: (-r.fused_score, r.product.price, r.product.name))
