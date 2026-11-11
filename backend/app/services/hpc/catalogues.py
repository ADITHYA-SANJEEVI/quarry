"""Versioned synthetic catalogues: immutable compute and storefront data, no DB.

All specifications describe fictional demo designs. No brands, ratings, medical
efficacy, certifications or merchant claims are inferred by the generator.
"""
from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import random
from sys import intern
from threading import RLock
from time import perf_counter

from app.domain.catalogue_facts import CanonicalProductFact, validate_fact_set
from app.models.product import Product
from app.services.hpc.contracts import FactSnapshot
from app.services.hpc.snapshot import canonical_json, capture_catalogue
from app.services.hpc.storage_io import atomic_replace
from app.services.retrieval import RetrievalResult

GENERATOR_VERSION = "studio_catalogue_v1"
SEED = 270026
SIZES = (1000, 10000, 50000)
DATA_ROOT = Path(__file__).resolve().parents[3] / "data" / "hpc"

# category, type, base INR, canonical role, two coherent specification axes.
# Ten variants per axis plus finish and series make distinct, useful designs.
TEMPLATES = (
    ("Electronics", "USB-C charger", 699, "charger", "power_w", (20,30,45,65,90,100), "ports", (1,2,3)),
    ("Electronics", "power bank", 1299, "power-bank", "capacity_mah", (5000,10000,15000,20000), "power_w", (18,20,30,45)),
    ("Electronics", "USB-C cable", 299, "cable", "length_m", (0.5,1,1.5,2,3), "power_w", (30,60,100)),
    ("Electronics", "phone case", 399, "case", "material", ("silicone","TPU","polycarbonate"), "phone_size_inches", (6.1,6.3,6.5,6.7)),
    ("Electronics", "smartphone", 13999, "phone", "storage_gb", (64,128,256,512), "memory_gb", (4,6,8,12)),
    ("Computing", "laptop", 35999, "laptop", "memory_gb", (8,16,24,32), "storage_gb", (256,512,1024)),
    ("Computing", "mechanical keyboard", 2499, "keyboard", "layout_keys", (61,68,75,87,104), "switch", ("linear","tactile","clicky")),
    ("Computing", "monitor", 8999, "monitor", "diagonal_inches", (22,24,27,32), "refresh_hz", (60,75,100,144)),
    ("Computing", "USB-C hub", 1999, "hub", "ports", (4,5,6,7,8), "cable_length_cm", (12,15,20,30)),
    ("Computing", "laptop sleeve", 799, "case", "diagonal_inches", (13,14,15,16), "material", ("felt","neoprene","canvas")),
    ("Audio", "wireless headphones", 1999, "headphones", "driver_mm", (30,35,40,45), "design", ("on-ear","over-ear")),
    ("Audio", "wired headphones", 899, "headphones", "cable_length_m", (1.2,1.5,2,3), "connector", ("3.5mm","USB-C")),
    ("Audio", "portable speaker", 1199, "speaker", "power_w", (5,10,15,20,30), "enclosure", ("cylinder","cube","bar")),
    ("Audio", "desktop speaker", 1999, "speaker", "power_w", (10,20,30,40), "channels", ("2.0","2.1")),
    ("Audio", "in-ear earphones", 599, "headphones", "driver_mm", (8,10,12), "connector", ("3.5mm","USB-C")),
    ("Home appliances", "electric kettle", 899, None, "capacity_l", (0.8,1,1.2,1.5,1.8), "power_w", (1000,1200,1500)),
    ("Home appliances", "table fan", 1499, None, "blade_cm", (20,25,30,35), "speeds", (2,3,4)),
    ("Home appliances", "dry iron", 699, None, "power_w", (750,1000,1200), "soleplate", ("steel","ceramic","aluminium")),
    ("Home appliances", "mixer grinder", 2299, None, "power_w", (500,650,750,900), "jars", (2,3,4)),
    ("Home appliances", "desk vacuum", 1299, None, "power_w", (30,45,60,90), "bin_ml", (200,300,400,500)),
    ("Fashion", "cotton t-shirt", 399, None, "size", ("XS","S","M","L","XL","XXL"), "fabric_gsm", (140,160,180,200)),
    ("Fashion", "linen shirt", 999, None, "size", ("S","M","L","XL","XXL"), "fit", ("regular","relaxed","slim")),
    ("Fashion", "canvas tote", 349, None, "capacity_l", (8,12,16,20), "strap_cm", (45,50,55,60)),
    ("Fashion", "cotton scarf", 299, None, "length_cm", (150,160,170,180), "weave", ("plain","twill","herringbone")),
    ("Fashion", "everyday backpack", 799, None, "capacity_l", (12,16,20,24,28), "pockets", (2,3,4,5)),
    ("Personal care", "body wash", 249, "body-care", "volume_ml", (100,200,250,300,500), "scent", ("fragrance-free","citrus","floral")),
    ("Personal care", "hand cream", 199, "hand-care", "volume_ml", (30,50,75,100), "scent", ("fragrance-free","citrus","floral")),
    ("Personal care", "body lotion", 299, "body-care", "volume_ml", (100,200,250,300), "scent", ("fragrance-free","citrus","floral")),
    ("Personal care", "lip balm", 149, "lip-care", "weight_g", (4,5,6,8), "finish", ("clear","rose","coral")),
    ("Personal care", "hand wash", 149, "hand-care", "volume_ml", (200,250,300,500), "scent", ("fragrance-free","citrus","floral")),
    ("Beauty", "gentle cleanser", 399, "cleanser", "volume_ml", (50,100,150,200,250), "texture", ("gel","cream","foam")),
    ("Beauty", "daily moisturiser", 499, "moisturiser", "volume_ml", (30,50,75,100), "texture", ("gel","cream","lotion")),
    ("Beauty", "face toner", 349, "toner", "volume_ml", (50,100,150,200), "scent", ("fragrance-free","floral")),
    ("Beauty", "face serum", 649, "serum", "volume_ml", (15,20,30,50), "texture", ("gel","emulsion")),
    ("Beauty", "tinted lip balm", 249, "lip-care", "weight_g", (4,5,6), "shade", ("rose","berry","peach","coral")),
    ("Furniture", "writing desk", 4999, None, "width_cm", (80,90,100,110,120), "material", ("pine","bamboo","engineered wood")),
    ("Furniture", "bookshelf", 2999, None, "shelves", (2,3,4,5), "width_cm", (40,50,60,70,80)),
    ("Furniture", "side table", 1499, None, "diameter_cm", (30,35,40,45,50), "material", ("pine","bamboo","steel")),
    ("Furniture", "storage stool", 1299, None, "capacity_l", (15,20,25,30), "cover", ("cotton","canvas","polyester")),
    ("Furniture", "desk chair", 3499, None, "seat_width_cm", (40,42,45,48), "cover", ("mesh","cotton","polyester")),
    ("Kitchen", "saucepan", 799, None, "capacity_l", (1,1.5,2,2.5,3), "material", ("stainless steel","aluminium")),
    ("Kitchen", "water bottle", 299, None, "capacity_ml", (350,500,650,750,1000), "material", ("steel","glass","polypropylene")),
    ("Kitchen", "storage jar", 199, None, "capacity_ml", (250,500,750,1000,1500), "material", ("glass","steel","polypropylene")),
    ("Kitchen", "chopping board", 349, None, "length_cm", (25,30,35,40), "material", ("bamboo","acacia","polypropylene")),
    ("Kitchen", "serving bowl", 249, None, "diameter_cm", (12,15,18,20,24), "material", ("ceramic","steel","glass")),
    ("Sports", "yoga mat", 599, None, "thickness_mm", (3,4,5,6,8), "length_cm", (170,175,180,185)),
    ("Sports", "resistance band", 299, None, "resistance_kg", (3,5,8,10,15), "length_cm", (100,120,150,180)),
    ("Sports", "exercise dumbbell", 699, None, "weight_kg", (1,2,3,4,5,8), "coating", ("rubber","vinyl","neoprene")),
    ("Sports", "skipping rope", 199, None, "length_cm", (240,260,280,300), "handle", ("foam","wood","polypropylene")),
    ("Sports", "training towel", 249, None, "length_cm", (60,80,100,120), "fabric", ("cotton","microfibre","bamboo fibre")),
    ("Books", "mathematics workbook", 199, None, "level", ("foundation","intermediate","advanced"), "pages", (96,128,160,192,224)),
    ("Books", "programming practice book", 399, None, "language", ("Python","Java","C++","JavaScript"), "pages", (160,192,224,256,320)),
    ("Books", "garden field journal", 249, None, "binding", ("paperback","spiral","hardback"), "pages", (96,128,160,192)),
    ("Books", "travel sketchbook", 299, None, "paper_gsm", (100,120,140,160), "pages", (64,80,96,128)),
    ("Books", "cooking notebook", 249, None, "binding", ("paperback","spiral","hardback"), "pages", (96,128,160,192)),
    ("Home improvement", "screwdriver set", 299, None, "pieces", (4,6,8,12), "handle", ("rubber","wood","polypropylene")),
    ("Home improvement", "LED desk lamp", 599, None, "power_w", (4,6,8,10,12), "colour_temperature_k", (3000,4000,5000,6500)),
    ("Home improvement", "wall shelf", 699, None, "width_cm", (30,40,50,60,80), "material", ("pine","bamboo","steel")),
    ("Home improvement", "measuring tape", 149, None, "length_m", (2,3,5,7.5,10), "case", ("rubber","steel","polypropylene")),
    ("Home improvement", "storage organiser", 349, None, "compartments", (4,6,8,10,12), "material", ("bamboo","steel","polypropylene")),
)


@dataclass(frozen=True, slots=True)
class DemoProduct(Product):
    # An unmapped, frozen value view retaining the reference adapter's Product
    # type contract. It is never added to a Session or sent to a worker.
    __abstract__ = True
    id: int = field()
    name: str = field()
    description: str = field()
    category: str = field()
    subcategory: str = field()
    price: Decimal = field()
    stock: int = field()
    tags: tuple[str, ...] = field()
    is_active: bool = field()
    archived_at: datetime | None = field()
    catalogue_facts: tuple[FactSnapshot, ...] = field()
    specifications: tuple[tuple[str, str], ...] = field()
    currency: str = "INR"

    @property
    def catalogue_active(self):
        return self.is_active and self.archived_at is None


def generate_records(size, seed=SEED):
    if size not in SIZES:
        raise ValueError("unsupported_catalogue_size")
    rng = random.Random(seed)
    colours = ("slate", "ivory", "navy", "sage", "terracotta", "charcoal")
    for i in range(size):
        cat, kind, base, role, axis1, options1, axis2, options2 = TEMPLATES[i % len(TEMPLATES)]
        variant = i // len(TEMPLATES)
        a = options1[variant % len(options1)]
        b = options2[(variant // len(options1)) % len(options2)]
        colour = colours[(variant // (len(options1) * len(options2))) % len(colours)]
        series = 100 + variant // (len(options1) * len(options2) * len(colours))
        consumable = cat in ("Beauty", "Personal care")
        colour_key = "packaging_colour" if consumable else "colour"
        specs = {axis1: str(a), axis2: str(b), colour_key: colour, "design_series": str(series)}
        finish = f"{colour.title()} Pack" if consumable else colour.title()
        title = f"{finish} {kind.title()} {a} {axis1.replace('_', ' ')} {b} {axis2.replace('_', ' ')} S{series}"
        facts = []
        def fact(family, concept):
            if (family, concept) not in {(f["family"], f["concept"]) for f in facts}:
                facts.append(CanonicalProductFact(family=family, concept=concept).model_dump(mode="json"))
        if role:
            fact("role", role)
        if cat in ("Books", "Computing", "Furniture"):
            fact("suitability", "study-work")
        else:
            fact("suitability", "daily-use")
        if "wireless" in kind:
            fact("attribute", "wireless")
        if "USB-C" in kind or (axis2 == "connector" and b == "USB-C"):
            fact("compatibility", "usb-c")
            fact("attribute", "usb-c")
        if role in ("charger", "power-bank", "cable"):
            fact("use_case", "device-charging")
            fact("compatibility", "phone")
            if (axis1 == "power_w" and a >= 65) or (axis2 == "power_w" and b >= 60):
                fact("compatibility", "laptop")
        if "gentle" in kind:
            fact("attribute", "gentle")
        if axis2 == "texture" and b == "gel":
            fact("attribute", "gel")
        if axis2 == "scent":
            fact("attribute", "fragrance-free" if b == "fragrance-free" else "fragranced")
        if "tinted" in kind:
            fact("attribute", "tinted")
        if "mechanical" in kind:
            fact("attribute", "mechanical")
        tags = [kind, cat.lower(), colour, *[f["concept"] for f in facts], str(a), str(b)]
        price = base + (variant % len(options1)) * max(10, base // 15) + rng.randrange(0, 10) * 10
        # Orthogonal deterministic failures include inconsistent archived+active rows.
        active, archived = i % 47 != 0, "2026-01-01T00:00:00+00:00" if i % 53 == 0 else None
        yield {"id": i + 1, "name": title,
               "description": f"Fictional Studio demo design. {kind.title()} with {colour} {'packaging' if consumable else 'finish'}; " + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in specs.items()) + ". Synthetic specifications for catalogue search demonstrations.",
               "category": cat, "subcategory": kind, "price": f"{price}.00", "currency": "INR",
               "stock": 0 if i % 29 == 0 else rng.randrange(1, 150), "is_active": active,
               "archived_at": archived, "tags": list(dict.fromkeys(tags)), "specifications": specs, "facts": facts}


def validate_record(record):
    if not isinstance(record["id"], int) or record["id"] < 1 or not record["name"] or not record["description"]:
        raise ValueError("invalid_product_identity")
    if record["category"] not in {t[0] for t in TEMPLATES} or not record["subcategory"]:
        raise ValueError("invalid_taxonomy")
    if record["currency"] != "INR" or not Decimal(record["price"]).is_finite() or Decimal(record["price"]) <= 0:
        raise ValueError("invalid_price")
    if type(record["stock"]) is not int or record["stock"] < 0 or type(record["is_active"]) is not bool:
        raise ValueError("invalid_eligibility")
    if record["archived_at"]:
        datetime.fromisoformat(record["archived_at"])
    if not record["tags"] or not record["specifications"]:
        raise ValueError("missing_search_attributes")
    validate_fact_set(CanonicalProductFact(**f) for f in record["facts"])


def generate_catalogue(root, size, seed=SEED):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    dataset = f"studio-{size}-v1"
    path = root / f"{dataset}.jsonl"
    temporary = path.with_suffix(".jsonl.tmp")
    digest, ids, names, categories = sha256(), set(), set(), Counter()
    available = archived = inactive = out_of_stock = 0
    started = perf_counter()
    with temporary.open("wb") as stream:
        for record in generate_records(size, seed):
            validate_record(record)
            if record["id"] in ids or record["name"] in names:
                raise ValueError("duplicate_product")
            ids.add(record["id"]); names.add(record["name"])
            categories[record["category"]] += 1
            archived += record["archived_at"] is not None
            inactive += not record["is_active"]
            out_of_stock += record["stock"] == 0
            available += record["is_active"] and not record["archived_at"] and record["stock"] > 0
            line = (canonical_json(record) + "\n").encode("utf-8")
            stream.write(line); digest.update(line)
    atomic_replace(temporary,path)
    manifest = {"schema_version": "hpc_dataset_v1", "dataset_id": dataset, "generator_version": GENERATOR_VERSION,
                "seed": seed, "count": size, "sha256": digest.hexdigest(), "file": path.name,
                "categories": dict(sorted(categories.items())), "available": available, "archived": archived,
                "inactive": inactive, "out_of_stock": out_of_stock, "currency": "INR", "synthetic": True,
                "checkout_supported": False, "canonical_fact_schema": "v1"}
    manifest_path = root / f"{dataset}.manifest.json"
    manifest_temp = manifest_path.with_suffix(".json.tmp")
    manifest_temp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    atomic_replace(manifest_temp,manifest_path)
    return manifest, {"generation_ms": (perf_counter() - started) * 1000, "bytes": path.stat().st_size}


class SnapshotCatalogue:
    def __init__(self, manifest, products, load_ms=0):
        self.manifest, self.products = manifest, tuple(products)
        self.by_id = {p.id: p for p in self.products}
        self.load_ms = load_ms
        self._snapshot = None
        self._lock = RLock()
        self._contexts = OrderedDict()
        self.snapshot_ms = 0

    @property
    def snapshot(self):
        with self._lock:
            if self._snapshot is None:
                started = perf_counter()
                self._snapshot = capture_catalogue(self.products)
                self.snapshot_ms = (perf_counter() - started) * 1000
            return self._snapshot

    def context(self, products, intent, behavior, *, profile=None):
        from app.services.hpc.context import build_context
        from app.services.hpc.snapshot import capture_intent, capture_behavior
        # Only valid for this immutable provider: hard eligibility is a pure
        # function of the accepted intent. No live catalogue uses this cache.
        key = (capture_intent(intent), capture_behavior(behavior))
        with self._lock:
            hit = key in self._contexts
            if key not in self._contexts:
                self._contexts[key] = build_context(self.snapshot, products, intent, behavior, profile=profile)
                if len(self._contexts) > 24:
                    self._contexts.popitem(last=False)
            self._contexts.move_to_end(key)
            if profile is not None:
                profile["context_cache_hit"] = hit
                if hit:
                    profile.update(global_statistics_ms=0, query_hash_ms=0)
            return self._contexts[key]

    def retrieve(self, session, intent):
        return RetrievalResult(self.products, self.manifest["available"])

    def wire(self, product):
        return {"id": f"demo:{self.manifest['dataset_id']}:{product.id}", "dataset_id": self.manifest["dataset_id"],
                "dataset_checksum": self.manifest["sha256"], "name": product.name, "description": product.description,
                "category": product.category, "subcategory": product.subcategory, "price": str(product.price),
                "currency": product.currency, "stock": product.stock, "active": product.catalogue_active,
                "archived_at": product.archived_at.isoformat() if product.archived_at else None,
                "available": product.catalogue_active and product.stock > 0, "tags": product.tags,
                "specifications": dict(product.specifications), "facts": [asdict(f) for f in product.catalogue_facts],
                "purchase_eligible": False, "checkout_supported": False, "purchase_restriction": "synthetic_demo_catalogue"}


class CatalogueRegistry:
    def __init__(self, root=DATA_ROOT):
        self.root = Path(root)
        self._lock = RLock()
        self._loaded = None  # One retained generation; callers retain leases as needed.

    def manifests(self):
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.root.glob("studio-*.manifest.json"))]

    def get(self, dataset_id):
        if dataset_id not in {f"studio-{n}-v1" for n in SIZES}:
            raise KeyError("unknown_dataset")
        with self._lock:
            if self._loaded and self._loaded.manifest["dataset_id"] == dataset_id:
                return self._loaded
            started = perf_counter()
            manifest = json.loads((self.root / f"{dataset_id}.manifest.json").read_text(encoding="utf-8"))
            path = self.root / f"{dataset_id}.jsonl"
            digest, products, ids, names = sha256(), [], set(), set()
            fact_values = {}
            with path.open("rb") as stream:
                for line in stream:
                    digest.update(line)
                    r = json.loads(line)
                    validate_record(r)
                    if r["id"] in ids or r["name"] in names:
                        raise ValueError("duplicate_product")
                    ids.add(r["id"]); names.add(r["name"])
                    facts = []
                    for value in r["facts"]:
                        key = tuple(intern(value[k]) for k in ("family", "concept", "assertion", "provenance", "schema_version"))
                        if key not in fact_values:
                            fact_values[key] = FactSnapshot(*key)
                        facts.append(fact_values[key])
                    products.append(DemoProduct(r["id"], r["name"], r["description"], intern(r["category"]), intern(r["subcategory"]),
                        Decimal(r["price"]), r["stock"], tuple(intern(t) for t in r["tags"]), r["is_active"],
                        datetime.fromisoformat(r["archived_at"]) if r["archived_at"] else None,
                        tuple(facts), tuple((intern(k), intern(v)) for k,v in r["specifications"].items())))
            if digest.hexdigest() != manifest["sha256"] or len(products) != manifest["count"]:
                raise ValueError("catalogue_checksum_or_count_mismatch")
            if sum(p.catalogue_active and p.stock > 0 for p in products) != manifest["available"]:
                raise ValueError("catalogue_eligibility_mismatch")
            self._loaded = SnapshotCatalogue(manifest, products, (perf_counter() - started) * 1000)
            return self._loaded


registry = CatalogueRegistry()
