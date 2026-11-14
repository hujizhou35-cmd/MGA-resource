"""Derive navigation from eligible variants and the pinned public manifest."""
from collections import Counter, defaultdict

LABELS = {
    "animals": ("Animals", "动物"), "people": ("People and human figures", "人体与人物"),
    "organs": ("Organs", "器官"), "tissues_vessels": ("Tissues and vessels", "组织与血管"),
    "cells": ("Cells", "细胞"), "organelles": ("Organelles", "细胞器"),
    "molecules": ("Molecules", "分子"), "microbes": ("Microbes", "微生物"),
    "lab_equipment": ("Lab equipment", "实验器材"), "experimental_actions": ("Experimental actions", "实验操作"),
    "clinical_workflow": ("Clinical components", "临床场景部件"), "omics_data": ("Omics and data components", "组学与数据部件"),
}
SOURCES = {
    "arcadia": ("Arcadia", "Model organism silhouettes", "模式生物剪影", "https://zenodo.org/records/18395998"),
    "bioicons": ("Bioicons", "Life science components from many authors, including pinned historical Servier artwork", "多作者生命科学组件，含已固定历史版本的 Servier 作品", "https://bioicons.com/"),
    "commons": ("Wikimedia Commons", "Selected anatomy illustrations", "定向选入的解剖插图", "https://commons.wikimedia.org/"),
    "healthicons": ("Health Icons", "Clinical equipment, people and public health", "临床器材、人物和公共卫生", "https://healthicons.org/"),
    "lab-equipment": ("KYU49 Lab Equipment", "Laboratory instruments and states", "实验器材及不同状态", "https://github.com/KYU49/SvgCollectionOfLabEquipment"),
    "nih-bioart": ("NIH BioArt Source", "Detailed medical and biological illustration", "精细医学和生物插画", "https://bioart.niaid.nih.gov/"),
    "open-pain": ("Open Pain Illustration", "Front and back human figures", "人体正背面", "https://github.com/DigitalHealthZurich/open-pain-illustration"),
    "openclipart": ("Openclipart", "Selected questionnaire component", "定向选入的问卷组件", "https://openclipart.org/"),
    "reactome": ("Reactome", "Cells, receptors and molecular structures", "细胞、受体与分子结构", "https://reactome.org/icon-lib"),
    "scidraw": ("SciDraw", "Animal, anatomy and experimental drawings", "动物、解剖与实验插画", "https://scidraw.io/"),
    "scisvg": ("SciSVG", "Cellular, laboratory and bioinformatics components", "细胞、实验与生信组件", "https://github.com/SaveenaSolanki/SciSVG"),
    "swissbiopics": ("SwissBioPics", "Cell illustrations with organelle context", "包含细胞器背景的细胞插画", "https://www.swissbiopics.org/"),
    "togo": ("DBCLS Togo Picture Gallery", "Life science illustrations and research components", "生命科学插画及研究组件", "https://togotv.dbcls.jp/en/pics.html"),
}
FACETS = "source license distribution_status category format structure content_type species state view role object depiction_kind color_mode palette_traits representation detail realism series transparent".split()


def build_catalog_info(catalog, published, variants, family_count):
    by_facet = {key: defaultdict(set) for key in FACETS}
    all_families = set()
    for family, values in variants:
        all_families.add(family)
        for key in FACETS:
            for value in values.get(key, []):
                by_facet[key][value].add(family)
    assert len(all_families) == family_count
    assigned = set().union(*by_facet["category"].values())
    categories = [{"id": key, "en": en, "zh": zh, "families": len(by_facet["category"][key])}
                  for key, (en, zh) in LABELS.items()]
    assert set(by_facet["category"]) <= LABELS.keys()
    originals = defaultdict(Counter)
    for item in published.values():
        for source in item["sources"]:
            provenance = {str(p["license"]) for p in item["provenance"] if p["source"] == source}
            originals[source].update(provenance)
            originals[source]["__total"] += 1
    assert set(originals) == SOURCES.keys()
    sources = [{"id": key, "name": name, "en": en, "zh": zh, "url": url, "originals": originals[key]["__total"],
                "licenses": sorted(k for k in originals[key] if k != "__total")}
               for key, (name, en, zh, url) in SOURCES.items()]
    return {"schema_version": 1, "catalog_sha256": catalog, "originals": len(published), "families": family_count,
            "categories": categories, "unclassified_families": len(all_families - assigned), "sources": sources,
            "facets": {key: [{"value": v, "families": len(ids)} for v, ids in sorted(entries.items())]
                       for key, entries in by_facet.items()},
            "identity_filters": ["asset_id", "family_id", "file_sha256"], "counts_overlap": True}
