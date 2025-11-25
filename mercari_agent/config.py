MODEL_NAME = "gpt-4.1-mini"
MAX_TURNS = 6 # Maximum number of recent user/assistant turns to keep in context.

MAX_SHALLOW = 120 # Number of raw items to fetch from Mercari API
MAX_CANDIDATES = 60 # Number of filtered items to enrich and rank out of the raw items
MAX_RETURN = 10 # Number of products to feed LLM for final recommendation after ranking
MIN_SELLER_RATING = 0.0 # Minimum seller rating

