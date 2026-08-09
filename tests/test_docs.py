"""Checks that keep the documentation honest.

The repo is a deliverable in its own right (spec S13): the acceptance test for
M4 is that a stranger with a badge and mosquitto-clients gets a lit edge in five
minutes using only the README. Documentation that has drifted from the code
fails that test just as thoroughly as documentation that was never written, and
it fails it invisibly -- nothing else in this repo notices when a limit changes
in `security.py` and the protocol page keeps quoting the old number.

So the facts the docs state about the code are asserted here, and the promises
the docs make about the adapters ("no installer downloads anything") are
asserted against the scripts rather than left as prose.

None of this validates prose. It validates the handful of things a reader will
copy, paste, and be misled by.
"""

import json
import os
import re
import unittest

from edgewise import model, security

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAPTERS = os.path.join(ROOT, "adapters")
DOCS = os.path.join(ROOT, "docs")

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
ENV_VAR = re.compile(r"EDGEWISE_[A-Z0-9]+(?:_[A-Z0-9]+)*")
TABLE_STATE = re.compile(r"^\|\s*`([a-z_/ ]+)`")
# The shapes that fetch and run code. Word-bounded, or `| sha256sum` reads as a
# pipe into a shell.
FETCHES = re.compile(r"\b(curl|wget)\b|\|\s*(sh|bash)\b|\beval\b")


def walk(base, suffixes):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for name in filenames:
            if not suffixes or name.endswith(suffixes):
                yield os.path.join(dirpath, name)


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def markdown_files():
    return sorted(walk(ROOT, (".md",)))


DOCSTRING = re.compile('"' * 3 + r"(?:.|\n)*?" + '"' * 3
                       + "|" + "'" * 3 + r"(?:.|\n)*?" + "'" * 3)


def code_only(text):
    """Comments and docstrings removed.

    The scripts *talk about* the things they promise not to do -- "if this ever
    grows a curl, do not run it" is the sentence a reviewer should find in
    there, and the HTTP bridge's docstring shows the `curl` commands a user is
    meant to type. Scanning prose would make documenting a tool the one thing
    that trips the test for using it.
    """
    text = DOCSTRING.sub("", text)
    return "\n".join(line for line in text.split("\n")
                     if not line.lstrip().startswith("#"))


def table_states(text, heading):
    """The first column of the state table under `heading`.

    Returns the set of names, so a state added to the code and forgotten in a
    table is a failure rather than a slow divergence nobody reads.
    """
    lines = text.split("\n")
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise AssertionError("no %r heading found" % heading)
    found = set()
    for line in lines[start:]:
        if line.startswith("## ") and found:
            break
        match = TABLE_STATE.match(line)
        if match:
            # "clear / TTL expiry" and the like: the state is the first word.
            found.add(match.group(1).split("/")[0].strip())
    return found


class TestLinks(unittest.TestCase):
    def test_every_relative_link_resolves(self):
        broken = []
        for path in markdown_files():
            base = os.path.dirname(path)
            for target in LINK.findall(read(path)):
                target = target.split()[0]  # drop a "title" suffix
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                target = target.split("#")[0]
                if not target:
                    continue
                resolved = os.path.normpath(os.path.join(base, target))
                if not os.path.exists(resolved):
                    broken.append("%s -> %s" % (
                        os.path.relpath(path, ROOT), target))
        self.assertEqual(broken, [], "broken relative links")


class TestStatesMatchTheCode(unittest.TestCase):
    EXPECTED = set(model.STATES) | {model.STATE_CLEAR}

    def test_readme_state_table(self):
        self.assertEqual(
            table_states(read(os.path.join(ROOT, "README.md")), "## The states"),
            self.EXPECTED)

    def test_protocol_state_table(self):
        self.assertEqual(
            table_states(read(os.path.join(DOCS, "protocol.md")), "### The states"),
            self.EXPECTED)

    def test_the_publishers_accept_exactly_those_states(self):
        # Both publishers are hand-written lists; a state added to the model
        # that they reject is a state no adapter can ever send.
        shell = read(os.path.join(ADAPTERS, "shell", "edgewise-pub.sh"))
        self.assertIn("|".join(model.STATES), shell)
        python = read(os.path.join(ADAPTERS, "shell", "edgewise_pub.py"))
        self.assertIn(
            "STATES = (%s)" % ", ".join('"%s"' % s for s in model.STATES), python)


class TestLimitsMatchTheCode(unittest.TestCase):
    """Numbers a reader will design against. Every one of these is quoted in
    docs/protocol.md and enforced somewhere else entirely."""

    def setUp(self):
        self.text = read(os.path.join(DOCS, "protocol.md"))

    def test_text_limits(self):
        self.assertIn("| 16 chars |", self.text)
        self.assertEqual(security.LIMIT_LABEL, 16)
        self.assertIn("| 64 chars |", self.text)
        self.assertEqual(security.LIMIT_MSG, 64)

    def test_ttl_range(self):
        self.assertIn("1–%d s" % model.MAX_TTL_S, self.text)
        self.assertIn("default %d" % model.DEFAULT_TTL_S, self.text)

    def test_payload_cap(self):
        self.assertIn("over %d bytes" % security.MAX_PAYLOAD, self.text)

    def test_text_duration_cap(self):
        self.assertIn("1–%d s" % security.MAX_TEXT_DURATION_S, self.text)

    def test_every_effect_is_documented(self):
        for effect in security.EFFECTS:
            self.assertIn("`%s`" % effect, self.text, effect)

    def test_edge_count(self):
        self.assertIn("`edge:0`…`edge:%d`" % (model.EDGES - 1), self.text)


class TestAdapters(unittest.TestCase):
    def scripts(self):
        return sorted(walk(ADAPTERS, (".sh", ".py")))

    def test_every_adapter_directory_has_a_readme(self):
        for name in sorted(os.listdir(ADAPTERS)):
            path = os.path.join(ADAPTERS, name)
            if os.path.isdir(path):
                self.assertTrue(os.path.exists(os.path.join(path, "README.md")),
                                "%s has no README" % name)

    def test_no_adapter_script_fetches_anything(self):
        # adapters/README.md and every installer promise this in prose. A
        # promise nothing checks is a promise that expires quietly.
        #
        # Only the shapes that actually fetch and run code. `pip install` is
        # deliberately not here: the Python publisher *tells* you to run it,
        # which is the opposite of doing it behind your back.
        offenders = []
        for path in self.scripts():
            for match in FETCHES.finditer(code_only(read(path))):
                offenders.append("%s: %s" % (
                    os.path.relpath(path, ROOT), match.group(0).strip()))
        self.assertEqual(offenders, [])

    def test_no_adapter_script_needs_sudo(self):
        offenders = [os.path.relpath(p, ROOT) for p in self.scripts()
                     if "sudo " in read(p)]
        self.assertEqual(offenders, [])

    def test_scripts_with_a_shebang_have_unix_line_endings(self):
        # test_packaging covers *.sh; run-and-report has no extension, which is
        # exactly the file a user is most likely to run directly and the one
        # where "bad interpreter: no such file or directory" is least guessable.
        for path in walk(ADAPTERS, ()):
            with open(path, "rb") as f:
                raw = f.read()
            if not raw.startswith(b"#!"):
                continue
            self.assertNotIn(b"\r\n", raw, os.path.relpath(path, ROOT))

    def test_every_env_var_used_is_documented(self):
        used = set()
        for path in self.scripts():
            used |= set(ENV_VAR.findall(read(path)))
        documented = set()
        for path in markdown_files():
            documented |= set(ENV_VAR.findall(read(path)))
        undocumented = sorted(used - documented)
        self.assertEqual(undocumented, [],
                         "used by an adapter, explained nowhere")

    def test_hooks_json_is_valid_and_needs_substitution(self):
        path = os.path.join(ADAPTERS, "claude-code", "hooks.json")
        data = json.loads(read(path))
        commands = [
            hook["command"]
            for entries in data["hooks"].values()
            for entry in entries
            for hook in entry["hooks"]
        ]
        self.assertEqual(len(commands), 4)
        for command in commands:
            # A real path here would be one machine's path, silently wrong on
            # every other machine.
            self.assertTrue(command.startswith("__EDGEWISE_HOOK__ "), command)
        states = [c.split()[1] for c in commands]
        for state in states:
            self.assertIn(state, tuple(model.STATES) + (model.STATE_CLEAR,))


class TestControlsMatchTheCode(unittest.TestCase):
    """controls.md calls itself the single source of truth for input, and says
    that if it and the code disagree the code is wrong. That was untrue for
    LEFT, which the table promised opened settings while `app.py` had no
    handler for it at all -- so the one screen the badge could not reach was
    also the one every setup instruction pointed at."""

    BUTTONS = ("UP", "DOWN", "LEFT", "RIGHT", "CONFIRM", "CANCEL")

    def setUp(self):
        self.controls = read(os.path.join(ROOT, "controls.md"))
        self.app = read(os.path.join(ROOT, "app.py"))

    def test_every_button_the_table_promises_is_handled(self):
        missing = []
        for button in self.BUTTONS:
            if button not in self.controls:
                continue
            if '_pressed("%s")' % button not in self.app:
                missing.append(button)
        self.assertEqual(missing, [], "documented in controls.md, no handler")

    def test_settings_is_reachable(self):
        self.assertIn("SCREEN_SETTINGS", self.app)
        # Declared-but-never-used is exactly how this went unnoticed: the
        # constant existed from M0 and nothing ever assigned it.
        self.assertGreater(self.app.count("SCREEN_SETTINGS"), 1,
                           "SCREEN_SETTINGS is declared but never used")


class TestRepositoryDeliverables(unittest.TestCase):
    """Spec S13 lists these by name. Their absence is the failure mode where
    everything works and nobody can use it."""

    def test_the_files_the_spec_requires_exist(self):
        for name in ("README.md", "SECURITY.md", "CONTRIBUTING.md", "LICENSE",
                     "controls.md", "docs/protocol.md", "docs/security.md",
                     "adapters/README.md"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, name)), name)

    def test_docs_and_adapters_stay_out_of_the_badge_tarball(self):
        # export-ignore is what stops a badge downloading the documentation.
        attrs = read(os.path.join(ROOT, ".gitattributes"))
        for pattern in ("docs/", "adapters/", "README.md", "SECURITY.md",
                        "CONTRIBUTING.md"):
            self.assertIn("%s export-ignore" % pattern, attrs, pattern)

    def test_readme_quickstart_publishes_retained(self):
        # The single most common way to be confused by this project is to
        # publish without -r and watch the board empty itself on reboot.
        readme = read(os.path.join(ROOT, "README.md"))
        for line in readme.split("\n"):
            if "mosquitto_pub" in line and "/slot/" in line:
                self.assertIn(" -r ", line, line)


if __name__ == "__main__":
    unittest.main()


class TestOnScreenHintsAreTrue(unittest.TestCase):
    """The recurring bug in this project, three times over: a screen that
    advertises an action nothing implements. LEFT opened settings that did not
    exist; controls.md documented a highlight nothing drew; the detail view
    offered ack and deny and handled neither.

    A view's own text is a promise to the user, so check the promises."""

    def setUp(self):
        self.views = read(os.path.join(ROOT, "views.py"))
        self.app = read(os.path.join(ROOT, "app.py"))

    def test_every_button_named_on_a_screen_has_a_handler(self):
        # Button names as they appear in hint strings drawn to the display.
        named = set(re.findall(r"\b(CONFIRM|CANCEL|LEFT|RIGHT|UP|DOWN)\b",
                               " ".join(re.findall(r'r\.text\(\s*"([^"]+)"',
                                                   self.views))))
        missing = [b for b in sorted(named)
                   if '_pressed("%s")' % b not in self.app
                   and '"%s" in self._held' % b not in self.app]
        self.assertEqual(missing, [], "promised on screen, no handler")

    def test_the_detail_view_hint_matches_controls_md(self):
        controls = read(os.path.join(ROOT, "controls.md"))
        self.assertIn("CONFIRM ack", self.views)
        self.assertIn("| CONFIRM | acknowledge", controls)


class TestClaudeMd(unittest.TestCase):
    """CLAUDE.md is the setup instructions a future session will follow without
    a human checking them first, so the paths in it have to be real."""

    def setUp(self):
        self.text = read(os.path.join(ROOT, "CLAUDE.md"))

    def test_every_script_it_tells_you_to_run_exists(self):
        missing = []
        for path in re.findall(r"\b((?:adapters|tools)/[\w/.-]+\.(?:sh|py))", self.text):
            if not os.path.exists(os.path.join(ROOT, path)):
                missing.append(path)
        self.assertEqual(missing, [], "CLAUDE.md points at files that do not exist")

    def test_it_documents_the_badge_notification_setup(self):
        # The whole reason it exists: a new session should be able to make the
        # badge report progress without being told how.
        for needle in ("install-hooks.sh", "EDGEWISE_ID", "~/.config/edgewise/env"):
            self.assertIn(needle, self.text, needle)

    def test_the_hardware_facts_match_the_code(self):
        # These were each found on hardware and cost a release. If the code
        # changes, this note has to change with it.
        import edgewise.views as views_mod
        from edgewise import boards

        self.assertIn("hardware indices 1-12", self.text.replace("–", "-"))
        profile = boards.load({"board": boards.KEY_2024})
        self.assertEqual(profile.led_offset, 1)
        self.assertIn("30", self.text)
        self.assertEqual(views_mod.EDGE_CENTRE_OFFSET_DEG, 30.0)


class TestAdaptersDoNotExecuteConfig(unittest.TestCase):
    """The config file is data, and data should not be handed a shell.

    The adapters used to `. "$ENV_FILE"`, which runs ~/.config/edgewise/env as
    a shell script every time a hook fires -- and a hook fires on every prompt.
    `edgewise-env.sh` parses it instead, assigning only names it recognises.
    """

    def scripts(self):
        return [p for p in walk(ADAPTERS, (".sh",))
                if os.path.basename(p) != "edgewise-env.sh"]

    def test_nothing_sources_the_user_config(self):
        offenders = []
        for path in self.scripts():
            for line in code_only(read(path)).split("\n"):
                stripped = line.strip()
                if stripped.startswith((". ", "source ")) and "ENV_FILE" in stripped:
                    offenders.append(os.path.relpath(path, ROOT))
        self.assertEqual(offenders, [], "config file executed as shell")

    def test_the_parser_assigns_only_known_names(self):
        # An allowlist, because assigning a computed name in POSIX sh needs
        # eval, and reaching for eval here would undo the point of parsing.
        # code_only: the comment above the list explains *why* eval is not
        # used, and scanning prose would make saying so the thing that fails.
        text = code_only(read(os.path.join(ADAPTERS, "shell", "edgewise-env.sh")))
        self.assertNotIn("eval", text)
        for name in ("EDGEWISE_ID", "EDGEWISE_BROKER", "EDGEWISE_PASS"):
            self.assertIn(name + ")", text, name)

    def test_the_environment_wins_over_the_file(self):
        # `. file` overwrites what is already set, so a one-off override on the
        # command line was silently ignored -- while the README promised it
        # worked. Parsing only fills in what is unset.
        text = read(os.path.join(ADAPTERS, "shell", "edgewise-env.sh"))
        self.assertIn("|| EDGEWISE_ID=$2", text)
