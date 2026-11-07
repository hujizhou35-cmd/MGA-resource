# MGA-resource · mainly-for-agent

A free medical illustration library for agents and people. Scientific illustrations and icons may be vectors, raster images, or mixed artwork. File formats and actual editing capabilities are recorded separately.

## Current phase

Collection, classification, deduplication and acceptance. The website is not yet built or deployed. Counts in upstream catalogs are candidates, not accepted artwork.

The [2026-11-07 QA candidate](docs/CLOUD-QA-RECOVERY-20261107.md) has passed content-scope filtering and an independent cloud restore of 19,473 originals with their previews. The curated set now contains 60 distinct families, five per category, with actual display and use-case composition reviews and recorded limitations. These QA archives are not the formal website release.

Acceptance on the restored catalog records 100 query passes and 20 gaps. The earlier two pending endothelium cases were corrected under the existing rule allowing explicit reusable components; the files remain labeled as individual cells. The original 120 questions and 706 result positions are unchanged. Overall acceptance remains incomplete because retrieval coverage is below the original thresholds.

A subsequent science-metadata update changes seven files without changing scope or artwork. Its cloud replay preserves 118 query result lists and adds one exact lysosome SVG to each of the two remaining queries. [Evidence-based relevance confirmation](data/diagnostics/science-update-42322620502-relevance.json) is now 102/120, with 51/60 in each language. New-version publication bindings remain pending. The [60-item QA package](data/diagnostics/curated-60-42320795764.json) includes actual reviews, attribution and the remaining acceptance failures.

The current data phase also includes [file-level color/style descriptions and reproducible selection diversity](docs/INCREMENTAL-PLAN-visual-diversity.md). Existing collection checkpoints continue; the new classification, variant filters, ranking and diversity checks must be finished before catalog acceptance and website deployment.

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
