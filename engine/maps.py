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
_VALID_TERRAINS = set(_RESOURCE_TERRAINS) | {C.TERRAIN_DESERT}
_VALID_NUMBERS = set(range(2, 13)) - {7}


# --------------------------------------------------------------------- pools
def terrain_pool(n, rng):
    """A list of ``n`` terrains in standard-ish proportions (>=1 desert)."""
    if n <= 0:
        return []
    deserts = max(1, round(n / 19.0))
    deserts = min(deserts, n - 1) if n > 1 else n
    remaining = n - deserts
    total_w = sum(_TERRAIN_WEIGHTS[t] for t in _RESOURCE_TERRAINS)
    counts = {t: int(remaining * _TERRAIN_WEIGHTS[t] / total_w) for t in _RESOURCE_TERRAINS}
    pool = [C.TERRAIN_DESERT] * deserts
    for t, c in counts.items():
        pool += [t] * c
    # Pad any rounding shortfall by cycling resource terrains.
    i = 0
    while len(pool) < n:
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


def auto_port_types(n_ports):
    """A port type list of length ``n_ports`` alternating generic 3:1 ports
    with one-per-resource 2:1 ports."""
    res_cycle = [C.WHEAT, C.ORE, C.SHEEP, C.BRICK, C.WOOD]
    out, ri = [], 0
    for i in range(n_ports):
        if i % 2 == 0:
            out.append(C.PORT_GENERIC)
        else:
            out.append(res_cycle[ri % len(res_cycle)])
            ri += 1
    return out


# ------------------------------------------------------------------- presets
def _preset_specs():
    return {
        "standard": {
            "name": "Standard Island",
            "description": "The classic 19-hex board, randomized each game.",
            "radius": 2,
        },
        "small": {
            "name": "Small Cove",
            "description": "A compact 7-hex board for fast 2-3 player duels.",
            "radius": 1,
        },
        "large": {
            "name": "Greater Isle",
            "description": "A 37-hex island for longer 4-6 player games.",
            "radius": 3,
        },
        "huge": {
            "name": "Continent",
            "description": "A sprawling 61-hex landmass for epic sessions.",
            "radius": 4,
        },
        "frontier": {
            "name": "Frontier (irregular)",
            "description": "A jagged, non-hexagonal island shape.",
            "axials": _frontier_axials(),
        },
    }


def _frontier_axials():
    """An irregular island: the radius-2 field minus three corners, plus two
    outcrops — a non-hexagonal coastline that still plays by the base rules."""
    field = set(map(tuple, geo.hex_field(2)))
    for corner in [(2, -2), (-2, 2), (2, 0)]:
        field.discard(corner)
    for outcrop in [(0, -3), (-1, 3)]:
        field.add(outcrop)
    return [list(c) for c in sorted(field)]


def list_presets():
    """Lobby-facing preset summaries."""
    out = []
    for pid, spec in _preset_specs().items():
        out.append({
            "id": pid,
            "name": spec["name"],
            "description": spec["description"],
            "tiles": _spec_tile_count(spec),
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
        if "portsExplicit" in spec or "ports" in spec:
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

    # Optional procedural overrides. A plain radius-2 island keeps the canonical
    # 9-port layout so the standard game looks exactly as before; other sizes
    # auto-spread a scaled set of ports.
    if spec.get("ports"):
        out["ports"] = _validate_port_types(spec["ports"])
    elif out.get("radius") == 2 and "axials" not in out:
        out["ports"] = list(C.PORT_SEQUENCE)
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
    deserts = 0
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
            deserts += 1
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
    if spec.get("portsExplicit"):
        return geo.build_geometry(axials, ports_explicit=spec["portsExplicit"])
    if spec.get("ports"):
        return geo.build_geometry(axials, port_types=spec["ports"])
    # Auto: build once portless to count the coast, then spread a scaled set.
    bare = geo.build_geometry(axials, port_types=[])
    coastal = sum(1 for e in bare["edges"] if e["coastal"])
    n_ports = max(2, round(coastal / 3.3))
    return geo.build_geometry(axials, port_types=auto_port_types(n_ports))


def _resolve_procedural(spec, rng):
    if spec.get("axials"):
        axials = [tuple(a) for a in spec["axials"]]
    else:
        axials = geo.hex_field(spec.get("radius", 2))

    board = _build_with_ports(axials, spec, rng)
    hex_ids = [h["id"] for h in board["hexes"]]
    n = len(hex_ids)

    terrains = terrain_pool(n, rng)
    adjacency = _hex_adjacency(board)

    assignment = None
    number_of = None
    for _ in range(300):  # retry until red 6/8 tokens are not adjacent
        rng.shuffle(terrains)
        assignment = dict(zip(hex_ids, terrains))
        non_desert = [h for h in hex_ids if assignment[h] != C.TERRAIN_DESERT]
        nums = number_pool(len(non_desert), rng)
        rng.shuffle(nums)
        number_of = dict(zip(non_desert, nums))
        if _red_numbers_ok(number_of, adjacency):
            break

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
