"""Step 3.5: apply the author's bulk review decisions to the v1.0 publishers.

Plain language: on 2026-10-15 Elorm told Claude to accept the drafted crosswalk and to use
default value maps. This script does that, but checks each mapping against the actual
data first. A mapping is accepted unless the data contradicts it (for example a field
mapped to "diameter" whose values are mostly text, or an "asset ID" whose values repeat).
Rows that fail a check are left unaccepted and listed in docs/AUTO_CHECK_FLAGS.csv so
Elorm can rule on them. Nothing here overwrites a decision already written in a crosswalk.

What it writes
  data/processed/crosswalk/<geoid>.csv   reviewer_decision = ACCEPT (or blank + note), value_map
  data/processed/value_maps_v1.csv        every coded value seen, how it was decoded, what it maps to
  docs/AUTO_CHECK_FLAGS.csv               mappings not accepted, with the reason
  data/processed/condition_scales.csv     condition fields whose numeric scale could not be read

Run:  python code/035_apply_review_defaults.py [--budget SECONDS]   (resumable per publisher)
"""

import argparse
import collections
import csv
import datetime
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
from common import PROC, RAW, ROOT, drop_excluded, get_json, layer_file, load_json, load_layer, save_json, slug, used_layers  # noqa: E402
from openstorm_qa.crosswalk import diameter_unit_factor, read_crosswalk, write_crosswalk  # noqa: E402
from openstorm_qa.schema import load_schema  # noqa: E402
from openstorm_qa.values import SCALE_0_100, condition_from_text, normalize  # noqa: E402

DECIDED = "2026-10-15"
NOTE_OK = f"accepted in bulk by author {DECIDED}; passed automated data check"
SAMPLE = 20000
STATE = os.path.join(RAW, "_state")
NUMERIC_TARGETS = {"diameter_in", "install_year", "last_inspection_date", "us_invert_ft", "ds_invert_ft", "rim_elev_ft"}
CODED = {"material", "condition", "lifecycle_status", "shape_type", "basin_type"}


def layer_domains(url):
    """Coded-value domains of an ArcGIS layer's fields: {field: {code: description}}. Uses the harvest cache."""
    meta = None
    for key in ("lyr " + url, "svc " + url):
        p = os.path.join(STATE, "http", slug(key, 150) + ".json")
        if os.path.exists(p):
            meta = load_json(p)
            break
    if meta is None and "/resource/" not in url:
        try:
            meta = get_json(url, {"f": "json"})
            save_json(meta, os.path.join(STATE, "http", slug("lyr " + url, 150) + ".json"))
        except RuntimeError:
            meta = {}
    out = {}
    for f in (meta or {}).get("fields") or []:
        dom = f.get("domain") or {}
        if dom.get("type") == "codedValue":
            out[f["name"]] = {str(c["code"]): str(c["name"]) for c in dom.get("codedValues") or []}
    # subtype-specific domains
    for st in (meta or {}).get("types") or []:
        for fname, dom in (st.get("domains") or {}).items():
            if (dom or {}).get("type") == "codedValue":
                out.setdefault(fname, {}).update({str(c["code"]): str(c["name"]) for c in dom.get("codedValues") or []})
    return out


NOT_RATED = {0.0, -1.0, 9.0, 88.0, 99.0, 999.0, 888.0, -9.0, -99.0, -999.0, -9999.0, 9999.0, 8888.0, 99999.0}


def numeric_scale(values):
    """(min, max) of a numeric field, ignoring 'not rated' codes; None if any value is text."""
    nums = []
    for v in values:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        if x not in NOT_RATED:
            nums.append(x)
    return (min(nums), max(nums)) if nums else None


def build_value_map(target, field_name, counts, domain, schema):
    """Default value map for one coded field. Returns (value_map, rows for value_maps_v1.csv, scale_problem or None)."""
    fd = schema["_fields_by_name"][target]
    vmap, log = {}, []
    raw_vals = [v for v in counts if v not in ("None", "")]
    if target == "condition" and not domain:
        sc = numeric_scale(raw_vals)
        if sc and sc[0] >= 0 and 5 < sc[1] <= 100:
            vm = {"__scale__": SCALE_0_100}
            log = []
            for v in raw_vals:
                m, st = normalize(schema["_fields_by_name"]["condition"], v, schema, {"value_map": vm})
                log.append({"raw_value": v, "count": counts[v], "decoded": "", "mapped_to": m or "",
                            "rule": "0 to 100 scale, 100 = best (Elorm 2026-10-15)" if m else "not rated code, treated as missing"})
            return vm, log, None
        if sc and not (sc[0] >= 1 and sc[1] <= 5):
            return {}, [{"raw_value": v, "count": counts[v], "decoded": "", "mapped_to": "", "rule": f"numeric scale {sc[0]:g} to {sc[1]:g}: direction unknown, left unmapped"} for v in raw_vals], sc
    for v in raw_vals:
        decoded = domain.get(v, "") if domain else ""
        source = decoded or v
        if domain and decoded:
            if target == "condition":
                mapped = condition_from_text(decoded)
                rule = "domain description -> condition words"
            else:
                mapped, st = normalize(fd, decoded, schema)
                mapped = mapped if st == "valid" else None
                rule = "domain description -> schema value"
            vmap[v] = mapped
        else:
            mapped, st = normalize(fd, source, schema)
            if st != "valid":
                mapped = None
            rule = "schema default rules" if mapped is not None else ("undecoded code, treated as missing" if str(v).strip().isdigit() else "not recognized")
            if target == "material" and mapped is None and str(v).strip().isdigit():
                vmap[v] = None
        log.append({"raw_value": v, "count": counts[v], "decoded": decoded, "mapped_to": "" if mapped is None else mapped, "rule": rule})
    return vmap, log, None


def _mostly_fractional(vals):
    """True when most filled numeric values carry more than two decimals."""
    nums = []
    for v in vals:
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if x > 0:
            nums.append(x)
    if len(nums) < 10:
        return False
    frac = sum(1 for x in nums if abs(round(x, 2) - x) > 1e-9)
    return frac / len(nums) > 0.5


def check_row(r, feats, schema, vmap):
    """Data check for one mapping. Returns (n_nonmissing, n_valid, problem or '')."""
    tgt = r["target_field"]
    fd = schema["_fields_by_name"].get(tgt)
    if not fd:
        return 0, 0, "target not in schema"
    rule = {"unit_factor": r.get("unit_factor"), "value_map": vmap}
    vals = [(ft.get("properties") or {}).get(r["source_field"]) for ft in feats]
    nonmiss = valid = 0
    seen = []
    for v in vals:
        x, st = normalize(fd, v, schema, rule)
        if st != "missing":
            nonmiss += 1
        if st == "valid":
            valid += 1
            if tgt == "asset_id":
                seen.append(x)
    problem = ""
    if nonmiss:
        share = valid / nonmiss
        if tgt == "asset_id" and seen and len(set(seen)) / len(seen) < 0.9:
            if r.get("method", "").startswith("synonym"):
                r["_warning"] = f"ID values repeat ({len(set(seen))} distinct of {len(seen)} in sample): a data-quality finding, mapping kept"
            else:
                problem = f"values repeat ({len(set(seen))} distinct of {len(seen)}); looks like a reference to other assets, not this asset's ID"
        elif tgt == "diameter_in" and _mostly_fractional(vals):
            problem = "most values have many decimals (e.g. 71.844302): not nominal pipe sizes, likely a length or area"
        elif tgt in NUMERIC_TARGETS and share < 0.5:
            problem = f"only {share:.0%} of filled values are valid for {tgt}"
        elif tgt == "condition" and share < 0.5:
            problem = f"only {share:.0%} of filled values could be read as a 1-5 condition"
        elif fd["type"] == "enum" and "OTHER" in fd.get("allowed", []):
            other = sum(1 for v in vals if normalize(fd, v, schema, rule)[0] == "OTHER")
            if other / nonmiss > 0.8:
                problem = f"{other / nonmiss:.0%} of filled values are not recognizable as {tgt}"
    return nonmiss, valid, problem


HOUSEKEEPING = r"creat|edit|update|modif|last|user|globalid|objectid|shape|comment|note|remark|next|due|plan|cip|warrant|insp|cond|rehab|lin(ed|ing|er)|acqui|attr|^att|valid|qa|qc|gps|survey|collect"
# Install year needs a positive "built / installed / constructed" signal in the name; other dates are events, not install years.
YEARISH = r"built|build|bld|const|inst|^years?$|^yr$"
NOT_INSTALL = r"tele|cctv|stencil|visit|assess|submi|source|rvs|review|insp|survey|clean|maint|repair|record|entry|data"
SIZEISH = r"width|height|rise|span|xsec|size|dia"
NOT_SIZE = r"top|bottom|bot|channel|ditch|bank|road|lane|crown|easement|swale"
NOT_MATERIAL = r"project|popup|info|note|comment|remark|desc_?2|status"


def data_pattern(lrows, feats, layer_type, schema, already):
    """Unmapped fields whose VALUES plainly are a key attribute (e.g. PipeType = DIP/PVC/RCP).
    Only fills a key attribute that has no accepted mapping in this layer. Returns [(row, target, evidence)]."""
    from openstorm_qa.schema import key_attributes_for
    from openstorm_qa.values import MATERIAL_MAP, material_from_text, parse_date
    need = [t for t in ("material", "install_year", "diameter_in") if t in key_attributes_for(schema, layer_type) and t not in already]
    out, taken = [], set()
    for r in lrows:
        if not need or r["target_field"] or (r.get("reviewer_decision") or "").strip() or r.get("method") == "housekeeping field":
            continue
        name = r["source_field"].lower()
        vals = [(ft.get("properties") or {}).get(r["source_field"]) for ft in feats[:5000]]
        filled = [v for v in vals if v not in (None, "", " ") and str(v).strip().upper() not in ("UNK", "UNKNOWN", "NULL", "N/A", "NONE")]
        if len(filled) < 20:
            continue
        if "material" in need and "material" not in taken and not re.search(NOT_MATERIAL, name):
            codes = [MATERIAL_MAP.get(re.sub(r"[^A-Z0-9]", "", str(v).upper())) or material_from_text(v) for v in filled]
            rec = [c for c in codes if c]
            if len(rec) / len(filled) >= 0.6 and len(set(rec)) >= 2:
                out.append((r, "material", f"{len(rec) / len(filled):.0%} of filled values are material codes ({', '.join(sorted(set(rec))[:4])})"))
                taken.add("material")
                continue
        if re.search(HOUSEKEEPING, name):
            continue
        if "install_year" in need and "install_year" not in taken and re.search(YEARISH, name) and not re.search(NOT_INSTALL, name):
            yrs = [parse_date(v) for v in filled]
            ok = [d for d in yrs if d and 1850 <= d.year <= datetime.date.today().year]
            if len(ok) / len(filled) >= 0.8:
                out.append((r, "install_year", f"{len(ok) / len(filled):.0%} of filled values are years {min(d.year for d in ok)} to {max(d.year for d in ok)}"))
                taken.add("install_year")
                continue
        if "diameter_in" in need and "diameter_in" not in taken and re.search(SIZEISH, name) and not re.search(NOT_SIZE, name):
            nums = []
            for v in filled:
                try:
                    nums.append(float(v))
                except (TypeError, ValueError):
                    pass
            uf = diameter_unit_factor(r["source_field"], r.get("source_alias", ""), nums[:50])
            scaled = [x * (12 if uf else 1) for x in nums]
            ok = [x for x in scaled if 2 <= x <= 240]
            if len(ok) / len(filled) >= 0.8 and not _mostly_fractional(nums):
                r["unit_factor"] = uf
                out.append((r, "diameter_in", f"{len(ok) / len(filled):.0%} of filled values are sizes {min(ok):g} to {max(ok):g} in" + (" (converted from feet)" if uf else "")))
                taken.add("diameter_in")
    return out


def process(geoid, schema, used):
    xw_path = os.path.join(PROC, "crosswalk", f"{geoid}.csv")
    rows = read_crosswalk(xw_path)
    metas = {os.path.relpath(layer_file(mp), RAW): mp for mp in glob.glob(os.path.join(RAW, "layers", geoid, "*.meta.json"))}
    flags, vlog, scales, warnings, patterns = [], [], [], [], []
    by_layer = collections.defaultdict(list)
    for r in rows:
        by_layer[r["source_layer_id"]].append(r)
    for lid, lrows in by_layer.items():
        if lid not in metas or (used is not None and lid not in used):
            continue
        meta = load_json(metas[lid])
        feats = drop_excluded(load_layer(metas[lid], limit=SAMPLE), (used or {}).get(lid))
        domains = layer_domains(meta["url"])
        cands = collections.defaultdict(list)
        for r in lrows:
            if (r.get("reviewer_decision") or "").strip():
                continue  # a human decision already exists: never touch it
            if not r["target_field"]:
                continue
            vmap = {}
            if r["target_field"] in CODED:
                counts = collections.Counter(str((ft.get("properties") or {}).get(r["source_field"])) for ft in feats)
                vmap, log, sc = build_value_map(r["target_field"], r["source_field"], counts, domains.get(r["source_field"]), schema)
                for lg in log:
                    vlog.append(dict(lg, geoid=geoid, source_layer_id=lid, source_field=r["source_field"], target_field=r["target_field"]))
                if sc:
                    scales.append({"geoid": geoid, "source_layer_id": lid, "source_field": r["source_field"], "min": sc[0], "max": sc[1],
                                   "n_values": len(counts), "elorm_rule": ""})
            elif domains.get(r["source_field"]) and r["target_field"] in NUMERIC_TARGETS:
                vmap = {k: d for k, d in domains[r["source_field"]].items()}
            nonmiss, valid, problem = check_row(r, feats, schema, vmap)
            r["value_map"] = json.dumps(vmap) if vmap else ""
            cands[r["target_field"]].append((valid, -len(problem), float(r["confidence"] or 0), r, problem, nonmiss))
        accepted_now = set()
        for tgt, cs in cands.items():
            if any(not c[4] for c in cs):
                accepted_now.add(tgt)
        if common.is_open_conveyance(meta):
            accepted_now.add("diameter_in")  # diameter does not apply to swales and channels
        for r, tgt, why in data_pattern(lrows, feats, meta["layer_type"], schema, accepted_now):
            r["target_field"], r["method"] = tgt, "data pattern"
            r["reviewer_decision"] = "ACCEPT"
            r["reviewer_note"] = f"accepted in bulk by author {DECIDED}; mapping found by data pattern ({why}), not by field name"
            patterns.append({"geoid": geoid, "source_layer_id": lid, "layer_type": r["layer_type"], "source_field": r["source_field"],
                             "target_field": tgt, "evidence": why, "sample_values": r["sample_values"], "elorm_decision": "", "elorm_note": ""})
        for tgt, cs in cands.items():
            def pref(c):
                n = c[3]["source_field"].lower()
                if tgt == "condition":  # structural condition beats maintenance (O&M) condition
                    return 1 if re.search(r"struct|_s$|str$", n) else (-1 if re.search(r"_?om$|o&m|maint|_m$", n) else 0)
                return 0
            cs.sort(key=lambda c: (c[1] == 0, c[0], pref(c), c[2]), reverse=True)
            if tgt == "diameter_in" and len(cs) > 1:
                # Schema: diameter = inside diameter, or the RISE (height) for non-round pipe. Prefer a height/rise
                # field when it is valid at least 90% as often as the best field (Dublin OH: 240 x 72 in box -> 72).
                best = cs[0][0]
                rise = [c for c in cs if not c[4] and re.search(r"height|rise|hgt|vert", c[3]["source_field"].lower() + " " + (c[3].get("source_alias") or "").lower()) and c[0] >= 0.9 * best]
                if rise and rise[0] is not cs[0]:
                    cs.remove(rise[0])
                    cs.insert(0, rise[0])
            for k, (valid, _, _, r, problem, nonmiss) in enumerate(cs):
                if k == 0 and not problem:
                    r["reviewer_decision"], r["reviewer_note"] = "ACCEPT", NOTE_OK + f" ({valid} of {nonmiss} filled values valid in sample)"
                    if r.get("_warning"):
                        r["reviewer_note"] += "; " + r["_warning"]
                        warnings.append({"geoid": geoid, "source_layer_id": lid, "layer_type": r["layer_type"], "source_field": r["source_field"],
                                         "target_field": tgt, "reason": "ACCEPTED WITH WARNING: " + r["_warning"], "sample_values": r["sample_values"],
                                         "elorm_decision": "", "elorm_note": ""})
                else:
                    reason = problem or f"another field ({cs[0][3]['source_field']}) was chosen for {tgt}"
                    r["reviewer_note"] = f"not accepted by automated check {DECIDED}: {reason}"
                    if problem:
                        flags.append({"geoid": geoid, "source_layer_id": lid, "layer_type": r["layer_type"], "source_field": r["source_field"],
                                      "target_field": tgt, "reason": problem, "sample_values": r["sample_values"],
                                      "elorm_decision": "", "elorm_note": ""})
    for r in rows:
        r.pop("_warning", None)
    write_crosswalk(rows, xw_path)
    return flags + warnings, vlog, scales, patterns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=0)
    a = ap.parse_args()
    t_end = time.time() + a.budget if a.budget else None
    common.DEADLINE[0] = t_end
    schema = load_schema()
    used = used_layers()
    with open(os.path.join(PROC, "v1_selection.csv"), newline="", encoding="utf-8") as f:
        sel = [r["geoid"] for r in csv.DictReader(f) if r["in_v1"] == "yes"]
    done_dir = os.path.join(STATE, "review_defaults")
    os.makedirs(done_dir, exist_ok=True)
    done = set(os.listdir(done_dir))
    for g in sel:
        if f"{g}.json" in done:
            continue
        if t_end and time.time() > t_end:
            print("MORE TO DO (run again)")
            return
        flags, vlog, scales, patterns = process(g, schema, used)
        save_json({"flags": flags, "values": vlog, "scales": scales, "patterns": patterns}, os.path.join(done_dir, f"{g}.json"))
    flags, vlog, scales, patterns = [], [], [], []
    for g in sel:
        d = load_json(os.path.join(done_dir, f"{g}.json"))
        flags += d["flags"]
        vlog += d["values"]
        scales += d["scales"]
        patterns += d.get("patterns", [])
    _write(os.path.join(ROOT, "docs", "DATA_PATTERN_MAPPINGS.csv"), patterns,
           ["geoid", "source_layer_id", "layer_type", "source_field", "target_field", "evidence", "sample_values", "elorm_decision", "elorm_note"])
    _write(os.path.join(ROOT, "docs", "AUTO_CHECK_FLAGS.csv"), flags,
           ["geoid", "source_layer_id", "layer_type", "source_field", "target_field", "reason", "sample_values", "elorm_decision", "elorm_note"])
    _write(os.path.join(PROC, "value_maps_v1.csv"), vlog,
           ["geoid", "source_layer_id", "source_field", "target_field", "raw_value", "count", "decoded", "mapped_to", "rule"])
    _write(os.path.join(PROC, "condition_scales.csv"), scales, ["geoid", "source_layer_id", "source_field", "min", "max", "n_values", "elorm_rule"])
    acc = sum(1 for g in sel for r in read_crosswalk(os.path.join(PROC, "crosswalk", f"{g}.csv")) if r.get("reviewer_decision") == "ACCEPT")
    print(f"v1 publishers: {len(sel)}  |  mappings accepted: {acc}  |  flagged by data check: {len(flags)}  |  "
          f"coded values mapped: {sum(1 for v in vlog if v['mapped_to'])} of {len(vlog)}  |  unreadable condition scales: {len(scales)}  |  "
          f"data-pattern mappings: {len(patterns)}")
    print("ALL DONE")


def _write(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
