"""Resolve review metadata and immutable archive evidence across Releases.

Moving a catalog or its review output must never rewrite artwork URLs. Archive
assets are keyed by their full URL because different Releases can reuse names.
"""
from __future__ import annotations


def add_release_options(parser):
    parser.add_argument("--publish-tag", help="Output evidence Release; defaults to the metadata Release")
    parser.add_argument("--archive-release-tag", action="append", default=[],
                        help="Additional original/preview archive Release; repeat for multiple sources")


def load_review_inputs(repository, metadata_tag, archive_tags=(), *, list_assets=None):
    if list_assets is None:
        from assemble import list_release_assets
        list_assets = list_release_assets
    metadata = list_assets(repository, metadata_tag)
    tags = list(dict.fromkeys([metadata_tag, *archive_tags]))
    manifest = {}
    for tag in tags:
        assets = metadata if tag == metadata_tag else list_assets(repository, tag)
        for name, asset in assets.items():
            if not name.endswith(".tar.gz"):
                continue
            url = asset["browser_download_url"]
            previous = manifest.get(url)
            if previous is not None and (previous.get("digest"), previous.get("size")) != (asset.get("digest"), asset.get("size")):
                raise ValueError("Conflicting archive evidence for the same immutable URL")
            manifest[url] = asset
    return metadata, manifest, tags


def release_bindings(args, archive_tags):
    return {"metadata_release_tag": args.release_tag,
            "archive_release_tags": archive_tags,
            "publish_tag": args.publish_tag or args.release_tag}
