#!/usr/bin/env python3
"""Gmail + Google Calendar for agents on Grok — without claude.ai connectors (Vika 25.11, variant 1).

Mail: IMAP/SMTP with a Google «app password» (2-step verification on). Calendar: the calendar's secret iCal
address (read-only). Credentials: ~/.grok-google.env (600) — GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GCAL_ICS_URL.

  google_mail.py list [N]                 last N letters in the inbox (default 10)
  google_mail.py search "запрос" [N]      Gmail search syntax: from:… subject:… newer_than:2d
  google_mail.py read UID                 full text of one letter
  google_mail.py send TO "тема" "текст"   send a letter (only when the owner asked to send exactly this)
  google_mail.py calendar [ДНЕЙ]          events from today for N days (default 7), owner's time zone
"""
import datetime as dt, email, email.header, email.utils, imaplib, os, re, smtplib, sys, urllib.request
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

CREDS = os.environ.get("GROK_GOOGLE_ENV", os.path.expanduser("~/.grok-google.env"))


def creds():
    env = {}
    try:
        for line in open(CREDS, encoding="utf-8"):
            if "=" in line and not line.startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def need(env, *keys):
    miss = [k for k in keys if not env.get(k)]
    if miss:
        sys.exit("Google не подключён: нет " + ", ".join(miss) + f" в {CREDS}. Владелец подключает командой "
                 "«claude-to-grok.py google» (урок «Chatplace и Google», версия для Grok).")


def dec(s):
    out = ""
    for part, enc in email.header.decode_header(s or ""):
        out += part.decode(enc or "utf-8", "replace") if isinstance(part, bytes) else part
    return out


def body_text(msg):
    parts = msg.walk() if msg.is_multipart() else [msg]
    html = ""
    for p in parts:
        ct = p.get_content_type()
        if p.get_content_disposition() == "attachment":
            continue
        raw = p.get_payload(decode=True) or b""
        txt = raw.decode(p.get_content_charset() or "utf-8", "replace")
        if ct == "text/plain":
            return txt
        if ct == "text/html" and not html:
            html = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"[ \t]+", " ", html)


def imap(env):
    m = imaplib.IMAP4_SSL("imap.gmail.com")
    m.login(env["GMAIL_ADDRESS"], env["GMAIL_APP_PASSWORD"].replace(" ", ""))
    m.select("INBOX", readonly=True)
    return m


def headers(m, uids):
    for uid in reversed(uids):
        _, data = m.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        h = email.message_from_bytes(data[0][1])
        print(f"[{uid.decode()}] {dec(h['Date'])[:25]} | {dec(h['From'])[:40]} | {dec(h['Subject'])[:80]}")


def cmd_list(env, n=10):
    m = imap(env)
    _, data = m.uid("search", None, "ALL")
    headers(m, data[0].split()[-int(n):])


def cmd_search(env, q, n=10):
    m = imap(env)
    _, data = m.uid("search", None, "X-GM-RAW", f'"{q}"')
    uids = data[0].split()
    print(f"Найдено: {len(uids)}")
    headers(m, uids[-int(n):])


def cmd_read(env, uid):
    m = imap(env)
    _, data = m.uid("fetch", uid, "(BODY.PEEK[])")
    msg = email.message_from_bytes(data[0][1])
    print(f"От: {dec(msg['From'])}\nКому: {dec(msg['To'])}\nДата: {msg['Date']}\nТема: {dec(msg['Subject'])}\n")
    print(body_text(msg)[:20000])


def cmd_send(env, to, subject, text):
    msg = MIMEText(text, "plain", "utf-8")
    msg["From"], msg["To"], msg["Subject"] = env["GMAIL_ADDRESS"], to, subject
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(env["GMAIL_ADDRESS"], env["GMAIL_APP_PASSWORD"].replace(" ", ""))
        s.send_message(msg)
    print(f"Отправлено: {to} — {subject}")


# --- calendar: minimal iCal reader (single + daily/weekly repeating events) ---------------------
WD = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text)


def parse_dt(value, params):
    if re.fullmatch(r"\d{8}", value):
        return dt.datetime.strptime(value, "%Y%m%d").date()
    tz = dt.timezone.utc if value.endswith("Z") else ZoneInfo(params.get("TZID", "UTC"))
    return dt.datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S").replace(tzinfo=tz)


def as_date(x):
    return x.date() if isinstance(x, dt.datetime) else x


def events(ics):
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfold(ics), re.S):
        ev = {}
        for line in block.strip().splitlines():
            m = re.match(r"([A-Z-]+)((?:;[^:]+)?):(.*)", line)
            if not m:
                continue
            params = dict(p.split("=", 1) for p in m.group(2).strip(";").split(";") if "=" in p)
            ev[m.group(1)] = (m.group(3).replace("\\,", ",").replace("\\n", " "), params)
        if "DTSTART" in ev:
            yield ev


def occurrences(ev, start, end):
    first = parse_dt(*ev["DTSTART"])
    rule = dict(p.split("=", 1) for p in ev["RRULE"][0].split(";")) if "RRULE" in ev else None
    if not rule:
        yield first
        return
    until = as_date(parse_dt(rule["UNTIL"], {})) if "UNTIL" in rule else None
    count, step, n = int(rule.get("COUNT", 10 ** 6)), int(rule.get("INTERVAL", 1)), 0
    days = [WD[d[-2:]] for d in rule.get("BYDAY", "").split(",") if d] if rule.get("FREQ") == "WEEKLY" else []
    cur = first
    while n < count:
        d = as_date(cur)
        if (until and d > until) or d > end:
            return
        if not days or d.weekday() in days:
            n += 1
            if d >= start:
                yield cur
        if rule.get("FREQ") == "DAILY":
            cur += dt.timedelta(days=step)
        elif rule.get("FREQ") == "WEEKLY":
            cur += dt.timedelta(days=1 if days else 7 * step)
        else:
            return


def agenda(ics, start, end, tz):
    rows = []
    for ev in events(ics):
        for t in occurrences(ev, start, end):
            local = t.astimezone(tz) if isinstance(t, dt.datetime) else t
            if start <= as_date(local) <= end:
                when = local.strftime("%d.%m %H:%M") if isinstance(local, dt.datetime) else local.strftime("%d.%m весь день")
                rows.append((as_date(local), when, ev.get("SUMMARY", ("(без названия)", {}))[0],
                             ev.get("LOCATION", ("", {}))[0]))
    return [r[1:] for r in sorted(rows, key=lambda r: (r[0], r[1]))]


def cmd_calendar(env, days=7, tz=None):
    tz = ZoneInfo(tz or env.get("OWNER_TZ") or "Europe/Moscow")
    ics = urllib.request.urlopen(env["GCAL_ICS_URL"], timeout=30).read().decode("utf-8", "replace")
    start = dt.datetime.now(tz).date()
    end = start + dt.timedelta(days=int(days))
    rows = agenda(ics, start, end, tz)
    print(f"Встречи {start:%d.%m}–{end:%d.%m} ({tz.key}): {len(rows)}")
    for when, title, place in rows:
        print(f"• {when} — {title}" + (f" ({place})" if place else ""))


def main(argv):
    if len(argv) < 2 or argv[1] not in ("list", "search", "read", "send", "calendar"):
        sys.exit(__doc__)
    env, c, a = creds(), argv[1], argv[2:]
    if c == "calendar":
        need(env, "GCAL_ICS_URL")
        return cmd_calendar(env, *a[:1])
    need(env, "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD")
    {"list": cmd_list, "search": cmd_search, "read": cmd_read, "send": cmd_send}[c](env, *a)


if __name__ == "__main__":
    main(sys.argv)
