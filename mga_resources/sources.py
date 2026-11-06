"""Version-pinned discovery without cloning or downloading source artwork.

Bioicons/Health Icons field mappings follow their public catalogs and the
BSD-2-Clause BioCartoon source adapters (Joseph Rich, 2026). Network, version
tracking and file delivery are independently implemented for cloud CI.
"""
from __future__ import annotations

import os
import re
import urllib.parse
from pathlib import PurePosixPath

from .http import HttpClient, SourceAccessError

LICENSES = {
    "cc-0": ("CC0-1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "cc0": ("CC0-1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "cc0-1.0": ("CC0-1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "cc-by-3.0": ("CC-BY-3.0", "https://creativecommons.org/licenses/by/3.0/"),
    "cc-by-4.0": ("CC-BY-4.0", "https://creativecommons.org/licenses/by/4.0/"),
    "cc-by-sa-3.0": ("CC-BY-SA-3.0", "https://creativecommons.org/licenses/by-sa/3.0/"),
    "cc-by-sa-4.0": ("CC-BY-SA-4.0", "https://creativecommons.org/licenses/by-sa/4.0/"),
    "mit": ("MIT", "https://opensource.org/license/mit"),
}


def humanize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value).replace("_", " ").replace("-", " ")).strip()


def _raw(repository: str, revision: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repository}/{revision}/{urllib.parse.quote(path, safe='/')}"


def _git_tree(source: dict, client: HttpClient) -> tuple[str, list[dict]]:
    repository = source["repository"]
    ref = source.get("ref") or "HEAD"
    commit = client.get_json(f"https://api.github.com/repos/{repository}/commits/{urllib.parse.quote(ref, safe='')}")
    revision = commit["sha"]
    tree_sha = commit["commit"]["tree"]["sha"]
    tree_url = f"https://api.github.com/repos/{repository}/git/trees/{tree_sha}?recursive=1"
    tree = client.get_json(tree_url)
    if tree.get("truncated"):
        raise SourceAccessError(tree_url, "truncated tree; source discovery cannot be declared complete")
    return revision, tree["tree"]


def _selected(path: str, source: dict) -> bool:
    extensions = {x.lower().lstrip(".") for x in source.get("extensions", ["svg", "png", "webp"])}
    if PurePosixPath(path).suffix.lower().lstrip(".") not in extensions:
        return False
    prefixes = source.get("path_prefixes", [""])
    if not any(path.startswith(p) for p in prefixes):
        return False
    include = source.get("include_regex")
    exclude = source.get("exclude_regex")
    return (not include or bool(re.search(include, path))) and not (exclude and re.search(exclude, path))


def _base(source: dict, sid: str, title: str, url: str, revision: str) -> dict:
    author = source.get("author")
    license_id = source.get("license")
    return {"source": source["source_id"], "source_id": sid, "title": title,
            "source_url": url, "author": author, "license": license_id,
            "license_url": source.get("license_url"),
            "attribution": source.get("attribution") or f"{title} — {author or source.get('name', source['source_id'])}; {url}; {license_id or 'license unconfirmed'}",
            "categories": list(source.get("categories", [])), "tags": [],
            "upstream_revision": revision, "files": []}


def _metadata(source: dict, client: HttpClient, revision: str) -> dict[str, dict]:
    kind = source.get("metadata_kind")
    path = source.get("metadata_path")
    if not path:
        return {}
    data = client.get_json(_raw(source["repository"], revision, path))
    if kind == "healthicons":
        return {item["path"]: item for item in data}
    if kind == "scisvg":
        return {item["path"]: item for item in data["assets"]}
    return {}


def discover_github(source: dict, client: HttpClient) -> list[dict]:
    revision, tree = _git_tree(source, client)
    metadata = _metadata(source, client, revision)
    results = []
    for item in sorted(tree, key=lambda x: x["path"]):
        path = item["path"]
        if item["type"] != "blob" or not _selected(path, source):
            continue
        url = f"https://github.com/{source['repository']}/blob/{revision}/{urllib.parse.quote(path, safe='/')}"
        record = _base(source, path, humanize(PurePosixPath(path).stem), url, revision)
        if source.get("metadata_kind") == "healthicons":
            # public/icons/svg/<style>/<category>/<name>.svg
            relative = path.removeprefix("public/icons/svg/")
            style, _, icon_path = relative.partition("/")
            info = metadata.get(str(PurePosixPath(icon_path).with_suffix("")), {})
            record["title"] = info.get("title", record["title"])
            record["tags"] = [t for t in info.get("tags", []) if not t.startswith("dhis2:")]
            record["tags"] += [style, info.get("category", "")]
            record["style"] = style
            record["family_hint"] = f"healthicons:{PurePosixPath(icon_path).with_suffix('')}"
            record["upstream_category"] = info.get("category")
        elif source.get("metadata_kind") == "scisvg":
            info = metadata.get(path, {})
            record["title"] = info.get("name", record["title"])
            record["description"] = info.get("description")
            record["author"] = info.get("contributor", record["author"])
            record["license"] = info.get("license", record["license"])
            record["tags"] = info.get("tags", []) + [info.get("category", ""), info.get("subcategory", "")]
            record["upstream_category"] = info.get("category")
        record["tags"] = [x for x in record["tags"] if x]
        record["attribution"] = f"{record['title']} — {record['author'] or source.get('name', source['source_id'])}; {url}; {record['license']}"
        record["files"] = [{"url": _raw(source["repository"], revision, path),
                             "format": PurePosixPath(path).suffix[1:].lower(),
                             "upstream_hash": "git-blob-sha1:" + item["sha"],
                             "upstream_size": item.get("size")}]
        results.append(record)
    return results


def discover_bioicons(source: dict, client: HttpClient) -> list[dict]:
    revision, tree = _git_tree(source, client)
    authors = client.get_json(_raw(source["repository"], revision, "static/icons/authors.json"))
    results = []
    for item in sorted(tree, key=lambda x: x["path"]):
        path = item["path"]
        if item["type"] != "blob" or not path.startswith("static/icons/") or not path.endswith(".svg"):
            continue
        parts = path.split("/")
        if len(parts) < 5:
            continue
        lic, category = parts[2:4]
        author = parts[4] if len(parts) >= 6 else None
        license_id, license_url = LICENSES.get(lic, ("LicenseRef-" + lic, None))
        display_author = author.replace("_", " ") if author else None
        author_url = authors.get(author) or authors.get(display_author)
        url = f"https://github.com/{source['repository']}/blob/{revision}/{urllib.parse.quote(path, safe='/')}"
        record = _base(source, path.removeprefix("static/icons/"), humanize(PurePosixPath(path).stem), url, revision)
        record.update(author=display_author, license=license_id, license_url=license_url,
                      author_url=author_url or None, upstream_category=category,
                      tags=[humanize(category)], license_evidence=url)
        if not author:
            record["distribution_allowed"] = False
            record["rights_status"] = "pending"
            record["rights_note"] = "Author attribution missing from source path; requires item-level resolution."
        elif any(held.casefold() in author.casefold() for held in source.get("hold_authors", ["Servier"])):
            # Preserve the declared license, but do not silently mirror through
            # an aggregator while the standalone-library question is unresolved.
            record["distribution_allowed"] = False
            record["rights_status"] = "pending"
            record["rights_note"] = "Servier standalone-library redistribution conditions require resolution."
        record["attribution"] = f"{record['title']} — {display_author}; {author_url or url}; {license_id}; via Bioicons"
        record["files"] = [{"url": _raw(source["repository"], revision, path), "format": "svg",
                             "upstream_hash": "git-blob-sha1:" + item["sha"], "upstream_size": item.get("size")}]
        results.append(record)
    return results


def discover_archive(source: dict, client: HttpClient) -> list[dict]:
    if os.environ.get("GITHUB_ACTIONS") != "true" and not source.get("allow_archive_download"):
        raise SourceAccessError(source["archive_url"], "bulk archive discovery is cloud-only")
    results = []
    revision = source.get("upstream_revision") or source["archive_checksum"]
    with client.get_archive(source["archive_url"], source.get("archive_checksum")) as archive:
        for member in sorted(archive.infolist(), key=lambda x: x.filename):
            path = member.filename
            if member.is_dir() or path.startswith("__MACOSX/") or not _selected(path, source):
                continue
            record = _base(source, path, humanize(PurePosixPath(path).stem), source["source_url"], revision)
            record["tags"] = [humanize(x) for x in PurePosixPath(path).parts[:-1] if x.lower() not in ("svg", "png")]
            family = "/".join(x for x in PurePosixPath(path).with_suffix("").parts if x.lower() not in ("svg", "png"))
            record["family_hint"] = source["source_id"] + ":" + family
            record["files"] = [{"url": source["archive_url"], "format": PurePosixPath(path).suffix[1:].lower(),
                                 "archive_member": path, "archive_checksum": source.get("archive_checksum"),
                                 "upstream_size": member.file_size}]
            results.append(record)
    return results


def discover_scidraw(source: dict, client: HttpClient) -> list[dict]:
    """Official paginated API mapping, adapted from BioCartoon's BSD adapter."""
    url = source.get("api_url", "https://scidraw.io/api/v1/drawings/?page=1")
    results, seen_pages = [], set()
    while url:
        if url in seen_pages:
            raise SourceAccessError(url, "pagination loop")
        seen_pages.add(url)
        response = client.get_json(url)
        for item in response.get("results", []):
            image_url = item["image_url"].replace("http://", "https://", 1)
            ext = (item.get("image_type") or urllib.parse.urlsplit(image_url).path.rsplit(".", 1)[-1]).lower()
            if ext not in source.get("extensions", ["svg", "png", "jpg", "jpeg", "webp"]):
                continue
            page = f"https://scidraw.io/drawing/{item['slug']}"
            record = _base(source, str(item["id"]), item["name"], page,
                           str(item.get("updated_at") or item.get("doi") or "unversioned-api"))
            raw_license = item.get("license", "")
            key = raw_license.lower().replace(" ", "-")
            license_id, license_url = LICENSES.get(key, (raw_license or None, None))
            alias = source.get("license_aliases", {}).get(key)
            if alias:
                license_id, license_url = alias["license"], alias["license_url"]
            record.update(license=license_id, license_url=license_url,
                          license_original=raw_license,
                          license_evidence=alias.get("evidence_url") if alias else page,
                          author=(item.get("primary_author") or {}).get("full_name"),
                          upstream_category=item.get("category_name"), doi=item.get("doi"))
            if key in ("cc-by", "ccby") and not alias:
                record.update(distribution_allowed=False, rights_status="pending",
                              rights_note="SciDraw API declares CC-BY without its version; exact license needs verification.")
            record["tags"] = [item["category_name"]] if item.get("category_name") else []
            record["attribution"] = f"{record['title']} — {record['author'] or 'author not supplied'}; {page}; {license_id}; DOI {item.get('doi') or 'not supplied'}"
            record["files"] = [{"url": image_url, "format": ext}]
            results.append(record)
        next_url = response.get("next")
        if next_url and urllib.parse.urlsplit(next_url).hostname != "scidraw.io":
            raise SourceAccessError(url, "unexpected next-page host")
        url = next_url.replace("http://", "https://", 1) if next_url else None
    return results


def discover(source: dict, client: HttpClient) -> list[dict]:
    """Discover a complete source snapshot or fail explicitly (never partial-success)."""
    from .web_sources import discover_nih, discover_reactome, discover_swissbiopics

    adapters = {"github_tree": discover_github, "bioicons": discover_bioicons,
                "archive": discover_archive, "zenodo_archive": discover_archive,
                "scidraw": discover_scidraw, "nih_bioart": discover_nih,
                "swissbiopics": discover_swissbiopics, "reactome": discover_reactome}
    adapter = source.get("adapter", "github_tree")
    if adapter not in adapters:
        raise ValueError(f"Source adapter not yet implemented: {adapter}")
    return adapters[adapter](source, client)
