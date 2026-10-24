"""Step 3: draft the field crosswalk for every layer in use.

Plain language: for each city's layer, proposes which of its fields means "material",
"diameter", "install year" and so on in the common schema. These are drafts. Elorm
reviews each row and writes ACCEPT, CHANGE (with the right target) or REJECT.
Re-running this script never erases a decision Elorm has already written.

Inputs   data/processed/layer_registry.csv (only layers marked USE)
Outputs  data/processed/crosswalk/<geoid>.csv     one file per publisher, for review
         data/processed/crosswalk_draft.csv        all publishers in one table
         data/processed/value_review.csv           most common raw values of coded fields
                                                   (material, condition, status...), counted
                                                   on the first 20,000 features of each layer

Run:  python code/03_draft_crosswalks.py [--budget SECONDS]   (resumable per publisher)
"""

import argparse
import collections
import csv
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PROC, RAW, drop_excluded, layer_file, load_json, load_layer, save_json, used_layers  # noqa: E402
from openstorm_qa.crosswalk import CROSSWALK_COLUMNS, draft_mapping, read_crosswalk, write_crosswalk  # noqa: E402
from openstorm_qa.schema import load_schema  # noqa: E402
from openstorm_qa.values import normalize  # noqa: E402

CODED = ("material", "condition", "lifecycle_status", "shape_type", "basin_type")
AUTO_NOTES = ("accepted in bulk by author", "not accepted by automated check")  # machine-made: redrafting may replace these
REVIEW_KEYS = ("target_field", "unit_factor", "value_map", "reviewer_decision", "reviewer_target", "reviewer_note")
SAMPLE = 20000
VALCOLS = ["geoid", "source_layer_id", "source_field", "target_field", "raw_value", "count", "draft_normalized", "draft_status", "elorm_value"]


def draft_publisher(geoid, metas, schema, used, outdir, valdir):
    path = os.path.join(outdir, f"{geoid}.csv")
    prior = {(r["source_layer_id"], r["source_field"]): r for r in read_crosswalk(path)} if os.path.exists(path) else {}
    rows, value_rows = [], []
    for mp in metas:
        meta = load_json(mp)
        layer_id = os.path.relpath(layer_file(mp), RAW)
        if used is not None and layer_id not in used:
            continue
        feats = drop_excluded(load_layer(mp, limit=SAMPLE), (used or {}).get(layer_id))
        samples = collections.defaultdict(list)
        for ft in feats[:500]:
            for k, v in (ft.get("properties") or {}).items():
                if v not in (None, "") and len(samples[k]) < 5 and v not in samples[k]:
                    samples[k].append(v)
        fields = meta.get("fields") or [{"name": k, "alias": "", "type": ""} for k in samples]
        for r in draft_mapping(fields, meta["layer_type"], schema, samples, layer_id=layer_id):
            old = prior.get((layer_id, r["source_field"]))
            human = old and not str(old.get("reviewer_note") or "").startswith(AUTO_NOTES)
            if human and (old.get("reviewer_decision") or old.get("value_map") or old.get("unit_factor")):
                for k in REVIEW_KEYS:
                    r[k] = old.get(k, "")
                r["method"] = (r["method"] + ";kept_review").strip(";")
            rows.append(r)
            tgt = r["reviewer_target"] if (r.get("reviewer_decision") or "").upper() == "CHANGE" else r["target_field"]
            if tgt in CODED:
                fd = schema["_fields_by_name"][tgt]
                cnt = collections.Counter(str((ft.get("properties") or {}).get(r["source_field"])) for ft in feats)
                vm = json.loads(r["value_map"]) if r.get("value_map") else {}
                for val, n in cnt.most_common(40):
                    norm, st = normalize(fd, None if val == "None" else val, schema, {"value_map": vm})
                    value_rows.append({"geoid": geoid, "source_layer_id": layer_id, "source_field": r["source_field"], "target_field": tgt,
                                       "raw_value": val, "count": n, "draft_normalized": norm if norm is not None else "",
                                       "draft_status": st, "elorm_value": ""})
    write_crosswalk(rows, path)
    save_json(value_rows, os.path.join(valdir, f"{geoid}.json"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=0)
    ap.add_argument("--redo", action="store_true", help="redraft publishers already drafted (reviews are kept)")
    ap.add_argument("--v1", action="store_true", help="only the publishers marked in_v1 = yes in v1_selection.csv")
    a = ap.parse_args()
    t_end = time.time() + a.budget if a.budget else None
    schema = load_schema()
    used = used_layers()
    outdir = os.path.join(PROC, "crosswalk")
    valdir = os.path.join(RAW, "_state", "values")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(valdir, exist_ok=True)
    by_geoid = collections.defaultdict(list)
    for mp in sorted(glob.glob(os.path.join(RAW, "layers", "*", "*.meta.json"))):
        by_geoid[os.path.basename(os.path.dirname(mp))].append(mp)
    if a.v1:
        with open(os.path.join(PROC, "v1_selection.csv"), newline="", encoding="utf-8") as f:
            v1 = {r["geoid"] for r in csv.DictReader(f) if r["in_v1"] == "yes"}
        redo_dir = os.path.join(RAW, "_state", "redo_v1")
        os.makedirs(redo_dir, exist_ok=True)
        redone = set(os.listdir(redo_dir))
        todo = [g for g in by_geoid if g in v1 and g not in redone]
    else:
        done = set(os.listdir(valdir))
        todo = [g for g in by_geoid if a.redo or f"{g}.json" not in done]
    print(f"{len(by_geoid)} publishers with layers, {len(todo)} to draft", flush=True)
    for i, g in enumerate(todo, 1):
        if t_end and time.time() > t_end:
            print(f"Stopped for time after {i - 1}; MORE TO DO (run again)")
            return
        draft_publisher(g, by_geoid[g], schema, used, outdir, valdir)
        if a.v1:
            open(os.path.join(RAW, "_state", "redo_v1", g), "w").close()

    # combine
    all_rows, value_rows = [], []
    for g in sorted(by_geoid):
        p = os.path.join(outdir, f"{g}.csv")
        if os.path.exists(p):
            all_rows += [dict(r, geoid=g) for r in read_crosswalk(p)]
        vp = os.path.join(valdir, f"{g}.json")
        if os.path.exists(vp):
            value_rows += load_json(vp)
    with open(os.path.join(PROC, "crosswalk_draft.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["geoid"] + CROSSWALK_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)
    with open(os.path.join(PROC, "value_review.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=VALCOLS)
        w.writeheader()
        w.writerows(value_rows)
    mapped = sum(1 for r in all_rows if r["target_field"])
    print(f"Publishers: {len(by_geoid)}  |  source fields: {len(all_rows)}  |  drafted mappings: {mapped}  |  "
          f"coded values to review: {len(value_rows)}")
    print("ALL DONE")


if __name__ == "__main__":
    main()
