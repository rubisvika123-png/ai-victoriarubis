#!/usr/bin/env python3
"""Tests for google_mail.py calendar reader (mail needs a live Google account — tested by hand 25.11)."""
import datetime as dt, os, sys, unittest
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import google_mail as gm  # noqa: E402

ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=Europe/Moscow:20261126T110000
SUMMARY:Консультация\\, Ольга
LOCATION:Zoom
END:VEVENT
BEGIN:VEVENT
DTSTART:20261127T070000Z
SUMMARY:Позвонить в школу
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261128
SUMMARY:День рождения мамы
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=Europe/Moscow:20261103T100000
RRULE:FREQ=WEEKLY;BYDAY=TU,TH
SUMMARY:Приём клиентов
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=Europe/Moscow:20261101T090000
RRULE:FREQ=DAILY;UNTIL=20261124T000000Z
SUMMARY:Старое ежедневное
END:VEVENT
END:VCALENDAR
"""


class T(unittest.TestCase):
    def test_agenda_owner_zone_repeats_and_all_day(self):
        tz = ZoneInfo("Europe/Moscow")
        rows = gm.agenda(ICS, dt.date(2026, 11, 26), dt.date(2026, 11, 28), tz)
        self.assertIn(("26.11 11:00", "Консультация, Ольга", "Zoom"), rows)
        self.assertIn(("27.11 10:00", "Позвонить в школу", ""), rows)       # 07:00Z → 10:00 МСК
        self.assertIn(("28.11 весь день", "День рождения мамы", ""), rows)
        self.assertIn(("26.11 10:00", "Приём клиентов", ""), rows)          # Thu 26.11, weekly TU,TH
        self.assertFalse(any("Старое" in r[1] for r in rows))              # ended before

    def test_missing_creds_message(self):
        with self.assertRaises(SystemExit) as e:
            gm.need({}, "GMAIL_ADDRESS")
        self.assertIn("claude-to-grok.py google", str(e.exception.code))


if __name__ == "__main__":
    unittest.main()
