"""Small, explicitly reviewed source inventories; no website-wide crawling."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .http import SourceAccessError

ROOT = Path(__file__).resolve().parents[1]
HASH = re.compile(r"(?:sha1:[0-9a-f]{40}|sha256:[0-9a-f]{64})\Z")
PER_ITEM_LICENSES = {"CC0-1.0", "CC-BY-3.0", "CC-BY-4.0", "CC-BY-SA-3.0", "CC-BY-SA-4.0"}


def discover_curated(source, client):
    """Use checked-in exact items and content hashes; CI fetches their originals.

    A manifest change requires an explicit registry digest update. Author/license
    are item-level fields, never inherited from the host or a collection label.
    Thumbnail review is recorded separately from cloud original/render checks.
    """
    relative = source.get("manifest_path", "")
    path = (ROOT / relative).resolve()
    if not path.is_relative_to((ROOT / "data").resolve()) or path.suffix != ".json":
        raise ValueError("Curated manifest must be a JSON file inside project data")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != source.get("manifest_sha256"):
        raise SourceAccessError(relative, "curated manifest differs from its pinned SHA-256")
    manifest = json.loads(data)
    if manifest.get("schema_version") != 1 or manifest.get("version") != source.get("manifest_version"):
        raise ValueError("Curated manifest schema/version mismatch")
    selected = [item for item in manifest.get("items", []) if item.get("source") == source["source_id"]]
    if not selected or len(selected) != source.get("expected_item_count"):
        raise ValueError("Curated source inventory count mismatch")
    records, seen = [], set()
    for item in selected:
        sid = item.get("source_id")
        if not isinstance(sid, str) or not sid or sid in seen:
            raise ValueError("Curated source IDs must be nonempty and unique")
        seen.add(sid)
        if (item.get("license") not in PER_ITEM_LICENSES or not item.get("license_url")
                or not item.get("author") or not item.get("attribution") or not item.get("rights_evidence")):
            raise ValueError("Curated item lacks independent rights/creator evidence")
        if (not item.get("title") or urlsplit(item.get("source_url", "")).scheme != "https"
                or not item.get("upstream_revision") or not item.get("files")):
            raise ValueError("Curated item lacks source identity/content version")
        for file in item["files"]:
            location = urlsplit(file.get("url", ""))
            size = file.get("upstream_size")
            if (location.scheme != "https" or location.netloc not in source.get("download_hosts", [])
                    or location.username or location.password or location.fragment
                    or not HASH.fullmatch(file.get("upstream_hash", ""))
                    or not isinstance(size, int) or isinstance(size, bool) or size <= 0
                    or file.get("format") not in source.get("extensions", [])):
                raise ValueError("Curated original lacks allowed URL/format and pinned content hash/size")
        record = copy.deepcopy(item)
        record.setdefault("distribution_allowed", True)
        record.setdefault("rights_status", "item_license_recorded")
        record.update(family_hint=f"{source['source_id']}:{sid}",
                      curated_manifest={"path": relative, "sha256": digest, "version": manifest["version"]})
        records.append(record)
    return records
