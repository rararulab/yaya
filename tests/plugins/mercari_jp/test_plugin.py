"""Tests for the Mercapi-backed Mercari Japan search plugin.

AC-bindings from ``specs/plugin-mercari_jp.spec``:

* Mercapi search response → ``test_search_returns_structured_candidates_from_mercapi_response``
* blocked Mercapi response → ``test_search_rejects_forbidden_mercapi_response_without_bypass``
* empty results → ``test_search_returns_empty_candidates_with_warning``
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.events import new_event
from yaya.kernel.plugin import KernelContext
from yaya.kernel.tool import ToolError, ToolOk, _clear_tool_registry, get_tool
from yaya.plugins.mercari_jp.plugin import MercariJpPlugin, MercariJpSearchTool
from yaya.plugins.mercari_jp.search import (
    MercariSearchRequest,
    build_mercapi_search_payload,
    build_mercari_search_url,
)


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    """Isolate the process-global tool registry for plugin lifecycle tests."""
    _clear_tool_registry()
    yield
    _clear_tool_registry()


MERCAPI_RESPONSE_WITH_ITEMS = {
    "meta": {
        "nextPageToken": "",
        "previousPageToken": "",
        "numFound": "2",
    },
    "items": [
        {
            "id": "m11111111111",
            "name": "Nintendo Switch 有機EL ホワイト 本体",
            "price": "28500",
            "status": "ITEM_STATUS_ON_SALE",
            "sellerId": "seller-1",
            "thumbnails": ["https://example.test/switch.jpg"],
            "itemConditionId": "3",
        },
        {
            "id": "m22222222222",
            "name": "Switch Lite ジャンク 部品取り",
            "price": "7000",
            "status": "ITEM_STATUS_ON_SALE",
            "sellerId": "seller-2",
            "thumbnails": [],
            "itemConditionId": "6",
        },
    ],
}


def _mercapi_client(
    status_code: int,
    body: dict[str, Any] | str,
    *,
    expected_max_price: int = 30_000,
) -> httpx.AsyncClient:
    """Build an AsyncClient that records one Mercapi-style request."""

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://api.mercari.jp/v2/entities:search"
        assert request.headers["X-Platform"] == "web"
        assert request.headers.get("DPoP")
        payload = json.loads(request.content.decode())
        condition = payload["searchCondition"]
        assert condition["keyword"] == "Nintendo Switch OLED"
        assert condition["priceMax"] == expected_max_price
        assert condition["status"] == ["STATUS_ON_SALE"]
        assert condition["sort"] == "SORT_SCORE"
        assert condition["order"] == "ORDER_DESC"
        if isinstance(body, str):
            return httpx.Response(status_code, text=body, request=request)
        return httpx.Response(status_code, json=body, request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(_handler))


def _raw_client(handler: httpx.MockTransport | httpx.AsyncBaseTransport) -> httpx.AsyncClient:
    """Build an AsyncClient with a custom transport."""
    return httpx.AsyncClient(transport=handler)


async def test_search_returns_structured_candidates_from_mercapi_response() -> None:
    """Mercapi API search results become normalized ranked candidates."""
    async with _mercapi_client(200, MERCAPI_RESPONSE_WITH_ITEMS) as client:
        result = await MercariJpSearchTool(
            keyword="Nintendo Switch OLED",
            max_price_jpy=30_000,
            must_have=["Switch"],
            must_not_have=["ジャンク"],
            limit=10,
        ).run_with_client(client)

    assert isinstance(result, ToolOk)
    data = result.display.data
    assert data["source"] == "mercapi_mercari"
    assert data["source_url"].startswith("https://jp.mercari.com/search?")
    assert data["items"][0]["title"] == "Nintendo Switch 有機EL ホワイト 本体"
    assert data["items"][0]["price_jpy"] == 28_500
    assert data["items"][0]["mercari_url"] == "https://jp.mercari.com/item/m11111111111"
    assert data["items"][0]["mercari_item_id"] == "m11111111111"
    assert data["items"][0]["condition"] == "目立った傷や汚れなし"
    assert data["items"][0]["availability"] == "available"
    assert data["items"][0]["score"] > data["items"][1]["score"]
    assert "within max price" in data["items"][0]["score_reasons"]


async def test_search_rejects_forbidden_mercapi_response_without_bypass() -> None:
    """HTTP 403 is surfaced as a rejected tool error with no workaround."""
    async with _mercapi_client(403, "Forbidden") as client:
        result = await MercariJpSearchTool(keyword="Nintendo Switch OLED", max_price_jpy=30_000).run_with_client(client)

    assert isinstance(result, ToolError)
    assert result.kind == "rejected"
    assert "refused" in result.display.text
    assert "bypass" in result.display.text


async def test_search_returns_empty_candidates_with_warning() -> None:
    """Empty Mercapi result pages stay successful and explain search drift."""
    response = {"meta": {"nextPageToken": "", "previousPageToken": "", "numFound": "0"}, "items": []}
    async with _mercapi_client(200, response) as client:
        result = await MercariJpSearchTool(keyword="Nintendo Switch OLED", max_price_jpy=30_000).run_with_client(client)

    assert isinstance(result, ToolOk)
    data = result.display.data
    assert data["items"] == []
    assert any("Mercari" in warning for warning in data["warnings"])
    assert any("keyword" in warning for warning in data["warnings"])


async def test_search_surfaces_http_errors_and_scores_price_mismatches() -> None:
    """Non-403 HTTP failures are internal errors and price drift lowers scores."""
    async with _mercapi_client(500, "server error") as client:
        failed = await MercariJpSearchTool(keyword="Nintendo Switch OLED", max_price_jpy=30_000).run_with_client(client)

    assert isinstance(failed, ToolError)
    assert failed.kind == "internal"
    assert "HTTP 500" in failed.display.text

    response = {
        "meta": {"nextPageToken": "", "previousPageToken": "", "numFound": "1"},
        "items": [
            {
                "id": "m33333333333",
                "name": "Fallback title",
                "price": "100",
                "status": "ITEM_STATUS_SOLD_OUT",
                "thumbnails": ["https://example.test/item.jpg"],
                "itemConditionId": "1",
            }
        ],
    }
    async with _mercapi_client(200, response, expected_max_price=50) as client:
        result = await MercariJpSearchTool(
            keyword="Nintendo Switch OLED",
            min_price_jpy=1_000,
            max_price_jpy=50,
            limit=5,
        ).run_with_client(client)

    assert isinstance(result, ToolOk)
    data = result.display.data
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["title"] == "Fallback title"
    assert item["image_url"] == "https://example.test/item.jpg"
    assert item["availability"] == "sold"
    assert "below min price" in item["score_reasons"]
    assert "above max price" in item["score_reasons"]


async def test_search_rejects_antibot_and_surfaces_malformed_responses() -> None:
    """Blocked and malformed upstream responses become explicit tool errors."""
    async with _mercapi_client(200, "captcha robot check") as client:
        rejected = await MercariJpSearchTool(keyword="Nintendo Switch OLED", max_price_jpy=30_000).run_with_client(
            client
        )

    assert isinstance(rejected, ToolError)
    assert rejected.kind == "rejected"

    async with _mercapi_client(200, "not json") as client:
        malformed = await MercariJpSearchTool(keyword="Nintendo Switch OLED", max_price_jpy=30_000).run_with_client(
            client
        )

    assert isinstance(malformed, ToolError)
    assert malformed.kind == "internal"
    assert "malformed JSON" in malformed.display.text

    async with _mercapi_client(200, {"items": {}}) as client:
        wrong_shape = await MercariJpSearchTool(keyword="Nintendo Switch OLED", max_price_jpy=30_000).run_with_client(
            client
        )

    assert isinstance(wrong_shape, ToolError)
    assert wrong_shape.kind == "internal"
    assert "items list" in wrong_shape.display.text

    def _list_body(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], request=request)

    async with _raw_client(httpx.MockTransport(_list_body)) as client:
        non_object = await MercariJpSearchTool(keyword="Nintendo Switch OLED").run_with_client(client)

    assert isinstance(non_object, ToolError)
    assert non_object.kind == "internal"
    assert "non-object" in non_object.display.text

    def _raise_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down", request=request)

    async with _raw_client(httpx.MockTransport(_raise_transport)) as client:
        failed_request = await MercariJpSearchTool(keyword="Nintendo Switch OLED").run_with_client(client)

    assert isinstance(failed_request, ToolError)
    assert failed_request.kind == "internal"
    assert "request failed" in failed_request.display.text


async def test_search_skips_malformed_items_and_keeps_unknown_values() -> None:
    """Malformed Mercapi items are skipped while unknown optional fields stay nullable."""
    response = {
        "meta": {"nextPageToken": "", "previousPageToken": "", "numFound": "4"},
        "items": [
            "not an item",
            {"id": "m44444444444", "name": "No price"},
            {"id": "m55555555555", "name": "Bad price", "price": "oops"},
            {
                "id": "m66666666666",
                "name": "Loose Nintendo Switch console",
                "price": "35000",
                "status": "paused",
                "thumbnails": [None, ""],
                "itemConditionId": "99",
            },
        ],
    }
    async with _mercapi_client(200, response) as client:
        result = await MercariJpSearchTool(
            keyword="Nintendo Switch OLED",
            max_price_jpy=30_000,
            must_have=["Switch"],
            must_not_have=["Lite"],
        ).run_with_client(client)

    assert isinstance(result, ToolOk)
    data = result.display.data
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["condition"] is None
    assert item["image_url"] is None
    assert item["availability"] == "unknown"
    assert "above max price" in item["score_reasons"]
    assert any("Skipped 3 malformed" in warning for warning in data["warnings"])


def test_mercari_search_url_and_request_filters() -> None:
    """Public URL construction carries filters while the client uses Mercapi request semantics."""
    newest = build_mercari_search_url(
        MercariSearchRequest(
            keyword="ignored",
            japanese_keywords=["  ポケモンカード  "],
            min_price_jpy=500,
            max_price_jpy=5_000,
            sort="newest",
        )
    )
    price_asc = build_mercari_search_url(MercariSearchRequest(keyword="Switch", status="all", sort="price_asc"))
    price_desc = build_mercari_search_url(MercariSearchRequest(keyword="Switch", sort="price_desc"))

    assert "keyword=%E3%83%9D%E3%82%B1%E3%83%A2%E3%83%B3%E3%82%AB%E3%83%BC%E3%83%89" in newest
    assert "price_min=500" in newest
    assert "price_max=5000" in newest
    assert "sort=created_time" in newest
    assert "order=desc" in newest
    assert "status=" not in price_asc
    assert "sort=price" in price_asc
    assert "order=asc" in price_asc
    assert "order=desc" in price_desc

    newest_payload = build_mercapi_search_payload(MercariSearchRequest(keyword="Switch", sort="newest"))
    sold_payload = build_mercapi_search_payload(MercariSearchRequest(keyword="Switch", status="sold_out"))
    all_payload = build_mercapi_search_payload(MercariSearchRequest(keyword="Switch", status="all", sort="price_asc"))
    desc_payload = build_mercapi_search_payload(MercariSearchRequest(keyword="Switch", sort="price_desc"))

    assert newest_payload["searchCondition"]["sort"] == "SORT_CREATED_TIME"
    assert newest_payload["searchCondition"]["order"] == "ORDER_DESC"
    assert sold_payload["searchCondition"]["status"] == ["STATUS_SOLD_OUT"]
    assert all_payload["searchCondition"]["status"] == []
    assert all_payload["searchCondition"]["sort"] == "SORT_PRICE"
    assert all_payload["searchCondition"]["order"] == "ORDER_ASC"
    assert desc_payload["searchCondition"]["sort"] == "SORT_PRICE"
    assert desc_payload["searchCondition"]["order"] == "ORDER_DESC"


def test_filter_fields_land_on_mercapi_payload() -> None:
    """#191 — category / brand / condition / shipping_payer map to Mercari's schema.

    Mercari takes string IDs in its JSON lists; condition and shipping_payer
    surface as single-item lists even though the tool accepts a single bucket
    so downstream widening to multi-select is a no-op.
    """
    payload = build_mercapi_search_payload(
        MercariSearchRequest(
            keyword="iPhone 15",
            category_ids=[7, 1346],
            brand_ids=[9999],
            item_condition="new",
            shipping_payer="seller",
        )
    )
    cond = payload["searchCondition"]
    assert cond["categoryId"] == ["7", "1346"]
    assert cond["brandId"] == ["9999"]
    assert cond["itemConditionId"] == ["1"]
    assert cond["shippingPayerId"] == ["2"]


def test_filter_defaults_preserve_legacy_payload_shape() -> None:
    """Unset filters must round-trip empty lists, same as before #191."""
    payload = build_mercapi_search_payload(MercariSearchRequest(keyword="iPhone"))
    cond = payload["searchCondition"]
    assert cond["categoryId"] == []
    assert cond["brandId"] == []
    assert cond["itemConditionId"] == []
    assert cond["shippingPayerId"] == []


def test_item_condition_buckets_cover_1_through_6() -> None:
    """Every token in the public surface maps to a unique 1..6 Mercari id."""
    mapping: dict[str, list[str]] = {}
    for token in ("new", "like_new", "no_scratches", "small_scratches", "scratches", "poor"):
        payload = build_mercapi_search_payload(
            MercariSearchRequest(keyword="x", item_condition=token)  # pyright: ignore[reportArgumentType]
        )
        mapping[token] = payload["searchCondition"]["itemConditionId"]
    assert mapping["new"] == ["1"]
    assert mapping["like_new"] == ["2"]
    assert mapping["no_scratches"] == ["3"]
    assert mapping["small_scratches"] == ["4"]
    assert mapping["scratches"] == ["5"]
    assert mapping["poor"] == ["6"]


def test_shipping_payer_tokens_map_to_mercari_ids() -> None:
    seller = build_mercapi_search_payload(MercariSearchRequest(keyword="x", shipping_payer="seller"))
    buyer = build_mercapi_search_payload(MercariSearchRequest(keyword="x", shipping_payer="buyer"))
    assert seller["searchCondition"]["shippingPayerId"] == ["2"]
    assert buyer["searchCondition"]["shippingPayerId"] == ["1"]


async def test_plugin_registers_unregisters_and_reports_health(tmp_path: Path) -> None:
    """Plugin lifecycle owns the mercari_jp_search v1 tool registration."""
    plugin = MercariJpPlugin()
    ctx = KernelContext(
        bus=EventBus(),
        logger=logging.getLogger("plugin.mercari-jp"),
        config={},
        state_dir=tmp_path,
        plugin_name=plugin.name,
    )

    assert plugin.subscriptions() == []
    await plugin.on_load(ctx)
    assert get_tool("mercari_jp_search") is MercariJpSearchTool
    await plugin.on_event(
        new_event("tool.call.request", {}, session_id="s", source="kernel"),
        ctx,
    )
    health = await plugin.health_check(ctx)
    assert health.status == "ok"
    await plugin.on_unload(ctx)
    assert get_tool("mercari_jp_search") is None
