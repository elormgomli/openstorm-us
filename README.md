# OpenStorm US v1.0.0

OpenStorm US is an open benchmark of municipal stormwater infrastructure data published online by U.S. cities and counties. It is freely available to local government staff, engineers, researchers and the public.

Municipalities maintain GIS inventories of inlets, manholes, pipes, outfalls and detention facilities, but no common standard governs how these data are recorded or shared, limiting comparison across jurisdictions.

Version 1.0 provides:

- a census of 804 U.S. city and county publishers of stormwater GIS data;
- a common data schema for stormwater assets, with a field-level crosswalk that maps each publisher's attributes to it;
- quality scores for the 50 most complete publishers (1.9 million features in 24 states), assessing completeness, attribute coverage, network connectivity and positional accuracy.

The results reveal a clear gap. Pipe material and size are about 91 percent complete, yet only 16 of the 50 publishers report pipe condition and only 5 report inspection dates, the information most needed for asset management.

## Start here

Open the file that answers your question. All tables are CSV and open in Excel.

| If you want to know | Open |
|---|---|
| Which US cities and counties publish storm-sewer GIS data | `data/publishers.csv` (804 publishers) |
| How complete and usable each publisher's data is | `data/scores_v1.csv` (the 50 scored publishers, ranked) |
| How each publisher's fields were matched to the common format | `data/crosswalk_v1.csv` (filter `used_in_scores` to `yes`) |

**Reading a score row.** Every score runs from 0 to 1 (1 = best). For example, Sioux Falls city (SD) ranks first with an overall score of 0.829, the equal-weight average of four parts: *completeness* 0.90 (which of the five asset types are published, and how many features have a location and an ID), *attribute_coverage* 0.54 (how often material, diameter, install year, condition and inspection date are filled with a usable value), *connectivity* 0.88 (share of pipe ends that meet a structure or another pipe within 1 m) and *positional* 1.00 (share of features with plausible, non-duplicate locations). The `pipe_*` columns give the share of pipes with each attribute; for example `pipe_material` 0.87 means 87 percent of its pipes record a usable material.

**Reading a crosswalk row.** Each row is one column in one publisher's layer. `source_field` is the publisher's name for it, `final_target` is the common-schema field it was translated to, and `value_map` shows how codes were read (for example `"4": "RCP"` means code 4 is reinforced concrete pipe; `null` means "unknown"). Only rows with `used_in_scores` = `yes` count in the scores; the rest are fields with no schema equivalent, housekeeping fields, or suggestions not accepted in review.

**Other files** answer narrower questions: `value_maps_v1.csv` (how codes were read), `layer_registry.csv` (which layers were used and why others were set aside), `census_catalog_hits.csv` (why a city is or is not in the census), `layer_inventory.csv` (every layer opened, including non-storm layers, which are listed but not kept or scored), `stats.json` (every number in the summary), `qa_report.txt` and `PROVENANCE.txt` (download log with file fingerprints). Every column is explained in `docs/CODEBOOK.md`. Read `docs/LIMITATIONS.md` before quoting a number; `docs/GLOSSARY.md` defines the terms.

## What is in this package

| Path | What it is |
|---|---|
| data/publishers.csv | The census: one row per US city or county publisher |
| data/census_catalog_hits.csv | Every catalog hit and why it was or was not included |
| data/layer_inventory.csv, data/layer_registry.csv | Every layer opened, and which were used and why |
| data/crosswalk_v1.csv | Field-by-field crosswalk for the 50 v1 publishers |
| data/value_maps_v1.csv, data/condition_scales.csv | How coded values were read |
| data/scores_v1.csv | The benchmark scores |
| data/qa_report.txt, data/stats.json, data/PROVENANCE.txt | Quality checks, every quoted number, every download with its SHA-256 |
| schema/openstorm_v1.json | The common schema |
| docs/ | Codebook, limitations, decisions, source terms, build spec, glossary |
| code/, openstorm_qa/, tests/ | The pipeline and the openstorm-qa package that produced all of the above |

## How to rebuild

Needs Python 3.9 or newer and internet access. From this folder:

```bash
python3 -m unittest discover -s tests        # 26 tests, about 1 second
python3 code/restore_v1_review.py            # puts the reviewed v1.0 crosswalk and publisher set where the pipeline reads them
python3 code/02_fetch_layers.py --workers 4  # re-downloads each city's layers from its live portal; several hours; safe to stop and restart
python3 code/04_score.py --reviewed          # scores the 50 publishers with the reviewed crosswalk
```

With the downloads logged in data/PROVENANCE.txt, these steps reproduce data/scores_v1.csv exactly (checked 2026-10-22: 1,250 of 1,250 cells identical). Portals change, so a download made later reflects each city's data on that day. To redo the census from scratch, run code/01_census.py and code/03_draft_crosswalks.py first; new publishers then need a reviewed crosswalk.

## Check your own layer

```bash
pip install .
openstorm-qa check --pipe pipes.geojson --manhole manholes.geojson --inlet inlets.geojson --json results.json
```

Layers must be GeoJSON in WGS84 (longitude, latitude).

The cities' own features are not redistributed: most publishers state no license. Use the code to rebuild the harmonized data from each live portal (see How to rebuild above).

## License

Data, schema, crosswalk and documents: CC BY 4.0. Code: MIT. Source layers remain under each publisher's terms.

## Cite

See CITATION.cff. Limitations: docs/LIMITATIONS.md.
