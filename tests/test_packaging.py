"""Checks on the things the Tildagon app store actually validates.

Ported from SkyScope, where a UTF-8 BOM written by a Windows tool got
`tildagon.toml` rejected with "Failed to parse contents of tildagon.toml".
Nothing local caught it: Python skips a BOM in source, `tomllib` strips it on
read, and the simulator ran the app perfectly. Only the store's parser cared.

PowerShell 5.1 puts that BOM there through `Set-Content -Encoding utf8`,
`Out-File` and plain `>` redirection -- and checking the fix with `git show >
file` re-adds one, which makes a correct fix look broken. Hence a test that
reads bytes.
"""

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BOM = b"\xef\xbb\xbf"

TEXT_SUFFIXES = (".py", ".toml", ".json", ".md", ".yml", ".yaml", ".sh")

VALID_CATEGORIES = (
    "Badge", "Music", "Media", "Apps", "Games", "Background", "Pattern",
)


def repo_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            d for d in dirnames if d not in (".git", "__pycache__", ".venv")
        ]
        for name in filenames:
            if name.endswith(TEXT_SUFFIXES):
                yield os.path.join(dirpath, name)


class TestEncoding(unittest.TestCase):
    def test_no_file_starts_with_a_byte_order_mark(self):
        offenders = []
        for path in repo_files():
            with open(path, "rb") as f:
                if f.read(3) == BOM:
                    offenders.append(os.path.relpath(path, ROOT))
        self.assertEqual(offenders, [], "BOM found; the app store cannot parse it")

    def test_every_file_is_valid_utf8(self):
        for path in repo_files():
            with open(path, "rb") as f:
                raw = f.read()
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                self.fail("%s is not UTF-8: %s" % (os.path.relpath(path, ROOT), exc))

    def test_no_file_has_windows_line_endings_in_a_shell_script(self):
        # A CRLF in an adapter script makes the shebang fail on Linux with the
        # famously unhelpful "bad interpreter: no such file or directory".
        for path in repo_files():
            if not path.endswith(".sh"):
                continue
            with open(path, "rb") as f:
                self.assertNotIn(b"\r\n", f.read(), os.path.relpath(path, ROOT))


class TestManifest(unittest.TestCase):
    def setUp(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib needs Python 3.11+")
        with open(os.path.join(ROOT, "tildagon.toml"), "rb") as f:
            self.manifest = tomllib.load(f)

    def test_required_sections(self):
        for section in ("app", "entry", "metadata"):
            self.assertIn(section, self.manifest)

    def test_category_is_one_the_store_accepts(self):
        self.assertIn(self.manifest["app"]["category"], VALID_CATEGORIES)

    def test_version_is_a_string(self):
        # The store rejects a bare 0.1 as "expected string, received bigint".
        self.assertIsInstance(self.manifest["metadata"]["version"], str)

    def test_description_within_140_characters(self):
        self.assertLessEqual(len(self.manifest["metadata"]["description"]), 140)

    def test_author_within_32_characters(self):
        self.assertLessEqual(len(self.manifest["metadata"]["author"]), 32)

    def test_store_copy_does_not_mention_ai(self):
        # Positioning, from the spec: Edgewise is a generic status semaphore.
        # The Claude Code adapter is one entry in the docs alongside CI, cron
        # and OctoPrint, and nothing the store shows says otherwise.
        text = " ".join([
            self.manifest["app"]["name"],
            self.manifest["metadata"]["description"],
        ]).lower()
        for word in ("ai", "llm", "claude", "chatgpt", "copilot"):
            self.assertNotIn(" %s " % word, " %s " % text)


if __name__ == "__main__":
    unittest.main()
