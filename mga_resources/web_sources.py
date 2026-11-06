"""Official public website catalogs; discovery reads metadata, never image bytes.

The byte-length RSC row parser and NIH file URL mapping adapt BioCartoon
data/scrape_bioart.py at ebc635137a17b3299c876b70d0633f69f35239a2.
Copyright (c) 2026 Joseph Rich, BSD-2-Clause; full notice:
docs/upstream-biocartoon-license.txt.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
from html.parser import HTMLParser

from .http import SourceAccessError
from .sources import _base


class CatalogHTML(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.links, self.cells, self.scripts = [], [], []
        self._link = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self._link = [attrs["href"], ""]
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs["src"])
        if "inj" in attrs.get("class", "").split() and attrs.get("data-name"):
            self.cells.append(attrs)

    def handle_data(self, data):
        if self._link is not None:
            self._link[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._link is not None:
            self.links.append(tuple(self._link))
            self._link = None


def parse_rsc(blob: str) -> list:
    """Decode JSON rows and byte-counted UTF-8 text rows from React Flight."""
    data, pos, rows, texts = blob.encode("utf-8"), 0, [], {}
    while pos < len(data):
        match = re.match(rb"([0-9a-f]+):", data[pos:])
        if not match:
            pos = data.find(b"\n", pos)
            if pos < 0:
                break
            pos += 1
            continue
        rid = match.group(1).decode()
        pos += match.end()
        if data[pos:pos + 1] == b"T":
            comma = data.find(b",", pos)
            length = int(data[pos + 1:comma], 16)
            start = comma + 1
            if comma < 0 or start + length > len(data):
                raise ValueError("Truncated React Flight text row")
            texts[rid] = data[start:start + length].decode("utf-8")
            pos = start + length
        else:
            end = data.find(b"\n", pos)
            if end < 0:
                end = len(data)
            payload = data[pos:end]
            pos = end + 1
            if payload[:1] in (b"{", b"[", b'"'):
                rows.append(json.loads(payload))

    def resolve(value):
        if isinstance(value, str) and value.startswith("$") and value[1:] in texts:
            return texts[value[1:]]
        if isinstance(value, list):
            return [resolve(x) for x in value]
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        return value

    return [resolve(row) for row in rows]


def _first(fields, key, default=None):
    value = fields.get(key, [])
    return value[0] if isinstance(value, list) and value else (value or default)


def discover_nih(source, client):
    url = "https://bioart.niaid.nih.gov/discover"
    page = client.get_text(url)
    scripts = [p for p in CatalogHTML(page).scripts if "/discover/page-" in p]
    if len(scripts) != 1:
        raise SourceAccessError(url, "official discover script changed; adapter needs review")
    script_url = urllib.parse.urljoin(url, scripts[0])
    script = client.get_text(script_url)
    action = re.search(r'createServerReference\)\("([0-9a-f]+)"[^;]{0,150}"discoverSearch"', script)
    if not action:
        raise SourceAccessError(url, "official discoverSearch action no longer found")
    headers = {"Next-Action": action.group(1), "Content-Type": "text/plain;charset=UTF-8", "Accept": "text/x-component"}
    results, seen, start, total = [], set(), 0, None
    extensions = set(source.get("extensions", ["svg", "png"]))
    while total is None or start < total:
        response = client.post_text(url, json.dumps([f"type:bioart?start={start}&size=100"]), headers=headers)
        rows = parse_rsc(response)
        catalog = next((r for r in rows if isinstance(r, dict) and isinstance(r.get("hits"), dict)), None)
        if catalog is None:
            raise SourceAccessError(url, "search response has no hits object")
        found = int(catalog["hits"]["found"])
        if total is not None and total != found:
            raise SourceAccessError(url, "catalog changed during pagination; snapshot incomplete")
        total = found
        hits = catalog["hits"].get("hit", [])
        if not hits and start < total:
            raise SourceAccessError(url, "empty page before declared catalog end")
        for item in hits:
            fields = item["fields"]
            sid = str(_first(fields, "id"))
            if not sid or sid == "None" or sid in seen:
                raise SourceAccessError(url, "missing/duplicate ID in paginated catalog")
            seen.add(sid)
            item_url = "https://bioart.niaid.nih.gov/bioart/" + sid
            title = html.unescape(_first(fields, "title", sid))
            record = _base(source, sid, title, item_url, _first(fields, "updated", "unversioned"))
            declared_license = _first(fields, "license")
            author = "; ".join(fields.get("creator", [])) or None
            record.update(author=author, license=declared_license, license_url=None,
                          license_evidence=item_url, upstream_action_script=script_url,
                          description=html.unescape(re.sub(r"<[^>]+>", " ", _first(fields, "description", ""))),
                          upstream_category=fields.get("ontologytype", []),
                          tags=sorted(set(html.unescape(x) for x in fields.get("keywords", []) + fields.get("ontologykey", []))),
                          family_hint="nih-bioart:" + sid)
            if declared_license != "Public Domain":
                # The search facet abbreviates CC-BY; the actual item's
                # Licensing anchor provides the version. Read only HTML.
                detail = client.get_text(item_url)
                license_links = {href.rstrip("/") + "/" for href, _ in CatalogHTML(detail).links
                                 if re.fullmatch(r"https://creativecommons.org/licenses/by/(?:1\.0|2\.0|2\.5|3\.0|4\.0)/?", href)}
                record["license_original"] = declared_license
                if f"BIOART-{int(sid):06d}" in detail and len(license_links) == 1:
                    record["license_url"] = next(iter(license_links))
                    record["license"] = "CC-BY-" + record["license_url"].strip("/").rsplit("/", 1)[1]
                else:
                    record.update(distribution_allowed=False, rights_status="pending",
                                  rights_note="Exact item license version could not be resolved from its Licensing link.")
            record["attribution"] = f"{title} — {author or 'creator not supplied'}, NIH BioArt Source; {item_url}; {record['license']}"
            for raw in fields.get("filesinfo", []):
                for group in raw.split("|"):
                    fmt, separator, values = group.partition(":")
                    fmt = fmt.lower()
                    if not separator or fmt not in extensions:
                        continue
                    for file_id in values.split(","):
                        if file_id.strip().isdigit():
                            record["files"].append({"url": f"https://bioart.niaid.nih.gov/api/bioarts/{sid}/files/{file_id.strip()}", "format": fmt})
            # Keep metadata-only entries visible to inventory/coverage, rather
            # than silently treating missing requested formats as a success.
            results.append(record)
        start += len(hits)
    if len(seen) != total:
        raise SourceAccessError(url, f"catalog count mismatch: {len(seen)} of {total}")
    return sorted(results, key=lambda r: int(r["source_id"]))


def discover_swissbiopics(source, client):
    url = "https://www.swissbiopics.org/"
    text = client.get_text(url)
    revision = "catalog-sha256:" + hashlib.sha256(text.encode()).hexdigest()
    results, seen = [], set()
    for item in CatalogHTML(text).cells:
        name = item["data-name"]
        if name in seen:
            continue
        seen.add(name)
        filename = name if name.endswith(".svg") else name + ".svg"
        title = item.get("data-title") or name.removesuffix(".svg").replace("_", " ")
        page = urllib.parse.urljoin(url, item.get("data-target") or "name/" + name)
        record = _base(source, name.removesuffix(".svg"), title, page, revision)
        record["family_hint"] = "swissbiopics:" + name.removesuffix(".svg")
        record["files"] = [{"url": url + "api/image/" + urllib.parse.quote(filename), "format": "svg"}]
        record["tags"] = [title, "cell", "subcellular locations"]
        results.append(record)
    if not results:
        raise SourceAccessError(url, "cell catalog selectors changed or catalog empty")
    return results


def discover_reactome(source, client):
    url = "https://reactome.org/icon-lib"
    root = client.get_text(url)
    revision = "catalog-sha256:" + hashlib.sha256(root.encode()).hexdigest()
    queue = [url]
    visited, items = set(), {}
    decoding_warnings = []
    while queue:
        page = queue.pop(0)
        if page in visited:
            continue
        visited.add(page)
        if len(visited) > 250:
            raise SourceAccessError(url, "category pagination exceeded 250 pages")
        try:
            text = root if page == url else client.get_text(page)
        except UnicodeDecodeError as exc:
            # Some official category HTML contains isolated Latin-1 symbols
            # inside otherwise UTF-8 markup. Preserve IDs and disclose repair.
            text = exc.object.decode("utf-8", errors="replace")
            decoding_warnings.append(page)
        for href, label in CatalogHTML(text).links:
            target = urllib.parse.urljoin(page, href)
            parsed = urllib.parse.urlsplit(target)
            if parsed.hostname != "reactome.org":
                continue
            match = re.fullmatch(r"/content/detail/(R-ICO-\d+)", parsed.path)
            if match:
                sid = match.group(1)
                title = re.sub(r"\s+", " ", label).strip() or sid
                if sid not in items:
                    record = _base(source, sid, title, target, revision)
                    record["author"] = None  # Designer not asserted from category pages.
                    record["attribution"] = f"{title} — Reactome (Cold Spring Harbor Laboratory, Ontario Institute for Cancer Research, European Bioinformatics Institute); {target}; CC BY 4.0; original artwork unchanged"
                    record["family_hint"] = "reactome:" + sid
                    record["files"] = [{"url": f"https://reactome.org/icon/{sid}.{ext}", "format": ext} for ext in source.get("extensions", ["svg", "png"])]
                    items[sid] = record
                elif items[sid]["title"] == sid and title != sid:
                    items[sid]["title"] = title
                    items[sid]["attribution"] = items[sid]["attribution"].replace(sid + " —", title + " —", 1)
                category = urllib.parse.unquote(urllib.parse.urlsplit(page).path.rsplit("/", 1)[-1]).replace("_", " ")
                if category != "icon-lib" and category not in items[sid]["tags"]:
                    items[sid]["tags"].append(category)
            elif parsed.path.startswith("/icon-lib/"):
                # Follow actual catalog/category pagination, not invented IDs.
                path = urllib.parse.quote(parsed.path.rstrip("/"), safe="/%")
                target = urllib.parse.urlunsplit(("https", "reactome.org", path, parsed.query, ""))
                if target not in visited and target not in queue:
                    queue.append(target)
    count = re.search(r"([\d,]+)\s+unique\s+icons", root, re.IGNORECASE)
    if not items or (count and len(items) != int(count.group(1).replace(",", ""))):
        raise SourceAccessError(url, f"icon inventory incomplete: {len(items)} versus declared {count.group(1) if count else 'unknown'}")
    if decoding_warnings:
        for record in items.values():
            record["discovery_warnings"] = ["Invalid UTF-8 symbols replaced in category metadata: " + ", ".join(decoding_warnings)]
    return sorted(items.values(), key=lambda r: r["source_id"])
