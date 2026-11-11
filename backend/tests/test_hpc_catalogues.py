from dataclasses import FrozenInstanceError
from hashlib import sha256
import json

import pytest
from pydantic import ValidationError

from app.schemas.hpc import SearchExecutionRequest
from app.schemas.orders import CreateOrderRequest
from app.services.constraints import ConstraintEngine
from app.services.hpc.catalogues import CatalogueRegistry, DATA_ROOT, SIZES, generate_catalogue, generate_records
from app.services.hpc.demo_search import make_service, downstream, result_digest
from app.services.hpc.runtime import ProcessRuntime
from app.services.intent_parser import FallbackIntentParser


@pytest.mark.parametrize("size", SIZES)
def test_committed_dataset_integrity_and_determinism(size, tmp_path):
    manifest, _ = generate_catalogue(tmp_path, size)
    committed = json.loads((DATA_ROOT / f"studio-{size}-v1.manifest.json").read_text())
    assert manifest == committed
    assert sha256((tmp_path / manifest["file"]).read_bytes()).hexdigest() == manifest["sha256"]
    provider = CatalogueRegistry(tmp_path).get(manifest["dataset_id"])
    assert len(provider.products) == len(provider.by_id) == size
    assert len({p.name for p in provider.products}) == size
    assert len(manifest["categories"]) == 12
    assert len({p.subcategory for p in provider.products}) == 60
    assert manifest["available"] == sum(p.catalogue_active and p.stock > 0 for p in provider.products)
    assert manifest["archived"] and manifest["inactive"] and manifest["out_of_stock"]
    with pytest.raises(FrozenInstanceError):
        provider.products[1].stock = 42
    # Full 50K data can be read back without touching any database or provider.
    assert provider.wire(provider.products[-1])["id"] == f"demo:studio-{size}-v1:{size}"
    assert not provider.wire(provider.products[-1])["purchase_eligible"]


def test_tampering_and_unknown_dataset_rejected(tmp_path):
    manifest, _ = generate_catalogue(tmp_path, 1000)
    path = tmp_path / manifest["file"]
    path.write_bytes(path.read_bytes().replace(b'"stock":', b'"stock":9', 1))
    with pytest.raises(ValueError, match="mismatch"):
        CatalogueRegistry(tmp_path).get(manifest["dataset_id"])
    with pytest.raises(KeyError):
        CatalogueRegistry(tmp_path).get("../live")


def test_synthetic_wire_id_is_rejected_by_live_checkout_contract():
    with pytest.raises(ValidationError):
        CreateOrderRequest(product_id="demo:studio-50000-v1:40")
    with pytest.raises(ValidationError):
        CreateOrderRequest(product_id=40, dataset_id="studio-50000-v1")


@pytest.mark.parametrize("size", SIZES)
def test_scaled_original_and_process_search_match_complete_contract(size):
    catalogue = CatalogueRegistry().get(f"studio-{size}-v1")
    intent = FallbackIntentParser().parse("usb c charger under 1000")
    reference, ranker = make_service(catalogue)
    result = reference.search(None, intent)
    expected = result_digest(result, ranker, downstream(result, intent))
    assert result.discovery_count == size
    assert all(p.catalogue_active and p.stock > 0 for p in result.eligible_products)
    with ProcessRuntime(max_workers=2, timeout_seconds=120) as runtime:
        service, observed = make_service(catalogue, SearchExecutionRequest(mode="parallel", workers=2), runtime)
        actual = service.search(None, intent)
        assert result_digest(actual, observed, downstream(actual, intent)) == expected
        assert len(service.compute.metadata.worker_pids) == 2
        assert service.compute.snapshot is catalogue.snapshot
        # Cache is restricted to immutable provider + accepted intent/behavior.
        first_context = service.compute.context
        assert catalogue.context(ConstraintEngine().pre_retrieval(catalogue.products, intent).eligible, intent, None) is first_context
        assert len(observed.calls) == 2 and observed.calls[-1]["limit"] is None


@pytest.mark.parametrize("query", ["wireless hedphones under 3000", "usb c charger and cable under 2500",
                                   "gentle cleanser and moisturiser under 1500", "programming practice book"])
def test_diverse_workloads_preserve_preview_and_evidence(query):
    catalogue = CatalogueRegistry().get("studio-1000-v1")
    intent = FallbackIntentParser().parse(query)
    reference, ranker = make_service(catalogue)
    result = reference.search(None, intent)
    expected = result_digest(result, ranker, downstream(result, intent))
    actual_service, observed = make_service(catalogue, SearchExecutionRequest(mode="serial"))
    actual = actual_service.search(None, intent)
    assert result_digest(actual, observed, downstream(actual, intent)) == expected
