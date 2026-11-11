"""Production-equivalent in-memory catalogue for retrieval evaluations."""

import json
from pathlib import Path

from app.db.electronics_fact_data import ELECTRONICS_FACTS_BY_NAME
from app.db.seed_data import DEMO_PRODUCT_FACTS_BY_NAME, DEMO_PRODUCTS
from app.models.product import Product
from app.models.product_fact import ProductFact


def hydrated_evaluation_catalogue() -> list[Product]:
    electronics = json.loads(
        (Path(__file__).parent / "electronics_catalogue.json").read_text()
    )
    facts_by_name = {**DEMO_PRODUCT_FACTS_BY_NAME, **ELECTRONICS_FACTS_BY_NAME}
    products: list[Product] = []
    for identifier, raw in enumerate([*DEMO_PRODUCTS, *electronics], start=1):
        product = Product(id=identifier, **raw)
        product.catalogue_facts = [
            ProductFact(
                family=fact["family"], concept=fact["concept"], assertion="supported",
                provenance="catalogue_fixture", schema_version="v1",
            )
            for fact in facts_by_name.get(product.name, [])
        ]
        products.append(product)
    return products
