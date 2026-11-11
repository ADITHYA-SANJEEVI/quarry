from decimal import Decimal
import os

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.hpc import SearchExecutionRequest
from app.services.hpc.catalogues import CatalogueRegistry
from app.services.hpc.demo_search import make_service, downstream, result_digest
from app.services.hpc.runtime import ProcessRuntime, effective_cpu_count
from app.services.intent_parser import FallbackIntentParser


@pytest.mark.parametrize("query", ["i need a gud speakr under 5000", "speaker under 5000", "speakers under 1500"])
def test_speaker_typo_retrieval_preserves_budget_and_eligibility(query):
    intent = FallbackIntentParser().parse(query)
    assert "speaker" in intent.resolved_roles
    catalogue = CatalogueRegistry().get("studio-1000-v1")
    service, _ = make_service(catalogue)
    result = service.search(None, intent)
    assert result.recommendations
    for row in result.recommendations:
        p = row.product
        assert p.catalogue_active and p.stock > 0
        assert p.price <= intent.strict_max_price
        assert any(f.family == "role" and f.concept == "speaker" for f in p.catalogue_facts)


@pytest.mark.parametrize("query", ["speakr under 10", "speaker without speaker under 5000"])
def test_speaker_repair_cannot_relax_impossible_constraints(query):
    catalogue = CatalogueRegistry().get("studio-1000-v1")
    service, _ = make_service(catalogue)
    assert not service.search(None, FallbackIntentParser().parse(query)).recommendations


@pytest.mark.parametrize("workers", [1, 2, 4, 8])
def test_full_search_digest_and_warm_worker_reuse(workers):
    if workers > effective_cpu_count():
        pytest.skip("insufficient allocated CPUs")
    catalogue = CatalogueRegistry().get("studio-1000-v1")
    queries = ["i need a gud speakr under 5000", "usb c charger under 1000",
               "gentle cleanser and fragrance-free moisturiser under 1500", "speaker under 10"]
    with ProcessRuntime(max_workers=workers, timeout_seconds=60) as runtime:
        previous = None
        for query in queries:
            intent = FallbackIntentParser().parse(query)
            reference, rr = make_service(catalogue)
            a = reference.search(None, intent)
            service, pr = make_service(catalogue, SearchExecutionRequest(mode="parallel", workers=workers), runtime)
            b = service.search(None, intent)
            assert result_digest(a, rr, downstream(a, intent)) == result_digest(b, pr, downstream(b, intent))
            meta = service.compute.metadata
            if meta.actual_mode == "parallel":
                assert len(meta.worker_pids) == workers
                assert os.getpid() not in meta.worker_pids
                if previous:
                    assert meta.worker_pids == previous
                    assert meta.startup_ms == 0
                previous = meta.worker_pids
        assert previous


def test_release_http_search_and_workbench():
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/health").json()["application"] == "quarry"
        response = client.post("/api/v1/hpc/catalogues/studio-1000-v1/search",
                               json={"query": "i need a gud speakr under 5000"})
        assert response.status_code == 200
        body = response.json()
        assert body["recommendations"]
        assert body["commerce"]["checkout_supported"] is False
        assert all(Decimal(r["product"]["price"]) <= 5000 for r in body["recommendations"])
        assert client.post("/api/v1/hpc/experiments", json={}).status_code in (403, 503)
        assert client.post("/api/payments/verify", json={}).status_code == 404
