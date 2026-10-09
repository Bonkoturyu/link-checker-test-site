"""Mutation tests: a validator that accepts every input must fail this suite."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "tests" / "validate_fixture.py"


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="link-fixture-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "site", self.root / "site")
        shutil.copyfile(ROOT / "expected-results.json", self.root / "expected-results.json")

    def run_validator(self):
        return subprocess.run(
            [sys.executable, str(VALIDATOR), "--root", str(self.root)],
            capture_output=True, text=True, timeout=20, check=False,
        )

    def assert_rejected(self, *messages):
        result = self.run_validator()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for message in messages:
            self.assertIn(message, result.stdout, result.stdout + result.stderr)

    def replace(self, name, old, new):
        path = self.root / "site" / name
        content = path.read_text(encoding="utf-8")
        self.assertIn(old, content)
        path.write_text(content.replace(old, new), encoding="utf-8")

    def test_unchanged_fixture_passes(self):
        result = self.run_validator()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("9 working, 2 missing_resource, 1 missing_fragment", result.stdout)
        self.assertIn("PASS case 02: missing_resource (HTTP 404)", result.stdout)
        self.assertIn("PASS case 11: missing_resource (HTTP 404)", result.stdout)
        self.assertIn("PASS case 04: missing_fragment (HTTP 200)", result.stdout)

    def test_deleted_good_file_fails_with_real_404(self):
        (self.root / "site" / "ok.html").unlink()
        self.assert_rejected("site inventory differs", "case 1: expected working, got missing_resource (HTTP 404)")

    def test_filled_missing_target_fails_with_real_200(self):
        (self.root / "site" / "missing.html").write_text("<!doctype html><html></html>", encoding="utf-8")
        self.assert_rejected("site inventory differs", "case 2: expected missing_resource, got working (HTTP 200)")

    def test_nonexistent_good_anchor_fails(self):
        self.replace("ok.html", 'id="section-1"', 'id="different"')
        self.assert_rejected("case 3: expected working, got missing_fragment (HTTP 200)")

    def test_extra_link_occurrence_fails(self):
        self.replace("index.html", "</main>", '<a href="ok.html">extra</a></main>')
        self.assert_rejected("ordered link occurrences differ: expected exactly 12, found 13")

    def test_changed_japanese_anchor_fails(self):
        self.replace("日本語.html", 'id="項目"', 'id="別項目"')
        self.assert_rejected("case 6: expected working, got missing_fragment (HTTP 200)",
                             "case 12: expected working, got missing_fragment (HTTP 200)")

    def test_duplicate_link_order_matters(self):
        self.replace("index.html", 'href="missing.html"', 'href="ok.html"')
        self.assert_rejected("ordered link occurrences differ: expected exactly 12, found 12")

    def test_absolute_project_path_is_rejected(self):
        self.replace("nested/page.html", 'href="../ok.html"', 'href="/ok.html"')
        self.assert_rejected("only project-relative URLs are allowed")

    def test_external_link_is_rejected_without_fetching_it(self):
        self.replace("index.html", 'href="ok.html"', 'href="https://example.invalid/"')
        self.assert_rejected("only project-relative URLs are allowed")

    def test_scripts_and_forms_are_rejected(self):
        self.replace("index.html", "</main>", "<script></script><form></form></main>")
        self.assert_rejected("forbidden or unsupported HTML tag: script", "forbidden or unsupported HTML tag: form")

    def test_unexpected_site_file_is_rejected(self):
        (self.root / "site" / "unexpected.txt").write_text("extra", encoding="utf-8")
        self.assert_rejected("site inventory differs")

    def test_symlink_is_rejected_before_serving(self):
        (self.root / "site" / "linked.txt").symlink_to("assets/sample.txt")
        self.assert_rejected("site/ must not contain symlinks or special files")

    def test_manifest_changes_are_rejected(self):
        manifest = self.root / "expected-results.json"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace('"working": 9', '"working": 8'), encoding="utf-8")
        self.assert_rejected("expected-results.json differs", "expected-results.json bytes changed")

    def test_text_only_drift_is_rejected(self):
        self.replace("ok.html", "架空の文章", "変更した文章")
        self.assert_rejected("ok.html: approved fixture bytes changed")


if __name__ == "__main__":
    unittest.main()
