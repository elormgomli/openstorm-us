"""Step 6: assemble the Zenodo release package for OpenStorm US.

Plain language: copies the finished, documented files into one folder and one zip, and writes
the Zenodo description (metadata) from AUTHORS.json and report/stats.json, so nothing is typed
by hand. Everything carries a draft stamp until Elorm approves; then run with --final.

Default content follows Option A of the checklist: no copies of the cities' features, only our
own work (census, schema, crosswalk, value maps, scores, QA) plus the code that rebuilds the
harmonized data from each city's live server.

Output  release/openstorm-us-v<version>/ and release/openstorm-us-v<version>.zip
        release/zenodo_metadata.json

Run:  python code/06_release.py [--final] [--version 1.0.0]
"""

import argparse
import csv
import json
import os
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PROC, RAW, ROOT, used_layers  # noqa: E402

DRAFT = "**DRAFT: not yet verified by the author. Do not cite.**\n\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", action="store_true", help="remove DRAFT stamps (only after the author approves)")
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--repo-url", default="", help="GitHub repository URL (filled at publish)")
    ap.add_argument("--doi", default="", help="Zenodo DOI reserved for this version (filled at publish)")
    ap.add_argument("--date", default="", help="release date YYYY-MM-DD (filled at publish)")
    a = ap.parse_args()
    out = os.path.join(ROOT, "release", f"openstorm-us-v{a.version}")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(os.path.join(out, "data"), exist_ok=True)
    os.makedirs(os.path.join(out, "schema"), exist_ok=True)
    os.makedirs(os.path.join(out, "docs"), exist_ok=True)

    sel = {r["geoid"] for r in csv.DictReader(open(os.path.join(PROC, "v1_selection.csv"), encoding="utf-8")) if r["in_v1"] == "yes"}

    # data files
    copies = {"publishers.csv": "publishers.csv", "census_candidates.csv": "census_catalog_hits.csv", "layer_inventory.csv": "layer_inventory.csv",
              "layer_registry.csv": "layer_registry.csv", "value_maps_v1.csv": "value_maps_v1.csv", "condition_scales.csv": "condition_scales.csv",
              "scores_reviewed.csv": "scores_v1.csv", "qa_report.txt": "qa_report.txt"}
    for src, dst in copies.items():
        shutil.copyfile(os.path.join(PROC, src), os.path.join(out, "data", dst))
    # crosswalk for the 50, without sample_values (raw excerpts of the cities' data)
    # final_target and used_in_scores spell out what the scoring code does (openstorm_qa.crosswalk.effective_rows with
    # require_review=True, on layers marked USE in the registry), so a reader does not have to apply the decision rules.
    used = used_layers() or {}
    rows = []
    for g in sorted(sel):
        with open(os.path.join(PROC, "crosswalk", f"{g}.csv"), newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                r.pop("sample_values", None)
                dec = (r.get("reviewer_decision") or "").strip().upper()
                tgt = (r.get("reviewer_target") or "").strip() if dec == "CHANGE" else (r.get("target_field") or "")
                ok = dec in ("ACCEPT", "CHANGE") and bool(tgt) and r["source_layer_id"] in used
                rows.append(dict(geoid=g, **r, final_target=tgt if dec in ("ACCEPT", "CHANGE") else "", used_in_scores="yes" if ok else "no"))
    with open(os.path.join(out, "data", "crosswalk_v1.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    shutil.copyfile(os.path.join(RAW, "PROVENANCE.txt"), os.path.join(out, "data", "PROVENANCE.txt"))
    shutil.copyfile(os.path.join(ROOT, "report", "stats.json"), os.path.join(out, "data", "stats.json"))
    shutil.copyfile(os.path.join(ROOT, "openstorm_qa", "schema", "openstorm_v1.json"), os.path.join(out, "schema", "openstorm_v1.json"))
    for d in ("CODEBOOK.md", "LIMITATIONS.md", "DECISIONS.md", "SOURCE_TERMS.md", "BUILD_SPEC.md", "GLOSSARY.md"):
        src = os.path.join(ROOT, "docs", d)
        if os.path.exists(src):
            body = open(src, encoding="utf-8").read()
            open(os.path.join(out, "docs", d), "w", encoding="utf-8").write((DRAFT if not a.final else "") + body)
    # code snapshot (the pipeline and the openstorm-qa package)
    for d in ("code", "openstorm_qa", "tests"):
        shutil.copytree(os.path.join(ROOT, d), os.path.join(out, d), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for f in ("LICENSE", "CITATION.cff", "AUTHORS.json", "pyproject.toml", "run_all.sh", "run_all.bat"):
        if os.path.exists(os.path.join(ROOT, f)):
            shutil.copyfile(os.path.join(ROOT, f), os.path.join(out, f))
    open(os.path.join(out, ".gitignore"), "w").write("__pycache__/\n*.pyc\n.env\n_state/\ndata/raw/\n")
    # publish-time identifiers go into CITATION.cff (root copy and release copy) only when supplied; never invented
    for cff in (os.path.join(ROOT, "CITATION.cff"), os.path.join(out, "CITATION.cff")):
        t = open(cff, encoding="utf-8").read()
        for tag, val in (("[VERIFY: release date]", a.date), ("[VERIFY: Zenodo DOI after deposit]", a.doi), ("[VERIFY: GitHub URL]", a.repo_url)):
            if val:
                t = t.replace(tag, val)
        open(cff, "w", encoding="utf-8").write(t)

    stats = json.load(open(os.path.join(ROOT, "report", "stats.json")))["reviewed"]
    census = sum(1 for _ in csv.DictReader(open(os.path.join(PROC, "publishers.csv"), encoding="utf-8")))
    authors = json.load(open(os.path.join(ROOT, "AUTHORS.json")))["authors"]
    # The summary is the author's own text (docs/SUMMARY.md, approved by Elorm 2026-10-21). Its numbers are checked against
    # report/stats.json so the words can never drift from the data.
    summary = open(os.path.join(ROOT, "docs", "SUMMARY.md"), encoding="utf-8").read().strip()
    checks = [(f"census of {census} ", "census count"), (f"(1.9 million features in {stats['n_states']} states)", "features and states"),
              (f"about {round(100 * min(stats['median_pipe_material'], stats['median_pipe_diameter']))} percent", "material/size share"),
              (f"only {stats['n_pipe_publishers_any_condition']} of the {stats['n_publishers_scored']}", "condition count"),
              (f"only {stats['n_pipe_publishers_any_inspection']} report inspection", "inspection count"),
              (f"the {stats['n_publishers_scored']} most complete", "publishers scored")]
    bad = [label for text, label in checks if text not in summary]
    if abs(stats["n_features"] / 1e6 - 1.9) > 0.05:
        bad.append("1.9 million features")
    if bad:
        sys.exit("SUMMARY.md no longer matches the data: " + ", ".join(bad) + ". Update the summary with the author before releasing.")

    def to_html(md):
        out, items = [], []
        for block in md.split("\n\n"):
            lines = [l for l in block.split("\n") if l.strip()]
            if lines and all(l.startswith("- ") for l in lines):
                out.append("<ul>" + "".join(f"<li>{l[2:]}</li>" for l in lines) + "</ul>")
            else:
                out.append("<p>" + " ".join(lines) + "</p>")
        return "".join(out)
    top = next(csv.DictReader(open(os.path.join(PROC, "scores_reviewed.csv"), encoding="utf-8")))
    readme = f"""# OpenStorm US v{a.version}

{DRAFT if not a.final else ''}{summary}

## Start here

Open the file that answers your question. All tables are CSV and open in Excel.

| If you want to know | Open |
|---|---|
| Which US cities and counties publish storm-sewer GIS data | `data/publishers.csv` ({census} publishers) |
| How complete and usable each publisher's data is | `data/scores_v1.csv` (the {len(sel)} scored publishers, ranked) |
| How each publisher's fields were matched to the common format | `data/crosswalk_v1.csv` (filter `used_in_scores` to `yes`) |

**Reading a score row.** Every score runs from 0 to 1 (1 = best). For example, {top['name']} ({top['state']}) ranks first with an overall score of {float(top['overall']):.3f}, the equal-weight average of four parts: *completeness* {float(top['completeness']):.2f} (which of the five asset types are published, and how many features have a location and an ID), *attribute_coverage* {float(top['attribute_coverage']):.2f} (how often material, diameter, install year, condition and inspection date are filled with a usable value), *connectivity* {float(top['connectivity']):.2f} (share of pipe ends that meet a structure or another pipe within 1 m) and *positional* {float(top['positional']):.2f} (share of features with plausible, non-duplicate locations). The `pipe_*` columns give the share of pipes with each attribute; for example `pipe_material` {float(top['pipe_material']):.2f} means {round(100 * float(top['pipe_material']))} percent of its pipes record a usable material.

**Reading a crosswalk row.** Each row is one column in one publisher's layer. `source_field` is the publisher's name for it, `final_target` is the common-schema field it was translated to, and `value_map` shows how codes were read (for example `"4": "RCP"` means code 4 is reinforced concrete pipe; `null` means "unknown"). Only rows with `used_in_scores` = `yes` count in the scores; the rest are fields with no schema equivalent, housekeeping fields, or suggestions not accepted in review.

**Other files** answer narrower questions: `value_maps_v1.csv` (how codes were read), `layer_registry.csv` (which layers were used and why others were set aside), `census_catalog_hits.csv` (why a city is or is not in the census), `layer_inventory.csv` (every layer opened, including non-storm layers, which are listed but not kept or scored), `stats.json` (every number in the summary), `qa_report.txt` and `PROVENANCE.txt` (download log with file fingerprints). Every column is explained in `docs/CODEBOOK.md`. Read `docs/LIMITATIONS.md` before quoting a number; `docs/GLOSSARY.md` defines the terms.

## What is in this package

| Path | What it is |
|---|---|
| data/publishers.csv | The census: one row per US city or county publisher |
| data/census_catalog_hits.csv | Every catalog hit and why it was or was not included |
| data/layer_inventory.csv, data/layer_registry.csv | Every layer opened, and which were used and why |
| data/crosswalk_v1.csv | Field-by-field crosswalk for the {len(sel)} v1 publishers |
| data/value_maps_v1.csv, data/condition_scales.csv | How coded values were read |
| data/scores_v1.csv | The benchmark scores |
| data/qa_report.txt, data/stats.json, data/PROVENANCE.txt | Quality checks, every quoted number, every download with its SHA-256 |
| schema/openstorm_v1.json | The common schema |
| docs/ | Codebook, limitations, decisions, source terms, build spec, glossary |
| code/, openstorm_qa/, tests/ | The pipeline and the openstorm-qa package that produced all of the above |

## How to rebuild

Needs Python 3.9 or newer and internet access. From this folder:

```bash
python3 -m unittest discover -s tests        # 26 tests, about 1 second
python3 code/restore_v1_review.py            # puts the reviewed v1.0 crosswalk and publisher set where the pipeline reads them
python3 code/02_fetch_layers.py --workers 4  # re-downloads each city's layers from its live portal; several hours; safe to stop and restart
python3 code/04_score.py --reviewed          # scores the 50 publishers with the reviewed crosswalk
```

With the downloads logged in data/PROVENANCE.txt, these steps reproduce data/scores_v1.csv exactly (checked 2026-10-22: 1,250 of 1,250 cells identical). Portals change, so a download made later reflects each city's data on that day. To redo the census from scratch, run code/01_census.py and code/03_draft_crosswalks.py first; new publishers then need a reviewed crosswalk.

## Check your own layer

```bash
pip install .
openstorm-qa check --pipe pipes.geojson --manhole manholes.geojson --inlet inlets.geojson --json results.json
```

Layers must be GeoJSON in WGS84 (longitude, latitude).

The cities' own features are not redistributed: most publishers state no license. Use the code to rebuild the harmonized data from each live portal (see How to rebuild above).

## License

Data, schema, crosswalk and documents: CC BY 4.0. Code: MIT. Source layers remain under each publisher's terms.

## Cite

See CITATION.cff. Limitations: docs/LIMITATIONS.md.
"""
    open(os.path.join(out, "README.md"), "w", encoding="utf-8").write(readme)

    creators = [{"name": f"{x.get('family', '')}, {x.get('given', '')}".strip(", "), "affiliation": x.get("affiliation", ""),
                 **({"orcid": x["orcid"]} if x.get("orcid") and "VERIFY" not in x["orcid"] else {})} for x in authors]
    meta = {"upload_type": "dataset", "title": f"OpenStorm US: an open benchmark and schema crosswalk of municipal stormwater infrastructure data (v{a.version})",
            "creators": creators, "description": to_html(summary) + "<p>Limitations, decisions and codebook are included in docs/.</p>",
            "license": "cc-by-4.0", "access_right": "open", "version": a.version,
            "keywords": ["stormwater", "storm sewer", "GIS", "asset management", "open data", "data quality", "benchmark", "MS4", "municipal infrastructure"],
            **({"related_identifiers": [{"identifier": a.repo_url, "relation": "isSupplementTo", "scheme": "url"}]} if a.repo_url else {})}
    open(os.path.join(ROOT, "release", "zenodo_metadata.json"), "w", encoding="utf-8").write(json.dumps(meta, indent=2))

    zpath = out + ".zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, _, fs in os.walk(out):
            for f in fs:
                p = os.path.join(dp, f)
                z.write(p, os.path.relpath(p, os.path.dirname(out)))
    print(f"Release folder: {out}\nZip: {zpath} ({os.path.getsize(zpath) / 1e6:.1f} MB)\nMetadata: release/zenodo_metadata.json\n"
          f"{'FINAL' if a.final else 'DRAFT'} build. ALL DONE")


if __name__ == "__main__":
    main()
