#!/usr/bin/env python3
"""Tests for grok_bridge: splitting, result parsing, allowlist, /new, queue, resume flag."""
import os
import stat
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grok_bridge as gb  # noqa: E402


class Fake(gb.Bridge):
    def __init__(self, tmp):
        super().__init__({"TELEGRAM_BOT_TOKEN": "x", "ALLOWED_CHAT": "42", "AGENT_DIR": tmp,
                          "STATE_DIR": os.path.join(tmp, "state")})
        self.sent = []

    def send(self, text):
        self.sent.append(text)


class T(unittest.TestCase):
    def test_chunks_respect_limit_and_lines(self):
        text = ("строка " * 50 + "\n") * 40
        parts = gb.chunks(text, 1000)
        self.assertTrue(all(len(p) <= 1000 for p in parts))
        self.assertEqual("".join(parts).replace("\n", "").replace(" ", ""),
                         text.replace("\n", "").replace(" ", ""))

    def test_chunks_empty(self):
        self.assertEqual(gb.chunks("  "), ["(пустой ответ)"])

    def test_parse_result_ok(self):
        out = 'noise\n{"type":"result","is_error":false,"result":"ответ","session_id":"s1"}\n'
        self.assertEqual(gb.parse_result(out), ("ответ", "s1", False))

    def test_parse_result_garbage_is_error(self):
        ans, sid, err = gb.parse_result("Error: not logged in")
        self.assertTrue(err)
        self.assertIsNone(sid)
        self.assertIn("not logged in", ans)

    def test_allowlist_ignores_strangers(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Fake(tmp)
            b.handle({"chat": {"id": 7}, "text": "привет"})
            self.assertEqual(b.queue, [])
            self.assertEqual(b.sent, [])

    def test_new_clears_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Fake(tmp)
            b._write(b.session_file, "abc")
            b.handle({"chat": {"id": 42}, "text": "/new"})
            self.assertEqual(b._read(b.session_file), "")
            self.assertEqual(len(b.sent), 1)

    def test_queue_and_busy_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Fake(tmp)
            b.handle({"chat": {"id": 42}, "text": "раз"})
            b.handle({"chat": {"id": 42}, "text": "два"})
            self.assertEqual(b.queue, ["раз", "два"])
            self.assertEqual(len(b.sent), 1)  # only the second gets «отвечу после»

    def test_ask_resumes_and_saves_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "agent")
            with open(fake, "w") as f:
                f.write('#!/bin/sh\necho "$@" > "$(dirname "$0")/args.txt"\n'
                        'echo \'{"type":"result","is_error":false,"result":"ok","session_id":"s2"}\'\n')
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
            old = gb.AGENT_BIN
            gb.AGENT_BIN = fake
            try:
                b = Fake(tmp)
                b._write(b.session_file, "s1")
                self.assertEqual(b.ask_oneshot("вопрос"), ("ok", False))
                self.assertIn("--resume s1", open(os.path.join(tmp, "args.txt")).read())
                self.assertEqual(b._read(b.session_file), "s2")
            finally:
                gb.AGENT_BIN = old


    def test_reminder_add_due_done(self):
        import remind, datetime as dt
        with tempfile.TemporaryDirectory() as tmp:
            remind.BASE = tmp
            remind.main(["add", "--agent", "x", "--at", (dt.datetime.now()+dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M"), "--tz", "Asia/Bangkok", "--text", "сервер"])
            self.assertEqual(remind.due("x"), [])
            later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
            items = remind.due("x", later)
            self.assertEqual(len(items), 1)
            remind.mark_done("x", items[0]["id"])
            self.assertEqual(remind.due("x", later), [])



    def test_same_time_reminder_is_not_doubled(self):
        """Vika 25.11: «оплатить сервер» came twice — the agent added it two times."""
        import remind, datetime as dt
        with tempfile.TemporaryDirectory() as tmp:
            remind.BASE = tmp
            at = (dt.datetime.now() + dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
            remind.main(["add", "--agent", "x", "--at", at, "--text", "оплатить сервер"])
            remind.main(["add", "--agent", "x", "--at", at, "--text", "Оплатить сервер VDSINA"])
            live = [it for it in remind.load("x") if not it["done"]]
            self.assertEqual(len(live), 1)
            self.assertEqual(live[0]["text"], "Оплатить сервер VDSINA")

    def test_different_reminders_same_time_both_kept(self):
        """25.11 student test: techspec's «оплатить сервер» replaced «позвонить в школу» set for the same 10:00."""
        import remind, datetime as dt
        with tempfile.TemporaryDirectory() as tmp:
            remind.BASE = tmp
            at = (dt.datetime.now() + dt.timedelta(days=2)).strftime("%Y-%m-%d") + " 10:00"
            remind.main(["add", "--agent", "x", "--at", at, "--text", "позвонить в школу Маши"])
            remind.main(["add", "--agent", "x", "--at", at, "--text", "оплатить сервер"])
            self.assertEqual(len([it for it in remind.load("x") if not it["done"]]), 2)

    def test_voice_key_added_later_is_picked_up(self):
        """25.11 lesson «навыки и голосовые» on Grok: key written into the bridge env after start works at once."""
        with tempfile.TemporaryDirectory() as tmp:
            st = os.path.join(tmp, "state-x"); os.makedirs(st)
            b = gb.Bridge({"TELEGRAM_BOT_TOKEN": "t", "ALLOWED_CHAT": "1", "AGENT_DIR": tmp, "STATE_DIR": st})
            self.assertEqual(b.groq_key(), "")
            open(os.path.join(tmp, "x.env"), "w").write("TELEGRAM_BOT_TOKEN=t\nGROQ_API_KEY=gsk_test\n")
            self.assertEqual(b.groq_key(), "gsk_test")

    def test_room_forwarder_turns_typed_line_into_task(self):
        """25.11: cron scripts type tasks into the old Claude room — after the move they must reach the Grok agent."""
        import room_forwarder, json
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(room_forwarder.forward("x", "ЗАДАЧА ОТ ПЛАНИРОВЩИКА: диск\n", root=tmp))
            self.assertFalse(room_forwarder.forward("x", "   \n", root=tmp))
            items = [json.loads(l) for l in open(os.path.join(tmp, "state-x", "inbox.jsonl"))]
            self.assertEqual(items, [{"kind": "schedule", "id": "комната", "text": "ЗАДАЧА ОТ ПЛАНИРОВЩИКА: диск"}])

    def test_reminder_list_in_owner_zone(self):
        """25.11 student test: a Moscow student's reminders were listed «(Таиланд)»."""
        import remind, datetime as dt, io, contextlib
        with tempfile.TemporaryDirectory() as tmp:
            remind.BASE = tmp
            at = (dt.datetime.now() + dt.timedelta(days=2)).strftime("%Y-%m-%d") + " 10:00"
            remind.main(["add", "--agent", "x", "--at", at, "--tz", "Europe/Moscow", "--text", "школа"])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                remind.main(["list", "--agent", "x"])
            self.assertIn("10:00 (МСК) — школа", buf.getvalue())

    def test_due_reminders_go_to_agent_as_one_warm_turn(self):
        """Vika 25.11: reminders must sound like the live Anya, not a dry «Напоминание: x»."""
        import grok_bridge as gb, remind, datetime as dt
        with tempfile.TemporaryDirectory() as tmp:
            remind.BASE = tmp
            past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
            remind.save("x", [{"id": 1, "due_utc": past.isoformat(), "text": "оплатить сервер", "done": False},
                              {"id": 2, "due_utc": past.isoformat(), "text": "позвонить маме", "done": False}])
            b = gb.Bridge.__new__(gb.Bridge)
            b.agent = "x"
            got = []
            b.enqueue = got.append
            b.fire_reminders()
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0]["kind"], "remind")
            self.assertEqual(remind.due("x"), [])
            prompt = b.render(got[0])
            self.assertIn("оплатить сервер", prompt)
            self.assertIn("позвонить маме", prompt)
            self.assertIn("живо", prompt)
            self.assertIn("Напоминание: оплатить сервер", b.plain_reminder(got[0]))



class VideoAlbumTest(unittest.TestCase):
    def test_video_and_album_become_one_prompt(self):
        import grok_bridge as gb
        b = gb.Bridge.__new__(gb.Bridge)
        b.chat, b.queue, b.lock, b.wake = "1", [], threading.Lock(), threading.Event()
        b.albums, b.album_timers = {}, {}
        b.state_dir = tempfile.mkdtemp(); b.session_file = os.path.join(b.state_dir, "s")
        b.download = lambda fid, suf: f"/in/{fid}{suf}"
        b.send = lambda *a, **k: None
        b._save_pending = lambda: None
        gb.ALBUM_WAIT = 0.2
        for i, cap in enumerate(["Смонтируй", None, None]):
            m = {"chat": {"id": 1}, "media_group_id": "g", "video": {"file_id": f"v{i}", "file_size": 1000}}
            if cap: m["caption"] = cap
            b.handle(m)
        time.sleep(0.5)
        self.assertEqual(len(b.queue), 1)
        p = b.queue[0]
        self.assertIn("альбом: 3", p); self.assertIn("/in/v0.mp4", p); self.assertIn("/in/v2.mp4", p); self.assertIn("Смонтируй", p)

    def test_big_video_is_reported_not_dropped(self):
        import grok_bridge as gb
        b = gb.Bridge.__new__(gb.Bridge)
        b.download = lambda *a: self.fail("must not download >20MB")
        p = b.to_prompt({"video": {"file_id": "x", "file_size": 30 * 1024 * 1024}})
        self.assertIn("НЕ СКАЧАН", p); self.assertIn("20 МБ", p)


class TasksTest(unittest.TestCase):
    def setUp(self):
        import tasks
        self.t = tasks
        self.home = tempfile.mkdtemp()
        tasks.BASE = self.home
        for a in ("marketer", "montazher"):
            os.makedirs(os.path.join(self.home, f"state-{a}"))

    def test_handoff_goes_to_inbox_and_pingpong_blocked(self):
        t = self.t
        self.assertIn("передал", t.handoff("marketer", "montazher", "смонтируй"))
        items = t.take_inbox("montazher")
        self.assertEqual(items, [{"kind": "handoff", "from": "marketer", "text": "смонтируй"}])
        self.assertEqual(t.take_inbox("montazher"), [])
        open(os.path.join(self.home, "state-montazher", "origin.txt"), "w").write("handoff")
        self.assertIn("ОТКАЗ", t.handoff("montazher", "marketer", "назад"))
        self.assertIn("нет такого", t.handoff("marketer", "nobody", "x"))

    def test_cron(self):
        import datetime as dt
        m = self.t.cron_match
        mon10 = dt.datetime(2026, 11, 23, 10, 0)  # Monday
        self.assertTrue(m("0 10 * * 1", mon10))
        self.assertFalse(m("0 10 * * 2", mon10))
        self.assertTrue(m("*/15 9-11 * * 1-5", mon10))
        self.assertTrue(m("0 10 * * 0", dt.datetime(2026, 11, 29, 10, 0)))  # Sunday as 0
        self.assertTrue(m("0 10 * * 7", dt.datetime(2026, 11, 29, 10, 0)))  # Sunday as 7

    def test_due_schedule_in_tz(self):
        import datetime as dt
        t = self.t
        t.save_schedules("marketer", [{"id": 1, "cron": "0 10 * * 1", "tz": "Asia/Bangkok", "text": "план"}])
        utc = dt.datetime(2026, 11, 23, 3, 0, tzinfo=dt.timezone.utc)  # 10:00 Bangkok, Monday
        self.assertEqual([i["id"] for i in t.due_schedules("marketer", utc)], [1])
        self.assertEqual(t.due_schedules("marketer", utc + dt.timedelta(minutes=1)), [])

    def test_bridge_render_and_report_flow(self):
        import grok_bridge as gb
        b = gb.Bridge.__new__(gb.Bridge)
        self.assertIn("ЗАДАЧА ОТ АГЕНТА «маркетолог»", b.render({"kind": "handoff", "from": "marketer", "text": "x"}))
        self.assertIn("принято", b.render({"kind": "report", "from": "montazher", "text": "готово"}))
        self.assertEqual(b.render({"kind": "vika", "text": "привет"}), "привет")



class ReviewFixesTest(unittest.TestCase):
    """Grok techspec code review 24.11: hang, lost answer, lost voice/photo."""

    def test_silent_hang_is_killed(self):
        import subprocess as sp
        a = gb.Acp.__new__(gb.Acp)
        a.p = sp.Popen(["sleep", "30"], stdin=sp.PIPE, stdout=sp.PIPE, text=True)
        a.n, a.session = 0, "s"
        a._send = lambda o: None
        gb.KILL_GRACE = 0
        t0 = time.time()
        with self.assertRaises(sp.TimeoutExpired):
            a.call("session/prompt", {}, 1)
        self.assertLess(time.time() - t0, 10)

    def test_deliver_retries_then_succeeds(self):
        b = gb.Bridge.__new__(gb.Bridge)
        b.state_dir = tempfile.mkdtemp()
        calls = []
        def send(text):
            calls.append(text)
            if len(calls) < 3:
                raise OSError("telegram down")
        b.send = send
        real_sleep, gb.time.sleep = gb.time.sleep, lambda s: None
        try:
            self.assertTrue(b.deliver("ответ"))
        finally:
            gb.time.sleep = real_sleep
        self.assertEqual(len(calls), 3)

    def test_photo_download_failure_is_text(self):
        b = gb.Bridge.__new__(gb.Bridge)
        def boom(*a):
            raise OSError("network")
        b.download = boom
        p = b.to_prompt({"photo": [{"file_id": "x", "file_size": 100}]})
        self.assertIn("НЕ СКАЧАН", p)



class RulesRefreshTest(unittest.TestCase):
    def test_new_session_only_after_rules_change_and_quiet(self):
        b = gb.Bridge.__new__(gb.Bridge)
        b.dir = tempfile.mkdtemp(); b.session_file = os.path.join(b.dir, "s.txt")
        open(b.session_file, "w").write("sid1")
        rules = os.path.join(b.dir, "CLAUDE.md"); open(rules, "w").write("v1")
        os.utime(rules, (1000, 1000))
        self.assertFalse(b.refresh_if_rules_changed(now=2000))          # first look: remember
        os.utime(rules, (3000, 3000))
        self.assertFalse(b.refresh_if_rules_changed(now=2100))          # changed, but mid-conversation
        self.assertTrue(b.refresh_if_rules_changed(now=2100 + gb.RULES_IDLE))  # quiet 30 min → fresh session
        self.assertEqual(open(b.session_file).read().strip(), "")



class FriendlyErrorTest(unittest.TestCase):
    def test_limit_is_said_plainly(self):
        self.assertIn("Лимит подписки Grok", gb.friendly_error("Error: You have hit your usage limit. Upgrade your plan"))
        self.assertTrue(gb.friendly_error("boom").startswith("Ошибка агента: boom"))



class TablesTest(unittest.TestCase):
    def test_table_becomes_lines(self):
        t = "Итог:\n\n| | Vietjet | Scoot |\n| --- | --- | --- |\n| Вылет | 18:00 | 18:55 |\n| Цена | 25 090 ₽ | 35 820 ₽ |\n\nКонец"
        out = gb.tables_to_text(t)
        self.assertNotIn("| ---", out)
        self.assertIn("Вылет: Vietjet — 18:00 · Scoot — 18:55", out)
        self.assertIn("Конец", out)


if __name__ == "__main__":
    unittest.main()
