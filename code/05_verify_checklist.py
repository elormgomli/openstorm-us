"""Step 5: write the author's verification checklist from the real data.

Plain language: picks real rows for Elorm to check by hand (Texas cities she knows, the most
extreme values, random features, and a random sample of field mappings), and writes each
check with the exact value we hold and a link that opens the same record on the city's own
server. Output: docs/VERIFY_CHECKLIST.md. The picks are random but repeatable (fixed seed).

Run:  python code/05_verify_checklist.py [--budget SECONDS]   (resumable per publisher)
"""

import argparse
import collections
import csv
import glob
import json
import os
import html
import random
import re
import sys
import time
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PROC, RAW, ROOT, drop_excluded, layer_file, load_json, load_layer, save_json, used_layers  # noqa: E402
from openstorm_qa.crosswalk import effective_rows, harmonize, read_crosswalk  # noqa: E402
from openstorm_qa.schema import load_schema  # noqa: E402
import datetime  # noqa: E402
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("review_defaults", os.path.join(os.path.dirname(os.path.abspath(__file__)), "035_apply_review_defaults.py"))
_rd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rd)
layer_domains = _rd.layer_domains


def shown_as(raw):
    """The value as the city's ArcGIS page displays it: dates (stored as milliseconds) appear as m/d/yyyy."""
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return str(raw)
    if 1e9 <= abs(x) <= 4.2e12 and x.is_integer():  # milliseconds between about 1837 and 2103
        try:
            d = datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=x)
            return f"{d.month}/{d.day}/{d.year}` or `{int(x)}"  # newer servers show the date, older ones the raw number
        except (OverflowError, ValueError):
            pass
    return str(raw)

SEED = 20261015
KEY = ("material", "diameter_in", "install_year", "condition", "last_inspection_date")
LABEL = {"material": "material", "diameter_in": "diameter (in)", "install_year": "install year", "condition": "condition (1-5)",
         "last_inspection_date": "last inspection"}
KNOWN = ["Grand Prairie city", "Waco city", "Round Rock city", "Tyler city", "Pflugerville city"]
STATE = os.path.join(RAW, "_state", "checklist")


def oid_field(meta):
    for f in meta.get("fields") or []:
        if f.get("type") == "esriFieldTypeOID":
            return f["name"]
    names = [f.get("name") for f in meta.get("fields") or []]
    for n in ("OBJECTID", "ObjectID", "objectid", "FID", "OBJECTID_1"):
        if n in names:
            return n
    return None


def record_link(url, oidf, oid):
    return f"{url}/query?where={quote(f'{oidf}={oid}')}&outFields=*&f=html"


def count_link(url):
    return f"{url}/query?where=1%3D1&returnCountOnly=true&f=html"


def scan_publisher(geoid, schema, used, sample_rows, rng_seed):
    """One pass over a publisher: examples for sampled mappings, pipe extremes, and one random pipe feature."""
    rows = effective_rows(read_crosswalk(os.path.join(PROC, "crosswalk", f"{geoid}.csv")), require_review=True)
    rng = random.Random(f"{rng_seed}-{geoid}")
    out = {"examples": {}, "extremes": {}, "random": None, "pipe_layers": [], "layer_urls": {}}
    want = {(r["source_layer_id"], r["source_field"]) for r in sample_rows}
    cands = []
    for mp in sorted(glob.glob(os.path.join(RAW, "layers", geoid, "*.meta.json"))):
        lid = os.path.relpath(layer_file(mp), RAW)
        if used is not None and lid not in used:
            continue
        meta = load_json(mp)
        lt = meta["layer_type"]
        feats = drop_excluded(load_layer(mp), (used or {}).get(lid))
        lr = [r for r in rows if r["source_layer_id"] == lid]
        h = harmonize(feats, lr, lt, schema, keep_raw=True)
        oidf = oid_field(meta)
        arc = "/resource/" not in meta["url"] and oidf
        doms = layer_domains(meta["url"]) if "/resource/" not in meta["url"] else {}
        tmap = {}
        for r in lr:
            tmap.setdefault(r["target_field"], r["source_field"])
        for r in lr:
            if (lid, r["source_field"]) in want:
                ex = []
                for f, rec in zip(feats, h):
                    raw = rec.get("raw", {}).get(r["target_field"])
                    if raw in (None, "", " "):
                        continue
                    ex.append({"raw": shown_as(raw)[:40], "value": rec["props"].get(r["target_field"]), "status": rec["status"].get(r["target_field"]),
                               "decoded": doms.get(r["source_field"], {}).get(str(raw), "")})
                    if len(ex) >= 40:
                        break
                rng.shuffle(ex)
                out["examples"][f"{lid}|{r['source_field']}"] = ex[:3]
                out["layer_urls"][lid] = meta["url"]
        if lt != "pipe":
            continue
        out["pipe_layers"].append({"name": meta["name"], "url": meta["url"], "n": len(feats), "arcgis": bool(arc)})
        if not arc:
            continue
        for f, rec in zip(feats, h):
            p, st = rec["props"], rec["status"]
            oid = (f.get("properties") or {}).get(oidf)
            if oid is None:
                continue
            base = {"layer": meta["name"], "url": meta["url"], "oidf": oidf, "oid": oid, "props": {k: p.get(k) for k in KEY if st.get(k) == "valid"},
                    "raw": {k: shown_as(rec.get("raw", {}).get(k))[:30] for k in KEY if st.get(k) == "valid"},
                    "fields": {k: tmap.get(k) for k in KEY if st.get(k) == "valid"},
                    "decoded": {k: doms.get(tmap.get(k), {}).get(str(rec.get("raw", {}).get(k)), "") for k in KEY if st.get(k) == "valid"}}
            if st.get("diameter_in") == "valid":
                e = out["extremes"].get("max_diameter")
                if not e or p["diameter_in"] > e["props"]["diameter_in"]:
                    out["extremes"]["max_diameter"] = base
            if st.get("install_year") == "valid":
                e = out["extremes"].get("min_year")
                if not e or p["install_year"] < e["props"]["install_year"]:
                    out["extremes"]["min_year"] = base
                e = out["extremes"].get("max_year")
                if not e or p["install_year"] > e["props"]["install_year"]:
                    out["extremes"]["max_year"] = base
            if st.get("condition") == "valid" and p["condition"] == "5" and "cond5" not in out["extremes"]:
                out["extremes"]["cond5"] = base
            if sum(1 for k in KEY if st.get(k) == "valid") >= 2:
                cands.append(base)
    out["random"] = rng.choice(cands) if cands else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=0)
    a = ap.parse_args()
    t_end = time.time() + a.budget if a.budget else None
    schema = load_schema()
    used = used_layers()
    rng = random.Random(SEED)
    sel = {r["geoid"]: r for r in csv.DictReader(open(os.path.join(PROC, "v1_selection.csv"), encoding="utf-8")) if r["in_v1"] == "yes"}
    scores = {r["geoid"]: r for r in csv.DictReader(open(os.path.join(PROC, "scores_reviewed.csv"), encoding="utf-8"))}
    name = {g: f"{scores[g]['name'].replace(' city', '').replace(' town', '')}, {scores[g]['state']}" for g in scores}

    # mapping sample: 6 accepted mappings per key attribute, spread over publishers
    acc = collections.defaultdict(list)
    for g in sorted(sel):
        for r in read_crosswalk(os.path.join(PROC, "crosswalk", f"{g}.csv")):
            if r.get("reviewer_decision") == "ACCEPT" and r["target_field"] in KEY:
                acc[r["target_field"]].append(dict(r, geoid=g))
    sample = []
    for t in KEY:
        pool = acc[t][:]
        rng.shuffle(pool)
        seen = set()
        for r in pool:
            if r["geoid"] in seen:
                continue
            sample.append(r)
            seen.add(r["geoid"])
            if len(seen) == 6:
                break
    known = [g for g in sel if scores.get(g, {}).get("name") in KNOWN]
    pool_ = sorted(set(sel) - set(known))
    rand_pubs = rng.sample(pool_, min(5, len(pool_)))

    os.makedirs(STATE, exist_ok=True)
    scans = {}
    for g in sorted(sel):
        cp = os.path.join(STATE, f"{g}.json")
        if os.path.exists(cp):
            scans[g] = load_json(cp)
            continue
        if t_end and time.time() > t_end:
            print(f"Scanned {len(scans)} of {len(sel)}. MORE TO DO (run again)")
            return
        scans[g] = scan_publisher(g, schema, used, [r for r in sample if r["geoid"] == g], SEED)
        save_json(scans[g], cp)

    stats = json.load(open(os.path.join(ROOT, "report", "stats.json")))["reviewed"]
    n_pattern = sum(1 for _ in csv.DictReader(open(os.path.join(ROOT, "docs", "DATA_PATTERN_MAPPINGS.csv"), encoding="utf-8")))
    flags = list(csv.DictReader(open(os.path.join(ROOT, "docs", "AUTO_CHECK_FLAGS.csv"), encoding="utf-8")))
    n_warn = sum(1 for r in flags if r["reason"].startswith("ACCEPTED"))
    n_acc = sum(len(v) for v in acc.values()) + sum(1 for g in sel for r in read_crosswalk(os.path.join(PROC, "crosswalk", f"{g}.csv"))
                                                   if r.get("reviewer_decision") == "ACCEPT" and r["target_field"] not in KEY)
    top = sorted(scores.values(), key=lambda r: int(r["rank"]))
    L = []
    w = L.append
    w("# Your verification checklist: OpenStorm US v1.0")
    w("")
    w(f"*Plain language: before anything goes public under your name, you check it. Every item names the exact file, the exact value we hold, and a link that opens the same record on the city's own server. Total time: about 2 hours, in one or two sittings. Tick each box and add the date. Generated {time.strftime('%Y-%m-%d')} with seed {SEED}, so the picks are repeatable.*")
    w("")
    w("*How to open a link: click it. A page from the city's ArcGIS server opens and shows one record as a table. Find the field named in the item and compare. If a link fails, the city may have changed its server since we downloaded on 2026-10-12; write that down, it is not your error.*")
    w("")
    w("## Part 1: Make sure the results can be rebuilt (20 to 40 min, mostly waiting)")
    w("")
    w("Pick one:")
    w("")
    w("- [ ] **Option A, run it yourself.** Open the Windows Start menu, type `cmd`, press Enter, then paste these two lines:")
    w("")
    w("  ```")
    w("  cd %USERPROFILE%\\Documents\\OpenStorm-US\\openstorm-us")
    w("  py code\\rebuild_from_backup.py")
    w("  ```")
    w("")
    w("  It unpacks the saved downloads, rescores all 50 cities from scratch, and ends with `ALL HEADLINE NUMBERS MATCH`.")
    w("- [ ] **Option B, ask Claude.** In a new chat, say: \"Re-run the OpenStorm US reviewed scoring from the raw backup in Documents\\OpenStorm-US and compare the headline numbers.\"")
    w("")
    w("The headline numbers it must reproduce (from `report/stats.json`):")
    w("")
    w(f"- Publishers scored: **{stats['n_publishers_scored']}** in {stats['n_states']} states; features scored: **{stats['n_features']:,}**")
    w(f"- Median overall score: **{stats['median_overall']:.3f}**")
    top3 = ", ".join("%s (%.3f)" % (name[r["geoid"]], float(r["overall"])) for r in top[:3])
    w(f"- Top three: {top3}")
    w(f"- Median pipe coverage: material {stats['median_pipe_material']:.0%}, diameter {stats['median_pipe_diameter']:.0%}, install year {stats['median_pipe_install_year']:.0%}")
    w(f"- Cities publishing any pipe condition: **{stats['n_pipe_publishers_any_condition']} of 50**; any inspection date: **{stats['n_pipe_publishers_any_inspection']} of 50**")
    w("")
    w("## Part 2: Check the sources and their terms (15 min)")
    w("")
    terms = [r for r in csv.DictReader(open(os.path.join(PROC, "source_terms.csv"), encoding="utf-8")) if r["geoid"] in sel]
    by = collections.defaultdict(set)
    for r in terms:
        by[r["geoid"]].add(r["draft_redistribution"])
    open_p = sorted(name[g] for g, v in by.items() if v == {"open"})
    restr_p = sorted(name[g] for g, v in by.items() if "restricted" in v)
    def item_page(r):
        if r["catalog"] == "socrata":
            dom = r["url"].split("/")[2]
            return f"https://{dom}/d/{r['item_id']}"
        return f"https://www.arcgis.com/home/item.html?id={r['item_id'].split('_')[0]}"

    w("The sources are two public catalogs, the cities' own servers, and the Census list used to name each city:")
    w("")
    w("- ArcGIS Hub catalog (most cities): https://hub.arcgis.com/search?q=storm%20sewer")
    w("- Socrata catalog (Austin, NYC, Gainesville and others): https://api.us.socrata.com/api/catalog/v1?q=storm%20sewer (opens as plain text, not a styled page; that is expected)")
    w("- US Census Gazetteer files, 2024 (official city and county names): https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2024.html")
    w("")
    w("- [ ] Open the three links above. Each loads and is free to use without signing in.")
    w("")
    w(f"Of the 50 cities, **{len(open_p)}** state an open license for their storm layers, **{len(restr_p)}** state restrictions, and the rest state no terms. Check the {len(open_p) + len(restr_p)} that state terms. Each link opens the city's catalog page; the terms are in the section called **Terms of Use** or **License** (scroll down; on Socrata pages look under **About this dataset**).")
    w("")
    for g in sorted(by, key=lambda g: name[g]):
        if not (by[g] == {"open"} or "restricted" in by[g]):
            continue
        kind = "open" if by[g] == {"open"} else "restricted"
        r = next(x for x in terms if x["geoid"] == g and x["draft_redistribution"] == kind)
        excerpt = re.sub(r"\s+", " ", html.unescape(r["terms_text"]))[:160]
        w(f"- [ ] **{name[g]}** ({kind}): we read \"{excerpt}{'...' if len(r['terms_text']) > 160 else ''}\". [Open the catalog page]({item_page(r)}) ___")
    w("")
    cands = [r for r in csv.DictReader(open(os.path.join(PROC, "census_candidates.csv"), encoding="utf-8")) if r["include"] == "yes" and r["geoid"] in sel]
    w("Two catalog items picked at random from the 50 cities (seed " + str(SEED) + "). Each should still load and still show storm-drain data:")
    w("")
    for r in rng.sample(cands, min(2, len(cands))):
        w(f"- [ ] **{name.get(r['geoid'], r['census_name'])}**, \"{r['title']}\": [catalog page]({item_page(r)}) · [service]({r['url']}) ___")
    w("")
    w("The full text of every city's terms is in `data/processed/source_terms.csv`, column `terms_text`, if you want more.")
    w("")
    w("## Part 3: Check rows by hand (45 to 60 min)")
    w("")
    w("For each row: click the link, find the field named, and confirm the source value. Then confirm our value is the right reading of it.")
    w("")
    w("**How to read the record page.** The link opens the city's own ArcGIS server. The top of the page is a long search form: ignore it. Scroll to the very bottom, or press **Ctrl+F** and type the field name. Under `# results: 1` the record is listed one line per field, as `FIELD_NAME: value`, for example `DRN_PIPE_MAT: 7` or `INST_DATE: 1/15/1958 12:00:00 AM`. Each item below tells you which field to find and what it should say.")
    w("")
    w("- **Dates** show as month/day/year with a time; only the date matters. Some older servers show a date as a long number instead (for example `252460800000`): that is the same date stored as milliseconds since January 1, 1970. Where a date is involved, each item gives both forms, `1/1/1978` or `252460800000`; either one is a match.")
    w("- **Numbers that stand for words** (like material `7`) are explained on the layer's code list. Where this applies, the item says what the code means and gives a **code list** link: open it, press Ctrl+F, type the field name, and read the `Coded Values` after it, each written `[code: meaning]`. Long lists are shortened to `... 5 more ...`: click that to see the rest.")
    w("- **Our value** is the source value translated into the common schema (for example `RCP` stays RCP; `Concrete` becomes CONCRETE_CIP; a date becomes its year). Mark the item wrong if the translation is wrong, not only if the value differs.")
    w("")

    def item(e, city):
        parts, coded = [], False
        for k in KEY:
            if k not in e["props"]:
                continue
            fld = (e.get("fields") or {}).get(k) or "?"
            dec = (e.get("decoded") or {}).get(k)
            coded = coded or bool(dec)
            parts.append(f"`{fld}` should say `{e['raw'][k]}`" + (f" (code meaning \"{dec}\")" if dec else "") + f" → our {LABEL[k]} **{e['props'][k]}**")
        links = f"[Open the record]({record_link(e['url'], e['oidf'], e['oid'])})" + (f" · [code list]({e['url']}?f=html)" if coded else "")
        return f"- [ ] **{city}**, layer \"{e['layer']}\", record {e['oidf']} {e['oid']}. " + "; ".join(parts) + f". {links} ___"

    w("**Five in Texas cities you know:**")
    w("")
    for g in known:
        e = scans[g]["random"]
        w(item(e, name[g]) if e else f"- [ ] **{name[g]}**: no pipe feature with two or more key values to check; confirm on its portal that pipes carry few attributes. ___")
    w("")
    w("**Extreme values (the most likely to be errors):**")
    w("")
    ex = collections.defaultdict(list)
    for g, s in scans.items():
        for k, e in s["extremes"].items():
            ex[k].append((g, e))
    picks = [("max_diameter", "Largest pipe diameter in the 50 cities", lambda e: -e["props"]["diameter_in"]),
             ("min_year", "Oldest install year", lambda e: e["props"]["install_year"]),
             ("max_year", "Newest install year", lambda e: -e["props"]["install_year"]),
             ("cond5", "A pipe we rate condition 5 (failed)", lambda e: 0)]
    for k, label, key in picks:
        if ex[k]:
            g, e = sorted(ex[k], key=lambda ge: key(ge[1]))[0]
            w(f"- {label}:")
            w("  " + item(e, name[g]))
    big = max(scans.items(), key=lambda gs: sum(l["n"] for l in gs[1]["pipe_layers"]))
    lay = max(big[1]["pipe_layers"], key=lambda l: l["n"])
    w(f"- Largest pipe layer: **{name[big[0]]}**, \"{lay['name']}\", we hold **{lay['n']:,}** pipes after rules. [Open the live count]({count_link(lay['url'])}). The live count should be the same or close (cities edit daily; ditch, duplicate and capped-layer rules explain any gap). ___")
    w("")
    w(f"**Five random features (seed {SEED}):**")
    w("")
    for g in rand_pubs:
        e = scans[g]["random"]
        w(item(e, name[g]) if e else f"- [ ] **{name[g]}**: no pipe feature with two or more key values; nothing to check. ___")
    w("")
    w("## Part 4: Field mappings, the field-by-field QA sample (30 to 45 min)")
    w("")
    w("You accepted the drafted mappings in bulk on 2026-10-15. This sample is your by-hand check of that decision: 6 random accepted mappings for each key attribute, each in a different city. For each, ask: does this source field really hold that attribute, and are the example values read correctly? You can usually judge from the field name, its alias and the examples. Where unsure, open **Field list**: it is the layer's page on the city's server; press Ctrl+F, type the field name, and read its full label (alias) and, for coded fields, its `Coded Values`. Mark **right** or **wrong**. If more than 2 of the 30 are wrong, tell Claude; the mapping rules get fixed and rerun before release.")
    w("")
    for t in KEY:
        w(f"**{LABEL[t].capitalize()}:**")
        w("")
        for r in [r for r in sample if r["target_field"] == t]:
            exs = scans[r["geoid"]]["examples"].get(f"{r['source_layer_id']}|{r['source_field']}", [])
            shown = ", ".join(f"`{x['raw']}`" + (f" (\"{x['decoded']}\")" if x.get("decoded") else "") + f" → {x['value'] if x['status'] == 'valid' else x['status']}" for x in exs) or "no filled values in this layer"
            alias = f" (alias \"{r['source_alias']}\")" if r.get("source_alias") and r["source_alias"] != r["source_field"] else ""
            lu = scans[r["geoid"]].get("layer_urls", {}).get(r["source_layer_id"], "")
            link = ""
            if lu and "/resource/" not in lu:
                link = f" [Field list]({lu}?f=html) (Ctrl+F `{r['source_field']}`)"
            elif lu:
                link = f" [Dataset page](https://{lu.split('/')[2]}/d/{lu.rstrip('/').split('/')[-1]})"
            w(f"- [ ] **{name[r['geoid']]}**, {r['layer_type']} field `{r['source_field']}`{alias} → {LABEL[t]}. Examples: {shown}.{link} right / wrong ___")
        w("")
    w("Also skim these two short lists (10 min):")
    w("")
    w(f"- [ ] `docs/DATA_PATTERN_MAPPINGS.csv`: {n_pattern} mappings made from what the values are, not the field name (for example Spokane `PipeType` = DIP, PVC, RCP → material). Strike any you disagree with in the `elorm_decision` column.")
    w(f"- [ ] `docs/AUTO_CHECK_FLAGS.csv`: {len(flags) - n_warn} mappings the data check refused, plus {n_warn} accepted with a duplicate-ID warning. Nothing to do unless one looks wrongly refused.")
    w("")
    w("## Part 5: Your decisions (10 min)")
    w("")
    w(f"- [ ] **What the Zenodo dataset contains.** Only {len(open_p)} of 50 cities state an open license. Option A (recommended): publish the census, schema, crosswalk, value maps, scores, QA report and the scripts that rebuild the harmonized data from each city's live server, but no copies of the cities' features. Fully safe, and anyone can still rebuild everything. Option B: also include harmonized feature copies for the {len(open_p)} open-licensed cities. Your choice: ___")
    w("- [ ] **Provisional scores for the other ~520 publishers.** Recommended: leave them out of v1.0 (they are not reviewed) and release them in v1.1 after review. Alternative: include them, labeled provisional. Your choice: ___")
    w("- [ ] **Round Rock TX condition scale (-3 to 6).** Recommended: leave unscored in v1.0. Your choice: ___")
    w(f"- [ ] **The plan's wording.** Your plan says you do \"the field-by-field QA\". After Part 4, that is accurate as \"field-by-field QA on a random sample, with automated checks on every mapping\". Keep the plan as written only if you would rather review all {n_acc:,} mappings. Your choice: ___")
    w("")
    w("## Part 6: Before it goes public (20 to 30 min)")
    w("")
    w("- [ ] Your name, ORCID and affiliation are correct in `AUTHORS.json`. Right now the full name and ORCID are blank: reply to Claude with them.")
    w("- [ ] The README summary paragraph and the Zenodo description are rewritten in your own words (Claude can do this with you line by line).")
    w("- [ ] You have read `docs/LIMITATIONS.md` and `docs/DECISIONS.md` and agree with them.")
    w("- [ ] Draft stamps removed and the publish gate passes (Claude does this after you approve).")
    w("- [ ] Evidence log row written on release day.")
    w("")
    w("**When every box is ticked, reply to Claude in your own words that you have checked the package and approve publishing.**")
    with open(os.path.join(ROOT, "docs", "VERIFY_CHECKLIST.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"Checklist written: {len(sample)} mapping checks, {len(known)} known-city checks, {len(rand_pubs)} random checks. ALL DONE")


if __name__ == "__main__":
    main()
