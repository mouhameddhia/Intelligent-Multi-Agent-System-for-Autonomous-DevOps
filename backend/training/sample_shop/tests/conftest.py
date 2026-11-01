from datetime import date

import pytest

from catalog import parse_catalog

CATALOG_LINES = [
    "sku,name,price,category,weight_kg",
    "# staples",
    "APL,Apples 1kg,2.49,food,1.0",
    "BRD,Sourdough bread,3.90,food,0.8",
    "",
    "NOV,Paperback novel,12.00,books,0.4",
    "HDP,Headphones,59.90,electronics,0.3",
    "KTL,Electric kettle,34.50,electronics,1.6",
    "JKT,Rain jacket,89.00,clothing,0.9",
    "TNT,Camping tent,149.00,outdoor,4.7",
]


@pytest.fixture
def catalog():
    return parse_catalog(CATALOG_LINES)


@pytest.fixture
def coupons():
    return [
        {"code": "SPRING10", "kind": "percent", "value": 10, "min_subtotal": 30,
         "valid_from": date(2026, 3, 1), "valid_to": date(2026, 3, 31)},
        {"code": "FIVEOFF", "kind": "fixed", "value": "5.00",
         "valid_from": date(2026, 1, 1), "valid_to": date(2026, 12, 31)},
        {"code": "APPLES", "kind": "bulk", "value": 50, "sku": "APL", "min_quantity": 3,
         "valid_from": date(2026, 1, 1), "valid_to": date(2026, 12, 31)},
    ]
