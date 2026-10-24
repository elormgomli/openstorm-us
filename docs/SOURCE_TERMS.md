# Source terms and what we redistribute

*Plain language: cities post their data under different terms. Some allow anyone to copy it; some only allow viewing. This file records the rule OpenStorm US follows. Decided by Elorm on 2026-10-19 (see end).*

## Proposed rule for v1.0

- **Always published (our own work, CC BY 4.0):** the census list, each layer's URL and field list, the crosswalk, the scores, the schema, the QA report.
- **Harmonized copies of the features themselves:** published on Zenodo only for publishers whose stated terms allow redistribution (for example public domain, CC0, CC BY, or an open-data ordinance). For all others we publish the scores and the script that rebuilds the harmonized copy from the live source, so users fetch it themselves.
- Each publisher's terms are recorded in `data/processed/source_terms.csv` (added during checkpoint 2) with the text or link we relied on.

## Decision needed

Keep this rule, or publish scores and crosswalks only (no feature copies) for all publishers in v1.0? Scores-only is simpler and fully safe; feature copies are more useful to other researchers.

## Decided (2026-10-19)

Option A, as recommended: v1.0 publishes our own work (census, schema, crosswalk, value maps, scores, QA) and the rebuild code, with no copies of the cities' features. Of the 50 v1.0 publishers only Sioux Falls SD states a fully open license (CC BY 4.0); Novi MI says public domain but restricts sale and marketing use, so it is counted as restricted (Elorm, 2026-10-20); five state restrictions in all; the rest state no terms.
