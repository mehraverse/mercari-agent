## Mercari Japan AI Shopper Agent

### Overview

- Command-line Python agent that helps users find items on **mercari.jp**.
- Uses OpenAI Chat Completions with function/tool calling to interpret user requests and decide when to query Mercari.
- For each user query, an “analyst” LLM turn generates 3–4 structured Mercari search candidates and invokes the `get_recommendations` tool.
- A recommendation backend (`mercari_agent.recommender`) runs those candidates via `mercapi` in parallel, pools and deduplicates the results, enriches top hits with full item details, and ranks them by relevance, price fit, seller quality, and shipping before handing the best items to the “presentation” LLM.

### Example

![Example CLI interaction](working-example.png)

### Setup Instructions

1. **Prerequisites**

   - Python 3.10 or later
   - `pip`
   - An OpenAI API key

2. **Create and activate a virtual environment (from the project root)**

   - macOS / Linux:
     - `python3 -m venv .venv`
     - `source .venv/bin/activate`
   - Windows (PowerShell):
     - `python -m venv .venv`
     - `.\\.venv\\Scripts\\Activate.ps1`

3. **Install dependencies**

   - `pip install --upgrade pip`
   - `pip install -r requirements.txt`

4. **Configure OpenAI credentials**
   - macOS / Linux:
     - `export OPENAI_API_KEY=your_key_here`
   - Windows (PowerShell):
     - `$Env:OPENAI_API_KEY='your_key_here'`

### Usage Instructions

- From the project root, inside the activated virtual environment, run:
  - `python main.py`
- You should see:
  - `Mercari assistant is ready. Type 'exit' or 'quit' to stop.`
- Then type natural-language requests, for example:
  - `Find a used Nintendo Switch under 25000 JPY where the seller pays shipping.`
- The agent will:
  - Use the `get_recommendations` tool to call the backend with several candidate keyword queries.
  - Search Mercari via `mercapi`, deduplicate and rank items, and return the top 3 options with reasons.
- End the session with `exit` or `quit`.

### Design Choices

- **Two-step LLM flow (analyst → presenter)**

  - The first “analyst” call (prompt `SYSTEM_ANALYST_PROMPT`) runs on `gpt-4.1-mini` with a typed `get_recommendations` tool. It turns free-form user intent into 3–4 candidate search configurations (keywords, optional min/max price, shipping preference, location, brand) and decides whether to call the tool at all.
  - The second “presentation” call (prompt `SYSTEM_PRESENTATION_PROMPT`) receives only the ranked items plus conversation context and focuses on explanation and formatting (top-3 list, reasons, and alternative suggestions when results are sparse). I deliberately keep ranking logic in Python instead of prompts so retrieval quality is testable and adjustable without re-tuning the LLM.

- **Multi-candidate search pooling backend**

  - `RecommendationService.recommend` runs all candidate searches in parallel via `mercapi`, then **pools** and deduplicates results across candidates before scoring. This pooling step explicitly trades a single brittle query for several slightly different ones, which makes the system more robust to wording and tokenization differences in Mercari’s search.
  - A shared relevance token set is built from both the user query and all candidate keywords so that ranking is driven by the overall intent, not just one particular phrasing.
  - This design explicitly favors recall and robustness (several slightly different searches pooled together) over relying on a single “perfect” hero query from the LLM, which matches how real users phrase vague or mixed EN/JA requests.

- **Ranking strategy and heuristics**

  - Shallow items are created as `ProductShallow` objects, then filtered by item type (`ITEM_TYPE_MERCARI` only), a configurable max budget, and a minimum seller rating. For high budgets, a minimum “reasonable” price floor is enforced to avoid obviously unrelated, ultra-cheap items.
  - Relevance is computed with a simple tokenizer (`mercari_agent.utils.tokenize`) and a token-hit ratio between query tokens and item names. A shallow score combines relevance, seller rating, and a price-proximity score (targeting ~70% of the max budget).
  - Top shallow candidates are then enriched using `raw_item.full_item()` concurrently, merged into `ProductFull`, and finally deep-ranked before returning the best few to the LLM.

- **Mercari integration details**

  - `MercapiClient` wraps `mercapi.Mercapi` and maps `SearchRequest` to Mercari search parameters. It:
    - Appends free-text location to the query string when provided.
    - Maps `shipping_preference` to Mercari’s `shipping_payer` codes.
    - Restricts to items with `STATUS_ON_SALE` so only active listings are considered.
  - `serialize_product` (in `mercari_agent.utils`) converts `ProductFull` into JSON and ensures that a public Mercari URL is always constructed from the item ID when possible.

- **State and resource usage**
  - `MercariChatAgent` keeps a short, durable history of user messages and final assistant replies, deliberately leaving out intermediate tool and analyst messages to keep context small while still supporting follow-up questions. The history is trimmed to the last `MAX_TURNS` user/assistant pairs (see `mercari_agent/config.py`) to keep context length and token usage under control.
  - A single `MercapiClient` instance is reused per agent to avoid repeated client initialization.
  - The backend is fully async and uses `asyncio.gather` both for running multiple candidate searches and for enriching item details.

### Data Flow Example

User Input  
↓  
`I want to buy a smart Sony TV under 30,000 yen. I don't want to pay for shipping.`

[ANALYST LLM] (decides what to search for)  
└─ Emits `candidates` for the `get_recommendations` tool, for example:

- `{"keywords": "smart Sony TV", "max_price_jpy": 30000, "shipping_preference": "seller_pays"}`
- `{"keywords": "Sony スマートテレビ", "max_price_jpy": 30000, "shipping_preference": "seller_pays"}`
- `{"keywords": "ソニー スマートテレビ", "max_price_jpy": 30000, "shipping_preference": "seller_pays"}`
- `{"keywords": "Sony ブラビア スマートテレビ", "max_price_jpy": 30000, "shipping_preference": "seller_pays"}`

↓  
[RECOMMENDATION BACKEND – 4 STAGES]

Stage 1: SHALLOW SEARCH (multi-candidate)  
└─ Convert each candidate into a `SearchRequest` and call Mercari via `mercapi` in parallel.  
└─ Mercari returns raw rows like: `{"id_": "m55501133788", "name": "スマートテレビ SONY 24型", "price": 15500, ...}`.

Stage 2: POOL, FILTER & SHALLOW SCORE  
└─ Pool and deduplicate all raw items across candidates by item ID.  
└─ Filter by item type, max budget, and minimum seller rating.  
└─ Apply shallow scoring: `relevance * 4.0 + rating * 1.2 + price_score * 1.3`.  
└─ Keep the top `max_candidates` (e.g. 60) as enrichment candidates.

Stage 3: ENRICH (async batch fetch)  
└─ Fetch full details for the shallow candidates concurrently via `raw_item.full_item()`.  
└─ For each successfully enriched item, merge fields into `ProductFull` (seller_rating, seller_sales_count, condition_label, shipping details, etc.).

Stage 4: DEEP SCORE & FINAL RANK  
└─ Compute deep score as `shallow_score + shipping_bonus` (bonus when the seller pays shipping).  
└─ Re-sort by deep score and keep the top `max_return` items (e.g. 10).

↓  
[PRESENTER LLM]  
└─ Receives the ranked list as JSON (plus conversation history), selects the best 3 items, and explains why they fit the user’s constraints (price vs budget, brand, condition, seller trust, shipping) in a concise reply.

### External Libraries

- `openai`

  - Official OpenAI Python SDK.
  - Used to call the Chat Completions API for both the analyst and presentation model steps, including tool calling.

- `mercapi`
  - Python client library for the Mercari Japan API (no HTML scraping).
  - `MercapiClient` uses it to:
    - Search items by keywords, price range, shipping payer, and “on sale” status, based on the `SearchRequest` fields coming from the LLM.
    - Load full item details (seller rating, sales count, condition, shipping info, category) for ranking via `raw_item.full_item()`.
  - `mercapi` handles the low-level HTTP requests and Mercari-specific request/response formats, so the recommendation code can work with normal Python objects.
  - This keeps Mercari access in one place (`mercari_agent.recommender`) and lets the rest of the agent focus on ranking logic and LLM prompts instead of dealing with web scraping or custom API calls.

### Potential Improvements

- Map additional user constraints (brand, category, condition) to the corresponding Mercari filters when supported by `mercapi`.
- Expose configuration for model selection and provider choice (e.g. switch between OpenAI and Anthropic via environment variables).
- Add caching and rate limiting around `mercapi` calls, as well as more granular error handling and user-facing messages when Mercari is slow or returns no results.
- Add unit tests for `RecommendationService` (filtering, scoring, tokenization) and integration tests that mock both the LLM and `mercapi`.
