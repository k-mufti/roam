"""Extracting place mentions and sentiment from unstructured text.

This is the NLP layer, and it is deliberately lexical rather than model-based.
Reasoning: the task is "find proper nouns that look like venue names in short,
code-switched, typo-heavy forum prose, then score how positive the surrounding
sentence is". A transformer NER model would add ~500MB and a GPU-shaped
inference cost to do the first half slightly better, and VADER — a tuned
lexicon with negation and intensifier handling — does the second half well
enough that the difference disappears once signals are aggregated over dozens
of comments.

The extractor is intentionally **high-recall, low-precision**. That is safe
because of where its output goes: `EntityResolver._resolve_name_only` requires
0.90 fuzzy similarity against a place that a geocoded source already
established, so junk candidates ("Definitely Worth", "Last Time") are simply
dropped. Being generous here costs nothing and catches names the stricter
patterns would miss.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from app.ingestion.normalize import normalize_name

#: Travel-domain terms VADER's general-purpose lexicon gets wrong or misses.
#: Values are on VADER's own -4..+4 valence scale. This is the sanctioned way
#: to extend VADER and costs nothing at runtime.
#:
#: Measured need, not guesswork — each of these produced a wrong score on real
#: forum prose during development. "at best" is the worst offender: VADER reads
#: the token "best" as strongly positive, so "mediocre at best" scored +0.48.
DOMAIN_LEXICON: dict[str, float] = {
    "tourist trap": -2.6,
    "touristy": -1.2,
    "overrated": -2.2,
    "overpriced": -2.0,
    "overhyped": -2.0,
    "at best": -1.6,
    "avoid": -2.4,
    "skip": -1.8,
    "underwhelming": -2.0,
    "mediocre": -1.9,
    "forgettable": -1.6,
    "rip off": -2.8,
    "ripoff": -2.8,
    "hidden gem": 3.0,
    "underrated": 2.4,
    "worth it": 2.2,
    "worth every": 2.4,
    "must visit": 2.6,
    "must try": 2.4,
    "institution": 1.4,
    "unbeatable": 3.0,
    "unforgettable": 2.8,
    "atmospheric": 1.6,
    "no frills": 0.4,
}

_ANALYZER = SentimentIntensityAnalyzer()
# VADER matches multi-word entries only if they are in its lexicon, so this
# both adds unknown terms and overrides ones it scores wrongly.
_ANALYZER.lexicon.update(DOMAIN_LEXICON)

#: Sentence boundaries. A regex, not a parser: forum prose has no reliable
#: punctuation, and a real sentence tokenizer (spaCy/NLTK punkt) would add a
#: dependency and a model download to marginally improve a split that only
#: needs to isolate roughly one clause around a mention.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\n])\s+|\n+")

#: Words that may appear *inside* a multi-word Spanish place name without
#: breaking it: "Sobrino de Botín", "Mercado de San Miguel", "Corral de la
#: Morería". Without these the extractor would split on every lowercase word.
_CONNECTORS = r"(?:de|del|de\s+la|de\s+los|la|el|los|las|y|d')"

#: A capitalized token: letters (incl. accented), optionally hyphenated,
#: optionally possessive.
_CAP = r"[A-ZÁÉÍÓÚÑÜÀÂÊÔÇ][\wÁÉÍÓÚÑÜáéíóúñüàâêôç'’\-]{1,24}"

#: An optional leading number, because venue names like "1862 Dry Bar" and
#: "100 Montaditos" start with digits and would otherwise be truncated.
_LEADING_NUM = r"(?:\d{1,4}\s+)?"

CANDIDATE_RE = re.compile(
    rf"\b{_LEADING_NUM}(?:{_CAP})(?:\s+(?:{_CONNECTORS}\s+)?{_CAP}){{0,4}}\b"
)

#: Capitalized words that are never venue names in this corpus. Keeping this
#: list small is fine — see the module docstring on why precision is cheap.
STOP_CAPS = frozenset(
    {
        "madrid", "spain", "spanish", "espana", "españa", "english", "europe",
        "european", "reddit", "google", "maps", "yelp", "tripadvisor", "airbnb",
        "instagram", "tiktok", "youtube", "michelin", "wikipedia",
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
        "sunday", "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
        "edit", "op", "tldr", "tl", "dr", "imo", "imho", "ymmv", "eu", "us",
        "usa", "uk", "euro", "euros", "metro", "wifi", "atm",
        # Sentence-initial capitals that constantly form fake candidates.
        "i", "the", "if", "it", "this", "that", "there", "these", "those",
        "you", "your", "we", "my", "also", "but", "and", "so", "for", "not",
        "just", "really", "definitely", "honestly", "personally", "highly",
        "some", "any", "all", "every", "most", "best", "great", "good", "nice",
        "go", "get", "try", "avoid", "skip", "eat", "drink", "visit", "check",
        "when", "where", "what", "why", "how", "who", "which", "here",
        "yes", "no", "maybe", "sure", "thanks", "thank", "hi", "hello", "hey",
        "absolutely", "literally", "actually", "basically", "obviously",
        "everyone", "everybody", "nobody", "someone", "anyone", "anything",
        "nothing", "something", "never", "always", "still", "even", "much",
        "many", "more", "less", "very", "too", "unless", "although", "though",
        "because", "while", "after", "before", "during", "instead", "however",
        "is", "are", "was", "were", "be", "been", "do", "does", "did", "can",
        "could", "should", "would", "will", "shall", "may", "might", "must",
        "have", "has", "had", "am", "their", "its", "his", "her", "our",
        "day", "days", "week", "weekend", "night", "morning", "afternoon",
        "evening", "lunch", "dinner", "breakfast", "brunch", "tapas",
    }
)

#: A candidate must survive to at least this many characters after cleaning.
MIN_CANDIDATE_LEN = 4
#: Reject candidates whose every token is a stop-cap.
MAX_CANDIDATE_TOKENS = 5


def sentiment(text: str) -> float:
    """VADER compound score in -1..1, with the domain lexicon applied."""
    if not text.strip():
        return 0.0
    return float(_ANALYZER.polarity_scores(text)["compound"])


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    """(start, end, sentence) spans over `text`."""
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for part in _SENTENCE_SPLIT_RE.split(text):
        if part is None:
            continue
        idx = text.find(part, cursor)
        if idx < 0:
            idx = cursor
        spans.append((idx, idx + len(part), part))
        cursor = idx + len(part)
    return [s for s in spans if s[2].strip()]


#: Below this magnitude the containing sentence is treated as carrying no
#: opinion, and the window widens. VADER returns exactly 0.0 when it matches no
#: lexicon entry at all, but near-zero scores are equally uninformative.
NEUTRAL_EPSILON = 0.05


def sentiment_at(
    text: str, position: int, sentences: list[tuple[int, int, str]]
) -> tuple[float, str]:
    """Sentiment near `position`, plus the text it was measured on.

    Scoping to the sentence rather than the document is the biggest accuracy
    win in this module: document-level scoring conflates opinions about
    different places, so a comment praising one restaurant and panning another
    would assign both the same average.

    But strict sentence scoping over-corrects. Recommendations routinely name
    the place in one sentence and evaluate it in the next — "Casa Dani in
    Mercado de la Paz. Their tortilla is genuinely the best in the city." —
    which scores the mention at exactly 0.0. So when the containing sentence
    carries no opinion, the window widens to its immediate neighbours, and only
    then falls back to the whole document.
    """
    index = next(
        (i for i, (start, end, _) in enumerate(sentences) if start <= position < end), None
    )
    if index is None:
        return sentiment(text), text.strip()

    local = sentences[index][2].strip()
    score = sentiment(local)
    if abs(score) > NEUTRAL_EPSILON:
        return score, local

    window = " ".join(
        s[2].strip() for s in sentences[max(0, index - 1) : index + 2]
    ).strip()
    widened = sentiment(window)
    if abs(widened) > NEUTRAL_EPSILON:
        return widened, window
    return sentiment(text), local or text.strip()


def _is_plausible(candidate: str) -> bool:
    tokens = [_bare(t) for t in candidate.split()]
    tokens = [t for t in tokens if t]
    if not tokens or len(tokens) > MAX_CANDIDATE_TOKENS:
        return False
    if len(candidate.replace(" ", "")) < MIN_CANDIDATE_LEN:
        return False
    # Every token is noise -> not a name.
    informative = [t for t in tokens if t not in STOP_CAPS]
    if not informative:
        return False
    # A single-token candidate must itself be informative and reasonably long.
    if len(tokens) == 1 and (tokens[0] in STOP_CAPS or len(tokens[0]) < MIN_CANDIDATE_LEN):
        return False
    # Leading stop-cap ("Definitely Botin") -> trim handled by caller via the
    # normalized key; reject only if the *first informative* token is missing.
    return True


def _bare(token: str) -> str:
    """Lowercase a token and drop any contraction/possessive tail.

    "It's" -> "it" so the stop-cap test actually fires; without this, every
    sentence starting with a contraction leaked a fake candidate.
    """
    return re.split(r"['\u2019]", token.lower().strip(".,!?;:-"))[0]


def _clean(candidate: str) -> str:
    """Trim leading/trailing stop-caps so "Honestly Casa Dani" -> "Casa Dani"."""
    tokens = candidate.split()
    while tokens and _bare(tokens[0]) in STOP_CAPS:
        tokens.pop(0)
    while tokens and _bare(tokens[-1]) in STOP_CAPS:
        tokens.pop()
    return " ".join(tokens).strip(" .,;:!?-")


def _is_sentence_initial(text: str, offset: int) -> bool:
    """Whether `offset` starts a sentence.

    Matters because English capitalizes the first word of every sentence, so
    sentence-initial capitalization is *not* evidence of a proper noun. "Quiet
    and atmospheric..." and "Free entry." both produced fake place candidates
    before this existed.
    """
    preceding = text[:offset].rstrip()
    return not preceding or preceding[-1] in ".!?:\n"


def extract_spans(text: str) -> list[tuple[str, int]]:
    """(candidate name, character offset) pairs, deduplicated by normalized key.

    Offsets are kept so sentiment can be scoped to the sentence the mention
    appears in rather than the whole document.
    """
    seen: set[str] = set()
    out: list[tuple[str, int]] = []
    for match in CANDIDATE_RE.finditer(text or ""):
        cleaned = _clean(match.group(0))
        if not cleaned or not _is_plausible(cleaned):
            continue
        key = normalize_name(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((cleaned, match.start()))
    return out


def extract_spans_tagged(text: str) -> list[tuple[str, int, bool]]:
    """`extract_spans` plus a sentence-initial flag per candidate."""
    return [(name, pos, _is_sentence_initial(text, pos)) for name, pos in extract_spans(text)]


def extract_candidates(text: str) -> list[str]:
    """Candidate venue names, in order of appearance, deduplicated."""
    return [name for name, _ in extract_spans(text)]


@dataclass(slots=True)
class Mention:
    """One aggregated place mention across a body of text.

    `upvotes` is summed over every comment that mentioned the place, and
    `sentiment_samples` holds one (score, weight) pair per mention so the
    aggregate can be upvote-weighted rather than a flat mean — a +400 comment
    calling a place overrated should outweigh three +1 comments praising it.
    """

    display_name: str
    key: str
    mention_count: int = 0
    upvotes: int = 0
    thread_score: int = 0
    sentiment_samples: list[tuple[float, float]] = field(default_factory=list)
    snippets: list[tuple[str, str | None, float]] = field(default_factory=list)
    subreddits: set[str] = field(default_factory=set)
    thread_ids: set[str] = field(default_factory=set)
    #: Unix timestamp of the most recent document mentioning this place. Feeds
    #: the recency decay in scoring: a place last discussed in 2019 is a weaker
    #: recommendation than one discussed last month, even at equal upvotes.
    newest_created_utc: float = 0.0

    @property
    def weighted_sentiment(self) -> float:
        """Upvote-weighted mean sentiment in -1..1."""
        if not self.sentiment_samples:
            return 0.0
        total_w = sum(w for _, w in self.sentiment_samples)
        if total_w <= 0:
            return sum(s for s, _ in self.sentiment_samples) / len(self.sentiment_samples)
        return sum(s * w for s, w in self.sentiment_samples) / total_w


class MentionAggregator:
    """Accumulates mentions across many documents, keyed by normalized name."""

    def __init__(self) -> None:
        self._by_key: dict[str, Mention] = {}
        self._name_votes: dict[str, defaultdict[str, int]] = {}
        self._mid_sentence_keys: set[str] = set()

    def add_document(
        self,
        text: str,
        *,
        upvotes: int = 0,
        thread_score: int = 0,
        url: str | None = None,
        subreddit: str | None = None,
        thread_id: str | None = None,
        created_utc: float | None = None,
    ) -> None:
        if not text:
            return
        sentences = split_sentences(text)
        # Weight a sentiment sample by upvotes, floored at 1 so a 0-score
        # comment still counts. The square root damps it: a +400 comment is
        # worth ~20 baseline comments, not 400, so one viral opinion informs
        # the aggregate without erasing everything else.
        weight = 1.0 + max(0, upvotes) ** 0.5

        for candidate, offset, sentence_initial in extract_spans_tagged(text):
            key = normalize_name(candidate)
            local_sentiment, sentence = sentiment_at(text, offset, sentences)
            mention = self._by_key.get(key)
            if mention is None:
                mention = Mention(display_name=candidate, key=key)
                self._by_key[key] = mention
                self._name_votes[key] = defaultdict(int)
            mention.mention_count += 1
            mention.upvotes += max(0, upvotes)
            mention.thread_score = max(mention.thread_score, thread_score)
            mention.sentiment_samples.append((local_sentiment, weight))
            if len(mention.snippets) < 6:
                # Store the scoped sentence, not the whole comment: it is what
                # the sentiment actually refers to, and it is what the tagging
                # pipeline should read.
                mention.snippets.append((sentence[:1200] or text[:1200], url, weight))
            if subreddit:
                mention.subreddits.add(subreddit)
            if thread_id:
                mention.thread_ids.add(thread_id)
            if created_utc:
                mention.newest_created_utc = max(mention.newest_created_utc, float(created_utc))
            # Track surface forms so the most-used spelling wins as display name.
            self._name_votes[key][candidate] += 1
            # Corpus-level capitalization evidence: remember whether this name
            # was ever capitalized somewhere other than the start of a
            # sentence. See `mentions()`.
            if not sentence_initial:
                self._mid_sentence_keys.add(key)

    def mentions(self, *, min_mentions: int = 1) -> list[Mention]:
        """Aggregated mentions, filtered.

        The single-token filter uses corpus-level capitalization evidence: a
        one-word candidate is kept only if it appeared capitalized *somewhere
        other than* the start of a sentence, anywhere in the corpus. Multi-word
        candidates are exempt, because consecutive capitalized words are strong
        evidence of a proper noun regardless of position.

        Known cost: a place only ever named as the first word of a sentence
        ("Botin is overrated.") is lost. That is the right trade — the
        alternative admits every sentence-initial adjective in the corpus.
        """
        out = []
        for key, mention in self._by_key.items():
            if mention.mention_count < min_mentions:
                continue
            if " " not in key and key not in self._mid_sentence_keys:
                continue
            votes = self._name_votes.get(key)
            if votes:
                mention.display_name = max(votes.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]
            out.append(mention)
        return sorted(out, key=lambda m: (-m.mention_count, -m.upvotes))
