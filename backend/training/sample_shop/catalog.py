"""Product catalogue, loaded from CSV lines: sku,name,price,category,weight_kg."""
from decimal import Decimal

from money import to_money


def parse_catalog(lines):
    """Dict sku -> product. Skips the header, blank lines and '#' comments."""
    catalog = {}
    for number, line in enumerate(lines, start=1):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("sku,"):
            continue
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 5:
            raise ValueError(f"line {number}: expected 5 fields, got {len(fields)}")
        sku, name, price, category, weight = fields
        if sku in catalog:
            raise ValueError(f"line {number}: duplicate sku {sku}")
        catalog[sku] = {
            "sku": sku,
            "name": name,
            "price": to_money(price),
            "category": category,
            "weight_kg": Decimal(weight),
        }
    return catalog


def find_product(catalog, sku):
    try:
        return catalog[sku]
    except KeyError:
        raise KeyError(f"unknown sku {sku}") from None


def in_category(catalog, category):
    """Products of one category, cheapest first; equal prices are ordered by sku."""
    products = [product for product in catalog.values() if product["category"] == category]
    return sorted(products, key=lambda product: (product["price"], product["sku"]))
