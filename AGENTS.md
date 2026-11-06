# MGA-resource

- Explain in plain Chinese. Ask promptly about material uncertainties that cannot be resolved by inspecting source evidence.
- Goal: a free, agent-first medical illustration library; finish collection, classification, deduplication and acceptance before implementing the public website.
- Accept scientific illustrations and icons in vector and raster formats. Do not call an SVG containing a bitmap editable vector.
- Keep bulk images and generated previews in cloud CI and cloud releases/hosting. Local work is code, metadata and bounded review samples only.
- Reuse upstream open-source implementations when useful, retain notices, and preserve each artwork's own license and attribution.
- Never download the full library onto the user's computer. Never add paid infrastructure or billing products.
- Preserve MGA-Designer and its existing work. Do not resume skill training.
- Check all published download files, hashes, actual image previews, catalog counts and coverage; candidates and machine checks are not Illustrator acceptance.

## Implementation contract

Python package: `mga_resources` (Python 3.12+). Cloud scripts under `scripts/`, tests under `tests/`, source registry and benchmark under `data/`.

Discovery returns JSON-serializable records with fields:
`source`, `source_id`, `title`, `source_url`, `author`, `license`, `license_url`, `attribution`, `categories` (list), `tags` (list), `upstream_revision`, `files` (list of `{url, format, upstream_hash?}`). Missing facts stay null/empty, never invented.

Processing adds stable `id`, normalized metadata, real media type, dimensions/transparency, file SHA-256 and size, preview info, validation findings, taxonomy and dedup family. Original bytes remain unchanged.

Sources with unclear item rights are excluded from public distribution until resolved, with explicit counts. A blocked request is recorded and not repeatedly retried or bypassed.
