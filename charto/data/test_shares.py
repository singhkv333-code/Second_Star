"""Shared setups: snapshot, view-only reads, copies, credit and scrubbing."""
import importlib.util
import json
import sqlite3
import threading
import unittest
from pathlib import Path


def _load():
    spec = importlib.util.spec_from_file_location(
        "shares_test_module", Path(__file__).with_name("shares.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SPEC = {"v": 1, "grid": "2h",
        "charts": [{"symbol": "TCS", "interval": "1d"},
                   {"symbol": "INFY", "interval": "1d"}],
        "workspace": {"drawings": [{"id": "d1", "type": "hline", "ref": "D1",
                                    "text": "<img src=x onerror=alert(1)>Breakout"}],
                      "scene": [], "indicators": ["rsi14"], "vp": None},
        "chat": [{"role": "user", "content": "never travels in spec"}]}
CHAT = [{"role": "user", "content": "Is TCS basing here?", "symbol": "TCS",
         "image": "data:image/png;base64,AAAA"},
        {"role": "assistant", "content": "## View\nHigher lows since June.",
         "cards": [{"kind": "scan"}]},
        {"role": "system", "content": "dropped"}]


class SharesTest(unittest.TestCase):
    def setUp(self):
        self.sh = _load()
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, name TEXT);
            CREATE TABLE layouts (
              id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
              name TEXT NOT NULL, spec TEXT NOT NULL, updated INTEGER NOT NULL,
              created INTEGER NOT NULL DEFAULT 0, opened INTEGER NOT NULL DEFAULT 0,
              symbols TEXT NOT NULL DEFAULT '', autosave INTEGER NOT NULL DEFAULT 0,
              chat_id TEXT NOT NULL DEFAULT '', thumb TEXT NOT NULL DEFAULT '',
              share_token TEXT, UNIQUE (user_id, name));
            INSERT INTO users VALUES (1,'a@x.in','Asha'),(2,'b@x.in','Bhavin'),
                                     (3,'c@x.in','');
        """)
        lock = threading.Lock()

        def free_name(uid, want):
            taken = {n for (n,) in self.db.execute(
                "SELECT name FROM layouts WHERE user_id=?", (uid,))}
            i, cand = 2, want
            while cand in taken:
                cand, i = f"{want} ({i})", i + 1
            return cand
        self.sh.bind(self.db, lock, free_name)

    def publish(self, uid=1, **kw):
        body = {"title": "TCS base", "note": "Watching 3,900", "spec": SPEC,
                "include_chat": True, "chat": CHAT, **kw}
        return self.sh.publish(uid, "", body)

    def test_publish_is_a_scrubbed_snapshot_without_private_fields(self):
        code, out = self.publish()
        self.assertEqual(code, 201)
        code, v = self.sh.view(out["token"], None, "ip1")
        self.assertEqual(code, 200)
        self.assertEqual(v["by"], "Asha")
        self.assertNotIn("email", json.dumps(v))
        self.assertNotIn("layout_id", v)
        self.assertNotIn("chat", v["spec"])
        text = v["spec"]["workspace"]["drawings"][0]["text"]
        self.assertNotIn("<", text)
        self.assertTrue(text.endswith("Breakout"))
        # text only: no screenshot, no panels, no system turn
        self.assertEqual([t["role"] for t in v["chat"]], ["user", "assistant"])
        self.assertNotIn("image", v["chat"][0])
        self.assertNotIn("cards", v["chat"][1])
        self.assertEqual(v["symbols"], ["TCS", "INFY"])

    def test_chat_is_only_shared_when_asked(self):
        _, out = self.publish(include_chat=False)
        self.assertIsNone(self.sh.view(out["token"], None, "k")[1]["chat"])

    def test_preview_is_public_minimal_and_does_not_count_a_view(self):
        _, out = self.publish()
        preview = self.sh.preview(out["token"])
        self.assertEqual(preview["title"], "TCS base")
        self.assertEqual(preview["symbol"], "TCS")
        self.assertTrue(preview["has_chat"])
        self.assertNotIn("chat", preview)
        self.assertNotIn("email", json.dumps(preview))
        self.assertEqual(self.sh.mine(1)[0]["views"], 0)
        self.assertIsNone(self.sh.preview("missing"))

    def test_views_count_once_per_viewer_and_never_the_author(self):
        _, out = self.publish()
        tok = out["token"]
        self.sh.view(tok, None, "ip1")
        self.sh.view(tok, None, "ip1")
        self.sh.view(tok, 2, "ip9")
        self.sh.view(tok, 1, "ip7")            # the author
        self.assertEqual(self.sh.view(tok, 1, "ip7")[1]["views"], 2)
        # signed out, then signed up from the same address: one person
        self.sh.view(tok, None, "ip5")
        self.sh.view(tok, 3, "ip5")
        self.assertEqual(self.sh.view(tok, 1, "ip7")[1]["views"], 3)

    def test_republish_keeps_the_link_and_only_the_owner_may(self):
        _, out = self.publish()
        tok = out["token"]
        self.assertEqual(self.publish(uid=2, token=tok, title="hijack")[0], 404)
        code, _ = self.publish(token=tok, title="TCS base v2")
        self.assertEqual(code, 200)
        self.assertEqual(self.sh.view(tok, None, "k")[1]["title"], "TCS base v2")

    def test_unpublish_kills_the_link_but_not_copies(self):
        _, out = self.publish()
        tok = out["token"]
        code, cp = self.sh.copy(2, tok)
        self.assertEqual(code, 200)
        self.assertEqual(self.sh.unpublish(2, tok)[0], 404)   # not theirs
        self.assertEqual(self.sh.unpublish(1, tok)[0], 200)
        self.assertEqual(self.sh.view(tok, None, "k")[0], 404)
        self.assertIsNotNone(self.db.execute(
            "SELECT 1 FROM layouts WHERE id=? AND user_id=2",
            (cp["layout_id"],)).fetchone())

    def test_copy_makes_an_editable_layout_with_credit_and_counts_people(self):
        _, out = self.publish()
        tok = out["token"]
        code, cp = self.sh.copy(2, tok)
        self.assertEqual(code, 200)
        self.assertEqual(cp["name"], "TCS base")
        self.assertTrue(cp["chat_id"].startswith("c"))
        self.assertEqual(len(cp["chat"]), 2)
        self.assertEqual(cp["origin"][0]["by"], "Asha")
        again = self.sh.copy(2, tok)[1]
        self.assertEqual(again["name"], "TCS base (2)")     # never overwrites
        self.assertEqual(self.sh.view(tok, None, "k")[1]["copies"], 1)

    def test_view_only_setups_refuse_copies_except_to_the_author(self):
        _, out = self.publish(allow_copy=False)
        self.assertEqual(self.sh.copy(2, out["token"])[0], 403)
        self.assertEqual(self.sh.copy(1, out["token"])[0], 200)

    def test_publishing_a_copy_carries_the_credit_chain(self):
        _, first = self.publish()
        _, cp = self.sh.copy(2, first["token"])
        _, second = self.sh.publish(2, "", {
            "title": "TCS base, my read", "spec": SPEC,
            "layout_id": cp["layout_id"]})
        _, cp3 = self.sh.copy(3, second["token"])
        chain = [c["by"] for c in cp3["origin"]]
        self.assertEqual(chain, ["Bhavin", "Asha"])
        # a nameless author is still credited, never blank
        _, third = self.sh.publish(3, "", {"title": "x", "spec": SPEC})
        self.assertEqual(self.sh.view(third["token"], None, "k")[1]["by"],
                         "A Pivot trader")

    def test_bad_input_is_refused_in_words(self):
        self.assertEqual(self.sh.publish(1, "", {"title": "t", "spec": {}})[0], 400)
        self.assertEqual(self.sh.publish(1, "", {"title": "  ", "spec": SPEC})[0], 400)
        self.assertEqual(self.sh.view("short", None, "k")[0], 404)
        self.assertEqual(self.sh.copy(2, "x" * 24)[0], 404)
        big = {**SPEC, "workspace": {"drawings": ["x" * 2_000_000]}}
        self.assertEqual(self.sh.publish(1, "", {"title": "t", "spec": big})[0], 400)

    def test_a_link_to_someone_elses_layout_is_not_recorded(self):
        self.db.execute("INSERT INTO layouts (user_id, name, spec, updated) "
                        "VALUES (2, 'theirs', '{}', 0)")
        lid = self.db.execute("SELECT id FROM layouts WHERE user_id=2").fetchone()[0]
        _, out = self.publish(layout_id=lid)
        self.assertIsNone(self.sh.mine(1)[0]["layout_id"])


if __name__ == "__main__":
    unittest.main()
