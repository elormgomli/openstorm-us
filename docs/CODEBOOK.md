# Codebook: OpenStorm US v1.0

*Plain language: this file explains every column in every data file of the release, what it means, and where it comes from. You only need it when you want to know exactly what a column holds.*

Conventions. `geoid` is the publisher key: `P` + 7-digit Census place GEOID for cities, towns and villages, or `C` + 5-digit county FIPS for counties (Census Gazetteer 2024). Scores are shares from 0 to 1 (multiply by 100 for percent). Blank means not applicable or not computed. All dates are ISO 8601.

## data/publishers.csv — the census (one row per US city or county publisher found)

| Column | Meaning | Source / method |
|---|---|---|
| geoid | Publisher key (see conventions) | Census Gazetteer 2024, matched by name and location |
| census_name | Official Census name, e.g. "Round Rock city" | Census Gazetteer 2024 |
| state | USPS state code | Census Gazetteer 2024 |
| publisher_class | city, county, or unclassified (utilities and districts matched to an area) | Rule on the catalog publisher name (`code/common.py`) |
| publisher_names | Names the publisher uses in the catalogs, separated by "; " | ArcGIS Hub `source` or organization name; Socrata attribution |
| n_items | Number of storm-related catalog items matched to this publisher | Count |
| catalogs | hub, socrata, or both | Catalog searched |

## data/census_catalog_hits.csv — every catalog hit and why it was or was not included

| Column | Meaning |
|---|---|
| catalog | hub (ArcGIS Hub) or socrata |
| item_id, title, type, url | The catalog item as listed |
| publisher, owner, domain | Who published it, as the catalog reports |
| publisher_class | city, county, utility_district, excluded (consultants, universities, non-US), unclassified |
| layer_guess | Storm layer type guessed from the title |
| include | yes if the item entered the census |
| reason | Why not included, or a note |
| geoid, census_name, state | Matched Census unit, when included |
| match_method, match_km | How the match was made; distance from the item's extent to the Census point, km |
| query | Search term that found it |

## data/layer_inventory.csv — every service layer opened during the harvest

| Column | Meaning |
|---|---|
| geoid, census_name, state, catalog, item_title | Publisher and catalog item |
| layer_url, layer_name, geometry_type | The service layer |
| layer_type | inlet, manhole, pipe, outfall, detention_basin, or blank if not a storm layer |
| n_reported, n_fetched | Features the service reported, and features saved (capped at 250,000) |
| n_fields | Number of attribute fields |
| status | kept, not storm layer, empty, error |
| error | Error message for failed layers |

## data/layer_registry.csv — which downloaded layers are used, and why

| Column | Meaning |
|---|---|
| layer_id | Internal path of the downloaded layer (stable key used by the crosswalk) |
| geoid, layer_type, layer_name, n_features, n_fields, url | Layer facts |
| use | USE or SKIP by rule |
| reason | Why skipped: duplicate, natural waterway, ditch layer, label layer, abandoned, proposed, boundary, sanitary, test server, not a conveyance |
| exclude_field, exclude_pattern | For mixed layers: features whose `exclude_field` matches `exclude_pattern` (ditches) are removed before scoring |
| note, flag | Notes; "counted" flags for channels, swales, laterals and private assets |
| elorm_override | The author's own ruling, if any (USE or SKIP); blank in v1.0 |
| final | The decision applied: elorm_override if set, else use |

## data/crosswalk_v1.csv — the schema crosswalk for the 50 v1.0 publishers (one row per source field)

| Column | Meaning |
|---|---|
| geoid | Publisher |
| source_layer_id | Layer (matches layer_registry.layer_id) |
| layer_type | Layer type |
| source_field, source_alias, source_type | The publisher's field name, alias and type |
| target_field | Common-schema field it maps to (blank = not mapped) |
| confidence, method | How the draft was made: synonym, keyword, fuzzy, data pattern, housekeeping field (never mapped) |
| unit_factor | Multiplier to the schema unit (for example 12 for feet to inches) |
| value_map | JSON lookup from raw values to schema values; `{"__scale__": "0-100, 100 = best"}` marks a 0 to 100 condition scale |
| reviewer_decision | ACCEPT, CHANGE, REJECT, or blank (not used in scores) |
| reviewer_target | New target when CHANGE |
| reviewer_note | Who decided and the automated data-check result |
| final_target | The schema field the row is actually translated to after review (target_field, or reviewer_target for CHANGE); blank if not accepted |
| used_in_scores | yes = this row is used in the v1.0 scores (accepted or changed in review, on a layer the registry marks USE); no = not used |

Only rows with reviewer_decision ACCEPT or CHANGE are used in the v1.0 scores; `used_in_scores` marks them. Owner and custodian are recorded but are not scored attributes: `owner` is who owns the asset, `custodian` who maintains it (source fields such as Maintained By or MAINTBY), which can differ.

## data/value_maps_v1.csv — how coded values were read

| Column | Meaning |
|---|---|
| geoid, source_layer_id, source_field, target_field | Where the value comes from |
| raw_value | The value as published |
| count | How many features had it, in the first 20,000 of the layer |
| decoded | Description from the layer's own coded-value domain, if any |
| mapped_to | Schema value it becomes (blank = treated as missing or not recognized) |
| rule | Which rule decided it |

## data/condition_scales.csv — condition fields whose scale could not be read

geoid, source_layer_id, source_field, min, max (ignoring "not rated" codes), n_values, elorm_rule. In v1.0 one field remains (Round Rock TX, -3 to 6); it scores as no usable condition.

## data/scores_v1.csv — the benchmark (one row per v1.0 publisher)

| Column | Meaning | How computed |
|---|---|---|
| rank | Rank by overall score | 1 = highest |
| geoid, name, state, publisher_class | Publisher | |
| overall | Equal-weight mean of the four components that apply | Weights in `schema/openstorm_v1.json` |
| completeness | 0.5 × share of the five layer types published + 0.5 × share of features with geometry and a valid asset ID | |
| attribute_coverage | Feature-weighted mean, over layers, of the share of features with a valid value for each key attribute of that layer type (material, diameter, install year, condition, last inspection date) | An unmapped key attribute counts as 0 |
| connectivity | Share of pipe endpoints within 1.0 m of an inlet, manhole, outfall, or another pipe end | Blank if no pipe layer |
| positional | Share of features with geometry, not at (0,0), within 50 km of the network median, not a duplicate point within 0.1 m, and pipes longer than 0.1 m | |
| layers_present | Layer types published, separated by ";" | |
| n_features, n_inlet, n_manhole, n_pipe, n_outfall, n_detention_basin | Feature counts after ditch removal and registry rules | |
| pipe_material, pipe_diameter, pipe_install_year, pipe_condition, pipe_inspection | Share of pipe features with a valid value for each key attribute | |
| pipe_components | Number of separate pipe networks (pieces not joined within 1.0 m) | |
| largest_component_share | Share of pipes in the largest connected piece | |
| positional_failures | JSON counts of each positional check failed | |

## schema/openstorm_v1.json — the common schema

Fields, types, units, allowed values, valid ranges, missing-value codes, and scoring weights and tolerances. Version 1.0.0 (identical in content to the 1.0.0-rc1 the author accepted). The human-readable decisions behind it are in `DECISIONS.md`.

## Other files

`stats.json` (every number quoted in documents), `qa_report.txt` (counts, losses, range checks, spot checks), `PROVENANCE.txt` (every download: date, file, bytes, SHA-256, URL), `DECISIONS.md`, `LIMITATIONS.md`, `SOURCE_TERMS.md`, and `code/` (the pipeline and the openstorm-qa package that produced everything here).
