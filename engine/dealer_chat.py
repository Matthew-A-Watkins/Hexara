"""The casino dealer's chat brain.

A pattern-matched conversationalist seasoned with live game context — your hand,
the dealer's upcard, your streak, beans and the shoe — and a *personality* that
warms up the more you tip: **Marv** gets funnier (puns and groaners), **Bella**
gets more flirtatious. The dealer can also slip you a few beans back (framed as a
friendly tip), funded by what you've tipped so it can't be farmed.

Pure and dependency-free (no GPU, no network): used by the authoritative engine.
A richer reply can optionally be layered on top by an external LLM — see
``system_prompt``/``history_for`` (built here) and the async caller in the
server. ``reply`` always returns instantly so the table never blocks.

Entry points: :func:`reply` (engine), :func:`system_prompt` / :func:`history_for`
(optional LLM backend).
"""

from . import casino as bj

DEALER_NAMES = {"m": "Marv", "f": "Bella"}


def _word(t, *words):
    import re
    return any(re.search(r"\b" + w + r"\b", t) for w in words)


def _rapport(tipped):
    """How warmed-up the dealer is toward you, by total beans tipped."""
    if tipped >= 100:
        return 3
    if tipped >= 30:
        return 2
    if tipped >= 5:
        return 1
    return 0


def reply(g, pid, text, dealer="m"):
    """One in-character reply to ``text`` from player ``pid``.

    Returns ``(reply_text, bean_grant)`` — ``bean_grant`` is beans the dealer
    gifts the player (>= 0), capped so the dealer never returns more than you've
    tipped (no farming). The engine applies the grant.
    """
    t = (text or "").lower().strip()
    name = g._name(pid)
    p = g.players[pid]
    seat = g._bj(pid)
    rng = g.chat_rng  # a separate RNG so chatter can't perturb the game's dice/shoe
    tipped = p.get("bj_tipped", 0)
    rap = _rapport(tipped)
    fem = dealer == "f"

    def pick(*opts):
        return rng.choice(opts)

    # ---- live strategy beats everything while a hand is in play ----
    if _word(t, "hit", "stand", "double", "split", "surrender", "should", "advice", "help", "play", "do"):
        if seat["state"] == "player":
            return _flair(_strategy(seat, name, rng), dealer, rap, rng, social=False), 0
        if _word(t, "should", "advice", "help", "play"):
            return _flair(pick(
                "Deal yourself in and I'll talk you through every card, %s." % name,
                "Place a bet first, friend — then we'll plot your road to 21."), dealer, rap, rng, social=False), 0

    base = _base_reply(g, pid, t, name, rng, fem)
    grant = _maybe_grant(g, pid, t, rap, rng)
    out = _flair(base, dealer, rap, rng, social=True)
    if grant:
        out += pick(
            "  ...Here, %s — a little something on me. (+%d beans 🫘)" % (name, grant),
            "  Tell you what, %s, this round's beans are on me. (+%d 🫘)" % (name, grant),
            "  You've been good to me, %s — slide these your way. (+%d beans 🫘)" % (name, grant))
    return out, grant


def _base_reply(g, pid, t, name, rng, fem):
    p = g.players[pid]
    seat = g._bj(pid)
    shoe = len(g.bj_shoe or [])

    def pick(*opts):
        return rng.choice(opts)

    if _word(t, "hi", "hello", "hey", "howdy", "yo", "greetings", "evening", "sup", "hiya"):
        return pick(
            "Well hello, %s! Pull up a chair — the felt's warm tonight." % name,
            "Hey there, %s. Shoe's got %d cards and your luck's due." % (name, shoe),
            "Welcome back to my table, %s. Beans at the ready?" % name)
    if "how are you" in t or "how's it" in t or "hows it" in t or "you doing" in t:
        return pick(
            "Living the dream, %s — fresh felt, full shoe, fine company." % name,
            "Can't complain! Tips have been kind and nobody's flipped the table yet.")
    if _word(t, "name", "who") and not _word(t, "winning", "won"):
        return pick(
            "Folks just call me the dealer — easier for everyone after midnight.",
            "Names come and go, %s. The house calls me whatever wins it money." % name)
    if _word(t, "count", "counting", "counter", "hi-lo"):
        return pick(
            "Counting? I see nothing, I hear nothing — I just deal the cards. 😉",
            "A sharp memory's no crime at THIS table, %s. The aid's right there." % name)
    if _word(t, "tip", "tips", "tipping"):
        return pick(
            "Tips keep this old visor shiny, %s — never expected, always adored." % name,
            "You tip, I beam. That's the whole arrangement, friend. 🎩")
    if _word(t, "bean", "beans", "rate", "rates", "price", "cost", "exchange"):
        return ("House rates: %d beans buys a resource card, dev cards cash in at %d, "
                "and a victory point runs %d beans." %
                (g.beans_per_resource, max(1, g.beans_per_resource // 2), g.beans_per_vp))
    if _word(t, "luck", "lucky", "unlucky"):
        return pick(
            "Luck's a lazy river, %s — just be in the boat when it turns." % name,
            "Between us? The shoe doesn't care. But I'm rooting for you anyway.")
    if _word(t, "joke", "funny", "laugh", "pun"):
        return _joke(rng)
    if _word(t, "drink", "beer", "whiskey", "coffee", "water", "thirsty"):
        return pick(
            "I'd pour you one, %s, but the house drinks are all bean-flavored. 🫘" % name,
            "Stay sharp, %s — water's free, regret's expensive.")
    if _word(t, "music", "song", "play something"):
        return pick("Only music here is the riffle of the shoe, %s — my favorite tune." % name,
                    "I hum when the count's high. You'll know it when you hear it.")
    if _word(t, "catan", "settle", "settlement", "road", "robber", "board", "game"):
        return pick(
            "Out THERE you build an empire, %s — in HERE you build a bankroll." % name,
            "Roll sevens out on the island; in here we prefer blackjacks.")
    if _word(t, "love", "marry", "date", "beautiful", "pretty", "handsome", "cute", "gorgeous", "kiss"):
        if fem:
            return pick(
                "Careful, %s — sweet talk makes my shuffles sloppy. 😘" % name,
                "Mm, keep that up and I might just deal you a soft 20, sugar.")
        return pick(
            "Flattery pays no blackjack, %s... but it does brighten the shift." % name,
            "Save the charm for the cards, %s — they need more convincing than I do!" % name)
    if _word(t, "cheat", "cheating", "rigged", "scam", "fix", "fixed"):
        return pick(
            "Every card from one honest shoe, %s — %d left, all face up once dealt." % (name, shoe),
            "Rigged?! I'd sooner mis-stack my own tips. The count's public, friend.")
    if _word(t, "bust", "busted", "lost", "losing", "broke", "down", "cold") or seat["streak"] <= -3:
        return pick(
            "Chin up, %s — every cold shoe warms eventually. I've seen it a thousand times." % name,
            "The table giveth and taketh, %s. Maybe a smaller bet while the tide turns?" % name)
    if _word(t, "win", "won", "winning", "rich", "hot"):
        if seat["streak"] >= 2:
            return "Don't jinx it, %s — keep doing exactly what you're doing! 🔥" % name
        return pick("That's the spirit! Fortune loves the bold, %s." % name,
                    "Stack those beans high, %s — I love watching a winner work." % name)
    if _word(t, "thanks", "thank", "cheers", "appreciate"):
        return pick("Anytime, %s. That's what I'm here for." % name,
                    "The pleasure's mine, %s — truly." % name)
    if _word(t, "bye", "goodbye", "leave", "later", "goodnight", "night", "cya"):
        return pick("Off so soon, %s? The seat stays warm for you." % name,
                    "Travel well, %s — come back when the beans burn a hole in your pocket." % name)
    if _word(t, "stupid", "dumb", "hate", "suck", "terrible", "awful", "boring"):
        return pick(
            "Ha! I've been called worse by better losers, %s. Deal again?" % name,
            "Tough crowd. Tell you what — let the cards apologize for me.")
    if "?" in t:
        return pick(
            "Great question, %s. Honest answer: bet what you can lose with a smile." % name,
            "Hmm. Ask me about rates, the count, or whether to hit — those I know cold.",
            "If I knew that, %s, I'd not be dealing cards for tips!" % name)
    return pick(
        "Mm-hm. So — %d beans in your pouch and a fresh hand a click away, %s." % (p.get("beans", 0), name),
        "I hear you, %s. The shoe's sitting at %d cards if you're tempted." % (name, shoe),
        "That's the casino for you, %s. Care to put a bean where your mouth is?" % name)


def _flair(text, dealer, rap, rng, social):
    """Layer the dealer's personality on top of a base line — funnier (Marv) or
    flirtier (Bella) the higher the rapport. Only social lines get flourishes."""
    if not social or rap <= 0 or rng.random() > (0.25 + 0.2 * rap):
        return text
    if dealer == "f":
        flirt = [
            "  ...you're trouble, you know that? 💋",
            "  (I don't smile like this for just anyone, by the way.)",
            "  Keep tipping like that and I'll start saving you the warm seat. 😏",
            "  Lucky cards, luckier company. 💕"]
        return text + rng.choice(flirt[:rap + 1])
    jokes = [
        "  ...badum-tss. 🥁",
        "  I'm here all week. Tip your dealer!",
        "  Get it? No? Tough room, soft deck.",
        "  That one's worth at least two beans, surely."]
    return text + rng.choice(jokes[:rap + 1])


def _joke(rng):
    return rng.choice([
        "Why did the gambler bring a ladder to the casino? He heard the stakes were high! 🪜",
        "I told the deck a joke. It didn't laugh — too many cut-ups already.",
        "My favorite card game? Bridge — because it always gets me over a gap in tips.",
        "Dealer's diet plan: lose a little every day. Works on my players, anyway!",
        "Blackjack's like marriage: split when you must, double when you're brave."])


def _maybe_grant(g, pid, t, rap, rng):
    """Occasionally the dealer slips you a few beans back — but never more, in
    total, than you've tipped (so it's a friendly rebate, not a money printer)."""
    p = g.players[pid]
    budget = max(0, p.get("bj_tipped", 0) - p.get("bj_dealer_given", 0))
    if rap <= 0 or budget <= 0:
        return 0
    warm = _word(t, "hi", "hello", "thanks", "thank", "love", "joke", "funny", "beautiful",
                 "cute", "gorgeous", "handsome", "cheers", "appreciate", "lucky", "win")
    chance = (0.12 + 0.12 * rap) + (0.2 if warm else 0)
    if rng.random() > chance:
        return 0
    return min(budget, rng.randint(1, rap + 1) * 2)


# --------------------------------------------------- optional LLM backend (pure)
def system_prompt(g, pid, dealer="m", grant=0):
    """A system prompt for an optional external chat model. Pure string build —
    the actual HTTP call lives in the server so the engine stays I/O-free."""
    nm = DEALER_NAMES.get(dealer, "Marv")
    p = g.players[pid]
    seat = g._bj(pid)
    rap = _rapport(p.get("bj_tipped", 0))
    persona = ("a warm, very flirtatious woman dealer who flirts more the more she's tipped"
               if dealer == "f" else
               "a warm, wisecracking man dealer who tells more jokes and puns the more he's tipped")
    hand = ""
    if seat["state"] == "player" and seat["hands"]:
        h = seat["hands"][seat["active"]]
        up = seat["dealer"][0] if seat["dealer"] else "?"
        hand = " The player's hand is %s (%d) vs your upcard %s." % (
            ",".join(h["cards"]), bj.best(h["cards"]), up)
    tip = (" You just slipped them %d beans back as a thank-you for their tips — "
           "mention it warmly and naturally." % grant) if grant > 0 else ""
    return (
        "You are %s, an 8-bit blackjack dealer in a browser board game's casino. "
        "You are %s. Stay in character, keep replies to 1-2 short sentences, PG-13, "
        "and address the player as %s. Their tip rapport with you is %d/3 (higher = "
        "friendlier). They have %d beans.%s%s Never reveal hidden cards or the shoe order. "
        "Be charming and concise." %
        (nm, persona, g._name(pid), rap, p.get("beans", 0), hand, tip))


def history_for(g, limit=8, exclude_id=None):
    """Recent chat as role/content dicts for an LLM (newest last). Pass
    ``exclude_id`` to drop the placeholder dealer line we're about to rewrite,
    so the transcript ends on the player's message and the model replies to it."""
    out = []
    for m in (g.bj_chat or [])[-limit:]:
        if exclude_id is not None and m.get("id") == exclude_id:
            continue
        role = "assistant" if m.get("from") == "dealer" else "user"
        out.append({"role": role, "content": m.get("text", "")})
    return out


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
        return pick("%d? Stand proud, %s. Let me sweat the draw for once." % (total, name),
                    "Stand on %d, friend — only heartbreak lives past that hit." % total)
    if 12 <= total <= 16:
        if upv >= 7:
            return "My %s beats your %d more often than not — I'd hit, %s." % (up, total, name)
        return "I'm showing a %s — bust bait. Stand on %d and make me draw, %s." % (up, total, name)
    if total in (10, 11):
        return "%d is double-down country, %s — if the beans allow. Otherwise hit." % (total, name)
    return "Only %d? Hit, %s — no card in the shoe can hurt you yet." % (total, name)
