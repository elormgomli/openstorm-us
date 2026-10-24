"""Small pure-Python geometry helpers for city-scale checks on WGS84 GeoJSON.

Coordinates are projected to a local equirectangular plane (meters) centered on
the data. At city scale (tens of km) the distance error is well under 1 percent,
which is fine for 0.1 to 1 meter snap tolerances.
"""

import math
from collections import defaultdict

R = 6371008.8  # mean Earth radius, meters


class LocalProjector:
    def __init__(self, lon0, lat0):
        self.lon0, self.lat0 = lon0, lat0
        self.k = math.cos(math.radians(lat0))

    def __call__(self, lon, lat):
        return (math.radians(lon - self.lon0) * R * self.k, math.radians(lat - self.lat0) * R)


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _clean(c):
    if isinstance(c, (list, tuple)) and c and not isinstance(c[0], (list, tuple)):
        x, y = (_num(c[0]), _num(c[1])) if len(c) >= 2 else (None, None)
        return [x, y] if x is not None and y is not None and -180 <= x <= 180 and -90 <= y <= 90 else None
    out = [k for k in (_clean(v) for v in (c or [])) if k]
    return out or None


def clean_geometry(geom):
    """Coerce coordinates to numbers (some portals publish them as text); drop invalid points. Returns None if nothing valid."""
    if not isinstance(geom, dict) or not geom.get("type"):
        return None
    c = _clean(geom.get("coordinates"))
    if not c:
        return None
    t = geom["type"]
    if t == "Point" and not isinstance(c[0], (int, float)):
        c = c[0] if c and isinstance(c[0], list) else None
    if t in ("LineString", "MultiPoint") and c and not isinstance(c[0], list):
        c = [c]
    if t == "LineString" and (not c or len(c) < 2):
        return None
    return {"type": t, "coordinates": c} if c else None


def iter_coords(geom):
    if not geom:
        return
    t, c = geom.get("type"), geom.get("coordinates")
    if t == "Point":
        yield c
    elif t in ("LineString", "MultiPoint"):
        yield from c
    elif t in ("MultiLineString", "Polygon"):
        for part in c:
            yield from part
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                yield from ring


def representative_point(geom):
    pts = [p for p in iter_coords(geom) if p and len(p) >= 2]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def line_parts(geom):
    if not geom:
        return []
    if geom.get("type") == "LineString":
        return [geom["coordinates"]]
    if geom.get("type") == "MultiLineString":
        return list(geom["coordinates"])
    return []


def line_length_m(part, proj):
    pts = [proj(p[0], p[1]) for p in part]
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


class GridIndex:
    """Spatial hash of points for fixed-radius neighbor queries."""

    def __init__(self, cell):
        self.cell = cell
        self.grid = defaultdict(list)

    def _c(self, x, y):
        return (int(math.floor(x / self.cell)), int(math.floor(y / self.cell)))

    def add(self, x, y, payload):
        self.grid[self._c(x, y)].append((x, y, payload))

    def near(self, x, y, r):
        cx, cy = self._c(x, y)
        n = int(math.ceil(r / self.cell))
        for i in range(cx - n, cx + n + 1):
            for j in range(cy - n, cy + n + 1):
                for px, py, pl in self.grid.get((i, j), ()):
                    if (px - x) ** 2 + (py - y) ** 2 <= r * r:
                        yield pl
