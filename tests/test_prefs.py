"""The settings screen's model: navigation, edits, and what they persist.

The dialogs themselves are `app_components` and cannot run off-badge, which is
exactly why the menu is split the way it is: everything that decides what an
edit *means* lives in `prefs`, with no firmware imports, so it is all testable
here. What is left on the badge is "open the platform's text dialog and hand
back a string".
"""

import unittest

from edgewise import boards, conf, prefs


def make_cfg(**over):
    cfg = conf.validate({})
    cfg.update(over)
    return cfg


class TestNavigation(unittest.TestCase):
    def setUp(self):
        self.m = prefs.SettingsModel(make_cfg())

    def test_starts_at_the_root(self):
        self.assertIsNone(self.m.group)
        self.assertEqual([i.label for i in self.m.items()],
                         ["Broker", "Device ID", "Board", "Display", "About"])

    def test_broker_is_first_because_nothing_works_without_it(self):
        self.assertEqual(self.m.items()[0].key, "broker")

    def test_selecting_a_group_descends(self):
        kind, _ = self.m.select()
        self.assertEqual(kind, prefs.KIND_GROUP)
        self.assertEqual(self.m.group, "broker")
        self.assertEqual(self.m.index, 0)
        self.assertEqual(self.m.current().key, "broker.host")

    def test_back_from_a_group_returns_to_its_own_row(self):
        self.m.index = 2                      # Board
        self.m.select()
        self.assertEqual(self.m.group, "board")
        self.assertFalse(self.m.back())       # did not leave settings
        self.assertIsNone(self.m.group)
        self.assertEqual(self.m.index, 2)     # not back at the top

    def test_back_from_the_root_closes_the_screen(self):
        self.assertTrue(self.m.back())

    def test_movement_wraps(self):
        self.m.move(-1)
        self.assertEqual(self.m.index, len(prefs.ROOT) - 1)
        self.m.move(1)
        self.assertEqual(self.m.index, 0)


class TestEdits(unittest.TestCase):
    def setUp(self):
        self.m = prefs.SettingsModel(make_cfg())

    def enter(self, group, key):
        self.m.group = group
        items = self.m.items()
        self.m.index = next(i for i, item in enumerate(items) if item.key == key)
        return self.m.current()

    def test_text_edit_applies(self):
        item = self.enter("broker", "broker.host")
        self.assertTrue(self.m.apply(item, "192.168.1.159"))
        self.assertEqual(self.m.cfg["broker"]["host"], "192.168.1.159")

    def test_a_cancelled_dialog_changes_nothing(self):
        # TextDialog returns False on CANCEL, not None. Treating that as an
        # empty confirm would erase the broker host on a mis-press, which is the
        # easiest mistake to make on six buttons.
        item = self.enter("broker", "broker.host")
        self.m.apply(item, "192.168.1.159")
        for cancelled in (None, False):
            self.assertFalse(self.m.apply(item, cancelled))
            self.assertEqual(self.m.cfg["broker"]["host"], "192.168.1.159")

    def test_confirming_an_empty_field_is_not_a_cancel(self):
        item = self.enter("broker", "broker.host")
        self.m.apply(item, "192.168.1.159")
        self.assertTrue(self.m.apply(item, ""))
        self.assertEqual(self.m.cfg["broker"]["host"], "")

    def test_number_edits_are_revalidated_not_trusted(self):
        # The dialog will happily return 999999; conf.validate owns the bounds.
        item = self.enter("broker", "broker.port")
        self.m.apply(item, "999999")
        self.assertEqual(self.m.cfg["broker"]["port"], 65535)

    def test_rubbish_in_a_number_field_is_refused(self):
        item = self.enter("broker", "broker.port")
        self.assertFalse(self.m.apply(item, "eight"))
        self.assertEqual(self.m.cfg["broker"]["port"], conf.DEFAULT_PORT)

    def test_emptying_a_credential_clears_it(self):
        # "" and absent are not the same to BrokerSpec, which passes the value
        # straight to the MQTT client.
        item = self.enter("broker", "broker.user")
        self.m.apply(item, "badge")
        self.assertEqual(self.m.cfg["broker"]["user"], "badge")
        self.m.apply(item, "")
        self.assertIsNone(self.m.cfg["broker"]["user"])

    def test_a_prefix_with_wildcards_is_cleaned(self):
        item = self.enter("broker", "broker.prefix")
        self.m.apply(item, "open/edgewise/")
        self.assertEqual(self.m.cfg["broker"]["prefix"], "open/edgewise")

    def test_toggle_flips_and_persists(self):
        self.enter("broker", "broker.tls")
        self.assertFalse(self.m.cfg["broker"]["tls"])
        kind, _ = self.m.select()
        self.assertEqual(kind, prefs.KIND_TOGGLE)
        self.assertTrue(self.m.cfg["broker"]["tls"])
        self.m.select()
        self.assertFalse(self.m.cfg["broker"]["tls"])

    def test_choice_cycles_through_every_option_and_returns(self):
        self.enter("display", "palette")
        seen = []
        for _ in range(len(conf.PALETTES)):
            self.m.select()
            seen.append(self.m.cfg["palette"])
        self.assertEqual(sorted(seen), sorted(conf.PALETTES))

    def test_board_choice_offers_custom_so_a_wrong_map_is_fixable(self):
        item = self.enter("board", "board")
        self.assertIn(boards.KEY_CUSTOM, item.choices)

    def test_calibrate_is_reachable(self):
        # It existed from M0 with nothing calling it. That is the whole reason
        # a wrong LED map meant a REPL.
        keys = [i.key for i in prefs.GROUPS[2][2]]
        self.assertIn("calibrate", keys)

    def test_replay_demo_is_reachable(self):
        keys = [i.key for i in prefs.GROUPS[4][2]]
        self.assertIn("replay_demo", keys)


class TestSummaries(unittest.TestCase):
    def setUp(self):
        self.m = prefs.SettingsModel(make_cfg())

    def item(self, group, key):
        # The cursor has to move too: select() acts on the current row, so a
        # helper that only set the group would silently test row zero.
        self.m.group = group
        items = self.m.items()
        self.m.index = next(i for i, item in enumerate(items) if item.key == key)
        return self.m.current()

    def test_password_is_never_rendered(self):
        item = self.item("broker", "broker.pass")
        self.m.apply(item, "hunter2")
        summary = self.m.summary(item)
        self.assertNotIn("hunter2", summary)
        self.assertEqual(set(summary), {"•"})

    def test_unset_reads_as_unset_rather_than_blank(self):
        self.assertEqual(self.m.summary(self.item("broker", "broker.host")),
                         "not set")

    def test_toggles_read_as_on_or_off(self):
        item = self.item("broker", "broker.tls")
        self.assertEqual(self.m.summary(item), "off")
        self.m.select()
        self.assertEqual(self.m.summary(item), "on")

    def test_rotation_reads_as_a_direction_not_an_index(self):
        item = self.item("board", "rotation")
        self.assertEqual(self.m.summary(item), "top")

    def test_board_shows_the_profile_name(self):
        item = self.item("board", "board")
        self.assertEqual(self.m.summary(item), "auto")
        self.m.cfg = prefs.put(self.m.cfg, "board", boards.KEY_CUSTOM)
        self.assertEqual(self.m.summary(item), "custom")

    def test_groups_have_no_value_column(self):
        self.m.group = None
        for item in prefs.ROOT:
            self.assertEqual(self.m.summary(item), "")

    def test_needs_broker_until_one_is_set(self):
        self.assertTrue(self.m.needs_broker())
        self.m.cfg = prefs.put(self.m.cfg, "broker.host", "192.168.1.159")
        self.assertFalse(self.m.needs_broker())


class TestEveryItemIsReachableAndSane(unittest.TestCase):
    """A menu entry whose key is not in the config is a row that silently does
    nothing when selected."""

    def test_every_editable_key_exists_in_the_config(self):
        cfg = make_cfg()
        actions = ("device_id", "regenerate", "calibrate", "replay_demo",
                   "version", "repo", "http_regen")
        for _, _, items in prefs.GROUPS:
            for item in items:
                if item.kind == prefs.KIND_ACTION:
                    self.assertIn(item.key, actions, item.key)
                    continue
                # `has`, not `get`: broker.user is legitimately None.
                self.assertTrue(prefs.has(cfg, item.key), item.key)

    def test_every_choice_item_lists_the_current_value(self):
        cfg = make_cfg()
        for _, _, items in prefs.GROUPS:
            for item in items:
                if item.kind == prefs.KIND_CHOICE:
                    self.assertIn(prefs.get(cfg, item.key), item.choices,
                                  item.key)

    def test_every_group_key_has_a_root_row(self):
        self.assertEqual([g[0] for g in prefs.GROUPS],
                         [i.key for i in prefs.ROOT])


if __name__ == "__main__":
    unittest.main()
