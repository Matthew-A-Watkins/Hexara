"""Blackjack shoe & hand primitives for the in-game casino (pure functions).

Each player owns a 6-deck shoe dealt to ~5/6 penetration: the shoe only
reshuffles when about one deck remains, and every dealt card is revealed, so
card counting is genuinely possible. Wagers are in "beans"; naturals pay 3:2.
The state machine (betting, hitting, dealer play, settlement) lives in
``game.py``; this module just knows cards.
"""

from . import constants as C

RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["S", "H", "D", "C"]

# Hi-Lo counting values are a player concern; we never compute them server-side.
_TEN = {"10", "J", "Q", "K"}


def new_shoe(rng):
    """A freshly shuffled 6-deck shoe (list of cards like 'AS', '10H')."""
    shoe = []
    for _ in range(C.BLACKJACK_DECKS):
        for s in SUITS:
            for r in RANKS:
                shoe.append(r + s)
    rng.shuffle(shoe)
    return shoe


def needs_shuffle(shoe):
    """True when the cut card is reached (about one of six decks left)."""
    return len(shoe) < 52  # ~5-deck penetration of a 6-deck shoe


def rank_of(card):
    return card[:-1]


def hand_value(cards):
    """Return (total, soft): the best total <= 21 if possible; soft=True when an
    ace is still counting as 11."""
    total, aces = 0, 0
    for c in cards:
        r = rank_of(c)
        if r == "A":
            total += 11
            aces += 1
        elif r in _TEN:
            total += 10
        else:
            total += int(r)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total, aces > 0


def best(cards):
    return hand_value(cards)[0]


def is_bust(cards):
    return best(cards) > 21


def is_blackjack(cards):
    return len(cards) == 2 and best(cards) == 21


def can_split(cards):
    """Two cards of equal blackjack value (e.g. any two tens) may be split."""
    if len(cards) != 2:
        return False
    return _split_key(cards[0]) == _split_key(cards[1])


def _split_key(card):
    r = rank_of(card)
    return 10 if r in _TEN else r
