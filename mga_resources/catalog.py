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
import subprocess
import time
import unicodedata
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
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

# Only narrow, explicit upstream categories are mapped automatically. Broad
# labels such as "Human physiology", "General items" or "Neuroscience" are
# left to the object names rather than assigning every item to an organ.
SOURCE_CATEGORY_MAP = {
    "amino acids": ["molecules"], "chemistry": ["molecules"],
    "animals": ["animals"], "cell culture": ["experimental_actions"],
    "cell lines": ["cells"], "cell types": ["cells"], "cell membrane": ["cells"],
    "chemo and bioinformatics": ["omics_data"], "computer hardware": ["omics_data"],
    "epigenetics": ["omics_data"], "genetics": ["omics_data"], "genomics": ["omics_data"],
    "extracellular matrix": ["tissues_vessels"], "intracellular components": ["organelles"],
    "lab apparatus": ["lab_equipment"], "machine learning": ["omics_data"],
    "microbiology": ["microbes"], "molecular biology": ["molecules"],
    "molecular modelling": ["molecules"], "nucleic acids": ["molecules"],
    "peptides": ["molecules"], "receptors channels": ["molecules"],
    "people other": ["people"], "procedures": ["experimental_actions"],
    "scientific graphs": ["omics_data"], "tissues": ["tissues_vessels"],
    "viruses": ["microbes"], "blood": ["clinical_workflow"],
    "body": ["people"], "healthcare workers": ["people"],
    "diagnostics": ["clinical_workflow"], "medical specialties": ["clinical_workflow"],
}
SOURCE_TOPIC_MAPPING_VERSION = "source-topic-1.0.0"


def _narrow_source_topics(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Map reviewed source subject buckets, never scientific object facts.

    Reactome's adapter stores only official category-page labels in tags;
    SciDraw preserves category_name as upstream_category. Do not apply this
    rule to a deduplicated family's union of tags or inherit other origins.
    NIH ontologytype arrays and Togo mixed taxon/subject tags are deliberately
    outside this mapping, including their known conflicting source labels.
    """
    if item.get("variants") or any(file.get("source_records") for file in item.get("files", [])):
        return []
    source = item.get("source")
    if source == "reactome":
        files = item.get("files") or []
        reviewed = all(
            file.get("public_scope", {}).get("status") == "included"
            and file["public_scope"].get("public_allowed") is True
            and file["public_scope"].get("evidence_original_sha256") == file.get("sha256")
            and file.get("sha256")
            and file["public_scope"].get("evidence")
            and file["public_scope"].get("reviewed_by")
            for file in files
        )
        if not files or not reviewed:
            return []
        labels = item.get("tags", [])
        mapping = {"transporter": "molecules", "compound": "molecules"}
        upstream_field = "tags"
        basis = "official_category_page_label_preserved_by_reactome_adapter"
    elif source == "scidraw":
        labels = [item.get("upstream_category")]
        mapping = {"bird": "animals"}
        upstream_field = "upstream_category"
        basis = "official_category_name_preserved_by_scidraw_adapter"
    else:
        return []
    facets = []
    for label in dict.fromkeys(value for value in labels if isinstance(value, str)):
        topic = mapping.get(label.casefold().strip())
        if topic:
            facet = {
                "source": source, "source_id": item["source_id"],
                "kind": "official_catalog_subject", "label": label,
                "taxonomy": topic, "upstream_field": upstream_field,
                "source_url": item.get("source_url"),
                "upstream_revision": item.get("upstream_revision"),
                "basis": basis, "mapping_version": SOURCE_TOPIC_MAPPING_VERSION,
                "scientific_assertion": False,
            }
            if source == "reactome":
                facet["scope_review_bindings"] = [{
                    "file_sha256": file["sha256"],
                    "decision_id": file["public_scope"].get("decision_id"),
                    "evidence": copy.deepcopy(file["public_scope"]["evidence"]),
                } for file in item["files"]]
            facets.append(facet)
    return facets


TAXONOMY["molecules"]["terms"] += [
    "amino acid", "amino acids", "alanine", "arginine", "asparagine", "aspartic acid",
    "cysteine", "glutamine", "glutamic acid", "glycine", "histidine", "isoleucine",
    "leucine", "lysine", "methionine", "phenylalanine", "proline", "serine",
    "threonine", "tryptophan", "tyrosine", "valine", "adenosine", "atp",
    "氨基酸", "丙氨酸", "精氨酸", "核苷酸",
]
TAXONOMY["people"]["terms"] += ["body", "arm", "leg", "hand", "foot", "head", "torso", "人体", "手臂", "躯干"]
TAXONOMY["organs"]["terms"] += ["anus", "colon", "rectum", "ear", "nose", "tongue", "肛门", "直肠", "结肠"]
TAXONOMY["tissues_vessels"]["terms"] += ["blood drop", "blood droplet", "blood sample", "血滴", "血样"]
TAXONOMY["lab_equipment"]["terms"] += ["blood bag", "transfusion bag", "infusion bag", "血袋", "输液袋"]
TAXONOMY["clinical_workflow"]["terms"] += ["blood type", "blood group", "transfusion", "donation", "blood bag", "血型", "输血", "献血", "血袋"]

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

CONCEPTS += [
    ("小鼠", "实验小鼠", "mouse", "mice"),
    ("大鼠", "实验大鼠", "rat", "rats"),
    ("兔", "实验兔", "rabbit"),
    ("科研人员", "研究人员", "scientist", "researcher"),
    ("医生", "临床医生", "doctor", "physician", "clinician"),
    ("患者", "病人", "参与者", "patient", "participant", "person"),
    ("人体", "身体", "human body", "body"),
    ("淋巴细胞", "lymphocyte"), ("T淋巴细胞", "T细胞", "t lymphocyte", "t cell"),
    ("B淋巴细胞", "B细胞", "b lymphocyte", "b cell"),
    ("中性粒细胞", "neutrophil"), ("成纤维细胞", "fibroblast"),
    ("细胞核", "cell nucleus", "nucleus", "nuclei"),
    ("血管内皮", "内皮细胞", "endothelium", "endothelial", "vascular endothelium", "endothelial cell"),
    ("细胞膜受体", "膜受体", "cell membrane receptor", "membrane receptor", "receptor"),
    ("ATP", "atp", "三磷酸腺苷", "adenosine triphosphate"),
    ("氨基酸", "amino acid", "amino acids"),
    ("丙氨酸", "alanine"), ("精氨酸", "arginine"), ("甘氨酸", "glycine"),
    ("球菌", "coccus", "cocci", "coccus bacteria"),
    ("杆菌", "杆状细菌", "rod shaped bacterium", "rod shaped bacteria", "bacillus"),
    ("冠状病毒", "coronavirus"),
    ("移液器", "micropipette", "pipette"),
    ("孔板", "多孔板", "microplate", "well plate"),
    ("培养皿", "细胞培养皿", "cell culture petri dish", "culture dish", "petri dish"),
    ("离心", "离心分离", "centrifugation"),
    ("移液", "pipetting", "liquid transfer"),
    ("随机分组", "randomization", "randomisation"),
    ("招募", "参与者招募", "participant recruitment", "recruitment"),
    ("血压", "blood pressure"),
    ("RNA测序", "rna sequencing", "rna-seq", "rna seq", "rnaseq"),
    ("热图", "heatmap", "heat map", "gene expression heatmap"),
    ("测序仪", "sequencer", "sequencing instrument"),
    ("基因网络", "gene network"),
    ("森林图", "forest plot"), ("血型", "blood type", "blood group"),
    ("实验器材", "仪器", "器材", "lab equipment", "equipment", "instrument", "apparatus"),
]

# These describe presentation or context, not the primary medical object.
# They boost ranking when metadata records them; they do not manufacture facts
# about an asset which has no view/style annotation.
PREFERRED_QUERY_TERMS = {
    "侧视": ("side view", "lateral"), "侧面": ("side view", "lateral"),
    "正面": ("front", "anterior"), "背面": ("back", "posterior"),
    "横截面": ("cross section", "cross-section"), "截面": ("cross section", "cross-section"),
    "分层": ("layers", "layered"), "层": ("layer", "layers"),
    "双螺旋": ("double helix",), "单链": ("single stranded", "single-stranded"),
    "Y形": ("y shaped", "y-shaped"), "y形": ("y shaped", "y-shaped"),
    "颗粒": ("particle",), "出芽": ("budding",),
    "光学": ("light", "optical"), "透明背景": ("transparent",),
    "彩色": ("colored", "colour", "color"), "线稿": ("outline", "line art"),
    "三维": ("3d",), "二维": ("2d",),
    "双": ("bilateral", "both", "pair"), "完整": ("complete", "whole"),
    "全身": ("whole body", "full body"), "两组": ("two groups",),
    "side view": ("side view", "lateral"), "lateral": ("lateral",),
    "front": ("front", "anterior"), "back": ("back", "posterior"),
    "anterior": ("anterior", "front"), "posterior": ("posterior", "back"),
    "cross section": ("cross section", "cross-section"), "cross-section": ("cross-section", "cross section"),
    "single stranded": ("single stranded", "single-stranded"), "single-stranded": ("single-stranded", "single stranded"),
    "double helix": ("double helix",), "y shaped": ("y shaped", "y-shaped"),
    "layers": ("layers", "layered"), "layer": ("layer", "layers"),
    "particle": ("particle",), "budding": ("budding",),
    "light": ("light", "optical"), "optical": ("optical", "light"),
    "outline": ("outline",), "flat": ("flat",), "transparent": ("transparent",),
    "3d": ("3d",), "2d": ("2d",), "monochrome": ("monochrome",),
    "two groups": ("two groups",),
}
QUERY_CONTEXT_TERMS = {
    "illustration", "illustrations", "image", "images", "icon", "icons", "diagram", "schematic",
    "template", "workflow", "procedure", "laboratory", "lab", "experiment", "study",
    "structure", "anatomical", "generic", "measurement", "survey", "separation",
    "a", "an", "the", "of", "for", "and", "with",
    "插画", "示意图", "示意", "图片", "素材", "图标", "模板", "流程", "操作", "步骤",
    "实验室", "实验", "研究", "通用", "结构", "解剖", "测量", "调查", "人物", "设备", "图",
    "一只", "一个", "一张", "的", "与", "和", "及",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(_text(x) for x in value)
    if isinstance(value, dict):
        return " ".join(_text(x) for x in value.values())
    return str(value)


def _matches(text: str, term: str) -> bool:
    term = term.lower()
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
    descriptive = " ".join(_text(item.get(key)) for key in ("title", "title_zh", "description", "categories", "tags", "upstream_category", "species", "anatomy", "action")).lower()
    descriptive = unicodedata.normalize("NFKC", descriptive).replace("_", " ")
    existing = [key for key in (item.get("taxonomy") or []) if key in TAXONOMY]
    evidence = {}
    for key, definition in TAXONOMY.items():
        matches = [term for term in definition["terms"] if _matches(descriptive, term)]
        if matches:
            evidence[key] = matches
    upstream_categories = [*item["categories"], item.get("upstream_category")]
    for upstream_category in upstream_categories:
        normalized = re.sub(r"[^a-z0-9]+", " ", _text(upstream_category).lower()).strip()
        for key in SOURCE_CATEGORY_MAP.get(normalized, []):
            evidence.setdefault(key, []).append(f"source_category:{upstream_category}")
    facets = _narrow_source_topics(item)
    if facets:
        item["source_subject_facets"] = facets
        item["topic_mapping_version"] = SOURCE_TOPIC_MAPPING_VERSION
        for facet in facets:
            evidence.setdefault(facet["taxonomy"], []).append(
                f"source_subject:{source}:{facet['upstream_field']}:{facet['label']}"
            )
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


def _search_context(item: dict[str, Any]) -> dict[str, Any]:
    """Preserve an upstream variant's facts before the family union of tags.

    Unknown attributes stay absent. This does not assign medical facts from a
    benchmark, a filename extension, the parent family or another variant.
    """
    keys = ("title", "title_zh", "description", "tags", "categories", "taxonomy",
            "source_subject_facets", "topic_mapping_version",
            "upstream_category", "species", "state", "view", "role", "object",
            "anatomy", "action", "depiction_kind", "scientific", "author", "distribution_status")
    return {key: copy.deepcopy(item[key]) for key in keys if item.get(key) is not None}


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
        variants = {}
        for entry in group:
            for variant in entry.get("variants") or [{
                **_search_context(entry),
                "id": entry["id"], "source": entry["source"], "source_id": entry["source_id"],
                "title": entry.get("title"), "variant_hint": entry.get("variant_hint"),
                "files": [{key: file.get(key) for key in ("sha256", "format", "url", "storage_path") if file.get(key) is not None} for file in entry["files"]],
            }]:
                if variant["id"] not in variants:
                    variants[variant["id"]] = copy.deepcopy(variant)
                else:
                    for file in variant.get("files", []):
                        if file not in variants[variant["id"]]["files"]:
                            variants[variant["id"]]["files"].append(copy.deepcopy(file))
        merged["variants"] = list(variants.values())
        merged["alternate_ids"] = sorted(({x["id"] for x in group} | set(variants) | {alias for x in group for alias in x.get("alternate_ids", [])}) - {merged["id"]})
        provenance = {}
        for entry in group:
            for origin in entry.get("provenance", []) or [_provenance(entry)]:
                provenance[json.dumps(origin, sort_keys=True, ensure_ascii=False)] = origin
        merged["provenance"] = list(provenance.values())
        merged["sources"] = sorted({source for x in group for source in x.get("sources") or [x["source"]]})
        merged["license_variants"] = sorted({license_id for x in group for license_id in x.get("license_variants") or [x.get("license")] if license_id})
        merged["titles"] = list(dict.fromkeys(title for x in group for title in x.get("titles") or [x.get("title")] if title))
        for key in ("tags", "categories", "taxonomy", "search_terms"):
            merged[key] = list(dict.fromkeys(value for x in group for value in x.get(key, [])))
        files = {}
        for entry in group:
            for source_file in entry["files"]:
                key = source_file.get("sha256") or source_file.get("url") or json.dumps(source_file, sort_keys=True)
                if key not in files:
                    files[key] = copy.deepcopy(source_file)
                    files[key]["source_records"] = []
                existing_origins = source_file.get("source_records") or [{**_search_context(entry), "asset_id": entry["id"], "source": entry["source"], "source_id": entry["source_id"], "title": entry.get("title"), "source_url": entry.get("source_url"), "url": source_file.get("url"), "license": entry.get("license"), "attribution": entry.get("attribution")}]
                for origin in existing_origins:
                    if origin not in files[key]["source_records"]:
                        files[key]["source_records"].append(origin)
        merged["files"] = list(files.values())
        merged["formats"] = sorted({x.get("format", "unknown") for x in merged["files"]})
        merged["structures"] = sorted({x.get("structure", "unknown") for x in merged["files"]})
        merged["validation"]["status"] = (
            "machine_checked" if merged["files"] and all(
                file.get("validation", {}).get("status") in {"machine_checked", "machine_checked_and_rendered"}
                for file in merged["files"]
            ) else "incomplete"
        )
        for check in ("scientific_review", "visual_review", "illustrator_editability"):
            statuses = {entry["validation"][check] for entry in group}
            merged["validation"][check] = statuses.pop() if len(statuses) == 1 else ("not_tested" if check == "illustrator_editability" else "not_reviewed")
        merged["deduplication"] = {"input_records": len(group), "method": "explicit_upstream_family_or_source_identity_or_exact_original_sha256"}
        result.append(merged)
    variant_ids = {variant["id"] for item in result for variant in item["variants"]}
    return sorted(result, key=lambda x: x["id"]), {"input_records": len(items), "logical_assets": len(result), "logical_families": len(result), "variant_records": len(variant_ids), "merged_records": len(items) - len(result)}


def _search_core(payload: dict[str, Any]) -> dict[str, Any]:
    """Process boundary only. Query/ranking policy is exclusively JavaScript."""
    entrypoint = Path(__file__).with_name("search-cli.mjs")
    try:
        completed = subprocess.run(
            ["node", str(entrypoint)], input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=120,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Shared search requires Node.js 22 or later; install Node before rebuilding/searching") from exc
    if completed.returncode:
        raise ValueError(completed.stderr.decode("utf-8", errors="replace").strip())
    return json.loads(completed.stdout)


def search_policy_binding() -> dict[str, str]:
    """Bind the complete portable policy, including imported vocabulary bytes."""
    names = {
        "search_core_sha256": "search-core.mjs",
        "search_vocabulary_sha256": "search-vocabulary.mjs",
        "search_cli_sha256": "search-cli.mjs",
    }
    values = {key: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
              for key, name in names.items()}
    files = {"mga_resources/" + name: values[key] for key, name in names.items()}
    canonical = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")
    return {**values, "search_policy_sha256": hashlib.sha256(canonical).hexdigest()}


@lru_cache(maxsize=8)
def _search_versions(policy_sha256: str) -> dict[str, Any]:
    prepared = _search_core({"action": "prepare", "records": []})
    version = prepared.get("index_version")
    if type(version) is not int or version < 1 or not prepared.get("ranking_version"):
        raise ValueError("Shared search CLI must declare ranking and index versions")
    if search_policy_binding()["search_policy_sha256"] != policy_sha256:
        raise ValueError("Search policy changed while reading its versions")
    return {"ranking_version": prepared["ranking_version"], "search_index_version": version}


def search_policy_metadata() -> dict[str, Any]:
    """Current runtime versions and byte hashes, reusable by cloud replay tools."""
    binding = search_policy_binding()
    return {**binding, **_search_versions(binding["search_policy_sha256"])}


def _assert_public_records(records: list[dict[str, Any]]) -> None:
    """Reject unfiltered internal scope records before writing public artifacts.

    This consumes explicit upstream scope decisions only; it does not classify
    titles, annotations, typography, templates or scientific artwork.
    """
    for record in records:
        for item in [record, *record.get("files", []), *[origin for file in record.get("files", []) for origin in file.get("source_records", [])]]:
            scope=item.get("public_scope") or {}
            if (item.get("catalog_visibility")=="internal_diagnostic" or scope.get("public_allowed") is False
                    or scope.get("status") in {"excluded", "pending_review"} or item.get("scope_status")=="excluded"):
                raise ValueError("Public catalog/index requires scope-filtered records; apply filter_public_records() before finalize or rebuild")


def _write_sqlite(records: list[dict[str, Any]], path: Path, catalog_version: str) -> dict[str, Any]:
    _assert_public_records(records)
    binding = search_policy_binding()
    prepared = _search_core({"action": "prepare", "records": records})
    index_version = prepared.get("index_version")
    if type(index_version) is not int or index_version < 1:
        raise ValueError("Shared search CLI must declare its index version")
    if search_policy_binding() != binding:
        raise ValueError("Search policy changed while preparing the index")
    policy = {**binding, "ranking_version": prepared["ranking_version"], "search_index_version": index_version}
    if path.exists():
        path.unlink()
    with closing(sqlite3.connect(path)) as db, db:
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
            CREATE TABLE index_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE file_variants (variant_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL,
                family_id TEXT NOT NULL, file_sha256 TEXT, eligible INTEGER NOT NULL,
                values_json TEXT NOT NULL, metadata TEXT NOT NULL,
                title_terms TEXT NOT NULL, context_terms TEXT NOT NULL, all_terms TEXT NOT NULL,
                rotation_key INTEGER NOT NULL, features TEXT NOT NULL);
            CREATE INDEX variants_family ON file_variants(family_id);
            CREATE INDEX variants_file ON file_variants(file_sha256);
            CREATE VIRTUAL TABLE file_variants_fts USING fts5(variant_id UNINDEXED, title, aliases, context,
                tokenize='porter unicode61');
        """)
        for item in records:
            db.execute("INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?)", (item["id"], _text(item.get("title")), item["source"], item.get("license"), item["distribution_status"], json.dumps(item, ensure_ascii=False, separators=(",", ":"))))
            db.executemany("INSERT INTO asset_taxonomy VALUES (?, ?)", [(item["id"], category) for category in item["taxonomy"]])
            db.executemany("INSERT INTO asset_formats VALUES (?, ?)", [(item["id"], fmt) for fmt in item["formats"]])
            db.execute("INSERT INTO assets_fts VALUES (?, ?, ?, ?)", (
                item["id"], _text([item.get("title"), item.get("title_zh")]),
                _text([item.get("search_terms"), item.get("titles"), item.get("tags")]),
                _text([item.get("description"), item.get("categories"), item.get("upstream_category"), item.get("taxonomy"), item.get("species"), item.get("anatomy"), item.get("action"), item.get("view"), item.get("style")]),
            ))
        for variant in prepared["variants"]:
            db.execute("INSERT INTO file_variants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                variant["variant_id"], variant["asset_id"], variant["family_id"], variant["file"].get("sha256"),
                int(variant["eligible"]), json.dumps(variant["values"], ensure_ascii=False, separators=(",", ":")),
                json.dumps(variant, ensure_ascii=False, separators=(",", ":")),
                json.dumps(variant["lexical"]["title"], ensure_ascii=False, separators=(",", ":")),
                json.dumps(variant["lexical"]["context"], ensure_ascii=False, separators=(",", ":")),
                json.dumps(variant["lexical"]["all"], ensure_ascii=False, separators=(",", ":")),
                variant["rotation_key"], json.dumps(variant["features"], ensure_ascii=False, separators=(",", ":")),
            ))
            db.execute("INSERT INTO file_variants_fts VALUES (?, ?, ?, ?)", (
                variant["variant_id"], variant["search"]["title"], variant["search"]["aliases"], variant["search"]["context"],
            ))
        db.executemany("INSERT INTO index_metadata VALUES (?, ?)", [
            ("catalog_version", catalog_version), *[(key, str(value)) for key, value in policy.items()],
        ])
        db.execute(f"PRAGMA user_version={index_version}")
    return policy


def finalize(records: Iterable[dict[str, Any]], output_dir: str | Path) -> dict[str, Any]:
    """Write portable indexes and factual counts; do not grant acceptance."""
    records=list(records)
    _assert_public_records(records)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    items, counts = deduplicate(records)
    catalog_path = output_dir / "catalog.jsonl"
    with catalog_path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
        if not items:
            handle.write("\n")
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
        "topic_mapping_version": SOURCE_TOPIC_MAPPING_VERSION,
        "missing_preview_files": sum(not x.get("preview") for x in unique_files.values()),
        "acceptance": {"scientific_coverage": "not_accepted", "visual_review": "not_completed", "illustrator_editability": "not_established_by_this_pipeline"},
        "catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
    }
    policy = _write_sqlite(items, output_dir / "catalog.sqlite", summary["catalog_sha256"])
    summary.update(policy)
    summary["search_policy"] = "mga_resources/search-core.mjs; old BM25 available as search_legacy"
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def rebuild_search_index(catalog_path: str | Path, index_path: str | Path) -> str:
    """Index a frozen catalog without normalizing or rewriting its bytes."""
    catalog_path, index_path = Path(catalog_path), Path(index_path)
    data = catalog_path.read_bytes()
    records = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    if any(not row.get("id") or not row.get("family_id") for row in records):
        raise ValueError("Index rebuild requires a finalized family catalog")
    version = hashlib.sha256(data).hexdigest()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    _write_sqlite(records, index_path, version)
    return version


def _query_plan(query: str) -> tuple[str, list[tuple[str, ...]]]:
    raw = unicodedata.normalize("NFKC", query).lower().strip()
    if "人体" in raw and any(_matches(raw, term) for key in ("organs", "tissues_vessels", "cells") for term in TAXONOMY[key]["terms"]):
        # “人体肝脏” specifies human species, not a second request for a whole
        # human-body illustration. Keep human as a mandatory search token.
        raw = raw.replace("人体", " human ")
    parts = []
    remaining = raw
    # Match long phrases first so “巨噬细胞” does not turn into independent
    # “cell” plus an unrecognized fragment, and “大鼠” does not become mouse.
    matches = []
    for concept in CONCEPTS:
        for original_term in concept:
            term = original_term.lower()
            if _matches(raw, term):
                matches.append((len(term), term, concept))
    for _, term, concept in sorted(matches, reverse=True):
        if not _matches(remaining, term):
            continue
        english = [alias.lower() for alias in concept if re.fullmatch(r"[a-zA-Z0-9 -]+", alias)]
        # Include the original term too, in case a source only labels in Chinese.
        aliases = list(dict.fromkeys([term, *english]))
        parts.append("(" + " OR ".join('"' + alias.replace('"', '""') + '"' for alias in aliases) + ")")
        if re.search(r"[\u3400-\u9fff]", term):
            remaining = remaining.replace(term, " ")
        else:
            remaining = re.sub(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", " ", remaining)
    preferences = []
    for term in sorted(PREFERRED_QUERY_TERMS, key=len, reverse=True):
        if _matches(remaining, term):
            preferences.append(PREFERRED_QUERY_TERMS[term])
            if re.search(r"[\u3400-\u9fff]", term):
                remaining = remaining.replace(term.lower(), " ")
            else:
                remaining = re.sub(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", " ", remaining)
    for term in sorted(QUERY_CONTEXT_TERMS, key=len, reverse=True):
        if re.search(r"[\u3400-\u9fff]", term):
            remaining = remaining.replace(term, " ")
        else:
            remaining = re.sub(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", " ", remaining)
    for word in re.findall(r"[^\W_]+", remaining, re.UNICODE):
        parts.append('"' + word.replace('"', '""') + '"')
    # A style-only query still has to match its actual terms. It must not
    # silently return arbitrary catalog entries after removing modifiers.
    if not parts and preferences:
        parts = ["(" + " OR ".join('"' + word + '"' for word in group) + ")" for group in preferences]
    return " AND ".join(dict.fromkeys(parts)), preferences


def _fts_query(query: str) -> str:
    return _query_plan(query)[0]


def search_legacy(db_path: str | Path, query: str, limit: int = 10, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Search a cached index; empty results are real, never arbitrary fallback."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    filters = filters or {}
    match, preferences = _query_plan(query)
    clauses, params = [], []
    if match:
        sql = "SELECT a.metadata, bm25(assets_fts, 0.0, 5.0, 2.0, 1.0) AS relevance FROM assets_fts f JOIN assets a ON a.id=f.id"
        clauses.append("assets_fts MATCH ?")
        params.append(match)
        ordering = "bm25(assets_fts, 0.0, 5.0, 2.0, 1.0), a.id"
    elif query.strip():
        return []
    else:
        sql, ordering = "SELECT a.metadata, 0 AS relevance FROM assets a", "a.title, a.id"
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
    params.append(min(100, max(limit * 5, 50)) if preferences else limit)
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        candidates = [(json.loads(row[0]), row[1]) for row in db.execute(sql, params)]
    if preferences:
        def preference_score(candidate):
            item, relevance = candidate
            text = _text([item.get(key) for key in ("title", "title_zh", "description", "tags", "view", "style", "categories")]).lower()
            matched = sum(any(_matches(text, term) for term in group) for group in preferences)
            return (-matched, relevance, item["id"])
        candidates.sort(key=preference_score)
    return [item for item, _ in candidates[:limit]]


def search_page(db_path: str | Path, query: str, limit: int = 10,
                filters: dict[str, Any] | None = None, *, mode: str = "explore",
                seed: str = "default", history: dict[str, Any] | None = None,
                project: dict[str, Any] | None = None, cursor: str | None = None,
                preferences: dict[str, Any] | None = None, candidate_pool: int = 75) -> dict[str, Any]:
    """Execute the shared core's compact SQL plan; no Python ranking policy.

    The database applies ALL medical/file/appearance predicates and selects one
    representative per family before candidate windows. Only window references
    and precomputed similarity features enter the ranking kernel. Full metadata
    is read for the returned selection, never all broad-query matches.
    """
    total_started = time.perf_counter()
    policy = search_policy_metadata()
    sql_seconds = core_seconds = 0.0
    filters, history, project = filters or {}, history or {}, project or {}
    def invoke_core(payload):
        nonlocal core_seconds
        started = time.perf_counter()
        result = _search_core(payload)
        core_seconds += time.perf_counter() - started
        return result
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        def read_sql(statement, parameters=()):
            nonlocal sql_seconds
            started = time.perf_counter()
            rows = db.execute(statement, parameters).fetchall()
            sql_seconds += time.perf_counter() - started
            return rows
        if read_sql("PRAGMA user_version")[0][0] != policy["search_index_version"]:
            raise ValueError(f"Search index must be rebuilt as v{policy['search_index_version']} with rebuild_search_index() or finalize(); search_legacy() remains available for old indexes")
        metadata = dict(read_sql("SELECT key, value FROM index_metadata"))
        if any(metadata.get(key) != str(value) for key, value in policy.items()):
            raise ValueError("Search policy version or byte hash changed; rebuild the index with rebuild_search_index() or finalize()")
        prepared = invoke_core({"action": "window_plan", "request": {
            "query": query, "filters": filters, "limit": limit, "mode": mode, "seed": seed,
            "history": history, "project": project, "cursor": cursor, "preferences": preferences or {},
            "candidate_pool": candidate_pool, "catalog_version": metadata["catalog_version"],
        }})
        state, sql, diagnostic_sql = prepared["state"], prepared["sql"], prepared["diagnostic_sql"]
        plan = state["plan"]
        if metadata["ranking_version"] != plan["ranking_version"]:
            raise ValueError("Search policy version changed; rebuild the index with finalize()")
        count_row = read_sql(sql["counts"], sql["params"])[0]
        counts = dict(zip(("qualified_variants", "qualified_families"), count_row))
        rows = []
        for row in read_sql(sql["window"], sql["params"]):
            candidate = dict(zip(("variant_id", "asset_id", "family_id", "file_sha256", "features", "relevance", "preference_score", "pinned", "selection_score", "rotation"), row))
            candidate["features"] = json.loads(candidate["features"])
            rows.append(candidate)
        compact_bytes = len(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        page = invoke_core({"action": "window_finish", "rows": rows, "counts": counts, "state": state})
        variants = [json.loads(read_sql("SELECT metadata FROM file_variants WHERE variant_id=?", (row["variant_id"],))[0][0]) for row in page["selected"]]
        details_bytes = len(json.dumps(variants, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        materialized = invoke_core({"action": "hydrate", "selected": page.pop("selected"), "variants": variants})
        enriched = []
        for result in materialized["results"]:
            asset = json.loads(read_sql("SELECT metadata FROM assets WHERE id=?", (result["asset_id"],))[0][0])
            selected = next(file for file in asset["files"] if file.get("sha256") == result["selected_file"].get("sha256"))
            # Keep the core's file-bound content classification and evidence;
            # this adapter only hydrates details and must not reclassify it.
            selected = {**selected, "content_type": result["selected_file"]["content_type"]}
            enriched.append({**asset, **result, "selected_file": selected, "preview": selected.get("preview"), "download": selected.get("archive") or {"url": selected.get("url")}})
        page["results"] = enriched
        page["diagnostics"]["unavailable_project_pins"] = [
            {**json.loads(pin), "reason": "selected file no longer satisfies this query, hard filters, availability or catalog"}
            for pin, available in read_sql(sql["pins"], sql["params"]) if not available
        ]
        # Small aggregates/samples explain missing metadata without loading the
        # rejected artwork records. Condition counts may overlap by design.
        base_where = " AND ".join(diagnostic_sql["text_clauses"]) or "1"
        text_count = read_sql(f"SELECT count(*) FROM {diagnostic_sql['from']} WHERE {base_where}", diagnostic_sql["text_params"])[0][0] if not plan["empty"] else 0
        rejected, uncertain = {}, []
        if not plan["empty"]:
            for condition in diagnostic_sql["conditions"]:
                count = read_sql(f"SELECT count(*) FROM {diagnostic_sql['from']} WHERE {base_where} AND NOT ({condition['sql']})", [*diagnostic_sql["text_params"], *condition["params"]])[0][0]
                rejected[condition["code"]] = count
            for key in plan["requirements"]:
                path = "$." + key
                other_conditions = [condition for condition in diagnostic_sql["conditions"] if condition["code"] != "hard_filter:" + key]
                uncertainty_where = " AND ".join([base_where, *[("(" + condition["sql"] + " OR " + condition["unknown_sql"] + ")") if condition.get("unknown_sql") else condition["sql"] for condition in other_conditions]])
                uncertainty_params = [*diagnostic_sql["text_params"], *[parameter for condition in other_conditions for parameter in condition["params"]], path]
                samples = read_sql(f"SELECT v.variant_id, v.family_id FROM {diagnostic_sql['from']} WHERE {uncertainty_where} AND json_array_length(v.values_json, ?)=0 ORDER BY v.variant_id LIMIT 20", uncertainty_params)
                count = read_sql(f"SELECT count(*) FROM {diagnostic_sql['from']} WHERE {uncertainty_where} AND json_array_length(v.values_json, ?)=0", uncertainty_params)[0][0]
                rejected["unknown:" + key] = count
                uncertain.extend({"variant_id": row[0], "family_id": row[1], "reason": "unknown:" + key} for row in samples)
    page["diagnostics"]["sqlite_pushdown"] = {"text_matched_variants": text_count,
        "payload_variants_loaded": len(rows), "payload_bytes_loaded": compact_bytes,
        "selected_detail_variants_loaded": len(variants), "selected_detail_bytes_loaded": details_bytes,
        "rejections_by_condition_may_overlap": rejected,
        "hard_filters_before_payload_load": True, "family_grouping_before_candidate_limit": True,
        "candidate_limit_before_filters": None}
    page["diagnostics"]["uncertain_candidates"] = uncertain[:20]
    page["adapter_metrics"].update({"sqlite_wall_milliseconds": sql_seconds * 1000,
        "node_process_wall_milliseconds": core_seconds * 1000,
        "search_page_wall_milliseconds": (time.perf_counter() - total_started) * 1000,
        "returned_results_bytes": len(json.dumps(page["results"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))})
    return page


def search(db_path: str | Path, query: str, limit: int = 10, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Backwards compatible list API, backed by the shared JS search policy."""
    return search_page(db_path, query, limit, filters)["results"]
