"""Map specifications: presets, validation and resolution to a board.

A *map spec* is a small JSON-friendly dict that describes a board. It comes
either from a built-in preset (the "DLC-style" alternate layouts) or from the
in-lobby custom map editor. Three content modes are supported:

  * ``radius``  - a regular hexagon of the given radius, randomly filled.
  * ``axials``  - an arbitrary island shape (list of [q, r]), randomly filled.
  * ``tiles``   - a fully explicit layout: each tile's terrain and number token.

Ports are either spread automatically, given as a type list (``ports``) spread
around the coast, or pinned exactly (``portsExplicit`` as vertex pairs). The
resolver returns the board graph plus the per-hex terrain/number assignment and
the robber's starting tile, ready for :class:`~engine.game.Game`.

Nothing here knows about networking or rendering.
"""

import math

from . import constants as C
from . import geometry as geo


class MapError(Exception):
    """Raised when a map spec is malformed. Message is shown to the host."""


# Procedural board sizes are capped so a board stays playable and renderable.
MIN_RADIUS = 1
MAX_RADIUS = 5          # radius 5 = 91 hexes
MAX_TILES = 200         # absolute cap for custom/axial boards

_RESOURCE_TERRAINS = [
    C.TERRAIN_FOREST, C.TERRAIN_PASTURE, C.TERRAIN_FIELDS,
    C.TERRAIN_HILLS, C.TERRAIN_MOUNTAINS,
]
# Relative weights mirror the standard 19-hex island (4/4/4/3/3).
_TERRAIN_WEIGHTS = {
    C.TERRAIN_FOREST: 4, C.TERRAIN_PASTURE: 4, C.TERRAIN_FIELDS: 4,
    C.TERRAIN_HILLS: 3, C.TERRAIN_MOUNTAINS: 3,
}
_VALID_TERRAINS = set(_RESOURCE_TERRAINS) | {C.TERRAIN_DESERT, C.TERRAIN_GOLD, C.TERRAIN_BEANS}
# Terrains that carry a number token and produce when rolled.
_PRODUCING_TERRAINS = set(_RESOURCE_TERRAINS) | {C.TERRAIN_GOLD, C.TERRAIN_BEANS}
_VALID_NUMBERS = set(range(2, 13)) - {7}


# --------------------------------------------------------------------- pools
def terrain_pool(n, rng, gold=0, beans=0, deserts=None):
    """A list of ``n`` terrains in standard-ish proportions, with optional
    ``gold`` fields, ``beans`` tiles and a desert count mixed in."""
    if n <= 0:
        return []
    gold = max(0, min(int(gold), n))
    beans = max(0, min(int(beans), n - gold))
    room = n - gold - beans  # slots left for desert + resource terrains
    if deserts is None:
        deserts = max(1, round(n / 19.0))
    deserts = max(0, min(int(deserts), room))
    # Always leave at least one producing resource tile when there's room for one
    # (avoids a degenerate all-desert board that can never produce).
    if room >= 1:
        deserts = min(deserts, room - 1)
    remaining = n - deserts - gold - beans
    total_w = sum(_TERRAIN_WEIGHTS[t] for t in _RESOURCE_TERRAINS)
    counts = {t: int(remaining * _TERRAIN_WEIGHTS[t] / total_w) for t in _RESOURCE_TERRAINS}
    pool = ([C.TERRAIN_DESERT] * deserts) + ([C.TERRAIN_GOLD] * gold) + ([C.TERRAIN_BEANS] * beans)
    for t, c in counts.items():
        pool += [t] * c
    i = 0
    while len(pool) < n:  # pad any rounding shortfall by cycling resource terrains
        pool.append(_RESOURCE_TERRAINS[i % len(_RESOURCE_TERRAINS)])
        i += 1
    return pool[:n]


def number_pool(n, rng):
    """A list of ``n`` number tokens following the standard token spread."""
    if n <= 0:
        return []
    base = list(C.NUMBER_TOKENS)  # the canonical 18-token multiset
    pool = []
    while len(pool) < n:
        pool += base
    return pool[:n]


# ------------------------------------------------------------------- presets
def _offset(field, dq, dr):
    return [(q + dq, r + dr) for (q, r) in field]


def _islands(centers, radius):
    """A non-overlapping set of radius-`radius` islands at the given centres."""
    out = []
    for (cq, cr) in centers:
        out += _offset(geo.hex_field(radius), cq, cr)
    # de-dup defensively in case centres are too close
    return [list(c) for c in sorted(set(out))]


def _elongated(rq, rr, rs):
    """A stretched hexagon: |q|<=rq, |r|<=rr, |q+r|<=rs."""
    return [[q, r] for r in range(-rr, rr + 1) for q in range(-rq, rq + 1)
            if abs(q) <= rq and abs(r) <= rr and abs(q + r) <= rs]


def _frontier_axials():
    field = set(map(tuple, geo.hex_field(2)))
    for corner in [(2, -2), (-2, 2), (2, 0)]:
        field.discard(corner)
    for outcrop in [(0, -3), (-1, 3)]:
        field.add(outcrop)
    return [list(c) for c in sorted(field)]


def _preset_specs():
    return {
        # --- sizes ---
        "standard": {"name": "Standard Island", "radius": 2,
                     "description": "The classic 19-hex board, randomized each game."},
        "small": {"name": "Small Cove", "radius": 1,
                  "description": "A compact 7-hex board for fast 2-3 player duels."},
        "large": {"name": "Greater Isle", "radius": 3,
                  "description": "A 37-hex island for longer 4-6 player games."},
        "huge": {"name": "Continent", "radius": 4,
                 "description": "A sprawling 61-hex landmass for epic sessions."},
        "colossal": {"name": "Colossus", "radius": 5,
                     "description": "A monstrous 91-hex world. Bring snacks."},
        # --- shapes ---
        "extended": {"name": "Greater Catan (5-6)", "axials": _elongated(3, 2, 3),
                     "description": "An extended board sized for 5-6 players."},
        "frontier": {"name": "Frontier", "axials": _frontier_axials(),
                     "description": "A jagged, non-hexagonal coastline."},
        "twin": {"name": "Twin Continents", "axials": _islands([(-4, 1), (4, -1)], 2),
                 "description": "Two landmasses separated by open sea."},
        # --- archipelagos (Seafarers-style layouts, base rules) ---
        "newshores": {"name": "Heading for New Shores", "gold": 2,
                      "axials": _islands([(0, 0), (7, -2), (3, 5)], 1),
                      "description": "Three islands and a couple of gold fields to chase."},
        "fourislands": {"name": "The Four Islands", "axials": _islands([(0, 0), (6, 0), (0, 5), (6, 5)], 1),
                        "description": "Four equal islands — claim your shores."},
        "pirateislands": {"name": "The Pirate Islands", "gold": 1,
                          "axials": _islands([(0, 0), (6, -1), (2, 5), (8, 4), (4, 9)], 1),
                          "description": "Five scattered isles for adventurous fleets."},
        # --- terrain themes ---
        "golden": {"name": "Golden Rivers", "radius": 2, "gold": 5,
                   "description": "A standard isle veined with gold fields."},
        "treasure": {"name": "Treasure Isles", "gold": 4,
                     "axials": _islands([(0, 0), (6, 0), (3, 5)], 1),
                     "description": "Three islands rich with gold."},
        "desertrun": {"name": "Through the Desert", "radius": 3, "deserts": 7,
                      "description": "A parched 37-hex board — every oasis counts."},
        # --- gamble ---
        "highroller": {"name": "High Roller's Isle", "radius": 2, "beans": 3,
                       "description": "Standard isle studded with bean tiles (needs Gamble mode)."},
    }


def _spec_has_beans(spec):
    if spec.get("beans"):
        return True
    return any(t.get("terrain") == C.TERRAIN_BEANS for t in (spec.get("tiles") or []))


def list_presets():
    """Lobby-facing preset summaries."""
    out = []
    for pid, spec in _preset_specs().items():
        out.append({
            "id": pid,
            "name": spec["name"],
            "description": spec["description"],
            "tiles": _spec_tile_count(spec),
            "needsGamble": _spec_has_beans(spec),  # bean tiles only pay in Gamble mode
        })
    return out


def preset_spec(pid):
    spec = _preset_specs().get(pid)
    return dict(spec) if spec else None


def _spec_tile_count(spec):
    if spec.get("tiles"):
        return len(spec["tiles"])
    if spec.get("axials"):
        return len(spec["axials"])
    r = spec.get("radius", 2)
    return 3 * r * r + 3 * r + 1


# ---------------------------------------------------------------- validation
def validate(spec):
    """Validate a (possibly user-authored) map spec, returning a normalized
    copy. Raises :class:`MapError` with a friendly message on any problem."""
    if spec is None:
        return {"name": "Standard Island", "radius": 2}
    if not isinstance(spec, dict):
        raise MapError("Map must be an object.")

    # A preset reference fills in defaults, then explicit fields override.
    if spec.get("preset"):
        base = preset_spec(spec["preset"])
        if base is None:
            raise MapError("Unknown preset '%s'." % spec["preset"])
        merged = dict(base)
        merged.update({k: v for k, v in spec.items() if k != "preset"})
        spec = merged

    out = {"name": str(spec.get("name") or "Custom Map")[:40]}

    if spec.get("tiles"):
        out["tiles"] = _validate_tiles(spec["tiles"])
        coords = set((t["q"], t["r"]) for t in out["tiles"])
        if spec.get("portsEdges"):
            out["portsEdges"] = _validate_port_edges(spec["portsEdges"], coords)
        elif "portsExplicit" in spec or "ports" in spec:
            out.update(_validate_ports(spec, out["tiles"]))
        if "robber" in spec:
            out["robber"] = spec["robber"]
        return out

    if spec.get("axials"):
        axials = _validate_axials(spec["axials"])
        out["axials"] = axials
    else:
        radius = spec.get("radius", 2)
        try:
            radius = int(radius)
        except (TypeError, ValueError):
            raise MapError("Radius must be a whole number.")
        if not (MIN_RADIUS <= radius <= MAX_RADIUS):
            raise MapError("Radius must be between %d and %d." % (MIN_RADIUS, MAX_RADIUS))
        out["radius"] = radius

    # Optional gold-field / bean-tile / desert counts mixed into the random fill.
    # Use `in spec` (not truthiness) so an explicit 0 (e.g. deserts:0) is kept.
    for key in ("gold", "beans", "deserts"):
        if key in spec and spec[key] is not None:
            try:
                v = int(spec[key])
            except (TypeError, ValueError):
                raise MapError("'%s' must be a whole number." % key)
            if v < 0:
                raise MapError("'%s' can't be negative." % key)
            out[key] = v

    # Ports: pinned edges (portsEdges) or an explicit type list win; otherwise
    # ports are placed thematically (by tile) after the terrain is known.
    if spec.get("portsEdges"):
        if out.get("axials"):
            coords = set((a[0], a[1]) for a in out["axials"])
        else:
            coords = set(geo.hex_field(out["radius"]))
        out["portsEdges"] = _validate_port_edges(spec["portsEdges"], coords)
    elif spec.get("ports"):
        out["ports"] = _validate_port_types(spec["ports"])
    return out


def _is_auto_ports(spec):
    """True when ports aren't pinned/explicit and should be placed thematically."""
    return not (spec.get("portsEdges") or spec.get("portsExplicit") or spec.get("ports"))


def _thematic_ports(board, hexes):
    """Place ports intentionally, BASED ON THE TILES: each 2:1 resource port sits
    on a coastal edge of a tile that produces that resource (one per resource),
    and the rest are generic 3:1 ports — spread evenly around the coast, with the
    count scaled to the number of land tiles (so islands aren't over-ported).
    Returns a list of (edge_id, port_type) for geometry.add_edge_ports."""
    coastal = [e for e in board["edges"] if e["coastal"]]
    if not coastal:
        return []
    vs = board["vertices"]

    def angle(e):  # clockwise around the board centre (screen y grows downward)
        mx = (vs[e["v1"]]["x"] + vs[e["v2"]]["x"]) / 2.0
        my = (vs[e["v1"]]["y"] + vs[e["v2"]]["y"]) / 2.0
        return math.atan2(-my, mx)

    coastal.sort(key=angle, reverse=True)
    n = len(coastal)
    n_ports = max(2, min(int(round(len(hexes) / 2.2)), n // 2 or 1))

    # Evenly-spaced coastal edges.
    chosen, used = [], set()
    for i in range(n_ports):
        idx = int(round(i * n / n_ports)) % n
        while idx in used and len(used) < n:
            idx = (idx + 1) % n
        used.add(idx)
        chosen.append(coastal[idx])

    # Type each by the resource of the land tile it touches (one 2:1 per resource).
    out, claimed = [], set()
    for e in chosen:
        res = C.TERRAIN_RESOURCE.get(hexes[e["hexes"][0]]["terrain"])
        if res and res not in claimed:
            claimed.add(res)
            out.append((e["id"], res))
        else:
            out.append((e["id"], C.PORT_GENERIC))
    return out


def _validate_port_edges(lst, coords):
    """Validate pinned ports: [{q, r, dir, type}] with dir 0-5 counting from the
    east-facing edge clockwise. Coastal-ness is checked at resolve time."""
    if not isinstance(lst, list):
        raise MapError("'portsEdges' must be a list.")
    out, seen = [], set()
    for p in lst:
        if not isinstance(p, dict):
            raise MapError("Each port needs q, r, dir and type.")
        try:
            q, r, d = int(p["q"]), int(p["r"]), int(p["dir"])
        except (KeyError, TypeError, ValueError):
            raise MapError("Each port needs whole-number q, r and dir.")
        if not (0 <= d <= 5):
            raise MapError("Port 'dir' must be 0-5.")
        if coords is not None and (q, r) not in coords:
            raise MapError("Port tile [%d, %d] is not on the board." % (q, r))
        _check_port_type(p.get("type"))
        key = (q, r, d)
        if key in seen:
            raise MapError("Two ports share the edge at [%d, %d] dir %d." % (q, r, d))
        seen.add(key)
        out.append({"q": q, "r": r, "dir": d, "type": p["type"]})
    return out


def _validate_axials(axials):
    if not isinstance(axials, list) or not axials:
        raise MapError("'axials' must be a non-empty list of [q, r] pairs.")
    if len(axials) > MAX_TILES:
        raise MapError("Too many tiles (max %d)." % MAX_TILES)
    seen, out = set(), []
    for a in axials:
        if not (isinstance(a, (list, tuple)) and len(a) == 2):
            raise MapError("Each tile coordinate must be [q, r].")
        q, r = int(a[0]), int(a[1])
        if (q, r) in seen:
            raise MapError("Duplicate tile at [%d, %d]." % (q, r))
        seen.add((q, r))
        out.append([q, r])
    return out


def _validate_tiles(tiles):
    if not isinstance(tiles, list) or not tiles:
        raise MapError("'tiles' must be a non-empty list.")
    if len(tiles) > MAX_TILES:
        raise MapError("Too many tiles (max %d)." % MAX_TILES)
    seen, out = set(), []
    for t in tiles:
        if not isinstance(t, dict):
            raise MapError("Each tile must be an object with q, r, terrain.")
        try:
            q, r = int(t["q"]), int(t["r"])
        except (KeyError, TypeError, ValueError):
            raise MapError("Each tile needs whole-number q and r.")
        if (q, r) in seen:
            raise MapError("Duplicate tile at [%d, %d]." % (q, r))
        seen.add((q, r))
        terrain = t.get("terrain")
        if terrain not in _VALID_TERRAINS:
            raise MapError("Unknown terrain '%s'." % terrain)
        number = t.get("number")
        if terrain == C.TERRAIN_DESERT:
            number = None
        elif terrain not in _PRODUCING_TERRAINS:
            number = None
        else:
            if number is None:
                raise MapError("Tile [%d, %d] needs a number token." % (q, r))
            try:
                number = int(number)
            except (TypeError, ValueError):
                raise MapError("Tile [%d, %d] has a bad number." % (q, r))
            if number not in _VALID_NUMBERS:
                raise MapError("Number tokens must be 2-12 (not 7).")
        out.append({"q": q, "r": r, "terrain": terrain, "number": number})
    return out


def _validate_ports(spec, tiles):
    out = {}
    if spec.get("portsExplicit"):
        pe = spec["portsExplicit"]
        if not isinstance(pe, list):
            raise MapError("'portsExplicit' must be a list.")
        clean = []
        for p in pe:
            if not isinstance(p, dict) or "vertices" not in p:
                raise MapError("Each explicit port needs type and vertices.")
            vs = p["vertices"]
            if not (isinstance(vs, (list, tuple)) and len(vs) == 2):
                raise MapError("Port vertices must be a [v1, v2] pair.")
            _check_port_type(p.get("type"))
            clean.append({"type": p["type"], "vertices": [int(vs[0]), int(vs[1])]})
        out["portsExplicit"] = clean
    elif spec.get("ports"):
        out["ports"] = _validate_port_types(spec["ports"])
    return out


def _validate_port_types(ports):
    if not isinstance(ports, list):
        raise MapError("'ports' must be a list of port types.")
    for p in ports:
        _check_port_type(p)
    return list(ports)


def _check_port_type(t):
    if t != C.PORT_GENERIC and t not in C.RESOURCES:
        raise MapError("Unknown port type '%s' (use '3:1' or a resource)." % t)


# ----------------------------------------------------------------- resolution
def resolve(spec, rng):
    """Resolve a validated-or-raw spec into a concrete board.

    Returns ``{"geo", "hexes", "robber_hex", "spec"}`` where ``hexes`` maps a
    hex id to ``{terrain, resource, number}``.
    """
    spec = validate(spec)

    if spec.get("tiles"):
        return _resolve_explicit(spec, rng)
    return _resolve_procedural(spec, rng)


def _build_with_ports(axials, spec, rng, coastal_hint=None):
    """Build geometry, choosing ports from the spec or auto-spreading them."""
    if spec.get("portsEdges"):
        # Ports pinned to specific hex edges (the map editor's format).
        board = geo.build_geometry(axials, port_types=[])
        qr_to_hid = {(h["q"], h["r"]): h["id"] for h in board["hexes"]}
        eports = []
        for pe in spec["portsEdges"]:
            hid = qr_to_hid.get((pe["q"], pe["r"]))
            if hid is None:
                raise MapError("Port tile [%d, %d] is not on the board." % (pe["q"], pe["r"]))
            eid = board["hex_edges"][hid][pe["dir"]]
            if not board["edges"][eid]["coastal"]:
                raise MapError("Ports must sit on coastal edges (tile [%d, %d], dir %d faces land)."
                               % (pe["q"], pe["r"], pe["dir"]))
            eports.append((eid, pe["type"]))
        return geo.add_edge_ports(board, eports)
    if spec.get("portsExplicit"):
        return geo.build_geometry(axials, ports_explicit=spec["portsExplicit"])
    if spec.get("ports"):
        return geo.build_geometry(axials, port_types=spec["ports"])
    # Auto: build with no ports here; they're placed thematically (by tile) once
    # the terrain is assigned (see _resolve_*).
    return geo.build_geometry(axials, port_types=[])


def _resolve_procedural(spec, rng):
    if spec.get("axials"):
        axials = [tuple(a) for a in spec["axials"]]
    else:
        axials = geo.hex_field(spec.get("radius", 2))

    board = _build_with_ports(axials, spec, rng)
    hex_ids = [h["id"] for h in board["hexes"]]
    n = len(hex_ids)

    terrains = terrain_pool(n, rng, gold=spec.get("gold", 0), beans=spec.get("beans", 0),
                            deserts=spec.get("deserts"))
    adjacency = _hex_adjacency(board)

    rng.shuffle(terrains)
    assignment = dict(zip(hex_ids, terrains))
    non_desert = [h for h in hex_ids if assignment[h] != C.TERRAIN_DESERT]
    # Place number tokens so the red 6/8 tokens are never adjacent. Random retry
    # almost never satisfies this on big boards, so place the reds constructively
    # onto a non-adjacent (independent) set of hexes, then fill the rest.
    number_of = _assign_numbers(non_desert, adjacency, rng)

    hexes, robber_hex = {}, None
    for h in hex_ids:
        terrain = assignment[h]
        hexes[h] = {
            "terrain": terrain,
            "resource": C.TERRAIN_RESOURCE[terrain],
            "number": number_of.get(h),
        }
        if terrain == C.TERRAIN_DESERT and robber_hex is None:
            robber_hex = h
    if robber_hex is None:
        robber_hex = hex_ids[0]
    if _is_auto_ports(spec):
        geo.add_edge_ports(board, _thematic_ports(board, hexes))
    return {"geo": board, "hexes": hexes, "robber_hex": robber_hex, "spec": spec}


def _resolve_explicit(spec, rng):
    tiles = spec["tiles"]
    axials = [(t["q"], t["r"]) for t in tiles]
    board = _build_with_ports(axials, spec, rng)

    # Map (q, r) -> hex id from the built geometry.
    qr_to_hid = {(h["q"], h["r"]): h["id"] for h in board["hexes"]}
    hexes, robber_hex = {}, None
    for t in tiles:
        hid = qr_to_hid[(t["q"], t["r"])]
        terrain = t["terrain"]
        hexes[hid] = {
            "terrain": terrain,
            "resource": C.TERRAIN_RESOURCE[terrain],
            "number": t["number"],
        }
        if terrain == C.TERRAIN_DESERT and robber_hex is None:
            robber_hex = hid

    robber = spec.get("robber")
    if isinstance(robber, dict) and (robber.get("q"), robber.get("r")) in qr_to_hid:
        robber_hex = qr_to_hid[(robber["q"], robber["r"])]
    elif isinstance(robber, int) and robber in hexes:
        robber_hex = robber
    if robber_hex is None:
        robber_hex = board["hexes"][0]["id"]
    if _is_auto_ports(spec):
        geo.add_edge_ports(board, _thematic_ports(board, hexes))
    return {"geo": board, "hexes": hexes, "robber_hex": robber_hex, "spec": spec}


# ----------------------------------------------------------------- helpers
def _hex_adjacency(board):
    adj = {h["id"]: set() for h in board["hexes"]}
    for e in board["edges"]:
        if len(e["hexes"]) == 2:
            a, b = e["hexes"]
            adj[a].add(b)
            adj[b].add(a)
    return adj


def _red_numbers_ok(number_of, adjacency):
    for hid, num in number_of.items():
        if num in C.RED_NUMBERS:
            for nb in adjacency.get(hid, ()):
                if number_of.get(nb) in C.RED_NUMBERS:
                    return False
    return True


def _assign_numbers(non_desert, adjacency, rng):
    """Assign number tokens to ``non_desert`` hexes so the red 6/8 tokens are
    never adjacent (constructively: pick an independent set for the reds)."""
    nums = number_pool(len(non_desert), rng)
    reds = [n for n in nums if n in C.RED_NUMBERS]
    nonreds = [n for n in nums if n not in C.RED_NUMBERS]
    order = list(non_desert)
    rng.shuffle(order)
    # Greedily collect hexes with no already-chosen red neighbour.
    red_hexes, chosen = set(), []
    for h in order:
        if len(chosen) >= len(reds):
            break
        if all(nb not in red_hexes for nb in adjacency.get(h, ())):
            chosen.append(h)
            red_hexes.add(h)
    # If the board is too dense to fit them all apart, place leftovers anywhere.
    if len(chosen) < len(reds):
        for h in order:
            if len(chosen) >= len(reds):
                break
            if h not in red_hexes:
                chosen.append(h)
                red_hexes.add(h)
    number_of = {}
    for i, h in enumerate(chosen):
        number_of[h] = reds[i]
    i = 0
    for h in non_desert:
        if h not in number_of:
            number_of[h] = nonreds[i]
            i += 1
    return number_of
