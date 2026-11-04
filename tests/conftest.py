"""Testy CRM: każda sesja testów na świeżej bazie SQLite, bez wątków w tle i bez wysyłki maili."""
import os
import sys
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="crm-test-"), "test.db")
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_DB}", "SECRET_KEY": "test", "JOURNEY_SCAN": "0", "AUTO_REALIZE": "0",
    "MAIL_SYNC": "0", "MAIL_REVIEW": "0", "LT_AUTO_MAIL": "0", "CLICKUP_API_TOKEN": "", "SMTP_HOST": "",
})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
