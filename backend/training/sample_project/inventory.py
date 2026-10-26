"""Stock tracking for the warehouse. Stock is a dict of sku -> quantity."""


def add_stock(stock, sku, quantity):
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    stock[sku] = stock.get(sku, 0) + quantity
    return stock


def remove_stock(stock, sku, quantity):
    available = stock.get(sku, 0)
    if quantity > available:
        raise ValueError(f"only {available} of {sku} in stock")
    stock[sku] = available - quantity
    return stock


def low_stock_items(stock, threshold=5):
    """SKUs whose quantity is below the threshold, sorted."""
    return sorted(sku for sku, quantity in stock.items() if quantity < threshold)


def reorder_quantity(current, target, pack_size):
    """Units to order to reach the target, rounded up to whole packs."""
    missing = target - current
    if missing <= 0:
        return 0
    packs = (missing + pack_size - 1) // pack_size
    return packs * pack_size
