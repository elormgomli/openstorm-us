"""Step 2.5: decide which downloaded layers (and which features in them) are used.

Plain language: some cities post the same layer twice, and some downloaded layers are not
storm assets (stream lines, map labels, abandoned pipes). Ditches are not counted
(Elorm, 2026-10-15): a layer of only ditches is skipped, and in a mixed layer such as
"Ditches and Culverts" only the ditch features are removed, using the layer's own type
field. Channels, swales, laterals and private assets are counted.

Every layer is listed once and marked USE or SKIP with a reason. Elorm can override any
row in the elorm_override column (USE / SKIP); later runs keep her choice.

Output  data/processed/layer_registry.csv
        columns exclude_field / exclude_pattern: features whose exclude_field matches
        exclude_pattern are dropped before crosswalk drafting and scoring.

Run:  python code/025_layer_registry.py [--budget SECONDS]   (resumable)
"""

import argparse
import collections
import csv
import glob
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PROC, RAW, layer_file, load_json, load_layer, save_json, slug  # noqa: E402

SKIP = [
    (r"\bnhd\b|flowline|\bstreams?\b|\brivers?\b|\bcreeks?\b|hydrography", "natural waterway, not a built asset"),
    (r"label|annotation|\banno\b|arrows?|flow direction|symbol", "map label or symbol layer"),
    (r"abandon|removed|retired|historic", "abandoned or removed assets"),
    (r"propo?e?sed|planned|future|construction project|cip\b|capital improvement", "proposed or project layer"),
    (r"service request|work ?order|complaint|inspection request|311", "service request or work order layer"),
    (r"drainage area|catchment|watershed|sub-?basin|basin boundar|sewershed|service area|boundar", "area boundary, not an asset"),
    (r"sanitary|wastewater|\bsewer lateral", "sanitary, not storm"),
    (r"\btest\b|geogimstest|staging|\bdev\b", "test or staging server"),
]
# Line layers typed as "pipe" only because they sit in a storm service: not conveyances.
PIPE_NOT = (r"\broads?\b|\bstreets?\b|sweep|\broutes?\b|\bcurbs?\b|sidewalk|fence|\btrails?\b|parcel|contour|centerline|easement|watercourse",
            "line layer that is not a conveyance (roads, curbs, fences, routes, watercourses)")
FLAG = r"channel|swale|lateral|private"
DITCH = re.compile(r"\bditch", re.I)
# A value counts as "this feature is a ditch" only if it names a ditch, not something related to one
# ("Ditch Crossing", "Empties into ditch", "STORM DITCH - Excavate" are culverts, pipes or activities).
CONTEXT = r"into|\bin ditch|\bto ditch|from ditch|outlets? (?:to|into)|crossing|block|excavat|repair|\bsod|paveme|empties|\bcar\b|maint|clean|investigat|inspect"
DITCH_PATTERN = r"(?i)^(?!.*(?:" + CONTEXT + r")).*\bditch"
DITCH_VALUE = re.compile(DITCH_PATTERN)
TYPE_FIELD = re.compile(r"type|class|kind|categ|feat|desc|system", re.I)
NOT_TYPE_FIELD = re.compile(r"^(from|to|up|down|us|ds|start|end)_?|cross|inlet|outlet|maint|equip|culv|comment|note|remark|insp|activit|work|^last", re.I)
SAMPLE = 5000
DITCH_ONLY_SHARE = 0.98


def find_ditch_field(feats):
    """Return (field, ditch_share) for the type-like field that labels features as ditches, or (None, 0)."""
    best = (None, 0.0, 0)
    n = len(feats) or 1
    if not feats:
        return None, 0.0
    for k in (feats[0].get("properties") or {}):
        if not TYPE_FIELD.search(k) or NOT_TYPE_FIELD.search(k):
            continue
        vals = collections.Counter(str((f.get("properties") or {}).get(k)) for f in feats)
        if len(vals) > 60 or (re.search(r"desc", k, re.I) and len(vals) > 20):  # free text, not a type list
            continue
        hits = sum(c for v, c in vals.items() if DITCH_VALUE.search(v))
        if hits and hits > best[2]:
            best = (k, hits / n, hits)
    return best[0], best[1]


def ditch_check(mp, cache_dir):
    """Cached per layer: {"field": ..., "share": ...}."""
    cp = os.path.join(cache_dir, slug(os.path.relpath(mp, RAW), 150) + ".json")
    if os.path.exists(cp):
        return load_json(cp)
    field, share = find_ditch_field(load_layer(mp, limit=SAMPLE))
    res = {"field": field, "share": round(share, 4)}
    save_json(res, cp)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=0)
    a = ap.parse_args()
    t_end = time.time() + a.budget if a.budget else None
    cache_dir = os.path.join(RAW, "_state", "ditch")
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(PROC, "layer_registry.csv")
    prior = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            prior = {r["layer_id"]: r for r in csv.DictReader(f)}
    rows, unfinished = [], 0
    for mp in sorted(glob.glob(os.path.join(RAW, "layers", "*", "*.meta.json"))):
        m = load_json(mp)
        lid = os.path.relpath(layer_file(mp), RAW)
        name = re.sub(r"[_\-.]+", " ", m["name"].lower())  # "Storm_Channel_Ditch" -> "storm channel ditch"
        text = re.sub(r"[_\-.]+", " ", f"{name} {m['url']}".lower())
        use, reason, ex_field, ex_pat, note = "USE", "", "", "", ""
        for pat, why in SKIP:
            if re.search(pat, text):
                use, reason = "SKIP", why
                break
        if use == "USE" and m["layer_type"] == "pipe" and re.search(PIPE_NOT[0], name) and not re.search(r"pipe|culvert|main|sewer|conduit", name):
            use, reason = "SKIP", PIPE_NOT[1]
        if use == "USE" and m["layer_type"] == "pipe":
            if t_end and time.time() > t_end and not os.path.exists(os.path.join(cache_dir, slug(os.path.relpath(mp, RAW), 150) + ".json")):
                unfinished += 1
                continue
            d = ditch_check(mp, cache_dir)
            if d["field"] and d["share"] >= DITCH_ONLY_SHARE:
                use, reason = "SKIP", "ditch layer (ditches not counted, Elorm 2026-10-15)"
            elif d["field"]:
                ex_field, ex_pat = d["field"], DITCH_PATTERN
                note = f"ditch features removed via {d['field']} (about {d['share']:.0%} of sampled features)"
            elif DITCH.search(name) and not re.search(r"culvert|pipe|sewer|main|conduit", name):
                use, reason = "SKIP", "ditch layer (ditches not counted, Elorm 2026-10-15)"
            elif DITCH.search(name):
                note = "name mentions ditches but no field labels them; kept whole, check"
        flag = re.search(FLAG, text)
        rows.append({"layer_id": lid, "geoid": os.path.basename(os.path.dirname(mp)), "layer_type": m["layer_type"], "layer_name": m["name"],
                     "n_features": m["n_features"], "n_fields": len(m.get("fields") or []), "url": m["url"],
                     "use": use, "reason": reason, "exclude_field": ex_field, "exclude_pattern": ex_pat, "note": note,
                     "flag": f"counted (Elorm 2026-10-15): {flag.group(0)}" if flag else "",
                     "elorm_override": (prior.get(lid) or {}).get("elorm_override", "")})
    if unfinished:
        print(f"Checked {len(rows)} layers; {unfinished} pipe layers still to check. MORE TO DO (run again)")
        return
    # duplicates: same publisher, same type, same feature count -> keep the one with the most fields
    groups = collections.defaultdict(list)
    for r in rows:
        if r["use"] == "USE" and int(r["n_features"]) >= 50:
            groups[(r["geoid"], r["layer_type"], r["n_features"])].append(r)
    for g in groups.values():
        if len(g) > 1:
            g.sort(key=lambda r: (-int(r["n_fields"]), r["layer_id"]))
            for r in g[1:]:
                r["use"], r["reason"] = "SKIP", f"duplicate of {g[0]['layer_id']}"
    for r in rows:
        r["final"] = (r["elorm_override"].strip().upper() or r["use"])
    cols = ["layer_id", "geoid", "layer_type", "layer_name", "n_features", "n_fields", "url", "use", "reason",
            "exclude_field", "exclude_pattern", "note", "flag", "elorm_override", "final"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    c = collections.Counter(r["reason"].split(" of ")[0] or "used" for r in rows)
    used = [r for r in rows if r["final"] == "USE"]
    print(f"Layers: {len(rows)}  |  used: {len(used)} ({sum(int(r['n_features']) for r in used):,} features before ditch removal, "
          f"{len({r['geoid'] for r in used})} publishers)  |  mixed layers with ditch features removed: {sum(1 for r in used if r['exclude_field'])}")
    for k, v in c.most_common():
        print(f"  {v:5d}  {k}")
    print("ALL DONE")


if __name__ == "__main__":
    main()
