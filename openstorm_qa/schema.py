"""Load the OpenStorm common schema and answer simple questions about it."""

import datetime
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCHEMA = os.path.join(_HERE, "schema", "openstorm_v1.json")


def load_schema(path=None):
    with open(path or DEFAULT_SCHEMA, encoding="utf-8") as f:
        s = json.load(f)
    s["_fields_by_name"] = {fd["name"]: fd for fd in s["fields"]}
    return s


def fields_for(schema, layer_type):
    return [f for f in schema["fields"] if "*" in f["layers"] or layer_type in f["layers"]]


def key_attributes_for(schema, layer_type):
    return [f["name"] for f in fields_for(schema, layer_type) if f.get("key_attribute")]


def norm_name(name):
    """Normalize a field name for matching: lowercase, alphanumerics only."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def resolve_bound(v):
    if v == "CURRENT_YEAR":
        return datetime.date.today().year
    if v == "TODAY":
        return datetime.date.today().isoformat()
    return v
