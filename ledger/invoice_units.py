"""Display existing/custom units without rewriting historical item data."""

UNIT_ALIASES = {
    "each": "pcs",
    "piece": "pcs",
    "pieces": "pcs",
    "pc": "pcs",
    "kilogram": "kg",
    "kilograms": "kg",
    "gram": "g",
    "grams": "g",
    "milligram": "mg",
    "tonne": "t",
    "tonnes": "t",
    "millimetre": "mm",
    "millimeter": "mm",
    "centimetre": "cm",
    "centimeter": "cm",
    "metre": "m",
    "meter": "m",
    "metres": "m",
    "meters": "m",
    "kilometre": "km",
    "kilometer": "km",
    "litre": "L",
    "liter": "L",
    "litres": "L",
    "liters": "L",
    "l": "L",
    "millilitre": "ml",
    "milliliter": "ml",
    "millilitres": "ml",
    "square metre": "m²",
    "square meter": "m²",
    "cubic metre": "m³",
    "cubic meter": "m³",
    "hour": "h",
    "hours": "h",
    "minute": "min",
    "minutes": "min",
    "second": "s",
    "carton": "ctn",
    "cartons": "ctn",
    "pallet": "plt",
    "pallets": "plt",
}


def display_quantity(quantity, unit):
    value = str(quantity)
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return f"{value} {UNIT_ALIASES.get(unit.strip().lower(), unit.strip())}"
