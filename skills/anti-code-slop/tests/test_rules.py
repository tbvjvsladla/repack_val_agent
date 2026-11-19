import unittest

from support import sc


class RuleTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = sc.load_rules()

    def test_every_rule_has_positive_and_negative(self):
        ids = [r["id"] for r in self.rules]
        self.assertEqual(len(ids), len(set(ids)), "rule ids must be unique")
        for rule in self.rules:
            with self.subTest(rule=rule["id"]):
                self.assertTrue(rule["positive"], "needs a positive example")
                self.assertTrue(rule["negative"], "needs a negative example")
                self.assertIn(rule["grade"], ("confirmed", "heuristic"))
                self.assertTrue(rule.get("manual_ref"))

    def test_rule_examples(self):
        for rule in self.rules:
            for kind in ("positive", "negative"):
                for example in rule[kind]:
                    with self.subTest(rule=rule["id"], kind=kind, code=example["code"][:40]):
                        found, _ = sc.scan_text(example["code"], example["lang"], [rule])
                        want = example.get("count", 1) if kind == "positive" else 0
                        self.assertEqual(len(found), want)

    def test_lexer_boundaries(self):
        cases = [
            # (lang, code, rule id, expected count)
            ("shell", 'echo "$#" ${#arr[@]} foo#bar\n# shellcheck disable=SC1\n', "SH-LINT-SUPPRESSION", 1),
            ("shell", 'echo "$#" ${#arr[@]} foo#bar\n', "SH-LINT-SUPPRESSION", 0),
            ("shell", "cat <<EOF\n# shellcheck disable=SC2086\nEOF\necho ok\n", "SH-LINT-SUPPRESSION", 0),
            ("shell", "cat <<'EOF'\n# TODO not a comment\nEOF\n# TODO real\n", "ANY-EXCUSE-MARKER", 1),
            ("rust", "let q = '\"';\n// TODO after a char literal\n", "ANY-EXCUSE-MARKER", 1),
            ("rust", "fn f<'a>(x: &'a str) -> &'a str { x }\n// TODO after lifetimes\n", "ANY-EXCUSE-MARKER", 1),
            ("rust", "/* outer /* inner */ TODO still comment */\nfn f() {}\n", "ANY-EXCUSE-MARKER", 1),
            ("rust", "/* outer /* inner */ still */\n#[allow(dead_code)]\nfn f() {}\n", "RS-LINT-SUPPRESSION", 1),
            ("rust", 'let s = r#"// TODO inside raw"#;\n', "ANY-EXCUSE-MARKER", 0),
            ("js", 'const r = /"/g; // eslint-disable-line no-useless-escape\n', "JS-LINT-SUPPRESSION", 1),
            ("js", "const r = /\\/\\//g;\nconst x = 1;\n", "JS-COMMENTED-CODE", 0),
            ("js", "const r = /\\/\\/ TODO/;\n", "ANY-EXCUSE-MARKER", 0),
            ("js", "const t = `a ${b} // TODO`;\n", "ANY-EXCUSE-MARKER", 0),
            ("js", "const a = b / c; // TODO divide\n", "ANY-EXCUSE-MARKER", 1),
            ("python", 's = "# TODO in a string"\nx = 1  # TODO real\n', "ANY-EXCUSE-MARKER", 1),
            ("python", 'f"{x} # TODO in an f-string"\n', "ANY-EXCUSE-MARKER", 0),
            ("rust", 'const USAGE: &str = "usage:\n  #[allow(dead_code)]\n  // ==========";\n// let mut sum = 0;\n// for e in es { sum += e; }\nfn f() {}\n', "RS-COMMENTED-CODE", 1),
            ("rust", 'const USAGE: &str = "usage:\n  #[allow(dead_code)]\n";\nfn f() {}\n', "RS-LINT-SUPPRESSION", 0),
            ("rust", 'const A: &str = "a\nb";\n// TODO after a multi-line string\n', "ANY-EXCUSE-MARKER", 1),
            ("shell", "x=$(cat <<EOF\n# ##############################\nit's text\nEOF\n)\n# shellcheck disable=SC2086\nrm $f || true\n", "SH-LINT-SUPPRESSION", 1),
            ("shell", "x=$(cat <<EOF\n# ##############################\nEOF\n)\n", "ANY-BANNER-COMMENT", 0),
            ("shell", "read a <<< y\n# shellcheck disable=SC2086\n", "SH-LINT-SUPPRESSION", 1),
            ("shell", "(( m = 1 << bit ))\n# shellcheck disable=SC2086\n", "SH-LINT-SUPPRESSION", 1),
        ]
        by_id = {r["id"]: r for r in self.rules}
        for lang, code, rule_id, count in cases:
            with self.subTest(lang=lang, code=code[:40]):
                found, _ = sc.scan_text(code, lang, [by_id[rule_id]])
                self.assertEqual(len(found), count)


if __name__ == "__main__":
    unittest.main()
