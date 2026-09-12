"""Distinctive-keyword extraction with TF-IDF.

The closed tag vocabulary in `lexicon.py` answers "which filter chips apply?".
This module answers a different question: "what is distinctive about *this*
place?" — and it needs a corpus-relative measure, because term frequency alone
is useless here. "tapas" appears in reviews of half the places in Madrid; it has
high frequency and near-zero discriminating power. "sherry", "churros",
"rowboat", "cocido" appear rarely and identify a place immediately.

That is exactly what IDF measures, hence scikit-learn's `TfidfVectorizer` rather
than a hand-rolled counter. Output tags are namespaced `topic:<term>` so the UI
can render them as descriptive keywords while keeping the closed vocabulary as
filter chips — and so an unbounded, data-derived vocabulary can never collide
with a curated tag name.
"""

from __future__ import annotations

import logging
import re

from sklearn.feature_extraction.text import TfidfVectorizer

log = logging.getLogger(__name__)

#: Prefix separating open-vocabulary keywords from curated tags.
TOPIC_PREFIX = "topic:"

#: Max keywords per place.
MAX_KEYWORDS = 4
#: Minimum TF-IDF weight to emit a keyword at all.
MIN_TFIDF_WEIGHT = 0.20
#: Terms shorter than this are too generic to identify anything.
MIN_TERM_LENGTH = 4

#: Corpus-specific stop words on top of sklearn's English list. These are words
#: that are frequent *in travel reviews about Madrid* specifically, so they
#: survive a general stop list while carrying no information here.
EXTRA_STOP_WORDS = frozenset(
    {
        "madrid", "spain", "spanish", "place", "places", "really", "very",
        "just", "good", "great", "nice", "best", "better", "worth", "like",
        "want", "know", "think", "going", "went", "come", "came", "make",
        "made", "thing", "things", "time", "times", "people", "definitely",
        "honestly", "actually", "probably", "maybe", "food", "drink", "drinks",
        "eat", "dinner", "lunch", "breakfast", "restaurant", "restaurants",
        "bar", "bars", "cafe", "coffee", "menu", "service", "staff", "area",
        "night", "day", "days", "week", "weekend", "morning", "hour", "hours",
        "euros", "euro", "price", "prices", "expensive", "cheap",
        # Travel-generic verbs/nouns that survive the general stop list.
        "works", "work", "open", "opens", "right", "real", "visit", "visited",
        "ticket", "tickets", "entry", "plate", "seating", "minutes",
        "getting", "looking", "trying", "recommend", "recommended",  # noqa
    }
)

_WORDY_RE = re.compile(rf"^[a-zA-Záéíóúñü]{{{MIN_TERM_LENGTH},}}$")


def lexicon_vocabulary() -> frozenset[str]:
    """Every word already used by the curated tag vocabulary.

    Derived from `TAG_RULES` rather than hand-listed, so the two can never drift
    apart. Keywords that duplicate a curated tag are pure noise: a place tagged
    `quiet` gaining `topic:quiet` tells the reader nothing new, and it did
    exactly that before this existed.
    """
    words: set[str] = set()
    for rule in TAG_RULES:
        words.add(rule.tag)
        words.update(rule.tag.replace("-", " ").split())
        for phrase in rule.include + rule.exclude:
            words.update(re.findall(r"[\w']+", phrase.lower()))
    return frozenset(words)


def extract_keywords(
    documents: dict[str, str], exclude_terms: dict[str, set[str]] | None = None
) -> dict[str, list[str]]:
    """Map place id -> `topic:` tags, given place id -> concatenated text.

    TF-IDF needs a corpus, so this is a batch operation over every place at
    once rather than per-place. `min_df=2` drops terms appearing for only one
    place — those are usually typos or proper nouns from a single comment, and
    with a corpus this small they would otherwise dominate every result by
    having maximal IDF.

    `exclude_terms` is a per-place veto set, used to drop a place's own name
    tokens. Those score extremely highly (a name appears in every comment about
    the place and nowhere else) while conveying nothing — "Parque de El Retiro"
    was getting `topic:parque` and `topic:park`.
    """
    exclude_terms = exclude_terms or {}
    ids = [pid for pid, text in documents.items() if text and text.strip()]
    if len(ids) < 3:
        log.info("corpus too small for TF-IDF (%d documents); skipping keywords", len(ids))
        return {}

    corpus = [documents[pid] for pid in ids]
    stop_words = sorted(set(ENGLISH_STOP_WORDS) | EXTRA_STOP_WORDS | lexicon_vocabulary())

    vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words=stop_words,
        max_df=0.5,   # a term in >50% of places is not distinctive
        min_df=2,     # ...and one in a single place is probably noise
        sublinear_tf=True,
        token_pattern=r"(?u)\b\w[\w'-]+\b",
    )
    try:
        matrix = vectorizer.fit_transform(corpus)
    except ValueError as exc:
        # Raised when every term is pruned by min_df/max_df on a tiny corpus.
        log.info("TF-IDF produced an empty vocabulary (%s); skipping keywords", exc)
        return {}

    vocabulary = vectorizer.get_feature_names_out()
    out: dict[str, list[str]] = {}
    for row, pid in enumerate(ids):
        vector = matrix.getrow(row)
        pairs = sorted(
            zip(vector.indices, vector.data, strict=True), key=lambda kv: -kv[1]
        )
        banned = exclude_terms.get(pid, set())
        terms: list[str] = []
        for index, weight in pairs:
            if weight < MIN_TFIDF_WEIGHT:
                break
            term = vocabulary[index]
            if not _WORDY_RE.match(term) or term in banned:
                continue
            terms.append(f"{TOPIC_PREFIX}{term}")
            if len(terms) >= MAX_KEYWORDS:
                break
        if terms:
            out[pid] = terms
    return out


# Imported late so the module docstring reads first; sklearn's list is long.
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS  # noqa: E402

from app.tagging.lexicon import TAG_RULES  # noqa: E402
