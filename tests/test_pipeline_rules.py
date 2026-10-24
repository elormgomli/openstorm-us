"""Tests for Elorm's 2026-10-15 rulings: ditches excluded, outfalls carry material and diameter."""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "code"))

from common import drop_excluded  # noqa: E402
from openstorm_qa import draft_mapping, load_schema  # noqa: E402

spec = importlib.util.spec_from_file_location("reg", os.path.join(ROOT, "code", "025_layer_registry.py"))
reg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reg)


def F(**p):
    return {"type": "Feature", "geometry": None, "properties": p}


class TestDitchRule(unittest.TestCase):
    def test_mixed_layer_uses_type_field_not_from_to(self):
        feats = [F(TYPE="Ditch", FROM_TYPE="Ditch")] * 6 + [F(TYPE="Culvert", FROM_TYPE="Pipe In to Culv")] * 3 + [F(TYPE="Bio Swale", FROM_TYPE="Start Ditch")]
        field, share = reg.find_ditch_field(feats)
        self.assertEqual(field, "TYPE")
        self.assertAlmostEqual(share, 0.6)
        kept = drop_excluded(feats, (field, reg.DITCH_PATTERN))
        self.assertEqual(sorted({f["properties"]["TYPE"] for f in kept}), ["Bio Swale", "Culvert"])

    def test_no_ditch_values(self):
        feats = [F(LINE_TYPE="PIPE")] * 5 + [F(LINE_TYPE="CHANNEL")]
        self.assertEqual(reg.find_ditch_field(feats), (None, 0.0))
        self.assertEqual(len(drop_excluded(feats, None)), 6)

    def test_roadside_ditch_matches(self):
        feats = [F(FEAT_TYPE="Roadside Ditch")] * 99 + [F(FEAT_TYPE="Culvert")]
        field, share = reg.find_ditch_field(feats)
        self.assertEqual(field, "FEAT_TYPE")
        self.assertGreater(share, 0.98)


    def test_related_values_are_not_ditches(self):
        for fields in ({"Equipment_Description": "Empties into ditch"}, {"CULVERT_TYPE": "Ditch Crossing"},
                       {"Final__Crossing_Feature": "Ditch"}, {"lastmainttype": "STORM DITCH - Excavate"}, {"Type_": "Ditch Block"},
                       {"Inlet_Type": "Ditch"}, {"lastinsptype": "Investigate - Storm Ditch"}):
            feats = [F(**fields)] * 5 + [F(**{k: "Pipe" for k in fields})] * 5
            self.assertEqual(reg.find_ditch_field(feats), (None, 0.0), fields)

    def test_named_ditches_are_ditches(self):
        for v in ("Open Ditch", "Roadside Ditch", "Ditch Minor", "Unlined Ditch", "Ditch/canal", "DITCH2", "Ditch (No Outlet)", "Earthen Ditch", "Ditchline"):
            feats = [F(TYPE=v)] * 3 + [F(TYPE="Pipe")] * 7
            self.assertEqual(reg.find_ditch_field(feats)[0], "TYPE", v)


class TestOutfallSchema(unittest.TestCase):
    def test_outfall_maps_material_and_diameter(self):
        s = load_schema()
        rows = draft_mapping([{"name": "MATERIAL"}, {"name": "DIAMETER"}], "outfall", s)
        self.assertEqual({r["source_field"]: r["target_field"] for r in rows}, {"MATERIAL": "material", "DIAMETER": "diameter_in"})


if __name__ == "__main__":
    unittest.main()
