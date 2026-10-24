"""Step 2: download every storm-sewer layer the census found.

Plain language: for each publisher in the census, opens each map service, finds the
storm-drain layers inside it (inlets, manholes, pipes, outfalls, basins), and saves a full
copy of each, with its field list, its stated terms of use, and the download date.

Inputs   data/processed/census_candidates.csv (rows with include = yes)
Outputs  data/raw/layers/<geoid>/<layer>.geojson and <layer>.meta.json (with provenance)
         data/processed/layer_inventory.csv   one row per service layer seen, kept or not
         data/processed/source_terms.csv      stated license / terms per catalog item

Run:  python code/02_fetch_layers.py [--budget SECONDS] [--max-features N] [--workers 4]

Resumable at every level. Work is checkpointed per service, per layer, and per page of
features, so the script can run in short windows (--budget) and pick up where it stopped.
It prints ALL DONE when nothing is left.
"""

import argparse
import concurrent.futures as cf
import csv
import glob
import json
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
from common import PROC, RAW, Deadline, classify_layer, get_json, load_json, provenance, save_json, slug, today
import shutil  # noqa: E402

LOCK = threading.Lock()
STATE = os.path.join(RAW, "_state")


# ---------------- Esri JSON -> GeoJSON ----------------

def esri_to_geojson_geometry(g):
    if not g:
        return None
    if "x" in g and "y" in g:
        if g["x"] is None or g["y"] is None or g["x"] != g["x"]:
            return None
        return {"type": "Point", "coordinates": [g["x"], g["y"]]}
    if "points" in g:
        return {"type": "MultiPoint", "coordinates": g["points"]} if g["points"] else None
    if "paths" in g:
        paths = [p for p in g["paths"] if len(p) >= 2]
        if not paths:
            return None
        return {"type": "LineString", "coordinates": paths[0]} if len(paths) == 1 else {"type": "MultiLineString", "coordinates": paths}
    if "rings" in g:
        rings = [r for r in g["rings"] if len(r) >= 4]
        return {"type": "Polygon", "coordinates": rings} if rings else None
    return None


def cached_json(key, url, params=None, **kw):
    """GET JSON once; later runs read the saved copy."""
    path = os.path.join(STATE, "http", slug(key, 150) + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    js = get_json(url, params, **kw)
    save_json(js, path)
    return js


# ---------------- ArcGIS REST ----------------

def service_layers(url):
    """Expand a service or layer URL into a list of (layer_url, layer_json)."""
    url = url.split("?")[0].rstrip("/")
    meta = cached_json("svc " + url, url, {"f": "json"})
    if re.search(r"/(FeatureServer|MapServer)/\d+$", url):
        return [(url, meta)]
    out = []
    for lyr in (meta.get("layers") or []):
        if lyr.get("subLayerIds"):  # group layer
            continue
        lu = f"{url}/{lyr['id']}"
        try:
            out.append((lu, cached_json("lyr " + lu, lu, {"f": "json"})))
        except Deadline:
            raise
        except RuntimeError as e:
            out.append((lu, {"name": lyr.get("name", ""), "_error": str(e)}))
    return out


def fetch_arcgis_layer(lu, meta, max_features, partdir):
    """Download a layer page by page into partdir; resumes from saved pages."""
    oid = meta.get("objectIdField") or next((f["name"] for f in meta.get("fields", []) if f.get("type") == "esriFieldTypeOID"), "OBJECTID")
    ids_path = os.path.join(partdir, "ids.json")
    have = set(os.listdir(partdir)) if os.path.isdir(partdir) else set()
    if "ids.json" in have:
        ids = load_json(ids_path)
    else:
        ids = sorted(get_json(lu + "/query", {"where": "1=1", "returnIdsOnly": "true", "f": "json"}, timeout=120).get("objectIds") or [])
        save_json(ids, ids_path)
    reported = len(ids)
    ids = ids[:max_features] if max_features else ids
    step = max(1, min(int(meta.get("maxRecordCount") or 1000), 1000))
    n = 0
    for k, i in enumerate(range(0, len(ids), step)):
        legacy = f"page_{k:05d}.json.gz"
        if legacy in have:  # page saved by an earlier version: add its count to the name
            cnt = len(load_json(os.path.join(partdir, legacy)))
            os.replace(os.path.join(partdir, legacy), os.path.join(partdir, f"page_{k:05d}_n{cnt}.json.gz"))
            have.add(f"page_{k:05d}_n{cnt}.json.gz")
        done = [h for h in have if h.startswith(f"page_{k:05d}_")]
        if done:
            n += int(done[0].split("_n")[1].split(".")[0])
            continue
        lo, hi = ids[i], ids[min(i + step, len(ids)) - 1]
        js = get_json(lu + "/query", {"where": f"{oid} >= {lo} AND {oid} <= {hi}", "outFields": "*", "outSR": "4326",
                                      "returnGeometry": "true", "f": "json"}, timeout=120)
        feats = [{"type": "Feature", "geometry": esri_to_geojson_geometry(f.get("geometry")), "properties": f.get("attributes", {})}
                 for f in js.get("features", [])]
        save_json(feats, os.path.join(partdir, f"page_{k:05d}_n{len(feats)}.json.gz"))
        n += len(feats)
    return n, reported


# ---------------- Socrata ----------------

def fetch_socrata(row, max_features, partdir):
    dom, rid = row["domain"], row["item_id"]
    view = cached_json(f"view {dom} {rid}", f"https://{dom}/api/views/{rid}.json")
    cols = view.get("columns", [])
    if not any(c.get("dataTypeName") in ("point", "line", "multiline", "polygon", "multipolygon", "location") for c in cols):
        raise RuntimeError("no geometry column in Socrata dataset")
    off, lim, k = 0, 50000, 0
    while True:
        page = os.path.join(partdir, f"page_{k:05d}.json.gz")
        if os.path.exists(page):
            fs = load_json(page)
        else:
            fs = get_json(f"https://{dom}/resource/{rid}.geojson", {"$limit": lim, "$offset": off}, timeout=150).get("features", [])
            save_json(fs, page)
        off += lim
        k += 1
        if len(fs) < lim or (max_features and off >= max_features):
            break
    feats = []
    for page in sorted(glob.glob(os.path.join(partdir, "page_*.json*"))):
        feats += load_json(page)
    fields = [{"name": c.get("fieldName"), "alias": c.get("name"), "type": c.get("dataTypeName")} for c in cols]
    return feats[:max_features] if max_features else feats, fields, view.get("name", row["title"])


# ---------------- driver ----------------

def service_key(row):
    return slug(row["url"].split("://")[-1], 150)


def process_service(row, max_features):
    """Process one catalog item fully, or raise Deadline. On completion writes its inventory record."""
    done_path = os.path.join(STATE, "done", service_key(row) + ".json")
    geoid = row["geoid"]
    outdir = os.path.join(RAW, "layers", geoid)
    recs = []
    try:
        if row["catalog"] == "socrata":
            partdir = os.path.join(STATE, "parts", service_key(row))
            feats, fields, name = fetch_socrata(row, max_features, partdir)
            gtype = next((f["geometry"]["type"] for f in feats if f.get("geometry")), "")
            lt = classify_layer(name, gtype, row["title"])
            status = "kept" if (lt and feats) else ("empty" if lt else "not storm layer")
            recs.append(_rec(row, row["url"], name, lt or "", gtype, len(feats), len(feats), len(fields), status))
            if status == "kept":
                _save(os.path.join(outdir, f"socrata-{row['item_id']}.geojson.gz"), feats, row["url"],
                      {"layer_type": lt, "name": name, "fields": fields, "geometry_type": gtype, "row": row, "reported_count": len(feats)})
            shutil.rmtree(partdir, ignore_errors=True)
        else:
            for lu, meta in service_layers(row["url"]):
                name, gtype = meta.get("name", ""), meta.get("geometryType", "")
                if meta.get("_error"):
                    recs.append(_rec(row, lu, name, "", gtype, "", "", "", "error", meta["_error"]))
                    continue
                lt = classify_layer(name, gtype, row["title"])
                nf = len(meta.get("fields") or [])
                if not lt:
                    recs.append(_rec(row, lu, name, "", gtype, "", "", nf, "not storm layer"))
                    continue
                base = os.path.join(outdir, slug(lu.split("://")[1], 90))
                mpath = base + ".meta.json"
                if os.path.exists(mpath):
                    m = load_json(mpath)
                    recs.append(_rec(row, lu, name, lt, gtype, m.get("reported_count", ""), m["n_features"], nf, "kept"))
                    continue
                partdir = os.path.join(STATE, "parts", slug(lu.split("://")[1], 150))
                try:
                    n, reported = fetch_arcgis_layer(lu, meta, max_features, partdir)
                except Deadline:
                    raise
                except RuntimeError as e:
                    recs.append(_rec(row, lu, name, lt, gtype, "", "", nf, "error", str(e)[:300]))
                    continue
                if not n:
                    recs.append(_rec(row, lu, name, lt, gtype, reported, 0, nf, "empty"))
                    continue
                fields = [{"name": f.get("name"), "alias": f.get("alias"), "type": f.get("type")} for f in meta.get("fields") or []]
                os.makedirs(outdir, exist_ok=True)
                os.replace(partdir, base + ".pages")  # instant: the page folder becomes the layer
                meta_out = {"layer_type": lt, "name": name, "fields": fields, "geometry_type": gtype, "row": row, "url": lu,
                            "max_record_count": meta.get("maxRecordCount"), "reported_count": reported, "fetched": today(),
                            "n_features": n, "file": os.path.basename(base) + ".pages"}
                with LOCK:
                    provenance_dir(base + ".pages", lu, f"{lt} layer '{name}', {n} features")
                save_json(meta_out, mpath)
                recs.append(_rec(row, lu, name, lt, gtype, reported, n, nf, "kept"))
    except Deadline:
        raise
    except Exception as e:  # a broken service is recorded and skipped, never retried forever
        recs.append(_rec(row, row["url"], row["title"], "", "", "", "", "", "error", str(e)[:300]))
    save_json(recs, done_path)


def provenance_dir(d, url, note):
    """Provenance for a page folder: hash of the concatenated page hashes."""
    import hashlib
    h = hashlib.sha256()
    size = 0
    for f in sorted(os.listdir(d)):
        if f.startswith("page_"):
            p = os.path.join(d, f)
            size += os.path.getsize(p)
            h.update(hashlib.sha256(open(p, "rb").read()).digest())
    line = " | ".join([today(), os.path.relpath(d, RAW), f"{size} bytes", f"sha256(pages):{h.hexdigest()}", url, note])
    with open(os.path.join(RAW, "PROVENANCE.txt"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _save(gpath, feats, url, meta):
    os.makedirs(os.path.dirname(gpath), exist_ok=True)
    save_json({"type": "FeatureCollection", "features": feats}, gpath)
    meta = dict(meta, url=url, fetched=today(), n_features=len(feats), file=os.path.basename(gpath))
    with LOCK:
        provenance(gpath, url, f"{meta['layer_type']} layer '{meta['name']}', {len(feats)} features")
    save_json(meta, gpath.replace(".geojson.gz", ".meta.json").replace(".geojson", ".meta.json"))


def _rec(row, url, name, lt, gtype, reported, fetched, nfields, status, err=""):
    return {"geoid": row["geoid"], "census_name": row["census_name"], "state": row["state"], "catalog": row["catalog"],
            "item_title": row["title"], "layer_url": url, "layer_name": name, "layer_type": lt, "geometry_type": gtype,
            "n_reported": reported, "n_fetched": fetched, "n_fields": nfields, "status": status, "error": err}


def item_terms(row):
    path = os.path.join(STATE, "terms", terms_key(row) + ".json")
    terms, credit = "", ""
    try:
        if row["catalog"] == "hub" and (row.get("license_info") or row.get("access_info")):
            terms, credit = row.get("license_info", ""), row.get("access_info", "")
        elif row["catalog"] == "hub":
            iid = row["item_id"].split("_")[0]
            js = get_json(f"https://www.arcgis.com/sharing/rest/content/items/{iid}", {"f": "json"})
            terms = re.sub(r"<[^>]+>", " ", js.get("licenseInfo") or "")
            terms = re.sub(r"\s+", " ", terms.replace("&nbsp;", " ")).strip()
            credit = js.get("accessInformation") or ""
        else:
            js = get_json(f"https://{row['domain']}/api/views/{row['item_id']}.json")
            terms = (js.get("license") or {}).get("name") or js.get("licenseId") or ""
            credit = js.get("attribution") or ""
    except Deadline:
        raise
    except Exception as e:
        terms = f"[could not read terms: {str(e)[:80]}]"
    t = terms.lower()
    # Restrictions outrank open wording: "public domain ... cannot be sold without consent" is restricted (Elorm, 2026-10-20, Novi MI).
    restricted = r"not for redistribution|may not be|cannot be|can not be|prohibit|internal use|no commercial|non-?commercial|for reference only|not be copied|not be sold|without (?:the )?(?:express )?(?:prior )?written (?:consent|permission)|marketing|exclusively by"
    guess = ("restricted" if re.search(restricted, t)
             else "open" if re.search(r"public domain|cc0|cc[- ]by|creative commons|open data|odc|no restrictions|free to use|without restriction", t)
             else "unstated")
    save_json({"geoid": row["geoid"], "catalog": row["catalog"], "item_id": row["item_id"], "url": row["url"], "terms_text": terms[:600],
               "credit": credit[:200], "draft_redistribution": guess, "elorm_decision": ""}, path)


def terms_key(row):
    return slug(f"{row['catalog']} {row['item_id']} {row.get('domain', '')}", 150)


def _listing(sub):
    d = os.path.join(STATE, sub)
    return set(os.listdir(d)) if os.path.isdir(d) else set()


def load_rows(only):
    with open(os.path.join(PROC, "census_candidates.csv"), newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["include"] == "yes"]
    seen, out = set(), []
    for r in rows:
        if only and r["geoid"] != only:
            continue
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        out.append(r)
    return out


def compile_outputs(rows):
    recs = []
    for p in sorted(glob.glob(os.path.join(STATE, "done", "*.json"))):
        recs += load_json(p)
    cols = ["geoid", "census_name", "state", "catalog", "item_title", "layer_url", "layer_name", "layer_type", "geometry_type",
            "n_reported", "n_fetched", "n_fields", "status", "error"]
    with open(os.path.join(PROC, "layer_inventory.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(recs)
    terms = [load_json(p) for p in sorted(glob.glob(os.path.join(STATE, "terms", "*.json")))]
    if terms:
        with open(os.path.join(PROC, "source_terms.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(terms[0]))
            w.writeheader()
            w.writerows(terms)
    return recs, terms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-features", type=int, default=250000)
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--budget", type=float, default=0, help="stop cleanly after this many seconds (0 = no limit)")
    a = ap.parse_args()
    if a.budget:
        common.DEADLINE[0] = time.time() + a.budget
    rows = load_rows(a.only)
    done = _listing("done")
    todo = [r for r in rows if service_key(r) + ".json" not in done]
    print(f"{len(rows)} services in census, {len(todo)} still to process", flush=True)
    stopped = False
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = [ex.submit(process_service, r, a.max_features) for r in todo]
        for fu in cf.as_completed(futs):
            try:
                fu.result()
            except Deadline:
                stopped = True
    if not stopped:
        try:
            with cf.ThreadPoolExecutor(a.workers) as ex:
                have = _listing("terms")
                for fu in [ex.submit(item_terms, r) for r in rows if terms_key(r) + ".json" not in have]:
                    fu.result()
        except Deadline:
            stopped = True
    recs, terms = compile_outputs(rows)
    done = _listing("done")
    left = sum(1 for r in rows if service_key(r) + ".json" not in done)
    kept = [r for r in recs if r["status"] == "kept"]
    print(f"Services done: {len(rows) - left}/{len(rows)}  |  layers kept: {len(kept)}  |  errors: {sum(1 for r in recs if r['status'] == 'error')}  |  "
          f"features: {sum(int(r['n_fetched'] or 0) for r in kept):,}  |  terms read: {len(terms)}/{len(rows)}")
    print("ALL DONE" if (left == 0 and len(terms) >= len(rows)) else "MORE TO DO (run again)")


if __name__ == "__main__":
    main()
