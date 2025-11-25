"""Mercari Japan shopping agent using OpenAI tool calling.

Two-step flow:
1. Analyst LLM decides when to search via get_recommendations tool
2. Presentation LLM formats top-3 results for user

Entry points: MercariChatAgent.chat()
"""

import json
from typing import Any, Dict, List, Optional

from openai import OpenAI
from .config import MODEL_NAME, MAX_TURNS
from .models import SearchRequest
from .recommender import AbstractMercariClient, MercapiClient, RecommendationService
from .utils import serialize_product, message_to_dict

_oai = OpenAI()

# System prompt used for the first ("analyst") LLM call.
SYSTEM_ANALYST_PROMPT = (
    "You are a Mercari Japan shopping assistant. "
    "Keep the conversation context in mind. "
    "Use the get_recommendations tool to fetch items when the user asks for products "
    "or clarifies their needs. Ask quick clarifying questions if the request is ambiguous. "
    "Generate 4 candidates for searching Mercari Japan based on the user's request as follows:\n"
    "- keywords: Extract concise search keywords in English and Japanese that capture the user's intended product. "
    "First candidate should be the user's actual text; subsequent candidates can use synonyms or related terms. "
    "- If you can infer a product type, set product_type; if the user specifies price, shipping preference, "
    "condition, location, or brand, copy those values into min_price_jpy, max_price_jpy, "
    "shipping_preference, condition, location, and brand for every candidate. "
    "Once you have tool results, defer to the follow-up step to present them."
)


# System prompt used for the second ("presentation") LLM call.
SYSTEM_PRESENTATION_PROMPT = (
    "You are a Mercari Japan shopping assistant. "
    "You already have a ranked list of products. "
    "Analyze the retrieved data and select the top 3 product options strictly that best meet the user's needs, "
    "providing clear and concise reasons for each recommendation in English. Consider factors such as price, seller rating, product condition, likes, and shipping details. "
    "If there are fewer than 3 items (especially 0):\n"
    "- Pick one closest alternative search keywords (in English), including useful synonyms\n"
    "- Ask the user if they want to try that\n\n"
    "Present the recommendations in this format:\n\n"
    "1. Product Name\n"
    "   - Price: XXX JPY\n"
    "   - URL: XXX\n"
    "   - Reason:\n\n"
)


tools = [
    {
        "type": "function",
        "function": {
            "name": "get_recommendations",
            "description": "Search Mercari Japan and return ranked products.",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidates": {
                        "type": "array",
                        "description": "3-4 alternative search candidates derived from the user request.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "keywords": {
                                    "type": "string",
                                    "description": "Gather the product, e.g. 'PS5 本体', 'テレビ 4K 55インチ'.",
                                },
                                "product_type": {
                                    "type": "string",
                                    "description": "Optional product type label, e.g. 'console', 'game', 'sofa', 'bed frame'.",
                                },
                                "category": {
                                    "type": "string",
                                    "description": "Optional category/subcategory description inferred from the query.",
                                },
                                "min_price_jpy": {
                                    "type": "integer",
                                    "description": "Optional lower bound on price in JPY.",
                                },
                                "max_price_jpy": {
                                    "type": "integer",
                                    "description": "Optional upper bound on price in JPY.",
                                },
                                "shipping_preference": {
                                    "type": "string",
                                    "description": "Optional shipping preference, e.g. 'seller_pays', 'buyer_pays', or 'any'.",
                                },
                                "location": {
                                    "type": "string",
                                    "description": "Optional preferred location or region in Japan.",
                                },
                                "brand": {
                                    "type": "string",
                                    "description": "Optional brand name filter.",
                                }
                            },
                            "required": ["keywords"],
                        },
                    },
                },
                "required": ["candidates"],
            },
        },
    }
]


class MercariChatAgent:
    """Mercari Two-step LLM pattern: analyst (tool calling) → presentation (formatting)."""

    def __init__(self, mercari_client: AbstractMercariClient | None = None) -> None:
        self.history: List[Dict[str, Any]] = []
        # Reuse a single Mercari client instance for all tool calls.
        self._mercari_client: AbstractMercariClient = mercari_client or MercapiClient()

    async def chat(self, user_query: str) -> str:
        """Handle one turn of conversation and return the assistant reply."""
        user_msg: Dict[str, str] = {"role": "user", "content": user_query}
        # Build working messages with history + current user query
        working_messages: List[Dict[str, Any]] = [*self.history, user_msg]

        try:
            # First step: analyst LLM to decide on tool calls or direct reply
            first = _oai.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "system", "content": SYSTEM_ANALYST_PROMPT}, *working_messages],
                tools=tools,
                tool_choice="auto",
            )
            first_msg = first.choices[0].message
            assistant_entry = message_to_dict(first_msg)

            tool_msgs: List[Dict[str, Any]] = []
            # If tool calls were made, execute them
            if first_msg.tool_calls:
                for tc in first_msg.tool_calls:
                    # Ignore unknown tool calls
                    if tc.function.name != "get_recommendations":
                        continue

                    try:
                        args: Dict[str, Any] = json.loads(tc.function.arguments)
                    except json.JSONDecodeError as e:
                        print(f"Warning: Failed to parse tool args: {e}, using fallback")
                        args = {"candidates": []}

                    # Build search requests directly from tool args and call the recommender.
                    candidates_payload = args.get("candidates") or []
                    # print(candidates_payload)
                    if not candidates_payload:
                        recs: List[Dict[str, Any]] = []
                    else:
                        # Compute a global max price for scoring/filtering from candidates.
                        max_values = [
                            c.get("max_price_jpy")
                            for c in candidates_payload
                            if c.get("max_price_jpy") is not None
                        ]
                        global_max_price: int | None = min(max_values) if max_values else None

                        # Initialize the recommendation service.
                        svc = RecommendationService(
                            client=self._mercari_client,
                            max_price_jpy=global_max_price,
                        )

                        search_requests: List[SearchRequest] = []

                        for cand in candidates_payload:
                            # Validate required fields
                            keywords = cand.get("keywords")
                            if not isinstance(keywords, str) or not keywords.strip():
                                continue
                            # Generate SearchRequest
                            search_requests.append(
                                SearchRequest(
                                    query_text=keywords.strip(),
                                    min_price=cand.get("min_price_jpy"),
                                    max_price=cand.get("max_price_jpy"),
                                    shipping_preference=cand.get("shipping_preference"),
                                    #condition=cand.get("condition"),
                                    location=cand.get("location"),
                                    brand=cand.get("brand"),
                                )
                            )

                        if not search_requests:
                            recs = []
                        else:
                            products = await svc.recommend(
                                search_requests=search_requests,
                                user_query=user_query,
                            )
                            print("Reasoning...")
                            recs = [serialize_product(p) for p in products]
                    # Append tool response message
                    tool_msgs.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": "get_recommendations",
                            "content": json.dumps(recs, ensure_ascii=False),
                        }
                    )

            # Presentation step: summarize and format top results.
            final_reply: Optional[str] = None
            if tool_msgs:
                summary_messages: List[Dict[str, Any]] = [
                    {"role": "system", "content": SYSTEM_PRESENTATION_PROMPT},
                    *working_messages,
                    assistant_entry,
                    *tool_msgs,
                ]
                # Call LLM to generate final reply
                second = _oai.chat.completions.create(
                    model=MODEL_NAME,
                    messages=summary_messages,
                )
                final_msg = second.choices[0].message
                final_reply = final_msg.content or ""
                final_entry = message_to_dict(final_msg)
                # Persist only the user request and the final natural-language reply.
                self.history.extend([user_msg, final_entry])
            else:
                final_reply = first_msg.content or ""
                # Persist the user request and the initial assistant reply.
                self.history.extend([user_msg, assistant_entry])
        # Catch-all for unexpected errors
        except Exception as e:
            print(f"Error in chat: {e}")
            final_reply = "Sorry, something went wrong. Please try again in a moment."
            self.history.extend(
                [
                    user_msg,
                    {"role": "assistant", "content": final_reply},
                ]
            )

        # Trim history to the most recent MAX_TURNS turns.
        self.history = self.history[-2 * MAX_TURNS :]

        return final_reply
