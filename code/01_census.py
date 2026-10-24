"""Step 1: census of US municipal storm-sewer publishers.

Plain language: searches the two big open-data catalogs (ArcGIS Hub and Socrata) for
storm-drain map layers, keeps the ones published by US cities, counties and stormwater
utilities, and matches each publisher to its official Census place or county.

Outputs
  data/raw/catalog/...                     every catalog page as fetched (with provenance)
  data/raw/census/*.zip                    Census Gazetteer files used for matching
  data/processed/census_candidates.csv     every catalog hit with its classification
  data/processed/publishers.csv            one row per matched publisher (the census)
  data/processed/census_review.csv         hits the rules could not decide; for Elorm

Run:  python code/01_census.py [--max-pages N]   (N limits pages per query, for testing)
"""

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import zipfile
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PROC, RAW, classify_layer, classify_publisher, clean_place_name, county_keys, parse_state, get, get_json, provenance, save_json, slug  # noqa: E402

HUB_URL = "https://hub.arcgis.com/api/search/v1/collections/dataset/items"
SOCRATA_URL = "https://api.us.socrata.com/api/catalog/v1"
GAZ_YEAR = "2024"
GAZ = {
    "place": f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{GAZ_YEAR}_Gazetteer/{GAZ_YEAR}_Gaz_place_national.zip",
    "county": f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{GAZ_YEAR}_Gazetteer/{GAZ_YEAR}_Gaz_counties_national.zip",
}
QUERIES = ["storm sewer", "storm drain", "stormwater inlet", "storm inlet", "catch basin", "storm manhole",
           "stormwater pipe", "storm pipe", "storm main", "stormwater outfall", "outfall", "culvert",
           "detention pond", "stormwater", "drainage structure", "MS4"]
HUB_TYPES = {"Feature Service", "Feature Layer", "Map Service"}
MATCH_KM = 60.0


# ---------------- catalogs ----------------

def harvest_hub(max_pages):
    items = {}
    for q in QUERIES:
        start, page = 1, 0
        while True:
            params = {"q": q, "limit": 100, "startindex": start}
            cache = os.path.join(RAW, "catalog", "hub", f"{slug(q)}_{start:05d}.json")
            if os.path.exists(cache):
                js = json.load(open(cache, encoding="utf-8"))
            else:
                js = get_json(HUB_URL, params)
                save_json(js, cache, f"{HUB_URL}?q={quote(q)}&limit=100&startindex={start}", "ArcGIS Hub Search API v1")
            feats = js.get("features", [])
            for f in feats:
                p = f.get("properties", {})
                iid = f.get("id") or p.get("id")
                if not iid or iid in items:
                    continue
                items[iid] = {
                    "catalog": "hub", "item_id": iid, "title": p.get("title") or p.get("name") or "",
                    "type": p.get("type") or "", "url": p.get("url") or "",
                    "publisher": p.get("source") or p.get("orgName") or p.get("organization") or "",
                    "owner": p.get("owner") or "", "extent": _centroid(f.get("geometry")),
                    "query": q, "tags": "; ".join(p.get("tags") or [])[:300],
                    "org_id": p.get("orgId") or "", "license_info": _strip_html(p.get("licenseInfo") or "")[:600],
                    "access_info": (p.get("accessInformation") or "")[:200],
                }
            page += 1
            n_total = js.get("numberMatched") or 0
            start += 100
            if not feats or start > n_total or (max_pages and page >= max_pages) or start > 10000:
                break
        print(f"  hub '{q}': {len(items)} unique items so far", flush=True)
    return list(items.values())


def harvest_socrata(max_pages):
    items = {}
    for q in QUERIES:
        offset, page = 0, 0
        while True:
            params = {"q": q, "limit": 100, "offset": offset, "only": "dataset,map,filter"}
            cache = os.path.join(RAW, "catalog", "socrata", f"{slug(q)}_{offset:05d}.json")
            if os.path.exists(cache):
                js = json.load(open(cache, encoding="utf-8"))
            else:
                js = get_json(SOCRATA_URL, params)
                save_json(js, cache, f"{SOCRATA_URL}?q={quote(q)}&limit=100&offset={offset}", "Socrata Discovery API")
            res = js.get("results", [])
            for r in res:
                rs, md = r.get("resource", {}), r.get("metadata", {})
                key = f"{md.get('domain')}/{rs.get('id')}"
                if key in items:
                    continue
                items[key] = {
                    "catalog": "socrata", "item_id": rs.get("id", ""), "title": rs.get("name", ""),
                    "type": rs.get("type", ""), "url": f"https://{md.get('domain')}/resource/{rs.get('id')}",
                    "publisher": rs.get("attribution") or md.get("domain", ""), "owner": md.get("domain", ""),
                    "domain": md.get("domain", ""), "extent": None, "query": q,
                    "tags": "; ".join((r.get("classification") or {}).get("domain_tags") or [])[:300],
                }
            page += 1
            offset += 100
            if not res or offset >= (js.get("resultSetSize") or 0) or (max_pages and page >= max_pages):
                break
        print(f"  socrata '{q}': {len(items)} unique items so far", flush=True)
    return list(items.values())


def _strip_html(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t).replace("&nbsp;", " ")).strip()


def resolve_org_names(items):
    """Hub items with no publisher name: look up the ArcGIS Online organization name from orgId (cached)."""
    cache_path = os.path.join(RAW, "catalog", "hub_orgs.json")
    cache = json.load(open(cache_path, encoding="utf-8")) if os.path.exists(cache_path) else {}
    need = sorted({it["org_id"] for it in items if it["catalog"] == "hub" and not it["publisher"] and it.get("org_id")} - set(cache))
    for k, oid in enumerate(need):
        try:
            js = get_json(f"https://www.arcgis.com/sharing/rest/portals/{oid}", {"f": "json"})
            cache[oid] = js.get("name") or ""
        except RuntimeError:
            cache[oid] = ""
        if k % 25 == 24:
            save_json(cache, cache_path)
    save_json(cache, cache_path, "https://www.arcgis.com/sharing/rest/portals/<orgId>?f=json", "org names for items without a source")
    for it in items:
        if it["catalog"] == "hub" and not it["publisher"]:
            it["publisher"] = cache.get(it.get("org_id"), "") or it.get("access_info", "")
            it["publisher_from"] = "org lookup" if cache.get(it.get("org_id")) else ("access info" if it.get("access_info") else "")
    print(f"  resolved {sum(1 for v in cache.values() if v)} organization names", flush=True)


US_BOXES = [(-125, 24, -66, 50), (-180, 51, -129, 72), (-161, 18, -154, 23), (-68, 17.5, -65, 18.6), (145, 13, 146, 21)]


def in_us(pt):
    return pt is None or any(x0 <= pt[0] <= x1 and y0 <= pt[1] <= y1 for x0, y0, x1, y1 in US_BOXES)


def _centroid(geom):
    pts = []
    def walk(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            pts.append(c)
        elif isinstance(c, (list, tuple)):
            for x in c:
                walk(x)
    if geom:
        walk(geom.get("coordinates"))
    pts = [p for p in pts if -180 <= p[0] <= 180 and -90 <= p[1] <= 90]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


# ---------------- Census gazetteer ----------------

def load_gazetteer():
    out = {}
    for kind, url in GAZ.items():
        path = os.path.join(RAW, "census", os.path.basename(url))
        if not os.path.exists(path):
            r = get(url, timeout=180)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "wb") as f:
                f.write(r.content)
            os.replace(path + ".tmp", path)
            provenance(path, url, f"Census Gazetteer {GAZ_YEAR} ({kind})")
        with zipfile.ZipFile(path) as z:
            name = [n for n in z.namelist() if n.endswith(".txt")][0]
            text = z.read(name).decode("latin-1")
        rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
        clean = []
        for r in rows:
            r = {k.strip(): (v or "").strip() for k, v in r.items()}
            nm = r["NAME"]
            base = re.sub(r"\s+(city|town|village|borough|cdp|municipality|city and borough|consolidated government.*|metropolitan government.*|unified government.*|county|parish|census area|municipio)$", "", nm, flags=re.I)
            clean.append({"kind": kind, "geoid": r["GEOID"], "name": nm, "state": r["USPS"],
                          "key": clean_place_name(base), "lat": float(r["INTPTLAT"]), "lon": float(r["INTPTLONG"]),
                          "lsad_cdp": nm.lower().endswith(" cdp")})
        out[kind] = clean
    return out


def km(a, b):
    lat = math.radians((a[1] + b[1]) / 2)
    return math.hypot((a[0] - b[0]) * math.cos(lat), a[1] - b[1]) * 111.195


def match_publisher(item, gaz, by_key):
    """Return (gaz row, method, distance_km) or (None, reason, None)."""
    cls = classify_publisher(item["publisher"] or item["owner"])
    if cls == "excluded":
        return None, "excluded publisher", None
    text = item["publisher"] or ""
    if item["catalog"] == "socrata" and not clean_place_name(text):
        text = re.sub(r"^(data|datahub|opendata|gis)\.|\.(gov|org|com|us|net)$|cityof|countyof", " ", item.get("domain", "")).replace(".", " ")
    state = parse_state(text)
    low = text.lower()
    tries = []
    if cls == "county" or re.search(r"\b(county|parish|borough)\b", low):
        tries += [("county", k) for k in county_keys(text)]
    key = clean_place_name(text)
    if len(key) >= 3:
        if cls == "county":
            tries += [("county", key)]
        elif cls == "city":
            tries += [("place", key)]  # a city never falls back to a same-named county
        else:
            tries += [("place", key), ("county", key)]
    if not tries:
        return None, "publisher name too short to match", None
    reasons = []
    for kind, k in tries:
        cands = [c for c in by_key.get((kind, k), []) if not c["lsad_cdp"]]
        if state:
            cands = [c for c in cands if c["state"] == state]
        if kind == "county" and "city" not in low:
            cands = [c for c in cands if not c["name"].lower().endswith(" city")] or cands
        if not cands:
            reasons.append(f"no Census {kind} '{k}'" + (f" in {state}" if state else ""))
            continue
        if item.get("extent"):
            best = min(cands, key=lambda c: km(item["extent"], (c["lon"], c["lat"])))
            d = km(item["extent"], (best["lon"], best["lat"]))
            if d <= MATCH_KM:
                return best, "name+location" + ("+state" if state else ""), round(d, 1)
            reasons.append(f"'{k}' matched but {d:.0f} km away")
            continue
        if len(cands) == 1:
            return cands[0], "name only (unique)" + ("+state" if state else ""), None
        reasons.append(f"'{k}' matches {len(cands)} Census units; needs location")
    return None, "; ".join(reasons[:3]), None


def nearest_county_km(pt, grid):
    if pt is None:
        return 0.0
    gx, gy = int(math.floor(pt[0])), int(math.floor(pt[1]))
    best = 1e9
    for i in range(gx - 2, gx + 3):
        for j in range(gy - 2, gy + 3):
            for c in grid.get((i, j), ()):
                best = min(best, km(pt, (c["lon"], c["lat"])))
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=0)
    a = ap.parse_args()
    os.makedirs(PROC, exist_ok=True)

    print("Searching ArcGIS Hub ...", flush=True)
    items = harvest_hub(a.max_pages)
    print("Searching Socrata ...", flush=True)
    items += harvest_socrata(a.max_pages)
    print("Looking up organization names for unnamed items ...", flush=True)
    resolve_org_names(items)
    print("Loading Census Gazetteer ...", flush=True)
    gaz = load_gazetteer()
    by_key, by_geoid = {}, {}
    for kind, rows in gaz.items():
        for r in rows:
            by_key.setdefault((kind, r["key"]), []).append(r)
            by_geoid[("C" if kind == "county" else "P") + r["geoid"]] = r
    cgrid = {}
    for r in gaz["county"]:
        cgrid.setdefault((int(math.floor(r["lon"])), int(math.floor(r["lat"]))), []).append(r)

    # Elorm's earlier rulings on publishers (one row per publisher name), kept across re-runs
    review_path = os.path.join(PROC, "census_review.csv")
    rulings = {}
    if os.path.exists(review_path):
        with open(review_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get("elorm_decision") or "").strip():
                    rulings[r["publisher"]] = r

    cand_rows, review = [], {}
    for it in items:
        storm_like = bool(re.search(r"storm|drain|inlet|catch|outfall|culvert|detention|retention|ms4|sewer", f"{it['title']} {it['tags']}", re.I))
        layer_guess = classify_layer(it["title"]) or ("multi/unknown" if storm_like else "")
        pub_class = classify_publisher(it["publisher"] or it["owner"])
        row = dict(it, extent=json.dumps(it["extent"]) if it["extent"] else "", publisher_class=pub_class,
                   layer_guess=layer_guess, include="", reason="", geoid="", census_name="", state="", match_method="", match_km="")
        if it["catalog"] == "hub" and it["type"] not in HUB_TYPES:
            row["reason"] = f"item type {it['type']} not a queryable service"
        elif it["catalog"] == "socrata" and it["type"] not in ("dataset", "map", "filter"):
            row["reason"] = f"socrata type {it['type']}"
        elif not storm_like:
            row["reason"] = "title/tags not storm related"
        elif not it["url"]:
            row["reason"] = "no service URL"
        elif not in_us(it["extent"]) or nearest_county_km(it["extent"], cgrid) > 120:
            row["reason"] = "extent outside the US"
        else:
            rule = rulings.get(it["publisher"])
            dec = (rule or {}).get("elorm_decision", "").strip().upper()
            g, how, d = (None, "", None)
            if dec == "EXCLUDE":
                row["reason"] = "excluded by reviewer"
            elif dec == "INCLUDE" and by_geoid.get((rule.get("elorm_geoid") or "").strip()):
                g, how = by_geoid[rule["elorm_geoid"].strip()], "reviewer"
            else:
                g, how, d = match_publisher(it, gaz, by_key)
            if g:
                row.update(include="yes", geoid=("C" if g["kind"] == "county" else "P") + g["geoid"], census_name=g["name"],
                           state=g["state"], match_method=how, match_km=d if d is not None else "")
                if pub_class == "utility_district":
                    row["reason"] = "utility/district matched to its area; flagged as separate group"
            elif dec != "EXCLUDE":
                row["reason"] = how
                if pub_class != "excluded" and it["publisher"]:
                    rv = review.setdefault(it["publisher"], {"publisher": it["publisher"], "publisher_class": pub_class, "n_items": 0,
                                                             "why_unmatched": how, "sample_titles": [], "sample_url": it["url"],
                                                             "extent": row["extent"], "elorm_decision": "", "elorm_geoid": "", "elorm_note": ""})
                    rv["n_items"] += 1
                    if len(rv["sample_titles"]) < 4:
                        rv["sample_titles"].append(it["title"][:60])
        cand_rows.append(row)

    cols = ["catalog", "item_id", "title", "type", "url", "publisher", "owner", "publisher_class", "layer_guess",
            "include", "reason", "geoid", "census_name", "state", "match_method", "match_km", "extent", "query", "tags", "domain",
            "license_info", "access_info"]
    _write(os.path.join(PROC, "census_candidates.csv"), cand_rows, cols)
    rv_rows = [dict(r, sample_titles=" | ".join(r["sample_titles"])) for r in review.values()]
    for name, r in rulings.items():  # keep rulings even if the publisher now matches on its own
        if name not in review:
            rv_rows.append({k: r.get(k, "") for k in ("publisher", "publisher_class", "n_items", "why_unmatched", "sample_titles",
                                                       "sample_url", "extent", "elorm_decision", "elorm_geoid", "elorm_note")})
    rv_rows.sort(key=lambda r: (-int(r["n_items"] or 0), r["publisher"]))
    _write(review_path, rv_rows, ["publisher", "publisher_class", "n_items", "why_unmatched", "sample_titles", "sample_url", "extent",
                                  "elorm_decision", "elorm_geoid", "elorm_note"])

    pubs = {}
    for r in cand_rows:
        if r["include"] == "yes":
            p = pubs.setdefault(r["geoid"], {"geoid": r["geoid"], "census_name": r["census_name"], "state": r["state"],
                                             "publisher_class": r["publisher_class"], "publisher_names": set(), "n_items": 0, "catalogs": set()})
            p["publisher_names"].add(r["publisher"])
            p["n_items"] += 1
            p["catalogs"].add(r["catalog"])
            if r["publisher_class"] in ("city", "county"):
                p["publisher_class"] = r["publisher_class"]
    prow = [dict(p, publisher_names="; ".join(sorted(p["publisher_names"])), catalogs="; ".join(sorted(p["catalogs"]))) for p in pubs.values()]
    _write(os.path.join(PROC, "publishers.csv"), sorted(prow, key=lambda r: (r["state"], r["census_name"])),
           ["geoid", "census_name", "state", "publisher_class", "publisher_names", "n_items", "catalogs"])

    print(f"\nCatalog hits: {len(items)}  |  included: {sum(1 for r in cand_rows if r['include'] == 'yes')}  |  "
          f"publishers: {len(pubs)}  |  publishers for review: {len(rv_rows)}")


def _write(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
