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


def _axial_hexes():
    """The 19 axial coordinates of a radius-2 hexagon (rows of 3-4-5-4-3)."""
    coords = []
    for r in range(-2, 3):
        for q in range(-2, 3):
            if abs(q) <= 2 and abs(r) <= 2 and abs(q + r) <= 2:
                coords.append((q, r))
    return coords


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


def build_geometry():
    """Build and return the immutable board graph.

    Returns a dict with:
      hexes:     list of {id, q, r, cx, cy}
      vertices:  list of {id, x, y, hexes:[hid], adjacent:[vid], edges:[eid], coastal:bool}
      edges:     list of {id, v1, v2, hexes:[hid], coastal:bool}
      ports:     list of {type, edge, vertices:[vid,vid], x, y}
      hex_vertices: {hid: [vid x6]}   corner order
      hex_edges:    {hid: [eid x6]}
      bounds:    {minx, miny, maxx, maxy}
    """
    axials = _axial_hexes()

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
    ports = _place_ports(vertices, edges)

    xs = [v["x"] for v in vertices] + [p["x"] for p in ports]
    ys = [v["y"] for v in vertices] + [p["y"] for p in ports]
    bounds = {"minx": min(xs), "miny": min(ys), "maxx": max(xs), "maxy": max(ys)}

    return {
        "hexes": hexes,
        "vertices": vertices,
        "edges": edges,
        "ports": ports,
        "hex_vertices": hex_vertices,
        "hex_edges": hex_edges,
        "bounds": bounds,
    }


def _place_ports(vertices, edges):
    """Place the 9 standard ports on coastal edges, spread evenly around the
    coast in the canonical clockwise sequence."""
    coastal = [e for e in edges if e["coastal"]]

    # Order coastal edges clockwise by the angle of their midpoint about the
    # board centre (origin). Screen y grows downward, so negate to go CW.
    def angle(e):
        mx = (vertices[e["v1"]]["x"] + vertices[e["v2"]]["x"]) / 2.0
        my = (vertices[e["v1"]]["y"] + vertices[e["v2"]]["y"]) / 2.0
        return math.atan2(-my, mx)

    coastal.sort(key=angle, reverse=True)
    n = len(coastal)
    count = len(C.PORT_SEQUENCE)

    ports = []
    for i, ptype in enumerate(C.PORT_SEQUENCE):
        e = coastal[round(i * n / count) % n]
        mx = (vertices[e["v1"]]["x"] + vertices[e["v2"]]["x"]) / 2.0
        my = (vertices[e["v1"]]["y"] + vertices[e["v2"]]["y"]) / 2.0
        # Nudge the port marker outward from the board centre.
        d = math.hypot(mx, my) or 1.0
        ports.append({
            "type": ptype,
            "edge": e["id"],
            "vertices": [e["v1"], e["v2"]],
            "x": mx + mx / d * 28.0,
            "y": my + my / d * 28.0,
        })
    return ports


# Build once at import; the geometry never changes.
GEOMETRY = build_geometry()
