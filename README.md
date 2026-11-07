# MGA-resource · mainly-for-agent

A free medical illustration library for agents and people. Scientific illustrations and icons may be vectors, raster images, or mixed artwork. File formats and actual editing capabilities are recorded separately.

## Current phase

Collection, classification, deduplication and acceptance. The website is not yet built or deployed. Counts in upstream catalogs are candidates, not accepted artwork.

The [2026-11-07 QA candidate](docs/CLOUD-QA-RECOVERY-20261107.md) has passed content-scope filtering and an independent cloud restore of 19,473 originals with their previews. The curated set now contains 60 distinct families, five per category, with actual display and use-case composition reviews and recorded limitations. These QA archives are not the formal website release.

Current formal acceptance records 102 query passes and 18 gaps, with 51/60 in each language. The 60-item curated set, six negative scenarios, source checks, color/style/diversity evidence and recovery verification pass. Six remaining checks concern query coverage: 102/120 is below 108, each language needs 54/60, and clinical workflow, experimental actions and omics each cover 2/5 concepts against a 4/5 requirement. The original questions and thresholds remain unchanged.

A science-metadata update changes seven files without changing scope or artwork. Its cloud replay preserves 118 query result lists and adds one exact lysosome SVG to each of the two remaining queries. A separate cloud proof establishes complete manifest equality except for the catalog hash, allowing reuse of the historical independent artwork restore; it does not claim a fresh artwork read. The new catalog, index and internal scope records also pass cloud audit. [Current evidence and remaining failures](data/diagnostics/science-version-acceptance-42326843247.json) retain the earlier 100/120 result as history. The [60-item QA package](data/diagnostics/curated-60-42320795764.json) contains actual reviews and attribution.

The data phase includes [file-level color/style descriptions and reproducible selection diversity](docs/INCREMENTAL-PLAN-visual-diversity.md). The current index has passed actual replay of nine functional cases, 75 appearance-filter calls and ten adoption-history projects. Historical choices remain fixed; replay does not create new adoption or visual-review claims. Overall collection acceptance still precedes website implementation.

Bulk artwork is processed on GitHub Actions cloud runners. This repository contains code, source descriptions and acceptance definitions, not the full image library. Versioned collection packages and reports are published as GitHub Release attachments. Future public file delivery uses Cloudflare Free static assets.

## Collection workflow

1. Discover published source metadata and pin the upstream version.
2. Fetch artwork on a cloud runner in bounded batches.
3. Preserve originals, inspect real media structure, create previews and retain attribution.
4. Publish durable release packages, file hashes, source reports and failures.
5. Aggregate all batches into a deduplicated catalog and run the bilingual coverage benchmark.
6. Complete visual and selected Illustrator acceptance before website implementation.

Run **Collect source** from the Actions tab. Standard Ubuntu runners in this public repository are used. No R2, paid AI, Git LFS, or larger runners are required.

## Local development

Python 3.12+:

```text
py -3.12 -m pip install -e ".[test]"
py -3.12 -m pytest
```

Local development is code and tiny test fixtures only. The collection command refuses bulk artwork acquisition outside GitHub Actions.

## Licenses

Project code is MIT unless an included upstream notice states otherwise. Artwork retains its own license, author, source and attribution requirements. A project's code license does not relicense its illustrations. See `NOTICE` and `docs/SOURCES.md`.
