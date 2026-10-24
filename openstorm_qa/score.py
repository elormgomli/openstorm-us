"""Score one publisher's storm-sewer network against the OpenStorm schema.

Four components, each 0 to 1:

completeness        Half: share of the five layer types published.
                    Half: share of features with usable geometry and an asset ID.
attribute_coverage  Feature-weighted mean, over layers, of the share of features with a
                    valid value for each key attribute that applies to that layer
                    (material, diameter, install year, condition, inspection date).
                    An unmapped key attribute counts as 0 for every feature.
connectivity        Share of pipe endpoints that land within the snap tolerance of a
                    node (inlet, manhole, outfall) or of another pipe's endpoint.
                    Not scored (None) when no pipe layer exists; the overall score then
                    re-weights the other three.
positional          Share of features that pass all positional checks: has geometry,
                    not at or near (0, 0), within the outlier distance of the network's
                    median location, not a duplicate point, and pipes longer than 0.1 m.

Overall = weighted mean of available components (weights in the schema file).
"""

import math
from collections import Counter

from .geom import GridIndex, LocalProjector, line_length_m, line_parts, median, representative_point
from .schema import key_attributes_for

LAYER_TYPES = ["inlet", "manhole", "pipe", "outfall", "detention_basin"]
NODE_TYPES = ["inlet", "manhole", "outfall"]


def _has_geom(rec):
    g = rec.get("geometry")
    return bool(g) and representative_point(g) is not None


def score_network(layers, schema):
    """layers: {layer_type: [harmonized records]} (from crosswalk.harmonize)."""
    cfg = schema["scoring"]
    present = [lt for lt in LAYER_TYPES if layers.get(lt)]
    result = {"layers_present": present, "feature_counts": {lt: len(layers.get(lt, [])) for lt in LAYER_TYPES}}
    total = sum(result["feature_counts"].values())
    if total == 0:
        result.update(completeness=0.0, attribute_coverage=0.0, connectivity=None, positional=0.0, overall=0.0)
        return result

    # ---- completeness
    ok = sum(1 for lt in present for r in layers[lt]
             if _has_geom(r) and r["status"].get("asset_id") == "valid")
    result["layer_presence"] = len(present) / len(LAYER_TYPES)
    result["record_completeness"] = ok / total
    result["completeness"] = 0.5 * result["layer_presence"] + 0.5 * result["record_completeness"]

    # ---- attribute coverage
    cov_detail, num, den = {}, 0.0, 0
    for lt in present:
        keys = key_attributes_for(schema, lt)
        recs = layers[lt]
        # A key marked "not_applicable" (e.g. diameter of a swale) is left out: no credit, no penalty.
        per = {}
        for k in keys:
            app = [r for r in recs if r["status"].get(k) != "not_applicable"]
            per[k] = (sum(1 for r in app if r["status"].get(k) == "valid") / len(app)) if app else None
        cov_detail[lt] = per
        if keys:
            for r in recs:
                app = [k for k in keys if r["status"].get(k) != "not_applicable"]
                if app:
                    num += sum(1 for k in app if r["status"].get(k) == "valid") / len(app)
                    den += 1
    result["attribute_coverage_detail"] = cov_detail
    result["attribute_coverage"] = num / den if den else 0.0

    # ---- shared projection
    pts = [representative_point(r["geometry"]) for lt in present for r in layers[lt] if _has_geom(r)]
    pts = [p for p in pts if not (abs(p[0]) < 1 and abs(p[1]) < 1)]
    if not pts:
        result.update(connectivity=None, positional=0.0)
        result["overall"] = _overall(result, cfg)
        return result
    lon0, lat0 = median([p[0] for p in pts]), median([p[1] for p in pts])
    proj = LocalProjector(lon0, lat0)

    # ---- connectivity
    tol = cfg["snap_tolerance_m"]
    pipes = layers.get("pipe") or []
    if pipes:
        idx = GridIndex(max(tol * 4, 1.0))
        for lt in NODE_TYPES:
            for i, r in enumerate(layers.get(lt) or []):
                p = representative_point(r["geometry"]) if r.get("geometry") and r["geometry"].get("type") == "Point" else None
                if p:
                    idx.add(*proj(*p[:2]), ("node", lt, i))
        ends = []
        for i, r in enumerate(pipes):
            for part in line_parts(r.get("geometry")):
                if len(part) >= 2:
                    for end in (part[0], part[-1]):
                        xy = proj(end[0], end[1])
                        ends.append((xy, i))
                        idx.add(*xy, ("end", "pipe", i))
        snapped, to_node = 0, 0
        for xy, i in ends:
            any_hit = node_hit = False
            for k, pl in enumerate(idx.near(xy[0], xy[1], tol)):
                if pl[0] == "end" and pl[2] == i:
                    continue
                any_hit = True
                if pl[0] == "node":
                    node_hit = True
                    break
                if k > 200:  # stacked features at one spot: stop scanning, result already known
                    break
            snapped += any_hit
            to_node += node_hit
        result["pipe_endpoints"] = len(ends)
        result["endpoints_snapped"] = snapped
        result["endpoints_at_node"] = to_node
        result["connectivity"] = snapped / len(ends) if ends else 0.0
        result["components"], result["largest_component_share"] = _components(pipes, proj, tol)
    else:
        result["connectivity"] = None

    # ---- positional
    out_km = cfg["outlier_distance_km"]
    dup_tol = cfg["duplicate_tolerance_m"]
    passed, fails = 0, Counter()
    for lt in present:
        seen = GridIndex(max(dup_tol * 10, 0.5))
        for r in layers[lt]:
            g = r.get("geometry")
            p = representative_point(g) if g else None
            if p is None:
                fails["no_geometry"] += 1
                continue
            if abs(p[0]) < 1 and abs(p[1]) < 1:
                fails["null_island"] += 1
                continue
            xy = proj(p[0], p[1])
            if math.hypot(*xy) > out_km * 1000:
                fails["outlier"] += 1
                continue
            if g["type"] == "Point":
                if any(True for _ in seen.near(xy[0], xy[1], dup_tol)):
                    fails["duplicate_point"] += 1
                    continue
                seen.add(xy[0], xy[1], 1)
            if lt == "pipe":
                parts = line_parts(g)
                if not parts or sum(line_length_m(pt, proj) for pt in parts) < 0.1:
                    fails["zero_length_pipe"] += 1
                    continue
            passed += 1
    result["positional_failures"] = dict(fails)
    result["positional"] = passed / total
    result["overall"] = _overall(result, cfg)
    return result


def _components(pipes, proj, tol):
    """Connected components of the pipe network, joining endpoints within tol."""
    parent = list(range(len(pipes)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    idx = GridIndex(max(tol * 4, 1.0))
    for i, r in enumerate(pipes):
        for part in line_parts(r.get("geometry")):
            if len(part) >= 2:
                for end in (part[0], part[-1]):
                    x, y = proj(end[0], end[1])
                    for k, j in enumerate(idx.near(x, y, tol)):
                        parent[find(i)] = find(j)
                        if k >= 50:  # everything this close is already joined through earlier unions
                            break
                    idx.add(x, y, i)
    sizes = Counter(find(i) for i in range(len(pipes)))
    return len(sizes), (max(sizes.values()) / len(pipes) if pipes else 0.0)


def _overall(res, cfg):
    w = cfg["weights"]
    parts = [(w[k], res[k]) for k in ("completeness", "attribute_coverage", "connectivity", "positional") if res.get(k) is not None]
    tw = sum(a for a, _ in parts)
    return sum(a * b for a, b in parts) / tw if tw else 0.0
