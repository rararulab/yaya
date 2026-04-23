"""Mercapi-backed Mercari Japan tool plugin."""

from __future__ import annotations

from typing import ClassVar, Literal, override

import httpx
from pydantic import Field

from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, HealthReport, KernelContext
from yaya.kernel.tool import (
    JsonBlock,
    TextBlock,
    Tool,
    ToolError,
    ToolOk,
    ToolReturnValue,
    register_tool,
    unregister_tool,
)
from yaya.plugins.mercari_jp.search import (
    DEFAULT_TIMEOUT_S,
    MercapiRejectedError,
    MercariSearchError,
    MercariSearchRequest,
    search_mercapi_mercari,
)

_NAME = "mercari-jp"
_VERSION = "0.1.0"
_TOOL_NAME = "mercari_jp_search"


class MercariJpSearchTool(Tool):
    """Search Mercari JP listings and return ranked candidates."""

    name: ClassVar[str] = _TOOL_NAME
    description: ClassVar[str] = (
        "Search Mercari Japan listings for product candidates using a Mercapi-compatible request. "
        "Use for shopping recommendations; it does not buy, log in, or bypass blocked responses."
    )

    keyword: str = Field(description="Primary search term from the user's shopping request.", min_length=1)
    japanese_keywords: list[str] = Field(
        default_factory=list,
        description="Optional Japanese keywords to prefer for the Mercari search query.",
    )
    must_have: list[str] = Field(
        default_factory=list,
        description="Terms that should increase ranking when visible in a candidate title.",
    )
    must_not_have: list[str] = Field(
        default_factory=list,
        description="Terms that should reduce ranking when visible in a candidate title.",
    )
    min_price_jpy: int | None = Field(default=None, ge=0, description="Optional minimum price in JPY.")
    max_price_jpy: int | None = Field(default=None, ge=0, description="Optional maximum price in JPY.")
    status: Literal["on_sale", "sold_out", "all"] = Field(
        default="on_sale",
        description="Desired sale status.",
    )
    sort: Literal["recommended", "newest", "price_asc", "price_desc"] = Field(
        default="recommended",
        description="Sort mode.",
    )
    limit: int = Field(default=20, ge=1, le=50, description="Maximum candidates to return.")

    @override
    async def run(self, ctx: KernelContext) -> ToolReturnValue:
        """Execute the search using a short-lived HTTP client."""
        del ctx
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S, follow_redirects=True) as client:
            return await self.run_with_client(client)

    async def run_with_client(self, client: httpx.AsyncClient) -> ToolReturnValue:
        """Execute the search with a caller-supplied HTTP client.

        Args:
            client: HTTP client. Tests pass a mock transport; production
                calls use the client created by :meth:`run`.

        Returns:
            Existing yaya tool envelope containing structured JSON or a
            user-facing error.
        """
        try:
            result = await search_mercapi_mercari(self._request(), client)
        except MercapiRejectedError as exc:
            return ToolError(
                kind="rejected",
                brief="Mercari refused search",
                display=TextBlock(
                    text=(
                        f"{exc} The mercari_jp_search tool does not bypass "
                        "403 responses, CAPTCHA, anti-bot checks, login, or session rejection."
                    )
                ),
            )
        except MercariSearchError as exc:
            return ToolError(
                kind="internal",
                brief="Mercapi Mercari search failed",
                display=TextBlock(text=str(exc)),
            )

        data = result.model_dump(mode="json")
        return ToolOk(
            brief=f"found {len(result.items)} Mercari candidate(s)",
            display=JsonBlock(data=data),
        )

    def _request(self) -> MercariSearchRequest:
        """Convert validated tool parameters into a search request."""
        return MercariSearchRequest(
            keyword=self.keyword,
            japanese_keywords=self.japanese_keywords,
            must_have=self.must_have,
            must_not_have=self.must_not_have,
            min_price_jpy=self.min_price_jpy,
            max_price_jpy=self.max_price_jpy,
            status=self.status,
            sort=self.sort,
            limit=self.limit,
        )


class MercariJpPlugin:
    """Bundled tool plugin that registers `mercari_jp_search`."""

    name: str = _NAME
    version: str = _VERSION
    category: Category = Category.TOOL
    requires: ClassVar[list[str]] = []

    def subscriptions(self) -> list[str]:
        """The v1 dispatcher handles tool.call.request events."""
        return []

    async def on_load(self, ctx: KernelContext) -> None:
        """Register the Mercari Japan search tool."""
        del ctx
        register_tool(MercariJpSearchTool)

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """No-op because v1 tool dispatch owns requests."""
        del ev, ctx

    async def on_unload(self, ctx: KernelContext) -> None:
        """Unregister the Mercari Japan search tool."""
        del ctx
        unregister_tool(_TOOL_NAME)

    async def health_check(self, ctx: KernelContext) -> HealthReport:
        """Report plugin load health without touching the network."""
        del ctx
        return HealthReport(status="ok", summary="mercari_jp_search registered")


plugin = MercariJpPlugin()
