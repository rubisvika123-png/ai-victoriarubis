#!/usr/bin/env python3
"""Telegram <-> Cursor CLI bridge: one bot = one agent folder on Grok (Vika 24.11.2026).

Long-polls Telegram, accepts messages only from ALLOWED_CHAT, runs
`agent -p --resume <session>` in the agent's folder and sends the answer back.
Voice -> Groq Whisper text, photo/document -> saved file path in the prompt.
/new starts a fresh conversation. Messages that arrive while the agent works
are queued and answered in order.

Env (bridge.env per bot): TELEGRAM_BOT_TOKEN, ALLOWED_CHAT, AGENT_DIR, MODEL,
GROQ_API_KEY (optional), STATE_DIR.
"""
import json
import re
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tasks  # noqa: E402  handoffs + schedules (Vika 24.11)

AGENT_BIN = os.path.expanduser("~/.local/bin/agent")
TG_LIMIT = 4000
TIMEOUT = 1800
ALBUM_WAIT = 3  # seconds to wait for the rest of an album
RULES_IDLE = 30 * 60  # quiet minutes before a fresh session picks up new rules
KILL_GRACE = 30  # seconds past the deadline before a silent agent process is killed


def tg(token, method, **params):
    data = urllib.parse.urlencode(params).encode()
    url = f"https://api.telegram.org/bot{token}/{method}"
    with urllib.request.urlopen(url, data, timeout=70) as r:
        return json.load(r)


def chunks(text, size=TG_LIMIT):
    """Split on paragraph/line boundaries so Telegram's 4096 limit never cuts a word."""
    text = text.strip() or "(пустой ответ)"
    out = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = text.rfind(" ", 0, size)
        if cut <= 0:
            cut = size
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    out.append(text)
    return out


def md_to_html(text):
    """Markdown → Telegram HTML: **bold**/__bold__, # headings → bold, `code`, - bullets, links."""
    import html as _h
    import re
    t = _h.escape(text, quote=False)
    t = re.sub(r"```[a-zA-Z]*\n(.*?)```", lambda m: "<pre>" + m.group(1) + "</pre>", t, flags=re.S)
    t = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*(.+?)\s*#*$", r"<b>\1</b>", t, flags=re.M)
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: "<b>" + (m.group(1) or m.group(2)) + "</b>", t, flags=re.S)
    t = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)\*(?![\w*])", r"<i>\1</i>", t)
    t = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"^(\s*)[-*]\s+", r"\1• ", t, flags=re.M)
    t = re.sub(r"^\s*-{3,}\s*$", "", t, flags=re.M)
    return t


SEND_EXT = (".mp4", ".mov", ".jpg", ".jpeg", ".png", ".pdf", ".docx", ".xlsx")
TG_FILE_MAX = 50 * 1024 * 1024


LIMIT_WORDS = re.compile(r"usage limit|rate limit|quota|limit (reached|exceeded)|out of (credits|requests)|upgrade your plan|лимит", re.I)


def tables_to_text(text):
    """Telegram can't show Markdown tables (24.11: the owner got «| --- | --- |» pipes). Turn each table into lines:
    «Вылет: Vietjet — 18:00 · Scoot — 18:55»."""
    lines, out, i = text.split("\n"), [], 0
    cells = lambda l: [c.strip() for c in l.strip().strip("|").split("|")]
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            head = cells(lines[i])
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                row = cells(lines[i])
                label, vals = row[0], row[1:]
                pairs = [f"{h} — {v}" if h else v for h, v in zip(head[1:], vals) if v]
                out.append(f"{label}: " + " · ".join(pairs) if label else " · ".join(pairs))
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def friendly_error(raw):
    """Agent errors in plain words for the owner (Vika's hard rule: she must never guess why an agent is mute).
    A spent Grok/Cursor subscription limit is said as such; anything else keeps the raw tail for diagnosis."""
    if LIMIT_WORDS.search(raw or ""):
        return ("Лимит подписки Grok сейчас исчерпан — агент не может ответить, пока он не обновится. "
                "Сообщение я сохранил: напишите ещё раз чуть позже. Подробность для техспеца: " + (raw or "")[-300:])
    return "Ошибка агента: " + (raw or "")[-1500:]


def files_in(text, base):
    """Existing deliverable files the answer mentions (absolute or relative to the agent dir)."""
    import re
    out = []
    for m in re.findall(r"[\w./~-]+\.(?:mp4|mov|jpe?g|png|pdf|docx|xlsx)\b", text, re.I):
        p = os.path.expanduser(m if m.startswith(("/", "~")) else os.path.join(base, m))
        if os.path.isfile(p) and p not in out and os.path.getsize(p) <= TG_FILE_MAX:
            out.append(p)
    return out[:5]


def parse_stream(stdout):
    """`--output-format stream-json`: the owner gets only the text written AFTER the last tool
    call — earlier assistant text is narration («сначала сверюсь с памятью…», Vika 24.11)."""
    segments, after_tool, sid, is_error, result = [], [], None, False, None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        t = obj.get("type")
        if t == "assistant":
            text = "".join(c.get("text", "") for c in obj.get("message", {}).get("content", [])
                           if c.get("type") == "text")
            if text.strip():
                segments.append(text)
                after_tool.append(text)
        elif t == "tool_call":
            after_tool = []
        elif t == "result":
            sid, is_error, result = obj.get("session_id"), obj.get("is_error", False), obj.get("result")
    if result is None and not segments:
        return parse_result(stdout)
    answer = "".join(after_tool).strip() or (segments[-1].strip() if segments else (result or ""))
    return answer, sid, is_error


def parse_result(stdout):
    """Cursor `--output-format json` prints one result object; take the last JSON line."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("type") == "result":
                return obj.get("result") or "", obj.get("session_id"), obj.get("is_error", False)
    return stdout.strip()[-3000:], None, True


class Acp:
    """One long-lived `agent acp` process (Agent Client Protocol, JSON-RPC over stdio)."""

    def __init__(self, cwd):
        self.cwd = cwd
        self.p = subprocess.Popen([AGENT_BIN, "acp"], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.n = 0
        self.session = None
        self.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}}, 60)

    def alive(self):
        return self.p.poll() is None

    def kill(self):
        self.p.kill()

    def _send(self, obj):
        self.p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.p.stdin.flush()

    def call(self, method, params, timeout, on_update=None):
        self.n += 1
        rid = self.n
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + timeout
        # readline() blocks: a silent hang would never reach the deadline check (Grok review 24.11)
        killer = threading.Timer(timeout + KILL_GRACE, self.p.kill)
        killer.daemon = True
        killer.start()
        try:
          while True:
            if time.time() > deadline:
                self._send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.session}})
                raise subprocess.TimeoutExpired(method, timeout)
            line = self.p.stdout.readline()
            if not line:
                if time.time() > deadline:
                    raise subprocess.TimeoutExpired(method, timeout)
                raise RuntimeError("acp process exited")
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("id") == rid and ("result" in o or "error" in o):
                if "error" in o:
                    raise RuntimeError(f"{method}: {o['error']}")
                return o["result"]
            if o.get("method") == "session/update" and on_update:
                on_update(o["params"].get("update", {}))
            elif o.get("method") == "session/request_permission" and "id" in o:
                # the copy runs with --force semantics: allow tool calls (it has no secrets beyond its own)
                opts = o["params"].get("options", [])
                oid = next((x["optionId"] for x in opts if "allow" in x.get("kind", "")),
                           opts[0]["optionId"] if opts else None)
                self._send({"jsonrpc": "2.0", "id": o["id"],
                            "result": {"outcome": {"outcome": "selected", "optionId": oid}}})
            elif "method" in o and "id" in o:
                self._send({"jsonrpc": "2.0", "id": o["id"], "error": {"code": -32601, "message": "not supported"}})
        finally:
            killer.cancel()

    def open(self, sid=None):
        if sid:
            try:
                self.call("session/load", {"sessionId": sid, "cwd": self.cwd, "mcpServers": []}, 120)
                self.session = sid
                return
            except Exception:
                pass
        self.session = self.call("session/new", {"cwd": self.cwd, "mcpServers": []}, 120)["sessionId"]

    def prompt(self, text, timeout):
        """Owner gets only the text after the last tool call — earlier text is narration."""
        state = {"after": []}

        def upd(u):
            kind = u.get("sessionUpdate")
            if kind == "agent_message_chunk":
                state["after"].append((u.get("content") or {}).get("text", ""))
            elif kind in ("tool_call", "tool_call_update") and state["after"]:
                if kind == "tool_call":
                    state["after"] = []
        self.call("session/prompt", {"sessionId": self.session, "prompt": [{"type": "text", "text": text}]},
                  timeout, upd)
        return "".join(state["after"]).strip() or "(агент выполнил задачу без текстового ответа)"


class ClaudeStream:
    """Persistent Claude Code (`claude -p --input-format stream-json`) — the GLM copies run on it,
    because Claude Code channels (our Telegram plugin) are off with an API-key login (24.11)."""

    def __init__(self, cwd, model="opus"):
        self.cwd, self.model, self.session, self.p = cwd, model, None, None

    def alive(self):
        return self.p is not None and self.p.poll() is None

    def kill(self):
        if self.p:
            self.p.kill()

    def open(self, sid=None):
        cmd = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--dangerously-skip-permissions", "--model", self.model]
        if sid:
            cmd += ["--resume", sid]
        env = dict(os.environ, IS_SANDBOX="1", PATH=os.path.expanduser("~/.local/bin") + ":" + os.environ.get("PATH", ""))
        self.p = subprocess.Popen(cmd, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
        self.session = sid or "pending"

    def prompt(self, text, timeout):
        self.p.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}},
                                      ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        deadline, after = time.time() + timeout, []
        killer = threading.Timer(timeout + KILL_GRACE, self.p.kill)  # a silent hang never reaches the deadline check
        killer.daemon = True
        killer.start()
        while True:
            if time.time() > deadline:
                self.kill()
                raise subprocess.TimeoutExpired("claude", timeout)
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("claude process exited")
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") == "assistant":
                for c in (o.get("message") or {}).get("content") or []:
                    if c.get("type") == "text":
                        after.append(c.get("text", ""))
                    elif c.get("type") == "tool_use":
                        after = []
            elif o.get("type") == "result":
                self.session = o.get("session_id") or self.session
                killer.cancel()
                return "".join(after).strip() or (o.get("result") or "").strip() or "(готово)"


# Owner's name as the agent hears it: ours «Виктория»; a student's bridge gets OWNER_FORMS=владелец|владельца|владельцу
# from the migrator (25.11: student agents were told «передал по просьбе Виктории»). nominative|genitive|dative.
OWNER_NOM, OWNER_GEN, OWNER_DAT = (os.environ.get("OWNER_FORMS", "Виктория|Виктории|Виктории").split("|") + ["", ""])[:3]


class Bridge:
    def __init__(self, env):
        self.token = env["TELEGRAM_BOT_TOKEN"]
        self.chat = str(env["ALLOWED_CHAT"])
        self.dir = env["AGENT_DIR"]
        self.model = env.get("MODEL", "grok-4.7-medium")
        self.backend = env.get("BACKEND", "cursor")
        self.groq = env.get("GROQ_API_KEY", "")
        self.state_dir = env.get("STATE_DIR", os.path.expanduser("~/bridge/state"))
        self.agent = os.path.basename(self.state_dir.rstrip("/")).replace("state-", "", 1)
        os.makedirs(os.path.join(self.state_dir, "inbox"), exist_ok=True)
        self.session_file = os.path.join(self.state_dir, "session.txt")
        self.offset_file = os.path.join(self.state_dir, "offset.txt")
        self.queue = []
        self.albums, self.album_timers = {}, {}
        self.lock = threading.Lock()
        self.wake = threading.Event()

    # --- state -------------------------------------------------------------
    def _read(self, path):
        try:
            return open(path).read().strip()
        except OSError:
            return ""

    def _write(self, path, value):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(value)
        os.replace(tmp, path)

    # --- telegram in --------------------------------------------------------
    def download(self, file_id, suffix):
        info = tg(self.token, "getFile", file_id=file_id)["result"]
        self._dl = getattr(self, "_dl", 0) + 1  # album parts arrive in the same millisecond
        path = os.path.join(self.state_dir, "inbox", f"{int(time.time()*1000)}-{self._dl}{suffix}")
        urllib.request.urlretrieve(f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}", path)
        return path

    def fetch(self, media, suffix):
        """Download a Telegram file; Bot API refuses files over 20 MB, say so instead of dropping the message."""
        if (media.get("file_size") or 0) > 20 * 1024 * 1024:
            mb = round(media["file_size"] / 1024 / 1024)
            return f"НЕ СКАЧАН: файл {mb} МБ, Телеграм-бот не может скачать больше 20 МБ — попроси прислать ссылкой (Яндекс Диск, Google Диск)"
        try:
            return self.download(media["file_id"], suffix)
        except Exception as e:
            return f"НЕ СКАЧАН: ошибка загрузки ({e})"

    def groq_key(self):
        """The voice key may be added later (course lesson «навыки и голосовые», 25.11): re-read the bridge's own
        env file, so the agent or the owner adds GROQ_API_KEY=… there and voice works without a restart."""
        if not self.groq:
            envf = os.path.join(os.path.dirname(self.state_dir.rstrip("/")), f"{self.agent}.env")
            try:
                for line in open(envf, encoding="utf-8"):
                    if line.startswith("GROQ_API_KEY="):
                        self.groq = line.split("=", 1)[1].strip()
            except OSError:
                pass
        return self.groq

    def transcribe(self, path):
        if not self.groq_key():
            return None
        r = subprocess.run(
            ["curl", "-s", "-m", "120", "https://api.groq.com/openai/v1/audio/transcriptions",
             "-H", f"Authorization: Bearer {self.groq}",
             "-F", f"file=@{path}", "-F", "model=whisper-large-v3", "-F", "language=ru",
             "-F", "response_format=text"],
            capture_output=True, text=True)
        return r.stdout.strip() or None

    def to_prompt(self, msg):
        """Turn a Telegram message into the text the agent sees; None = ignore."""
        parts = []
        if msg.get("voice") or msg.get("audio"):
            v = msg.get("voice") or msg.get("audio")
            path = self.fetch(v, ".ogg")
            text = None if path.startswith("НЕ СКАЧАН") else self.transcribe(path)
            parts.append(f"[голосовое, расшифровка] {text}" if text else f"[голосовое, файл {path}, расшифровать не удалось]")
        if msg.get("photo"):
            path = self.fetch(msg["photo"][-1], ".jpg")
            parts.append(f"[фото: {path} — открой и посмотри]")
        if msg.get("document"):
            d = msg["document"]
            path = self.fetch(d, os.path.splitext(d.get("file_name", ""))[1] or ".bin")
            parts.append(f"[файл {d.get('file_name', '')}: {path}]")
        for key, label in (("video", "видео"), ("video_note", "видео-кружок"), ("animation", "гифка")):
            if msg.get(key):  # 24.11: videos were silently dropped, montazher saw only «смонтируй»
                v = msg[key]
                path = self.fetch(v, os.path.splitext(v.get("file_name", ""))[1] or ".mp4")
                parts.append(f"[{label}: {path}]")
        text = msg.get("text") or msg.get("caption")
        if text:
            parts.append(text)
        return "\n".join(parts) or None

    # --- agent --------------------------------------------------------------
    def ask(self, prompt):
        """Persistent ACP agent first (no 10-30 s cold start per message, Vika 24.11);
        the one-shot CLI stays as a fallback if ACP breaks."""
        try:
            self.refresh_if_rules_changed()
            sid = self._read(self.session_file)
            if self.backend == "claude":
                if not getattr(self, "acp", None) or not self.acp.alive() or (not sid and self.acp.session not in (None, "pending") and sid != self.acp.session):
                    if getattr(self, "acp", None):
                        self.acp.kill()
                    self.acp = ClaudeStream(self.dir, self.model)
                    self.acp.open(sid or None)
                answer = self.acp.prompt(prompt, TIMEOUT)
                self._write(self.session_file, self.acp.session)
                return answer, False
            if not getattr(self, "acp", None) or not self.acp.alive():
                self.acp = Acp(self.dir)
            if not sid or sid != self.acp.session:
                self.acp.open(sid or None)
                self._write(self.session_file, self.acp.session)
            return self.acp.prompt(prompt, TIMEOUT), False
        except subprocess.TimeoutExpired:
            raise
        except Exception as e:
            print("acp failed, falling back to -p:", e, file=sys.stderr, flush=True)
            try:
                self.acp.kill()
            except Exception:
                pass
            self.acp = None
            self._write(self.session_file, "")
            return self.ask_oneshot(prompt)

    def refresh_if_rules_changed(self, now=None):
        """The rules file (CLAUDE.md) is rebuilt hourly by grok-sync.py, but a running agent session keeps the
        rules it started with (24.11: every rule fix needed a manual session reset). Start a fresh session when
        the rules changed AND the owner has been quiet for a while — never in the middle of a conversation."""
        now = now or time.time()
        try:
            mtime = os.path.getmtime(os.path.join(self.dir, "CLAUDE.md"))
        except OSError:
            return False
        started = getattr(self, "rules_seen", None)
        last = getattr(self, "last_turn", 0)
        self.last_turn = now
        if started is None:
            self.rules_seen = mtime
            return False
        if mtime > started and now - last >= RULES_IDLE:
            self.rules_seen = mtime
            self._write(self.session_file, "")
            if getattr(self, "acp", None):
                try:
                    self.acp.kill()
                except Exception:
                    pass
                self.acp = None
            return True
        return False

    def ask_oneshot(self, prompt):
        cmd = [AGENT_BIN, "-p", "--trust", "--force", "--model", self.model, "--output-format", "stream-json"]
        session = self._read(self.session_file)
        if session:
            cmd += ["--resume", session]
        cmd.append(prompt)
        r = subprocess.run(cmd, cwd=self.dir, capture_output=True, text=True, timeout=TIMEOUT)
        answer, sid, is_error = parse_stream(r.stdout + ("\n" + r.stderr if r.returncode else ""))
        if sid:
            self._write(self.session_file, sid)
        return answer, is_error

    # --- «Печатает…» bubble: a real message, so it shows in the chat list (Vika 24.11), deleted on answer ---
    def status_bubble(self):
        try:
            r = tg(self.token, "sendMessage", chat_id=self.chat, text="<i>Печатает…</i>", parse_mode="HTML",
                   disable_notification="true")
            mid = r["result"]["message_id"]
        except Exception:
            return None

        def later():  # long task: same wording as the Claude agents after 5 minutes
            time.sleep(300)
            if getattr(self, "_bubble", None) == mid:
                try:
                    tg(self.token, "editMessageText", chat_id=self.chat, message_id=mid,
                       text="<i>Ещё работаю…</i>", parse_mode="HTML")
                except Exception:
                    pass
        self._bubble = mid
        threading.Thread(target=later, daemon=True).start()
        return mid

    def drop_bubble(self, mid):
        self._bubble = None
        if mid:
            try:
                tg(self.token, "deleteMessage", chat_id=self.chat, message_id=mid)
            except Exception:
                pass

    def typing_loop(self, stop):
        while not stop.is_set():
            try:
                tg(self.token, "sendChatAction", chat_id=self.chat, action="typing")
            except Exception:
                pass
            stop.wait(5)

    def send(self, text):
        text = tables_to_text(text)
        for part in chunks(text):
            try:  # agents write Markdown; Telegram shows it raw («непонятные символы», Vika 24.11)
                tg(self.token, "sendMessage", chat_id=self.chat, text=md_to_html(part), parse_mode="HTML")
            except Exception:
                tg(self.token, "sendMessage", chat_id=self.chat, text=part)
            time.sleep(1)  # flood control: one message per second
        for path in files_in(text, self.dir):
            # the agent can only print a path; the bridge delivers the file (montage reels, images)
            method, field = ("sendVideo", "video") if path.lower().endswith((".mp4", ".mov")) else ("sendDocument", "document")
            subprocess.run(["curl", "-s", "-m", "300", f"https://api.telegram.org/bot{self.token}/{method}",
                            "-F", f"chat_id={self.chat}", "-F", f"{field}=@{path}"], capture_output=True)
            time.sleep(1)

    def worker(self):
        while True:
            self.wake.wait()
            with self.lock:
                if not self.queue:
                    self.wake.clear()
                    continue
                prompt = self.queue.pop(0)
            if prompt is None:
                self.warmup()
                continue
            item = prompt if isinstance(prompt, dict) else {"kind": "vika", "text": prompt}
            prompt = self.render(item)
            self._write(os.path.join(self.state_dir, "origin.txt"), item["kind"])  # tasks.py anti ping-pong
            self.current = item if item["kind"] != "vika" else item["text"]
            stop = threading.Event()
            threading.Thread(target=self.typing_loop, args=(stop,), daemon=True).start()
            status_id = self.status_bubble()
            started = time.time()
            try:
                answer, is_error = self.ask(prompt)
                if is_error:
                    answer = self.plain_reminder(item) if item["kind"] == "remind" else friendly_error(answer)
            except subprocess.TimeoutExpired:
                answer = "Агент думал дольше 30 минут и был остановлен. Напишите ещё раз короче или /new."
            except Exception as e:  # never let one bad turn kill the bridge
                answer = f"Сбой моста: {e}"
            finally:
                stop.set()
            if item["kind"] == "remind" and answer.startswith(("Агент думал", "Сбой моста")):
                answer = self.plain_reminder(item)  # a reminder must arrive even if the agent failed
            # per-turn timing for the Claude-vs-Grok speed comparison (Vika 24.11)
            with open(os.path.join(self.state_dir, "turns.tsv"), "a") as f:
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{time.time() - started:.0f}\t"
                        f"{len(prompt)}\t{len(answer)}\n")
            self.drop_bubble(status_id)
            self._write(os.path.join(self.state_dir, "origin.txt"), "vika")
            if item["kind"] == "report":
                self.current = None
                self._save_pending()
                continue  # Vika already got the result from the other agent's bot
            if item["kind"] == "handoff":
                answer = f"Задача от агента «{tasks.NAMES.get(item['from'], item['from'])}»:\n\n" + answer
                tasks.append(os.path.join(tasks.sdir(item["from"]), "inbox.jsonl"),
                             {"kind": "report", "from": self.agent, "text": answer[:3000]})
            self.deliver(answer)
            self.current = None  # only now: the answer is in Telegram (Grok review 24.11)
            self._save_pending()

    def deliver(self, answer):
        """Send with retries; a failed send must not silently eat a finished answer."""
        for wait in (0, 5, 15, 30, 60, 120):
            time.sleep(wait)
            try:
                self.send(answer)
                return True
            except Exception as e:
                print(f"send failed (retry after {wait}s):", e, file=sys.stderr, flush=True)
        with open(os.path.join(self.state_dir, "undelivered.txt"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%F %T") + "\n" + answer + "\n---\n")
        return False

    def render(self, item):
        """Text the agent sees for a queue item (Vika's own message, a handed task, a report, a schedule)."""
        who = tasks.NAMES.get(item.get("from", ""), item.get("from", ""))
        if item["kind"] == "handoff":
            return (f"ЗАДАЧА ОТ АГЕНТА «{who}» (передал по просьбе {OWNER_GEN}): {item['text']}\n\n"
                    f"Сделай. Твой ответ сам уйдёт {OWNER_DAT} в твой бот, а отправителю — отчёт. Дальше по цепочке не передавай.")
        if item["kind"] == "report":
            return (f"ОТЧЁТ ОТ АГЕНТА «{who}» по задаче, которую ты передавал:\n{item['text']}\n\n"
                    f"{OWNER_NOM} этот ответ уже получил(а). Просто прими к сведению, ответь одним словом: принято.")
        if item["kind"] == "remind":
            what = "\n".join("- " + t for t in item["texts"])
            return (f"НАПОМИНАНИЕ ПОДОШЛО (его поставил(а) {OWNER_NOM}). Напомни {OWNER_DAT} сейчас одним сообщением:\n" + what +
                    "\n\nПиши живо и тепло, как близкий помощник: обратись по имени, коротко, по-человечески, "
                    "можно одно уместное эмодзи; если есть что-то полезное к делу (ссылка, сумма, срок из памяти) — добавь одной строкой. "
                    "Без слова «Напоминание:» в начале, без списков и заголовков, если пункт один.")
        if item["kind"] == "schedule":
            return f"ЗАДАЧА ПО РАСПИСАНИЮ #{item.get('id')} (её поставил(а) {OWNER_NOM}): {item['text']}"
        return item["text"]

    def handle(self, msg):
        if str(msg.get("chat", {}).get("id")) != self.chat:
            return  # allowlist: only the owner
        if (msg.get("text") or "").strip() == "/new":
            self._write(self.session_file, "")
            self.send("Начали новый разговор. Прошлую переписку агент больше не тащит, память и правила на месте.")
            return
        prompt = self.to_prompt(msg)
        if not prompt:
            return
        group = msg.get("media_group_id")
        if group:  # an album = several updates; give the agent ONE message with all parts in order
            with self.lock:
                self.albums.setdefault(group, []).append(prompt)
                old = self.album_timers.pop(group, None)
            if old:
                old.cancel()
            t = threading.Timer(ALBUM_WAIT, self._flush_album, args=(group,))
            self.album_timers[group] = t
            t.start()
            return
        self.enqueue(prompt)

    def _flush_album(self, group):
        with self.lock:
            parts = self.albums.pop(group, [])
            self.album_timers.pop(group, None)
        if parts:
            self.enqueue(f"[альбом: {len(parts)} файлов, по порядку]\n" + "\n".join(parts))

    def enqueue(self, prompt):
        with self.lock:
            self.queue.append(prompt)
            self._save_pending()
            busy = len(self.queue) > 1
        self.wake.set()
        if busy and not isinstance(prompt, dict):  # the «wait» note is for Vika's own messages only
            self.send("Принял, отвечу после текущего ответа.")

    def warmup(self):
        """Load the agent and its rules once at start, so the first real message is not slow."""
        try:
            self.ask("Служебная проверка связи от моста, ответь одним словом: готов")
        except Exception as e:
            print("warmup failed:", e, file=sys.stderr, flush=True)

    def fire_reminders(self):
        """Due reminders go to the agent as ONE turn, so it writes them warmly in its own voice (Vika 25.11)."""
        import remind
        due = remind.due(self.agent)
        if not due:
            return
        for it in due:
            remind.mark_done(self.agent, it["id"])
        self.enqueue({"kind": "remind", "texts": [it["text"] for it in due]})

    def plain_reminder(self, item):
        """Fallback when the agent can't answer (limit, crash): the reminder must still arrive."""
        return "\n".join("Напоминание: " + t for t in item["texts"])

    def reminder_loop(self):
        """Deliver reminders the agent scheduled with remind.py (the bridge writes first, the agent can't)."""
        import remind
        agent = self.agent
        last_minute = None
        while True:
            try:
                self.fire_reminders()
                for it in tasks.take_inbox(agent):  # tasks handed over by other agents / their reports
                    self.enqueue(it)
                now = time.time()
                minute = int(now // 60)
                if minute != last_minute:  # schedules fire once per matching minute
                    last_minute = minute
                    utc = __import__("datetime").datetime.fromtimestamp(minute * 60, __import__("datetime").timezone.utc)
                    for it in tasks.due_schedules(agent, utc):
                        self.enqueue({"kind": "schedule", "id": it["id"], "text": it["text"]})
            except Exception as e:
                print("reminder loop:", e, file=sys.stderr, flush=True)
            time.sleep(5)

    # --- pending queue survives restarts (24.11: a restart mid-answer lost Vika's question) ---
    def _pending_path(self):
        return os.path.join(self.state_dir, "pending.json")

    def _save_pending(self):
        items = ([self.current] if getattr(self, "current", None) else []) + [q for q in self.queue if q]
        self._write(self._pending_path(), json.dumps(items, ensure_ascii=False))

    def _load_pending(self):
        try:
            return json.load(open(self._pending_path(), encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def run(self):
        threading.Thread(target=self.reminder_loop, daemon=True).start()
        with self.lock:
            self.queue.append(None)  # warmup marker, handled by the worker before real messages
            self.queue.extend(self._load_pending())
        self.wake.set()
        threading.Thread(target=self.worker, daemon=True).start()
        offset = int(self._read(self.offset_file) or 0)
        backoff = 5
        while True:
            try:
                upd = tg(self.token, "getUpdates", offset=offset, timeout=50)["result"]
                backoff = 5
            except urllib.error.HTTPError as e:
                # 429: hammering every few seconds keeps the flood penalty alive — honour
                # retry_after and back off exponentially (Stepan's 15.5h ban, 13.10).
                wait = backoff
                try:
                    wait = max(wait, json.load(e).get("parameters", {}).get("retry_after", 0))
                except Exception:
                    pass
                print(f"poll failed: HTTP {e.code}, sleeping {wait}s", file=sys.stderr, flush=True)
                time.sleep(wait)
                backoff = min(backoff * 2, 300)
                continue
            except Exception as e:
                print("poll failed:", e, file=sys.stderr, flush=True)
                time.sleep(5)
                continue
            for u in upd:
                offset = u["update_id"] + 1
                self._write(self.offset_file, str(offset))
                if "message" in u:
                    try:
                        self.handle(u["message"])
                    except Exception as e:
                        print("handle failed:", e, file=sys.stderr, flush=True)
                        if str(u["message"].get("chat", {}).get("id")) == self.chat:
                            self.enqueue(f"[сообщение {OWNER_GEN} не удалось обработать ({e}). Скажи {OWNER_DAT}, что оно не дошло, и попроси прислать ещё раз]")


def load_env(path):
    env = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return env


if __name__ == "__main__":
    Bridge(load_env(sys.argv[1])).run()
