import hashlib
import io
import unittest
import zipfile
from unittest.mock import patch

from mga_resources.http import HttpClient, SourceAccessError
from mga_resources.sources import discover, discover_archive


class FakeClient:
    def __init__(self, mapping):
        self.mapping, self.calls = mapping, []

    def get_json(self, url):
        self.calls.append(url)
        return self.mapping[url]


class SourceTests(unittest.TestCase):
    def git_fixture(self, entries, **extra):
        commit = "a" * 40
        tree = "b" * 40
        data = {
            "https://api.github.com/repos/example/art/commits/HEAD": {"sha": commit, "commit": {"tree": {"sha": tree}}},
            f"https://api.github.com/repos/example/art/git/trees/{tree}?recursive=1": {"tree": entries, "truncated": False},
        }
        data.update(extra)
        return FakeClient(data)

    def test_pins_commit_not_tree_and_excludes_non_assets(self):
        client = self.git_fixture([
            {"path": "art/HumanLiver.svg", "type": "blob", "sha": "c" * 40, "size": 100},
            {"path": "logo.svg", "type": "blob", "sha": "d" * 40},
        ])
        source = {"source_id": "example", "repository": "example/art", "path_prefixes": ["art/"], "license": "CC0-1.0"}
        records = discover(source, client)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "Human Liver")
        self.assertIn("/" + "a" * 40 + "/art/", records[0]["files"][0]["url"])
        self.assertEqual(records[0]["files"][0]["upstream_hash"], "git-blob-sha1:" + "c" * 40)

    def test_truncated_tree_fails_instead_of_claiming_full_collection(self):
        client = self.git_fixture([])
        list(client.mapping.values())[1]["truncated"] = True
        with self.assertRaisesRegex(SourceAccessError, "truncated"):
            discover({"source_id": "x", "repository": "example/art"}, client)

    def test_bioicons_preserves_author_and_holds_servier(self):
        paths = ["static/icons/cc-0/Animals/Jane_Doe/mouse.svg", "static/icons/cc-by-3.0/Organs/Servier/heart.svg"]
        client = self.git_fixture([{"path": p, "type": "blob", "sha": "c" * 40} for p in paths])
        client.mapping["https://raw.githubusercontent.com/example/art/" + "a" * 40 + "/static/icons/authors.json"] = {"Jane Doe": "https://example.org/jane"}
        records = discover({"source_id": "bioicons", "repository": "example/art", "adapter": "bioicons"}, client)
        self.assertEqual(records[0]["license"], "CC0-1.0")
        self.assertEqual(records[0]["author"], "Jane Doe")
        self.assertFalse(records[1]["distribution_allowed"])
        self.assertEqual(records[1]["license"], "CC-BY-3.0")

    def test_healthicons_variants_share_family_and_use_real_title(self):
        entries = [{"path": f"public/icons/svg/{style}/body/heart.svg", "type": "blob", "sha": "c" * 40} for style in ("filled", "outline")]
        client = self.git_fixture(entries)
        client.mapping["https://raw.githubusercontent.com/example/art/" + "a" * 40 + "/public/icons/meta-data.json"] = [{"path": "body/heart", "title": "Heart Organ", "category": "body", "tags": ["cardiac", "dhis2:heart"]}]
        source = {"source_id": "healthicons", "repository": "example/art", "metadata_kind": "healthicons", "metadata_path": "public/icons/meta-data.json"}
        records = discover(source, client)
        self.assertEqual(records[0]["title"], "Heart Organ")
        self.assertEqual(records[0]["family_hint"], records[1]["family_hint"])
        self.assertNotIn("dhis2:heart", records[0]["tags"])

    def test_archive_discovery_rejects_local_bulk_fetch(self):
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "false"}):
            with self.assertRaisesRegex(SourceAccessError, "cloud-only"):
                discover_archive({"archive_url": "https://example.org/library.zip"}, None)

    def test_file_validates_git_blob_and_reads_only_member(self):
        payload = b"<svg/>"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("images/mouse.svg", payload)
        client = HttpClient()
        client._archive_cache["https://example.org/archive.zip"] = buffer.getvalue()
        result = client.get_file({"url": "https://example.org/archive.zip", "archive_member": "images/mouse.svg"})
        self.assertEqual(result, payload)
        client.get_bytes = lambda *a, **kw: payload
        good_hash = hashlib.sha1(b"blob 6\0" + payload).hexdigest()
        self.assertEqual(client.get_file({"url": "https://example.org/x.svg", "upstream_hash": "git-blob-sha1:" + good_hash}), payload)
        with self.assertRaisesRegex(SourceAccessError, "hash mismatch"):
            client.get_file({"url": "https://example.org/x.svg", "upstream_hash": "git-blob-sha1:" + "0" * 40})

    def test_known_denied_catalog_is_not_requested(self):
        with self.assertRaisesRegex(SourceAccessError, "previously denied"):
            HttpClient().get_bytes("https://assets.biocartoon.com/catalog/assets.sqlite.gz")


if __name__ == "__main__":
    unittest.main()
