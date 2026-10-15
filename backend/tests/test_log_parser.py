"""Deterministic log parser: masking, grouping, ranking and reduction ratio."""
from __future__ import annotations

from datetime import datetime, timezone

from app.agents.log_parser import mask, normalize_source, parse_logs
from app.connectors.base import LogLine
from app.connectors.simulated import SimulatedWorld, parse_log_line


def line(msg: str, level: str = "INFO", src: str = "svc") -> LogLine:
    return LogLine(ts=datetime(2026, 1, 1, tzinfo=timezone.utc), source=src, level=level, message=msg, raw=msg)


def test_mask_variable_tokens():
    m = mask("dial tcp 10.0.3.17:5050: connect refused session=4f9ac2e1b0d3 took 123ms id=ab12cd")
    assert "<IP>" in m and "session=<ID>" in m and "<NUM>ms" in m and "id=<ID>" in m
    assert "10.0.3.17" not in m


def test_mask_keeps_meaningful_words():
    m = mask('invalid PAYMENT_SERVICE_ADDR "paymentservice50051": missing port in address')
    assert "PAYMENT_SERVICE_ADDR" in m and "missing port" in m


def test_normalize_pod_source():
    assert normalize_source("checkoutservice-7d9f8c6b5-x2k9p") == "checkoutservice"
    assert normalize_source("frontend-canary-3fa9c1d2e-abcde") == "frontend-canary"
    assert normalize_source("kubelet") == "kubelet"


def test_grouping_and_counts():
    lines = [line(f"request served in {i}ms session={i:012x}") for i in range(50)]
    lines += [line("config validation failed, exiting with code 1", "FATAL", f"checkoutservice-7d9f8c6b5-x2k9{i}") for i in range(3)]
    r = parse_logs(lines)
    assert r.raw_line_count == 53
    assert len(r.groups) == 2
    # FATAL ranks above a more frequent INFO pattern
    assert r.groups[0].level == "FATAL" and r.groups[0].count == 3
    assert list(r.groups[0].sources) == ["checkoutservice"]
    assert r.groups[1].count == 50


def test_reduction_ratio():
    r = parse_logs([line(f"x {i}") for i in range(100)])
    assert len(r.groups) == 1
    assert r.reduction_ratio(8) == 0.99


def test_empty_input():
    r = parse_logs([])
    assert r.raw_line_count == 0 and r.groups == [] and r.reduction_ratio(8) == 0.0


def test_scenario_logs_reduce_massively(settings):
    w = SimulatedWorld(settings.scenarios_dir, "checkout-config-crashloop")
    r = parse_logs([parse_log_line(l) for l in w.raw_logs])
    assert r.raw_line_count > 1000
    assert len(r.groups) < 40
    top_errors = [g.template for g in r.top(5) if g.level in ("ERROR", "FATAL")]
    assert any("PAYMENT_SERVICE_ADDR" in t for t in top_errors)
