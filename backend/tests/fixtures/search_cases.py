"""Queries retained from the original serial contract; no payment/auth harness."""
CASES = [
    ("single", "gentle cleanser under 700", False, False),
    ("composition", "gentle cleanser and fragrance-free moisturiser under 1500", False, False),
    ("merchant_offer", "cleanser and moisturiser under 1500", True, False),
    ("infeasible_budget", "cleanser and moisturiser under 10", False, False),
    ("compatibility", "usb-c charger under 3000", False, False),
    ("exclusion", "cleanser without fragrance under 1000", False, False),
    ("clarification", "lotion under 5000", False, False),
    ("behavioral", "gentle cleanser under 700", False, True),
    ("compact_typo", "gentlefacewash under 700", False, False),
    ("ties_and_archive", "cleanser under 300", False, False),
    ("semantic_primary", "gentle cleanser under 700", False, False),
    ("semantic_exact_title", "Tie Cleanser A", False, False),
    ("semantic_fallback", "gentle cleanser under 700", False, False),
]
