"""Policy regressions: explicit variants, scientific constraints and reproducibility.

Synthetic fixtures prove mechanics only. They do not replace the fixed bilingual
benchmark or independent visual relevance and appearance review of real assets.
"""
import copy
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from mga_resources.catalog import _search_core, finalize, search, search_page


def artwork(number, *, title="Mouse", family=None, color="color", species="mouse", source="fixture", **fields):
    digest = hashlib.sha256(str(number).encode()).hexdigest()
    preview_sha = hashlib.sha256((str(number) + "preview").encode()).hexdigest()
    appearance = {
        "classification_version": "appearance-1.0.0",
        "binding": {"file_sha256": digest, "preview_sha256": preview_sha},
        "color": {"mode": color, "palette_traits": [], "confidence": "high", "review_status": "machine_measured_pending_visual_review"},
        "style": {
            "representation": {"values": ["flat_fill"], "basis_evidence_indices": [0], "review_status": "official_file_label", "confidence": "high"},
            "detail": {"value": "minimal", "basis_evidence_indices": [0], "review_status": "official_file_label", "confidence": "high"},
            "realism": {"value": "schematic", "basis_evidence_indices": [0], "review_status": "official_file_label", "confidence": "high"},
            "series": {"memberships": [{"id": "fixture-series", "label": "Fixture"}], "basis_evidence_indices": [0]},
            "accepted_evidence": [{"kind": "official_source_file", "file_sha256": digest, "source_url": "https://example.invalid/style", "labels": {"detail": "minimal"}}],
        },
    }
    return {"source": source, "source_id": str(number), "title": title, "family_hint": family,
            "species": species, "license": "CC0-1.0", "author": "Fixture artist", **fields,
            "files": [{"sha256": digest, "format": "svg", "structure": "pure_vector", "url": f"https://example.invalid/{number}.svg",
                       "archive": {"url": "https://example.invalid/originals.tar.gz", "member": f"{number}.svg"},
                       "preview": {"sha256": preview_sha, "archive": {"url": "https://example.invalid/previews.tar.gz", "member": f"{number}.png"}},
                       "validation": {"status": "machine_checked_and_rendered"}, "appearance": appearance}]}


def database(tmp_path, records):
    finalize(records, tmp_path)
    return tmp_path / "catalog.sqlite"


def test_color_filter_selects_its_own_file_preview_download_before_family_grouping(tmp_path):
    black, colored = artwork(1, family="one", color="black_white"), artwork(2, family="one")
    db = database(tmp_path, [black, colored])
    for query, expected in (("彩色小鼠", colored), ("黑白小鼠", black)):
        rows = search(db, query)
        assert len(rows) == 1
        selected = rows[0]["selected_file"]
        assert selected["sha256"] == expected["files"][0]["sha256"]
        assert rows[0]["preview"] == expected["files"][0]["preview"]
        assert rows[0]["download"] == expected["files"][0]["archive"]
    assert len(search(db, "mouse")) == 1


def test_filter_precedes_large_pool_cutoff_and_grouping(tmp_path):
    records = [artwork(i, color="black_white") for i in range(150)] + [artwork(900)]
    db = database(tmp_path, records)
    page = search_page(db, "mouse", filters={"color_mode": "color"})
    assert [row["selected_source"]["source_id"] for row in page["results"]] == ["900"]
    assert page["diagnostics"]["sqlite_pushdown"]["text_matched_variants"] == 151
    assert page["diagnostics"]["sqlite_pushdown"]["payload_variants_loaded"] == 1
    assert page["diagnostics"]["candidate_pool"] == 75


def test_unknown_scientific_constraints_do_not_match_or_cross_variant_contexts(tmp_path):
    normal = artwork(1, family="same", title="Mouse lung", state="normal", view="lateral", role="whole_organ")
    rat = artwork(2, family="same", title="Rat lung", species="rat", state="pneumonia", view="anterior", role="whole_organ")
    unknown = artwork(3, title="Mouse lung")
    db = database(tmp_path, [normal, rat, unknown])
    page = search_page(db, "正常小鼠肺侧视", filters={"role": "whole_organ"})
    assert len(page["results"]) == 1
    assert page["results"][0]["selected_file"]["sha256"] == normal["files"][0]["sha256"]
    assert page["diagnostics"]["sqlite_pushdown"]["rejections_by_condition_may_overlap"]["unknown:state"] == 1
    assert page["diagnostics"]["uncertain_candidates"]
    assert search(db, "小鼠肺炎") == []  # Rat pathology cannot leak to the mouse file.


def test_file_appearance_binding_unknown_style_and_preferences_are_distinct(tmp_path):
    known, unknown = artwork(1), artwork(2)
    unknown["files"][0]["appearance"]["binding"]["file_sha256"] = "f" * 64
    db = database(tmp_path, [known, unknown])
    assert len(search(db, "彩色小鼠")) == 1
    assert len(search(db, "偏好彩色小鼠")) == 2
    assert search(db, "mouse", filters={"detail": "detailed"}) == []
    assert len(search(db, "mouse", filters={"representation": "flat_fill", "detail": "minimal", "series": "fixture-series"})) == 1
    assert len(search(db, "mouse", filters={"preferences": {"detail": "detailed"}})) == 2


def test_unresolved_palette_is_not_a_hard_match_but_known_medium_is_retained(tmp_path):
    known, uncertain, unmeasured, unresolved = [artwork(i) for i in range(4)]
    for item in (known, uncertain, unmeasured, unresolved):
        item["files"][0]["appearance"]["color"].update(palette_traits=["monochrome_hue"], confidence="medium")
    uncertain["files"][0]["appearance"]["color"]["mode"] = "unknown"
    unmeasured["files"][0]["appearance"]["color"]["review_status"] = "not_measured"
    unresolved["files"][0]["appearance"]["color"]["review_status"] = "needs_review"
    unresolved["files"][0]["appearance"]["style"]["series"]["review_status"] = "needs_review"
    db = database(tmp_path, [known, uncertain, unmeasured, unresolved])
    for filters in ({"palette_traits": "monochrome_hue"}, {"color_mode": "color"}):
        page = search_page(db, "mouse", filters=filters)
        assert [r["selected_file"]["sha256"] for r in page["results"]] == [known["files"][0]["sha256"]]
        assert page["diagnostics"]["uncertain_candidates"]
    assert len(search(db, "mouse", filters={"series": "fixture-series"})) == 3
    assert len(search(db, "mouse", filters={"preferences": {"palette_traits": "monochrome_hue"}})) == 4


def test_seed_history_pagination_and_same_project_pins(tmp_path):
    db = database(tmp_path, [artwork(i) for i in range(120)])
    first = search_page(db, "mouse", seed="paper-a")
    repeated=search_page(db, "mouse", seed="paper-a")
    assert first["results"] == repeated["results"]
    assert first["next_cursor"] == repeated["next_cursor"]
    assert first["bindings"] == repeated["bindings"]
    second = search_page(db, "mouse", seed="paper-a", cursor=first["next_cursor"])
    assert not {r["family_id"] for r in first["results"]} & {r["family_id"] for r in second["results"]}
    assert first["diagnostics"]["rerank_windows"] == 2
    other = search_page(db, "mouse", seed="paper-b")
    assert first["results"][0]["family_id"] != other["results"][0]["family_id"]
    picked = first["results"][0]
    selected = {"family_id": picked["family_id"], "file_sha256": picked["selected_file"]["sha256"], "asset_id": picked["asset_id"]}
    pinned = search_page(db, "mouse", mode="design", seed="paper-b", project={"selected": [selected]})
    assert pinned["results"][0]["selected_file"]["sha256"] == selected["file_sha256"]
    assert pinned["results"][0]["pinned"]
    for changed in ({"seed": "changed"}, {"history": {"adopted": []}}, {"project": {"selected": [selected]}}):
        with pytest.raises(ValueError, match="Cursor inputs"):
            search_page(db, "mouse", cursor=first["next_cursor"], **{"seed": "paper-a", **changed})
    seen, cursor = set(), None
    while True:
        page = search_page(db, "mouse", limit=30, seed="all-pages", cursor=cursor)
        ids = {r["family_id"] for r in page["results"]}
        assert not seen & ids
        seen |= ids
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 120


def test_exact_mode_ignores_rotation_and_popularity_native_badges(tmp_path):
    records = [artwork(i) for i in range(20)]
    records[0]["downloads"] = 1000000
    records[0]["validation"] = {"illustrator_editability": "verified", "visual_review": "curated"}
    db = database(tmp_path, records)
    one = search_page(db, "mouse", mode="exact", seed="a")
    history = {"adopted": [{"family_id": one["results"][0]["family_id"], "status": "adopted"}]}
    two = search_page(db, "mouse", mode="exact", seed="b", history=history)
    assert [r["variant_id"] for r in one["results"]] == [r["variant_id"] for r in two["results"]]
    browsed = search_page(db, "mouse", seed="a", history={"browsed": history["adopted"]})
    assert [r["variant_id"] for r in browsed["results"]] == [r["variant_id"] for r in search_page(db, "mouse", seed="a")["results"]]


def test_scarce_only_valid_asset_remains_usable_and_unavailable_pin_is_reported(tmp_path):
    only = artwork(1)
    bad = artwork(2, species="rat", title="Rat")
    db = database(tmp_path, [only, bad])
    result = search_page(db, "mouse", seed="a")["results"][0]
    pin = {"family_id": result["family_id"], "file_sha256": result["selected_file"]["sha256"]}
    for seed in ("a", "b", "c"):
        page = search_page(db, "mouse", seed=seed, history={"adopted": [{**pin, "status": "adopted"}]})
        assert len(page["results"]) == 1 and page["diagnostics"]["scarcity"]
    page = search_page(db, "mouse", filters={"color_mode": "black_white"}, project={"selected": [pin]})
    assert page["results"] == [] and page["diagnostics"]["unavailable_project_pins"]
    with pytest.raises(ValueError, match="conflicts"):
        search_page(db, "mouse", filters={"color_mode": "color"}, mode="design", project={"style": {"color_mode": "black_white"}})


def test_sqlite_adapter_matches_direct_shared_core_for_filters_and_order(tmp_path):
    db = database(tmp_path, [artwork(i, color="color" if i % 2 else "grayscale") for i in range(20)])
    with closing(sqlite3.connect(db)) as conn:
        variants = [json.loads(row[0]) for row in conn.execute("SELECT metadata FROM file_variants")]
        version = conn.execute("SELECT value FROM index_metadata WHERE key='catalog_version'").fetchone()[0]
    for filters in ({"color_mode": "color"}, {"species": "rat"}, {"state": "normal"}, {"detail": "minimal"}):
        request = {"query": "mouse", "filters": filters, "seed": "parity", "catalog_version": version}
        direct = _search_core({"action": "search", "variants": variants, "request": request})
        wrapped = search_page(db, "mouse", filters=filters, seed="parity")
        assert [r["variant_id"] for r in direct["results"]] == [r["variant_id"] for r in wrapped["results"]]
        assert direct["bindings"] == wrapped["bindings"]


def test_compound_chinese_words_do_not_disintegrate_into_unrelated_generic_tokens():
    for chinese, required in (("脾脏", "spleen"), ("血管内皮细胞", "endothelial cell"), ("细胞膜", "cell membrane")):
        plan = _search_core({"action": "plan", "query": chinese})["plan"]
        assert len(plan["terms"]) == 1
        assert required in plan["terms"][0]


def test_named_molecular_compound_is_not_lost_and_random_network_is_not_randomization(tmp_path):
    db=database(tmp_path,[artwork(1,title="ATP",species=None),artwork(2,title="random network",species=None),artwork(3,title="blood sample tube",species=None)])
    assert [row["title"] for row in search(db,"ATP molecule structure diagram")] == ["ATP"]
    assert search(db,"randomization") == []
    assert search(db,"blood collection") == []


def test_whole_organ_qualifier_does_not_become_whole_animal(tmp_path):
    db=database(tmp_path,[artwork(1,title="whole mouse liver"),artwork(2,title="mouse liver")])
    rows=search(db,"完整小鼠肝脏")
    assert [row["title"] for row in rows] == ["whole mouse liver"]


def test_unknown_filter_and_obsolete_index_fail_explicitly(tmp_path):
    db = database(tmp_path, [artwork(1)])
    with pytest.raises(ValueError, match="Unknown filter"):
        search(db, "mouse", filters={"nonsense": "true"})
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA user_version=1")
    with pytest.raises(ValueError, match="rebuilt as v11"):
        search(db, "mouse")


def test_compact_windows_match_direct_core_across_boundaries_history_and_pins(tmp_path):
    records=[artwork(i, color="color" if i % 3 else "grayscale") for i in range(175)]
    db=database(tmp_path,records)
    with closing(sqlite3.connect(db)) as conn:
        variants=[json.loads(row[0]) for row in conn.execute("SELECT metadata FROM file_variants")]
        version=conn.execute("SELECT value FROM index_metadata WHERE key='catalog_version'").fetchone()[0]
    pin={"family_id":variants[-1]["family_id"],"file_sha256":variants[-1]["file"]["sha256"]}
    history={"adopted":[{"family_id":row["family_id"],"status":"adopted"} for row in variants[:20]]}
    for mode in ("explore","design","exact"):
        request={"query":"mouse","catalog_version":version,"mode":mode,"seed":"window-parity","history":history,"project":{"selected":[pin]},"limit":70}
        cursor=None
        seen=set()
        for _ in range(3):
            direct=_search_core({"action":"search","variants":variants,"request":{**request,"cursor":cursor}})
            wrapped=search_page(db,"mouse",limit=70,mode=mode,seed=request["seed"],history=history,project=request["project"],cursor=cursor)
            ids=[row["variant_id"] for row in direct["results"]]
            assert ids==[row["variant_id"] for row in wrapped["results"]]
            assert direct["next_cursor"]==wrapped["next_cursor"]
            assert not seen & set(ids)
            seen.update(ids)
            cursor=wrapped["next_cursor"]
        assert cursor is None and len(seen)==175


def test_large_family_never_consumes_the_candidate_window(tmp_path):
    records=[artwork(i,family="one-work-many-variants") for i in range(100)]
    records += [artwork(i) for i in range(100,180)]
    db=database(tmp_path,records)
    page=search_page(db,"mouse")
    assert page["diagnostics"]["qualified_families"]==81
    assert page["diagnostics"]["compact_candidates_loaded"]==75
    assert len({row["family_id"] for row in page["results"]})==10
    assert page["diagnostics"]["sqlite_pushdown"]["selected_detail_variants_loaded"]==10


def test_chinese_alias_substrings_do_not_turn_equipment_into_an_action(tmp_path):
    db=database(tmp_path,[artwork(1,title="Centrifuge",species=None),artwork(2,title="Centrifugation",species=None)])
    for query in ("离心分离步骤示意","centrifugation separation workflow illustration"):
        assert [row["title"] for row in search(db,query)]==["Centrifugation"]


def test_animal_object_is_not_a_species_tag_on_brains_cells_actions_or_equipment(tmp_path):
    records=[artwork(1,title="Mouse lateral view"),artwork(2,title="Mouse Brain",tags=["Side view"]),
        artwork(3,title="Mouse cell",tags=["Side view"]),artwork(4,title="Mouse head",tags=["Side view"]),
        artwork(5,title="Mouse injection",tags=["Side view"]),artwork(6,title="Computer mouse",species=None),
        artwork(7,title="Mouse unknown apparatus label",tags=["Side view"]),artwork(8,title="Mouse")]
    db=database(tmp_path,records)
    for query in ("实验小鼠侧视插画","laboratory mouse side view illustration"):
        page=search_page(db,query)
        assert [row["title"] for row in page["results"]]==["Mouse lateral view"]
        unknown={row["family_id"] for row in page["diagnostics"]["uncertain_candidates"] if row["reason"]=="unknown:view"}
        mouse_family=search(db,"mouse",filters={"file_sha256":records[-1]["files"][0]["sha256"]})[0]["family_id"]
        assert mouse_family in unknown
        assert all(row["family_id"]!=page["results"][0]["family_id"] for row in page["diagnostics"]["uncertain_candidates"])
    assert [row["title"] for row in search(db,"mouse brain side view")]==["Mouse Brain"]
    assert [row["title"] for row in search(db,"computer mouse")]==["Computer mouse"]


def test_taking_blood_is_an_action_but_a_collection_tube_is_not(tmp_path):
    db=database(tmp_path,[artwork(1,title="taking blood sample",species=None),artwork(2,title="blood collection tube",species=None),artwork(3,title="blood sample tube",species=None)])
    for query in ("采血过程示意","blood collection procedure illustration"):
        assert [row["title"] for row in search(db,query)]==["taking blood sample"]


@pytest.mark.parametrize("queries,title,decoys", [
    (("采血管插画", "blood collection tube illustration"), "Blood collection tube", ["Blood vessel", "Blood collection"]),
    (("细胞培养瓶插画", "cell culture flask illustration"), "Cell culture flask", ["Cell culture", "Animal cell"]),
    (("实验室离心管", "laboratory centrifuge tube"), "Centrifuge tube", ["Centrifuge", "Centrifugation"]),
    (("微量离心管", "microcentrifuge tube"), "Microcentrifuge tube", ["Centrifuge", "Centrifuge tube"]),
    (("实验室离心机", "laboratory centrifuge"), "Centrifuge", ["Centrifuge tube", "Centrifugation"]),
])
def test_equipment_compounds_are_objects_not_their_nested_actions(tmp_path, queries, title, decoys):
    db = database(tmp_path, [artwork(i, title=name, species=None) for i, name in enumerate([title, *decoys])])
    for query in queries:
        assert [row["title"] for row in search(db, query)] == [title]


def test_body_part_and_attached_equipment_requirements_are_not_discarded(tmp_path):
    db = database(tmp_path, [artwork(1, title="Human arm", species="human"),
        artwork(2, title="Human body", species="human"), artwork(3, title="Syringe with needle", species=None),
        artwork(4, title="Syringe", species=None), artwork(5, title="Needle", species=None)])
    for query in ("人体手臂插画", "human arm illustration"):
        assert [row["title"] for row in search(db, query)] == ["Human arm"]
    for query in ("带针注射器插画", "syringe with needle illustration"):
        assert [row["title"] for row in search(db, query)] == ["Syringe with needle"]


def test_lexical_modifiers_preserve_age_and_distinguish_consultation_from_examination(tmp_path):
    records = [artwork(1, title="Person", species="human", tags=["Adult"]),
        artwork(2, title="Person", species="human", tags=["Infant"]),
        artwork(3, title="Doctor Patient", species="human", description="Doctor consulting with patient"),
        artwork(4, title="Doctor Patient", species="human", description="Doctor examining a patient")]
    db = database(tmp_path, records)
    for query in ("成年人物", "adult person"):
        assert [row["selected_source"]["source_id"] for row in search(db, query)] == ["1"]
    for query in ("医生与患者沟通场景", "clinician patient consultation"):
        assert [row["selected_source"]["source_id"] for row in search(db, query)] == ["3"]
    for query in ("医生为患者检查的场景插画", "clinician examining a patient illustration"):
        assert [row["selected_source"]["source_id"] for row in search(db, query)] == ["4"]


def test_oral_requirement_is_not_discarded_for_unlabelled_medication(tmp_path):
    db = database(tmp_path, [artwork(1, title="Tablet", species=None),
        artwork(2, title="Pill bug", species=None)])
    assert search(db, "oral tablet") == []


def test_oral_form_alternatives_keep_grounded_medical_identity(tmp_path):
    db=database(tmp_path,[
        artwork(1,title='Drug capsule',species=None,object=['drug capsule','oral medication']),
        artwork(2,title='Drug tablet',species=None,object=['drug tablet','oral medication']),
        artwork(3,title='Drug tablet',species=None),
        artwork(4,title='Bacterial capsule',species=None,tags=['oral bacteria']),
        artwork(5,title='Tablet computer',species=None,tags=['oral presentation']),
    ])
    for query in ('口服片剂或胶囊插画','oral tablets capsules illustration','oral tablets or capsules'):
        rows=search(db,query)
        assert {r['selected_source']['source_id'] for r in rows}=={'1','2'}
    for query in ('oral tablets and capsules','口服片剂与胶囊'):
        assert search(db,query)==[]
    assert {r['selected_source']['source_id'] for r in search(db,'oral capsules')}=={'1'}


def test_whole_body_words_do_not_replace_the_primary_species_with_a_body_part(tmp_path):
    db=database(tmp_path,[artwork(1,title="Mouse whole body"),artwork(2,title="Human brain",species="human"),artwork(3,title="Human body",species="human")])
    assert [r["title"] for r in search(db,"mouse")]==["Mouse whole body"]
    assert [r["title"] for r in search(db,"human body")]==["Human body"]


def test_manual_science_evidence_is_exact_file_bound_and_conflicts_stay_unknown(tmp_path):
    good=artwork(1,title="Unspecified silhouette",species=None)
    file=good["files"][0]
    file["scientific"]={"object":["mouse"],"species":["mouse"],"view":["lateral"],"role":["whole_body"],"depiction_kind":"animal"}
    file["scientific_review_evidence"]=[{"kind":"manual_visual_review","status":"applied","original_sha256":file["sha256"],"preview_sha256":file["preview"]["sha256"],"original_archive":file["archive"],"applies_to_archive":file["archive"],"applies_to_preview_archive":file["preview"]["archive"],"reviewer":"Fixture reviewer","evidence_uri":"https://example.invalid/review.png","evidence_sha256":"f"*64,"accepted_assertions":copy.deepcopy(file["scientific"])}]
    bad=copy.deepcopy(good);bad["source_id"]="bad";bad["files"][0]["sha256"]="e"*64
    conflict=copy.deepcopy(good);conflict["source_id"]="conflict";conflict["files"][0]["sha256"]="d"*64
    conflict["files"][0]["scientific_review_evidence"][0]["original_sha256"]="d"*64
    conflict["files"][0]["scientific_conflicts"]=["species"]
    db=database(tmp_path,[good,bad,conflict])
    rows=search(db,"mouse side view",filters={"role":"whole_body"})
    assert len(rows)==1 and rows[0]["selected_file"]["sha256"]==file["sha256"]
    science=rows[0]["selected_science"]
    assert science["primary_object"]["basis"]=="manual_visual_review"
    assert science["primary_object"]["whole_body_verified"] is True
    assert science["basis"]["species"]["kind"]=="manual_visual_review"
    assert rows[0]["selected_file"]["scientific_review_evidence"]


def test_two_requested_views_require_both_on_the_selected_file(tmp_path):
    both=artwork(1,title="Human body",species="human",view=["anterior","posterior"])
    front=artwork(2,title="Human body",species="human",view="anterior")
    db=database(tmp_path,[both,front])
    for query in ("人体正面与背面示意","human body anterior posterior illustration"):
        rows=search(db,query)
        assert len(rows)==1 and rows[0]["selected_file"]["sha256"]==both["files"][0]["sha256"]


def test_biological_cell_query_excludes_electrochemical_cells_and_cell_parts(tmp_path):
    db=database(tmp_path,[artwork(1,title="Animal cell",species=None),artwork(2,title="Cyclic Voltammetry Const Temp Cell",species=None),artwork(3,title="Cell membrane",species=None),artwork(4,title="Macrophage",species=None)])
    rows=search(db,"cell")
    assert {row["title"] for row in rows}=={"Animal cell","Macrophage"}


def test_primary_organ_needs_depiction_evidence_not_a_tag_or_a_cell_modifier(tmp_path):
    records=[artwork(1,title="Heart",species=None),
        artwork(2,title="Heart cell",species=None),
        artwork(3,title="Liver",species=None,tags=["heart"]),
        artwork(4,title="Figure one",species=None,tags=["heart"]),
        artwork(5,title="Figure two",species=None,description="Illustration of a heart."),
        artwork(6,title="Figure three",species=None,object=["heart"],tags=["heart"])]
    db=database(tmp_path,records)
    page=search_page(db,"heart")
    assert {r["title"] for r in page["results"]}=={"Heart","Figure two","Figure three"}
    assert any(r["reason"]=="unknown:object" for r in page["diagnostics"]["uncertain_candidates"])
    assert [r["title"] for r in search(db,"heart cell")]==["Heart cell"]
    described=next(r for r in page["results"] if r["title"]=="Figure two")
    assert described["selected_science"]["primary_object"]["basis"]=="explicit_source_depiction_statement"


def test_parallel_requested_organs_require_both_and_match_direct_core(tmp_path):
    db=database(tmp_path,[artwork(1,title="Heart and liver",species=None),artwork(2,title="Heart",species=None,tags=["liver"]),artwork(3,title="Liver",species=None,tags=["heart"])])
    with closing(sqlite3.connect(db)) as conn:
        variants=[json.loads(row[0]) for row in conn.execute("SELECT metadata FROM file_variants")]
        version=conn.execute("SELECT value FROM index_metadata WHERE key='catalog_version'").fetchone()[0]
    for query in ("heart liver", "心脏肝脏"):
        page=search_page(db,query)
        direct=_search_core({"action":"search","variants":variants,"request":{"query":query,"catalog_version":version}})
        assert [r["title"] for r in page["results"]]==["Heart and liver"]
        assert [r["variant_id"] for r in direct["results"]]==[r["variant_id"] for r in page["results"]]


def test_specific_compound_objects_are_not_generic_cells_or_whole_organs(tmp_path):
    db=database(tmp_path,[artwork(1,title="Magnetic Cell Sorter",species=None),
        artwork(2,title="Disease Cell Cycle wheel",species=None),
        artwork(3,title="Liver fluke adult",species=None),
        artwork(4,title="Liver Lobule",species=None),
        artwork(5,title="Anemone 4 cell stage embryo",species=None),
        artwork(6,title="Liver",species=None),artwork(7,title="Macrophage",species=None)])
    assert [r["title"] for r in search(db,"liver")]==["Liver"]
    assert [r["title"] for r in search(db,"cell")]==["Macrophage"]
    for query,title in (("cell sorter","Magnetic Cell Sorter"),("cell cycle","Disease Cell Cycle wheel"),("liver fluke","Liver fluke adult"),("liver lobule","Liver Lobule")):
        assert [r["title"] for r in search(db,query)]==[title]


def test_scope_decisions_exclude_templates_but_keep_annotated_objects(tmp_path):
    # Same title: scope follows the reviewed per-file decision, never a title
    # blacklist or the presence of labels/legends/text boxes.
    included=artwork(1,title="Bone drawing with labels and legend",species=None)
    excluded=artwork(2,title=included["title"],species=None)
    pending=artwork(3,title=included["title"],species=None)
    for item,status,allowed in ((included,"included",True),(excluded,"excluded",False),(pending,"pending_review",False)):
        item["files"][0]["public_scope"]={"status":status,"public_allowed":allowed,"decision_id":"fixture-file-scope","reason":"Fixture reviewed scope decision, not a medical judgment","evidence":[{"uri":"fixture-review.json","sha256":"a"*64}],"policy_sha256":"b"*64}
        item["files"][0]["text_content"]={"status":"present","kinds":["labels","legend"],"editable_text":"unknown"}
    # prepare expects finalized identities/provenance. Feed normalized records
    # directly for the common JS guard without producing a public catalog.
    from mga_resources.catalog import normalize_record
    prepared=_search_core({"action":"prepare","records":[normalize_record(item) for item in (included,excluded,pending)]})
    page=_search_core({"action":"search","variants":prepared["variants"],"request":{"query":"bone","catalog_version":"fixture-scope"}})
    assert [r["selected_file"]["sha256"] for r in page["results"]]==[included["files"][0]["sha256"]]
    assert page["results"][0]["selected_file"]["text_content"]["editable_text"]=="unknown"
    for blocked in (excluded,pending):
        with pytest.raises(ValueError,match="scope-filtered"):
            finalize([blocked],tmp_path/blocked["source_id"])
        assert not (tmp_path/blocked["source_id"]/"catalog.jsonl").exists()
    db=database(tmp_path/"clean",[included])
    assert search(db,"bone")[0]["selected_file"]["public_scope"]["status"]=="included"


def test_internal_catalog_cannot_be_published_and_unreviewed_objects_remain_searchable(tmp_path):
    item=artwork(1)
    item["files"][0]["public_scope"]={"status":"unreviewed","public_allowed":True}
    internal=copy.deepcopy(item);internal["catalog_visibility"]="internal_diagnostic"
    with pytest.raises(ValueError,match="scope-filtered"):
        finalize([internal],tmp_path/"internal")
    db=database(tmp_path/"public",[item])
    assert len(search(db,"mouse"))==1


def test_named_molecule_noun_class_is_equivalent_in_chinese_and_english(tmp_path):
    db=database(tmp_path,[artwork(1,title="ATP",species=None),artwork(2,title="Protein",species=None)])
    pages=[search_page(db,q) for q in ("ATP分子结构示意","ATP molecule structure schematic")]
    assert all([r["title"] for r in p["results"]]==["ATP"] for p in pages)
    assert all(len(p["query_plan"]["terms"])==1 for p in pages)


def test_bone_and_cartilage_noun_classes_keep_cross_section_hard(tmp_path):
    section=artwork(1,title="Transverse section of bone with osteons",species=None)
    whole=artwork(2,title="Bone",species=None)
    cartilage=artwork(3,title="Hyaline cartilage",species=None)
    marrow=artwork(4,title="Bone marrow",species=None)
    db=database(tmp_path,[section,whole,cartilage,marrow])
    for query in ("骨组织截面插画","bone tissue cross section illustration"):
        page=search_page(db,query)
        assert [r["title"] for r in page["results"]]==[section["title"]]
        assert page["query_plan"]["requirements"]["view"]==["cross_section"]
        assert len(page["query_plan"]["terms"])==1
        assert any(r["reason"]=="unknown:view" for r in page["diagnostics"]["uncertain_candidates"])
    for query in ("软骨组织示意","cartilage tissue illustration"):
        page=search_page(db,query)
        assert [r["title"] for r in page["results"]]==[cartilage["title"]]
        assert len(page["query_plan"]["terms"])==1


def test_lung_tissue_is_not_a_whole_organ_with_a_broad_tissue_tag(tmp_path):
    organ=artwork(1,title="Human Lung",species="human",tags=["lung tissue","tissue"])
    tissue=artwork(2,title="Human Lung Tissue",species="human")
    db=database(tmp_path,[organ,tissue])
    for query in ("人体肺组织插画","human lung tissue illustration"):
        assert [r["title"] for r in search(db,query)]==[tissue["title"]]
    for query in ("人体肺部插画","human lungs illustration"):
        assert [r["title"] for r in search(db,query)]==[organ["title"]]


def test_lungs_plural_is_an_organ_not_a_human_body_request(tmp_path):
    db=database(tmp_path,[artwork(1,title="Human Lungs",species="human"),artwork(2,title="Human body",species="human")])
    for query in ("人体肺部插画","human lungs illustration"):
        page=search_page(db,query)
        assert [r["title"] for r in page["results"]]==["Human Lungs"]
        assert "lung" in page["query_plan"]["requirements"]["object"]
        assert "human" not in page["query_plan"]["requirements"]["object"]


def test_named_fruitfly_species_never_relaxes_to_an_unspecified_genus(tmp_path):
    db=database(tmp_path,[artwork(1,title="Drosophila melanogaster",species=None),
        artwork(2,title="Drosophila",species=None),
        artwork(3,title="Drosophila simulans",species="Drosophila simulans",object="drosophila")])
    pages=[search_page(db,q) for q in ("黑腹果蝇插画","Drosophila melanogaster illustration")]
    assert all([r["title"] for r in p["results"]]==["Drosophila melanogaster"] for p in pages)
    assert all(p["query_plan"]["requirements"]["species"]==["drosophila_melanogaster"] for p in pages)
    assert [r["title"] for r in search(db,"fruit fly",filters={"file_sha256":artwork(1)["files"][0]["sha256"]})]==["Drosophila melanogaster"]


def content_claim(file, value, *, official=False):
    return {"value": value, "review_status": "official_file_label" if official else "visual_reviewed",
            "binding": {"file_sha256": file["sha256"], "preview_sha256": file["preview"]["sha256"]},
            "evidence": [{"kind": "official_source_file" if official else "manual_visual_review",
                          "uri": "https://example.invalid/content-review.json", "sha256": "a" * 64,
                          "reviewer": "Fixture reviewer; synthetic filter mechanics only"}]}


def test_media_type_alias_uses_structure_before_family_and_candidate_window(tmp_path):
    vector = [artwork(i) for i in range(150)]
    vector[0]["family_hint"] = "two_formats"
    raster = artwork(900, family="two_formats")
    raster["files"][0].update(format="png", structure="raster", media_type="image/png")
    # A misleading MIME metadata value cannot override the actual structure.
    vector[1]["files"][0]["media_type"] = "raster"
    db = database(tmp_path, [*vector, raster])
    page = search_page(db, "mouse", filters={"media_type": "raster"})
    canonical = search_page(db, "mouse", filters={"structure": "raster"})
    assert page["bindings"] == canonical["bindings"]
    assert len(page["results"]) == 1
    result = page["results"][0]
    assert result["selected_file"]["sha256"] == raster["files"][0]["sha256"]
    assert result["preview"] == raster["files"][0]["preview"]
    assert result["download"] == raster["files"][0]["archive"]
    assert page["diagnostics"]["sqlite_pushdown"]["text_matched_variants"] == 151
    assert page["diagnostics"]["sqlite_pushdown"]["payload_variants_loaded"] == 1
    with pytest.raises(ValueError, match="Conflicting aliases"):
        search(db, "mouse", filters={"media_type": "raster", "structure": "pure_vector"})
    with pytest.raises(ValueError, match="Unsupported structure"):
        search(db, "mouse", filters={"media_type": "image/png"})


def test_structure_filters_are_honest_about_mixed_and_embedded_svg(tmp_path):
    structures = ["pure_vector", "raster", "mixed", "embedded_raster"]
    records = [artwork(i) for i in range(4)]
    for item, structure in zip(records, structures):
        item["files"][0].update(structure=structure, format="png" if structure == "raster" else "svg")
    unknown = artwork(10)
    unknown["files"][0].pop("structure")
    unknown["files"][0]["media_type"] = "pure_vector"
    db = database(tmp_path, [*records, unknown])
    for structure, expected in zip(structures, records):
        page = search_page(db, "mouse", filters={"media_type": structure})
        assert [r["selected_file"]["sha256"] for r in page["results"]] == [expected["files"][0]["sha256"]]
        assert page["diagnostics"]["sqlite_pushdown"]["rejections_by_condition_may_overlap"]["unknown:structure"] == 1
    assert len(search(db, "mouse", filters={"format": "svg"})) == 4


def test_content_type_requires_exact_evidence_and_keeps_unknowns_separate(tmp_path):
    drawn, photo, micrograph, ambiguous = [artwork(i, family="shared" if i < 2 else None) for i in range(4)]
    known = [(drawn, "illustration"), (photo, "photograph"), (micrograph, "micrograph"),
             (ambiguous, "photograph_or_real_micrograph")]
    for item, value in known:
        item["files"][0]["content_type"] = content_claim(item["files"][0], value, official=value == "micrograph")
    bare, wrong_file, wrong_preview, pending, unreviewed = [artwork(i) for i in range(10, 15)]
    bare["files"][0]["content_type"] = "illustration"
    for item in (wrong_file, wrong_preview, pending):
        item["files"][0]["content_type"] = content_claim(item["files"][0], "illustration")
    wrong_file["files"][0]["content_type"]["binding"]["file_sha256"] = "b" * 64
    wrong_preview["files"][0]["content_type"]["binding"]["preview_sha256"] = "c" * 64
    pending["files"][0]["content_type"]["review_status"] = "needs_review"
    unreviewed["files"][0].update(format="png", structure="raster", media_type="image/png",
                                  public_scope={"status": "unreviewed", "public_allowed": True})
    db = database(tmp_path, [*(r for r, _ in known), bare, wrong_file, wrong_preview, pending, unreviewed])
    with closing(sqlite3.connect(db)) as conn:
        variants = [json.loads(row[0]) for row in conn.execute("SELECT metadata FROM file_variants")]
        version = conn.execute("SELECT value FROM index_metadata WHERE key='catalog_version'").fetchone()[0]
    for item, content in known:
        filters = {"content_type": content}
        page = search_page(db, "mouse", filters=filters, seed="content-parity")
        direct = _search_core({"action": "search", "variants": variants, "request": {
            "query": "mouse", "filters": filters, "seed": "content-parity", "catalog_version": version}})
        assert [r["variant_id"] for r in direct["results"]] == [r["variant_id"] for r in page["results"]]
        assert [r["selected_file"]["sha256"] for r in page["results"]] == [item["files"][0]["sha256"]]
        proof = page["results"][0]["selected_file"]["content_type"]
        assert proof["value"] == content and proof["evidence"]
        assert proof["binding"]["file_sha256"] == item["files"][0]["sha256"]
        assert page["diagnostics"]["sqlite_pushdown"]["rejections_by_condition_may_overlap"]["unknown:content_type"] == 5
    # Broad search keeps uncertain content eligible, without mislabeling it.
    broad = search(db, "mouse", filters={"file_sha256": unreviewed["files"][0]["sha256"]})
    assert broad[0]["selected_file"]["content_type"]["value"] == "unknown"


def test_content_type_scope_projection_uses_only_bound_media_dimension(tmp_path):
    good, suitability, aggregate_only, bad_hash, no_proof, conflict = [artwork(i) for i in range(6)]
    for item in (good, suitability, aggregate_only, bad_hash, no_proof, conflict):
        f = item["files"][0]
        f["public_scope"] = {"status": "included", "public_allowed": True, "scope_dimension": "media_type",
                             "reviewed_dimensions": {"media_type": "included"}, "decision_id": "fixture-media-review",
                             "evidence_original_sha256": f["sha256"], "evidence_preview_sha256": f["preview"]["sha256"],
                             "reviewed_by": "Fixture visual reviewer", "reviewed_at": "2026-11-07",
                             "evidence": [{"uri": "fixture-review.json", "sha256": "a" * 64}]}
    suitability["files"][0]["public_scope"].update(scope_dimension="component_suitability", reviewed_dimensions={"component_suitability": "included"})
    aggregate_only["files"][0]["public_scope"]["scope_dimension"] = "component_suitability"
    bad_hash["files"][0]["public_scope"]["evidence_preview_sha256"] = "f" * 64
    no_proof["files"][0]["public_scope"]["evidence"] = []
    conflict["files"][0]["content_type"] = content_claim(conflict["files"][0], "photograph")
    db = database(tmp_path, [good, suitability, aggregate_only, bad_hash, no_proof, conflict])
    page = search_page(db, "mouse", filters={"content_type": "illustration"})
    assert [r["selected_file"]["sha256"] for r in page["results"]] == [good["files"][0]["sha256"]]
    assert page["results"][0]["selected_file"]["content_type"]["basis"] == "file_bound_scope_media_type_review"
    assert page["diagnostics"]["sqlite_pushdown"]["rejections_by_condition_may_overlap"]["unknown:content_type"] == 5
    assert search(db, "mouse", filters={"content_type": "photograph"}) == []


def test_old_ranking_index_is_rejected_even_with_unchanged_sql_schema(tmp_path):
    db = database(tmp_path, [artwork(1)])
    with closing(sqlite3.connect(db)) as conn, conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
        assert conn.execute("SELECT value FROM index_metadata WHERE key='ranking_version'").fetchone()[0] == "shared-search-3.5.1"
        conn.execute("UPDATE index_metadata SET value='shared-search-3.4.2' WHERE key='ranking_version'")
    with pytest.raises(ValueError, match="policy version.*changed; rebuild"):
        search(db, "mouse", filters={"media_type": "raster"})


def test_explicit_editable_vector_phrase_is_structure_not_an_fts_object(tmp_path):
    records = [artwork(i) for i in range(4)]
    for item, structure in zip(records, ["pure_vector", "mixed", "embedded_raster", "raster"]):
        item["files"][0].update(structure=structure, format="png" if structure == "raster" else "svg")
    rat = artwork(10, title="Rat", species="rat")
    db = database(tmp_path, [*records, rat])
    for query in ("editable vector mouse", "可编辑矢量小鼠", "可编辑的矢量图小鼠", "editable SVG mouse"):
        for filters in ({}, {"media_type": "pure_vector"}):
            page = search_page(db, query, filters=filters)
            assert [r["selected_file"]["sha256"] for r in page["results"]] == [records[0]["files"][0]["sha256"]]
            plan = page["query_plan"]
            assert len(plan["terms"]) == 1 and "mouse" in plan["terms"][0]
            assert plan["requirements"]["structure"] == ["pure_vector"]
            assert plan["requirements"]["species"] == ["mouse"]
            assert plan["media_interpretation"]["native_editability_verified"] is False
            assert page["results"][0]["preview"] == records[0]["files"][0]["preview"]
    with pytest.raises(ValueError, match="conflicts with explicit structure"):
        search(db, "editable vector mouse", filters={"media_type": "raster"})
    narrowed = search_page(db, "editable vector mouse", filters={"structure": ["raster", "pure_vector"]})
    assert narrowed["query_plan"]["requirements"]["structure"] == ["pure_vector"]


def test_biological_vector_and_bare_editable_are_not_discarded():
    for query in ("viral vector mouse", "expression vector", "editable mouse"):
        plan = _search_core({"action": "plan", "query": query})["plan"]
        assert "structure" not in plan["requirements"]
        assert "media_interpretation" not in plan
        preserved = "editable" if query == "editable mouse" else "vector"
        assert any(preserved in terms for terms in plan["terms"])


def test_appearance_aliases_intersect_without_silently_widening_hard_filters():
    filters = {"color": ["color", "grayscale"], "color_mode": "grayscale"}
    plan = _search_core({"action": "plan", "query": "mouse", "filters": filters})["plan"]
    assert plan["requirements"]["color_mode"] == ["grayscale"]
    with pytest.raises(ValueError, match="Conflicting aliases"):
        _search_core({"action": "plan", "query": "mouse", "filters": {"color": "color", "color_mode": "grayscale"}})
