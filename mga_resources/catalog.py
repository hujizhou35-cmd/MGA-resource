"""Normalize, deduplicate and index the cloud artwork catalog.

Text classification is a retrieval aid, not a judgment of scientific or visual
quality. Exact duplicates are merged while every provenance record is retained.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TAXONOMY = {
    "animals": {"label_zh": "动物", "terms": ["animal", "mouse", "mice", "rat", "rabbit", "zebrafish", "drosophila", "monkey", "macaque", "pig", "fish", "xenopus", "organism", "小鼠", "大鼠", "动物", "斑马鱼", "果蝇"]},
    "people": {"label_zh": "人体与临床人物", "terms": ["person", "people", "patient", "doctor", "nurse", "scientist", "human body", "human male", "human female", "woman", "man", "child", "elderly", "pregnant", "人体", "患者", "医生", "护士", "老人"]},
    "organs": {"label_zh": "器官", "terms": ["organ", "liver", "heart", "brain", "kidney", "lung", "intestine", "stomach", "pancreas", "spleen", "thyroid", "eye", "uterus", "prostate", "bladder", "skin", "肝", "心脏", "脑", "肾", "肺", "肠", "胃", "器官"]},
    "tissues_vessels": {"label_zh": "组织与血管", "terms": ["tissue", "vessel", "artery", "arteries", "vein", "aorta", "capillary", "vascular", "cartilage", "bone", "muscle", "epithelium", "endothelium", "组织", "血管", "动脉", "静脉", "骨", "肌肉", "软骨"]},
    "cells": {"label_zh": "细胞", "terms": ["cell", "cells", "lymphocyte", "macrophage", "neuron", "hepatocyte", "platelet", "erythrocyte", "neutrophil", "fibroblast", "organoid", "stem cell", "细胞", "巨噬", "神经元", "血小板", "类器官"]},
    "organelles": {"label_zh": "细胞器", "terms": ["organelle", "mitochondria", "mitochondrion", "nucleus", "nuclei", "golgi", "lysosome", "ribosome", "endoplasmic", "peroxisome", "vesicle", "细胞器", "线粒体", "细胞核", "高尔基", "溶酶体", "核糖体", "内质网"]},
    "molecules": {"label_zh": "分子", "terms": ["molecule", "dna", "rna", "protein", "antibody", "receptor", "ligand", "enzyme", "lipid", "glucose", "chromosome", "plasmid", "nucleic", "peptide", "分子", "蛋白", "抗体", "受体", "染色体", "质粒"]},
    "microbes": {"label_zh": "微生物", "terms": ["microbe", "bacteria", "bacterium", "virus", "viral", "yeast", "fungus", "fungi", "bacteriophage", "microbiome", "microbiology", "微生物", "细菌", "病毒", "酵母", "真菌", "噬菌体"]},
    "lab_equipment": {"label_zh": "实验器材", "terms": ["apparatus", "equipment", "pipette", "microscope", "syringe", "centrifuge", "tube", "petri", "culture dish", "flask", "beaker", "well plate", "bioreactor", "incubator", "spectrometer", "试管", "移液", "显微镜", "注射器", "培养皿", "离心机", "仪器"]},
    "experimental_actions": {"label_zh": "实验操作", "terms": ["injection", "inject", "dissection", "transfection", "transduction", "cell culture", "culturing", "pipetting", "centrifugation", "blood sampling", "sequencing", "western blot", "pcr", "实验操作", "注射", "解剖", "转染", "细胞培养", "采血", "测序"]},
    "clinical_workflow": {"label_zh": "临床流程", "terms": ["hospital", "clinic", "cohort", "clinical trial", "screening", "diagnosis", "treatment", "follow up", "randomization", "randomisation", "medical record", "questionnaire", "healthcare", "hospitalization", "队列", "临床", "诊断", "治疗", "筛查", "随访", "病历", "问卷"]},
    "omics_data": {"label_zh": "组学与数据流程", "terms": ["omics", "genomics", "transcriptomics", "proteomics", "bioinformatics", "database", "dataset", "computer", "analysis", "heatmap", "volcano", "umap", "network", "machine learning", "statistics", "组学", "数据库", "生信", "分析", "数据", "热图", "机器学习"]},
}

# Query expansion contains vocabulary, not inferred scientific facts. English
# aliases are also inserted into the index for reliable Chinese lookup with
# SQLite's standard tokenizer (which is not a Chinese word segmenter).
CONCEPTS = [
    ("小白鼠", "小鼠", "实验鼠", "mouse", "mice", "mus musculus"),
    ("大鼠", "rat", "rats", "rattus"),
    ("斑马鱼", "zebrafish", "danio rerio"), ("果蝇", "drosophila", "fruit fly"),
    ("兔", "rabbit"), ("猴", "monkey", "macaque"),
    ("患者", "病人", "patient"), ("医生", "doctor", "physician"),
    ("护士", "nurse"), ("老人", "老年人", "elderly", "older adult"),
    ("人体", "human body"), ("肝脏", "肝", "liver", "hepatic"),
    ("心脏", "heart", "cardiac"), ("肾脏", "肾", "kidney", "renal"),
    ("肺部", "肺", "lung", "pulmonary"), ("大脑", "脑", "brain", "cerebral"),
    ("肠道", "肠", "intestine", "intestinal", "gut"),
    ("胰腺", "pancreas", "pancreatic"), ("皮肤", "skin"),
    ("血管", "blood vessel", "vessel", "vascular"), ("动脉", "artery", "arteries"),
    ("主动脉", "aorta"), ("静脉", "vein"), ("软骨", "cartilage"),
    ("骨骼", "骨", "bone", "skeleton"), ("肌肉", "muscle"),
    ("细胞", "cell", "cells"), ("细胞器", "organelle", "organelles"),
    ("巨噬细胞", "巨噬", "macrophage"), ("神经元", "neuron"),
    ("肝细胞", "hepatocyte"), ("红细胞", "erythrocyte", "red blood cell"),
    ("血小板", "platelet"), ("淋巴细胞", "lymphocyte"),
    ("类器官", "organoid"), ("干细胞", "stem cell"),
    ("线粒体", "mitochondria", "mitochondrion"), ("细胞核", "nucleus", "nuclei"),
    ("内质网", "endoplasmic reticulum"), ("高尔基体", "golgi"),
    ("溶酶体", "lysosome"), ("核糖体", "ribosome"),
    ("蛋白质", "蛋白", "protein"), ("抗体", "antibody", "antibodies"),
    ("受体", "receptor"), ("染色体", "chromosome"),
    ("质粒", "plasmid"), ("脱氧核糖核酸", "dna"), ("核糖核酸", "rna"),
    ("细菌", "bacteria", "bacterium"), ("病毒", "virus", "viral"),
    ("真菌", "fungus", "fungi"), ("酵母", "yeast"), ("噬菌体", "bacteriophage", "phage"),
    ("显微镜", "microscope"), ("移液器", "pipette"),
    ("试管", "test tube"), ("培养皿", "petri dish", "culture dish"),
    ("注射器", "syringe"), ("离心机", "centrifuge"),
    ("培养箱", "incubator"), ("生物反应器", "bioreactor"),
    ("注射", "injection", "inject"), ("采血", "blood sampling", "blood collection"),
    ("细胞培养", "cell culture"), ("转染", "transfection"),
    ("测序", "sequencing"), ("队列", "cohort"),
    ("随访", "follow up", "follow-up"), ("问卷", "questionnaire"),
    ("筛查", "screening"), ("治疗", "treatment", "therapy"),
    ("数据库", "database"), ("组学", "omics"),
    ("热图", "heatmap", "heat map"), ("网络", "network"),
    ("机器学习", "machine learning"), ("临床", "clinical"),
]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(_text(x) for x in value)
    if isinstance(value, dict):
        return " ".join(_text(x) for x in value.values())
    return str(value)


def _matches(text: str, term: str) -> bool:
    if re.search(r"[\u3400-\u9fff]", term):
        return term in text
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None


def stable_id(source: str, source_id: str) -> str:
    prefix = re.sub(r"[^a-z0-9]+", "-", str(source).lower()).strip("-") or "source"
    digest = hashlib.sha256(f"{source}\0{source_id}".encode()).hexdigest()[:20]
    return f"{prefix[:36]}-{digest}"


def normalize_license(value: Any) -> str | None:
    if not value:
        return None
    raw = re.sub(r"[\s_]+", "-", str(value).strip()).upper()
    aliases = {
        "CC-0": "CC0-1.0", "CC0": "CC0-1.0", "CC-ZERO": "CC0-1.0",
        "PUBLIC-DOMAIN": "Public-Domain", "PUBLICDOMAIN": "Public-Domain", "PD": "Public-Domain",
        "MIT-LICENSE": "MIT", "APACHE-2": "Apache-2.0", "APACHE-2.0": "Apache-2.0",
    }
    return aliases.get(raw, raw)


def redistribution_status(record: dict[str, Any]) -> str:
    if record.get("distribution_allowed") is False:
        return "needs_rights_review"
    rights_status = _text(record.get("rights_status")).lower()
    if rights_status in {"pending", "unknown", "unverified", "needs_review", "needs_rights_review", "denied", "prohibited", "hold"}:
        return "needs_rights_review"
    explicit = _text(record.get("redistribution") or record.get("distribution_status")).lower()
    if explicit in {"denied", "prohibited", "reference_only", "needs_rights_review", "pending", "hold"}:
        return "needs_rights_review"
    license_id = normalize_license(record.get("license"))
    if license_id in {"CC0-1.0", "Public-Domain", "MIT", "BSD-2-CLAUSE", "BSD-3-CLAUSE", "ISC", "Apache-2.0"}:
        return "license_recorded"
    if license_id and re.fullmatch(r"CC-BY(?:-SA)?-(?:1\.0|2\.0|2\.5|3\.0|4\.0)", license_id):
        return "license_recorded" if record.get("author") or record.get("attribution") else "needs_attribution"
    return "needs_rights_review"


def _public_file(file_record: dict[str, Any]) -> dict[str, Any]:
    clean = copy.deepcopy(file_record)
    for key in ("local_path", "download_path", "runner_path", "absolute_path"):
        clean.pop(key, None)
    # Do not publish local directory values supplied by an earlier script.
    for key in ("path", "storage_path"):
        path = clean.get(key)
        if path and (Path(str(path)).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", str(path))):
            clean.pop(key)
    preview = clean.get("preview")
    if isinstance(preview, dict):
        for key in ("local_path", "runner_path", "absolute_path"):
            preview.pop(key, None)
        if preview.get("path") and (Path(str(preview["path"])).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", str(preview["path"]))):
            preview["path"] = re.split(r"[\\/]", str(preview["path"]))[-1]
    return clean


def normalize_record(record: dict[str, Any], processed_files: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    item = copy.deepcopy(record)
    source, source_id = _text(item.get("source")), _text(item.get("source_id"))
    if not source or not source_id:
        raise ValueError("Every asset requires source and source_id")
    item["id"] = stable_id(source, source_id)
    item["license_original"] = item.get("license_original", item.get("license"))
    item["license"] = normalize_license(item.get("license"))
    item["categories"] = list(dict.fromkeys(item.get("categories") or []))
    item["tags"] = list(dict.fromkeys(item.get("tags") or []))
    item["files"] = [_public_file(x) for x in (processed_files if processed_files is not None else item.get("files", []))]
    for key in ("local_path", "runner_path", "download_path"):
        item.pop(key, None)
    descriptive = " ".join(_text(item.get(key)) for key in ("title", "title_zh", "description", "categories", "tags", "species", "anatomy", "action")).lower()
    descriptive = unicodedata.normalize("NFKC", descriptive).replace("_", " ")
    existing = [key for key in (item.get("taxonomy") or []) if key in TAXONOMY]
    evidence = {}
    for key, definition in TAXONOMY.items():
        matches = [term for term in definition["terms"] if _matches(descriptive, term)]
        if matches:
            evidence[key] = matches
    item["taxonomy"] = list(dict.fromkeys(existing + list(evidence)))
    item["taxonomy_evidence"] = evidence
    item["classification_method"] = "source_metadata_and_keyword_rules; not_visual_review"
    aliases = []
    for concept in CONCEPTS:
        if any(_matches(descriptive, term) for term in concept):
            aliases.extend(concept)
    item["search_terms"] = list(dict.fromkeys([*item.get("search_terms", []), *aliases]))
    item["distribution_status"] = redistribution_status(item)
    statuses = [x.get("validation", {}).get("status", "not_checked") for x in item["files"]]
    passed = {"machine_checked", "machine_checked_and_rendered"}
    item["validation"] = {
        "status": "machine_checked" if statuses and all(x in passed for x in statuses) else "incomplete",
        "scientific_review": item.get("validation", {}).get("scientific_review", "not_reviewed"),
        "visual_review": item.get("validation", {}).get("visual_review", "not_reviewed"),
        "illustrator_editability": item.get("validation", {}).get("illustrator_editability", "not_tested"),
    }
    item["structures"] = sorted({x.get("structure", "unknown") for x in item["files"]})
    item["formats"] = sorted({x.get("format", "unknown") for x in item["files"]})
    item["family_id"] = item.get("family_id") or stable_id(source, _text(item.get("family_hint") or item.get("variant_of") or source_id))
    return item


def _provenance(item: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "source", "source_id", "title", "source_url", "author", "license", "license_original", "license_url", "attribution", "upstream_revision", "distribution_status")
    return {key: item.get(key) for key in keys}


def deduplicate(records: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Merge exact original-file duplicates and repeated source identities.

    No perceptual similarity is treated as proof of equivalence. Multiple
    original formats survive; each original's own provenance is retained.
    """
    items = [normalize_record(item) for item in records]
    parent = list(range(len(items)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a: int, b: int) -> None:
        parent[find(b)] = find(a)

    identities, families, hashes = {}, {}, {}
    for index, item in enumerate(items):
        if item["id"] in identities:
            union(index, identities[item["id"]])
        identities[item["id"]] = index
        if item["family_id"] in families:
            union(index, families[item["family_id"]])
        families[item["family_id"]] = index
        for file in item["files"]:
            digest = file.get("sha256")
            if digest:
                if digest in hashes:
                    union(index, hashes[digest])
                hashes[digest] = index
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, item in enumerate(items):
        groups[find(index)].append(item)
    result = []
    for group in groups.values():
        group.sort(key=lambda x: (x["distribution_status"] != "license_recorded", x["id"]))
        merged = copy.deepcopy(group[0])
        merged["id"] = min(x["family_id"] for x in group)
        merged["family_id"] = merged["id"]
        merged["alternate_ids"] = sorted({x["id"] for x in group if x["id"] != merged["id"]})
        merged["variants"] = list({x["id"]: {
            "id": x["id"], "source": x["source"], "source_id": x["source_id"],
            "title": x.get("title"), "variant_hint": x.get("variant_hint"),
            "files": [{key: file.get(key) for key in ("sha256", "format", "url", "storage_path") if file.get(key) is not None} for file in x["files"]],
        } for x in group}.values())
        provenance = {}
        for entry in group:
            for origin in entry.get("provenance", []) or [_provenance(entry)]:
                provenance[json.dumps(origin, sort_keys=True, ensure_ascii=False)] = origin
        merged["provenance"] = list(provenance.values())
        merged["sources"] = sorted({x["source"] for x in group})
        merged["license_variants"] = sorted({x["license"] for x in group if x.get("license")})
        merged["titles"] = list(dict.fromkeys(x.get("title") for x in group if x.get("title")))
        for key in ("tags", "categories", "taxonomy", "search_terms"):
            merged[key] = list(dict.fromkeys(value for x in group for value in x.get(key, [])))
        files = {}
        for entry in group:
            for source_file in entry["files"]:
                key = source_file.get("sha256") or source_file.get("url") or json.dumps(source_file, sort_keys=True)
                if key not in files:
                    files[key] = copy.deepcopy(source_file)
                    files[key]["source_records"] = []
                existing_origins = source_file.get("source_records") or [{"asset_id": entry["id"], "source": entry["source"], "source_id": entry["source_id"], "title": entry.get("title"), "source_url": entry.get("source_url"), "url": source_file.get("url"), "license": entry.get("license"), "attribution": entry.get("attribution")}]
                for origin in existing_origins:
                    if origin not in files[key]["source_records"]:
                        files[key]["source_records"].append(origin)
        merged["files"] = list(files.values())
        merged["formats"] = sorted({x.get("format", "unknown") for x in merged["files"]})
        merged["structures"] = sorted({x.get("structure", "unknown") for x in merged["files"]})
        merged["deduplication"] = {"input_records": len(group), "method": "explicit_upstream_family_or_source_identity_or_exact_original_sha256"}
        result.append(merged)
    return sorted(result, key=lambda x: x["id"]), {"input_records": len(items), "logical_assets": len(result), "logical_families": len(result), "variant_records": len(identities), "merged_records": len(items) - len(result)}


def _write_sqlite(records: list[dict[str, Any]], path: Path) -> None:
    if path.exists():
        path.unlink()
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE assets (id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
                license TEXT, distribution_status TEXT NOT NULL, metadata TEXT NOT NULL);
            CREATE TABLE asset_taxonomy (asset_id TEXT NOT NULL, category TEXT NOT NULL,
                PRIMARY KEY (asset_id, category));
            CREATE INDEX taxonomy_category ON asset_taxonomy(category, asset_id);
            CREATE TABLE asset_formats (asset_id TEXT NOT NULL, format TEXT NOT NULL,
                PRIMARY KEY (asset_id, format));
            CREATE INDEX formats_format ON asset_formats(format, asset_id);
            CREATE VIRTUAL TABLE assets_fts USING fts5(id UNINDEXED, title, aliases, context,
                tokenize='porter unicode61');
        """)
        for item in records:
            db.execute("INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?)", (item["id"], _text(item.get("title")), item["source"], item.get("license"), item["distribution_status"], json.dumps(item, ensure_ascii=False, separators=(",", ":"))))
            db.executemany("INSERT INTO asset_taxonomy VALUES (?, ?)", [(item["id"], category) for category in item["taxonomy"]])
            db.executemany("INSERT INTO asset_formats VALUES (?, ?)", [(item["id"], fmt) for fmt in item["formats"]])
            db.execute("INSERT INTO assets_fts VALUES (?, ?, ?, ?)", (
                item["id"], _text([item.get("title"), item.get("title_zh")]),
                _text([item.get("search_terms"), item.get("titles"), item.get("tags")]),
                _text([item.get("description"), item.get("categories"), item.get("taxonomy"), item.get("species"), item.get("anatomy"), item.get("action")]),
            ))
        db.execute("PRAGMA user_version=1")


def finalize(records: Iterable[dict[str, Any]], output_dir: str | Path) -> dict[str, Any]:
    """Write portable indexes and factual counts; do not grant acceptance."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    items, counts = deduplicate(records)
    catalog_path = output_dir / "catalog.jsonl"
    with catalog_path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    unique_files = {}
    for item in items:
        for file in item["files"]:
            if file.get("sha256"):
                unique_files[file["sha256"]] = file
    summary = {
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), **counts,
        "unique_original_files": len(unique_files),
        "unique_original_bytes": sum(x.get("size_bytes", 0) for x in unique_files.values()),
        "sources": dict(Counter(source for item in items for source in item["sources"])),
        "licenses": dict(Counter(item.get("license") or "unknown" for item in items)),
        "distribution_status": dict(Counter(item["distribution_status"] for item in items)),
        "media_structures": dict(Counter(x.get("structure", "unknown") for x in unique_files.values())),
        "validation_status": dict(Counter(x.get("validation", {}).get("status", "not_checked") for x in unique_files.values())),
        "taxonomy": {key: {"label_zh": definition["label_zh"], "assets": sum(key in item["taxonomy"] for item in items)} for key, definition in TAXONOMY.items()},
        "unclassified_assets": sum(not item["taxonomy"] for item in items),
        "missing_preview_files": sum(not x.get("preview") for x in unique_files.values()),
        "acceptance": {"scientific_coverage": "not_accepted", "visual_review": "not_completed", "illustrator_editability": "not_established_by_this_pipeline"},
        "catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
    }
    _write_sqlite(items, output_dir / "catalog.sqlite")
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _fts_query(query: str) -> str:
    raw = unicodedata.normalize("NFKC", query).lower().strip()
    parts = []
    remaining = raw
    # Match long phrases first so “巨噬细胞” does not turn into independent
    # “cell” plus an unrecognized fragment, and “大鼠” does not become mouse.
    matches = []
    for concept in CONCEPTS:
        for term in concept:
            if _matches(raw, term):
                matches.append((len(term), term, concept))
    for _, term, concept in sorted(matches, reverse=True):
        if not _matches(remaining, term):
            continue
        english = [alias for alias in concept if re.fullmatch(r"[a-z0-9 -]+", alias)]
        # Include the original term too, in case a source only labels in Chinese.
        aliases = list(dict.fromkeys([term, *english]))
        parts.append("(" + " OR ".join('"' + alias.replace('"', '""') + '"' for alias in aliases) + ")")
        if re.search(r"[\u3400-\u9fff]", term):
            remaining = remaining.replace(term, " ")
        else:
            remaining = re.sub(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", " ", remaining)
    for word in re.findall(r"[^\W_]+", remaining, re.UNICODE):
        if word not in {"a", "an", "the", "of", "for", "and", "with", "image", "illustration", "icon", "图", "图片", "素材"}:
            parts.append('"' + word.replace('"', '""') + '"')
    return " AND ".join(parts)


def search(db_path: str | Path, query: str, limit: int = 10, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Search a cached index; empty results are real, never arbitrary fallback."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    filters = filters or {}
    match = _fts_query(query)
    clauses, params = [], []
    if match:
        sql = "SELECT a.metadata FROM assets_fts f JOIN assets a ON a.id=f.id"
        clauses.append("assets_fts MATCH ?")
        params.append(match)
        ordering = "bm25(assets_fts, 0.0, 5.0, 2.0, 1.0), a.id"
    elif query.strip():
        return []
    else:
        sql, ordering = "SELECT a.metadata FROM assets a", "a.title, a.id"
    for key in ("source", "license", "distribution_status"):
        if filters.get(key):
            clauses.append(f"a.{key} = ?")
            params.append(filters[key])
    if filters.get("category"):
        clauses.append("EXISTS (SELECT 1 FROM asset_taxonomy t WHERE t.asset_id=a.id AND t.category=?)")
        params.append(filters["category"])
    if filters.get("format"):
        clauses.append("EXISTS (SELECT 1 FROM asset_formats t WHERE t.asset_id=a.id AND t.format=?)")
        params.append(filters["format"])
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY " + ordering + " LIMIT ?"
    params.append(limit)
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        return [json.loads(row[0]) for row in db.execute(sql, params)]
