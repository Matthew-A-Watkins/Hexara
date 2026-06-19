"""The casino dealer's tiny chat brain.

Deliberately *not* a neural model: it's a pattern-matched conversationalist
(ELIZA-style) seasoned with live game context — your current hand, the dealer's
upcard, your streak, beans and the shoe. That keeps it dependency-free, runs in
microseconds on the CPU (no GPU, no network), and still feels alive at the
table because it can answer "should I hit?" with actual basic strategy.

Single entry point: :func:`reply`.
"""

import re

from . import casino as bj


def _word(t, *words):
    return any(re.search(r"\b" + w + r"\b", t) for w in words)


def reply(g, pid, text):
    """One in-character reply from the dealer to ``text`` from player ``pid``."""
    t = (text or "").lower().strip()
    name = g._name(pid)
    p = g.players[pid]
    seat = g._bj(pid)
    rng = g.rng

    def pick(*opts):
        return rng.choice(opts)

    # Live strategy question beats every other pattern while a hand is in play.
    if _word(t, "hit", "stand", "double", "split", "should", "advice", "help", "play"):
        if seat["state"] == "player":
            return _strategy(seat, name, rng)
        if _word(t, "should", "advice", "help", "play"):
            return pick(
                "Deal yourself in and I'll talk you through every card, %s." % name,
                "Place a bet first, friend — then we'll plot your path to 21.")

    if _word(t, "hi", "hello", "hey", "howdy", "yo", "greetings", "evening"):
        return pick(
            "Well hello, %s! Pull up a chair — the felt's warm tonight." % name,
            "Hey there, %s. The shoe's got %d cards in it and your luck's due." % (name, len(g.bj_shoe or [])),
            "Welcome back to my table, %s. Beans ready?" % name)
    if "how are you" in t or "how's it going" in t or "hows it going" in t:
        return pick(
            "Living the dream, %s — fresh felt, full shoe, good company." % name,
            "Can't complain! Tips have been kind and nobody's flipped the table yet.")
    if _word(t, "name") or "who are you" in t:
        return pick(
            "Folks just call me the dealer — easier for everyone after midnight.",
            "Names come and go, %s. The house calls me whatever wins them money." % name)
    if _word(t, "count", "counting", "counter"):
        return pick(
            "Counting? I see nothing, I hear nothing — I just deal the cards. 😉",
            "A sharp memory isn't a crime at THIS table, %s. The aid's right there." % name)
    if _word(t, "tip", "tips"):
        return pick(
            "Tips keep this old visor shiny, %s — never expected, always adored." % name,
            "You tip, I beam. That's the whole arrangement, friend. 🎩")
    if _word(t, "bean", "beans", "rate", "rates", "price", "cost"):
        return ("House rates: %d beans buys a resource card, dev cards cash in at %d, "
                "and a victory point runs %d beans." %
                (g.beans_per_resource, g.beans_per_resource // 2, g.beans_per_vp))
    if _word(t, "luck", "lucky"):
        return pick(
            "Luck's a lazy river, %s — you just have to be in the boat when it turns." % name,
            "Between us? The shoe doesn't care. But I'm rooting for you anyway.")
    if _word(t, "love", "marry", "date", "beautiful", "pretty", "handsome", "cute"):
        return pick(
            "Flattery pays no blackjack, %s... but it does brighten the shift. 💛" % name,
            "House rules say I can only love a well-played double down. Sorry, %s!" % name)
    if _word(t, "cheat", "cheating", "rigged", "scam"):
        return pick(
            "Every card from one honest shoe, %s — %d left, all face up once dealt." % (name, len(g.bj_shoe or [])),
            "Rigged?! I'd sooner mis-stack my own tips. The count's public, friend.")
    if _word(t, "lost", "losing", "bust", "busted", "broke") or seat["streak"] <= -3:
        return pick(
            "Chin up, %s — every cold shoe warms eventually. I've seen it a thousand times." % name,
            "The table giveth and taketh, %s. Maybe a smaller bet while the tide turns?" % name)
    if _word(t, "win", "won", "winning"):
        if seat["streak"] >= 2:
            return "Don't jinx it, %s — just keep doing exactly what you're doing! 🔥" % name
        return pick(
            "That's the spirit! Fortune loves the bold, %s." % name,
            "Stack those beans high, %s — I love watching a winner work." % name)
    if _word(t, "bye", "goodbye", "leave", "later"):
        return pick(
            "Off so soon, %s? The seat stays warm for you." % name,
            "Travel well, %s — come back when the beans burn a hole in your pocket." % name)
    if "?" in t:
        return pick(
            "Great question, %s. My honest answer: bet what you can lose with a smile." % name,
            "Hmm. Ask me about rates, the count, or whether to hit — those I know cold.",
            "If I knew that, %s, I wouldn't be dealing cards for tips!" % name)
    # Ambient fallback, flavoured by their situation.
    beans = p.get("beans", 0)
    return pick(
        "Mm-hm. So — %d beans in your pouch and a fresh hand a click away, %s." % (beans, name),
        "I hear you, %s. The shoe's sitting at %d cards if you're tempted." % (name, len(g.bj_shoe or [])),
        "That's the casino for you, %s. Care to put a bean where your mouth is?" % name)


def _strategy(seat, name, rng):
    """A genuine basic-strategy hint for the live hand."""
    hand = seat["hands"][seat["active"]]
    cards = hand["cards"]
    total, soft = bj.hand_value(cards)
    up = bj.rank_of(seat["dealer"][0]) if seat["dealer"] else "10"
    upv = 11 if up == "A" else (10 if up in ("10", "J", "Q", "K") else int(up))

    def pick(*opts):
        return rng.choice(opts)

    if bj.can_split(cards) and bj.rank_of(cards[0]) in ("A", "8"):
        return "Aces and eights, %s — you always split those. House wisdom, free of charge." % name
    if soft and total <= 17:
        return "Soft %d can't bust — I'd hit that all day, %s." % (total, name)
    if total >= 17:
        return pick(
            "%d? Stand proud, %s. Let me sweat the draw for once." % (total, name),
            "Stand on %d, friend — only heartbreak lives past that hit." % total)
    if 12 <= total <= 16:
        if upv >= 7:
            return "My %s showing beats your %d more often than not — I'd hit, %s." % (up, total, name)
        return "I'm showing a %s — that's bust bait. Stand on %d and make me draw, %s." % (up, total, name)
    if total in (10, 11):
        return "%d is double-down country, %s — if the beans allow. Otherwise hit it." % (total, name)
    return "Only %d? Hit, %s — no card in the shoe can hurt you yet." % (total, name)
