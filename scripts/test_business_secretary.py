#!/usr/bin/env python3
"""Tests for business-secretary.py: the owner's Telegram Business chatbot that answers
clients in the owner's PERSONAL chats with no tools (Vika 12.11.2026)."""
import importlib.util, os, tempfile, time

spec = importlib.util.spec_from_file_location(
    "sec", os.path.join(os.path.dirname(os.path.abspath(__file__)), "business-secretary.py"))
sec = importlib.util.module_from_spec(spec); spec.loader.exec_module(sec)

OWNER, CLIENT, STRANGER = 111, 222, 333


class FakeApi:
    def __init__(self): self.calls = []
    def __call__(self, method, **params):
        self.calls.append((method, params)); return {"ok": True, "result": {}}
    def sent(self): return [p for m, p in self.calls if m == "sendMessage"]


def make(model_answer="Здравствуйте! Покрытие стоит 2000 рублей."):
    d = tempfile.mkdtemp()
    kb = os.path.join(d, "knowledge.md"); open(kb, "w").write("Покрытие 2000 рублей.")
    prompts = []
    def model(system, prompt):
        prompts.append(system + "\n" + prompt); return model_answer
    s = sec.Secretary(owner_id=OWNER, state_dir=d, knowledge_path=kb, api=FakeApi(), model=model)
    return s, prompts


def conn(user_id, cid="C1", can_reply=True, enabled=True):
    return {"business_connection": {"id": cid, "user": {"id": user_id}, "user_chat_id": user_id,
                                    "rights": {"can_reply": can_reply}, "is_enabled": enabled, "date": 1}}


def msg(from_id, text="Сколько стоит покрытие?", cid="C1", chat=CLIENT, mid=1, **extra):
    m = {"business_connection_id": cid, "message_id": mid, "from": {"id": from_id, "first_name": "Аня"},
         "chat": {"id": chat, "type": "private"}, "date": int(time.time())}
    if text is not None: m["text"] = text
    m.update(extra)
    return {"business_message": m}


def to_client(s): return [p for p in s.api.sent() if p.get("business_connection_id")]
def to_owner(s): return [p for p in s.api.sent() if p["chat_id"] == OWNER and not p.get("business_connection_id")]


# 1. Owner connects -> client message gets an answer through the business connection.
s, prompts = make()
s.handle(conn(OWNER)); s.handle(msg(CLIENT))
out = to_client(s)
assert len(out) == 1, s.api.calls
assert out[0]["business_connection_id"] == "C1" and out[0]["chat_id"] == CLIENT
assert "2000" in out[0]["text"]
assert "Покрытие 2000 рублей." in prompts[0], "knowledge not in prompt"
assert "Сколько стоит покрытие?" in prompts[0]

# 2. A stranger connects the same bot to THEIR account -> ignored, never answers there.
s, prompts = make()
s.handle(conn(STRANGER, cid="EVIL")); s.handle(msg(CLIENT, cid="EVIL"))
assert s.api.sent() == [] and prompts == [], "answered on a stranger's connection"

# 3. Unknown connection id (never saw business_connection) -> ignored.
s, prompts = make()
s.handle(msg(CLIENT, cid="NOPE"))
assert s.api.sent() == [] and prompts == []

# 4. Connection without the reply right / disabled later -> no answers.
s, _ = make(); s.handle(conn(OWNER, can_reply=False)); s.handle(msg(CLIENT))
assert to_client(s) == []
s, _ = make(); s.handle(conn(OWNER)); s.handle(conn(OWNER, enabled=False)); s.handle(msg(CLIENT))
assert to_client(s) == [], "answered after the owner disconnected the bot"

# 5. Owner writes in a chat himself -> silent in THAT chat for 12h, other chats still answered.
s, prompts = make()
s.handle(conn(OWNER))
s.handle(msg(OWNER, text="Сейчас отвечу сама", chat=CLIENT, mid=2))
s.handle(msg(CLIENT, mid=3))
assert to_client(s) == [], "talked over the owner"
s.handle(msg(CLIENT + 1, chat=CLIENT + 1, mid=4))
assert len(to_client(s)) == 1, "other chats must still be answered"
s.paused[str(CLIENT)] = time.time() - 1  # 12h later
s.handle(msg(CLIENT, mid=5))
assert len(to_client(s)) == 2

# 6. Model hands over -> client gets a neutral line, owner gets a notification.
s, _ = make(model_answer="[ВЛАДЕЛЬЦУ] клиент спрашивает про скидку для группы")
s.handle(conn(OWNER)); s.handle(msg(CLIENT, text="А скидка на группу из 5 человек есть?"))
assert len(to_client(s)) == 1 and "[ВЛАДЕЛЬЦУ]" not in to_client(s)[0]["text"], s.api.calls
assert len(to_owner(s)) == 1 and "скидк" in to_owner(s)[0]["text"], s.api.calls

# 7. Voice / photo / sticker -> no model call, owner notified.
s, prompts = make()
s.handle(conn(OWNER)); s.handle(msg(CLIENT, text=None, voice={"file_id": "x", "duration": 3}))
assert prompts == [] and len(to_owner(s)) == 1

# 8. Model failure -> nothing sent to the client (silence beats a broken answer), owner told.
s, _ = make()
def boom(system, prompt): raise RuntimeError("timeout")
s.model = boom
s.handle(conn(OWNER)); s.handle(msg(CLIENT))
assert to_client(s) == [] and len(to_owner(s)) == 1

# 9. Chat history is kept per chat and fed back.
s, prompts = make()
s.handle(conn(OWNER))
s.handle(msg(CLIENT, text="Меня зовут Оля", mid=10))
s.handle(msg(CLIENT, text="Как меня зовут?", mid=11))
assert "Меня зовут Оля" in prompts[1] and "Как меня зовут?" not in prompts[0]

# 10. The prompt says client text is data, never an instruction.
assert "не инструкц" in prompts[0].lower()

# 11. Connection survives a restart (state on disk).
s, _ = make(); s.handle(conn(OWNER))
s2 = sec.Secretary(owner_id=OWNER, state_dir=s.state_dir, knowledge_path=s.knowledge_path,
                   api=FakeApi(), model=lambda a, b: "Ок")
s2.handle(msg(CLIENT))
assert len(to_client(s2)) == 1, "forgot the owner's connection after restart"

# 12. Edited business messages are ignored.
s, prompts = make(); s.handle(conn(OWNER))
s.handle({"edited_business_message": msg(CLIENT)["business_message"]})
assert prompts == []

# 13. Ordinary messages to the bot itself (not business) are never sent to the model.
s, prompts = make(); s.handle(conn(OWNER))
s.handle({"message": {"message_id": 1, "from": {"id": STRANGER}, "chat": {"id": STRANGER}, "text": "hi"}})
assert prompts == []

# 14. Real model runner: claude is called with ALL tools off and no user settings/MCP.
argv = sec.claude_argv("sonnet")
i = argv.index("--tools"); assert argv[i + 1] == "", argv
for flag in ("-p", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"):
    assert flag in argv, flag
j = argv.index("--setting-sources"); assert argv[j + 1] == "", argv

# 15. Very long model answers are cut to Telegram's limit.
s, _ = make(model_answer="а" * 5000); s.handle(conn(OWNER)); s.handle(msg(CLIENT))
assert len(to_client(s)[0]["text"]) <= 4096

# 16. Allowlist: with only_chats set, every other chat is ignored (test runs, 12.11 incident:
#     the owner picked «all personal chats» instead of one test chat).
s, prompts = make()
s.only_chats = {CLIENT}
s.handle(conn(OWNER))
s.handle(msg(CLIENT + 5, chat=CLIENT + 5, mid=20))
assert prompts == [] and s.api.sent() == [], "answered outside the allowlist"
s.handle(msg(CLIENT, mid=21))
assert len(to_client(s)) == 1

# 17. Backlog after a restart: messages older than MAX_AGE_SEC are not answered.
s, prompts = make(); s.handle(conn(OWNER))
old = msg(CLIENT, mid=30); old["business_message"]["date"] = int(time.time()) - sec.MAX_AGE_SEC - 10
s.handle(old)
assert prompts == [] and s.api.sent() == []

print("PASS")
