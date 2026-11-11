import json
from decimal import Decimal
from pathlib import Path


DEMO_PRODUCTS: list[dict[str, object]] = [
    {
        "name": "Dewdrop Barrier Cream",
        "description": "A rich daily moisturiser for dry, sensitive skin.",
        "category": "skincare",
        "price": Decimal("849.00"),
        "stock": 36,
        "tags": ["moisturiser", "sensitive-skin", "fragrance-free"],
    },
    {
        "name": "Cloudmilk Gentle Cleanser",
        "description": "A low-foam cleanser that leaves skin comfortable, not tight.",
        "category": "skincare",
        "price": Decimal("599.00"),
        "stock": 48,
        "tags": ["cleanser", "sensitive-skin", "fragrance-free"],
    },
    {
        "name": "Sunveil SPF 50 Fluid",
        "description": "Lightweight broad-spectrum sunscreen with an invisible finish.",
        "category": "sun-care",
        "price": Decimal("999.00"),
        "stock": 29,
        "tags": ["sunscreen", "spf-50", "no-white-cast"],
    },
    {
        "name": "Petal Tint Lip Balm",
        "description": "Sheer rose lip care with nourishing plant butters.",
        "category": "lip-care",
        "price": Decimal("349.00"),
        "stock": 72,
        "tags": ["lip-care", "tinted", "budget"],
    },
    {
        "name": "Quiet Night Repair Serum",
        "description": "A calming evening serum for stressed, dehydrated skin.",
        "category": "skincare",
        "price": Decimal("1299.00"),
        "stock": 18,
        "tags": ["serum", "barrier-support", "sensitive-skin"],
    },
    {
        "name": "Morning Glow Gift Box",
        "description": "A cleanser, moisturiser, and lip balm in a ready-to-gift box.",
        "category": "gift-sets",
        "price": Decimal("1599.00"),
        "stock": 20,
        "tags": ["gift-set", "daily-routine", "under-2000"],
    },
    {
        "name": "Oat Silk Body Lotion",
        "description": "A fast-absorbing body lotion made for easily irritated skin.",
        "category": "body-care",
        "price": Decimal("749.00"),
        "stock": 41,
        "tags": ["body-care", "sensitive-skin", "fragrance-free"],
    },
    {
        "name": "Berry Cocoa Lip Mask",
        "description": "An overnight lip treatment with a soft berry-cocoa scent.",
        "category": "lip-care",
        "price": Decimal("429.00"),
        "stock": 55,
        "tags": ["lip-care", "overnight", "giftable"],
    },
    {
        "name": "Mineral Mist SPF 40",
        "description": "A mineral sunscreen mist for easy top-ups on the go.",
        "category": "sun-care",
        "price": Decimal("1149.00"),
        "stock": 17,
        "tags": ["sunscreen", "mineral", "sensitive-skin"],
    },
    {
        "name": "Velvet Clean Makeup Melt",
        "description": "A balm cleanser that melts makeup and sunscreen without residue.",
        "category": "skincare",
        "price": Decimal("899.00"),
        "stock": 33,
        "tags": ["cleanser", "makeup-remover", "dry-skin"],
    },
    {
        "name": "Soft Focus Hand Cream",
        "description": "A compact hand cream with ceramides and a clean cotton scent.",
        "category": "body-care",
        "price": Decimal("299.00"),
        "stock": 86,
        "tags": ["hand-care", "budget", "giftable"],
    },
    {
        "name": "Moon Garden Ritual Set",
        "description": "Premium night cream, serum, and lip mask for a slow evening ritual.",
        "category": "gift-sets",
        "price": Decimal("2899.00"),
        "stock": 12,
        "tags": ["gift-set", "premium", "night-routine"],
    },
    {
        "name": "Clear Day Gel Moisturiser",
        "description": "A weightless gel moisturiser for combination and oily skin.",
        "category": "skincare",
        "price": Decimal("699.00"),
        "stock": 46,
        "tags": ["moisturiser", "oil-free", "combination-skin"],
    },
    {
        "name": "Rosewater Recovery Toner",
        "description": "A hydrating toner with panthenol for dry, dull skin.",
        "category": "skincare",
        "price": Decimal("649.00"),
        "stock": 39,
        "tags": ["toner", "hydrating", "fragrance-free"],
    },
    {
        "name": "Weekend Escape Minis",
        "description": "Travel-size cleanser, sunscreen, and lip balm for short trips.",
        "category": "gift-sets",
        "price": Decimal("799.00"),
        "stock": 25,
        "tags": ["travel", "gift-set", "budget"],
    },
    {
        "name": "Amber Vanilla Body Wash",
        "description": "A creamy body wash with a warm, comforting fragrance.",
        "category": "body-care",
        "price": Decimal("549.00"),
        "stock": 37,
        "tags": ["body-care", "fragranced", "giftable"],
    },
    {
        "name": "Daily Defence SPF 30",
        "description": "An everyday sunscreen lotion for comfortable daily wear.",
        "category": "sun-care",
        "price": Decimal("679.00"),
        "stock": 51,
        "tags": ["sunscreen", "spf-30", "budget"],
    },
    {
        "name": "Calm Skin Essentials Set",
        "description": "A fragrance-free cleanser and moisturiser pair for sensitive skin.",
        "category": "gift-sets",
        "price": Decimal("1399.00"),
        "stock": 22,
        "tags": ["gift-set", "sensitive-skin", "fragrance-free"],
    },
    {
        "name": "Plum Plush Lip Oil",
        "description": "A glossy lip oil with a subtle plum tint and soft vanilla note.",
        "category": "lip-care",
        "price": Decimal("479.00"),
        "stock": 64,
        "tags": ["lip-care", "tinted", "giftable"],
    },
    {
        "name": "Radiance Reset Gift Box",
        "description": "A premium glow-focused set with serum, sunscreen, and lip oil.",
        "category": "gift-sets",
        "price": Decimal("2499.00"),
        "stock": 15,
        "tags": ["gift-set", "premium", "glow"],
    },
]


def load_demo_electronics_products() -> list[dict[str, object]]:
    """Load the existing cross-domain fixture for development demo state only."""
    fixture_path = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "electronics_catalogue.json"
    products = json.loads(fixture_path.read_text(encoding="utf-8"))
    return [{**product, "price": Decimal(str(product["price"]))} for product in products]


# Sparse authoritative facts for the reference catalogue. Tags remain legacy
# display/retrieval metadata; these declarations are the canonical truth layer.
DEMO_PRODUCT_FACTS_BY_NAME: dict[str, list[dict[str, str]]] = {
    "Dewdrop Barrier Cream": [
        {"family": "role", "concept": "moisturiser"},
        {"family": "attribute", "concept": "fragrance-free"},
        {"family": "suitability", "concept": "dry-skin"},
        {"family": "suitability", "concept": "sensitive-skin"},
    ],
    "Cloudmilk Gentle Cleanser": [
        {"family": "role", "concept": "cleanser"},
        {"family": "attribute", "concept": "gentle"},
        {"family": "attribute", "concept": "fragrance-free"},
        {"family": "suitability", "concept": "sensitive-skin"},
    ],
    "Sunveil SPF 50 Fluid": [
        {"family": "role", "concept": "sunscreen"},
        {"family": "attribute", "concept": "lightweight"},
        {"family": "use_case", "concept": "sun-protection"},
    ],
    "Petal Tint Lip Balm": [
        {"family": "role", "concept": "lip-care"},
        {"family": "attribute", "concept": "tinted"},
    ],
    "Quiet Night Repair Serum": [
        {"family": "role", "concept": "serum"},
        {"family": "suitability", "concept": "sensitive-skin"},
    ],
    "Morning Glow Gift Box": [
        {"family": "role", "concept": "gift-set"},
        {"family": "use_case", "concept": "gift-ready"},
        {"family": "use_case", "concept": "cleanser-moisturiser-routine"},
    ],
    "Oat Silk Body Lotion": [
        {"family": "role", "concept": "body-care"},
        {"family": "attribute", "concept": "fragrance-free"},
        {"family": "suitability", "concept": "sensitive-skin"},
    ],
    "Berry Cocoa Lip Mask": [
        {"family": "role", "concept": "lip-care"},
        {"family": "attribute", "concept": "giftable"},
    ],
    "Mineral Mist SPF 40": [
        {"family": "role", "concept": "sunscreen"},
        {"family": "attribute", "concept": "mineral"},
        {"family": "suitability", "concept": "sensitive-skin"},
        {"family": "use_case", "concept": "sun-protection"},
    ],
    "Velvet Clean Makeup Melt": [
        {"family": "role", "concept": "cleanser"},
        {"family": "attribute", "concept": "makeup-remover"},
        {"family": "suitability", "concept": "dry-skin"},
        {"family": "use_case", "concept": "makeup-removal"},
    ],
    "Soft Focus Hand Cream": [
        {"family": "role", "concept": "hand-care"},
        {"family": "attribute", "concept": "giftable"},
    ],
    "Moon Garden Ritual Set": [
        {"family": "role", "concept": "gift-set"},
        {"family": "attribute", "concept": "premium"},
        {"family": "use_case", "concept": "gift-ready"},
    ],
    "Clear Day Gel Moisturiser": [
        {"family": "role", "concept": "moisturiser"},
        {"family": "attribute", "concept": "gel"},
        {"family": "attribute", "concept": "oil-free"},
        {"family": "suitability", "concept": "oily-skin"},
        {"family": "suitability", "concept": "combination-skin"},
    ],
    "Rosewater Recovery Toner": [
        {"family": "role", "concept": "toner"},
        {"family": "attribute", "concept": "fragrance-free"},
        {"family": "suitability", "concept": "dry-skin"},
    ],
    "Weekend Escape Minis": [
        {"family": "role", "concept": "gift-set"},
        {"family": "attribute", "concept": "travel"},
        {"family": "use_case", "concept": "gift-ready"},
    ],
    "Amber Vanilla Body Wash": [
        {"family": "role", "concept": "body-care"},
        {"family": "attribute", "concept": "fragranced"},
        {"family": "attribute", "concept": "giftable"},
    ],
    "Daily Defence SPF 30": [
        {"family": "role", "concept": "sunscreen"},
        {"family": "use_case", "concept": "sun-protection"},
    ],
    "Calm Skin Essentials Set": [
        {"family": "role", "concept": "gift-set"},
        {"family": "attribute", "concept": "fragrance-free"},
        {"family": "suitability", "concept": "sensitive-skin"},
        {"family": "use_case", "concept": "gift-ready"},
        {"family": "use_case", "concept": "cleanser-moisturiser-routine"},
    ],
    "Plum Plush Lip Oil": [
        {"family": "role", "concept": "lip-care"},
        {"family": "attribute", "concept": "tinted"},
        {"family": "attribute", "concept": "giftable"},
    ],
    "Radiance Reset Gift Box": [
        {"family": "role", "concept": "gift-set"},
        {"family": "attribute", "concept": "premium"},
        {"family": "use_case", "concept": "gift-ready"},
    ],
}
