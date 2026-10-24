# Limitations

*Plain language: what OpenStorm US cannot tell you, and where its data is weak. Read this before quoting any number.*

1. **Only what is posted openly.** Many cities keep complete drainage inventories internally and publish nothing, or publish a simplified view. A low score measures the public dataset, not the city's internal records or its maintenance practice.
2. **Catalog search can miss publishers.** The census finds layers through ArcGIS Hub and Socrata keyword search. Cities on other platforms (CKAN, custom portals, map viewers with no catalog entry) are missed unless added by hand.
3. **Publisher matching is rule-based.** Organization names are matched to Census places and counties by name and location. Ambiguous cases go to a review list; some errors may remain.
4. **Layer type is inferred from names and geometry.** A layer called "Structures" may hold inlets and manholes together. Mixed layers are scored under one type unless split by hand.
5. **Field meaning is inferred until reviewed.** Draft mappings use field names and aliases. A field called CONDITION may hold a construction status, not an assessed condition. Only reviewed mappings are used in release scores.
6. **Condition scales differ.** Cities use NASSCO PACP grades, 1 to 10 scales, or words like "Good". Mapping them to one 1 to 5 scale is a judgment, documented per publisher in the value maps.
7. **Connectivity is geometric, not hydraulic.** A pipe endpoint within 1 m of a node counts as connected. Flow direction, invert consistency and true topology are not checked in v1.0.
8. **Positional checks are coarse.** They catch empty, (0,0), far-away and duplicate features. They do not measure survey accuracy against ground truth.
9. **Snapshot in time.** Each layer is scored as downloaded on the date in `PROVENANCE.txt`. Portals change without notice.
10. **Very large layers are capped** at 250,000 features per layer for practicality; 2 layers hit the cap in the October 2026 harvest.
11. **Catalog search depth.** ArcGIS Hub returns at most 10,000 results per search term; the broad term "stormwater" reached that limit, so some publishers found only through it may be missed. Narrower terms reduce this.
12. **Unreachable services.** 141 service layers returned errors and 4 services could not be reached at all during the harvest (listed in `layer_inventory.csv`); they are retried in v1.1.
13. **Value lists are sampled.** The raw-value lists used to build material and condition value maps count the first 20,000 features of each layer; rare codes in very large layers may be missing from the list (they score as OTHER or invalid until mapped).
14. **Duplicate and non-asset layers** were removed by rule (`layer_registry.csv`); each removal is listed with its reason and can be overridden.
15. **Bulk-accepted crosswalk.** For v1.0 the author accepted the drafted field mappings in bulk (2026-10-15) rather than row by row. Every mapping was checked automatically against the data; 78 were refused and are listed in `docs/AUTO_CHECK_FLAGS.csv`, and 29 were added from data patterns (`docs/DATA_PATTERN_MAPPINGS.csv`). The verification checklist samples mappings for a by-hand check.
16. **Condition scales.** Five condition fields on 0 to 100 scales are read with 100 = best (Elorm, 2026-10-15). One field, Round Rock TX's -3 to 6 scale, has no documented direction and scores as no usable condition (`data/processed/condition_scales.csv`).
17. **Pre-1970s dates** and other coded values are read by documented default rules (`docs/DECISIONS.md`); a city whose codes mean something else may be scored lower or higher than it should be.
18. **Open conveyances.** Swales, channels, open drains, canals and flumes are scored like pipes except that diameter is not applicable to them (no credit, no penalty). Whether a layer is an open conveyance is judged from its name; an open channel published under a pipe-like name is scored as a pipe.
19. **Width versus rise.** The schema defines diameter as the rise (height) for non-round pipe. Where a city fills a height field only for its non-round pipes and a width field for all (Dublin OH, Norwalk CT), the width is used, so for their box and arch culverts (about 2 to 3 percent of their pipes) the value is the span, not the rise. Coverage scores are unaffected.
20. **Snapshots and record numbers.** Cities republish layers; record numbers (OBJECTID) can change afterwards (King County WA republished its swale layer on 2026-10-20). Every value is as downloaded on the date in `PROVENANCE.txt`.
