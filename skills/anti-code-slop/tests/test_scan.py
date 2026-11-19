import tempfile
import unittest
from pathlib import Path

from support import sc, small_target


class Scan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = sc.load_rules()

    def ids(self, code, lang="rust"):
        found, _ = sc.scan_text(code, lang, self.rules, "f")
        return [s["id"] for s in found if s["rule"] == "RS-LINT-SUPPRESSION"]

    def test_signal_id_stable_and_context_bound(self):
        base = "fn a() {}\n#[allow(dead_code)]\nfn b() {}\n"
        shifted = "// a new line above\n\n" + base
        self.assertEqual(self.ids(base), self.ids(shifted), "unrelated lines above must not change the id")
        two = "#[allow(dead_code)]\nfn b() {}\n#[allow(dead_code)]\nfn c() {}\n"
        first, second = self.ids(two)
        self.assertNotEqual(first.split(":")[2], second.split(":")[2], "different next lines give different contexts")
        twins = "fn wrap() {\n    mark();\n    #[allow(unused)]\n    let x = 1;\n    mark();\n    #[allow(unused)]\n    let x = 1;\n    mark();\n}\n"
        found, _ = sc.scan_text(twins, "rust", self.rules, "f")
        pair = [s for s in found if s["rule"] == "RS-LINT-SUPPRESSION"]
        self.assertEqual(pair[0]["context"], pair[1]["context"])
        self.assertEqual([s["id"].rsplit(":", 1)[1] for s in pair], ["0", "1"])

    def test_sibling_and_above_edits_keep_id(self):
        before = "// =========== banner ===========\n#[allow(dead_code)]\nfn unused() {}\n#[allow(non_snake_case)]\npub struct Wire {}\n"
        after = "#[allow(non_snake_case)]\npub struct Wire {}\n"
        self.assertEqual(self.ids(before)[-1], self.ids(after)[0])
        nested = "impl A {\n    #[allow(unused)]\n    fn a() {}\n}\nimpl B {\n    #[allow(unused)]\n    fn a() {}\n}\n"
        first, second = self.ids(nested)
        self.assertNotEqual(first.split(":")[2], second.split(":")[2], "the enclosing item separates the twins")

    def test_main_guard_signal_ignores_sibling_signature(self):
        before = ("def b(path):  # noqa: ANN001\n    return path\n\n\nif __name__ == \"__main__\":\n    try:\n        b(1)\n"
                  "    except Exception:  # noqa: BLE001\n        raise\n")
        after = before.replace("def b(path):  # noqa: ANN001", "def b(path: str) -> None:")
        ids = lambda code: [s["id"] for s in sc.scan_text(code, "python", self.rules, "f")[0] if "BLE001" in s["text"]]
        self.assertEqual(ids(before), ids(after))

    def test_star_banner_and_non_head_legal(self):
        by_id = {r["id"]: r for r in self.rules}
        banner = [by_id["ANY-BANNER-COMMENT"]]
        self.assertEqual(len(sc.scan_text("/*******************/\nfn f() {}\n", "rust", banner)[0]), 1)
        self.assertEqual(len(sc.scan_text("/*******************/\nexport const a = 1;\n", "js", banner)[0]), 1)
        mid = "x = 1\n# ==========\n# copyright notes below\ny = 2\n"
        self.assertEqual(len(sc.scan_text(mid, "python", banner)[0]), 1)

    def test_doc_heading_underline_is_neither_banner_nor_contract(self):
        by_id = {r["id"]: r for r in self.rules}
        banner = [by_id["ANY-BANNER-COMMENT"]]
        rust = "//! Ledger\n//! ==========\n\n/// Notes\n/// ----------\npub fn f() {}\n"
        self.assertEqual(sc.scan_text(rust, "rust", banner)[0], [])
        texts = [e["text"] for e in sc.protected_comments(rust, "rust", sc.constraint_regex(self.rules))]
        self.assertEqual(texts, ["Ledger", "Notes"])
        js = "/*******************/\nexport function f() {}\n"
        self.assertEqual(len(sc.scan_text(js, "js", banner)[0]), 1)
        self.assertEqual(sc.protected_comments(js, "js", sc.constraint_regex(self.rules)), [])

    def test_unchecked_language_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = small_target(Path(tmp), {"a.go": "package main // TODO\n", "b.py": "x = 1  # TODO\n",
                                              "tool": "#!/bin/bash\n# TODO\n"})
            result = sc.scan_tree(target, self.rules)
            self.assertEqual(result["files"]["unchecked"], ["a.go"])
            self.assertEqual(result["files"]["checked"], {"b.py": "python", "tool": "shell"})

    def test_exclusion_uses_relative_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "target" / "build" / "project"
            (root / "src").mkdir(parents=True)
            (root / "src" / "a.py").write_text("x = 1  # noqa\n")
            (root / "target").mkdir()
            (root / "target" / "gen.py").write_text("x = 1  # noqa\n")
            (root / "build").mkdir()
            (root / "build" / "out.py").write_text("x = 1  # noqa\n")
            hashes = sc.hash_tree(root)
            self.assertIn("src/a.py", hashes)
            self.assertIn("build/out.py", hashes, "build/ is hashed so scope changes are seen")
            self.assertNotIn("target/gen.py", hashes)
            result = sc.scan_tree(root, self.rules)
            self.assertEqual([s["file"] for s in result["signals"]], ["src/a.py"])
            self.assertEqual(result["files"]["excluded"], {"build/out.py": "vendor_or_build_dir"})

    def test_generated_binary_and_lock_files_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = small_target(Path(tmp), {"gen.py": "# @generated by tool\nx = 1  # noqa\n",
                                              "Cargo.lock": "# x\n"})
            (target / "blob.py").write_bytes(b"\0\0# noqa\n")
            result = sc.scan_tree(target, self.rules)
            self.assertEqual(result["files"]["excluded"],
                             {"Cargo.lock": "lock_file", "blob.py": "binary", "gen.py": "generated"})

    def test_protected_comment_classes(self):
        code = ("// SPDX-License-Identifier: MIT\n// see https://github.com/o/r/issues/7\n"
                "// IMPORTANT: keep order\n/// Public docs.\npub fn f() {}\n/// Private docs.\nfn g() {}\n")
        entries = sc.protected_comments(code, "rust", sc.constraint_regex(self.rules))
        classes = sorted((e["class"], e["text"]) for e in entries)
        self.assertEqual(classes, [("constraint", "IMPORTANT: keep order"), ("issue_rfc", "see https://github.com/o/r/issues/7"),
                                   ("legal", "SPDX-License-Identifier: MIT"), ("public_contract", "Public docs.")])
        py = '"""Module doc."""\ndef public():\n    """Public doc."""\ndef _private():\n    """Hidden."""\n'
        texts = sorted(e["text"] for e in sc.protected_comments(py, "python", sc.constraint_regex(self.rules)))
        self.assertEqual(texts, ["Module doc.", "Public doc."])

    def test_scan_is_deterministic(self):
        from acceptance_spec import SEEDED
        a = sc.scan_tree(SEEDED, self.rules)
        b = sc.scan_tree(SEEDED, sc.load_rules())
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
