"""Cloud-only clean, content-addressed recovery bundles from an explicit manifest.

Build and restore use the same streaming verifier. Originals are never changed.
The JSON bundle index binds the manifest and every tar.gz part; a receipt is the
last published asset. This is storage/restore evidence, not scientific acceptance.
Only the standard library is required. Core functions accept synthetic readers
for local tests; both network CLI commands require a GitHub Actions runner.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from urllib.parse import quote, unquote, urlsplit
from urllib.request import urlopen


CHUNK = 1024 * 1024
STATIC_FILE_LIMIT = 24 * 1024 * 1024  # Strictly less than this, not <=.
STATIC_COUNT_LIMIT = 18_000
PACK_BYTES = 512 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}\Z")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
FORMAT = re.compile(r"[a-z0-9]{1,12}\Z")


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def valid_sha(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def positive_size(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def safe_member(value):
    return (isinstance(value, str) and value and not any(c in value for c in "\\:\x00")
            and not any(part in {"", ".", ".."} for part in value.split("/")))


def file_proof(path):
    digest, size = hashlib.sha256(), 0
    with Path(path).open("rb") as stream:
        while block := stream.read(CHUNK):
            size += len(block)
            digest.update(block)
    return {"sha256": digest.hexdigest(), "size_bytes": size}


def release_url(repository, tag, name):
    return f"https://github.com/{repository}/releases/download/{quote(tag, safe='')}/{quote(name, safe='')}"


def release_location(url, repository):
    parsed = urlsplit(url)
    parts = parsed.path.lstrip("/").split("/")
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment
            or len(parts) != 6 or "/".join(parts[:2]) != repository or parts[2:4] != ["releases", "download"]):
        raise ValueError("Only explicit GitHub Release assets in the current repository are allowed")
    tag, name = unquote(parts[4]), unquote(parts[5])
    if not tag or not NAME.fullmatch(name) or any(c in tag for c in "\x00\r\n"):
        raise ValueError("Invalid Release asset location")
    return tag, name


def parse_manifest(data, expected_sha):
    if not valid_sha(expected_sha) or sha(data) != expected_sha:
        raise ValueError("Publication manifest SHA-256 mismatch")
    manifest = json.loads(data)
    if data != json_bytes(manifest):
        raise ValueError("Publication manifest must use the acceptance checker's canonical JSON bytes")
    if (manifest.get("schema_version") != 1 or manifest.get("acceptance_report_required") is not True
            or not isinstance(manifest.get("collection_release"), str) or not manifest["collection_release"]
            or not valid_sha(manifest.get("catalog_sha256")) or not manifest.get("files")):
        raise ValueError("Not a nonempty versioned publication manifest")
    return manifest


def plan_manifest(manifest):
    """Deduplicate bytes, while verifying every exact archive/member reference."""
    objects, archives, originals = {}, {}, set()
    for item in manifest["files"]:
        if item.get("sha256") in originals:
            raise ValueError("Duplicate original in publication manifest")
        originals.add(item.get("sha256"))
        for role, value in (("original", item), ("preview", item.get("preview") or {})):
            digest, size, fmt = value.get("sha256"), value.get("size_bytes"), value.get("format")
            location = value.get("archive") or {}
            url, member = location.get("url"), location.get("member")
            # Acceptance's preview contract permits no explicit format. The
            # renderer stores PNG previews; retain its exact .png member suffix.
            if fmt is None and role == "preview" and isinstance(member, str) and member.endswith(".png"):
                fmt = "png"
            if (not valid_sha(digest) or not positive_size(size) or not isinstance(fmt, str)
                    or not FORMAT.fullmatch(fmt) or not isinstance(url, str) or not safe_member(member)):
                raise ValueError("Manifest artwork lacks valid hash/size/format/archive/member")
            obj = objects.setdefault(digest, {"sha256": digest, "size_bytes": size, "format": fmt, "roles": []})
            if obj["size_bytes"] != size or obj["format"] != fmt:
                raise ValueError("Conflicting size or format for identical artwork hash")
            if role not in obj["roles"]:
                obj["roles"].append(role)
            wanted = archives.setdefault(url, {})
            if member in wanted and wanted[member]["sha256"] != digest:
                raise ValueError("Conflicting hashes for one source archive member")
            wanted[member] = obj
    for obj in objects.values():
        obj["roles"].sort()
        obj["path"] = f"objects/{obj['sha256']}.{obj['format']}"
    return objects, archives


class HashingReader:
    """Hash the entire compressed source, including bytes after the tar EOF."""
    def __init__(self, stream, expected):
        self.stream, self.expected = stream, expected
        self.digest, self.size = hashlib.sha256(), 0

    def read(self, size=-1):
        block = self.stream.read(size)
        self.size += len(block)
        if self.size > self.expected["size_bytes"]:
            raise ValueError("Archive exceeds its bound compressed size")
        self.digest.update(block)
        return block

    def finish(self):
        while self.read(CHUNK):
            pass
        actual = {"sha256": self.digest.hexdigest(), "size_bytes": self.size}
        if actual != self.expected:
            raise ValueError("Full archive SHA-256/size mismatch")
        return actual


def extract_selected(opener, url, proof, wanted, staging, *, exact=False):
    """One compressed stream read; never extract unlisted files or links.

    A selected source member is staged under its content hash, never its upstream
    filename. All selected references are verified even if their bytes duplicate
    another source. No staged result is trusted until the whole archive is read.
    """
    if not valid_sha(proof.get("sha256")) or not positive_size(proof.get("size_bytes")):
        raise ValueError("Archive requires trusted SHA-256 and size before streaming")
    seen = set()
    with closing(opener(url)) as incoming:
        reader = HashingReader(incoming, proof)
        # Explicit gzip wrapper checks gzip CRC/end-of-stream as well as tar.
        with gzip.GzipFile(fileobj=reader, mode="rb") as inflated:
            with tarfile.open(fileobj=inflated, mode="r|", ignore_zeros=exact) as archive:
                for member in archive:
                    if member.name not in wanted:
                        if exact:
                            raise ValueError(f"Unlisted member in clean pack: {member.name}")
                        continue
                    if member.name in seen or not member.isfile() or not safe_member(member.name):
                        raise ValueError("Duplicate or unsafe selected archive member")
                    obj = wanted[member.name]
                    if member.size != obj["size_bytes"]:
                        raise ValueError("Selected member size mismatch")
                    seen.add(member.name)
                    destination = staging / obj["sha256"]
                    # Existing bytes have been checked in an earlier archive;
                    # still read and hash this exact additional reference.
                    sink = None if destination.exists() else destination.open("xb")
                    digest, count = hashlib.sha256(), 0
                    try:
                        with closing(archive.extractfile(member)) as content:
                            while block := content.read(CHUNK):
                                count += len(block)
                                digest.update(block)
                                if sink is not None:
                                    sink.write(block)
                    finally:
                        if sink is not None:
                            sink.close()
                    if count != obj["size_bytes"] or digest.hexdigest() != obj["sha256"]:
                        raise ValueError("Selected member SHA-256/size mismatch")
            # Drain the uncompressed tail; this validates the complete gzip.
            # Clean tar padding is zero. Nonzero trailing payload is forbidden
            # for our clean outputs (legacy input tar may carry other padding).
            while block := inflated.read(CHUNK):
                if exact and block.strip(b"\x00"):
                    raise ValueError("Unexpected trailing payload in clean tar")
        verified = reader.finish()
    if seen != set(wanted):
        raise ValueError("Selected members missing from archive")
    return {"url": url, **verified, "selected_member_count": len(seen), "complete_stream_verified": True}


def partition(objects, *, file_limit=STATIC_FILE_LIMIT, count_limit=STATIC_COUNT_LIMIT, pack_bytes=PACK_BYTES):
    if not (0 < file_limit <= STATIC_FILE_LIMIT and 0 < count_limit <= STATIC_COUNT_LIMIT and pack_bytes > 0):
        raise ValueError("Invalid publication pack limits")
    output, current, size = [], [], 0
    for obj in sorted(objects.values(), key=lambda value: value["sha256"]):
        if obj["size_bytes"] >= file_limit:
            output.append(("download", [obj]))
            continue
        if current and (len(current) >= count_limit or size + obj["size_bytes"] > pack_bytes):
            output.append(("static", current))
            current, size = [], 0
        current.append(obj)
        size += obj["size_bytes"]
    if current:
        output.append(("static", current))
    return output


def write_pack(path, objects, staging):
    with path.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
                for obj in objects:
                    member = tarfile.TarInfo(obj["path"])
                    member.size, member.mode, member.mtime = obj["size_bytes"], 0o644, 0
                    with (staging / obj["sha256"]).open("rb") as content:
                        archive.addfile(member, content)


def verify_pack(path, objects):
    proof = file_proof(path)
    # Use a fresh empty destination: verification reads every member, with no
    # shortcut based on the staged source copies used to create the tar.
    with tempfile.TemporaryDirectory(prefix="mga-restore-check-") as folder:
        evidence = extract_selected(lambda _: path.open("rb"), str(path), proof,
                                    {obj["path"]: obj for obj in objects}, Path(folder), exact=True)
    return {key: evidence[key] for key in ("sha256", "size_bytes")}


def restore_evidence(index, index_sha, bundle_url, *, checked_by, checked_at=None):
    members = sorted({obj["sha256"] for part in index["packs"] for obj in part["members"]})
    return {"schema_version": 1, "publish_manifest_sha256": index["publish_manifest"]["sha256"],
            "passed": True, "published_subset_only": True, "checked_by": checked_by,
            "checked_at": checked_at or datetime.now(timezone.utc).isoformat(),
            "bundle_url": bundle_url, "bundle_sha256": index_sha, "bundle_type": "multipart-index",
            "artwork_member_sha256": members, "artwork_member_count": len(members),
            "pack_count": len(index["packs"]), "exact_member_sets_verified": True,
            "all_member_sha256_verified": True, "all_compressed_sha256_and_sizes_verified": True,
            "acceptance_scope": "Storage recovery only; scientific and Illustrator acceptance are separate."}


def build_bundle(manifest_data, expected_sha, *, repository, version, archive_proofs, opener, output,
                 file_limit=STATIC_FILE_LIMIT, count_limit=STATIC_COUNT_LIMIT, pack_bytes=PACK_BYTES):
    """Build from one explicit manifest. Tests supply in-memory synthetic streams."""
    if not VERSION.fullmatch(version):
        raise ValueError("Invalid publication version")
    manifest = parse_manifest(manifest_data, expected_sha)
    objects, archives = plan_manifest(manifest)
    for url in archives:
        release_location(url, repository)
    if set(archive_proofs) != set(archives):
        raise ValueError("Source archive proof set differs from manifest references")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Build output must be empty")
    tag = f"publish-{version}"
    index = {"schema_version": 1, "bundle_type": "multipart-index", "version": version,
             "repository": repository, "release_tag": tag, "collection_release": manifest["collection_release"],
             "published_subset_only": True, "artwork_object_count": len(objects),
             "static_limits": {"max_files_per_pack": count_limit, "file_size_strictly_less_than": file_limit},
             "large_files_retained_in_download_packs": True, "source_archives": [], "packs": [],
             "recovery": {"website_required": False, "original_bytes_unchanged": True,
                          "tool": "scripts/build_publish_pack.py restore --bundle-url URL --bundle-sha256 SHA256",
                          "tool_revision": "clean-publish-pack-v1", "artwork_layout": "objects/<sha256>.<format>",
                          "rights_and_source_credit": "Full selected provenance is retained in publish_manifest.json.",
                          "instructions": ["Obtain this index URL and SHA-256 from the final receipt.",
                              "Verify the index and the referenced publication manifest.",
                              "Read each referenced pack; verify its complete compressed SHA-256 and size.",
                              "Restore only the listed regular members and verify their exact set, sizes and SHA-256.",
                              "Keep the manifest with the restored objects to preserve each artwork's rights and credit."]}}
    manifest_name = f"{version}.publish_manifest.json"
    (output / manifest_name).write_bytes(manifest_data)
    index["publish_manifest"] = {"name": manifest_name, "url": release_url(repository, tag, manifest_name),
                                 "sha256": expected_sha, "size_bytes": len(manifest_data)}
    with tempfile.TemporaryDirectory(prefix="mga-publish-staging-") as folder:
        staging = Path(folder)
        for url in sorted(archives):
            proof = extract_selected(opener, url, archive_proofs[url], archives[url], staging)
            index["source_archives"].append(proof)
        for number, (kind, members) in enumerate(partition(objects, file_limit=file_limit,
                                                          count_limit=count_limit, pack_bytes=pack_bytes)):
            name = f"{version}.{kind}-{number:04d}.tar.gz"
            path = output / name
            write_pack(path, members, staging)
            proof = verify_pack(path, members)
            index["packs"].append({"name": name, "url": release_url(repository, tag, name), "kind": kind,
                                   **proof, "members": members, "member_count": len(members),
                                   "uncompressed_bytes": sum(item["size_bytes"] for item in members)})
            # Each content-addressed object occurs in exactly one output pack.
            for item in members:
                (staging / item["sha256"]).unlink()
    validate_index(index, manifest, repository)
    index_name = f"{version}.bundle.json"
    (output / index_name).write_bytes(json_bytes(index))
    return index, output / index_name


def validate_index(index, manifest, repository):
    """Require exact manifest bytes, roles, paths, and one placement per hash."""
    objects, _ = plan_manifest(manifest)
    if (index.get("schema_version") != 1 or index.get("bundle_type") != "multipart-index"
            or index.get("repository") != repository or index.get("published_subset_only") is not True
            or not isinstance(index.get("version"), str) or not VERSION.fullmatch(index["version"])
            or index.get("release_tag") != f"publish-{index.get('version')}"
            or index.get("collection_release") != manifest["collection_release"]
            or index.get("publish_manifest", {}).get("sha256") != sha(json_bytes(manifest))):
        raise ValueError("Bundle index/manifest binding mismatch")
    reference = index["publish_manifest"]
    if (reference.get("name") != f"{index['version']}.publish_manifest.json"
            or reference.get("size_bytes") != len(json_bytes(manifest))
            or release_location(reference.get("url", ""), repository) != (index["release_tag"], reference["name"])):
        raise ValueError("Publication manifest location/size mismatch")
    limits = index.get("static_limits") or {}
    if not (isinstance(limits.get("max_files_per_pack"), int) and 0 < limits["max_files_per_pack"] <= STATIC_COUNT_LIMIT
            and isinstance(limits.get("file_size_strictly_less_than"), int)
            and 0 < limits["file_size_strictly_less_than"] <= STATIC_FILE_LIMIT):
        raise ValueError("Bundle static limits are invalid")
    found, names = {}, set()
    for part in index.get("packs", []):
        if (part.get("kind") not in {"static", "download"} or not NAME.fullmatch(part.get("name", ""))
                or part["name"] in names or not valid_sha(part.get("sha256")) or not positive_size(part.get("size_bytes"))):
            raise ValueError("Invalid or duplicate clean pack")
        tag, name = release_location(part["url"], repository)
        if tag != index["release_tag"] or name != part["name"]:
            raise ValueError("Clean pack URL does not match index")
        names.add(name)
        members = part.get("members") or []
        if part.get("member_count") != len(members) or not members:
            raise ValueError("Clean pack member count mismatch")
        if part["kind"] == "static" and len(members) > limits["max_files_per_pack"]:
            raise ValueError("Static pack file count exceeded")
        for obj in members:
            digest = obj.get("sha256")
            if digest in found or objects.get(digest) != obj:
                raise ValueError("Bundle does not contain the exact selected artwork objects")
            if part["kind"] == "static" and obj["size_bytes"] >= limits["file_size_strictly_less_than"]:
                raise ValueError("Large file incorrectly placed in static pack")
            found[digest] = obj
        if part.get("uncompressed_bytes") != sum(obj["size_bytes"] for obj in members):
            raise ValueError("Clean pack byte count mismatch")
    if found != objects or index.get("artwork_object_count") != len(objects):
        raise ValueError("Bundle exact artwork hash set mismatch")


class GitHubStore:
    """Public media stream; authentication stays inside gh's GitHub API calls."""
    def __init__(self, repository):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid GitHub repository")
        self.repository, self.cache = repository, {}

    def gh(self, *args):
        result = subprocess.run(["gh", *args], text=True, encoding="utf-8", capture_output=True, timeout=1800)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        return result.stdout

    def assets(self, tag, *, refresh=False):
        if refresh or tag not in self.cache:
            release = json.loads(self.gh("api", f"repos/{self.repository}/releases/tags/{quote(tag, safe='')}"))
            pages = json.loads(self.gh("api", "--paginate", "--slurp",
                                       f"repos/{self.repository}/releases/{release['id']}/assets?per_page=100"))
            rows = [row for page in pages for row in page]
            if len({row["name"] for row in rows}) != len(rows):
                raise ValueError("Duplicate GitHub Release asset names")
            self.cache[tag] = {row["name"]: row for row in rows}
        return self.cache[tag]

    def proof(self, url):
        tag, name = release_location(url, self.repository)
        row = self.assets(tag).get(name)
        if row is None or release_location(row["browser_download_url"], self.repository) != (tag, name):
            raise ValueError("Referenced asset missing from GitHub Release")
        value = row.get("digest") or ""
        digest = value.removeprefix("sha256:") if value.startswith("sha256:") else None
        if not valid_sha(digest) or not positive_size(row.get("size")):
            raise ValueError("GitHub asset lacks verifiable SHA-256/size")
        return {"sha256": digest, "size_bytes": row["size"]}

    def open(self, url):
        release_location(url, self.repository)
        # No token is forwarded to media/CDN hosts; no retry on access denial.
        return urlopen(url, timeout=120)

    def metadata(self, url, expected_sha, *, limit=128 * 1024 * 1024):
        proof = self.proof(url)
        if proof["sha256"] != expected_sha or proof["size_bytes"] > limit:
            raise ValueError("Metadata GitHub digest or size bound mismatch")
        with closing(self.open(url)) as incoming:
            reader = HashingReader(incoming, proof)
            blocks = []
            while block := reader.read(CHUNK):
                blocks.append(block)
            reader.finish()
        return b"".join(blocks)

    def ensure_release(self, tag):
        try:
            self.assets(tag)
            return
        except RuntimeError as exc:
            if "HTTP 404" not in str(exc):
                raise
        self.gh("release", "create", tag, "--repo", self.repository, "--target", os.environ["GITHUB_SHA"],
                "--title", f"MGA-resource recovery data: {tag}", "--prerelease",
                "--notes", "Clean manifest-selected artwork recovery data. Receipt is the final commit marker. "
                "Storage restore checks do not replace the independent release acceptance report. No website is deployed.")
        self.assets(tag, refresh=True)

    def upload_checked(self, tag, path):
        expected = file_proof(path)
        url = release_url(self.repository, tag, path.name)
        existing = self.assets(tag, refresh=True).get(path.name)
        if existing is None:
            self.gh("release", "upload", tag, str(path), "--repo", self.repository)
            self.assets(tag, refresh=True)
        if self.proof(url) != expected:
            raise ValueError("Existing/uploaded Release asset differs; immutable version will not be overwritten")
        return {"name": path.name, "url": url, **expected, "github_digest_verified": True}

    def preflight(self, tag, paths):
        """Catch a reused version's conflicting manifest/index before any upload."""
        existing = self.assets(tag, refresh=True)
        for path in paths:
            if path.name in existing and self.proof(release_url(self.repository, tag, path.name)) != file_proof(path):
                raise ValueError("Publication version already binds different bytes; choose a new explicit version")


def publish_bundle(index, index_path, output, store, *, run_id, checked_by):
    """All data and proof assets precede the final, remotely verified receipt."""
    if not NAME.fullmatch(run_id) or len(run_id) > 40:
        raise ValueError("Invalid run identity")
    if index_path.read_bytes() != json_bytes(index):
        raise ValueError("Bundle index changed after restore verification")
    for reference in [*index["packs"], index["publish_manifest"]]:
        if file_proof(output / reference["name"]) != {key: reference[key] for key in ("sha256", "size_bytes")}:
            raise ValueError("Bundle file changed after restore verification")
    tag = index["release_tag"]
    store.ensure_release(tag)
    paths = [output / name for name in [*(part["name"] for part in index["packs"]),
                                       index["publish_manifest"]["name"], index_path.name]]
    store.preflight(tag, paths)
    published = []
    for path in paths:
        published.append(store.upload_checked(tag, path))
    evidence = restore_evidence(index, file_proof(index_path)["sha256"],
                                release_url(index["repository"], tag, index_path.name), checked_by=checked_by)
    evidence["published_assets_remote_digests_verified"] = True
    prefix = f"{index['version']}-{run_id}"
    evidence_path = output / f"{prefix}.restore_evidence.json"
    evidence_path.write_bytes(json_bytes(evidence))
    published.append(store.upload_checked(tag, evidence_path))
    receipt = {"schema_version": 1, "status": "complete", "version": index["version"],
               "publish_manifest_sha256": index["publish_manifest"]["sha256"],
               "bundle_url": evidence["bundle_url"], "bundle_sha256": evidence["bundle_sha256"],
               "restore_evidence": published[-1], "published_assets": published,
               "publication_order": "Receipt uploaded last, after every listed asset's remote SHA-256/size matched",
               "checked_at": evidence["checked_at"], "run_id": run_id}
    receipt_path = output / f"{prefix}.receipt.json"
    receipt_path.write_bytes(json_bytes(receipt))
    return store.upload_checked(tag, receipt_path)


def restore_bundle(bundle_url, expected_sha, *, store, output, checked_by):
    """Independently restore the published data using only its index and hashes."""
    data = store.metadata(bundle_url, expected_sha)
    index = json.loads(data)
    if data != json_bytes(index):
        raise ValueError("Bundle index is not canonical JSON")
    manifest_ref = index["publish_manifest"]
    manifest_data = store.metadata(manifest_ref["url"], manifest_ref["sha256"])
    if len(manifest_data) != manifest_ref["size_bytes"]:
        raise ValueError("Restored manifest size mismatch")
    manifest = parse_manifest(manifest_data, manifest_ref["sha256"])
    validate_index(index, manifest, store.repository)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Restore output must be empty")
    with tempfile.TemporaryDirectory(prefix="mga-restore-") as folder:
        staging = Path(folder)
        for part in index["packs"]:
            proof = {key: part[key] for key in ("sha256", "size_bytes")}
            if store.proof(part["url"]) != proof:
                raise ValueError("Remote clean pack digest differs from bundle index")
            extract_selected(store.open, part["url"], proof, {obj["path"]: obj for obj in part["members"]}, staging, exact=True)
        (output / "objects").mkdir()
        for part in index["packs"]:
            for obj in part["members"]:
                shutil.move(str(staging / obj["sha256"]), str(output / obj["path"]))
    (output / "publish_manifest.json").write_bytes(manifest_data)
    (output / "bundle.json").write_bytes(data)
    evidence = restore_evidence(index, expected_sha, bundle_url, checked_by=checked_by)
    (output / "restore_evidence.json").write_bytes(json_bytes(evidence))
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--manifest-url", required=True)
    build.add_argument("--manifest-sha256", required=True)
    build.add_argument("--version", required=True)
    build.add_argument("--publish", action="store_true")
    build.add_argument("--output", type=Path, default=Path("build/publish-pack"))
    restore = commands.add_parser("restore")
    restore.add_argument("--bundle-url", required=True)
    restore.add_argument("--bundle-sha256", required=True)
    restore.add_argument("--output", type=Path, default=Path("build/restored-publication"))
    args = parser.parse_args(argv)
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Bulk build/restore is cloud-only. Local work is limited to synthetic unit tests.")
    store = GitHubStore(os.environ.get("GITHUB_REPOSITORY", ""))
    identity = {"kind": "agent", "id": "MGA-resource/build_publish_pack.py@" + os.environ.get("GITHUB_SHA", "unknown")}
    if args.command == "restore":
        evidence = restore_bundle(args.bundle_url, args.bundle_sha256, store=store, output=args.output, checked_by=identity)
        print(json.dumps({"restored": evidence["artwork_member_count"], "output": str(args.output)}))
        return 0
    _, manifest_name = release_location(args.manifest_url, store.repository)
    if not manifest_name.endswith("publish_manifest.json"):
        parser.error("Input must be an explicit versioned publish_manifest.json Release asset")
    manifest_data = store.metadata(args.manifest_url, args.manifest_sha256)
    manifest = parse_manifest(manifest_data, args.manifest_sha256)
    _, archives = plan_manifest(manifest)
    proofs = {url: store.proof(url) for url in archives}
    print(json.dumps({"phase": "verified-inputs", "selected_originals": len(manifest["files"]),
                      "source_archives": len(proofs), "compressed_source_bytes": sum(value["size_bytes"] for value in proofs.values())}), flush=True)
    index, index_path = build_bundle(manifest_data, args.manifest_sha256, repository=store.repository,
                                    version=args.version, archive_proofs=proofs, opener=store.open, output=args.output)
    if args.publish:
        receipt = publish_bundle(index, index_path, args.output, store,
                                 run_id=os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"], checked_by=identity)
        print(json.dumps({"receipt": receipt, "artwork_object_count": index["artwork_object_count"]}))
    else:
        print(json.dumps({"published": False, "verified_objects": index["artwork_object_count"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
