"""
Owner detection — maps PDF customer names to canonical owner labels.

Config in settings.toml:
  [owners]
  "Emanuel"    = "Gandrik"
  "Dian Pratiwi" = "Helen"

Matching is case-insensitive substring. First match wins.
Falls back to "Unknown" if no match found.
"""

_DEFAULT_MAPPINGS = {
    "Emanuel": "Gandrik",
    "Dian Pratiwi": "Helen",
}


def detect_owner(customer_name: str, mappings: dict = None) -> str:
    """
    Return canonical owner label for a customer name string.
    mappings: {substring: owner_label} — loaded from settings.toml [owners]
    """
    if mappings is None:
        mappings = _DEFAULT_MAPPINGS
    name_lower = customer_name.lower()
    for substring, owner in mappings.items():
        if substring.lower() in name_lower:
            return owner
    return "Unknown"
