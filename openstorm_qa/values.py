"""Turn raw source values into common-schema values.

Every normalizer returns (value, status) where status is one of
'valid', 'missing' (blank, sentinel or placeholder) or 'invalid'
(present but out of range or unparseable). Attribute coverage counts
only 'valid'.
"""

import datetime
import re

from .schema import resolve_bound

# Material lookup (reviewed by the author for v1.0). Keys are normalized (uppercase, letters/digits only).
# Elorm rules on this table; unmatched non-blank values become OTHER.
MATERIAL_MAP = {
    "RCP": "RCP", "REINFORCEDCONCRETE": "RCP", "REINFORCEDCONCRETEPIPE": "RCP", "RCPPIPE": "RCP", "RC": "RCP",
    "CONC": "CONCRETE_CIP", "CONCRETE": "CONCRETE_CIP", "CIPCONCRETE": "CONCRETE_CIP", "CASTINPLACE": "CONCRETE_CIP", "PRECAST": "CONCRETE_CIP", "PRECASTCONCRETE": "CONCRETE_CIP",
    "CMP": "CMP", "CORRUGATEDMETAL": "CMP", "CORRUGATEDMETALPIPE": "CMP", "CSP": "CMP", "CORRUGATEDSTEEL": "CMP", "CAP": "CMP", "CORRUGATEDALUMINUM": "CMP",
    "PVC": "PVC", "POLYVINYLCHLORIDE": "PVC", "C900": "PVC", "SDR35": "PVC",
    "HDPE": "HDPE", "HIGHDENSITYPOLYETHYLENE": "HDPE", "PE": "HDPE", "POLYETHYLENE": "HDPE", "ADS": "HDPE", "N12": "HDPE",
    "CPP": "CPP", "CORRUGATEDPLASTIC": "CPP", "CORRUGATEDPOLYETHYLENE": "CPP", "PP": "CPP", "POLYPROPYLENE": "CPP",
    "VCP": "VCP", "VITRIFIEDCLAY": "VCP", "CLAY": "VCP", "VC": "VCP",
    "DIP": "DIP", "DUCTILEIRON": "DIP", "DI": "DIP",
    "CIP": "CIP", "CI": "CIP", "CASTIRON": "CIP",
    "STEEL": "STEEL", "STL": "STEEL",
    "BRICK": "BRICK", "BRK": "BRICK",
    "MASONRY": "MASONRY", "STONE": "MASONRY", "BLOCK": "MASONRY",
}

SHAPE_MAP = {"CIRCULAR": "CIRCULAR", "ROUND": "CIRCULAR", "CIRC": "CIRCULAR", "C": "CIRCULAR",
             "BOX": "BOX", "RECTANGULAR": "BOX", "RECT": "BOX", "RCB": "BOX", "BOXCULVERT": "BOX",
             "ARCH": "ARCH", "PIPEARCH": "ARCH", "ELLIPTICAL": "ELLIPTICAL", "ELLIPSE": "ELLIPTICAL", "HE": "ELLIPTICAL", "VE": "ELLIPTICAL"}

STATUS_MAP = {"ACTIVE": "ACTIVE", "INSERVICE": "ACTIVE", "A": "ACTIVE", "Y": "ACTIVE", "YES": "ACTIVE", "EXISTING": "ACTIVE", "EX": "ACTIVE",
              "ABANDONED": "ABANDONED", "ABAN": "ABANDONED", "ABD": "ABANDONED", "INACTIVE": "ABANDONED",
              "REMOVED": "REMOVED", "PROPOSED": "PROPOSED", "PROP": "PROPOSED", "PLANNED": "PROPOSED"}

BASIN_MAP = {"WET": "WET", "WETPOND": "WET", "RETENTION": "WET", "DRY": "DRY", "DRYPOND": "DRY", "DETENTION": "DRY",
             "UNDERGROUND": "UNDERGROUND", "VAULT": "UNDERGROUND"}


# Keyword fallbacks, checked in order, for descriptive text that is not an exact code.
MATERIAL_KEYWORDS = [
    (r"\bhdpe\b|high[- ]density", "HDPE"),
    (r"corrugated (plastic|polyethylene|poly)|\bcpp\b|polypropylene|\bpp\b|\bn-?12\b|\bads\b", "CPP"),
    (r"\brcp\b|reinforced concrete|\brc pipe", "RCP"),
    (r"corrugated (metal|steel|alum)|\bcmp\b|\bcsp\b|\bcap\b|galvanized", "CMP"),
    (r"\bpvc\b|polyvinyl|\bsdr\b|\bc-?900\b", "PVC"),
    (r"polyethylene|\bpe\b|plastic", "HDPE"),
    (r"vitrified|\bvcp\b|\bclay\b|terra ?cotta", "VCP"),
    (r"ductile|\bdip\b", "DIP"),
    (r"cast iron|\bcip\b|\bci\b", "CIP"),
    (r"\bsteel\b|\bmetal\b|\biron\b", "STEEL"),
    (r"\bbrick", "BRICK"),
    (r"stone|masonry|block|rubble", "MASONRY"),
    (r"concrete|\bconc\b|precast|cast[- ]in[- ]place", "CONCRETE_CIP"),
]
CONDITION_WORDS = [
    (r"very poor|fail|critical|collaps|severe|immediate|unusable|destroyed|imminent", "5"),
    (r"excellent|very good|like new|^new$|\bnew\b", "1"),
    (r"poor|bad|deteriorat|major", "4"),
    (r"fair|average|moderate|satisfactory|minor|\bok\b|adequate", "3"),
    (r"good", "2"),
]
CONDITION_NOT_RATED = {"INACCESSIBLE", "NOT INSPECTED", "NOT RATED", "NOT ASSESSED", "UNRATED", "NR", "NI", "N/R", "NOT ACCESSIBLE", "BURIED", "NOT FOUND"}
CONDITION_NOT_RATED_CODES = (0, -1, 9, 88, 99, 999, 888)
LETTER_GRADES = {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5", "F": "5"}


# 0 to 100 condition scales: 100 = best (Elorm, 2026-10-15). Bins: 80-100 -> 1, 60-79 -> 2, 40-59 -> 3, 20-39 -> 4, below 20 -> 5.
SCALE_0_100 = "0-100, 100 = best"


def condition_from_0_100(x):
    return "1" if x >= 80 else "2" if x >= 60 else "3" if x >= 40 else "4" if x >= 20 else "5"


def material_from_text(v):
    t = str(v).lower()
    for pat, code in MATERIAL_KEYWORDS:
        if re.search(pat, t):
            return code
    return None


def condition_from_text(v):
    t = str(v).strip().lower()
    m = re.match(r"^(?:pacp\s*)?(?:grade|rating|score|level|cond(?:ition)?)?\s*([1-5])(?:\s*[-:].*)?$", t)
    if m:
        return m.group(1)
    if t.upper() in LETTER_GRADES:
        return LETTER_GRADES[t.upper()]
    for pat, code in CONDITION_WORDS:
        if re.search(pat, t):
            return code
    return None


def _key(v):
    return re.sub(r"[^A-Z0-9]", "", str(v).upper())


def is_missing(v, schema):
    if v is None:
        return True
    if isinstance(v, float) and v != v:  # NaN
        return True
    if isinstance(v, str):
        return v.strip().upper() in {m.upper() for m in schema["missing_values"]}
    return False


def _to_number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.match(r'^\s*(-?\d+(?:\.\d+)?)\s*(?:"|in|inch|inches)?\s*$', str(v), re.I)
    return float(m.group(1)) if m else None


def parse_date(v):
    """Return a datetime.date from ArcGIS epoch milliseconds, ISO text or US text, else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if 18500101 <= v <= 21001231 and float(v).is_integer() and 1 <= int(v) // 100 % 100 <= 12:  # yyyymmdd as a number
            try:
                return datetime.date(int(v) // 10000, int(v) // 100 % 100, max(1, int(v) % 100))
            except ValueError:
                pass
        if abs(v) >= 1e9:  # epoch milliseconds (ArcGIS); covers every date after 1970-01-12 and before 1969-12-20
            try:
                return (datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=v)).date()
            except OverflowError:
                return None
        if 1800 <= v <= 2200:  # bare year
            return datetime.date(int(v), 1, 1)
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%m/%d/%Y %H:%M:%S", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(s[:26].rstrip("Z"), fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{4})$", s)
    if m:
        return datetime.date(int(m.group(1)), 1, 1)
    return None


def normalize(field, raw, schema, rule=None):
    """Normalize one raw value for one schema field. rule is the crosswalk row (may carry
    'unit_factor' and 'value_map')."""
    rule = rule or {}
    if is_missing(raw, schema):
        return None, "missing"
    vmap = dict(rule.get("value_map") or {})
    scale = vmap.pop("__scale__", None)
    if scale == SCALE_0_100 and field["name"] == "condition":
        x = _to_number(raw)
        if x is None or x in CONDITION_NOT_RATED_CODES or x in schema["numeric_missing_sentinels"] or x < 0:
            return None, ("missing" if x is not None or is_missing(raw, schema) else "invalid")
        if x > 100:
            return None, "invalid"
        return condition_from_0_100(x), "valid"
    if vmap:
        k = str(raw).strip()
        if k in vmap:
            raw = vmap[k]
        elif _key(k) in {_key(a): b for a, b in vmap.items()}:
            raw = {_key(a): b for a, b in vmap.items()}[_key(k)]
        if raw is None or is_missing(raw, schema):
            return None, "missing"
    name, ftype = field["name"], field["type"]

    if ftype == "number":
        x = _to_number(raw)
        if x is None:
            return None, "invalid"
        if x in schema["numeric_missing_sentinels"]:
            return None, "missing"
        x *= float(rule.get("unit_factor") or 1.0)
        lo, hi = field.get("valid_range", [None, None])
        if (lo is not None and x < lo) or (hi is not None and x > hi):
            return x, "invalid"
        return x, "valid"

    if ftype == "integer":  # install_year
        d = parse_date(raw)
        if d is None:
            x = _to_number(raw)
            if x is None or x in schema["numeric_missing_sentinels"]:
                return None, "missing" if x is not None else "invalid"
            d = parse_date(x)
            if d is None:
                return None, "invalid"
        y = d.year
        lo, hi = (resolve_bound(b) for b in field.get("valid_range", [1850, "CURRENT_YEAR"]))
        return y, "valid" if lo <= y <= hi else "invalid"

    if ftype == "date":
        d = parse_date(raw)
        if d is None:
            return None, "invalid"
        lo, hi = (resolve_bound(b) for b in field.get("valid_range", ["1900-01-01", "TODAY"]))
        iso = d.isoformat()
        return iso, "valid" if lo <= iso <= hi else "invalid"

    if ftype == "enum":
        allowed = field["allowed"]
        if name == "condition":
            if str(raw).strip().upper() in CONDITION_NOT_RATED:
                return None, "missing"
            if isinstance(raw, str) and re.match(r"^[0-5][0-9A-Z][0-5][0-9A-Z]$", raw.strip().upper()):
                g = raw.strip()[0]  # PACP quick rating: first digit is the highest defect grade; 0000 = no defects
                return ("1" if g == "0" else g), "valid"
            x = _to_number(raw)
            if x is not None and (x in CONDITION_NOT_RATED_CODES or x in schema["numeric_missing_sentinels"]):
                return None, "missing"  # common "not rated" codes
            if x is not None and float(x).is_integer() and str(int(x)) in allowed:
                return str(int(x)), "valid"
            if x is None:
                c = condition_from_text(raw)
                if c:
                    return c, "valid"
            return None, "invalid"  # other scales need a value_map from the crosswalk
        table = {"material": MATERIAL_MAP, "shape_type": SHAPE_MAP, "lifecycle_status": STATUS_MAP, "basin_type": BASIN_MAP}.get(name, {})
        k = _key(raw)
        if k in allowed:
            return k, "valid"
        if k in table:
            return table[k], "valid"
        if name == "material":
            if k.isdigit():
                return None, "invalid"  # a numeric domain code that was not decoded
            m = material_from_text(raw)
            if m:
                return m, "valid"
        return ("OTHER", "valid") if "OTHER" in allowed else ("UNKNOWN", "missing")

    s = str(raw).strip()
    return s, "valid"
