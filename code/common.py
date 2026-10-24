"""Shared helpers for the OpenStorm US pipeline: paths, polite HTTP, provenance, classification.

You do not need to open this file. It is used by the numbered scripts.
"""

import datetime
import gzip
import hashlib
import json
import os
import re
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
PROC = os.path.join(ROOT, "data", "processed")
PROV = os.path.join(RAW, "PROVENANCE.txt")
UA = "OpenStormUS/0.1 (open research benchmark of municipal stormwater data; contact in AUTHORS.json)"

_session = requests.Session()
_session.headers["User-Agent"] = UA


DEADLINE = [None]  # epoch seconds; set by scripts that run in time-limited windows


class Deadline(Exception):
    pass


def check_deadline():
    if DEADLINE[0] and time.time() > DEADLINE[0]:
        raise Deadline()


def get(url, params=None, tries=4, pause=0.5, timeout=60):
    """GET with retries and a short pause between calls so portals are not hammered."""
    last = None
    check_deadline()
    if DEADLINE[0]:
        timeout = max(5, min(timeout, DEADLINE[0] - time.time() + 5))
    for k in range(tries):
        try:
            r = _session.get(url, params=params, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            time.sleep(pause)
            return r
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(pause * (2 ** k) * 2)
    raise RuntimeError(f"GET failed after {tries} tries: {url} ({last})")


def get_json(url, params=None, **kw):
    r = get(url, params, **kw)
    try:
        js = r.json()
    except ValueError:
        raise RuntimeError(f"not JSON: {url}")
    if isinstance(js, dict) and "error" in js and isinstance(js["error"], dict):
        raise RuntimeError(f"service error {js['error'].get('code')}: {js['error'].get('message')} ({url})")
    return js


def provenance(path, url, note=""):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    line = " | ".join([datetime.date.today().isoformat(), os.path.relpath(path, RAW), f"{os.path.getsize(path)} bytes",
                       f"sha256:{h.hexdigest()}", url, note]).rstrip(" |")
    os.makedirs(RAW, exist_ok=True)
    with open(PROV, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def open_text(path, mode="r"):
    """Open .json or .json.gz transparently."""
    if path.endswith(".gz"):
        return gzip.open(path, mode + "t", encoding="utf-8", compresslevel=6) if "w" in mode else gzip.open(path, "rt", encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def load_json(path):
    with open_text(path) as f:
        return json.load(f)


def layer_file(meta_path):
    """The feature store that belongs to a .meta.json: a .pages folder, a .geojson.gz, or a .geojson."""
    for ext in (".pages", ".geojson.gz", ".geojson"):
        p = meta_path.replace(".meta.json", ext)
        if os.path.exists(p):
            return p
    return meta_path.replace(".meta.json", ".geojson")


def load_layer(meta_path, limit=None):
    """Features of a downloaded layer, whichever way it is stored (first `limit` only, if given)."""
    p = layer_file(meta_path)
    if os.path.isdir(p):
        feats = []
        for f in sorted(os.listdir(p)):
            if f.startswith("page_"):
                feats += load_json(os.path.join(p, f))
                if limit and len(feats) >= limit:
                    break
        return feats[:limit] if limit else feats
    feats = load_json(p)["features"]
    return feats[:limit] if limit else feats


def used_layers():
    """Layers marked USE in data/processed/layer_registry.csv, as {layer_id: exclusion or None}
    where exclusion is (field, regex) for features to drop. None if there is no registry yet."""
    import csv as _csv
    path = os.path.join(PROC, "layer_registry.csv")
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as f:
        return {r["layer_id"]: ((r["exclude_field"], r["exclude_pattern"]) if r.get("exclude_field") else None)
                for r in _csv.DictReader(f) if r["final"] == "USE"}


def drop_excluded(feats, exclusion):
    """Remove features whose exclusion field matches the exclusion pattern (e.g. ditch features)."""
    if not exclusion:
        return feats
    field, pat = exclusion
    rx = re.compile(pat)
    return [f for f in feats if not rx.search(str((f.get("properties") or {}).get(field, "")))]


def save_json(obj, path, url=None, note=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with (gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) if path.endswith(".gz") else open(tmp, "w", encoding="utf-8")) as f:
        json.dump(obj, f)
    os.replace(tmp, path)  # atomic: a stopped run never leaves a half-written file
    if url:
        provenance(path, url, note)


def slug(s, n=60):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:n] or "x"


# ---------------- classification (rules reviewed by Elorm for v1.0) ----------------

LAYER_KEYWORDS = {
    # order matters: first match wins
    "detention_basin": [r"detention", r"retention", r"\bpond", r"\bbmp", r"stormwater control", r"scm\b", r"(?<!catch )\bbasin"],
    "outfall": [r"outfall", r"discharge point", r"headwall", r"endwall", r"end ?wall", r"\boutlet"],
    "inlet": [r"inlet", r"catch ?basin", r"\bcb\b", r"grate", r"curb opening", r"drop inlet"],
    "manhole": [r"manhole", r"\bmh\b", r"junction", r"access ?structure", r"\bvault"],
    "pipe": [r"pipe", r"\bmains?\b", r"gravity", r"culvert", r"conduit", r"storm ?lines?", r"sewer ?lines?", r"lateral"],
}
STORM_WORDS = r"storm|drain|stormwater|swm|sw_|ms4|catch|inlet|outfall|culvert|detention|retention"
NOT_STORM = r"sanitary|wastewater|\bsewershed|water ?main|hydrant|potable|reclaimed|gas |electric|fiber|sidewalk|watershed boundar|floodplain|fema|parcel"


def classify_layer(name, geometry_type=None, context=""):
    """Return a layer type or None. geometry_type is an esriGeometry* string or GeoJSON type."""
    text = f"{context} {name}".lower()
    nm = name.lower()
    # The layer's own name decides first: "Sanitary Mains" inside a "Storm Sewer" service is not storm.
    if re.search(NOT_STORM, nm) and not re.search(r"storm|drain", nm):
        return None
    if re.search(NOT_STORM, text) and not re.search(r"storm|drain", text):
        return None
    g = (geometry_type or "").lower()
    for lt, pats in LAYER_KEYWORDS.items():
        if any(re.search(p, name.lower()) for p in pats):
            if lt == "pipe" and g and "line" not in g:
                continue
            if lt in ("inlet", "manhole", "outfall") and g and "point" not in g:
                continue
            if lt == "detention_basin" and g and "polygon" not in g:
                continue
            return lt
    if g and "line" in g and re.search(STORM_WORDS, text):
        return "pipe"  # e.g. a line layer simply called "Storm Sewer"
    return None


PUBLISHER_PATTERNS = [
    ("county", r"\b(county|parish|borough)\b"),
    ("city", r"\b(city|town|village|township|municipality)\s+of\b|\bcity\b|\btown\b|\bvillage\b"),
    ("utility_district", r"utility|utilities|district|authority|\bmsd\b|commission|conservancy"),
]
EXCLUDE_PUBLISHER = r"universit|college|school|student|engineer|consult|\binc\b|\bllc\b|\bltd\b|esri|associates|\bcorp|partners|canada|ontario|quebec|alberta|british columbia|\bnsw\b|australia"


def classify_publisher(name):
    n = (name or "").lower()
    if not n.strip():
        return "unclassified"
    if re.search(EXCLUDE_PUBLISHER, n):
        return "excluded"
    for kind, pat in PUBLISHER_PATTERNS:
        if re.search(pat, n):
            return kind
    return "unclassified"


STATES = {"alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO", "connecticut": "CT",
          "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
          "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
          "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
          "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
          "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
          "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
          "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
          "puerto rico": "PR"}
ABBR = set(STATES.values())
NOISE = (r"\b(the|city|town|village|township|county|parish|borough|municipality|consolidated|unified|metropolitan|government|govt|gov|"
         r"gis|open ?data|opendata|hub|maps?|mapping|online|arcgis|ago|agol|portal|geospatial|geographic|information|systems?|data|"
         r"public works|works|engineering|stormwater|storm ?water|utilities|utility|department|dept|division|services?|management|"
         r"office|program|official|site|web|and|of|for|in|on|at|com|gov|org|net|us)\b")


def _ascii(s):
    import unicodedata
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def parse_state(name):
    """Return a USPS code if the publisher name states one ('Pierce County, Washington', 'Bend, OR')."""
    n = _ascii(name or "").lower()
    for full in sorted(STATES, key=len, reverse=True):
        if re.search(r"(,|\(|\s-\s|\s)\s*" + full + r"\b", n) and not re.match(r"^\s*" + full, n):
            if not (full == "washington" and re.search(r"washington county|washington (city|township)", n)):
                return STATES[full]
    m = re.search(r"(?:,|\(|\s)\s*([A-Z]{2})\b\)?\s*$", _ascii(name or "").strip())
    if m and m.group(1) in ABBR:
        return m.group(1)
    return ""


def clean_place_name(name):
    """'City of Round Rock, TX' -> 'round rock'; 'Tarrant County GIS' -> 'tarrant'."""
    n = _ascii(name or "").lower()
    n = re.sub(r"\(.*?\)", " ", n)
    n = re.sub(r"\b[a-z0-9-]+\.(gov|org|com|us|net)\b", " ", n)       # domains
    n = re.split(r"\s[-:|]\s", n)[0] if re.search(r"\b(city|town|village|county|parish)\b", re.split(r"\s[-:|]\s", n)[0]) else n
    n = re.sub(r",.*$", " ", n)
    n = re.sub(r"\s(" + "|".join(a.lower() for a in ABBR) + r")\s*$", " ", n.strip())  # trailing 'NC'
    if re.search(r"\bwashington,?\s*d\.?\s?c\b", _ascii(name or "").lower()):
        return "washington"
    for full in sorted(STATES, key=len, reverse=True):
        n = re.sub(r"\b" + full + r"$", " ", n.strip()) if not n.strip() == full else n
    n = re.sub(NOISE, " ", n)
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(r"\b(st|ste)\b", "saint", n)
    return re.sub(r"\s+", " ", n).strip()


def county_keys(name):
    """Possible county names inside a publisher name: 'Tallahassee-Leon County GIS' -> ['leon', 'tallahassee leon']."""
    n = re.sub(r"[^a-z ]", " ", _ascii(name or "").lower())
    out = []
    for m in re.finditer(r"((?:[a-z]+ ){1,3})(county|parish|borough)\b", n + " "):
        words = [w for w in m.group(1).split() if w not in ("the", "of", "and")]
        for k in range(1, len(words) + 1):
            out.append(" ".join(words[-k:]))
    m = re.search(r"\b(county|parish) of ([a-z ]+)", n)
    if m:
        out.append(clean_place_name(m.group(2)))
    return [k for k in dict.fromkeys(out) if len(k) >= 3]


OPEN_CONVEYANCE = r"swale|channel|open drain|open conveyance|canal|flume|ditch|waterway"


def is_open_conveyance(meta):
    """Swales, channels, canals, flumes: open conveyances, for which pipe diameter does not apply (Elorm, 2026-10-20)."""
    # The layer's own name decides, never the service name: Grand Prairie TX keeps "Existing Storm Sewer" in a service called "Ditches".
    name = re.sub(r"[_\-.]+", " ", str(meta.get("name", "")).lower())
    return (meta.get("layer_type") == "pipe" and bool(re.search(OPEN_CONVEYANCE, name))
            and not re.search(r"\bpipes?\b|culvert|\bmains?\b|closed|sewer|conduit|gravity", name))


def today():
    return datetime.date.today().isoformat()
