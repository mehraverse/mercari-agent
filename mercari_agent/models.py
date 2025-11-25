# Data models for interactions.
from dataclasses import dataclass
from typing import Optional


@dataclass
class SearchRequest:
    """High-level search request built from LLM/tool args.

    This is the front-facing search model; recommender maps it to mercapi.search.
    """

    query_text: str
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    shipping_preference: Optional[str] = None  # "seller_pays" | "buyer_pays" | "any" | None
    #condition: Optional[str] = None  # "new" | "like_new" | "good" | "any" | None
    location: Optional[str] = None  # free-text or normalized area
    brand: Optional[str] = None  # brand name filter


@dataclass
class ProductShallow:
    """Lightweight product from Mercari search."""
    id: str
    name: str
    price_jpy: int
    item_type: Optional[str] = None
    seller_rating: Optional[float] = None
    seller_sales_count: Optional[int] = None
    condition_label: Optional[str] = None
    url: Optional[str] = None
    created_at: Optional[str] = None  # ISO8601 string


@dataclass
class ProductFull(ProductShallow):
    """Full product details for ranking and presentation."""
    description: Optional[str] = None
    category: Optional[str] = None
    attributes: Optional[dict] = None
    shipping_fee_included: Optional[bool] = None
    shipping_days_min: Optional[int] = None
    shipping_days_max: Optional[int] = None
