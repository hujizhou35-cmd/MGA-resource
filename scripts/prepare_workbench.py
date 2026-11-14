"""Prepare the new website from the immutable, accepted website export in CI."""
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from runpy import run_path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build/website"
PUBLIC = OUT / "public"
CATALOG = "f3f869f4ed24e4582ff26c46fabb64a1e68593d2a731eff66e728ec6b280f377"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def catalog_hash():
    h = hashlib.sha256()
    for p in sorted((PUBLIC / "catalog").glob("*.json")):
        h.update(p.name.encode() + b"\0" + bytes.fromhex(sha(p)))
    return h.hexdigest()

def main():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Production navigation is built in cloud CI.")
    from prepare_website import cloud_asset, TAG
    report = json.loads((OUT / "prepare-receipt.json").read_text(encoding="utf-8"))
    assert str(report["run_id"]) == "43485204419"
    assert report["accepted_bindings"]["catalog_sha256"] == CATALOG
    assert sha(ROOT / "mga_resources/search-core.mjs") == report["accepted_bindings"]["search_core_sha256"]
    assert sha(OUT / "search.sql") == report["sql_sha256"]
    assert sha(PUBLIC / "acceptance.json") == report["formal_report_sha256"]
    before = catalog_hash()
    manifest = cloud_asset("science-update-43479028418.publish_manifest.json", TAG,
                           report["accepted_bindings"]["publish_manifest_sha256"], OUT / "input")
    originals = {f["sha256"]: f for f in json.loads(manifest.read_text(encoding="utf-8"))["files"]}
    with sqlite3.connect(OUT / "derived-metadata.sqlite") as db:
        db.executescript((OUT / "search.sql").read_text(encoding="utf-8"))
        rows = [(family, json.loads(value)) for family, value in db.execute("SELECT family_id,values_json FROM file_variants WHERE eligible=1")]
    make = run_path(str(ROOT / "site/lib/catalog_info.py"))["build_catalog_info"]
    info = make(CATALOG, originals, rows, report["family_metadata_files"])
    count = sum(1 for _ in (PUBLIC / "catalog").glob("*.json"))
    assert count == info["families"] == 7838 and info["originals"] == 19473
    shutil.copytree(ROOT / "site/public", PUBLIC, dirs_exist_ok=True)
    (PUBLIC / "catalog-info.json").write_text(json.dumps(info, ensure_ascii=False, separators=(",", ":"))+"\n", encoding="utf-8")
    assert catalog_hash() == before
    receipt = {"catalog_sha256": CATALOG, "catalog_files_sha256": before, "family_metadata_files": count,
               "publish_manifest_sha256": sha(manifest), "delivery_objects_sha256": sha(OUT / "delivery-objects.json"),
               "catalog_info_sha256": sha(PUBLIC / "catalog-info.json"), "downloadable_originals": len(originals),
               "artwork_reads": 0, "database_imports": 0, "source_export_run": report["run_id"]}
    (OUT / "workbench-receipt.json").write_text(json.dumps(receipt, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(receipt))

if __name__ == "__main__":
    main()
