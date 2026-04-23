"""Mercapi-compatible Mercari Japan search helpers."""

from __future__ import annotations

import base64
import json
import re
import time
import uuid
from typing import Any, Literal, TypeGuard, cast

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from pydantic import BaseModel, Field

MERCARI_SEARCH_ENDPOINT = "https://api.mercari.jp/v2/entities:search"
MERCARI_WEB_BASE_URL = "https://jp.mercari.com"
MERCAPI_SOURCE: Literal["mercapi_mercari"] = "mercapi_mercari"
DEFAULT_TIMEOUT_S = 10.0

_SPACE_RE = re.compile(r"\s+")
_REJECTION_MARKERS = (
    "access denied",
    "forbidden",
    "captcha",
    "robot",
    "anti-bot",
)
_CONDITION_LABELS = {
    1: "新品、未使用",
    2: "未使用に近い",
    3: "目立った傷や汚れなし",
    4: "やや傷や汚れあり",
    5: "傷や汚れあり",
    6: "全体的に状態が悪い",
}


class MercariSearchError(Exception):
    """Base error for Mercapi-backed Mercari search failures."""


class MercapiRejectedError(MercariSearchError):
    """Raised when Mercari refuses the request or serves an anti-bot response."""


class MercariSearchRequest(BaseModel):
    """Structured search request for Mercari JP search.

    Attributes:
        keyword: Primary user-visible search term.
        japanese_keywords: Additional Japanese search terms the LLM derived.
        must_have: Terms that should increase ranking when present.
        must_not_have: Terms that should reduce ranking when present.
        min_price_jpy: Optional lower price bound in JPY.
        max_price_jpy: Optional upper price bound in JPY.
        status: Desired sale status.
        sort: Desired sort mode.
        limit: Maximum normalized candidates returned to the caller.
    """

    keyword: str = Field(min_length=1)
    japanese_keywords: list[str] = Field(default_factory=list)
    must_have: list[str] = Field(default_factory=list)
    must_not_have: list[str] = Field(default_factory=list)
    min_price_jpy: int | None = Field(default=None, ge=0)
    max_price_jpy: int | None = Field(default=None, ge=0)
    status: Literal["on_sale", "sold_out", "all"] = "on_sale"
    sort: Literal["recommended", "newest", "price_asc", "price_desc"] = "recommended"
    limit: int = Field(default=20, ge=1, le=50)

    @property
    def query_term(self) -> str:
        """Return the search term sent to Mercari."""
        for term in [*self.japanese_keywords, self.keyword]:
            cleaned = term.strip()
            if cleaned:
                return cleaned
        return self.keyword.strip()


class MercariCandidate(BaseModel):
    """One normalized product candidate from Mercapi search results."""

    title: str
    price_jpy: int
    condition: str | None = None
    availability: Literal["available", "sold", "unknown"] = "unknown"
    mercari_url: str
    mercari_item_id: str
    image_url: str | None = None
    seller_rating: float | None = None
    score: float
    score_reasons: list[str]


class MercariSearchResult(BaseModel):
    """Structured result returned by the Mercari Japan search tool."""

    source: Literal["mercapi_mercari"] = MERCAPI_SOURCE
    query_used: str
    source_url: str
    items: list[MercariCandidate]
    warnings: list[str]


class MercapiSigner:
    """Generate DPoP headers using the mechanism used by take-kun/mercapi."""

    def __init__(self) -> None:
        """Create one per-runtime keypair and UUID for signed Mercari requests."""
        self._uuid = str(uuid.UUID(int=uuid.uuid4().int))
        self._key = ec.generate_private_key(ec.SECP256R1())

    def dpop(self, *, url: str, method: str) -> str:
        """Return a compact ES256 DPoP JWT for one request."""
        public_numbers = self._key.public_key().public_numbers()
        header = {
            "typ": "dpop+jwt",
            "alg": "ES256",
            "jwk": {
                "crv": "P-256",
                "kty": "EC",
                "x": _b64url(public_numbers.x.to_bytes(32, "big")),
                "y": _b64url(public_numbers.y.to_bytes(32, "big")),
            },
        }
        payload = {
            "iat": int(time.time()),
            "jti": str(uuid.uuid4()),
            "htu": url,
            "htm": method,
            "uuid": self._uuid,
        }
        signing_input = ".".join([
            _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ])
        signature_der = self._key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        r_value, s_value = utils.decode_dss_signature(signature_der)
        signature = r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big")
        return f"{signing_input}.{_b64url(signature)}"


def build_mercari_search_url(request: MercariSearchRequest) -> str:
    """Build the public Mercari search URL corresponding to a request.

    Args:
        request: Search request derived from the user's shopping intent.

    Returns:
        Absolute browser-facing Mercari search URL.
    """
    params: dict[str, str] = {"keyword": request.query_term}
    if request.status != "all":
        params["status"] = request.status
    if request.min_price_jpy is not None:
        params["price_min"] = str(request.min_price_jpy)
    if request.max_price_jpy is not None:
        params["price_max"] = str(request.max_price_jpy)
    if request.sort == "newest":
        params["sort"] = "created_time"
        params["order"] = "desc"
    elif request.sort == "price_asc":
        params["sort"] = "price"
        params["order"] = "asc"
    elif request.sort == "price_desc":
        params["sort"] = "price"
        params["order"] = "desc"
    return str(httpx.URL(f"{MERCARI_WEB_BASE_URL}/search", params=params))


def build_mercapi_search_payload(request: MercariSearchRequest) -> dict[str, Any]:
    """Build the JSON search payload used by take-kun/mercapi."""
    sort_by, sort_order = _mercapi_sort(request.sort)
    return {
        "userId": "",
        "pageSize": 120,
        "pageToken": "",
        "searchSessionId": uuid.uuid4().hex,
        "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
        "thumbnailTypes": [],
        "searchCondition": {
            "keyword": request.query_term,
            "sort": sort_by,
            "order": sort_order,
            "status": _mercapi_status(request.status),
            "sizeId": [],
            "categoryId": [],
            "brandId": [],
            "sellerId": [],
            "priceMin": request.min_price_jpy or 0,
            "priceMax": request.max_price_jpy or 0,
            "itemConditionId": [],
            "shippingPayerId": [],
            "shippingFromArea": [],
            "shippingMethod": [],
            "colorId": [],
            "hasCoupon": False,
            "attributes": [],
            "itemTypes": [],
            "skuIds": [],
            "excludeKeyword": " ".join(term for term in request.must_not_have if term.strip()),
        },
        "defaultDatasets": [],
        "serviceFrom": "suruga",
    }


async def search_mercapi_mercari(
    request: MercariSearchRequest,
    client: httpx.AsyncClient,
    *,
    signer: MercapiSigner | None = None,
) -> MercariSearchResult:
    """Fetch and normalize Mercari JP results via a Mercapi-compatible request.

    Args:
        request: Structured search request.
        client: HTTP client supplied by the caller. Tests pass a
            `MockTransport`; production tool calls pass a short-lived
            client with a timeout.
        signer: Optional DPoP signer. Tests normally use the default
            signer and only assert that a header is present.

    Returns:
        Structured result with normalized candidates and warnings.

    Raises:
        MercapiRejectedError: Mercari returned HTTP 401/403 or an anti-bot body.
        MercariSearchError: The HTTP request failed, returned a non-success
            status other than 401/403, or returned malformed JSON.
    """
    payload = build_mercapi_search_payload(request)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.3"
        ),
        "X-Platform": "web",
        "DPoP": (signer or MercapiSigner()).dpop(url=MERCARI_SEARCH_ENDPOINT, method="POST"),
    }
    try:
        response = await client.post(MERCARI_SEARCH_ENDPOINT, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise MercariSearchError(f"Mercapi Mercari search request failed: {exc}") from exc
    if response.status_code in {401, 403}:
        raise MercapiRejectedError(f"Mercari refused the Mercapi search request with HTTP {response.status_code}.")
    if response.status_code >= 400:
        raise MercariSearchError(f"Mercapi Mercari search returned HTTP {response.status_code}.")
    body_text = response.text
    if any(marker in body_text.lower() for marker in _REJECTION_MARKERS):
        raise MercapiRejectedError("Mercari served an anti-bot or forbidden response.")
    try:
        body_unknown: Any = response.json()
    except ValueError as exc:
        raise MercariSearchError("Mercapi Mercari search returned malformed JSON.") from exc
    if not isinstance(body_unknown, dict):
        raise MercariSearchError("Mercapi Mercari search returned a non-object JSON body.")
    body = cast(dict[str, Any], body_unknown)
    return parse_mercapi_search_response(
        body,
        request=request,
        source_url=build_mercari_search_url(request),
    )


def parse_mercapi_search_response(
    body: dict[str, Any],
    *,
    request: MercariSearchRequest,
    source_url: str,
) -> MercariSearchResult:
    """Parse a Mercapi search response into normalized candidates."""
    raw_items_value: object = body.get("items", [])
    raw_items = _list_or_none(raw_items_value)
    if raw_items is None:
        raise MercariSearchError("Mercapi Mercari search response did not contain an items list.")

    warnings: list[str] = [
        "Seller rating is not available from Mercapi search results.",
        "Mercari search results can change quickly as listings sell or disappear.",
    ]
    candidates: list[MercariCandidate] = []
    skipped_malformed = 0

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            skipped_malformed += 1
            continue
        candidate = _candidate_from_item(cast(dict[str, Any], raw_item), request)
        if candidate is None:
            skipped_malformed += 1
            continue
        candidates.append(candidate)

    if skipped_malformed:
        warnings.append(f"Skipped {skipped_malformed} malformed Mercari search item(s).")
    if not candidates:
        warnings.append("No Mercari product candidates matched this search.")
        warnings.append("Try more specific Japanese keywords or a different price range.")

    candidates.sort(key=lambda item: (-item.score, item.price_jpy, item.title))
    return MercariSearchResult(
        query_used=request.query_term,
        source_url=source_url,
        items=candidates[: request.limit],
        warnings=warnings,
    )


def _candidate_from_item(raw_item: dict[str, Any], request: MercariSearchRequest) -> MercariCandidate | None:
    """Build one normalized candidate from a Mercapi search item."""
    item_id = _string_or_none(raw_item.get("id"))
    title = _clean_title(raw_item.get("name"))
    price = _int_or_none(raw_item.get("price"))
    if item_id is None or title is None or price is None:
        return None

    condition = _CONDITION_LABELS.get(_int_or_none(raw_item.get("itemConditionId")) or 0)
    image_url = _first_string(raw_item.get("thumbnails"))
    availability = _availability(raw_item.get("status"))
    score, score_reasons = _score_candidate(
        title=title,
        price_jpy=price,
        condition=condition,
        image_url=image_url,
        request=request,
    )
    return MercariCandidate(
        title=title,
        price_jpy=price,
        condition=condition,
        availability=availability,
        mercari_url=f"{MERCARI_WEB_BASE_URL}/item/{item_id}",
        mercari_item_id=item_id,
        image_url=image_url,
        seller_rating=None,
        score=score,
        score_reasons=score_reasons,
    )


def _mercapi_sort(sort: str) -> tuple[str, str]:
    """Map tool sort modes to Mercapi search enum names."""
    if sort == "newest":
        return "SORT_CREATED_TIME", "ORDER_DESC"
    if sort == "price_asc":
        return "SORT_PRICE", "ORDER_ASC"
    if sort == "price_desc":
        return "SORT_PRICE", "ORDER_DESC"
    return "SORT_SCORE", "ORDER_DESC"


def _mercapi_status(status: str) -> list[str]:
    """Map tool status modes to Mercapi search enum names."""
    if status == "on_sale":
        return ["STATUS_ON_SALE"]
    if status == "sold_out":
        return ["STATUS_SOLD_OUT"]
    return []


def _availability(status: object) -> Literal["available", "sold", "unknown"]:
    """Normalize Mercari status strings into user-facing availability."""
    if not isinstance(status, str):
        return "unknown"
    normalized = status.lower()
    if normalized in {"on_sale", "item_status_on_sale"}:
        return "available"
    if normalized in {"sold_out", "trading", "item_status_sold_out", "item_status_trading"}:
        return "sold"
    return "unknown"


def _score_candidate(
    *,
    title: str,
    price_jpy: int,
    condition: str | None,
    image_url: str | None,
    request: MercariSearchRequest,
) -> tuple[float, list[str]]:
    """Return a deterministic ranking score and explanation."""
    score = 0.4
    reasons: list[str] = ["Mercapi Mercari search candidate"]
    title_lower = title.lower()
    score = _apply_term_scores(score, reasons, title_lower, request)
    score = _apply_price_score(score, reasons, price_jpy, request)
    score = _apply_quality_score(score, reasons, condition, image_url)
    clamped = min(1.0, max(0.0, score))
    return round(clamped, 3), reasons


def _apply_term_scores(
    score: float,
    reasons: list[str],
    title_lower: str,
    request: MercariSearchRequest,
) -> float:
    """Apply must-have and must-not-have title matching scores."""
    for term in request.must_have:
        cleaned = term.strip().lower()
        if cleaned and cleaned in title_lower:
            score += 0.15
            reasons.append(f"matches required term: {term}")

    for term in request.must_not_have:
        cleaned = term.strip().lower()
        if cleaned and cleaned in title_lower:
            score -= 0.4
            reasons.append(f"contains excluded term: {term}")
    return score


def _apply_price_score(
    score: float,
    reasons: list[str],
    price_jpy: int,
    request: MercariSearchRequest,
) -> float:
    """Apply configured price-bound scoring."""
    if request.min_price_jpy is not None:
        if price_jpy >= request.min_price_jpy:
            score += 0.05
            reasons.append("within min price")
        else:
            score -= 0.2
            reasons.append("below min price")
    if request.max_price_jpy is not None:
        if price_jpy <= request.max_price_jpy:
            score += 0.2
            reasons.append("within max price")
        else:
            score -= 0.25
            reasons.append("above max price")
    return score


def _apply_quality_score(
    score: float,
    reasons: list[str],
    condition: str | None,
    image_url: str | None,
) -> float:
    """Apply visible condition and image scoring."""
    if condition in {"新品、未使用", "未使用に近い", "目立った傷や汚れなし"}:
        score += 0.1
        reasons.append("favorable listed condition")
    if image_url is not None:
        score += 0.05
        reasons.append("has product image")
    return score


def _clean_title(value: object) -> str | None:
    """Return a whitespace-normalized title from one raw value."""
    if not isinstance(value, str):
        return None
    title = _SPACE_RE.sub(" ", value).strip()
    return title or None


def _string_or_none(value: object) -> str | None:
    """Return non-empty strings only."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _first_string(value: object) -> str | None:
    """Return the first non-empty string in a list-like value."""
    items = _list_or_none(value)
    if items is None:
        return None
    for item in items:
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


def _list_or_none(value: object) -> list[Any] | None:
    """Return list values with explicit element type for strict checkers."""
    if not _is_list(value):
        return None
    return value


def _is_list(value: object) -> TypeGuard[list[Any]]:
    """Narrow unknown JSON values to list[Any]."""
    return isinstance(value, list)


def _int_or_none(value: object) -> int | None:
    """Parse integer-like Mercapi values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _b64url(data: bytes) -> str:
    """Return unpadded base64url text."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")
