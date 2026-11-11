from dataclasses import asdict, FrozenInstanceError
from decimal import Decimal
import pickle

import pytest

from app.services.hpc.snapshot import capture_catalogue
from app.services.hpc.context import build_context
from app.services.hpc.features import score_partition
from app.services.hpc.engine import fuse_features
from app.services.constraints import ConstraintEngine
from app.services.intent_parser import FallbackIntentParser
from app.services.relevance import DeterministicRelevanceAdapter
from tests.fixtures.evaluation_catalogue import hydrated_evaluation_catalogue
from tests.fixtures.search_cases import CASES


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_pure_serial_features_match_reference_exactly(case):
    products = hydrated_evaluation_catalogue()
    intent = FallbackIntentParser().parse(case[1])
    snapshot = capture_catalogue(products)
    products = ConstraintEngine().pre_retrieval(products, intent).eligible
    context = build_context(snapshot, products, intent)
    result = fuse_features(snapshot, context, score_partition(snapshot, context, context.ordinals), products)
    expected = DeterministicRelevanceAdapter().retrieve(products, intent)
    assert result == expected  # Same original product objects, all feature fields.
    for actual, reference in zip(result, expected, strict=True):
        for field in ["lexical_score", "fuzzy_score", "character_score", "phrase_score", "fused_score"]:
            assert getattr(actual, field).hex() == getattr(reference, field).hex()


def test_snapshot_is_immutable_independent_and_preserves_prices_facts_order():
    products = hydrated_evaluation_catalogue()[::-1]
    products[0].price = Decimal("199.00")
    snapshot = capture_catalogue(products)
    assert pickle.loads(pickle.dumps(snapshot)) == snapshot
    assert snapshot.products[0].price == "199.00"
    assert [p.id for p in snapshot.products] == [p.id for p in products]
    with pytest.raises(FrozenInstanceError):
        snapshot.products[0].stock = 0
    products[0].tags.append("mutation")
    assert "mutation" not in snapshot.products[0].tags
    assert capture_catalogue(products).generation != snapshot.generation
