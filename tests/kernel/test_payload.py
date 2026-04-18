"""Unit tests for kernel/payload.py helpers.

Covers happy paths (typed value present), miss paths (key absent), and
wrong-type paths (key present but wrong shape). DEBUG coercion lines
are captured via ``caplog`` so future regressions in the "silent drop"
policy surface as a test failure, not a production surprise.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from yaya.kernel.payload import (
    payload_dict,
    payload_int,
    payload_list_of_dicts,
    payload_str,
)

pytestmark = pytest.mark.unit


# -- payload_str -----------------------------------------------------------


def test_payload_str_returns_value_when_string() -> None:
    assert payload_str({"k": "v"}, "k") == "v"


def test_payload_str_returns_default_when_missing() -> None:
    assert payload_str({}, "k") == ""
    assert payload_str({}, "k", default="fallback") == "fallback"


def test_payload_str_returns_default_on_wrong_type_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="yaya.kernel.payload"):
        assert payload_str({"k": 123}, "k", default="fb") == "fb"
    assert any("payload_str coerced int" in rec.message for rec in caplog.records)


def test_payload_str_does_not_log_on_missing_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A missing key is expected; only wrong-typed values should log.
    with caplog.at_level(logging.DEBUG, logger="yaya.kernel.payload"):
        assert payload_str({}, "k") == ""
    assert all("payload_str" not in rec.message for rec in caplog.records)


# -- payload_int -----------------------------------------------------------


def test_payload_int_returns_value_when_int() -> None:
    assert payload_int({"k": 7}, "k", default=0) == 7


def test_payload_int_returns_default_when_missing() -> None:
    assert payload_int({}, "k", default=5) == 5


def test_payload_int_rejects_bool_even_though_bool_is_int_subclass(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="yaya.kernel.payload"):
        assert payload_int({"k": True}, "k", default=0) == 0
        assert payload_int({"k": False}, "k", default=9) == 9
    assert any("payload_int rejected bool" in rec.message for rec in caplog.records)


def test_payload_int_rejects_string_digit(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="yaya.kernel.payload"):
        assert payload_int({"k": "7"}, "k", default=0) == 0
    assert any("payload_int coerced str" in rec.message for rec in caplog.records)


# -- payload_dict ----------------------------------------------------------


def test_payload_dict_returns_value_when_dict() -> None:
    src: dict[str, Any] = {"k": {"a": 1, "b": "x"}}
    assert payload_dict(src, "k") == {"a": 1, "b": "x"}


def test_payload_dict_returns_empty_when_missing() -> None:
    assert payload_dict({}, "k") == {}


def test_payload_dict_returns_empty_and_logs_on_list(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="yaya.kernel.payload"):
        assert payload_dict({"k": [1, 2, 3]}, "k") == {}
    assert any("payload_dict coerced list" in rec.message for rec in caplog.records)


# -- payload_list_of_dicts -------------------------------------------------


def test_payload_list_of_dicts_returns_value_when_list_of_dicts() -> None:
    src: dict[str, Any] = {"k": [{"a": 1}, {"b": 2}]}
    assert payload_list_of_dicts(src, "k") == [{"a": 1}, {"b": 2}]


def test_payload_list_of_dicts_returns_empty_when_missing() -> None:
    assert payload_list_of_dicts({}, "k") == []


def test_payload_list_of_dicts_returns_empty_on_wrong_outer_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="yaya.kernel.payload"):
        assert payload_list_of_dicts({"k": "not a list"}, "k") == []
    assert any("payload_list_of_dicts coerced str" in rec.message for rec in caplog.records)


def test_payload_list_of_dicts_drops_non_dict_elements(
    caplog: pytest.LogCaptureFixture,
) -> None:
    src: dict[str, Any] = {"k": [{"a": 1}, "garbage", 42, {"b": 2}, None]}
    with caplog.at_level(logging.DEBUG, logger="yaya.kernel.payload"):
        result = payload_list_of_dicts(src, "k")
    assert result == [{"a": 1}, {"b": 2}]
    dropped_kinds = [rec.message for rec in caplog.records if "dropped" in rec.message]
    assert len(dropped_kinds) == 3  # str, int, NoneType


def test_payload_list_of_dicts_does_not_log_on_missing_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="yaya.kernel.payload"):
        assert payload_list_of_dicts({}, "k") == []
    assert all("payload_list_of_dicts" not in rec.message for rec in caplog.records)
