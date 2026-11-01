"""Which test file the Fix agent is shown (agents/fix.py). A verbose pytest log
lists every test file, passing ones included; the Fix agent must see a file
with a failing test, found at its real path in the repository."""

from agent_system.agents.fix import _find_test_path_from_text

_VERBOSE_LOG = """\
+ python -m pytest -v
tests/test_cart.py::test_cart_lines_sorted_by_sku PASSED                 [  4%]
tests/test_orders.py::test_checkout_with_coupon FAILED                   [ 73%]
tests/test_tax.py::test_discount_lowers_vat_proportionally FAILED        [ 96%]
=================================== FAILURES ===================================
tests/test_orders.py:14: AssertionError
tests/test_tax.py:24: AssertionError
=========================== short test summary info ============================
FAILED tests/test_orders.py::test_checkout_with_coupon - AssertionError
FAILED tests/test_tax.py::test_discount_lowers_vat_proportionally - Assertion...
ERROR: script returned exit code 1
"""


def test_passing_test_files_are_skipped():
    assert _find_test_path_from_text(_VERBOSE_LOG) == "tests/test_orders.py"


def test_the_target_files_own_test_is_preferred():
    assert _find_test_path_from_text(_VERBOSE_LOG, "tax.py") == "tests/test_tax.py"


def test_target_without_a_failing_test_falls_back_to_the_first_failing_file():
    assert _find_test_path_from_text(_VERBOSE_LOG, "money.py") == "tests/test_orders.py"


def test_traceback_line_alone_is_enough():
    log = "test_refund.py::test_fee PASSED\n/var/lib/jenkins/workspace/lab/test_app.py:12: AssertionError\n"
    assert _find_test_path_from_text(log) == "/var/lib/jenkins/workspace/lab/test_app.py"


def test_without_failure_markers_the_first_test_path_is_used():
    assert _find_test_path_from_text("see tests/test_app.py for details") == "tests/test_app.py"
    assert _find_test_path_from_text("no paths here") is None
