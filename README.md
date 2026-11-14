# MGA-resource · mainly-for-agent

**English** · [简体中文](docs/README.zh-CN.md)

A free library of reusable medical illustration components for drawing agents and researchers. Search for organs, cells, animals, clinical scenes or equipment, then get a preview, the exact original and its attribution.

**[Open the workbench](https://mga-resource-mainly-for-agent.pages.dev/) · [Agent API](https://mga-resource-mainly-for-agent.pages.dev/agent) · [Project story](https://mga-resource-mainly-for-agent.pages.dev/about) · [Sources](https://mga-resource-mainly-for-agent.pages.dev/sources)**

## Why it exists

MGA-Designer needs components that can be assembled into new graphical abstracts. Extracting reusable objects from existing figures takes time. Searching across scattered libraries also makes it difficult for an agent to compare styles, retrieve the exact file and keep its license attached. MGA-resource makes that workflow reproducible.

## Start here

Browse by category or enter a medical object. Narrow results by color, style, format or source, inspect the actual preview, and save or download the file. A project keeps a stable seed, selected files and a style plan; changing projects or exploration rounds gives suitable alternatives a chance to appear. Export the local project to share your selections with an agent.

```sh
curl -sS 'https://mga-resource-mainly-for-agent.pages.dev/v1/search?q=mouse&limit=5&response_profile=compact'
```

For an agent, inspect `preview.url`, retrieve `download.url`, verify `download.sha256` and keep `selected_source.provenance`. There is no need to navigate to the upstream gallery or unpack the catalog. [Runnable example](site/public/agent-example.mjs) · [OpenAPI](https://mga-resource-mainly-for-agent.pages.dev/openapi.json) · [Catalog and filter values](https://mga-resource-mainly-for-agent.pages.dev/catalog-info.json)

## How it was built

![Survey sources, collect in the cloud, preserve originals, index, validate and publish](site/public/workflow-en.svg)

On 6 November 2026, collection began from pinned upstream revisions on cloud runners, preserving original bytes and attribution. File inspection and previews supported grouping variants into families and recording appearance. On 7 November the scope was refined using actual visual evidence: reusable labeled illustrations stayed, while complete text-led workflows, research design teaching figures and result charts went out. The library went online after separate component and website acceptance on 14 November. [Milestones and evidence](docs/CLOUD-QA-RECOVERY-20261107.md)

## Where the artwork comes from

**19,473 downloadable originals across 7,838 families.** Examples include detailed NIH BioArt illustrations, experimental and animal drawings from SciDraw, Health Icons, Reactome structural components, and life science illustrations from Bioicons and DBCLS Togo. Pinned historical Servier artwork distributed through Bioicons retains its specific credit and license record.

[Sources, published counts and rights](docs/SOURCES.md) · [Source directory](https://mga-resource-mainly-for-agent.pages.dev/sources). A file can have more than one source record; the source counts overlap.

## Evidence and limits

| Evidence | Recorded result |
| --- | --- |
| [Component retrieval acceptance](data/acceptance-reports/component-43481252923.json) | 120/120 task-language units |
| [Original historical benchmark](docs/CLOUD-QA-RECOVERY-20261107.md) | 102/120, a different benchmark |
| [Initial website delivery](data/acceptance-reports/website-20261114.json) | 142 aligned requests and 38,748 original and preview objects checked by HTTP and SHA-256 |

Inspect candidates for the intended scientific use. SVG is a file format; some files contain raster content. Adjust labels only on a copy, preserving scientific meaning and credit. Code is MIT unless an upstream notice specifies otherwise. Artwork keeps its own license. Free search has a daily resource limit and reports service failures separately from no matches; known static links remain accessible.

[Architecture and maintainer notes](docs/WEBSITE-ARCHITECTURE.md). Bulk artwork is processed in cloud CI and served from published objects; it does not live in Git history.
