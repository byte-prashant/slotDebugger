"""Reading the game's vocabulary: `BG`, `FG12`, `total`, `hit_rate`.

The keys in an expected report are the game's own words, not ours, so every key has
to be interpreted before it can be filled in. A key is one of four things:

| Kind | Example | Resolved by |
|---|---|---|
| a role | `bet`, `total`, `plays` | `role()` |
| an attribute of a component | `rtp`, `hit_rate` | `attribute()` |
| the name of a component | `BG`, `FG12` | `best_component()` |
| a label identifying one | `"name": "FG1"` | `is_identifier()` |

Matching is by meaning, not by string distance alone: `FG12` is *free game 1+2*, so
it may only match a report row carrying the same digits. Nothing matches below
`WEAK` — an unresolved key is better than a plausible wrong number.
"""

import re
from difflib import SequenceMatcher
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

# Match confidence: below WEAK refuse to match at all, below STRONG say so.
STRONG = 0.8
WEAK = 0.5
METADATA = 0.72

# Short names games use for features, expanded to the words report rows use.
ALIAS = {
    "bg": ("base", "game"),
    "basegame": ("base", "game"),
    "mg": ("base", "game"),
    "main": ("base",),
    "normal": ("base",),
    "fg": ("free", "game"),
    "fs": ("free", "game"),
    "freegame": ("free", "game"),
    "freespin": ("free", "game"),
    "spin": ("game",),
    "round": ("game",),
    "bns": ("bonus",),
    "bonusgame": ("bonus", "game"),
    "jp": ("jackpot",),
    "scat": ("scatter",),
    "multi": ("multiplier",),
}

# Words that say nothing about *which* component is meant. `game` is here because it
# is filler in every abbreviation games use (BG, FG, base game, free game, free spin).
NOISE = {"game", "win", "amount", "value", "rtp", "pct", "percent", "payout", "contribution"}

# Keys that mean something other than a component.
ROLES = {
    "total": "total", "totalrtp": "total", "rtp": "total", "gamertp": "total",
    "overall": "total", "overallrtp": "total", "sum": "total", "grandtotal": "total",
    "bet": "bet", "betamount": "bet", "betsize": "bet", "stake": "bet",
    "stakeamount": "bet", "wager": "bet", "cost": "bet", "costperspin": "bet",
    "plays": "plays", "totalplays": "plays", "spins": "plays", "totalspins": "plays",
    "numplays": "plays", "rounds": "plays",
}

# Keys *inside* a component's own object.
ATTRS = {
    "rtp": "rtp", "value": "rtp", "total": "rtp", "contribution": "rtp",
    "share": "share",
    "hitrate": "hit_rate", "hits": "hit_rate", "frequency": "hit_rate", "freq": "hit_rate",
}

# Fields that name the component an object is about, rather than measure it.
ID_FIELDS = {"name", "component", "feature", "event", "id", "key", "label"}


def flat(name: Any) -> str:
    """`"Free Game 1"` -> `"freegame1"`; the form two spellings can be compared in."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def tokens(name: Any) -> Tuple[FrozenSet[str], str]:
    """`FG12` -> ({free}, "12"); `free_game_1_2_total_win` -> ({free, total}, "12").

    Digits are kept apart from words because they select *which* feature is meant,
    and a feature index must agree exactly for two names to be the same thing.
    """
    words: List[str] = []
    digits: List[str] = []
    for part in re.findall(r"[a-z]+|\d+", str(name).lower()):
        if part.isdigit():
            digits.append(part)
            continue
        if len(part) > 3 and part.endswith("s"):
            part = part[:-1]
        words.extend(ALIAS.get(part, (part,)))
    return frozenset(w for w in words if w not in NOISE), "".join(digits)


def role(key: Any) -> Optional[str]:
    """`"bet"` -> `"bet"`; None when the key is not one of the known roles."""
    return ROLES.get(flat(key))


def attribute(key: Any) -> Optional[str]:
    """`"hit_rate"` -> `"hit_rate"`; None when the key does not measure a component."""
    return ATTRS.get(flat(key))


def is_identifier(key: Any) -> bool:
    """Whether the key names the component an object describes, e.g. `"name"`."""
    return flat(key) in ID_FIELDS


def best_component(key: Any, components: Dict) -> Tuple[Optional[str], float]:
    """The report component a game-specific key such as `BG` or `FG12` refers to.

    Returns `(name, score)`, or `(None, 0.0)` when nothing is close enough.
    """
    words, digits = tokens(key)
    key_flat = flat(key)
    best_name, best_rank = None, ()
    for name in sorted(components):
        cwords, cdigits = tokens(name)
        if digits != cdigits:  # FG1 must not match FG2, or an undigited row
            continue
        if key_flat == flat(name):
            return name, 1.0
        overlap = len(words & cwords) / len(words) if words else 0.0
        score = max(overlap, SequenceMatcher(None, key_flat, flat(name)).ratio())
        if digits and overlap:
            score = max(score, 0.75)  # the index matched and so did a word
        # ties go to a parent total, then to the shorter name
        rank = (score, "total" in cwords, -len(name))
        if rank > best_rank:
            best_name, best_rank = name, rank
    if best_name is None or best_rank[0] < WEAK:
        return None, 0.0
    return best_name, best_rank[0]


def best_metadata(key: Any, metadata: Dict) -> Optional[str]:
    """The metadata value whose field name is closest to `key`, if any is close."""
    key_flat = flat(key)
    best, score = None, METADATA
    for field, value in metadata.items():
        ratio = SequenceMatcher(None, key_flat, flat(field)).ratio()
        if ratio > score:
            best, score = value, ratio
    return best
