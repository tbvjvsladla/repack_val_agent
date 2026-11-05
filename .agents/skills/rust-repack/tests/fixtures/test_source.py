import unittest
from source import f


class SourceTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(f([]), "")

    def test_order_and_duplicates(self):
        self.assertEqual(f(["b:2", "a:1", "b:3"]), "b=5\na=1\n")

    def test_zero_and_boundary(self):
        self.assertEqual(f(["a:000", "b:1000"]), "a=0\nb=1000\n")

    def test_malformed(self):
        for value in ["a", ":1", "a:-1", "a:+1", "a:1:2", "é:1", "a: 1"]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid item"):
                f([value])

    def test_out_of_range(self):
        with self.assertRaisesRegex(ValueError, "invalid item"):
            f(["a:1001"])


if __name__ == "__main__":
    unittest.main()
