import hashlib
import io
import json
import unittest
import zipfile
from unittest.mock import patch

from mga_resources.http import HttpClient, SourceAccessError
from mga_resources.sources import discover, discover_archive
from mga_resources.web_sources import parse_rsc
from mga_resources.archive_metadata import load_arcadia_metadata, enrich_arcadia


class FakeClient:
    def __init__(self, mapping):
        self.mapping, self.calls = mapping, []

    def get_json(self, url):
        self.calls.append(url)
        return self.mapping[url]

    def get_text(self, url):
        self.calls.append(url)
        return self.mapping[url]

    def post_text(self, url, payload, *, headers):
        self.calls.append((url, payload))
        return self.mapping[(url, payload)]


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

    def test_arcadia_names_credits_and_styles_share_true_family(self):
        url = "https://example.org/arcadia/"
        records = [{"sci": "Agaricus bisporus", "common": "White button mushroom",
                    "notes": "Modified from PhyloPic silhouette, uploaded by T. Michael Keesey.",
                    "files": [{"name": "Agaricus-bisporus-raster.png"}, {"name": "Agaricus-bisporus-silhouette.svg"}]}]
        client = FakeClient({url: "window.RECORDS = " + json.dumps(records) + ";"})
        source = {"source_id": "arcadia", "source_url": "https://zenodo.org/records/18395998", "metadata_url": url,
                  "filename_aliases": {"Agaricus-bisporis": "Agaricus-bisporus"}}
        metadata = load_arcadia_metadata(source, client)
        raster, vector = {"family_id": "old-derived-family", "family_hint": "old/archive-path"}, {}
        enrich_arcadia(raster, "Tricolor + stroke PNGs/Agaricus-bisporis-tricolorstroke.png", source, metadata)
        enrich_arcadia(vector, "Silhouette SVGs/Agaricus-bisporus-silhouette.svg", source, metadata)
        self.assertEqual(raster["family_hint"], vector["family_hint"])
        self.assertIn("White button mushroom", raster["title"])
        self.assertIn("T. Michael Keesey", raster["attribution"])
        self.assertEqual(raster["scientific_name"], "Agaricus bisporus")
        self.assertEqual(raster["species"], "Agaricus bisporus")
        self.assertNotIn("family_id", raster)

    def test_arcadia_body_parts_are_not_merged_into_whole_organism(self):
        url = "https://example.org/arcadia/"
        data = [{"sci": "Homo sapiens", "common": "Human", "notes": "", "files": [
            {"name": "Homo-sapiens-raster.png"}, {"name": "Homo-sapiens-neuron.png"}]}]
        source = {"source_id": "arcadia", "source_url": url, "metadata_url": url}
        metadata = load_arcadia_metadata(source, FakeClient({url: "window.RECORDS=" + json.dumps(data) + ";"}))
        whole, neuron = {}, {}
        enrich_arcadia(whole, "Raster PNGs/Homo-sapiens-raster.png", source, metadata)
        enrich_arcadia(neuron, "Raster PNGs/Homo-sapiens-neuron.png", source, metadata)
        self.assertNotEqual(whole["family_hint"], neuron["family_hint"])
        self.assertEqual(whole["organism_family"], neuron["organism_family"])
        self.assertIn("neuron", neuron["title"])

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

    def test_authorless_bioicons_record_is_counted_and_held(self):
        client = self.git_fixture([{"path": "static/icons/cc-by-4.0/General_items/article.svg", "type": "blob", "sha": "c" * 40}])
        client.mapping["https://raw.githubusercontent.com/example/art/" + "a" * 40 + "/static/icons/authors.json"] = {}
        records = discover({"source_id": "bioicons", "repository": "example/art", "adapter": "bioicons"}, client)
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]["author"])
        self.assertFalse(records[0]["distribution_allowed"])

    def test_rsc_utf8_byte_lengths_and_embedded_newline(self):
        text = "蛋白\nLundström"
        stream = f'0:{{"a":"$@1"}}\n2:T{len(text.encode()):x},{text}1:{{"description":"$2"}}\n'
        self.assertEqual(parse_rsc(stream)[1]["description"], text)
        with self.assertRaisesRegex(ValueError, "Truncated"):
            parse_rsc("2:Tff,abc")

    def nih_fixture(self, hits, found=None):
        url = "https://bioart.niaid.nih.gov/discover"
        script = "/_next/static/chunks/app/discover/page-abc.js"
        response = {"hits": {"found": len(hits) if found is None else found, "hit": hits}}
        mapping = {url: f'<script src="{script}"></script>',
                   "https://bioart.niaid.nih.gov" + script: 'createServerReference)("abcd",J.callServer,void 0,J.findSourceMapURL,"discoverSearch")',
                   (url, json.dumps(["type:bioart?start=0&size=100"])): "1:" + json.dumps(response) + "\n"}
        for hit in hits:
            sid = hit["fields"]["id"][0]
            mapping["https://bioart.niaid.nih.gov/bioart/" + sid] = f"BIOART-{int(sid):06d}"
        return FakeClient(mapping)

    def test_nih_preserves_variants_and_holds_unversioned_ccby(self):
        hits = [{"fields": {"id": ["571"], "title": ["Kidney"], "license": ["CC-BY"],
                            "creator": ["A. Artist"], "filesinfo": ["All:1,2,3|SVG:1,2|PNG:3|AI:4"]}}]
        record = discover({"source_id": "nih-bioart", "adapter": "nih_bioart"}, self.nih_fixture(hits))[0]
        self.assertEqual(len(record["files"]), 3)
        self.assertEqual(record["author"], "A. Artist")
        self.assertFalse(record["distribution_allowed"])
        self.assertEqual(record["license"], "CC-BY")

    def test_nih_resolves_exact_item_license_link(self):
        hits = [{"fields": {"id": ["572"], "title": ["Skin"], "license": ["CC-BY"]}}]
        client = self.nih_fixture(hits)
        client.mapping["https://bioart.niaid.nih.gov/bioart/572"] += '<a href="https://creativecommons.org/licenses/by/4.0/">License</a>'
        record = discover({"source_id": "nih-bioart", "adapter": "nih_bioart"}, client)[0]
        self.assertEqual(record["license"], "CC-BY-4.0")
        self.assertEqual(record["license_original"], "CC-BY")
        self.assertNotEqual(record.get("distribution_allowed"), False)

    def test_scidraw_documented_alias_preserves_original_license(self):
        item = {"id": "id1", "name": "Mouse", "slug": "mouse", "image_url": "https://scidraw.io/files/m.svg", "license": "cc-by", "primary_author": {"full_name": "Artist"}}
        client = FakeClient({"https://scidraw.io/api/v1/drawings/?page=1": {"results": [item], "next": None}})
        source = {"source_id": "scidraw", "adapter": "scidraw", "license_aliases": {"cc-by": {"license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/", "evidence_url": "https://scidraw.io/licensing"}}}
        record = discover(source, client)[0]
        self.assertEqual(record["license"], "CC-BY-4.0")
        self.assertEqual(record["license_original"], "cc-by")
        self.assertNotEqual(record.get("distribution_allowed"), False)

    def test_nih_duplicate_pagination_is_failure(self):
        hit = {"fields": {"id": ["113"], "title": ["Cell"], "license": ["Public Domain"]}}
        with self.assertRaisesRegex(SourceAccessError, "duplicate"):
            discover({"source_id": "nih-bioart", "adapter": "nih_bioart"}, self.nih_fixture([hit, hit]))

    def test_nih_brush_without_image_is_explicit_scope_exclusion(self):
        fields = {"id": ["9"], "title": ["Actin Filament Brush"], "license": ["Public Domain"],
                  "filesinfo": ["All:1,2|AI:1|JPG:2"], "ontologykey": ["Proteins", "Brush"],
                  "ontologytype": ["Bioart Category", "Image Type"]}
        source = {"source_id": "nih-bioart", "adapter": "nih_bioart"}
        record = discover(source, self.nih_fixture([{"fields": fields}]))[0]
        self.assertEqual(record["scope_status"], "excluded")
        self.assertEqual(record["exclusion_reason"], "brush_without_downloadable_artwork")
        fields["ontologykey"] = ["Proteins", "Vector"]
        record = discover(source, self.nih_fixture([{"fields": fields}]))[0]
        self.assertNotIn("scope_status", record)
        self.assertEqual(record["files"], [])

    def test_swissbiopics_uses_svg_endpoint_not_thumbnail(self):
        client = FakeClient({"https://www.swissbiopics.org/": '<div class="inj" data-name="Animal_cells" data-title="Animal cell" data-target="name/Animal_cell"></div>'})
        rows = discover({"source_id": "swissbiopics", "adapter": "swissbiopics"}, client)
        self.assertEqual(rows[0]["files"][0]["url"], "https://www.swissbiopics.org/api/image/Animal_cells.svg")
        self.assertEqual(len(client.calls), 1)

    def test_reactome_follows_catalog_pages_and_checks_count(self):
        client = FakeClient({"https://reactome.org/icon-lib": '2 unique icons <a href="/icon-lib/human_tissue">Human tissue</a>',
                             "https://reactome.org/icon-lib/human_tissue": '<a href="/content/detail/R-ICO-1"><img src="aorta.png"></a><a href="/content/detail/R-ICO-1">Aorta</a><a href="/icon-lib/Human tissue?page=2">Next</a>',
                             "https://reactome.org/icon-lib/Human%20tissue?page=2": '<a href="/content/detail/R-ICO-2">Heart</a><a href="/content/detail/R-ICO-1">Aorta</a>'})
        rows = discover({"source_id": "reactome", "adapter": "reactome"}, client)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "Aorta")
        client.mapping["https://reactome.org/icon-lib"] = '3 unique icons <a href="/icon-lib/human_tissue">Human tissue</a>'
        with self.assertRaisesRegex(SourceAccessError, "incomplete"):
            discover({"source_id": "reactome", "adapter": "reactome"}, client)

    def test_scidraw_upgrades_only_same_host_pagination(self):
        client = FakeClient({"https://scidraw.io/api/v1/drawings/?page=1": {"results": [], "next": "http://scidraw.io/api/v1/drawings/?page=2"},
                             "https://scidraw.io/api/v1/drawings/?page=2": {"results": [], "next": None}})
        self.assertEqual(discover({"source_id": "scidraw", "adapter": "scidraw"}, client), [])
        self.assertTrue(client.calls[-1].startswith("https://"))
        client.mapping[client.calls[0]]["next"] = "https://unexpected.example/page=2"
        with self.assertRaisesRegex(SourceAccessError, "host"):
            discover({"source_id": "scidraw", "adapter": "scidraw"}, client)

    def togo_fixture(self, entries):
        source = {"source_id": "togo", "adapter": "togo", "source_url": "https://togotv.dbcls.jp/en/pics.html",
                  "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                  "license_evidence_url": "https://dbarchive.biosciencedbc.jp/en/togo-pic/lic.html"}
        url = "https://togotv-api.dbcls.jp/api/entries?target=pictures&from=1&rows=200"
        client = FakeClient({url: {"data": entries, "numfound": len(entries), "last_page": 1, "start": 0}})
        return source, client

    def test_togo_keeps_item_author_and_exact_cc_license(self):
        source, client = self.togo_fixture([{"TogoTV_Image_ID": 12, "name_en": "White mouse", "author": "Artist A",
            "license": "https://creativecommons.org/licenses/by/4.0/", "png": "White mouse.png", "svg": "White mouse.svg", "scientific_name": "Mus musculus", "tax_id": "10090"}])
        record = discover(source, client)[0]
        self.assertEqual(record["author"], "Artist A")
        self.assertEqual(record["license"], "CC-BY-4.0")
        self.assertIn("White%20mouse.svg", record["files"][0]["url"])
        self.assertEqual(len(record["files"]), 2)

    def test_togo_malformed_filename_license_uses_explicit_collection_evidence(self):
        source, client = self.togo_fixture([{"TogoTV_Image_ID": 42, "name_en": "Tobacco", "license": "202304_Common_tobacco.ai", "png": "tobacco.png"}])
        record = discover(source, client)[0]
        self.assertEqual(record["license"], "CC-BY-4.0")
        self.assertEqual(record["license_original"], "202304_Common_tobacco.ai")
        self.assertEqual(record["license_evidence"], source["license_evidence_url"])
        self.assertTrue(record["discovery_warnings"])

    def test_togo_partial_inventory_is_failure(self):
        source, client = self.togo_fixture([])
        next(iter(client.mapping.values()))["numfound"] = 1
        with self.assertRaisesRegex(SourceAccessError, "empty Togo page"):
            discover(source, client)

    def test_togo_svg_variant_zip_expands_only_in_cloud(self):
        source, client = self.togo_fixture([{"TogoTV_Image_ID": 99, "name_en": "ChIP icons", "license": "https://creativecommons.org/licenses/by/4.0/", "svg": "icons_svg.zip", "png": "icons.png"}])
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "false"}):
            with self.assertRaisesRegex(SourceAccessError, "cloud-only"):
                discover(source, client)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("icons/red.svg", b"<svg/>")
            archive.writestr("icons/blue.svg", b"<svg/>")
            archive.writestr("README.txt", "not artwork")
        client.get_archive = lambda url: zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}):
            record = discover(source, client)[0]
        self.assertEqual(len(record["files"]), 3)
        self.assertEqual(record["files"][0]["archive_member"], "icons/blue.svg")


if __name__ == "__main__":
    unittest.main()
