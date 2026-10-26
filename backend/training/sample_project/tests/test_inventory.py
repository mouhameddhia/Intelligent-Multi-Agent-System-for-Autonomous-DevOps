import pytest

from inventory import add_stock, low_stock_items, remove_stock, reorder_quantity


def test_add_stock_accumulates():
    stock = {}
    add_stock(stock, "apple", 3)
    add_stock(stock, "apple", 2)
    assert stock == {"apple": 5}


def test_add_stock_rejects_non_positive():
    with pytest.raises(ValueError):
        add_stock({}, "apple", 0)
    assert add_stock({}, "apple", 1) == {"apple": 1}


def test_remove_stock_decrements():
    stock = {"pear": 4}
    remove_stock(stock, "pear", 3)
    assert stock == {"pear": 1}


def test_remove_stock_allows_emptying():
    assert remove_stock({"pear": 4}, "pear", 4) == {"pear": 0}


def test_remove_stock_rejects_overdraw():
    with pytest.raises(ValueError):
        remove_stock({"pear": 2}, "pear", 3)


def test_low_stock_items_sorted_and_strict():
    stock = {"b": 1, "a": 0, "c": 5, "d": 9}
    assert low_stock_items(stock) == ["a", "b"]
    assert low_stock_items(stock, threshold=6) == ["a", "b", "c"]


def test_reorder_quantity_rounds_up_to_packs():
    assert reorder_quantity(3, 10, 4) == 8
    assert reorder_quantity(2, 10, 4) == 8
    assert reorder_quantity(1, 10, 4) == 12


def test_reorder_quantity_when_stocked():
    assert reorder_quantity(10, 10, 4) == 0
    assert reorder_quantity(12, 10, 4) == 0
