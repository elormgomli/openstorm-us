"""Tests for openstorm-qa on a tiny synthetic network with known answers.

Network (Arlington-ish coordinates, ~11 m per 0.0001 deg lat):
  inlet I1 --pipe A--> manhole M1 --pipe B--> manhole M2 --pipe C--> (5 m short of) outfall O1
Plus: one duplicate inlet (I2 on top of I1) and one inlet at (0, 0).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openstorm_qa import draft_mapping, effective_rows, harmonize, load_schema, score_network  # noqa: E402
from openstorm_qa.values import normalize, parse_date  # noqa: E402

LON, LAT = -97.1081, 32.7357
DEG_M = 1 / 111195.0  # degrees latitude per meter


def pt(dx_m, dy_m):
    return [LON + dx_m * DEG_M / 0.8411, LAT + dy_m * DEG_M]  # cos(32.7357 deg) ~ 0.8411


def P(coords, **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": coords}, "properties": props}


def L(a, b, **props):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [a, b]}, "properties": props}


I1, M1, M2, O1 = pt(0, 0), pt(100, 0), pt(200, 0), pt(300, 0)
C_END = pt(295, 0)  # 5 m short of the outfall

FIX = {
    "inlet": [P(I1, FACILITYID="I1", INSTALLDATE=946684800000, MATERIAL="Concrete"),
              P(I1, FACILITYID="I2", INSTALLDATE=None, MATERIAL="UNK"),
              P([0.0, 0.0], FACILITYID="I3", INSTALLDATE="1985", MATERIAL="")],
    "manhole": [P(M1, FACILITYID="M1", COND="3"), P(M2, FACILITYID="M2", COND="7")],
    "outfall": [P(O1, FACILITYID="O1", INSP_DATE="2021-05-04")],
    "pipe": [L(I1, M1, FACILITYID="A", PIPE_MATL="RCP", DIAMETER=18, INSTALLDATE="1987-06-01"),
             L(M1, M2, FACILITYID="B", PIPE_MATL="Corrugated Metal", DIAMETER=0, INSTALLDATE="06/01/1999"),
             L(M2, C_END, FACILITYID="C", PIPE_MATL="weird stuff", DIAMETER="24", INSTALLDATE=1700)],
}


def build(schema):
    layers = {}
    for lt, feats in FIX.items():
        names = sorted({k for f in feats for k in f["properties"]})
        rows = draft_mapping([{"name": n} for n in names], lt, schema)
        layers[lt] = harmonize(feats, effective_rows(rows), lt, schema)
    return layers


class TestValues(unittest.TestCase):
    def setUp(self):
        self.s = load_schema()
        self.f = self.s["_fields_by_name"]

    def test_dates(self):
        self.assertEqual(parse_date(946684800000).isoformat(), "2000-01-01")
        self.assertEqual(parse_date(31536000000).isoformat(), "1971-01-01")   # 1966-1973 epoch ms used to fail
        self.assertEqual(parse_date(-94694400000).isoformat(), "1967-01-01")
        self.assertEqual(parse_date(19850615).isoformat(), "1985-06-15")
        self.assertEqual(parse_date("06/01/1999").year, 1999)
        self.assertIsNone(parse_date("soon"))

    def test_material(self):
        self.assertEqual(normalize(self.f["material"], "Reinforced Concrete Pipe", self.s), ("RCP", "valid"))
        self.assertEqual(normalize(self.f["material"], "unk", self.s), (None, "missing"))

    def test_diameter_sentinel_and_range(self):
        self.assertEqual(normalize(self.f["diameter_in"], 0, self.s)[1], "missing")
        self.assertEqual(normalize(self.f["diameter_in"], '15"', self.s), (15.0, "valid"))
        self.assertEqual(normalize(self.f["diameter_in"], 1.5, self.s, {"unit_factor": "12"}), (18.0, "valid"))
        self.assertEqual(normalize(self.f["diameter_in"], 500, self.s)[1], "invalid")
        self.assertEqual(normalize(self.f["diameter_in"], 999, self.s)[1], "missing")  # "not recorded" code

    def test_condition_value_map(self):
        self.assertEqual(normalize(self.f["condition"], "3", self.s), ("3", "valid"))
        self.assertEqual(normalize(self.f["condition"], "Sound", self.s)[1], "invalid")
        self.assertEqual(normalize(self.f["condition"], "Sound", self.s, {"value_map": {"Sound": "2"}}), ("2", "valid"))

    def test_material_text(self):
        f = self.f["material"]
        for raw, want in (("Reinforced Concrete Pipe (RCP)", "RCP"), ("Corrugated Metal Pipe", "CMP"), ("Corrugated HDPE", "HDPE"),
                          ("Corrugated Plastic", "CPP"), ("Vitrified Clay", "VCP"), ("Precast Concrete", "CONCRETE_CIP"), ("Wood", "OTHER")):
            self.assertEqual(normalize(f, raw, self.s), (want, "valid"), raw)
        self.assertEqual(normalize(f, "3", self.s)[1], "invalid")  # undecoded numeric code

    def test_condition_text(self):
        f = self.f["condition"]
        for raw, want in (("Good", "2"), ("Very Good", "1"), ("Fair", "3"), ("Poor", "4"), ("Very Poor", "5"), ("Failed", "5"),
                          ("Grade 4", "4"), ("PACP 2", "2"), ("B", "2"), ("3 - Fair", "3")):
            self.assertEqual(normalize(f, raw, self.s), (want, "valid"), raw)
        self.assertEqual(normalize(f, 8, self.s)[1], "invalid")
        self.assertEqual(normalize(f, 0, self.s)[1], "missing")
        self.assertEqual(normalize(f, "Inaccessible", self.s)[1], "missing")
        self.assertEqual(normalize(f, 88, self.s)[1], "missing")
        self.assertEqual(normalize(f, "3100", self.s), ("3", "valid"))   # PACP quick rating
        self.assertEqual(normalize(f, "0000", self.s), ("1", "valid"))
        self.assertEqual(normalize(f, "4E00", self.s), ("4", "valid"))

    def test_condition_0_100(self):
        f, vm = self.f["condition"], {"value_map": {"__scale__": "0-100, 100 = best"}}
        for raw, want in ((100, "1"), (80, "1"), (79, "2"), (60, "2"), (45, "3"), (20, "4"), (19, "5"), (1, "5")):
            self.assertEqual(normalize(f, raw, self.s, vm), (want, "valid"), raw)
        self.assertEqual(normalize(f, -1, self.s, vm)[1], "missing")
        self.assertEqual(normalize(f, 0, self.s, vm)[1], "missing")
        self.assertEqual(normalize(f, 150, self.s, vm)[1], "invalid")

    def test_install_year_range(self):
        self.assertEqual(normalize(self.f["install_year"], 1700, self.s)[1], "invalid")


class TestCrosswalk(unittest.TestCase):
    def test_draft(self):
        s = load_schema()
        rows = draft_mapping([{"name": "PIPE_MATL"}, {"name": "DIAMETER"}, {"name": "Shape__Length"}, {"name": "FOO_BAR"}], "pipe", s)
        got = {r["source_field"]: r["target_field"] for r in rows}
        self.assertEqual(got, {"PIPE_MATL": "material", "DIAMETER": "diameter_in", "FOO_BAR": ""})

    def test_draft_guards(self):
        s = load_schema()
        pipe = {r["source_field"]: r["target_field"] for r in draft_mapping(
            [{"name": "CONDITIONDATE"}, {"name": "INSTALLDATESOURCE"}, {"name": "OUTFALLID"}, {"name": "DOWNID", "alias": "Downstream Facility ID"},
             {"name": "NEXT_INSP_DATE"}, {"name": "FACILITYID"}, {"name": "COND_RATING"}, {"name": "INSTALL_DATE"}], "pipe", s)}
        self.assertEqual(pipe["CONDITIONDATE"], "")
        self.assertEqual(pipe["INSTALLDATESOURCE"], "")
        self.assertEqual(pipe["OUTFALLID"], "")
        self.assertEqual(pipe["DOWNID"], "")
        self.assertEqual(pipe["NEXT_INSP_DATE"], "")
        more = {r["source_field"]: r["target_field"] for r in draft_mapping([{"name": "INSTALLDATESRC"}, {"name": "INSTALLATIONID"}, {"name": "CONDITIOND"}], "pipe", s)}
        self.assertEqual(set(more.values()), {""})
        sizes = {r["source_field"]: r["target_field"] for r in draft_mapping([{"name": "XSectionWidth"}, {"name": "CrossSectionShape"}, {"name": "YR_CONST"}], "pipe", s)}
        self.assertEqual(sizes, {"XSectionWidth": "diameter_in", "CrossSectionShape": "shape_type", "YR_CONST": "install_year"})
        self.assertEqual((pipe["FACILITYID"], pipe["COND_RATING"], pipe["INSTALL_DATE"]), ("asset_id", "condition", "install_year"))
        out = {r["source_field"]: r["target_field"] for r in draft_mapping([{"name": "OUTFALLID"}], "outfall", s)}
        self.assertEqual(out["OUTFALLID"], "asset_id")

    def test_feet_detection(self):
        from openstorm_qa.crosswalk import diameter_unit_factor
        self.assertEqual(diameter_unit_factor("Height", "Height (ft)", [1, 2, 4]), "12")
        self.assertEqual(diameter_unit_factor("DIAM_FT", "", [1.5, 2]), "12")
        self.assertEqual(diameter_unit_factor("Diameter", "Diameter (in)", [12, 18]), "")
        self.assertEqual(diameter_unit_factor("Diameter", "", [12, 18]), "")
        self.assertEqual(diameter_unit_factor("SHAFT", "", [12, 18]), "")

    def test_reviewer_reject_and_change(self):
        rows = [{"layer_type": "pipe", "source_field": "X", "target_field": "material", "reviewer_decision": "REJECT"},
                {"layer_type": "pipe", "source_field": "Y", "target_field": "", "reviewer_decision": "CHANGE", "reviewer_target": "diameter_in"},
                {"layer_type": "pipe", "source_field": "Z", "target_field": "owner", "reviewer_decision": ""}]
        self.assertEqual([r["source_field"] for r in effective_rows(rows)], ["Y", "Z"])
        self.assertEqual([r["source_field"] for r in effective_rows(rows, require_review=True)], ["Y"])


class TestScore(unittest.TestCase):
    def setUp(self):
        self.s = load_schema()
        self.r = score_network(build(self.s), self.s)

    def test_connectivity(self):
        # 6 pipe endpoints; only pipe C's downstream end (5 m short of O1) fails to snap.
        self.assertEqual(self.r["pipe_endpoints"], 6)
        self.assertEqual(self.r["endpoints_snapped"], 5)
        self.assertAlmostEqual(self.r["connectivity"], 5 / 6)
        self.assertEqual(self.r["components"], 1)

    def test_positional(self):
        # 9 features; I2 duplicates I1 and I3 sits at (0, 0).
        self.assertEqual(self.r["positional_failures"], {"duplicate_point": 1, "null_island": 1})
        self.assertAlmostEqual(self.r["positional"], 7 / 9)

    def test_completeness(self):
        # 4 of 5 layer types; all 9 features have IDs, I3 geometry is (0,0) but present.
        self.assertAlmostEqual(self.r["layer_presence"], 0.8)
        self.assertAlmostEqual(self.r["record_completeness"], 1.0)
        self.assertAlmostEqual(self.r["completeness"], 0.9)

    def test_coverage_pipe(self):
        d = self.r["attribute_coverage_detail"]["pipe"]
        # material: RCP, CMP, OTHER(valid) = 3/3; diameter: 18 ok, 0 missing, 24 ok = 2/3;
        # install_year: 1987, 1999 ok, 1700 invalid = 2/3; condition, inspection unmapped = 0.
        self.assertAlmostEqual(d["material"], 1.0)
        self.assertAlmostEqual(d["diameter_in"], 2 / 3)
        self.assertAlmostEqual(d["install_year"], 2 / 3)
        self.assertEqual(d["condition"], 0.0)

    def test_not_applicable_excluded(self):
        # Marking diameter "not_applicable" on every pipe removes it from coverage: no credit, no penalty.
        layers = build(self.s)
        for rec in layers["pipe"]:
            rec["status"]["diameter_in"] = "not_applicable"
        r = score_network(layers, self.s)
        self.assertIsNone(r["attribute_coverage_detail"]["pipe"]["diameter_in"])
        self.assertAlmostEqual(r["attribute_coverage_detail"]["pipe"]["material"], 1.0)

    def test_no_pipes_reweights(self):
        layers = build(self.s)
        del layers["pipe"]
        r = score_network(layers, self.s)
        self.assertIsNone(r["connectivity"])
        self.assertAlmostEqual(r["overall"], (r["completeness"] + r["attribute_coverage"] + r["positional"]) / 3)


class TestGeometry(unittest.TestCase):
    def test_text_coordinates(self):
        from openstorm_qa.geom import clean_geometry
        self.assertEqual(clean_geometry({"type": "Point", "coordinates": ["-97.1", "32.7"]}), {"type": "Point", "coordinates": [-97.1, 32.7]})
        self.assertIsNone(clean_geometry({"type": "Point", "coordinates": ["x", None]}))
        self.assertEqual(clean_geometry({"type": "LineString", "coordinates": [["-97", "32"], [-97.1, 32.1]]})["coordinates"], [[-97.0, 32.0], [-97.1, 32.1]])
        self.assertIsNone(clean_geometry({"type": "LineString", "coordinates": [[-97, 32]]}))
        self.assertEqual(len(clean_geometry({"type": "MultiLineString", "coordinates": [[[0, 1], [1, 1]], [[2, 2], ["a", 3]]]})["coordinates"]), 2)


class TestCLI(unittest.TestCase):
    def test_cli_runs(self):
        d = tempfile.mkdtemp()
        args = []
        for lt, feats in FIX.items():
            p = os.path.join(d, lt + ".geojson")
            with open(p, "w") as f:
                json.dump({"type": "FeatureCollection", "features": feats}, f)
            args += ["--" + lt, p]
        out = os.path.join(d, "res.json")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cp = subprocess.run([sys.executable, "-m", "openstorm_qa", "check", *args, "--json", out], cwd=root, capture_output=True, text=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("connectivity", cp.stdout)
        self.assertTrue(os.path.exists(os.path.join(d, "res_crosswalk_draft.csv")))


if __name__ == "__main__":
    unittest.main()
