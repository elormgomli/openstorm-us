"""Step 4: score every publisher and write the stats file.

Plain language: runs the openstorm-qa checks on each city's layers and ranks them.

  --provisional  (default) uses the drafted mappings. Used only to pick the v1.0 set.
  --reviewed     uses only mappings Elorm marked ACCEPT or CHANGE. These are the
                 release scores.

Outputs  data/processed/scores_provisional.csv | scores_reviewed.csv
         data/processed/v1_selection.csv     (provisional run: the 50 strongest US city and
                                              county publishers, plus utilities listed apart)
         report/stats.json                   every number any document quotes
         data/processed/qa_report.txt        counts, losses, ranges, spot checks
"""

import argparse
import collections
import csv
import glob
import json
import os
import hashlib
import random
import time
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PROC, RAW, drop_excluded, is_open_conveyance, layer_file, load_json, load_layer, save_json, used_layers, ROOT, today  # noqa: E402
from openstorm_qa.crosswalk import effective_rows, harmonize, read_crosswalk  # noqa: E402
from openstorm_qa.schema import load_schema  # noqa: E402
from openstorm_qa.score import LAYER_TYPES, score_network  # noqa: E402

V1_SIZE = 50
SCORE_VERSION = "12"  # bump when scoring or harmonizing code changes, so cached results are recomputed
SEED = 20261212


def load_publishers():
    with open(os.path.join(PROC, "publishers.csv"), newline="", encoding="utf-8") as f:
        return {r["geoid"]: r for r in csv.DictReader(f)}


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--provisional", action="store_true")
    g.add_argument("--reviewed", action="store_true")
    ap.add_argument("--budget", type=float, default=0, help="stop starting new publishers after this many seconds")
    ap.add_argument("--shard", default="", help="i/n: only compute every n-th publisher (for parallel runs); outputs come from a final unsharded run")
    a = ap.parse_args()
    mode = "reviewed" if a.reviewed else "provisional"
    schema = load_schema()
    pubs = load_publishers()
    sel = None
    if mode == "reviewed":
        with open(os.path.join(PROC, "v1_selection.csv"), newline="", encoding="utf-8") as f:
            sel = {r["geoid"] for r in csv.DictReader(f) if r["in_v1"] == "yes"}

    used = used_layers()
    cache_dir = os.path.join(RAW, "_state", "scores", mode)
    os.makedirs(cache_dir, exist_ok=True)
    t_end = time.time() + a.budget if a.budget else None
    out, layer_counts, missing_xw, unfinished = [], collections.Counter(), [], 0
    for d in sorted(glob.glob(os.path.join(RAW, "layers", "*"))):
        geoid = os.path.basename(d)
        if sel is not None and geoid not in sel:
            continue
        if a.shard:
            si, sn = map(int, a.shard.split("/"))
            if int(hashlib.md5(geoid.encode()).hexdigest(), 16) % sn != si:
                unfinished += 0
                continue
        xw_path = os.path.join(PROC, "crosswalk", f"{geoid}.csv")
        if not os.path.exists(xw_path):
            missing_xw.append(geoid)
            continue
        metas = []
        for mp in sorted(glob.glob(os.path.join(d, "*.meta.json"))):
            lid = os.path.relpath(layer_file(mp), RAW)
            if used is None or lid in used:
                metas.append((mp, lid))
        if not metas:
            continue
        key = hashlib.sha256((open(xw_path, "rb").read().hex() + "|".join(f"{l}{(used or {}).get(l)}" for _, l in metas) + schema["schema_version"] + SCORE_VERSION).encode()).hexdigest()
        cpath = os.path.join(cache_dir, f"{geoid}.json")
        cached = load_json(cpath) if os.path.exists(cpath) else None
        if cached and cached.get("key") == key:
            res, lc = cached["res"], cached["layer_counts"]
        else:
            if t_end and time.time() > t_end:
                unfinished += 1
                continue
            rows = effective_rows(read_crosswalk(xw_path), require_review=(mode == "reviewed"))
            layers, lc = collections.defaultdict(list), collections.Counter()
            for mp, lid in metas:
                meta = load_json(mp)
                lr = [r for r in rows if r["source_layer_id"] == lid]
                feats = drop_excluded(load_layer(mp), (used or {}).get(lid))
                hr = harmonize(feats, lr, meta["layer_type"], schema)
                if is_open_conveyance(meta):
                    for rec in hr:
                        rec["status"]["diameter_in"], rec["props"]["diameter_in"] = "not_applicable", None
                layers[meta["layer_type"]] += hr
                lc[meta["layer_type"]] += 1
            res = score_network(dict(layers), schema)
            del layers
            save_json({"key": key, "res": res, "layer_counts": dict(lc)}, cpath)
        layer_counts.update(lc)
        p = pubs.get(geoid, {})
        cov = res.get("attribute_coverage_detail", {})
        row = {"geoid": geoid, "name": p.get("census_name", ""), "state": p.get("state", ""), "publisher_class": p.get("publisher_class", ""),
               "layers_present": ";".join(res["layers_present"]), "n_features": sum(res["feature_counts"].values()),
               **{f"n_{lt}": res["feature_counts"][lt] for lt in LAYER_TYPES},
               **{k: _r(res.get(k)) for k in ("overall", "completeness", "attribute_coverage", "connectivity", "positional")},
               "pipe_material": _r(cov.get("pipe", {}).get("material")), "pipe_diameter": _r(cov.get("pipe", {}).get("diameter_in")),
               "pipe_install_year": _r(cov.get("pipe", {}).get("install_year")), "pipe_condition": _r(cov.get("pipe", {}).get("condition")),
               "pipe_inspection": _r(cov.get("pipe", {}).get("last_inspection_date")),
               "pipe_components": res.get("components", ""), "largest_component_share": _r(res.get("largest_component_share")),
               "positional_failures": json.dumps(res.get("positional_failures", {}))}
        out.append(row)

    if unfinished or a.shard:
        print(f"Scored {len(out)} publishers; {unfinished} left." + (" MORE TO DO (run again)" if unfinished else " Shard finished."))
        return

    out.sort(key=lambda r: -float(r["overall"] or 0))
    for i, r in enumerate(out, 1):
        r["rank"] = i
    cols = ["rank", "geoid", "name", "state", "publisher_class", "overall", "completeness", "attribute_coverage", "connectivity", "positional",
            "layers_present", "n_features"] + [f"n_{lt}" for lt in LAYER_TYPES] + \
           ["pipe_material", "pipe_diameter", "pipe_install_year", "pipe_condition", "pipe_inspection", "pipe_components", "largest_component_share", "positional_failures"]
    _write(os.path.join(PROC, f"scores_{mode}.csv"), out, cols)

    if mode == "provisional":
        sp_ = os.path.join(PROC, "v1_selection.csv")
        overrides = {}
        if os.path.exists(sp_):
            with open(sp_, newline="", encoding="utf-8") as f:
                overrides = {r["geoid"]: r["elorm_override"] for r in csv.DictReader(f) if r.get("elorm_override", "").strip()}
        muni = [r for r in out if r["publisher_class"] in ("city", "county") and int(r["n_pipe"]) > 0]
        top = {r["geoid"] for r in muni[:V1_SIZE]}
        lock_path = os.path.join(PROC, "v1_locked.json")
        if os.path.exists(lock_path):  # Elorm accepted the set: keep it exactly, whatever the new provisional ranks are
            top = set(load_json(lock_path)["geoids"])
        top = {g for g in top if overrides.get(g, "").upper() != "OUT"} | {g for g, v in overrides.items() if v.upper() == "IN"}
        _write(os.path.join(PROC, "v1_selection.csv"),
               [{"geoid": r["geoid"], "name": r["name"], "state": r["state"], "publisher_class": r["publisher_class"], "provisional_overall": r["overall"],
                 "in_v1": "yes" if r["geoid"] in top else "no", "elorm_override": overrides.get(r["geoid"], "")} for r in out],
               ["geoid", "name", "state", "publisher_class", "provisional_overall", "in_v1", "elorm_override"])

    stats = _stats(out, mode, layer_counts, missing_xw)
    os.makedirs(os.path.join(ROOT, "report"), exist_ok=True)
    sp = os.path.join(ROOT, "report", "stats.json")
    allstats = json.load(open(sp)) if os.path.exists(sp) else {}
    allstats[mode] = stats
    with open(sp, "w") as f:
        json.dump(allstats, f, indent=2)
    _qa_report(out, stats, mode)
    print(f"\n{len(out)} publishers scored ({mode}). Median overall: {stats.get('median_overall')}")


def _r(v):
    return "" if v is None else round(v, 4)


def _med(xs):
    xs = sorted(xs)
    return round(xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2, 4) if xs else None


def _stats(out, mode, layer_counts, missing_xw):
    s = {"mode": mode, "date": today(), "n_publishers_scored": len(out), "n_layers": dict(layer_counts),
         "n_features": sum(int(r["n_features"]) for r in out), "publishers_without_crosswalk": missing_xw,
         "by_class": dict(collections.Counter(r["publisher_class"] for r in out)),
         "n_states": len({r["state"] for r in out if r["state"]})}
    for k in ("overall", "completeness", "attribute_coverage", "connectivity", "positional", "pipe_material", "pipe_diameter",
              "pipe_install_year", "pipe_condition", "pipe_inspection"):
        vals = [float(r[k]) for r in out if r[k] != ""]
        s[f"median_{k}"] = _med(vals)
        s[f"n_with_{k}"] = len(vals)
    pipe_pubs = [r for r in out if int(r["n_pipe"]) > 0]
    s["n_publishers_with_pipes"] = len(pipe_pubs)
    for k, lab in (("pipe_condition", "condition"), ("pipe_inspection", "inspection"), ("pipe_install_year", "install_year")):
        s[f"n_pipe_publishers_any_{lab}"] = sum(1 for r in pipe_pubs if r[k] != "" and float(r[k]) > 0)
    return s


def _qa_report(out, stats, mode):
    rng = random.Random(SEED)
    lines = [f"OpenStorm US QA report ({mode}), {today()}", "=" * 60, ""]
    inv = os.path.join(PROC, "layer_inventory.csv")
    if os.path.exists(inv):
        with open(inv, newline="", encoding="utf-8") as f:
            irows = list(csv.DictReader(f))
        st = collections.Counter(r["status"] for r in irows)
        lines += ["Layer inventory (step 2):", *[f"  {k}: {v}" for k, v in st.items()]]
        short = [r for r in irows if r["status"] == "kept" and r["n_reported"] and r["n_fetched"] and int(r["n_fetched"]) < int(r["n_reported"])]
        lines += [f"  layers where fewer features were saved than the service reports: {len(short)}"]
        lines += [f"    {r['geoid']} {r['layer_name']}: {r['n_fetched']} of {r['n_reported']}" for r in short[:20]]
        errs = [r for r in irows if r["status"] == "error"]
        lines += [f"  first errors ({len(errs)} total):"] + [f"    {r['geoid']} {r['layer_url']}: {r['error'][:120]}" for r in errs[:15]] + [""]
    lines += ["Stats:", json.dumps(stats, indent=2), ""]
    lines += ["Range checks (every score must be within 0 to 1):"]
    bad = [r["geoid"] for r in out for k in ("overall", "completeness", "attribute_coverage", "connectivity", "positional")
           if r[k] != "" and not (0 <= float(r[k]) <= 1)]
    lines += [f"  out of range: {bad or 'none'}", ""]
    lines += ["Spot checks for Elorm (compare to the publisher's portal by hand):"]
    picks = out[:3] + out[-3:] + rng.sample(out, min(5, len(out)))
    seen = set()
    for r in picks:
        if r["geoid"] in seen:
            continue
        seen.add(r["geoid"])
        lines.append(f"  #{r['rank']} {r['name']}, {r['state']} ({r['geoid']}): overall {r['overall']}, pipes {r['n_pipe']}, "
                     f"pipe material coverage {r['pipe_material']}, connectivity {r['connectivity']}")
    with open(os.path.join(PROC, "qa_report.txt" if mode == "reviewed" else "qa_report_provisional.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")


def _write(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
