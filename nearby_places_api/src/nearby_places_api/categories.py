"""Map Overture Places categories to the API's own categories.

The tourism table was checked against Overture Places for central Tokyo
(release downloaded 2026-10-05). See README "分類対応表" for the reasons.
"""
from __future__ import annotations

import re

RESTAURANT = "restaurant"
TOURISM = "tourism"
API_CATEGORIES = (RESTAURANT, TOURISM)

# Top-level taxonomy labels: food_and_drink (current), eat_and_drink (legacy).
RESTAURANT_LABELS = {"food_and_drink", "eat_and_drink"}
RESTAURANT_SUFFIXES = ("restaurant", "eatery", "cafe", "coffee_shop", "izakaya", "bakery")

# Any of these labels anywhere in the hierarchy makes a place a tourism spot.
TOURISM_LABELS = {
    # Museums (all subtypes: art, history, science, ...).
    "museum",
    # Attractions.
    "amusement_park", "zoo", "aquarium", "planetarium", "observatory",
    # Historic places. historic_site is filtered by HISTORIC_NAME below.
    "historic_site", "palace", "monument",
    # Public art.
    "sculpture_statue", "street_art",
    # Parks, gardens and nature.
    "park", "national_park", "garden", "botanical_garden", "mountain", "beach", "nature_reserve", "lake",
    # Temples. Shinto shrines are not labelled reliably; see SHRINE_OR_TEMPLE_NAME.
    "buddhist_place_of_worship",
    "visitor_center",
    # Legacy taxonomy (before the 2025 taxonomy change).
    "attractions_and_activities", "tourist_attraction", "landmark_and_historical_building",
    "historical_landmark", "castle", "temple", "shrine", "buddhist_temple", "shinto_shrine",
}
# Too small or too everyday to suggest as a detour, even under a tourism label.
TOURISM_EXCLUDED_LABELS = {"playground", "dog_park"}
# Small neighbourhood playgrounds that are labelled as park.
CHILDREN_PARK_NAME = re.compile(r"児童遊園|児童公園|ちびっこ")

# Shinto shrines appear as christian_place_of_worship, religious_organization or with
# no category at all (e.g. Meiji Jingu, Yasukuni Jinja, Hie Jinja), so use the name.
RELIGIOUS_LABELS = {"place_of_worship", "religious_organization"}
SHRINE_OR_TEMPLE_NAME = re.compile(
    r"(神社|神宮|大社|天満宮|天神|八幡宮|稲荷|明神|寺|観音|不動尊|Shrine|Jinja|Jingu|Taisha|Temple)$", re.IGNORECASE)
# Places with no category at all, such as the Tokyo Metropolitan Government observation deck.
UNCATEGORIZED_TOURISM_NAME = re.compile(r"展望室|展望台|Observatory|Observation Deck", re.IGNORECASE)
# historic_site also holds many condominiums and office towers, so keep only names that
# look like a historic place (ruins, monuments, graves, slopes, gates, old residences, ...).
HISTORIC_NAME = re.compile(
    r"跡|碑|墓|廟|塚|坂|門|井戸|庚申|地蔵|史跡|旧|発祥|記念|資料館|邸|城|橋|宮|塔|庭園|御殿|見附|"
    r"ruins|site|monument|memorial|grave|tomb|castle|gate|garden", re.IGNORECASE)
NOT_HISTORIC_NAME = re.compile(
    r"歩道橋|跨線橋|レジデンス|マンション|ハイツ|タワー|ビル|コート|ヒルズ|ハウス|"
    r"residence|mansion|tower|court|hills|house|heights|apartment", re.IGNORECASE)


def place_labels(properties: dict) -> list[str]:
    """All category labels of a place, for both current and legacy schemas."""
    labels = []
    taxonomy = properties.get("taxonomy")
    if isinstance(taxonomy, dict):
        labels += _strings(taxonomy.get("hierarchy"))
        labels += _strings([taxonomy.get("primary")])
        labels += _strings(taxonomy.get("alternates"))
    labels += _strings([properties.get("basic_category")])
    legacy = properties.get("categories")
    if isinstance(legacy, dict):
        labels += _strings([legacy.get("primary")])
        labels += _strings(legacy.get("alternate"))
    return labels


def source_category(properties: dict) -> str | None:
    """The most specific Overture label, kept apart from the API category."""
    taxonomy = properties.get("taxonomy")
    if isinstance(taxonomy, dict) and isinstance(taxonomy.get("primary"), str):
        return taxonomy["primary"]
    if isinstance(properties.get("basic_category"), str):
        return properties["basic_category"]
    legacy = properties.get("categories")
    if isinstance(legacy, dict) and isinstance(legacy.get("primary"), str):
        return legacy["primary"]
    return None


def classify(properties: dict) -> str | None:
    """Return restaurant, tourism or None. Restaurants win when both match."""
    labels = place_labels(properties)
    if any(label in RESTAURANT_LABELS or label.endswith(RESTAURANT_SUFFIXES) for label in labels):
        return RESTAURANT
    if _is_tourism(set(labels), _name(properties)):
        return TOURISM
    return None


def _is_tourism(labels: set[str], name: str) -> bool:
    if labels & TOURISM_EXCLUDED_LABELS:
        return False
    if (not labels or labels & RELIGIOUS_LABELS) and SHRINE_OR_TEMPLE_NAME.search(name):
        return True
    if not labels and UNCATEGORIZED_TOURISM_NAME.search(name):
        return True
    matched = labels & TOURISM_LABELS
    if "park" in matched and CHILDREN_PARK_NAME.search(name):
        return False
    if matched == {"historic_site"}:
        return bool(HISTORIC_NAME.search(name)) and not NOT_HISTORIC_NAME.search(name)
    return bool(matched) or any(label.endswith("museum") for label in labels)


def _name(properties: dict) -> str:
    names = properties.get("names")
    name = names.get("primary") if isinstance(names, dict) else None
    return name.strip() if isinstance(name, str) else ""


def _strings(values) -> list[str]:
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, str) and value]
