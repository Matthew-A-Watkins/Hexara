"""Static board geometry for the standard 19-hex board.

The board is built once as a pure graph of hexes, vertices (corners) and
edges, using axial hex coordinates converted to pixel positions. Shared
corners/edges between neighbouring hexes are de-duplicated so that a
settlement on a corner is correctly fed by every adjacent hex.

Coordinates are emitted in an arbitrary pixel space centred on the origin;
the client rescales them to fit its canvas. IDs are assigned deterministically
(sorted by position) so the server, the layout and the client always agree.
"""

import math

from . import constants as C

# Hex size: distance from a hex centre to one of its corners, in layout px.
HEX_SIZE = 60.0

# Floating-point rounding used to merge corners that are geometrically the
# same point. Distinct vertices are >= HEX_SIZE apart, so rounding to 2
# decimals is safe.
_QUANT = 2


def hex_field(radius):
    """Axial coordinates of a regular hexagon of the given radius.

    radius 2 -> the standard 19-hex island (rows 3-4-5-4-3); radius N has
    3N^2+3N+1 hexes. Used to build boards of any size.
    """
    coords = []
    for r in range(-radius, radius + 1):
        for q in range(-radius, radius + 1):
            if abs(q) <= radius and abs(r) <= radius and abs(q + r) <= radius:
                coords.append((q, r))
    return coords


def _axial_hexes():
    """The 19 axial coordinates of a radius-2 hexagon (rows of 3-4-5-4-3)."""
    return hex_field(2)


def _hex_center(q, r):
    """Pixel centre of a pointy-top hex at axial (q, r)."""
    x = HEX_SIZE * math.sqrt(3) * (q + r / 2.0)
    y = HEX_SIZE * 1.5 * r
    return (x, y)


def _hex_corner(cx, cy, i):
    """Pixel position of corner i (0..5) of a pointy-top hex."""
    angle = math.radians(60 * i - 30)
    return (cx + HEX_SIZE * math.cos(angle), cy + HEX_SIZE * math.sin(angle))


def _key(pt):
    return (round(pt[0], _QUANT), round(pt[1], _QUANT))


def build_geometry(axials=None, port_types=None, ports_explicit=None):
    """Build and return an immutable board graph for the given hex field.

    axials:         list of (q, r) hex coordinates. Defaults to the standard
                    19-hex island.
    port_types:     list of port type strings to spread evenly around the coast
                    (auto-placement). Defaults to the canonical 9-port sequence
                    when no explicit ports are given.
    ports_explicit: list of {"type", "vertices": [vid, vid]} placed exactly as
                    given (used by custom maps). Overrides port_types.

    Returns a dict with:
      hexes:     list of {id, q, r, cx, cy}
      vertices:  list of {id, x, y, hexes:[hid], adjacent:[vid], edges:[eid], coastal:bool}
      edges:     list of {id, v1, v2, hexes:[hid], coastal:bool}
      ports:     list of {type, edge, vertices:[vid,vid], x, y}
      hex_vertices: {hid: [vid x6]}   corner order
      hex_edges:    {hid: [eid x6]}
      bounds:    {minx, miny, maxx, maxy}
    """
    if axials is None:
        axials = _axial_hexes()
    if port_types is None and ports_explicit is None:
        port_types = list(C.PORT_SEQUENCE)

    hexes = []
    for hid, (q, r) in enumerate(axials):
        cx, cy = _hex_center(q, r)
        hexes.append({"id": hid, "q": q, "r": r, "cx": cx, "cy": cy})

    # --- Collect unique vertices (corners) -------------------------------
    # First pass: discover every corner position and the hexes touching it.
    vkey_to_pt = {}
    vkey_hexes = {}
    hex_corner_keys = {}  # hid -> [key x6] in corner order
    for h in hexes:
        keys = []
        for i in range(6):
            pt = _hex_corner(h["cx"], h["cy"], i)
            k = _key(pt)
            vkey_to_pt[k] = pt
            vkey_hexes.setdefault(k, set()).add(h["id"])
            keys.append(k)
        hex_corner_keys[h["id"]] = keys

    # Assign vertex ids deterministically (top-to-bottom, left-to-right).
    ordered_keys = sorted(vkey_to_pt.keys(), key=lambda k: (round(vkey_to_pt[k][1], 3),
                                                            round(vkey_to_pt[k][0], 3)))
    key_to_vid = {k: vid for vid, k in enumerate(ordered_keys)}

    vertices = []
    for vid, k in enumerate(ordered_keys):
        x, y = vkey_to_pt[k]
        vertices.append({
            "id": vid, "x": x, "y": y,
            "hexes": sorted(vkey_hexes[k]),
            "adjacent": set(), "edges": set(), "coastal": False,
        })

    hex_vertices = {h["id"]: [key_to_vid[k] for k in hex_corner_keys[h["id"]]]
                    for h in hexes}

    # --- Collect unique edges --------------------------------------------
    edge_map = {}  # frozenset({v1,v2}) -> {v1,v2, hexes:set}
    hex_edges = {}
    for h in hexes:
        vids = hex_vertices[h["id"]]
        elist = []
        for i in range(6):
            a = vids[i]
            b = vids[(i + 1) % 6]
            ek = frozenset((a, b))
            rec = edge_map.setdefault(ek, {"v": tuple(sorted((a, b))), "hexes": set()})
            rec["hexes"].add(h["id"])
            elist.append(ek)
        hex_edges[h["id"]] = elist

    # Assign edge ids deterministically (by midpoint position).
    def _emid(ek):
        a, b = edge_map[ek]["v"]
        return ((vertices[a]["x"] + vertices[b]["x"]) / 2.0,
                (vertices[a]["y"] + vertices[b]["y"]) / 2.0)

    ordered_ekeys = sorted(edge_map.keys(),
                           key=lambda ek: (round(_emid(ek)[1], 3), round(_emid(ek)[0], 3)))
    ekey_to_eid = {ek: eid for eid, ek in enumerate(ordered_ekeys)}

    edges = []
    for eid, ek in enumerate(ordered_ekeys):
        v1, v2 = edge_map[ek]["v"]
        hids = sorted(edge_map[ek]["hexes"])
        coastal = len(hids) == 1
        edges.append({"id": eid, "v1": v1, "v2": v2, "hexes": hids, "coastal": coastal})
        vertices[v1]["adjacent"].add(v2)
        vertices[v2]["adjacent"].add(v1)
        vertices[v1]["edges"].add(eid)
        vertices[v2]["edges"].add(eid)
        if coastal:
            vertices[v1]["coastal"] = True
            vertices[v2]["coastal"] = True

    hex_edges = {hid: [ekey_to_eid[ek] for ek in eks] for hid, eks in hex_edges.items()}

    # Normalise the set fields to sorted lists for JSON friendliness.
    for v in vertices:
        v["adjacent"] = sorted(v["adjacent"])
        v["edges"] = sorted(v["edges"])

    # --- Ports ------------------------------------------------------------
    ports = _make_ports(vertices, hexes, edges, port_types, ports_explicit)

    return _recompute_bounds({
        "hexes": hexes,
        "vertices": vertices,
        "edges": edges,
        "ports": ports,
        "hex_vertices": hex_vertices,
        "hex_edges": hex_edges,
        "bounds": None,
    })


# How far a port badge sits off its edge, in layout px. Far enough that the
# marker clears the tile artwork, close enough that the dock lines still read.
PORT_NUDGE = 36.0


def _port_marker(vertices, hexes, edge):
    """A port record on ``edge``, pushed outward AWAY FROM THE LAND.

    Outward is the normal away from the owning hex's centre (coastal edges
    have exactly one hex), so the badge never sits on a tile — nudging away
    from the board origin (the old behaviour) overlapped tiles whenever the
    coast didn't face the centre, e.g. on irregular islands.
    """
    v1, v2 = edge["v1"], edge["v2"]
    mx = (vertices[v1]["x"] + vertices[v2]["x"]) / 2.0
    my = (vertices[v1]["y"] + vertices[v2]["y"]) / 2.0
    if len(edge["hexes"]) == 1:
        h = hexes[edge["hexes"][0]]
        dx, dy = mx - h["cx"], my - h["cy"]
    else:  # inland edge (only possible via odd explicit specs): fall back
        dx, dy = mx, my
    d = math.hypot(dx, dy) or 1.0
    return {
        "edge": edge["id"],
        "vertices": [v1, v2],
        "x": mx + dx / d * PORT_NUDGE,
        "y": my + dy / d * PORT_NUDGE,
    }


def _recompute_bounds(board):
    """Refresh the board's bounding box (vertices + port markers)."""
    xs = [v["x"] for v in board["vertices"]] + [p["x"] for p in board["ports"]]
    ys = [v["y"] for v in board["vertices"]] + [p["y"] for p in board["ports"]]
    board["bounds"] = {"minx": min(xs), "miny": min(ys), "maxx": max(xs), "maxy": max(ys)}
    return board


def add_edge_ports(board, edge_ports):
    """Attach ports to specific edges of an already-built board.

    edge_ports: iterable of (edge_id, port_type). Used by custom maps that pin
    ports to exact hex edges. Recomputes the bounds afterwards."""
    for eid, ptype in edge_ports:
        rec = _port_marker(board["vertices"], board["hexes"], board["edges"][eid])
        rec["type"] = ptype
        board["ports"].append(rec)
    return _recompute_bounds(board)


def _make_ports(vertices, hexes, edges, port_types, ports_explicit):
    """Return port records, either placed exactly (ports_explicit) or spread
    evenly around the coast from a list of port type strings (port_types)."""
    if ports_explicit:
        # Map a vertex pair -> edge id so explicit ports can carry their edge.
        pair_to_eid = {}
        edge_by_id = {}
        for e in edges:
            pair_to_eid[frozenset((e["v1"], e["v2"]))] = e["id"]
            edge_by_id[e["id"]] = e
        out = []
        for spec in ports_explicit:
            v1, v2 = spec["vertices"]
            # Skip ports that reference vertices outside this board rather than
            # crashing (defensive: explicit ports come from hand-authored specs).
            if not (0 <= v1 < len(vertices) and 0 <= v2 < len(vertices)):
                continue
            eid = pair_to_eid.get(frozenset((v1, v2)))
            if eid is not None:
                rec = _port_marker(vertices, hexes, edge_by_id[eid])
            else:  # not a real edge: place it off the pair's midpoint
                mx = (vertices[v1]["x"] + vertices[v2]["x"]) / 2.0
                my = (vertices[v1]["y"] + vertices[v2]["y"]) / 2.0
                d = math.hypot(mx, my) or 1.0
                rec = {"edge": None, "vertices": [v1, v2],
                       "x": mx + mx / d * PORT_NUDGE, "y": my + my / d * PORT_NUDGE}
            rec["type"] = spec["type"]
            out.append(rec)
        return out
    return _place_ports(vertices, hexes, edges, port_types or [])


def _place_ports(vertices, hexes, edges, port_types):
    """Spread the given port types over coastal edges, evenly around the coast
    in clockwise order. With the canonical sequence on the standard board this
    reproduces the 9-port layout exactly."""
    coastal = [e for e in edges if e["coastal"]]
    if not coastal or not port_types:
        return []

    # Order coastal edges clockwise by the angle of their midpoint about the
    # board centre (origin). Screen y grows downward, so negate to go CW.
    def angle(e):
        mx = (vertices[e["v1"]]["x"] + vertices[e["v2"]]["x"]) / 2.0
        my = (vertices[e["v1"]]["y"] + vertices[e["v2"]]["y"]) / 2.0
        return math.atan2(-my, mx)

    coastal.sort(key=angle, reverse=True)
    n = len(coastal)
    count = len(port_types)

    ports = []
    used = set()
    for i, ptype in enumerate(port_types):
        idx = round(i * n / count) % n
        # Don't stack two ports on the same edge on small/odd coasts.
        while idx in used and len(used) < n:
            idx = (idx + 1) % n
        used.add(idx)
        e = coastal[idx]
        rec = _port_marker(vertices, hexes, e)
        rec["type"] = ptype
        ports.append(rec)
    return ports


# Build once at import; the geometry never changes.
GEOMETRY = build_geometry()
