"""Hand-written bugs for training/sample_shop, harder than the single-token
mutants of mutations.py: deleted guards, swapped arguments, wrong business
rules, aliasing, multi-line logic errors and wrong configuration values. Many
of them make a test fail in a different module from the one holding the bug
(test_orders.py exercises every module).

Each bug stays inside one file, because the production Fix agent rewrites one
file. Like a Mutant it has an id, a file, a function (the label the agents are
checked against) and apply(source).
"""
import ast
import json
from dataclasses import dataclass

from .mutations import PROJECTS, RUNBUGRUN, Mutant

SHOP = "sample_shop"


@dataclass(frozen=True)
class Bug:
    name: str
    file: str
    function: str  # the function holding the bug, or the constant for configuration bugs
    kind: str
    edits: tuple   # ((old, new), ...): each old text occurs exactly once in the clean file
    project: str = SHOP

    @property
    def id(self) -> str:
        return f"{self.project}/{self.file}::{self.name}"

    def apply(self, source: str) -> str:
        for old, new in self.edits:
            assert source.count(old) == 1, f"{self.id}: {old!r} must occur exactly once"
            source = source.replace(old, new)
        return source


def load_bug(data: dict):
    """Rebuild a Bug or a Mutant from the dict saved in a teacher run."""
    if "edits" in data:
        return Bug(**{**data, "edits": tuple(tuple(edit) for edit in data["edits"])})
    return Mutant(**data)


def _owner(source: str, old: str) -> str | None:
    line = source[:source.index(old)].count("\n") + 1
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.lineno <= line <= node.end_lineno:
            return node.name
    return None


# (name, file, kind, edits, function for module-level bugs)
_CATALOGUE = [
    # money.py
    ("round-half-even", "money.py", "wrong rounding", [
        ("from decimal import ROUND_HALF_UP, Decimal", "from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal"),
        ("rounding=ROUND_HALF_UP)", "rounding=ROUND_HALF_EVEN)")], "to_money"),
    ("float-accepted", "money.py", "deleted guard", [
        ('    if isinstance(value, float):\n        raise TypeError("use str or Decimal for money, not float")\n', "")], None),
    ("percent-truncated", "money.py", "wrong rounding", [
        ("return to_money(Decimal(amount) * Decimal(percent) / 100)",
         "return to_money(int(Decimal(amount) * Decimal(percent)) / Decimal(100))")], None),
    ("percent-not-rounded", "money.py", "missing rounding", [
        ("return to_money(Decimal(amount) * Decimal(percent) / 100)",
         "return to_money(amount) * Decimal(percent) / 100")], None),
    ("split-extra-cents-last", "money.py", "wrong rule", [
        ("(1 if i < extra else 0)", "(1 if i >= parts - extra else 0)")], None),
    ("split-zero-parts", "money.py", "boundary", [("if parts <= 0:", "if parts < 0:")], None),
    ("split-drops-remainder", "money.py", "lost value", [
        ("share, extra = divmod(cents, parts)", "share, extra = cents // parts, 0")], None),
    ("format-double-minus", "money.py", "wrong variable", [
        ('return f"{sign}{abs(amount):,.2f} {currency}"', 'return f"{sign}{amount:,.2f} {currency}"')], None),
    ("format-no-thousands", "money.py", "formatting", [
        ('return f"{sign}{abs(amount):,.2f} {currency}"', 'return f"{sign}{abs(amount):.2f} {currency}"')], None),
    ("format-default-currency", "money.py", "wrong default", [
        ('def format_money(amount, currency="EUR"):', 'def format_money(amount, currency="USD"):')], None),
    # dates.py
    ("parse-day-first", "dates.py", "swapped fields", [
        ("year, month, day = (int(part)", "day, month, year = (int(part)")], None),
    ("saturday-business-day", "dates.py", "boundary", [("day.weekday() < 5", "day.weekday() < 6")], None),
    ("holidays-ignored", "dates.py", "deleted condition", [
        ("return day.weekday() < 5 and day not in HOLIDAYS", "return day.weekday() < 5")], None),
    ("counts-start-day", "dates.py", "statement order", [
        ("        current += timedelta(days=1)\n        if is_business_day(current):\n            remaining -= 1",
         "        if is_business_day(current):\n            remaining -= 1\n        current += timedelta(days=1)")], None),
    ("calendar-days", "dates.py", "deleted condition", [
        ("        if is_business_day(current):\n            remaining -= 1", "        remaining -= 1")], None),
    ("end-exclusive", "dates.py", "boundary", [("return start <= day <= end", "return start <= day < end")], None),
    ("wrong-holiday", "dates.py", "configuration", [("date(2026, 3, 20)", "date(2026, 3, 21)")], "HOLIDAYS"),
    # catalog.py
    ("header-parsed", "catalog.py", "deleted condition", [
        (' or line.startswith("sku,")', "")], None),
    ("comment-parsed", "catalog.py", "deleted condition", [
        (' or line.startswith("#")', "")], None),
    ("line-numbers-from-zero", "catalog.py", "off by one", [
        ("enumerate(lines, start=1)", "enumerate(lines)")], None),
    ("fields-not-stripped", "catalog.py", "missing normalisation", [
        ('fields = [field.strip() for field in line.split(",")]', 'fields = line.split(",")')], None),
    ("duplicates-overwrite", "catalog.py", "deleted guard", [
        ('        if sku in catalog:\n            raise ValueError(f"line {number}: duplicate sku {sku}")\n', "")], None),
    ("columns-swapped", "catalog.py", "swapped fields", [
        ("sku, name, price, category, weight = fields", "sku, name, category, price, weight = fields")], None),
    ("unknown-sku-returns-none", "catalog.py", "swallowed error", [
        ('raise KeyError(f"unknown sku {sku}") from None', "return None")], None),
    ("ties-not-by-sku", "catalog.py", "incomplete sort key", [
        ('key=lambda product: (product["price"], product["sku"])', 'key=lambda product: product["price"]')], None),
    ("most-expensive-first", "catalog.py", "wrong order", [
        ('product["sku"]))', 'product["sku"]), reverse=True)')], None),
    # cart.py
    ("add-mutates-input", "cart.py", "aliasing", [
        ("    new_cart = dict(cart)\n    new_cart[sku] = new_cart.get(sku, 0) + quantity",
         "    new_cart = cart\n    new_cart[sku] = new_cart.get(sku, 0) + quantity")], None),
    ("remove-mutates-input", "cart.py", "aliasing", [
        ("    new_cart = dict(cart)\n    left =", "    new_cart = cart\n    left =")], None),
    ("max-quantity-exclusive", "cart.py", "boundary", [
        ("if new_cart[sku] > MAX_QUANTITY:", "if new_cart[sku] >= MAX_QUANTITY:")], None),
    ("zero-quantity-allowed", "cart.py", "boundary", [("if quantity < 1:", "if quantity < 0:")], None),
    ("remove-keeps-empty-line", "cart.py", "boundary", [("if left > 0:", "if left >= 0:")], None),
    ("remove-goes-negative", "cart.py", "deleted branch", [
        ("    if left > 0:\n        new_cart[sku] = left\n    else:\n        del new_cart[sku]",
         "    new_cart[sku] = left")], None),
    ("lines-unsorted", "cart.py", "wrong order", [("for sku in sorted(cart):", "for sku in cart:")], None),
    ("line-total-unit-price", "cart.py", "wrong formula", [
        ('to_money(product["price"] * quantity)', 'to_money(product["price"])')], None),
    ("weight-ignores-quantity", "cart.py", "wrong formula", [
        ('find_product(catalog, sku)["weight_kg"] * quantity', 'find_product(catalog, sku)["weight_kg"]')], None),
    # discounts.py
    ("min-subtotal-exclusive", "discounts.py", "boundary", [
        ('subtotal >= coupon.get("min_subtotal", 0)', 'subtotal > coupon.get("min_subtotal", 0)')], None),
    ("validity-args-swapped", "discounts.py", "swapped arguments", [
        ('is_within(today, coupon["valid_from"], coupon["valid_to"])',
         'is_within(coupon["valid_from"], today, coupon["valid_to"])')], None),
    ("dates-not-checked", "discounts.py", "deleted condition", [
        ('        is_within(today, coupon["valid_from"], coupon["valid_to"])\n        and subtotal',
         "        subtotal")], None),
    ("discount-not-capped", "discounts.py", "deleted guard", [
        ("return min(amount, subtotal)", "return amount")], None),
    ("bulk-on-whole-cart", "discounts.py", "wrong variable", [
        ('amount = percent_of(line_total, coupon["value"])', 'amount = percent_of(subtotal, coupon["value"])')], None),
    ("bulk-min-quantity-exclusive", "discounts.py", "boundary", [
        ('quantity >= coupon["min_quantity"]', 'quantity > coupon["min_quantity"]')], None),
    ("bulk-any-sku", "discounts.py", "deleted condition", [
        ('product["sku"] == coupon["sku"] and ', "")], None),
    ("ties-keep-last", "discounts.py", "boundary", [("if amount > best_amount:", "if amount >= best_amount:")], None),
    ("coupons-stack", "discounts.py", "wrong rule", [
        ('        if amount > best_amount:\n            best_code, best_amount = coupon["code"], amount',
         '        best_code, best_amount = coupon["code"], best_amount + amount')], None),
    ("unknown-kind-ignored", "discounts.py", "swallowed error", [
        ('raise ValueError(f"unknown coupon kind {kind}")', "amount = to_money(0)")], None),
    # tax.py
    ("default-rate-reduced", "tax.py", "configuration", [("DEFAULT_RATE = 19", "DEFAULT_RATE = 7")], "DEFAULT_RATE"),
    ("books-full-rate", "tax.py", "configuration", [('"books": 7', '"books": 19')], "RATES"),
    ("no-default-rate", "tax.py", "missing fallback", [
        ("return RATES.get(category, DEFAULT_RATE)", "return RATES[category]")], None),
    ("discount-per-line", "tax.py", "wrong formula", [
        ("share = line_total - Decimal(discount) * line_total / subtotal",
         "share = line_total - Decimal(discount)")], None),
    ("discount-ignored", "tax.py", "deleted branch", [
        ("        if discount and subtotal:\n            share = line_total - Decimal(discount) * line_total / subtotal\n",
         "")], None),
    ("discount-ratio-inverted", "tax.py", "wrong formula", [
        ("Decimal(discount) * line_total / subtotal", "Decimal(discount) * subtotal / line_total")], None),
    # shipping.py
    ("free-threshold-exclusive", "shipping.py", "boundary", [
        ("subtotal >= threshold", "subtotal > threshold")], None),
    ("started-kg-truncated", "shipping.py", "wrong rounding", [
        ("math.ceil(weight_kg - INCLUDED_KG)", "int(weight_kg - INCLUDED_KG)")], None),
    ("included-kg-charged", "shipping.py", "wrong formula", [
        ("math.ceil(weight_kg - INCLUDED_KG)", "math.ceil(weight_kg)")], None),
    ("negative-extra-kg", "shipping.py", "deleted guard", [
        ("extra_kg = max(0, math.ceil(weight_kg - INCLUDED_KG))", "extra_kg = math.ceil(weight_kg - INCLUDED_KG)")], None),
    ("world-free-shipping", "shipping.py", "configuration", [
        ('"eu": Decimal("120.00")}', '"eu": Decimal("120.00"), "world": Decimal("500.00")}')], "FREE_SHIPPING_FROM"),
    ("fees-unpacked-swapped", "shipping.py", "swapped fields", [
        ("base, per_kg, _ = ZONES[zone]", "per_kg, base, _ = ZONES[zone]")], None),
    ("no-dispatch-day", "shipping.py", "off by one", [
        ("add_business_days(order_day, transit_days + 1)", "add_business_days(order_day, transit_days)")], None),
    ("calendar-delivery", "shipping.py", "wrong helper", [
        ("import math\n", "import math\nfrom datetime import timedelta\n"),
        ("return add_business_days(order_day, transit_days + 1)",
         "return order_day + timedelta(days=transit_days + 1)")], "delivery_date"),
    ("zone-not-validated", "shipping.py", "deleted guard", [
        ('    if zone not in ZONES:\n        raise ValueError(f"unknown zone {zone}")\n', "")], None),
    ("eu-transit-days", "shipping.py", "configuration", [
        ('"eu": ("9.90", "2.50", 5)', '"eu": ("9.90", "2.50", 4)')], "ZONES"),
    # loyalty.py
    ("tier-threshold-exclusive", "loyalty.py", "boundary", [("if points >= threshold:", "if points > threshold:")], None),
    ("tier-first-match", "loyalty.py", "early return", [
        ("        if points >= threshold:\n            name = tier",
         "        if points >= threshold:\n            return tier")], None),
    ("points-rounded", "loyalty.py", "wrong rounding", [("points = int(total)", "points = round(total)")], None),
    ("tier-includes-this-order", "loyalty.py", "wrong rule", [
        ('if tier_for(current_points) == "gold":', 'if tier_for(current_points + points) == "gold":')], None),
    ("partial-voucher", "loyalty.py", "wrong rounding", [
        ("vouchers = wanted // POINTS_PER_VOUCHER", "vouchers = -(-wanted // POINTS_PER_VOUCHER)")], None),
    ("redeem-all-refused", "loyalty.py", "boundary", [("if wanted > points:", "if wanted >= points:")], None),
    # orders.py
    ("free-shipping-before-discount", "orders.py", "wrong rule", [
        ("shipping_cost(zone, total_weight(cart, catalog), discounted)",
         "shipping_cost(zone, total_weight(cart, catalog), items)")], None),
    ("tax-before-discount", "orders.py", "missing argument", [
        ("tax = total_tax(lines, discount)", "tax = total_tax(lines)")], None),
    ("total-without-shipping", "orders.py", "missing term", [
        ("total = to_money(discounted + tax + shipping)", "total = to_money(discounted + tax)")], None),
    ("total-without-discount", "orders.py", "wrong variable", [
        ("total = to_money(discounted + tax + shipping)", "total = to_money(items + tax + shipping)")], None),
    ("points-before-tax", "orders.py", "wrong variable", [
        ('"points": points_earned(total, loyalty_points),', '"points": points_earned(discounted, loyalty_points),')], None),
    ("points-args-swapped", "orders.py", "swapped arguments", [
        ("points_earned(total, loyalty_points)", "points_earned(loyalty_points, total)")], None),
    ("empty-cart-accepted", "orders.py", "deleted guard", [
        ('    if not cart:\n        raise ValueError("cart is empty")\n', "")], None),
    ("delivery-args-swapped", "orders.py", "swapped arguments", [
        ("delivery_date(zone, today)", "delivery_date(today, zone)")], None),
    # invoices.py
    ("short-padding", "invoices.py", "formatting", [("{sequence:05d}", "{sequence:04d}")], None),
    ("sequence-zero-allowed", "invoices.py", "boundary", [("if sequence < 1:", "if sequence < 0:")], None),
    ("sequence-ignores-year", "invoices.py", "deleted condition", [
        (' if number.split("-")[1] == str(year)', "")], None),
    ("year-compared-as-int", "invoices.py", "type mismatch", [
        ('number.split("-")[1] == str(year)', 'number.split("-")[1] == year')], None),
    ("sequence-counts-invoices", "invoices.py", "wrong formula", [
        ("return max(used, default=0) + 1", "return len(used) + 1")], None),
    ("discount-without-minus", "invoices.py", "formatting", [
        ("-{format_money(order['discount'])}", "{format_money(order['discount'])}")], None),
    ("zero-discount-shown", "invoices.py", "wrong condition", [
        ('if order["discount"]:', 'if order["discount"] is not None:')], None),
    ("vat-and-shipping-swapped", "invoices.py", "statement order", [
        ("    rows.append(f\"VAT: {format_money(order['tax'])}\")\n    rows.append(f\"Shipping: {format_money(order['shipping'])}\")",
         "    rows.append(f\"Shipping: {format_money(order['shipping'])}\")\n    rows.append(f\"VAT: {format_money(order['tax'])}\")")], None),
]


def shop_bugs() -> list[Bug]:
    bugs = []
    for name, file, kind, edits, function in _CATALOGUE:
        source = (PROJECTS[SHOP] / file).read_text(encoding="utf-8")
        function = function or _owner(source, edits[0][0])
        assert function, f"{file}::{name}: give the function for a module-level bug"
        bug = Bug(name, file, function, kind, tuple(tuple(edit) for edit in edits))
        bug.apply(source)  # every old text must match the clean file exactly once
        bugs.append(bug)
    return bugs


def runbugrun_bugs() -> list[Bug]:
    """The real bugs selected by training/runbugrun.py: the project holds the
    fixed submission, and the bug swaps in the buggy one, so like every Bug it
    stays inside one file."""
    bugs = []
    for path in sorted(RUNBUGRUN.glob("*/bug.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        fixed = (path.parent / "main.py").read_text(encoding="utf-8")
        bugs.append(Bug(f"rbr-{meta['id']}", "main.py", meta["function"], meta["kind"],
                        ((fixed, meta["buggy_code"]),), project=f"runbugrun/{path.parent.name}"))
    return bugs
