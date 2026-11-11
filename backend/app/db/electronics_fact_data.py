"""Canonical fact declarations for the existing electronics demo fixture."""

ELECTRONICS_FACTS_BY_NAME: dict[str, list[dict[str, str]]] = {
    "Volt 65W USB-C Charger": [
        {"family": "role", "concept": "charger"},
        {"family": "attribute", "concept": "usb-c"},
        {"family": "attribute", "concept": "pd"},
        {"family": "attribute", "concept": "fast-charging"},
        {"family": "compatibility", "concept": "macbook"},
        {"family": "compatibility", "concept": "laptop"},
        {"family": "compatibility", "concept": "phone"},
        {"family": "use_case", "concept": "laptop-charging"},
    ],
    "Volt 20W USB-C Charger": [
        {"family": "role", "concept": "charger"},
        {"family": "attribute", "concept": "usb-c"},
        {"family": "compatibility", "concept": "phone"},
        {"family": "use_case", "concept": "phone-charging"},
    ],
    "Arc USB-C Cable": [
        {"family": "role", "concept": "cable"},
        {"family": "attribute", "concept": "usb-c"},
        {"family": "compatibility", "concept": "usb-c"},
    ],
    "CableDock USB-C Hub": [
        {"family": "role", "concept": "hub"},
        {"family": "attribute", "concept": "usb-c"},
        {"family": "compatibility", "concept": "laptop"},
        {"family": "compatibility", "concept": "macbook"},
    ],
    "TravelCharge 10000": [
        {"family": "role", "concept": "power-bank"},
        {"family": "attribute", "concept": "usb-c"},
        {"family": "attribute", "concept": "travel"},
        {"family": "use_case", "concept": "device-charging"},
    ],
    "Nimbus Studio Headphones": [
        {"family": "role", "concept": "headphones"},
        {"family": "attribute", "concept": "noise-cancelling"},
        {"family": "attribute", "concept": "wireless"},
        {"family": "suitability", "concept": "commute"},
    ],
    "Orbit A1 Phone": [
        {"family": "role", "concept": "phone"},
        {"family": "attribute", "concept": "large-screen"},
        {"family": "attribute", "concept": "non-gaming"},
    ],
}
