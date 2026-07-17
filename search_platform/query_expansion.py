"""
Static acronym/synonym expansion. Deliberately not LLM-based: this runs on
every search request and needs to stay in the low-single-digit-millisecond
range, whereas the LLM budget is spent on explanations (see explain.py)
where it's user-visible value, not a latency tax on every keystroke.
"""
import re

EXPANSIONS: dict[str, list[str]] = {
    "rag": ["retrieval-augmented generation"],
    "nlp": ["natural language processing"],
    "llm": ["large language model"],
    "llms": ["large language models"],
    "cv": ["computer vision"],
    "ml": ["machine learning"],
    "dl": ["deep learning"],
    "ai": ["artificial intelligence"],
    "gnn": ["graph neural network"],
    "gan": ["generative adversarial network"],
    "rl": ["reinforcement learning"],
    "cnn": ["convolutional neural network"],
    "rnn": ["recurrent neural network"],
    "ir": ["information retrieval"],
    "genai": ["generative ai"],
    "hci": ["human-computer interaction"],
    "iot": ["internet of things"],
    "ner": ["named entity recognition"],
}
# Reverse map so full phrases also pull in the acronym.
for _acr, _phrases in list(EXPANSIONS.items()):
    for _phrase in _phrases:
        EXPANSIONS.setdefault(_phrase, [])
        if _acr not in EXPANSIONS[_phrase]:
            EXPANSIONS[_phrase].append(_acr)

TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9\-]*")


def expand_query(query: str) -> tuple[str, list[str]]:
    """
    Returns (expanded_query_for_retrieval, list_of_added_terms).
    The expanded query is used for BM25/vector recall; the original query
    is still shown to the user and used for highlighting.
    """
    query_lower = query.lower()
    added: list[str] = []

    for phrase, expansions in EXPANSIONS.items():
        if " " in phrase:
            if phrase in query_lower:
                for exp in expansions:
                    if exp not in query_lower and exp not in added:
                        added.append(exp)
        else:
            if re.search(rf"\b{re.escape(phrase)}\b", query_lower):
                for exp in expansions:
                    if exp not in query_lower and exp not in added:
                        added.append(exp)

    expanded_query = query if not added else f"{query} {' '.join(added)}"
    return expanded_query, added
