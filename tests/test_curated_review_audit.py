import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("curated_audit", Path(__file__).resolve().parents[1]/"scripts/audit_curated_review.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    source = {"source_id": "source-1", "source_url": "https://source/item"}
    archive = {"url": "https://release/archive", "member": "original.svg"}
    pa = {"url": "https://release/archive", "member": "preview.png"}
    item = {"sha256": "original", "archive": archive, "preview": {"sha256": "preview", "archive": pa},
            "structure": "pure_vector", "format": "svg", "asset_ids": ["asset"], "family_ids": ["family"], "provenance": [source]}
    candidate = {"candidate_id": "asset:file-0", "asset_id": "asset", "family_id": "family", "original_sha256": "original",
                 "original_archive": archive, "preview": item["preview"], "type": "pure_vector", "preview_status": "viewable"}
    evidence = {"original_sha256": "original", "original_archive": archive, "preview_sha256": "preview", "preview_archive": pa,
                "observations": "Visible cell nucleus", "visual_verdict": "usable_with_limitations", "tile": 1, **source}
    doc = {"items": [evidence], "review_sheet_url": "https://release/sheet", "review_sheet_sha256": "sheet"}
    row = {**evidence, "candidate_id": "asset:file-0", "asset_id": "asset", "family_id": "family", "structure": "pure_vector", "format": "svg",
           "usable": True, "source_evidence_checked": True, "visual_evidence_notes": "Visible cell nucleus", "recommended_use": "Cell concept",
           "limitations": ["No disease assertion"], "category_evidence": {"observed": "Visible cell nucleus"}, "source_records": [source],
           "exact_prior_review": {"path": "review.json", "sha256": "doc", "json_pointer": "/items/0", "observations": evidence["observations"]},
           "viewed_sheet": {"uri": doc["review_sheet_url"], "sha256": "sheet", "tile": 1}, "categories": ["cells"]}
    review = {"catalog_binding": {"sha256": "catalog"}, "independent_from_metadata_matcher": True, "reviewer": "Observer",
              "mapping_candidates": {"asset:file-0": candidate}, "curated": [row], "category_counts": {"cells": 1}, "distinct_families": 1}
    return review, {"catalog_sha256": "catalog", "files": [item]}, {"review.json": ("doc", doc)}


class CuratedAuditTest(unittest.TestCase):
    def test_bound_partial_review_does_not_pass_sixty(self):
        result = module.audit(*fixture())
        self.assertTrue(result["binding_passed"])
        self.assertFalse(result["full_curated_coverage_requirement_passed"])

    def test_same_family_cannot_count_twice(self):
        review, manifest, docs = fixture()
        review["curated"].append(copy.deepcopy(review["curated"][0]))
        result = module.audit(review, manifest, docs)
        self.assertFalse(result["binding_passed"])
        self.assertEqual(result["distinct_families"], 1)

    def test_excluded_original_cannot_reappear_via_prior_review(self):
        review, manifest, docs = fixture()
        manifest["files"] = []
        self.assertFalse(module.audit(review, manifest, docs)["binding_passed"])

    def test_changed_preview_or_source_document_is_rejected(self):
        review, manifest, docs = fixture()
        review["curated"][0]["preview_archive"] = {"url": "https://different", "member": "preview.png"}
        self.assertFalse(module.audit(review, manifest, docs)["binding_passed"])
        review, manifest, docs = fixture()
        docs["review.json"] = ("changed", docs["review.json"][1])
        self.assertFalse(module.audit(review, manifest, docs)["binding_passed"])


if __name__ == "__main__":
    unittest.main()
