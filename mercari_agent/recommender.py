from __future__ import annotations

"""Mercari Japan recommendation tool.

Provides:
- MercapiClient: Async wrapper for mercapi library
- RecommendationService: Two-stage ranking (shallow → deep)
- Token synonyms and core token matching"""

import asyncio
from typing import Any, Iterable, List, Optional

from .config import MAX_SHALLOW, MAX_CANDIDATES, MAX_RETURN, MIN_SELLER_RATING
from .models import ProductShallow, ProductFull, SearchRequest
from .utils import tokenize

try:
    from mercapi import Mercapi  # type: ignore
    from mercapi.requests.search import SearchRequestData  # type: ignore
except ImportError:
    Mercapi = None  # type: ignore
    SearchRequestData = None  # type: ignore

class AbstractMercariClient:
    """Interface for Mercari clients."""
    async def search(self, request: SearchRequest, limit: int = 120) -> Iterable[Any]:
        #Return raw search results for a structured request
        raise NotImplementedError

    async def enrich_item(self, raw_item: Any) -> Any:
        #Return a fully-populated raw item via a detail endpoint
        raise NotImplementedError


class MercapiClient(AbstractMercariClient):
    """Async adapter for mercapi.Mercapi."""

    def __init__(self) -> None:
        if Mercapi is None or SearchRequestData is None:
            raise RuntimeError("mercapi is not installed")
        self._client = Mercapi()

    async def search(self, request: SearchRequest, limit: int = 120) -> Iterable[Any]:
        # Map SearchRequest to mercapi search params
        query = request.query_text
        #print("Search query:", query)
        if request.location:
            # Append location to the query
            query = f"{query} {request.location}"

        price_min = request.min_price
        price_max = request.max_price

        shipping_payer: List[int] = []
        if request.shipping_preference == "seller_pays":
            shipping_payer = [2]
        elif request.shipping_preference == "buyer_pays":
            shipping_payer = [1]

        results = await self._client.search(
            query,
            price_min=price_min,
            price_max=price_max,
            #item_conditions=item_conditions,
            shipping_payer=shipping_payer,
            # Only search for items that are currently on sale
            status=[SearchRequestData.Status.STATUS_ON_SALE],
        )
        return results.items[:limit]

    async def enrich_item(self, raw_item: Any) -> Any:
        try:
            return await raw_item.full_item()
        except Exception as e:
            #print(f"Failed to enrich item {getattr(raw_item, 'id_', 'unknown')}: {e}")
            return None


class RecommendationService:
    """Shallow search → prefilter → enrich → deep rank over Mercari items."""

    def __init__(
        self,
        client: AbstractMercariClient,
        max_shallow: int = MAX_SHALLOW,
        max_candidates: int = MAX_CANDIDATES,
        max_return: int = MAX_RETURN,
        min_seller_rating: float = MIN_SELLER_RATING,
        max_price_jpy: Optional[int] = None,
    ) -> None:
        self.client = client
        self.max_shallow = max_shallow
        self.max_candidates = max_candidates
        self.max_return = max_return
        self.min_seller_rating = min_seller_rating
        self.max_price_jpy = max_price_jpy
        self._current_tokens: List[str] = []
    
    async def recommend(
        self,
        search_requests: List[SearchRequest],
        user_query: str,
    ) -> List[ProductFull]:
        """Run multiple keyword searches and rank pooled results."""
        if not search_requests:
            return []

        # Run all searches in parallel.
        search_tasks = [
            self.client.search(req, limit=self.max_shallow)
            for req in search_requests
        ]
        per_request_results = await asyncio.gather(
            *search_tasks,
            return_exceptions=False,
        )

        # Pool and deduplicate raw items across all candidates.
        raw_items: List[Any] = []
        seen_ids: set[str] = set()
        for items in per_request_results:
            for item in items:
                item_id = getattr(item, "id_", None)
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                raw_items.append(item)

        # Build relevance tokens from the user query + all candidate query texts.
        combined_query_text = " ".join(req.query_text for req in search_requests)                
        self._current_tokens = tokenize(f"{user_query} {combined_query_text}")
        #print("Raw results: ", len(raw_items))

        # Parse shallow items, filter, and score.
        shallow_items = [self._parse_shallow(item) for item in raw_items]
        filtered = [p for p in shallow_items if self._should_include(p)]
        scored = sorted(filtered, key=self._shallow_score, reverse=True)
        candidates = scored[:self.max_candidates]
        #print("Candidate total: ", len(candidates))

        raw_by_id = {it.id_: it for it in raw_items}

        # Enrich top candidates concurrently.
        enriched_raw = await asyncio.gather(
            *[
                self.client.enrich_item(raw_by_id[p.id])
                for p in candidates
                if p.id in raw_by_id
            ],
            return_exceptions=False,
        )

        # Merge shallow + enriched data.
        enriched: List[ProductFull] = []
        for raw, shallow in zip(enriched_raw, candidates):
            if raw is None:
                continue  # skip items that failed to enrich
            enriched.append(self._merge_full(raw, shallow))

        # Final deep ranking
        ranked = sorted(enriched, key=self._deep_score, reverse=True)
        return ranked[:self.max_return]

    def _parse_shallow(self, raw_item: Any) -> ProductShallow:
        """Convert raw search result to ProductShallow."""
        return ProductShallow(
            id=raw_item.id_,
            name=raw_item.name,
            price_jpy=raw_item.price,
            item_type=raw_item.item_type,
            # Filled in later from full item details
            seller_rating=None,
            seller_sales_count=None,
            condition_label=None,
            # Construct the public URL later during serialization
            url=None,
            # SearchResultItem.created is a datetime parsed by mercapi.
            created_at=raw_item.created.isoformat() if raw_item.created else None,
        )

    # Basic filters
    def _should_include(self, p: ProductShallow) -> bool:
        """Check if product passes filters."""
        # Item type filter
        if p.item_type and p.item_type != "ITEM_TYPE_MERCARI":
            return False
        # Price filter
        if self.max_price_jpy and p.price_jpy > self.max_price_jpy:
            return False
        # Enforce a minimum reasonable price if max_price_jpy is set high.
        if self.max_price_jpy and self.max_price_jpy >= 10000:
            min_reasonable = max(500, int(self.max_price_jpy * 0.25))
            if p.price_jpy < min_reasonable:
                return False
        # Seller rating filter
        if (p.seller_rating or 0.0) < self.min_seller_rating:
            return False
        return True

    def _shallow_score(self, p: ProductShallow) -> float:
        """Score using price proximity, relevance, rating, and sales count."""
        relevance = self._relevance_score(p)
        rating = p.seller_rating or 0.0
        price_score = self._price_score(p)
        #sales_bonus = min((p.seller_sales_count or 0) / 1000.0, 1.0) * 0.5
        return relevance * 4.0 + rating * 1.2 + (price_score * 1.3) #+ sales_bonus

    def _relevance_score(self, p: ProductShallow) -> float:
        """Measure how well the item name matches the current token set."""
        if not self._current_tokens:
            return 0.0
        name = (p.name or "").lower()
        # Count token hits in the name
        hits = sum(1 for t in self._current_tokens if t and t in name)
        return hits / max(len(self._current_tokens), 1)
    
    def _price_score(self, p: ProductShallow) -> float:
        if not self.max_price_jpy:
            return 0.0
        # Ideal price is 70% of max_price_jpy
        target = max(1.0, self.max_price_jpy * 0.7)
        # Score decreases linearly as price deviates from target
        diff = abs(p.price_jpy - target)
        return max(0.0, 1.0 - diff / target)

    def _merge_full(self, raw: Any, shallow: ProductShallow) -> ProductFull:
        """Combine shallow and enriched data."""
        if raw.seller:
            shallow.seller_rating = float(raw.seller.star_rating_score)
            shallow.seller_sales_count = int(raw.seller.num_sell_items)
        
        if raw.item_condition:
            shallow.condition_label = raw.item_condition.name

        if raw.created:
            shallow.created_at = raw.created.isoformat()

        # Shipping details
        shipping_fee_included = False
        if raw.shipping_payer and raw.shipping_payer.code:
            shipping_fee_included = raw.shipping_payer.code == "seller"
    
        shipping_days_min: Optional[int] = None
        shipping_days_max: Optional[int] = None
        if raw.shipping_duration:
            shipping_days_min = raw.shipping_duration.min_days
            shipping_days_max = raw.shipping_duration.max_days

        # Category label from full item
        category: Optional[str] = None
        if raw.item_category:
            category = raw.item_category.name

        return ProductFull(
            **shallow.__dict__,
            description=raw.description,
            category=category,
            attributes=None,
            shipping_fee_included=shipping_fee_included,
            shipping_days_min=shipping_days_min,
            shipping_days_max=shipping_days_max,
        )

    def _deep_score(self, p: ProductFull) -> float:
        """Add shipping bonus to shallow score."""
        base = self._shallow_score(p)
        # Bonus for free shipping
        shipping_bonus = 0.5 if p.shipping_fee_included else 0.0
        return base + shipping_bonus
