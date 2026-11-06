"""Artwork metadata enrichment without fetching the archived images."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath

from .http import SourceAccessError


def arcadia_family(filename: str, aliases: dict | None = None) -> str:
    stem = PurePosixPath(filename).stem.lower()
    stem = re.sub(r"-(?:raster|silhouette|tricolor-?stroke)$", "", stem)
    for old, new in (aliases or {}).items():
        if stem == old.lower() or stem.startswith(old.lower() + "-"):
            stem = new.lower() + stem[len(old):]
            break
    return stem


def load_arcadia_metadata(source: dict, client) -> dict:
    url = source["metadata_url"]
    text = client.get_text(url)
    match = re.search(r"window\.RECORDS\s*=\s*(\[.*?\]);", text, re.DOTALL)
    if not match:
        raise SourceAccessError(url, "official organism metadata no longer found")
    organisms = json.loads(match.group(1))
    if not organisms:
        raise SourceAccessError(url, "empty official organism metadata")
    aliases = source.get("filename_aliases", {})
    names, families = {}, {}
    for organism in organisms:
        for item in organism["files"]:
            filename = item["name"]
            names[filename.lower()] = (organism, item)
            key = arcadia_family(filename, aliases)
            if key in families and families[key][0]["sci"] != organism["sci"]:
                raise SourceAccessError(url, "ambiguous organism family metadata")
            families[key] = (organism, item)
    return {"names": names, "families": families, "url": url,
            "revision": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()}


def enrich_arcadia(record: dict, path: str, source: dict, metadata: dict) -> None:
    name = PurePosixPath(path).name
    family = arcadia_family(name, source.get("filename_aliases", {}))
    match = metadata["names"].get(name.lower()) or metadata["families"].get(family)
    record["family_hint"] = source["source_id"] + ":" + family
    record["metadata_source"] = metadata["url"]
    record["metadata_revision"] = metadata["revision"]
    if not match:
        record["metadata_status"] = "unmatched_archive_file"
        return
    organism, item = match
    scientific, common = organism["sci"], organism.get("common")
    style = "tricolor-stroke" if "tricolor" in name.lower() else ("silhouette" if "silhouette" in name.lower() else "raster")
    # Distinct cells/life stages keep distinct families; only image styles and
    # file formats collapse. The original archive member stays unchanged.
    record["title"] = f"{common} ({scientific})" if common else scientific
    organism_key = scientific.lower().replace(" ", "-")
    base_family = min((arcadia_family(f["name"], source.get("filename_aliases", {})) for f in organism["files"]), key=len)
    if family.startswith(base_family + "-"):
        record["title"] += " — " + family[len(base_family) + 1:].replace("-", " ")
    record["title"] += " — " + style
    record["scientific_name"] = scientific
    record["common_name"] = common
    record["organism_family"] = "arcadia:" + organism_key
    record["style"] = style
    record["tags"] = [x for x in (common, scientific, style) if x]
    record["provenance_notes"] = organism.get("notes") or None
    record["metadata_status"] = "matched_official_gallery"
    credit = f"{record['title']} — Arcadia Science; {source['source_url']}; CC0-1.0"
    if organism.get("notes"):
        credit += "; " + organism["notes"]
    record["attribution"] = credit
