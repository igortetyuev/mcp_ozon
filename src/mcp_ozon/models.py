"""Dataclass models returned by tools; serialized to JSON with empty fields dropped."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any


@dataclass
class ProductTile:
    sku: str
    title: str
    url: str
    price: int | None = None
    card_price: int | None = None
    original_price: int | None = None
    discount_pct: int | None = None
    rating: float | None = None
    reviews: int | None = None
    seller: str | None = None
    delivery_label: str | None = None
    delivery_date: str | None = None
    express: bool | None = None
    in_stock: bool | None = None


@dataclass
class DeliveryVariant:
    available: bool
    eta_text: str | None = None
    date: str | None = None
    eta_at: str | None = None
    price: int | None = None


@dataclass
class DeliveryOptions:
    sku: str
    express: DeliveryVariant | None = None
    courier_earliest: DeliveryVariant | None = None
    pickup: DeliveryVariant | None = None
    address: str | None = None


@dataclass
class Order:
    number: str
    date: str | None = None
    status: str | None = None
    status_group: str | None = None
    total: int | None = None
    items: list[ProductTile] = field(default_factory=list)


@dataclass
class CartItem:
    sku: str
    title: str
    quantity: int
    price: int | None = None
    card_price: int | None = None
    original_price: int | None = None
    url: str | None = None
    available: bool = True


@dataclass
class FacetValue:
    value: str
    title: str
    count: int | None = None


@dataclass
class Facet:
    key: str
    title: str
    values: list[FacetValue] = field(default_factory=list)


def _strip(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {k: v for k, v in _strip(asdict(value)).items() if v not in (None, [], {})}
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if _strip(v) not in (None, [], {})}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def to_jsonable(obj: Any) -> Any:
    """Serialize dataclass instances/lists to plain JSON-ready structures, dropping empties."""
    return _strip(obj)
