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
    with pytest.raises(ValueError, match="rebuilt as v4"):
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
