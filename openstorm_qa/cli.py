"""Command line: check storm-sewer layers against the OpenStorm schema.

  openstorm-qa check --pipe pipes.geojson --manhole mh.geojson --inlet inlets.geojson \
      [--outfall o.geojson] [--detention_basin b.geojson] [--crosswalk cw.csv] [--json out.json]

Without --crosswalk, field mappings are drafted automatically from the schema's synonyms,
and the drafts are written next to --json (or printed) so you can review them.
"""

import argparse
import json
import sys

from . import __version__
from .crosswalk import draft_mapping, effective_rows, harmonize, read_crosswalk, write_crosswalk
from .schema import load_schema
from .score import LAYER_TYPES, score_network


def _fields_from_features(features, n=200):
    names, samples = [], {}
    for f in features[:n]:
        for k, v in (f.get("properties") or {}).items():
            if k not in samples:
                names.append(k)
                samples[k] = []
            if v not in (None, "") and len(samples[k]) < 5 and v not in samples[k]:
                samples[k].append(v)
    return [{"name": k, "alias": "", "type": ""} for k in names], samples


def run_check(paths, schema, crosswalk_rows=None):
    layers, drafts = {}, []
    for lt, path in paths.items():
        with open(path, encoding="utf-8") as f:
            feats = json.load(f).get("features", [])
        if crosswalk_rows is None:
            fields, samples = _fields_from_features(feats)
            rows = draft_mapping(fields, lt, schema, samples, layer_id=path)
            drafts.extend(rows)
            eff = effective_rows(rows)
        else:
            eff = effective_rows([r for r in crosswalk_rows if r["layer_type"] == lt])
        layers[lt] = harmonize(feats, eff, lt, schema)
    return score_network(layers, schema), drafts


def main(argv=None):
    ap = argparse.ArgumentParser(prog="openstorm-qa", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="score a set of storm-sewer layers")
    for lt in LAYER_TYPES:
        c.add_argument("--" + lt, metavar="GEOJSON", help=f"{lt} layer as WGS84 GeoJSON")
    c.add_argument("--crosswalk", help="reviewed crosswalk CSV")
    c.add_argument("--schema", help="alternative schema JSON")
    c.add_argument("--json", help="write full results to this file")
    a = ap.parse_args(argv)

    schema = load_schema(a.schema)
    paths = {lt: getattr(a, lt) for lt in LAYER_TYPES if getattr(a, lt)}
    if not paths:
        ap.error("give at least one layer, for example --pipe pipes.geojson")
    cw = read_crosswalk(a.crosswalk) if a.crosswalk else None
    res, drafts = run_check(paths, schema, cw)

    def pct(v):
        return "not scored" if v is None else f"{v * 100:.1f}%"

    print(f"OpenStorm QA {__version__}  (schema {schema['schema_version']})")
    print("Layers: " + ", ".join(f"{k}={v}" for k, v in res["feature_counts"].items() if v))
    for k in ("completeness", "attribute_coverage", "connectivity", "positional", "overall"):
        print(f"  {k:<20}{pct(res.get(k))}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2)
        if drafts:
            write_crosswalk(drafts, a.json.rsplit(".", 1)[0] + "_crosswalk_draft.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
