"""Rebuild the v1.0 scores from the saved downloads and check the headline numbers match.

Plain language: unpacks the saved copies of the 50 cities' layers (Documents/OpenStorm-US/raw_backup),
rescores everything from scratch, and compares the results with report/stats.json.
Prints ALL HEADLINE NUMBERS MATCH when the rebuild reproduces the release.

Run from the project folder:  py code/rebuild_from_backup.py      (Windows)
                              python3 code/rebuild_from_backup.py (Mac / Linux)
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP = os.path.join(os.path.dirname(ROOT), "raw_backup")
LAYERS = os.path.join(ROOT, "data", "raw", "layers")
KEYS = ["n_publishers_scored", "n_features", "n_states", "median_overall", "median_completeness", "median_attribute_coverage",
        "median_connectivity", "median_positional", "median_pipe_material", "median_pipe_diameter", "median_pipe_install_year",
        "n_pipe_publishers_any_condition", "n_pipe_publishers_any_inspection"]


def main():
    tars = sorted(glob.glob(os.path.join(BACKUP, "*.tar")))
    if not tars:
        sys.exit(f"No backup found in {BACKUP}")
    os.makedirs(LAYERS, exist_ok=True)
    for t in tars:
        geoid = os.path.basename(t)[:-4]
        if not os.path.isdir(os.path.join(LAYERS, geoid)):
            with tarfile.open(t) as tf:
                for m in tf.getmembers():  # refuse anything that would land outside the layers folder
                    if m.name.startswith(("/", "..")) or ".." in m.name.split("/"):
                        sys.exit(f"unsafe path in {t}: {m.name}")
                tf.extractall(LAYERS)
    print(f"{len(tars)} cities unpacked. Rescoring from scratch (10 to 30 minutes)...", flush=True)
    stats_path = os.path.join(ROOT, "report", "stats.json")
    before = json.load(open(stats_path))["reviewed"]
    shutil.copyfile(stats_path, stats_path + ".before_rebuild")
    cache = os.path.join(ROOT, "data", "raw", "_state", "scores", "reviewed")
    shutil.rmtree(cache, ignore_errors=True)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "code", "04_score.py"), "--reviewed"], cwd=ROOT)
    if r.returncode:
        sys.exit("Scoring failed; copy the lines above into the Claude chat.")
    after = json.load(open(stats_path))["reviewed"]
    bad = [k for k in KEYS if before.get(k) != after.get(k)]
    for k in KEYS:
        print(f"  {k:36} release {before.get(k)!s:>12}   rebuild {after.get(k)!s:>12}   {'OK' if k not in bad else 'DIFFERENT'}")
    print("ALL HEADLINE NUMBERS MATCH" if not bad else f"{len(bad)} NUMBERS DIFFER: tell Claude")


if __name__ == "__main__":
    main()
