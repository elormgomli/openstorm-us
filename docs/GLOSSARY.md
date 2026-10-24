# Glossary

Plain definitions of the terms you will see in this project.

**Abstract.** A short summary at the top of a paper, usually 150 to 300 words.

**Archive (release archive).** A single zip file containing a frozen copy of the whole project at one point in time.

**arXiv, medRxiv, bioRxiv, engrXiv.** Free public websites where researchers post papers before or alongside journal review. Each covers different fields.

**Citation file (CITATION.cff).** A small file that tells people and software exactly how to cite your project.

**Codebook.** A list explaining every column in your data: what it means, its units, and where it came from.

**Commit.** A saved snapshot of changes to the project, with a note describing what changed.

**Dataset.** An organized table (or set of tables) of information, like a spreadsheet.

**Docket.** The public file for a proposed government rule. Comments submitted to it become part of the official record.

**DOI (Digital Object Identifier).** A permanent link and ID for your work, like a library call number that never changes. It looks like `10.5281/zenodo.1234567`.

**Draft stamp.** A label marking every figure and document as not yet verified, kept until the author checked everything. Removed on 2026-10-21 after Elorm's approval.

**Endorsement (arXiv).** A one-time approval some arXiv categories require from an existing author before your first post there.

**Figure.** A chart, graph, or map in a paper or report.

**GitHub.** A website that stores project files publicly and keeps every past version.

**License.** The terms under which others may reuse your work. CC BY 4.0 means anyone can reuse it if they credit you. MIT is a common equivalent for code.

**Limitations.** An honest list of what the project cannot tell you and where its data is weak. Every credible project has one.

**Metadata.** Information about your work: title, authors, description, keywords, license.

**Open data.** Information published free for anyone to use, usually by governments or research bodies.

**ORCID iD.** A free, permanent ID number for researchers, so your work is linked to you even if your name is spelled differently somewhere. It looks like `0000-0002-1825-0097`.

**Pipeline.** The set of scripts that downloads the data, cleans it, and produces the results, in order. Running it again gives the same results.

**Preprint.** A paper posted publicly before peer review.

**Provenance.** A record of exactly where each piece of data came from and when it was downloaded.

**Public comment.** Written feedback submitted to a government agency on a proposed rule, during a set comment period.

**QA (quality assurance) report.** A file listing the checks run on the data: counts, matches, and anything unusual.

**README.** The front page of a project. It explains what the project is and how to use it.

**Release.** A named, frozen version of the project (for example v1.0) that can be cited.

**Reproduce.** To re-run the project from scratch and get the same numbers. It proves the results are real.

**Repository (repo).** The project folder that lives online, usually on GitHub, with its full history.

**Sandbox.** A practice version of a website (like Zenodo's) for testing before doing it for real.

**Script.** A small program file that does one job, such as downloading data.

**Spot check.** Picking a few rows by hand and comparing them to the original source.

**Stats file.** A file the pipeline writes with every number used in the paper, so the text always matches the data.

**Token.** A temporary password that lets Claude upload to GitHub or Zenodo on your behalf. You can cancel it at any time.

**Tracking number.** The receipt ID you get after submitting a public comment. Save it.

**Vintage.** The year or edition of a data source.

**Zenodo.** A free, permanent research archive run by CERN. It gives your project a DOI.

## Terms specific to OpenStorm US

**Attribute coverage.** The share of features in a layer that have a real value (not blank, not "Unknown", not 0 as a stand-in) for a field asset managers rely on, such as pipe material or install year.

**Benchmark.** A fixed, public yardstick. Here: one schema plus one scoring method, so any city's storm-sewer data can be measured the same way.

**Connectivity.** Whether pipes actually touch the inlets, manholes and outfalls they should. A pipe whose end floats 3 feet from the nearest manhole breaks the network for flow tracing.

**Crosswalk.** A lookup table that says "this city calls it PIPE_MATL; in our common schema that is `material`." One row per source field.

**Feature layer / Feature service.** How ArcGIS publishes map data online. One layer is one type of thing (for example, all inlets in a city), and it can be downloaded as data.

**Open-data portal.** A government website that publishes datasets for free download. Most US cities use ArcGIS Hub or Socrata.

**Positional QA.** Checks that features sit where they should: inside the city's boundary, not on top of each other, not at latitude 0 longitude 0.

**PyPI.** The public library of Python packages. Publishing `openstorm-qa` there lets anyone install it with one command.

**Schema.** The agreed list of fields, their names, types and allowed values. The common schema is the single format every city's data is translated into.

**Topology.** The rules about how features connect and overlap (pipes meet at nodes, no duplicate points). Topology checks are the automated form of connectivity checks.
