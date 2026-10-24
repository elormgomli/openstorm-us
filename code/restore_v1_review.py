"""Restore the reviewed v1.0 review state from the release files, so 04_score.py --reviewed can re-score.

Plain language: the release ships the census, the layer registry and the reviewed crosswalk as single
tables in data/. The pipeline reads them from data/processed/ (one crosswalk file per publisher). This
script copies them back into that layout. It changes nothing in data/ itself.

Run:  python code/restore_v1_review.py
then: python code/02_fetch_layers.py --workers 4   (re-downloads each city's layers from the live portals)
      python code/04_score.py --reviewed
"""

import csv
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PROC, ROOT  # noqa: E402

DATA = os.path.join(ROOT, "data")
XW_COLS = ["source_layer_id", "layer_type", "source_field", "source_alias", "source_type", "sample_values", "target_field", "confidence",
           "method", "unit_factor", "value_map", "reviewer_decision", "reviewer_target", "reviewer_note"]


def main():
    os.makedirs(os.path.join(PROC, "crosswalk"), exist_ok=True)
    for src, dst in (("publishers.csv", "publishers.csv"), ("census_catalog_hits.csv", "census_candidates.csv"),
                     ("layer_inventory.csv", "layer_inventory.csv"), ("layer_registry.csv", "layer_registry.csv")):
        shutil.copyfile(os.path.join(DATA, src), os.path.join(PROC, dst))
    scores = list(csv.DictReader(open(os.path.join(DATA, "scores_v1.csv"), encoding="utf-8")))
    with open(os.path.join(PROC, "v1_selection.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["geoid", "name", "state", "publisher_class", "provisional_overall", "in_v1", "elorm_override"])
        w.writeheader()
        for r in scores:
            w.writerow(dict(geoid=r["geoid"], name=r["name"], state=r["state"], publisher_class=r["publisher_class"],
                            provisional_overall="", in_v1="yes", elorm_override=""))
    json.dump({"accepted_by": "Elorm", "note": "restored from data/scores_v1.csv", "geoids": [r["geoid"] for r in scores]},
              open(os.path.join(PROC, "v1_locked.json"), "w"), indent=1)
    by = {}
    for r in csv.DictReader(open(os.path.join(DATA, "crosswalk_v1.csv"), encoding="utf-8")):
        by.setdefault(r.pop("geoid"), []).append(r)
    for g, rows in by.items():
        with open(os.path.join(PROC, "crosswalk", f"{g}.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=XW_COLS, extrasaction="ignore", restval="")
            w.writeheader()
            w.writerows(rows)
    print(f"Restored {len(scores)} publishers and {len(by)} crosswalks into {PROC}. ALL DONE")


if __name__ == "__main__":
    main()
