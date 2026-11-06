# MGA-resource · mainly-for-agent

A free medical illustration library for agents and people. Scientific illustrations and icons may be vectors, raster images, or mixed artwork. File formats and actual editing capabilities are recorded separately.

## Current phase

Collection, classification, deduplication and acceptance. The website is not yet built or deployed. Counts in upstream catalogs are candidates, not accepted artwork.

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
