"""Draft and apply field crosswalks (source field -> common-schema field).

A crosswalk row is a dict with at least:
  source_layer_id, layer_type, source_field, target_field
and optionally: unit_factor, value_map (JSON object as text), confidence, method,
reviewer_decision (ACCEPT / CHANGE / REJECT), reviewer_target, reviewer_note.

Drafts are suggestions only. In OpenStorm US, a mapping is used in a release
only after the reviewer_decision column is filled in by the schema owner.
"""

import csv
import difflib
import json
import re

# Edit-tracking and housekeeping fields: never a schema attribute, even if the name looks close.
NEVER_MAP = re.compile(r"^(created|creat|creation|creator|lastedit|lastedited|lastupdate|lastupdated|lastmod|modified|editor|editdate|edited|"
                       r"globalid|objectid|shape|enabled|comments?|notes?|remarks?|township|range|section|label|symbol)")
NO_FUZZY = {"asset_id", "owner", "inlet_type", "receiving_water"}  # too easy to guess wrong; exact synonyms only
FUZZY_MIN = 0.86
# Name parts that mean "this field is ABOUT the attribute, not the attribute itself" (a date of, a source of, a
# reference to another asset). Checked against the normalized field name and alias.
UNLESS = {
    "condition": r"date|dt$|time|by$|source|src|comment|note|method|reason|inspector|conditiond$|^condd",
    "install_year": r"source|src|method|by$|comment|note|contractor|project|plan|accuracy|quality|conf|id$|num$|no$|lin(ed|ing|er)|rehab|warrant|acqui|modif",
    "material": r"source|comment|note|lining|liner|coat|bedding|backfill|lid|cover|frame|grate|casting",
    "diameter_in": r"source|comment|note|lid|cover|frame|manhole|riser",
    "last_inspection_date": r"next|due|schedul|freq|interval|gps|by$|inspector|type|result|comment|note",
    "shape_type": r"width|height|wid$|hgt|size|dim|area|depth|span|rise|diam",
    "asset_id": r"^(up|down|dn|us|ds|from|to|start|end|begin|parent|related|conn|recv|receiv)|upstream|downstream|old|prev|legacy|orig|source|cad|work",
}
ID_OF = {"outfall": {"outfall"}, "inlet": {"inlet"}, "mh": {"manhole"}, "manhole": {"manhole"}, "pipe": {"pipe"}, "main": {"pipe"},
         "line": {"pipe"}, "pond": {"detention_basin"}, "basin": {"detention_basin"}, "bmp": {"detention_basin"},
         "structure": {"inlet", "manhole", "outfall"}, "struct": {"inlet", "manhole", "outfall"}, "node": {"inlet", "manhole", "outfall"}}


def diameter_unit_factor(name, alias, values):
    """'12' when a size field is in feet (said in its name or alias, e.g. "Height (ft)"), else ''."""
    text = re.sub(r"[_\-]+", " ", f"{name} {alias}".lower()).strip()
    if re.search(r"\(in\)|inch|\bin\b", text):
        return ""
    if re.search(r"\bft\b|\(ft\)|feet|foot", text):
        nums = []
        for v in values:
            try:
                nums.append(float(v))
            except (TypeError, ValueError):
                pass
        nums = [x for x in nums if x > 0]
        if not nums or max(nums) <= 20:
            return "12"
    return ""


def _blocked(target, cand, layer_type):
    """True when a candidate name should not map to target even though it looks like it."""
    pat = UNLESS.get(target)
    if pat and re.search(pat, cand):
        return True
    if target == "asset_id":
        m = re.match(r"^(outfall|inlet|mh|manhole|pipe|main|line|pond|basin|bmp|structure|struct|node)(id|no|num|number)$", cand)
        if m and layer_type not in ID_OF[m.group(1)]:
            return True  # e.g. OUTFALLID on a pipe layer points to another asset
    return False


# Keyword rules: a field whose normalized name contains the keyword maps to the target (medium confidence).
CONTAINS = [
    (r"insp.*(date|dt)|lastinsp|inspected|inspdt", "last_inspection_date"),
    (r"install|instdate|dateinst|yrbuilt|yearbuilt|constyear|yrconst|yearconst|constructed|builtyear|yearinsta|yrinst", "install_year"),
    (r"diam|pipesize|pipedia", "diameter_in"),
    (r"materi|matl|pipemat", "material"),
    (r"conditi|condrat|condscore|pacp", "condition"),
    (r"xsec.*(width|height|rise|span)|crosssection(width|height|rise|span)|pipe(width|height|rise)|^(rise|span)$", "diameter_in"),
    (r"crosssection|pipeshape|xsec", "shape_type"),
    (r"lifecycle|activeflag|operstat|opstatus", "lifecycle_status"),
    (r"upinv|usinv|invup|upstreaminv|startinv|frominv", "us_invert_ft"),
    (r"dninv|dsinv|downinv|invdn|downstreaminv|endinv|toinv", "ds_invert_ft"),
    (r"rimel|rimelev|topelev", "rim_elev_ft"),
    (r"structuretype|structtype|inlettype|cbtype", "inlet_type"),
    (r"receivingw|receivwat", "receiving_water"),
    (r"maint(ained)?_?by|managed_?by|custodian", "custodian"),
    (r"ownedby|ownership|^owner", "owner"),
    (r"facilityid|assetid|assetnum", "asset_id"),
]

from .geom import clean_geometry
from .schema import fields_for, norm_name
from .values import normalize

CROSSWALK_COLUMNS = [
    "source_layer_id", "layer_type", "source_field", "source_alias", "source_type", "sample_values",
    "target_field", "confidence", "method", "unit_factor", "value_map",
    "reviewer_decision", "reviewer_target", "reviewer_note",
]


def draft_mapping(fields, layer_type, schema, samples=None, layer_id=""):
    """fields: list of dicts with name, alias, type (as in an ArcGIS layer's 'fields').
    Returns one crosswalk row per source field (target '' when no suggestion)."""
    samples = samples or {}
    targets = fields_for(schema, layer_type)
    syn = {}
    for t in targets:
        for s in t.get("synonyms", []) + [t["name"]]:
            syn.setdefault(norm_name(s), t["name"])
    rows, used = [], {}
    for f in fields:
        nm, alias = f.get("name", ""), f.get("alias") or ""
        ftype = str(f.get("type", "")).replace("esriFieldType", "")
        if ftype in ("OID", "Geometry", "GlobalID") or norm_name(nm) in ("shapelength", "shapearea", "shapelen", "objectid", "fid", "globalid"):
            continue
        target, conf, method = "", 0.0, ""
        if NEVER_MAP.match(norm_name(nm)) and norm_name(nm) not in syn:
            rows.append({"source_layer_id": layer_id, "layer_type": layer_type, "source_field": nm, "source_alias": alias,
                         "source_type": ftype, "sample_values": json.dumps(samples.get(nm, [])[:5], default=str),
                         "target_field": "", "confidence": 0.0, "method": "housekeeping field", "unit_factor": "", "value_map": "",
                         "reviewer_decision": "", "reviewer_target": "", "reviewer_note": ""})
            continue
        for cand in (norm_name(nm), norm_name(alias)):
            if cand and cand in syn and not _blocked(syn[cand], cand, layer_type):
                target, conf, method = syn[cand], 0.95, "synonym"
                break
        if not target:
            tnames = {t["name"] for t in targets}
            for cand in (norm_name(nm), norm_name(alias)):
                hit = next((t for pat, t in CONTAINS if t in tnames and cand and re.search(pat, cand) and not _blocked(t, cand, layer_type)), None)
                if hit:
                    target, conf, method = hit, 0.75, "keyword"
                    break
        if not target:
            best, score = "", 0.0
            for key, tname in syn.items():
                if tname in NO_FUZZY:
                    continue
                for cand in (norm_name(nm), norm_name(alias)):
                    if not cand:
                        continue
                    if _blocked(tname, cand, layer_type):
                        continue
                    r = difflib.SequenceMatcher(None, cand, key).ratio()
                    if r > score:
                        best, score = tname, r
            if score >= FUZZY_MIN:
                target, conf, method = best, round(score * 0.8, 2), "fuzzy"
        unit_factor = diameter_unit_factor(nm, alias, samples.get(nm, [])) if target == "diameter_in" else ""
        rows.append({
            "source_layer_id": layer_id, "layer_type": layer_type, "source_field": nm, "source_alias": alias,
            "source_type": ftype, "sample_values": json.dumps(samples.get(nm, [])[:5], default=str),
            "target_field": target, "confidence": conf, "method": method, "unit_factor": unit_factor,
            "value_map": "", "reviewer_decision": "", "reviewer_target": "", "reviewer_note": "",
        })
        if target:
            used.setdefault(target, []).append(rows[-1])
    # If two source fields claim one target, keep the higher-confidence one and flag the rest.
    for target, rs in used.items():
        if len(rs) > 1:
            rs.sort(key=lambda r: -float(r["confidence"]))
            for r in rs[1:]:
                r["method"] = (r["method"] + ";duplicate_target").strip(";")
                r["confidence"] = round(float(r["confidence"]) * 0.5, 2)
    return rows


def effective_rows(rows, require_review=False):
    """Rows that actually map, honoring reviewer decisions. With require_review, only ACCEPT/CHANGE rows count."""
    out = []
    for r in rows:
        dec = (r.get("reviewer_decision") or "").strip().upper()
        if dec == "REJECT":
            continue
        if require_review and dec not in ("ACCEPT", "CHANGE"):
            continue
        tgt = (r.get("reviewer_target") or "").strip() if dec == "CHANGE" else r.get("target_field", "")
        if not tgt or "duplicate_target" in (r.get("method") or "") and dec != "ACCEPT":
            continue
        rr = dict(r)
        rr["target_field"] = tgt
        vm = rr.get("value_map")
        rr["value_map"] = json.loads(vm) if isinstance(vm, str) and vm.strip() else (vm or {})
        out.append(rr)
    return out


def harmonize(features, rows, layer_type, schema, keep_raw=False):
    """Map raw GeoJSON features into common-schema records.

    Returns a list of dicts: {"geometry": ..., "props": {field: value}, "status": {field: status}}.
    Fields with no mapping get status 'unmapped'."""
    by_target = {}
    for r in rows:
        if r["layer_type"] == layer_type:
            by_target.setdefault(r["target_field"], r)
    fields = fields_for(schema, layer_type)
    out = []
    for feat in features:
        p = feat.get("properties") or {}
        lower = {k.lower(): v for k, v in p.items()}
        rec = {"geometry": clean_geometry(feat.get("geometry")), "props": {}, "status": {}}
        if keep_raw:
            rec["raw"] = {}
        for fd in fields:
            r = by_target.get(fd["name"])
            if not r:
                rec["status"][fd["name"]] = "unmapped"
                continue
            raw = p.get(r["source_field"], lower.get(str(r["source_field"]).lower()))
            val, st = normalize(fd, raw, schema, r)
            rec["props"][fd["name"]], rec["status"][fd["name"]] = val, st
            if keep_raw:
                rec["raw"][fd["name"]] = raw
        out.append(rec)
    return out


def read_crosswalk(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_crosswalk(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CROSSWALK_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            rr = dict(r)
            if isinstance(rr.get("value_map"), dict):
                rr["value_map"] = json.dumps(rr["value_map"]) if rr["value_map"] else ""
            w.writerow(rr)
