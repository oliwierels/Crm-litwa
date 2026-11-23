from flask import Flask, render_template, request, redirect, url_for, jsonify, make_response, abort, session, g, \
    has_request_context
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date, timedelta, timezone
from email.mime.text import MIMEText
from urllib.parse import quote
from werkzeug.middleware.proxy_fix import ProxyFix
import hashlib, hmac, json, os, re, secrets, smtplib, threading, time
import requests
import journey
import logistics
import market as markets
import offers
import poczta
import health
import pricing
import sales_rules


def _include(part):
    """Wczytuje wspólną część kodu z parts/<part>.py do przestrzeni nazw crm.py — w tym miejscu pliku (kolejność
    jak przed podziałem). Części są identyczne w CRM PL i CRM LT/DE (docs/PORZADKI.md)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parts", f"{part}.py")
    with open(path, encoding="utf-8") as fh:
        exec(compile(fh.read(), path, "exec"), globals())

try:
    from zoneinfo import ZoneInfo
    WARSAW_TZ = ZoneInfo("Europe/Warsaw")
except Exception:
    WARSAW_TZ = None

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(16))
_db_url = os.environ.get("DATABASE_URL", "sqlite:///crm.db")
if _db_url.startswith("postgres://"):  # Render/Heroku podają stary scheme
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # limit na wiadomości głosowe w komentarzach
# logowanie (gdy ustawione CRM_PASSWORD) trzyma się 30 dni; Secure tylko na Railway (lokalnie jest http)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PUBLIC_DOMAIN"))
# Railway stoi za proxy: prawdziwe IP klienta i https z nagłówków X-Forwarded-*
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
db = SQLAlchemy(app)

_include("rynki")


USERS = [
    {"id": 1, "name": "Oliwier", "color": "#6366f1", "avatar": "O", "email": "o.latek@33bots.lt",
     "full_name": "Oliwier Latek", "phone": "+48 531 408 004"},
    {"id": 2, "name": "Bartek", "color": "#10b981", "avatar": "B", "email": "b.wysocki@33bots.pl",
     "full_name": "Bartosz Wysocki", "phone": "+48 601 499 947"},
    {"id": 3, "name": "Jerzy", "color": "#f59e0b", "avatar": "J", "email": "j.chmielewski@33bots.pl",
     "full_name": "Jerzy Chmielewski", "phone": ""},
]
USERS_BY_ID = {u["id"]: u for u in USERS}

STAGES = ["Nowy", "Oferta", "Wygrany", "Zrealizowany", "Stracony"]
# etapy usunięte z lejka -> gdzie trafiają ich leady (migracja przy starcie, stare nazwy od asystenta)
REMOVED_STAGES = {"Kontakt": "Nowy", "Negocjacje": "Oferta"}
# wygrane: Wygrany = czeka na realizację, Zrealizowany = dzień realizacji minął (CRM przenosi sam)
WON_STAGES = ("Wygrany", "Zrealizowany")
# Wygrane leady z CRM LT / CRM DE wracają tutaj — z flagą 🇱🇹/🇩🇪 przed nazwą — do logistyki (ekipa, robot, trasa)
# i do zestawień WYGRANYCH (dashboard, statystyki, panel skuteczności, MCP): to jedyna wspólna agregacja danych
# obu CRM. Klienta prowadzi CRM LT/DE (maile, umowa, faktura) i on nadpisuje te leady; polski CRM nic do nich
# nie wysyła (Customer Journey, kampanie), nie liczy ich jako nowych / straconych i nie robi z nimi automatyzacji.
MARKET_COPY_SOURCES = ("CRM LT", "CRM DE")
STAGE_COLORS = {  # Kontakt/Negocjacje zostają tylko dla starych wpisów w historii etapów
    "Nowy": "#6366f1",
    "Kontakt": "#3b82f6",
    "Oferta": "#f59e0b",
    "Negocjacje": "#ef4444",
    "Wygrany": "#10b981",
    "Zrealizowany": "#14b8a6",
    "Stracony": "#6b7280",
}

ACTIVITY_KINDS = ["call", "sms", "email", "online", "meeting", "task", "note"]
ACTIVITY_KIND_ICONS = {"call": "📞", "sms": "💬", "email": "✉️", "online": "💻", "meeting": "🤝", "task": "📌",
                       "note": "📝", "decision": "⚖️"}
ACTIVITY_KIND_LABELS = {
    "call": "Telefon", "sms": "SMS", "email": "E-mail", "online": "Prezentacja online",
    "meeting": "Spotkanie na żywo", "task": "Zadanie", "note": "Notatka", "decision": "Decyzja",
}

ROBOT_MODELS = [
    "Unitree G1",
    "Unitree B2 (piesek)",
    "Oba (G1 + piesek)",
]

# stare nazwy modeli -> aktualne (migracja danych przy starcie)
ROBOT_MODEL_ALIASES = {
    "Unitree G1 + B2 (pakiet)": "Oba (G1 + piesek)",
}

CURRENCIES = ["EUR", "PLN", "USD", "GBP"]

QUALITY_LEVELS = ["Dobry", "Średni", "Słaby"]
QUALITY_COLORS = {"Dobry": "#22c55e", "Średni": "#f59e0b", "Słaby": "#ef4444"}
QUALITY_ICONS = {"Dobry": "🟢", "Średni": "🟡", "Słaby": "🔴"}


class Lead(db.Model):
    __tablename__ = "leads"
    id = db.Column(db.Integer, primary_key=True)
    company = db.Column(db.String(200), nullable=False)
    contact_name = db.Column(db.String(200))
    contact_email = db.Column(db.String(200))
    contact_email2 = db.Column(db.String(200))  # drugi adres klienta (np. asystentka, księgowość)
    pl_id = db.Column(db.Integer, nullable=True)  # id leada w polskim CRM, jeśli został stamtąd przeniesiony
    pl_copy_id = db.Column(db.Integer, nullable=True)  # kopia wygranego leada w polskim CRM (logistyka)
    pl_copy_hash = db.Column(db.String(40), nullable=True)  # odcisk danych wysłanych ostatnio do tej kopii
    market = db.Column(db.String(2), nullable=False, default=_default_market, index=True)  # pl | lt | de
    contact_phone = db.Column(db.String(50))
    stage = db.Column(db.String(50), nullable=False, default="Nowy")
    robot_model = db.Column(db.String(100))
    rental_days = db.Column(db.Integer)
    value_pln = db.Column(db.Float)
    currency = db.Column(db.String(8), default=lambda: markets.get(_default_market())["currency"])
    quality = db.Column(db.String(16), default="Średni")
    contract_signed = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    assigned_to = db.Column(db.Integer, default=1)
    source = db.Column(db.String(100))
    location = db.Column(db.String(200))
    event_date = db.Column(db.DateTime, nullable=True)
    event_end_date = db.Column(db.DateTime, nullable=True)
    clickup_task_id = db.Column(db.String(64), nullable=True)
    clickup_pending = db.Column(db.Boolean, default=False)  # True = czeka w zakładce "Z ClickUp"
    lt_mail_at = db.Column(db.DateTime, nullable=True)  # wysłany mail do litewskiego leada (prośba o dane)
    # proces sprzedaży (sales_rules.py): typ klienta, kraj, powód straty, potwierdzenie wygranej, telefon potwierdzający
    client_type = db.Column(db.String(20), nullable=True)  # firma | prywatna | agencja | partner
    country = db.Column(db.String(4), nullable=True)  # PL / LT / DE / RO…; pusty = kraj rynku albo podpowiedź z danych
    lost_reason = db.Column(db.String(30), nullable=True)
    lost_comment = db.Column(db.Text, nullable=True)
    win_confirmation = db.Column(db.String(20), nullable=True)  # umowa | mail
    call_answers = db.Column(db.Text, nullable=True)  # JSON: odpowiedzi z telefonu potwierdzającego
    response_alert_at = db.Column(db.DateTime, nullable=True)  # kiedy CRM zgłosił przekroczony czas reakcji
    form_token = db.Column(db.String(40), nullable=True, index=True)  # link do formularza dla klienta
    form_filled_at = db.Column(db.DateTime, nullable=True)  # klient wypełnił formularz
    handled_at = db.Column(db.DateTime, nullable=True)  # lead obsłużony (telefon / mail / oferta) — znika z alarmu
    handled_by = db.Column(db.Integer, nullable=True)
    handled_how = db.Column(db.String(40), nullable=True)
    # dane do faktury (zakładka „Dane do faktury” w leadzie; NIP -> reszta z Białej listy MF)
    nip = db.Column(db.String(20), nullable=True)
    billing_name = db.Column(db.String(300), nullable=True)
    billing_street = db.Column(db.String(200), nullable=True)
    billing_postcode = db.Column(db.String(20), nullable=True)
    billing_city = db.Column(db.String(120), nullable=True)
    billing_country = db.Column(db.String(2), nullable=True)
    billing_email = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    activities = db.relationship("Activity", backref="lead", cascade="all, delete-orphan")

    @property
    def emails(self):
        """Poprawne adresy klienta (główny, potem drugi), bez powtórzeń — do wysyłki i wyboru adresatów."""
        return email_list(self.contact_email, self.contact_email2)


_include("modele")


class Journey(db.Model):
    """Customer Journey wygranego leada: umowa (+ baza wiedzy) → wydarzenie → po wydarzeniu."""
    __tablename__ = "journeys"
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False, unique=True)
    contract_status = db.Column(db.String(20), nullable=False, default="do_wyslania")  # do_wyslania | wyslana | podpisana
    contract_sent_at = db.Column(db.DateTime)
    contract_signed_at = db.Column(db.DateTime)
    contract_signed_via = db.Column(db.String(20))  # mail (odesłana) | recznie (np. podpisana na miejscu)
    contract_reminded_at = db.Column(db.DateTime)
    contract_reminders = db.Column(db.Integer, default=0)
    contract_fields = db.Column(db.Text)  # JSON — pola umowy poprawione w okienku przed wysyłką
    contract_subject = db.Column(db.String(300))  # temat maila z umową (przypomnienie idzie jako „Re: …”)
    kb_status = db.Column(db.String(20), nullable=False, default="brak")  # brak | wyslana | otrzymana
    kb_sent_at = db.Column(db.DateTime)
    kb_received_at = db.Column(db.DateTime)
    kb_reminded_at = db.Column(db.DateTime)
    kb_reminders = db.Column(db.Integer, default=0)
    after = db.Column(db.Text)  # JSON {"faktura": true, ...}
    closed = db.Column(db.Boolean, default=False)
    closed_at = db.Column(db.DateTime)
    needs_review = db.Column(db.Boolean, default=False)  # lead sprzed Customer Journey — status do potwierdzenia
    msgids = db.Column(db.Text)  # Message-ID naszych maili — odpowiedź klienta rozpoznajemy po wątku
    invoice_uuid = db.Column(db.String(64))  # faktura wystawiona z CRM w inFakt
    invoice_number = db.Column(db.String(64))
    invoice_at = db.Column(db.DateTime)
    invoice_issuer = db.Column(db.String(40))  # wystawca (konto inFakt): "" = INFAKT_API_KEY, np. "oliwier"
    robot_persona = db.Column(db.Text)  # JSON: osobowość robota na to wydarzenie (agent ElevenLabs) — robot_agent.py
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    lead = db.relationship("Lead", backref=db.backref("journey", uselist=False, cascade="all, delete-orphan"))


_include("modele_journey")


class Campaign(db.Model):
    """Blitzkrieg: jedna wiadomość do wielu leadów, wysyłana powoli (np. 5 na godzinę)."""
    __tablename__ = "campaigns"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(300), nullable=False)
    body = db.Column(db.Text, nullable=False)
    sender_id = db.Column(db.Integer, nullable=False)
    rate_per_hour = db.Column(db.Integer, nullable=False, default=5)
    hour_from = db.Column(db.Integer, default=8)  # wysyłka tylko w godzinach (czas warszawski)
    hour_to = db.Column(db.Integer, default=20)
    status = db.Column(db.String(20), nullable=False, default="szkic")  # szkic | trwa | pauza | koniec
    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    started_at = db.Column(db.DateTime)
    last_sent_at = db.Column(db.DateTime)
    market = db.Column(db.String(2), nullable=False, default=_default_market)  # pl / lt / de — język i nadawca

    recipients = db.relationship("CampaignRecipient", backref="campaign", cascade="all, delete-orphan",
                                 order_by="CampaignRecipient.id")


class CampaignRecipient(db.Model):
    __tablename__ = "campaign_recipients"
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=False, index=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=True)
    email = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(200))
    company = db.Column(db.String(200))
    status = db.Column(db.String(20), nullable=False, default="czeka")  # czeka | wyslany | blad | pominiety
    error = db.Column(db.String(300))
    sent_at = db.Column(db.DateTime)

    lead = db.relationship("Lead")


class SentOffer(db.Model):
    """Oferta PDF przygotowana przez prepare_offer: szkic do sprawdzenia, potem wysłana z CRM."""
    __tablename__ = "sent_offers"
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=True)
    filename = db.Column(db.String(200), nullable=False)
    pdf = db.Column(db.LargeBinary, nullable=False)
    data = db.Column(db.Text)  # JSON z danymi oferty
    to_addr = db.Column(db.String(200))
    subject = db.Column(db.String(300))
    body = db.Column(db.Text)
    price = db.Column(db.String(40))
    sender_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sent_at = db.Column(db.DateTime, nullable=True)  # None = szkic czeka na sprawdzenie i „Wyślij”

    lead = db.relationship(
        "Lead", backref=db.backref("sent_offers", cascade="all, delete-orphan", order_by="SentOffer.created_at"),
    )


class InboxMessage(db.Model):
    """Mail ze skrzynki kontakt@ przeczytany przez import zapytań (każdy tylko raz)."""
    __tablename__ = "inbox_messages"
    id = db.Column(db.Integer, primary_key=True)
    message_key = db.Column(db.String(320), unique=True, nullable=False, index=True)  # Message-ID albo hash
    from_addr = db.Column(db.String(200))
    subject = db.Column(db.String(300))
    received_at = db.Column(db.DateTime)
    body = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="nowy")  # lead | dopisany | pominiety | blad
    reason = db.Column(db.String(300))
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    mailbox = db.Column(db.String(200))  # skrzynka zapytań (kontakt@ danego rynku)
    market = db.Column(db.String(2), nullable=False, default=lambda: markets.DEFAULT)

    lead = db.relationship("Lead")


class MailMessage(db.Model):
    """Mail ze skrzynki firmowej (Odebrane albo Wysłane) — zakładka Poczta. Tłumaczenie na polski (maile w języku
    innego rynku) trzymamy obok oryginału, żeby każdy mail tłumaczyć tylko raz."""
    __tablename__ = "mail_messages"
    __table_args__ = (db.UniqueConstraint("mailbox", "folder", "uidvalidity", "uid", name="uq_mail_uid"),)
    id = db.Column(db.Integer, primary_key=True)
    mailbox = db.Column(db.String(200), index=True, nullable=False)
    market = db.Column(db.String(2), index=True)  # pl | lt | de — z domeny skrzynki
    folder = db.Column(db.String(10), nullable=False)  # in | sent
    uid = db.Column(db.Integer, nullable=True)  # None = wysłany z CRM, czeka na kopię z Wysłanych
    uidvalidity = db.Column(db.String(40), nullable=True)
    message_id = db.Column(db.String(400), index=True)
    in_reply_to = db.Column(db.String(400))
    refs = db.Column(db.Text)
    thread_key = db.Column(db.String(400), index=True)
    from_addr = db.Column(db.String(300))
    from_name = db.Column(db.String(300))
    to_addrs = db.Column(db.Text)
    to_names = db.Column(db.Text)
    cc_addrs = db.Column(db.Text)
    addrs = db.Column(db.Text)  # " a@x.pl b@y.lt " — szukanie po adresie leada
    subject = db.Column(db.String(500))
    date = db.Column(db.DateTime, index=True)
    body_text = db.Column(db.Text)
    snippet = db.Column(db.String(300))
    attachments = db.Column(db.Text)  # JSON [{n, name, type, size}]
    truncated = db.Column(db.Boolean, default=False)
    lang = db.Column(db.String(8))
    subject_pl = db.Column(db.String(600))
    body_pl = db.Column(db.Text)
    translated_at = db.Column(db.DateTime)
    translate_error = db.Column(db.String(300))
    quoted_pl = db.Column(db.Text)  # tłumaczenie cytowanej historii (robione przy pierwszym rozwinięciu)
    seen = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class GeoCache(db.Model):
    """Zapamiętane współrzędne miejsc spoza wbudowanej listy miast (pytamy OpenStreetMap raz)."""
    __tablename__ = "geo_cache"
    id = db.Column(db.Integer, primary_key=True)
    place_key = db.Column(db.String(200), unique=True, nullable=False)  # opis miejsca po normalize()
    lat = db.Column(db.Float, nullable=True)  # None = nie znaleziono
    lon = db.Column(db.Float, nullable=True)
    label = db.Column(db.String(200))
    checked_at = db.Column(db.DateTime, default=datetime.utcnow)


def ensure_schema():
    # brakujące kolumny (działa na SQLite i Postgresie); rynek w starych wierszach = rynek domyślny serwisu
    mkt, cur = markets.DEFAULT, markets.get(markets.DEFAULT)["currency"]
    _j_cols = {c["name"] for c in db.inspect(db.engine).get_columns("journeys")}
    with db.engine.begin() as conn:  # docs/MIGRACJE.md
        if "invoice_issuer" not in _j_cols:  # wystawca faktury — kilka kont inFakt
            conn.exec_driver_sql("ALTER TABLE journeys ADD COLUMN invoice_issuer VARCHAR(40)")
        if "robot_persona" not in _j_cols:  # osobowość robota na wydarzenie (agent ElevenLabs)
            conn.exec_driver_sql("ALTER TABLE journeys ADD COLUMN robot_persona TEXT")
    _mt_cols = {c["name"] for c in db.inspect(db.engine).get_columns("mail_tasks")}
    with db.engine.begin() as conn:  # sprawy z poczty: sprawdzanie, czy już załatwione (docs/MIGRACJE.md)
        for col, typ in (("checked_at", "TIMESTAMP NULL"), ("check_note", "TEXT"),
                         ("market", f"VARCHAR(2) NOT NULL DEFAULT '{mkt}'")):
            if col not in _mt_cols:
                conn.exec_driver_sql(f"ALTER TABLE mail_tasks ADD COLUMN {col} {typ}")
    insp = db.inspect(db.engine)
    if "mail_messages" in insp.get_table_names():
        mcols = [col["name"] for col in insp.get_columns("mail_messages")]
        with db.engine.begin() as conn:
            if "market" not in mcols:  # rynek maila z domeny skrzynki (.de → de), reszta — rynek domyślny
                conn.exec_driver_sql(f"ALTER TABLE mail_messages ADD COLUMN market VARCHAR(2) DEFAULT '{mkt}'")
                if "de" in markets.MARKETS:
                    conn.exec_driver_sql("UPDATE mail_messages SET market = 'de' WHERE mailbox LIKE '%.de'")
            for col, typ in (("subject_pl", "VARCHAR(600)"), ("body_pl", "TEXT"), ("translated_at", "TIMESTAMP NULL"),
                             ("translate_error", "VARCHAR(300)"), ("quoted_pl", "TEXT")):  # tłumaczenie na polski
                if col not in mcols:
                    conn.exec_driver_sql(f"ALTER TABLE mail_messages ADD COLUMN {col} {typ}")
    if "inbox_messages" in insp.get_table_names():
        icols = [col["name"] for col in insp.get_columns("inbox_messages")]
        with db.engine.begin() as conn:
            if "mailbox" not in icols:
                conn.exec_driver_sql("ALTER TABLE inbox_messages ADD COLUMN mailbox VARCHAR(200)")
            if "market" not in icols:
                conn.exec_driver_sql(f"ALTER TABLE inbox_messages ADD COLUMN market VARCHAR(2) NOT NULL DEFAULT '{mkt}'")
    if "campaigns" in insp.get_table_names():
        if "market" not in [col["name"] for col in insp.get_columns("campaigns")]:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(f"ALTER TABLE campaigns ADD COLUMN market VARCHAR(2) NOT NULL DEFAULT '{mkt}'")
    cols = [c["name"] for c in db.inspect(db.engine).get_columns("leads")]
    with db.engine.begin() as conn:
        if "contact_email2" not in cols:
            conn.exec_driver_sql("ALTER TABLE leads ADD COLUMN contact_email2 VARCHAR(200)")
        if "market" not in cols:
            conn.exec_driver_sql(f"ALTER TABLE leads ADD COLUMN market VARCHAR(2) NOT NULL DEFAULT '{mkt}'")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_leads_market ON leads (market)")
        for col, typ in (("handled_at", "TIMESTAMP NULL"), ("handled_by", "INTEGER"), ("handled_how", "VARCHAR(40)")):
            if col not in cols:
                conn.exec_driver_sql(f"ALTER TABLE leads ADD COLUMN {col} {typ}")
        # proces sprzedaży — tylko nowe kolumny (cofnięcie: docs/MIGRACJE.md)
        for col, typ in (("client_type", "VARCHAR(20)"), ("country", "VARCHAR(4)"), ("lost_reason", "VARCHAR(30)"),
                         ("lost_comment", "TEXT"), ("win_confirmation", "VARCHAR(20)"), ("call_answers", "TEXT"),
                         ("response_alert_at", "TIMESTAMP NULL")):
            if col not in cols:
                conn.exec_driver_sql(f"ALTER TABLE leads ADD COLUMN {col} {typ}")
        sh_cols = [c["name"] for c in db.inspect(db.engine).get_columns("stage_history")]
        for col, typ in (("reason", "VARCHAR(30)"), ("note", "TEXT")):
            if col not in sh_cols:
                conn.exec_driver_sql(f"ALTER TABLE stage_history ADD COLUMN {col} {typ}")
        if "pl_id" not in cols:
            conn.exec_driver_sql("ALTER TABLE leads ADD COLUMN pl_id INTEGER")
        if "pl_copy_id" not in cols:
            conn.exec_driver_sql("ALTER TABLE leads ADD COLUMN pl_copy_id INTEGER")
        if "pl_copy_hash" not in cols:
            conn.exec_driver_sql("ALTER TABLE leads ADD COLUMN pl_copy_hash VARCHAR(40)")
        if "lt_mail_at" not in cols:
            conn.exec_driver_sql("ALTER TABLE leads ADD COLUMN lt_mail_at TIMESTAMP NULL")
        if "form_token" not in cols:
            conn.exec_driver_sql("ALTER TABLE leads ADD COLUMN form_token VARCHAR(40)")
        if "form_filled_at" not in cols:
            conn.exec_driver_sql("ALTER TABLE leads ADD COLUMN form_filled_at TIMESTAMP NULL")
        if "event_date" not in cols:
            conn.exec_driver_sql("ALTER TABLE leads ADD COLUMN event_date TIMESTAMP NULL")
        if "currency" not in cols:
            conn.exec_driver_sql(f"ALTER TABLE leads ADD COLUMN currency VARCHAR(8) DEFAULT '{cur}'")
        if "location" not in cols:
            conn.exec_driver_sql("ALTER TABLE leads ADD COLUMN location VARCHAR(200)")
        if "event_end_date" not in cols:
            conn.exec_driver_sql("ALTER TABLE leads ADD COLUMN event_end_date TIMESTAMP NULL")
        if "quality" not in cols:
            conn.exec_driver_sql("ALTER TABLE leads ADD COLUMN quality VARCHAR(16) DEFAULT 'Średni'")
        if "contract_signed" not in cols:
            default_val = "0" if db.engine.dialect.name == "sqlite" else "FALSE"
            conn.exec_driver_sql(f"ALTER TABLE leads ADD COLUMN contract_signed BOOLEAN DEFAULT {default_val}")
        if "clickup_task_id" not in cols:
            conn.exec_driver_sql("ALTER TABLE leads ADD COLUMN clickup_task_id VARCHAR(64)")
        if "clickup_pending" not in cols:
            default_val = "0" if db.engine.dialect.name == "sqlite" else "FALSE"
            conn.exec_driver_sql(f"ALTER TABLE leads ADD COLUMN clickup_pending BOOLEAN DEFAULT {default_val}")
        for col, sql_type in (("nip", "VARCHAR(20)"), ("billing_name", "VARCHAR(300)"), ("billing_street", "VARCHAR(200)"),
                              ("billing_postcode", "VARCHAR(20)"), ("billing_city", "VARCHAR(120)"),
                              ("billing_country", "VARCHAR(2)"), ("billing_email", "VARCHAR(200)")):
            if col not in cols:
                conn.exec_driver_sql(f"ALTER TABLE leads ADD COLUMN {col} {sql_type} NULL")
        act_cols_full = db.inspect(db.engine).get_columns("activities")
        act_cols = [c["name"] for c in act_cols_full]
        if "voice_note" not in act_cols:
            conn.exec_driver_sql("ALTER TABLE activities ADD COLUMN voice_note TEXT NULL")
        if "outcome" not in act_cols:
            conn.exec_driver_sql("ALTER TABLE activities ADD COLUMN outcome VARCHAR(20) NULL")
        if "created_by" not in act_cols:  # kto utworzył / zlecił zadanie (raport dnia: zlecone zadania)
            conn.exec_driver_sql("ALTER TABLE activities ADD COLUMN created_by INTEGER NULL")
        # lead_id przestało być wymagane (zadania własne, bez firmy) — stare bazy mają NOT NULL
        lead_id_col = next((c for c in act_cols_full if c["name"] == "lead_id"), None)
        if lead_id_col is not None and not lead_id_col["nullable"]:
            if db.engine.dialect.name == "postgresql":
                conn.exec_driver_sql("ALTER TABLE activities ALTER COLUMN lead_id DROP NOT NULL")
            else:
                # SQLite nie umie zdjąć NOT NULL — przebudowa tabeli wg aktualnego modelu
                from sqlalchemy.schema import CreateTable
                ddl = str(CreateTable(Activity.__table__).compile(db.engine))
                ddl = ddl.replace("CREATE TABLE activities", "CREATE TABLE activities_new", 1)
                conn.exec_driver_sql(ddl)
                common = ", ".join(c["name"] for c in act_cols_full)
                conn.exec_driver_sql(f"INSERT INTO activities_new ({common}) SELECT {common} FROM activities")
                conn.exec_driver_sql("DROP TABLE activities")
                conn.exec_driver_sql("ALTER TABLE activities_new RENAME TO activities")
        led_cols = [c["name"] for c in db.inspect(db.engine).get_columns("lead_event_dates")]
        if "location" not in led_cols:
            conn.exec_driver_sql("ALTER TABLE lead_event_dates ADD COLUMN location VARCHAR(200) NULL")
        op_cols = [c["name"] for c in db.inspect(db.engine).get_columns("logistics_operators")]
        if "user_id" not in op_cols:
            conn.exec_driver_sql("ALTER TABLE logistics_operators ADD COLUMN user_id INTEGER NULL")
        crew_cols = [c["name"] for c in db.inspect(db.engine).get_columns("logistics_crews")]
        if "robots_count" not in crew_cols:
            conn.exec_driver_sql("ALTER TABLE logistics_crews ADD COLUMN robots_count INTEGER DEFAULT 1")
        if "operator2_id" not in crew_cols:
            conn.exec_driver_sql("ALTER TABLE logistics_crews ADD COLUMN operator2_id INTEGER NULL")
        if "robot2_id" not in crew_cols:
            conn.exec_driver_sql("ALTER TABLE logistics_crews ADD COLUMN robot2_id INTEGER NULL")
    # zamień stare nazwy modeli robotów na aktualne
    for old, new in ROBOT_MODEL_ALIASES.items():
        Lead.query.filter_by(robot_model=old).update({"robot_model": new})
    Lead.query.filter(Lead.currency.is_(None)).update({"currency": cur})
    Lead.query.filter(Lead.quality.is_(None)).update({"quality": "Średni"})
    Lead.query.filter(Lead.contract_signed.is_(None)).update({"contract_signed": False})
    Lead.query.filter(Lead.clickup_pending.is_(None)).update({"clickup_pending": False})
    db.session.commit()
    # backfill: leady z ustawionym event_date sprzed wprowadzenia tabeli konkretnych dni
    # (albo nietknięte od tamtej pory) nie mają jeszcze wpisów w event_days — bez tego
    # znikałyby z kalendarza, bo ten czyta wyłącznie z tej tabeli
    MAX_BACKFILL_DAYS = sales_rules.MAX_EVENT_DAYS  # dawniej 366 — zakres 0002…2026 rozpisywał się na setki dni
    for lead in Lead.query.filter(Lead.event_date.isnot(None)).filter(~Lead.event_days.any()).all():
        if not sane_event_date(lead.event_date) or (lead.event_end_date and not sane_event_date(lead.event_end_date)):
            continue  # literówka w roku — do ręcznej poprawy (raport „Porządki w danych”), nie rozpisujemy
        start = lead.event_date.date()
        end = lead.event_end_date.date() if lead.event_end_date else start
        if end < start:
            start, end = end, start
        span = min((end - start).days, MAX_BACKFILL_DAYS - 1)
        for i in range(span + 1):
            db.session.add(LeadEventDate(lead_id=lead.id, event_date=start + timedelta(days=i)))
    db.session.commit()
    # etapy Kontakt i Negocjacje usunięte z lejka — ich leady przechodzą na najbliższy etap (wpis w historii)
    for old, new in REMOVED_STAGES.items():
        for lead in Lead.query.filter(Lead.stage == old).all():
            db.session.add(StageHistory(lead_id=lead.id, from_stage=old, to_stage=new, changed_by=None))
            lead.stage = new
    db.session.commit()


def get_current_user(req):
    try:
        uid = int(req.cookies.get("crm_user", 1))
        return USERS_BY_ID.get(uid, USERS[0])
    except Exception:
        return USERS[0]


_include("rynki_filtr")


# ---------- JĘZYK INTERFEJSU (PL / LT) ----------
# Każdy wybiera język przyciskiem w menu (zapamiętany w ciasteczku na tym urządzeniu). Tłumaczenie robi
# i18n.py na gotowej stronie — szablony zostają takie same jak w polskim CRM.
import i18n  # noqa: E402

UI_LANG_COOKIE = "crm_lang"


def ui_lang():
    try:
        lang = request.cookies.get(UI_LANG_COOKIE, "")
    except RuntimeError:
        return i18n.DEFAULT_LANG
    return lang if lang in i18n.LANGS else (os.environ.get("CRM_DEFAULT_LANG", "").strip().lower()
                                           if os.environ.get("CRM_DEFAULT_LANG", "").strip().lower() in i18n.LANGS
                                           else i18n.DEFAULT_LANG)


@app.route("/crm/lang/<code>")
def set_ui_lang(code):
    nxt = request.args.get("next", "")
    nxt = nxt if nxt.startswith("/") and not nxt.startswith("//") else (request.referrer or "/crm/")
    resp = redirect(nxt)
    if code in i18n.LANGS:
        resp.set_cookie(UI_LANG_COOKIE, code, max_age=3600 * 24 * 365, samesite="Lax")
    return resp


_NO_TRANSLATE_ENDPOINTS = {"static", "lead_form", "mcp_endpoint"}


@app.after_request
def _translate_ui(resp):
    try:
        if (ui_lang() != "lt" or request.endpoint in _NO_TRANSLATE_ENDPOINTS or request.path.startswith("/f/")
                or resp.direct_passthrough or resp.is_streamed or resp.status_code in (204, 304)):
            return resp
        if resp.mimetype == "text/html":
            resp.set_data(i18n.translate_html(resp.get_data(as_text=True)))
        elif resp.mimetype == "application/json":
            new = i18n.translate_json_bytes(resp.get_data())
            if new is not None:
                resp.set_data(new)
    except Exception:  # tłumaczenie nigdy nie może zepsuć strony — wtedy zostaje po polsku
        app.logger.exception("i18n")
    return resp


@app.context_processor
def _inject_ui_lang():
    return {"ui_lang": ui_lang(), "ui_langs": i18n.LANGS}


# ---------- LOGOWANIE (włącza się, gdy w Railway jest zmienna CRM_PASSWORD) ----------

# strony dostępne bez logowania: konektor MCP (chroni go MCP_SECRET) i samo logowanie
PUBLIC_ENDPOINTS = {"static", "index", "crm_login", "crm_logout", "mcp_endpoint", "lead_form", "set_ui_lang", "whatsapp_webhook", "viber_phone"}
_include("logowanie")


_include("zadania_pomocnicze")


def lead_country(lead):
    """Kraj leada: wpisany ręcznie albo podpowiedź (faktura, język klienta, domena, waluta, miejsce)."""
    if lead.country:
        return lead.country
    if is_market_copy(lead):
        return "LT" if lead.source == "CRM LT" else "DE"
    if len(markets.MARKETS) > 1 and getattr(lead, "market", None) in markets.MARKETS:  # kilka rynków: kraj = rynek
        return lead.market.upper()
    places = " ".join([lead.location or ""] + [d.location or "" for d in lead.event_days])
    return sales_rules.suggest_country(places, lead.currency, lead.emails, lead.contact_phone, lead.billing_country,
                                       lead_language(lead))


_include("bramki_etapow")


_include("follow_upy")


DEFAULT_RATES = {"PLN": 1.0, "EUR": 4.25, "USD": 3.9, "GBP": 5.0}  # domyślne — kurs EUR bierzemy z NBP (P3)


def currency_rate(cur):
    """Ile zł za 1 jednostkę waluty: ustawienie fx_<WAL> (panel skuteczności / NBP), inaczej domyślny."""
    cur = (cur or "PLN").upper()
    if cur == "PLN":
        return 1.0
    try:
        return float(get_setting(f"fx_{cur}") or DEFAULT_RATES.get(cur, 1.0))
    except (TypeError, ValueError):
        return DEFAULT_RATES.get(cur, 1.0)


def to_pln(value, cur):
    return float(value or 0) * currency_rate(cur)


_include("automaty")

# ---------- STRAŻNIK CRM: czujniki pracowników w tle (ocena, alarmy i strona — niżej, „STRAŻNIK CRM”) ----------
_include("straznik_czujniki")


def _realize_scheduler():
    """W tle, poza startem aplikacji i poza żądaniami, co 10 minut: przenoszenie zrealizowanych
    i scalanie oczywistych duplikatów z importów."""
    time.sleep(15)
    while True:
        try:
            with app.app_context():
                # jednorazowo po wdrożeniu: telefon dla wszystkich aktywnych leadów bez następnego kroku
                if not get_setting(NEXT_STEPS_BACKFILL_KEY):
                    added = backfill_next_steps()
                    set_setting(NEXT_STEPS_BACKFILL_KEY, f"{added} @ {datetime.utcnow():%Y-%m-%d %H:%M}")
                    db.session.commit()
                db.session.remove()
        except Exception as e:
            health_fail("automaty", e)
        try:
            with app.app_context():
                auto_realize_leads()
                auto_merge_duplicates()
                if os.environ.get("SALES_AUTOMATION", "1") != "0":
                    sales_automation_pass()
                run_offer_audit_once()
                cleanup_offer_audit_steps()
                cleanup_won_tasks_once()  # raz: wygrane leady bez otwartych zadań (dalej Customer Journey)
                clear_form_day_event_dates()
                db.session.remove()
            health_beat("automaty")
        except Exception as e:
            app.logger.exception("realize_scheduler")
            health_fail("automaty", e)
        time.sleep(_AUTO_REALIZE_EVERY)


if os.environ.get("AUTO_REALIZE", "1") != "0":
    _start_worker("realize", _realize_scheduler)


@app.context_processor
def inject_globals():
    """Dane potrzebne w base.html (modal Nowy lead, sidebar) na każdej stronie."""
    return {
        "users": USERS,
        "USERS_BY_ID": USERS_BY_ID,
        "stages": STAGES,
        "stage_colors": STAGE_COLORS,
        "robot_models": ROBOT_MODELS,
        "currencies": CURRENCIES,
        "sum_by_currency": sum_by_currency,
        "quality_levels": QUALITY_LEVELS,
        "client_types": sales_rules.CLIENT_TYPES,
        "lost_reasons": sales_rules.LOST_REASONS,
        "win_confirmations": sales_rules.WIN_CONFIRMATIONS,
        "countries": sales_rules.COUNTRIES,
        "quality_colors": QUALITY_COLORS,
        "quality_icons": QUALITY_ICONS,
        "activity_kinds": ACTIVITY_KINDS,
        "kind_icons": ACTIVITY_KIND_ICONS,
        "kind_labels": ACTIVITY_KIND_LABELS,
        "journey_badge": journey_badge(),
        "to_local": to_local,
        "journey_doc_kinds": DOC_KINDS,
        "journey_columns": journey.COLUMN_LABELS,
        "journey_after": journey.AFTER_ITEMS,
        "mail_results": JOURNEY_MAIL_RESULTS,
        "followup_ready": lambda a: followup_lead(a) is not None,
        "event_date_min": f"{MIN_EVENT_YEAR}-01-01",
        "event_date_max": f"{date.today().year + 15}-12-31",
        "clickup_pending_count": Lead.query.filter_by(clickup_pending=True).count(),
        "offer_drafts_count": SentOffer.query.filter(SentOffer.sent_at.is_(None), _not_compose_offer()).count(),
        "ai_ready": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
        "campaigns_running": Campaign.query.filter_by(status="trwa").count(),
        "crm_locked": bool(crm_password()),
        "quick_task_leads": Lead.query.filter(Lead.clickup_pending.isnot(True))
            .order_by(Lead.company).with_entities(Lead.id, Lead.company).all(),
        "user": get_current_user(request),
    }


@app.template_filter("pln")
def fmt_pln(v):
    """Format kwoty z separatorem tysięcy: 15000 -> '15 000'."""
    try:
        return f"{int(round(float(v or 0))):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


def sum_by_currency(leads):
    """Suma wartości leadów rozbita per waluta: {'PLN': 12000, 'EUR': 3000}."""
    totals = {}
    for lead in leads:
        if lead.value_pln:
            cur = lead.currency or M(lead_market(lead))["currency"]
            totals[cur] = totals.get(cur, 0) + lead.value_pln
    return totals


@app.template_filter("money")
def fmt_money(totals):
    """{'PLN': 12000, 'EUR': 3000} -> '12 000 PLN + 3 000 EUR'."""
    if not totals:
        return f"0 {M()['currency']}"
    parts = [f"{fmt_pln(totals[c])} {c}" for c in CURRENCIES if c in totals]
    parts += [f"{fmt_pln(v)} {c}" for c, v in totals.items() if c not in CURRENCIES]
    return " + ".join(parts)


_include("daty_wydarzen")


# ---------- CODZIENNY RAPORT E-MAIL ----------

def build_daily_summary():
    """Treść raportu: przeterminowane, na dziś, leady bez kontaktu >= 7 dni."""
    pending = Activity.query.filter_by(done=False).filter(Activity.kind != "note").all()
    overdue = [a for a in pending if activity_urgency(a) == "overdue"]
    today_acts = [a for a in pending if activity_urgency(a) == "today"]
    silent = [
        (lead, lead_inactivity(lead))
        for lead in Lead.query.filter(Lead.stage.in_(ACTIVE_STAGES)).all()
        if lead_inactivity(lead) >= 7
    ]
    silent.sort(key=lambda x: x[1], reverse=True)

    def fmt_act(a):
        who = USERS_BY_ID.get(a.assigned_to, {}).get("name", "?")
        due = a.due_date.strftime("%d.%m") if a.due_date else "-"
        lead = a.lead.company if a.lead else "Zadanie własne"
        kind_label = ACTIVITY_KIND_LABELS.get(a.kind, a.kind).lower()
        return f"  - {lead}: {kind_label} ({due}, {who})"

    lines = [f"{M()['brand']} - raport {date.today().strftime('%d.%m.%Y')}", ""]
    lines.append(f"PRZETERMINOWANE ({len(overdue)}):")
    lines += [fmt_act(a) for a in overdue] or ["  brak"]
    lines.append("")
    lines.append(f"NA DZIS ({len(today_acts)}):")
    lines += [fmt_act(a) for a in today_acts] or ["  brak"]
    lines.append("")
    lines.append(f"LEADY BEZ KONTAKTU 7+ DNI ({len(silent)}):")
    lines += [f"  - {lead.company} ({lead.stage}, cisza {d}d)" for lead, d in silent] or ["  brak"]

    subject = (
        f"CRM {date.today().strftime('%d.%m')}: "
        f"{len(overdue)} przeterminowane, {len(today_acts)} na dzis, {len(silent)} bez kontaktu"
    )
    return subject, "\n".join(lines)


_include("raport_dzienny")


# ---------- SYNCHRONIZACJA LEADÓW Z CLICKUP ----------

_include("clickup_mapowanie")


def _clickup_get(path, params=None, token=None):
    token = (token or os.environ.get("CLICKUP_API_TOKEN", "")).strip()
    headers = {"Authorization": token, "User-Agent": "33bots-CRM/1.0"}
    last_err = None
    for attempt in range(3):  # chwilowe zerwanie połączenia nie powinno wywalać synchronizacji
        try:
            r = requests.get(f"{CLICKUP_API}{path}", headers=headers, params=params, timeout=20)
        except requests.exceptions.RequestException as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
            continue
        if not r.ok:
            # treść odpowiedzi ClickUp mówi dokładnie co jest nie tak (zły ID listy, zły token, zły parametr...)
            raise ClickUpAPIError(f"ClickUp {r.status_code}: {r.text[:500]}")
        return r.json()
    raise ClickUpAPIError(f"Brak połączenia z ClickUp po 3 próbach: {last_err}")


_include("clickup_import")


# ---- Niemcy: osobna lista ClickUp (formularze z reklam Meta w DE) ----
# CRM czyta listę wprost z ClickUp (bez żadnego innego CRM po drodze). Wszystkie leady z listy trafiają do rynku
# Niemcy od razu na tablicę i do okna alarmu „obsłuż dziś”; zaległe (wcześniejsze zgłoszenia) są w CRM od chwili
# importu, a data zgłoszenia z ClickUp jest w notatce. Do klienta nic nie wychodzi samo (Niemcy: auto_send=False).
CLICKUP_DE_SOURCE = "ClickUp DE"
CLICKUP_DE_LATE_MIN = 15  # zgłoszenie starsze niż tyle minut w chwili importu = zaległe
CLICKUP_AUTO_ACCEPT_DAYS = int(os.environ.get("CLICKUP_AUTO_ACCEPT_DAYS", "7") or 7)
CLICKUP_DE_ALIASES = {  # początek słowa w nazwie pola formularza (małe litery) → pole leada
    "company": ["firmenname", "firma", "unternehmen", "company", "nazwa firmy"],
    "contact_name": ["vor- und nachname", "ansprechpartner", "kontaktperson", "nachname", "vorname", "name",
                     "imię i nazwisko", "imie i nazwisko"],
    "contact_phone": ["telefon", "handy", "mobil", "phone", "tel"],
    "contact_email": ["e-mail", "email", "mail"],
    "location": ["veranstaltungsort", "ort", "stadt", "location", "lokalizacja", "miasto"],
    "event_date": ["veranstaltungsdatum", "eventdatum", "datum", "date", "data"],
}
_DE_PHONE_RE = re.compile(r"(?:\+|00)\d[\d\s/()-]{7,}\d|\b0\d[\d\s/-]{6,}\d\b")
_DE_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


CLICKUP_DE_LISTS_KEY = "clickup_de_lists"
CLICKUP_DE_REDISCOVER_H = 6
_DE_MARK_RE = re.compile(r"(?:\+|\b00)\s?(?:49|43|41)[\d\s/()-]{6,}|@[\w.-]+\.(?:de|at|ch)\b", re.I)


def clickup_de_configured():
    """Czy niemiecka lista ClickUp jest podłączona w tym CRM (bez łączenia z ClickUp)."""
    return bool(os.environ.get("CLICKUP_LIST_ID_DE", "").strip() or os.environ.get("CLICKUP_API_TOKEN_DE", "").strip())


def clickup_de_config():
    """([id list], token) niemieckiej listy ClickUp: CLICKUP_LIST_ID_DE (po przecinku) albo — gdy ustawiony jest
    tylko klucz CLICKUP_API_TOKEN_DE — lista znaleziona sama (jak w CRM PL). (None, None), gdy niepodłączona."""
    env_ids = [x.strip() for x in os.environ.get("CLICKUP_LIST_ID_DE", "").split(",") if x.strip()]
    de_token = os.environ.get("CLICKUP_API_TOKEN_DE", "").strip()
    token = de_token or os.environ.get("CLICKUP_API_TOKEN", "").strip()
    if env_ids and token:
        return env_ids, token
    if de_token:
        # w zapytaniu (przycisk) tylko zapamiętana lista — szukanie po wszystkich listach trwa dłużej niż 30 s
        return clickup_de_lists(discover=not has_request_context()), de_token
    return None, None


def clickup_all_lists(token=None):
    """Wszystkie listy dostępne tokenem: [{id, name}] (przestrzenie → foldery → listy i listy bez folderu)."""
    out = []
    for team in (_clickup_get("/team", token=token) or {}).get("teams") or []:
        for sp in (_clickup_get(f"/team/{team['id']}/space", {"archived": "false"}, token=token) or {}).get("spaces") or []:
            for f in (_clickup_get(f"/space/{sp['id']}/folder", {"archived": "false"}, token=token) or {}).get("folders") or []:
                for lst in f.get("lists") or []:
                    out.append({"id": str(lst["id"]), "name": f"{sp.get('name')} / {f.get('name')} / {lst.get('name')}"})
            for lst in (_clickup_get(f"/space/{sp['id']}/list", {"archived": "false"}, token=token) or {}).get("lists") or []:
                out.append({"id": str(lst["id"]), "name": f"{sp.get('name')} / {lst.get('name')}"})
    return out


def _clickup_task_is_german(task):
    blob = " ".join([task.get("name") or "", task.get("text_content") or ""]
                    + [str(_clickup_cf_value(cf) or "") for cf in task.get("custom_fields") or []])
    return bool(_DE_MARK_RE.search(blob))


def clickup_de_lists(discover=True, force=False):
    """Listy z niemieckimi zgłoszeniami widoczne kluczem CLICKUP_API_TOKEN_DE — wykryte same (zapamiętane na
    kilka godzin; nowy klucz = szukanie od razu). Ta sama zasada co w CRM PL."""
    try:
        cached = json.loads(get_setting(CLICKUP_DE_LISTS_KEY) or "{}")
    except ValueError:
        cached = {}
    token = os.environ.get("CLICKUP_API_TOKEN_DE", "").strip() or None
    tok_fp = hashlib.sha1((token or "").encode()).hexdigest()[:10]
    ttl = timedelta(hours=CLICKUP_DE_REDISCOVER_H) if cached.get("ids") else timedelta(minutes=30)
    fresh = (cached.get("at") and cached.get("tok") == tok_fp
             and datetime.fromisoformat(cached["at"]) > datetime.utcnow() - ttl)
    if (fresh and not force) or not discover:
        return (cached.get("ids") or []) if cached.get("tok") == tok_fp else []
    main = os.environ.get("CLICKUP_LIST_ID", "").strip()
    found, checked = [], []
    for lst in clickup_all_lists(token)[:120]:
        if lst["id"] == main:
            continue
        try:
            tasks = (_clickup_get(f"/list/{lst['id']}/task", {"archived": "false", "page": 0}, token=token) or {}).get("tasks") or []
        except ClickUpAPIError as e:  # brak dostępu do jednej listy / limit zapytań — sprawdzamy dalej
            checked.append({"id": lst["id"], "name": lst["name"], "error": str(e)[:120]})
            time.sleep(1)
            continue
        time.sleep(0.4)  # limit zapytań ClickUp (ok. 100 na minutę)
        sample = tasks[:40]
        de = sum(1 for t in sample if _clickup_task_is_german(t))
        checked.append({"id": lst["id"], "name": lst["name"], "tasks": len(sample), "german": de})
        if de >= 1 and (de >= 3 or re.search(r"niem|germ|deutsch|\bde\b|dach", lst["name"], re.I)) and de * 2 >= len(sample):
            found.append(lst["id"])
    set_setting(CLICKUP_DE_LISTS_KEY, json.dumps({"ids": found, "at": datetime.utcnow().isoformat(timespec="seconds"),
                                                  "tok": tok_fp, "checked": checked[:60]}, ensure_ascii=False))
    db.session.commit()
    return found


def _clickup_cf_value(cf):
    """Czytelna wartość pola formularza (lista rozwijana zwraca numer opcji, data — milisekundy)."""
    value = cf.get("value")
    if value in (None, "", []):
        return None
    if cf.get("type") == "drop_down":
        for opt in (cf.get("type_config") or {}).get("options") or []:
            if value in (opt.get("orderindex"), opt.get("id")):
                return opt.get("name")
    if cf.get("type") == "date":
        try:
            return datetime.utcfromtimestamp(int(value) / 1000)
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, dict)):
        return None
    return str(value).strip()


def _clickup_task_to_lead_de(task):
    """Task z niemieckiej listy (formularz Meta po niemiecku) → pola leada rynku Niemcy."""
    fields, extra = {"source": CLICKUP_DE_SOURCE, "currency": "EUR"}, []
    for cf in task.get("custom_fields") or []:
        name = (cf.get("name") or "").strip()
        value = _clickup_cf_value(cf)
        if value in (None, ""):
            continue
        lname = name.lower()
        target = next((f for f, aliases in CLICKUP_DE_ALIASES.items()
                       if any(re.search(r"(?<![a-zäöüß])" + re.escape(a), lname) for a in aliases)), None)
        if target == "event_date":
            when = value if isinstance(value, datetime) else None
            if when is None and isinstance(value, str):
                d = parse_date(value)
                when = datetime(d.year, d.month, d.day) if d else None
            if when:
                fields.setdefault("event_date", when)
                continue
        elif target == "contact_email" and "contact_email" in fields and "contact_email2" not in fields \
                and str(value).strip().lower() != fields["contact_email"].lower():
            fields["contact_email2"] = str(value)[:200]
            continue
        elif target and target not in fields:
            fields[target] = str(value)[:200]
            continue
        extra.append(f"{name}: {value}")
    title = (task.get("name") or "").strip()
    if "company" not in fields:  # bez pola firmy tytuł taska jest firmą (zwykle imię i nazwisko)
        fields["company"] = (title or "Lead z ClickUp (DE)")[:200]
    elif title and title != fields["company"] and "contact_name" not in fields:
        fields["contact_name"] = title[:200]
    if "contact_name" not in fields and _looks_like_person_name(fields["company"]):
        fields["contact_name"] = fields["company"]
    text = (task.get("text_content") or "").strip()
    fields["notes"] = "\n".join(([text] if text else []) + extra).strip()
    blob = f"{title}\n{text}"
    if "contact_phone" not in fields:
        m = _DE_PHONE_RE.search(blob)
        if m:
            fields["contact_phone"] = m.group(0).strip()
    if "contact_email" not in fields:
        m = _DE_EMAIL_RE.search(blob)
        if m:
            fields["contact_email"] = m.group(0)
    if task.get("date_created"):
        try:
            fields["created_at"] = datetime.utcfromtimestamp(int(task["date_created"]) / 1000)
        except (TypeError, ValueError):
            pass
    due = task.get("due_date")  # termin taska = dzień zgłoszenia to nie data wydarzenia
    if due and "event_date" not in fields:
        try:
            due_dt = datetime.utcfromtimestamp(int(due) / 1000)
            if not fields.get("created_at") or to_local(due_dt).date() != to_local(fields["created_at"]).date():
                fields["event_date"] = due_dt
        except (TypeError, ValueError):
            pass
    emails = {(a.get("email") or "").lower() for a in task.get("assignees") or []}
    fields["assigned_to"] = next((u["id"] for u in USERS if (u.get("email") or "").lower() in emails), USERS[0]["id"])
    return fields


CLICKUP_DE_SINCE_KEY = "clickup_de_direct_since"
CLICKUP_DE_SINCE_MARGIN_MIN = 60  # zgłoszenia z ostatniej godziny przed podłączeniem bierzemy (PL mógł ich jeszcze nie przekazać)


def clickup_de_since():
    """Od kiedy ten CRM czyta niemiecką listę ClickUp wprost (UTC; zapisane przy pierwszym udanym odczycie, z zapasem).
    Zgłoszenia utworzone wcześniej, których tu nie ma, NIE stają się leadami: do tej chwili niemieckie zgłoszenia
    przychodziły przez CRM PL, więc brak takiego leada znaczy, że był już obsłużony, scalony albo usunięty — bez tej
    granicy pierwszy odczyt zasypałby tablicę i okno „obsłuż dziś” starymi zgłoszeniami."""
    try:
        return datetime.fromisoformat(get_setting(CLICKUP_DE_SINCE_KEY))
    except (TypeError, ValueError):
        since = datetime.utcnow().replace(microsecond=0) - timedelta(minutes=CLICKUP_DE_SINCE_MARGIN_MIN)
        set_setting(CLICKUP_DE_SINCE_KEY, since.isoformat())
        db.session.commit()
        return since


def sync_clickup_de_leads():
    """Niemiecka lista ClickUp → leady rynku Niemcy, wszystkie od razu na tablicę (zaległe w CRM od chwili importu).
    Bez duplikatów (task, telefon, e-mail) i bez zgłoszeń sprzed podłączenia listy (clickup_de_since).
    Zwraca (ok, nowe, komunikat)."""
    list_ids, token = clickup_de_config()
    if list_ids is None:
        return False, 0, "Niemiecka lista ClickUp niepodłączona: ustaw CLICKUP_API_TOKEN_DE (i ewentualnie CLICKUP_LIST_ID_DE)"
    if not list_ids:
        return False, 0, "Niemcy: klucz CLICKUP_API_TOKEN_DE nie widzi listy z niemieckimi zgłoszeniami"
    imported = linked = moved = older = 0
    tasks = [t for lid in list_ids for t in _clickup_fetch_all_tasks(lid, token=token)]
    since = clickup_de_since()  # dopiero po udanym odczycie listy
    for task in tasks:
        task_id = task.get("id")
        if not task_id:
            continue
        fields = _clickup_task_to_lead_de(task)
        existing = all_markets(Lead.query.filter_by(clickup_task_id=task_id)).first()
        if existing:
            # ten sam task wpadł wcześniej z głównej listy do poczekalni — przenieś go do Niemiec
            if existing.clickup_pending and existing.stage == "Nowy" and existing.market != "de":
                existing.market, existing.source = "de", CLICKUP_DE_SOURCE
                moved += 1
            if existing.clickup_pending:  # z niemieckiej listy wszystko widoczne od razu
                existing.clickup_pending = False
                moved += 1
            continue
        if db.session.get(AppSetting, f"clickup_dup:{task_id}"[:64]):
            continue
        if fields.get("created_at") and fields["created_at"] < since:
            older += 1  # sprzed podłączenia listy do tego CRM — nie ruszamy (ani leada, ani notatki)
            continue
        same = find_recent_duplicate(fields.get("contact_email"), fields.get("contact_phone"))
        if same:
            if not same.clickup_task_id:
                same.clickup_task_id = task_id
            else:
                set_setting(f"clickup_dup:{task_id}"[:64], str(same.id))
            for f in ("contact_name", "contact_email", "contact_phone", "location"):
                if not getattr(same, f) and fields.get(f):
                    setattr(same, f, fields[f])
            db.session.add(Activity(
                lead_id=same.id, kind="note", done=True, done_at=datetime.utcnow(), assigned_to=same.assigned_to or 1,
                notes=f"🔗 Ten sam lead przyszedł też z ClickUp (lista DE) — nie dodano duplikatu.\n{fields.get('notes') or ''}".strip()))
            linked += 1
            continue
        created = fields.pop("created_at", None)
        lead = Lead(company=fields["company"], clickup_task_id=task_id, stage="Nowy", clickup_pending=False,
                    assigned_to=fields.get("assigned_to", 1), source=CLICKUP_DE_SOURCE, notes=fields.get("notes", ""),
                    currency="EUR", market="de")
        for f in _CLICKUP_REFRESH_FIELDS + ("contact_email2",):
            if f in fields:
                setattr(lead, f, fields[f])
        if created and created >= datetime.utcnow() - timedelta(minutes=CLICKUP_DE_LATE_MIN):
            lead.created_at = created
        elif created:  # zaległe zgłoszenie: w CRM od teraz (okno „obsłuż dziś”), data z ClickUp w notatce
            lead.notes = (f"📥 Zgłoszenie z ClickUp z {to_local(created):%d.%m.%Y %H:%M} — zaległe, w CRM od "
                          f"{to_local(datetime.utcnow()):%d.%m.%Y}.\n" + (lead.notes or "")).strip()
        db.session.add(lead)
        db.session.flush()
        db.session.add(StageHistory(lead_id=lead.id, from_stage=None, to_stage=lead.stage, changed_by=None))
        imported += 1
    db.session.commit()
    return True, imported, (f"Niemcy (ClickUp DE): {imported} nowych"
                            + (f", {linked} połączono z istniejącymi" if linked else "")
                            + (f", {moved} przeniesiono z poczekalni" if moved else "")
                            + f" · zgłoszeń na liście: {len(tasks)}"
                            + (f", w tym {older} sprzed podłączenia {to_local(since):%d.%m %H:%M} (bez zmian)" if older else ""))


def sync_clickup_main_leads():
    """Lista litewska (CLICKUP_LIST_ID) → leady. None, gdy niepodłączona. Zwraca (ok, nowe, komunikat)."""
    main_id = os.environ.get("CLICKUP_LIST_ID", "").strip()
    de_env = [x.strip() for x in os.environ.get("CLICKUP_LIST_ID_DE", "").split(",") if x.strip()]
    if not (main_id and os.environ.get("CLICKUP_API_TOKEN", "").strip()) or main_id in de_env:
        return None
    return _sync_clickup_main(main_id)


def sync_clickup_leads():
    """Lista litewska (CLICKUP_LIST_ID) i — gdy podłączona w tym CRM — niemiecka (CLICKUP_API_TOKEN_DE /
    CLICKUP_LIST_ID_DE). Błąd jednej listy nie zatrzymuje drugiej. Zwraca (ok, liczba_nowo_zaimportowanych, komunikat)."""
    results = []
    try:
        main = sync_clickup_main_leads()
        if main is not None:
            results.append(main)
    except ClickUpAPIError as e:
        db.session.rollback()
        results.append((False, 0, f"Lista główna: {e}"))
    if clickup_de_configured():
        try:
            results.append(sync_clickup_de_leads())
        except ClickUpAPIError as e:
            db.session.rollback()
            results.append((False, 0, f"Lista DE: {e}"))
    if not results:
        return False, 0, "Brak konfiguracji: ustaw CLICKUP_API_TOKEN i CLICKUP_LIST_ID (oraz CLICKUP_API_TOKEN_DE dla Niemiec)"
    if not clickup_de_configured():
        results.append((True, 0, "Niemcy: niemiecka lista ClickUp nie jest podłączona w tym CRM (CLICKUP_API_TOKEN_DE) — "
                                 "niemieckie zgłoszenia przychodzą przez CRM PL"))
    return all(r[0] for r in results), sum(r[1] for r in results), "; ".join(r[2] for r in results)


def _sync_clickup_main(list_id):
    """Pobiera taski z głównej listy ClickUp: nowe tworzy jako leady, a te które wciąż czekają na
    akceptację (jeszcze nie w CRM) odświeża najnowszymi danymi z ClickUp — dzięki temu
    poprawki w mapowaniu pól obejmują też już zaimportowane, ale nieprzejrzane leady."""

    tasks = _clickup_fetch_all_tasks(list_id)
    imported = 0
    refreshed = 0
    linked = 0
    for task in tasks:
        task_id = task.get("id")
        if not task_id:
            continue
        fields = _clickup_task_to_lead(task)
        existing = Lead.query.filter_by(clickup_task_id=task_id).first()
        if existing:
            if existing.clickup_pending:  # odśwież, dopóki lead nie został zaakceptowany
                for f in _CLICKUP_REFRESH_FIELDS:
                    if f in fields:
                        setattr(existing, f, fields[f])
                refreshed += 1
            continue
        if db.session.get(AppSetting, f"clickup_dup:{task_id}"[:64]):
            continue  # ten task był już scalony z innym leadem
        # ten sam klient już jest (np. z maila agencji do kontakt@) — nie dubluj, tylko połącz
        same = find_recent_duplicate(fields.get("contact_email"), fields.get("contact_phone"))
        if same:
            if not same.clickup_task_id:
                same.clickup_task_id = task_id
            else:
                set_setting(f"clickup_dup:{task_id}"[:64], str(same.id))
            for f in ("contact_name", "contact_email", "contact_phone", "location"):
                if not getattr(same, f) and fields.get(f):
                    setattr(same, f, fields[f])
            db.session.add(Activity(
                lead_id=same.id, kind="note", done=True, done_at=datetime.utcnow(), assigned_to=same.assigned_to or 1,
                notes=f"🔗 Ten sam lead przyszedł też z ClickUp — nie dodano duplikatu.\n{fields.get('notes') or ''}".strip(),
            ))
            linked += 1
            continue
        lead = Lead(
            company=fields["company"], clickup_task_id=task_id, stage="Nowy",
            clickup_pending=True,  # ląduje w zakładce "Z ClickUp", nie od razu w CRM
            assigned_to=fields.get("assigned_to", 1), source=fields.get("source", "ClickUp"),
            notes=fields.get("notes", ""), currency=fields.get("currency", "EUR"),
            market=markets.detect(fields.get("contact_phone"), [fields.get("contact_email")]) or DEFAULT_MARKET,
        )
        for f in _CLICKUP_REFRESH_FIELDS:
            if f in fields:
                setattr(lead, f, fields[f])
        db.session.add(lead)
        imported += 1
    db.session.commit()
    return True, imported, (f"Zaimportowano {imported} nowych, odświeżono {refreshed} oczekujących leadów z ClickUp"
                            + (f", {linked} połączono z istniejącymi (bez duplikatów)" if linked else ""))


def _clickup_scheduler():
    """Co CLICKUP_POLL_SECONDS (domyślnie 5 min) sprawdza ClickUp i importuje nowe leady."""
    interval = int(os.environ.get("CLICKUP_POLL_SECONDS", 300))
    while True:
        try:
            with app.app_context():
                res = sync_clickup_main_leads()
            if res is not None:
                health_beat("clickup", res[0], info=res[2], error=res[2])
        except Exception as e:
            app.logger.exception("clickup")
            health_fail("clickup", e)
        if clickup_de_configured():
            try:
                with app.app_context():
                    ok, _n, msg = sync_clickup_de_leads()
                health_beat("clickup_de", ok, info=msg, error=msg)
            except Exception as e:
                app.logger.exception("clickup_de")
                health_fail("clickup_de", e)
        time.sleep(interval)


if (os.environ.get("CLICKUP_API_TOKEN") and os.environ.get("CLICKUP_LIST_ID")) or clickup_de_configured():
    _start_worker("clickup", _clickup_scheduler)


@app.route("/crm/integrations/clickup/sync")
def crm_clickup_sync_test():
    try:
        ok, count, msg = sync_clickup_leads()
    except ClickUpAPIError as e:
        ok, count, msg = False, 0, str(e)
    except Exception as e:
        ok, count, msg = False, 0, f"Blad synchronizacji: {e}"
    return jsonify({"ok": ok, "imported": count, "info": msg})


@app.route("/crm/integrations/clickup/debug")
def crm_clickup_debug():
    """Podgląd surowych danych pierwszego taska z listy — do diagnozy mapowania pól."""
    list_id = os.environ.get("CLICKUP_LIST_ID", "").strip()
    if not os.environ.get("CLICKUP_API_TOKEN", "").strip() or not list_id:
        return jsonify({"ok": False, "info": "Brak konfiguracji: ustaw CLICKUP_API_TOKEN i CLICKUP_LIST_ID"})
    try:
        data = _clickup_get(f"/list/{list_id}/task", params={"archived": "false"})
    except ClickUpAPIError as e:
        return jsonify({"ok": False, "info": str(e)})
    tasks = data.get("tasks", [])
    if not tasks:
        return jsonify({"ok": True, "info": "Lista jest pusta — brak tasków do pokazania"})
    t = tasks[0]
    mapped = _clickup_task_to_lead(t)
    mapped_safe = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in mapped.items()}
    return jsonify({
        "ok": True,
        "task_name": t.get("name"),
        "date_created": t.get("date_created"),
        "assignees": t.get("assignees"),
        "custom_fields_raw": t.get("custom_fields"),
        "mapped_result": mapped_safe,
    })


_include("uzytkownicy")


@app.route("/crm/")
def crm_dashboard():
    user = get_current_user(request)
    today = date.today()

    # zadania: domyślnie tylko moje; ?all=1 pokazuje wszystkich
    show_all = request.args.get("all") == "1"
    pending_q = Activity.query.filter_by(done=False).filter(Activity.kind != "note")
    if not show_all:
        pending_q = pending_q.filter(Activity.assigned_to == user["id"])
    all_pending = pending_q.all()
    overdue, today_acts, soon_acts = [], [], []
    for a in all_pending:
        u = activity_urgency(a)
        if u == "overdue":
            overdue.append(a)
        elif u == "today":
            today_acts.append(a)
        elif u == "soon":
            soon_acts.append(a)

    overdue.sort(key=lambda a: a.due_date or date.min)
    today_acts.sort(key=lambda a: a.due_date or date.min)
    soon_acts.sort(key=lambda a: a.due_date or date.min)

    stage_counts = {s: 0 for s in STAGES}
    stage_values = {s: {} for s in STAGES}  # per waluta: {'PLN': 12000, 'EUR': 3000}
    for stage, cur, cnt, val in db.session.query(
        Lead.stage, Lead.currency, db.func.count(Lead.id), db.func.sum(Lead.value_pln)
    ).filter(Lead.clickup_pending.isnot(True)).group_by(Lead.stage, Lead.currency).all():
        if stage in stage_counts:
            stage_counts[stage] += cnt
            if val:
                c = cur or "EUR"
                stage_values[stage][c] = stage_values[stage].get(c, 0) + val
    total_leads = sum(stage_counts.values())
    # przychód: zrealizowany (po ostatnim dniu wynajmu) i wygrany, ale jeszcze do realizacji
    realized_value = stage_values["Zrealizowany"]
    to_realize_value = stage_values["Wygrany"]
    open_value = {}
    for s in ACTIVE_STAGES:
        for c, v in stage_values[s].items():
            open_value[c] = open_value.get(c, 0) + v

    won_count = sum(stage_counts[s] for s in WON_STAGES)
    closed = won_count + stage_counts["Stracony"]
    win_rate = round(100 * won_count / closed) if closed else None

    # aktywne leady bez zaplanowanego następnego zadania
    no_next_step = [
        lead for lead in Lead.query.filter(
            Lead.stage.in_(ACTIVE_STAGES), Lead.clickup_pending.isnot(True)
        ).all()
        if not any(not a.done for a in lead.activities)
    ]
    no_next_step.sort(key=lead_inactivity, reverse=True)

    # Customer Journey: klienci, przy których trzeba teraz coś zrobić (najpilniejsi na górze)
    rank = {"red": 0, "yellow": 1, "nodate": 2}
    journey_todo = sorted((v for v in journey_board_views() if v["attention"]),
                          key=lambda v: (rank.get(v["urgency"], 4), v["event"] or date.max))

    # zlecenia, na które zalogowany jedzie jako operator (panel znika, gdy nie jest operatorem)
    all_ops = Operator.query.order_by(Operator.name).all()
    my_ops = [o for o in all_ops if operator_user_id(o) == user["id"]]
    my_jobs = operator_jobs([o.id for o in my_ops]) if my_ops else None
    # operatorzy bez konta — do połączenia jednym kliknięciem, gdy zalogowany jeszcze nie jest operatorem
    link_candidates = [] if my_ops else [o for o in all_ops if o.active and operator_user_id(o) is None]

    return render_template(
        "crm/dashboard.html",
        user=user, users=USERS, USERS_BY_ID=USERS_BY_ID,
        overdue=overdue, today_acts=today_acts, soon_acts=soon_acts,
        stage_counts=stage_counts, stage_values=stage_values,
        stages=STAGES, stage_colors=STAGE_COLORS,
        total_leads=total_leads, realized_value=realized_value, to_realize_value=to_realize_value,
        open_value=open_value, win_rate=win_rate,
        no_next_step=no_next_step,
        journey_todo=journey_todo,
        my_ops=my_ops, my_jobs=my_jobs, link_candidates=link_candidates,
        show_all=show_all,
        inactivity_fn=lead_inactivity,
        inactivity_color_fn=lead_inactivity_color,
        today=today,
        urgency_fn=activity_urgency,
    )


CLOSED_STAGES = WON_STAGES + ("Stracony",)


def _history_order(h):
    return (h.changed_at or datetime.min, h.id or 0)


def _closing_entry(lead, history=None):
    """Wpis historii, od którego liczy się zamknięcie leada: dla wygranego — wejście w wygraną
    (Wygrany albo Zrealizowany) po ostatnim innym etapie, czyli przejście Wygrany → Zrealizowany
    nie zmienia daty wygranej; dla straconego — ostatnie wejście na Stracony."""
    history = sorted(lead.stage_history if history is None else history, key=_history_order)
    found, in_won = None, False
    for h in history:
        won = h.to_stage in WON_STAGES
        if lead.stage in WON_STAGES and won and not in_won:
            found = h
        elif lead.stage == "Stracony" and h.to_stage == "Stracony":
            found = h
        in_won = won
    return found


def own_leads():
    """Filtr: leady polskiego CRM, bez kopii wygranych z CRM LT/DE (do automatyzacji i wszystkiego poza wygranymi)."""
    return db.or_(Lead.source.is_(None), Lead.source.notin_(MARKET_COPY_SOURCES))


def is_market_copy(lead):
    return (lead.source or "") in MARKET_COPY_SOURCES


def foreign_market(lead):
    return None


def closed_lead_events(include_copies=False):
    """Leady zamknięte wg ich OBECNEGO etapu: [(lead, "Wygrany"|"Stracony", kiedy)] (czas
    warszawski). Zrealizowany liczy się jako wygrany, z datą wygranej. Lead wygrany, a potem
    przeniesiony na Stracony, liczy się tylko jako stracony (w miesiącu straty); lead cofnięty
    z Wygranego np. do Oferty nie liczy się wcale. Każdy lead najwyżej raz."""
    by_lead = {}
    for h in StageHistory.query.filter(StageHistory.changed_at.isnot(None)).all():
        by_lead.setdefault(h.lead_id, []).append(h)
    out = []
    for lead in Lead.query.filter(Lead.stage.in_(CLOSED_STAGES), Lead.clickup_pending.isnot(True)).all():
        entry = _closing_entry(lead, by_lead.get(lead.id, []))
        if entry:
            out.append((lead, "Wygrany" if lead.stage in WON_STAGES else lead.stage, to_local(entry.changed_at)))
    return out


# ---------- PANEL SKUTECZNOŚCI (P3) ----------
_include("statystyki_sprzedazy")


@app.route("/crm/stats")
def crm_stats():
    today = date.today()
    # ostatnie 12 miesięcy, od najstarszego
    months = []
    y, m = today.year, today.month
    for i in range(11, -1, -1):
        mm, yy = m - i, y
        while mm <= 0:
            mm += 12
            yy -= 1
        months.append((yy, mm))

    stats = {k: {"new": 0, "won": 0, "lost": 0, "won_value": {}, "won_done": {}, "won_todo": {},
                 "new_leads": [], "won_leads": [], "lost_leads": []} for k in months}
    for lead in Lead.query.filter(Lead.clickup_pending.isnot(True)).all():
        created = to_local(lead.created_at)
        if created:
            k = (created.year, created.month)
            if k in stats:
                stats[k]["new"] += 1
                stats[k]["new_leads"].append(lead)
    # wygrane/stracone wg obecnego etapu leada — skasowany jako Stracony znika z wygranych
    for lead, stage, when in sorted(closed_lead_events(), key=lambda x: x[2]):
        k = (when.year, when.month)
        if k not in stats:
            continue
        if stage == "Wygrany":
            stats[k]["won"] += 1
            stats[k]["won_leads"].append(lead)
            if lead.value_pln:
                cur = lead.currency or "EUR"
                stats[k]["won_value"][cur] = stats[k]["won_value"].get(cur, 0) + lead.value_pln
                part = stats[k]["won_done" if lead.stage == "Zrealizowany" else "won_todo"]
                part[cur] = part.get(cur, 0) + lead.value_pln
        else:
            stats[k]["lost"] += 1
            stats[k]["lost_leads"].append(lead)

    # źródła leadów
    sources = {}
    for lead in Lead.query.filter(Lead.clickup_pending.isnot(True)).all():
        src = (lead.source or "").strip() or "(brak źródła)"
        s = sources.setdefault(src, {"total": 0, "won": 0, "won_value": {}})
        s["total"] += 1
        if lead.stage in WON_STAGES:
            s["won"] += 1
            if lead.value_pln:
                cur = lead.currency or "EUR"
                s["won_value"][cur] = s["won_value"].get(cur, 0) + lead.value_pln
    sources_sorted = sorted(sources.items(), key=lambda kv: kv[1]["total"], reverse=True)

    month_names = ["Sty", "Lut", "Mar", "Kwi", "Maj", "Cze", "Lip", "Sie", "Wrz", "Paź", "Lis", "Gru"]
    rows = [{"label": f"{month_names[mm-1]} '{str(yy)[2:]}", **stats[(yy, mm)]} for yy, mm in months]
    max_bar = max([r["new"] for r in rows] + [r["won"] for r in rows] + [1])
    flow = build_stage_flow()

    # przychód wg miesiąca, w którym FAKTYCZNIE odbywa się wynajem (pierwszy dzień) — łącznie
    # z przyszłymi, już zabukowanymi; osobno od tabeli wyżej, która liczy wg daty zamknięcia.
    # Zrealizowany = po ostatnim dniu wynajmu, Wygrany = jeszcze do realizacji.
    realization = {}
    totals = {"done": {}, "todo": {}, "done_count": 0, "todo_count": 0}
    undated = []  # wygrane bez (sensownej) daty — CRM nie wie, kiedy przenieść je do Zrealizowanych
    for lead in Lead.query.filter(Lead.stage.in_(WON_STAGES), Lead.clickup_pending.isnot(True)).all():
        part = "done" if lead.stage == "Zrealizowany" else "todo"
        totals[part + "_count"] += 1
        cur = lead.currency or "EUR"
        if lead.value_pln:
            totals[part][cur] = totals[part].get(cur, 0) + lead.value_pln
        if not sane_event_date(lead.event_date):
            undated.append(lead)
            continue
        k = (lead.event_date.year, lead.event_date.month)
        r = realization.setdefault(k, {"count": 0, "value": {}, "done": {}, "todo": {}, "leads": []})
        r["count"] += 1
        r["leads"].append(lead)
        if lead.value_pln:
            r["value"][cur] = r["value"].get(cur, 0) + lead.value_pln
            r[part][cur] = r[part].get(cur, 0) + lead.value_pln
    real_rows = []
    for (yy, mm) in sorted(realization.keys()):
        r = realization[(yy, mm)]
        real_rows.append({
            "label": f"{month_names[mm-1]} '{str(yy)[2:]}",
            "count": r["count"], "value": r["value"], "done": r["done"], "todo": r["todo"],
            "leads": sorted(r["leads"], key=lambda l: l.event_date),
            "pln": r["value"].get("EUR", 0),
            "done_pln": r["done"].get("EUR", 0), "todo_pln": r["todo"].get("EUR", 0),
            "current": (yy, mm) == (today.year, today.month),
            "future": (yy, mm) > (today.year, today.month),
        })
    real_max = max([row["pln"] for row in real_rows] + [1])
    undated.sort(key=lambda l: (l.stage != "Wygrany", -(l.value_pln or 0)))

    return render_template(
        "crm/stats.html", rows=rows, sources=sources_sorted, max_bar=max_bar, flow=flow,
        real_rows=real_rows, real_max=real_max, rev_totals=totals, undated=undated,
    )


@app.route("/crm/calendar")
def crm_calendar():
    user = get_current_user(request)
    return render_template("crm/calendar.html", user=user, users=USERS)


@app.route("/crm/api/activities")
def api_activities():
    start = request.args.get("start")
    end = request.args.get("end")

    events = []

    query = Activity.query
    for a in query.all():
        s = serialize_activity(a)
        if not s["due_date"]:
            continue
        if start and s["due_date"] < start[:10]:
            continue
        if end and s["due_date"] > end[:10]:
            continue
        events.append({
            "id": f"act-{a.id}",
            "title": f"{ACTIVITY_KIND_ICONS.get(a.kind,'•')} {s['lead_company'] or 'Zadanie własne'}",
            "start": s["due_date"],
            "allDay": True,
            "backgroundColor": s["color"],
            "borderColor": s["color"],
            "textColor": "#fff",
            "extendedProps": {**s, "event_type": "activity"},
            "classNames": ["done-event"] if a.done else [],
        })

    lead_query = Lead.query.filter(
        Lead.event_date.isnot(None), Lead.stage != "Stracony", Lead.clickup_pending.isnot(True)
    )
    for lead in lead_query.all():
        ordered = lead.event_days  # już posortowane po dacie (relationship order_by)
        if not ordered:
            continue

        def eff_loc(ed):
            return ed.location or lead.location or ""

        # grupuj wybrane dni w ciągłe odcinki tej samej lokalizacji — ciągły wynajem
        # w jednym miejscu dalej jest jednym paskiem, a osobne dni (np. tylko 1 i 10
        # września) albo zmiana miasta w środku ciągu są osobnymi wydarzeniami
        runs = [[ordered[0]]]
        for ed in ordered[1:]:
            prev = runs[-1][-1]
            if (ed.event_date - prev.event_date).days == 1 and eff_loc(ed) == eff_loc(prev):
                runs[-1].append(ed)
            else:
                runs.append([ed])

        # kolor wydarzenia wg etapu — fiolet/róż, celowo inne niż kolory pilności zadań (czerwony/żółty/indygo/zielony)
        evt_color = "#db2777" if lead.stage in WON_STAGES else "#7c3aed"
        for run in runs:
            run_start, run_end = run[0].event_date, run[-1].event_date
            if start and run_end.isoformat() < start[:10]:
                continue
            if end and run_start.isoformat() > end[:10]:
                continue
            days = len(run)
            run_location = eff_loc(run[0])
            title = f"🗓️ {lead.company}" + (f" ({days} dni)" if days > 1 else "")
            event = {
                "id": f"evt-{lead.id}-{run_start.isoformat()}",
                "title": title,
                "start": run_start.isoformat(),
                "allDay": True,
                "backgroundColor": evt_color,
                "borderColor": evt_color,
                "textColor": "#fff",
                "extendedProps": {
                    "event_type": "lead_event",
                    "lead_id": lead.id,
                    "lead_company": lead.company,
                    "lead_contact": lead.contact_name or "",
                    "robot_model": lead.robot_model or "",
                    "value_pln": lead.value_pln,
                    "currency": lead.currency or M(lead_market(lead))["currency"],
                    "location": run_location,
                    "rental_days": days,
                    "stage": lead.stage,
                },
            }
            if days > 1:
                # FullCalendar: end jest wyłączny, więc start + N dni = pasek przez N dni
                event["end"] = (run_end + timedelta(days=1)).isoformat()
            events.append(event)

    return jsonify(events)


_include("proby_kontaktu")


def private_price_text(lead):
    """Cena pakietu z kalkulatora rynku (EUR, z ceną minimalną) dla leada; bez danych — od ceny minimalnej rynku."""
    try:
        q = lt_quote(lead, remote_budget=1)
        if q.get("price"):
            return f"robot z operatorem za {q['price']:,} € netto (z dojazdem)".replace(",", " ")
    except Exception:
        pass
    return f"robot z operatorem od {pricing.rates(lead_market(lead)).get('min', 1900):,.0f} € netto + dojazd".replace(",", " ")


def sales_automation_pass():
    """Automaty procesu sprzedaży (co 10 min, w tle). Tylko wewnętrzne zadania i szkice — NIC nie wysyłają:
    1) szkic pierwszej odpowiedzi dla nowych osób prywatnych (do sprawdzenia i wysłania przez człowieka),
    2) telefon, gdy prywatny klient odpisał na pierwszą odpowiedź,
    3) zadanie „Zdecyduj” po limicie nieudanych prób kontaktu,
    4) zapis przekroczenia czasu reakcji (alert w oknie nowych leadów)."""
    now = datetime.utcnow()
    today0 = datetime.combine(to_local(now).date(), datetime.min.time())
    made = {"drafts": 0, "reply_calls": 0, "decide": 0, "late": 0}
    auto = auto_tasks_on()  # 1)–3) to zadania — domyślnie wyłączone, następny krok ustawia człowiek
    # 1) osoby prywatne — szkic pierwszej odpowiedzi
    for lead in (Lead.query.filter(Lead.stage == "Nowy", Lead.created_at >= now - timedelta(days=3),
                                   Lead.handled_at.is_(None), Lead.clickup_pending.isnot(True), own_leads()).all()
                 if auto else []):
        if not lead.emails or foreign_market(lead) or (lead.client_type or client_type_hint(lead)[0]) != "prywatna":
            continue
        if Activity.query.filter(Activity.lead_id == lead.id,
                                 Activity.notes.like(sales_rules.PRIVATE_DRAFT_PREFIX + "%")).first():
            continue
        owner = USERS_BY_ID.get(lead.assigned_to) or USERS[0]
        subject, body = sales_rules.private_draft(lead.company, lead.contact_name, private_price_text(lead), lead.location,
                                                  [d.event_date for d in lead.event_days], owner.get("full_name", ""),
                                                  mail_phone(owner.get("phone", ""), lead_market(lead)))
        db.session.add(Activity(lead_id=lead.id, kind="email", due_date=today0, assigned_to=owner["id"],
                                notes=f"{sales_rules.PRIVATE_DRAFT_PREFIX} — szkic gotowy, NIC nie poszło do klienta. "
                                      f"Sprawdź i wyślij: Poczta → „Otwórz szkic”.\nTemat: {subject}\n---\n{body}"))
        if lead.contact_phone and not Activity.query.filter(Activity.lead_id == lead.id, Activity.kind == "call",
                                                            Activity.done.isnot(True)).first():
            db.session.add(Activity(lead_id=lead.id, kind="call", due_date=today0, assigned_to=owner["id"],
                                    notes=f"📞 Zadzwoń do osoby prywatnej (najwyżej {sales_rules.PRIVATE_ATTEMPT_LIMIT} próby) "
                                          "— zapytaj o termin, godziny i budżet; szkic maila czeka w Poczcie."))
        made["drafts"] += 1
    db.session.commit()
    # 2) prywatny klient odpisał po naszej pierwszej odpowiedzi → telefon (dopiero teraz)
    for d in (Activity.query.filter(Activity.notes.like(sales_rules.PRIVATE_DRAFT_PREFIX + "%"), Activity.done.is_(True),
                                    Activity.done_at >= now - timedelta(days=30)).all() if auto else []):
        lead = d.lead
        if not lead or lead.stage not in ACTIVE_STAGES:
            continue
        q = mail_query_for_addrs(lead.emails)
        reply = q.filter(MailMessage.folder == "in", MailMessage.date > d.done_at).first() if q is not None else None
        if reply is None or Activity.query.filter(Activity.lead_id == lead.id, Activity.notes.like(sales_rules.REPLY_CALL_PREFIX + "%")).first():
            continue
        db.session.add(Activity(lead_id=lead.id, kind="call", assigned_to=lead.assigned_to or d.assigned_to, due_date=today0,
                                notes=f"{sales_rules.REPLY_CALL_PREFIX} na pierwszą odpowiedź ({to_local(reply.date):%d.%m %H:%M}) — "
                                      "zadzwoń i domknij termin, godziny i budżet."))
        made["reply_calls"] += 1
    db.session.commit()
    # 3) limit prób kontaktu
    for lead in Lead.query.filter(Lead.stage.in_(ACTIVE_STAGES), Lead.clickup_pending.isnot(True), own_leads(),
                                  Lead.id.in_(db.session.query(Activity.lead_id).filter(
                                      Activity.done.is_(True), Activity.done_at >= now - timedelta(days=60),
                                      db.or_(Activity.outcome.in_(sales_rules.FAILED_OUTCOMES), Activity.notes.like("📵%"))))).all():
        if check_contact_limit(lead):
            made["decide"] += 1
    db.session.commit()
    # 4) przekroczony czas reakcji — zapis (alert pokazuje okno nowych leadów)
    for lead in [l for code in markets.MARKETS for l in unhandled_leads(code)]:
        if lead.response_alert_at is None and (lead_response_minutes(lead) or 0) > sales_rules.RESPONSE_TARGET_MIN:
            lead.response_alert_at = now
            made["late"] += 1
    db.session.commit()
    return made


def lead_response_minutes(lead):
    """Czas reakcji w minutach pracy (pn–pt 9–18): od wpłynięcia zapytania do pierwszej odpowiedzi (lead obsłużony);
    nieobsłużony — ile już czeka. Licznik staje na sales_rules.RESPONSE_CAP_MIN („ponad 2 h”)."""
    if not lead or not lead.created_at:
        return None
    end = lead.handled_at or datetime.utcnow()
    return sales_rules.response_minutes(to_local(lead.created_at).replace(tzinfo=None), to_local(end).replace(tzinfo=None))


_include("zadania_api")


FOLLOWUP_LANGS = {"lt": "litewski", "de": "niemiecki"}  # tylko języki rynków — nigdy po angielsku ani po polsku


def lead_language(lead):
    """Język klienta = język rynku leada (Litwa → litewski, Niemcy → niemiecki). Wszystko, co idzie do klienta
    (maile, oferta, umowa, formularz), jest w tym języku — nigdy po angielsku ani po polsku."""
    return markets.get(lead_market(lead))["lang"]


_FU_TEXT = {
    "pl": {"subject": "Wynajem robota Unitree G1 — {company}", "offer_subject": "Oferta wynajmu robota — {company}",
           "when": " na wydarzenie {date}",
           "first_offer": "uprzejmie przypominam się w sprawie oferty na wynajem robota humanoidalnego Unitree G1{when}, "
                          "którą wysłaliśmy {sent}. Czy udało się z nią zapoznać?",
           "first": "uprzejmie przypominam się w sprawie wynajmu robota humanoidalnego Unitree G1{when}. Czy mogę w czymś pomóc?",
           "hello": "Dzień dobry,",
           "help": "Chętnie odpowiem na pytania, dopasuję szczegóły albo zarezerwuję termin — wystarczy odpisać "
                   "na tę wiadomość lub zadzwonić.",
           "bye": "Z pozdrowieniami,"},
    "en": {"subject": "Unitree G1 robot rental — {company}", "offer_subject": "Robot rental offer — {company}",
           "when": " for your event on {date}",
           "first_offer": "I'm just following up on the offer for renting the Unitree G1 humanoid robot{when}, "
                          "which we sent on {sent}. Have you had a chance to look at it?",
           "first": "I'm just following up regarding the Unitree G1 humanoid robot rental{when}. Is there anything I can help with?",
           "hello": "Hello,",
           "help": "I'm happy to answer any questions, adjust the details or reserve the date for you — simply reply "
                   "to this email or give me a call.",
           "bye": "Best regards,"},
    "de": {"subject": "Vermietung des Roboters Unitree G1 — {company}", "offer_subject": "Angebot Robotervermietung — {company}",
           "when": " für Ihre Veranstaltung am {date}",
           "first_offer": "ich wollte kurz an unser Angebot zur Vermietung des humanoiden Roboters Unitree G1{when} "
                          "erinnern, das wir Ihnen am {sent} geschickt haben. Konnten Sie es sich schon ansehen?",
           "first": "ich wollte mich kurz zur Vermietung des humanoiden Roboters Unitree G1{when} melden. "
                    "Kann ich Ihnen irgendwie weiterhelfen?",
           "hello": "Guten Tag,",
           "help": "Gerne beantworte ich Ihre Fragen, passe die Details an oder reserviere den Termin für Sie — "
                   "antworten Sie einfach auf diese E-Mail oder rufen Sie mich an.",
           "bye": "Mit freundlichen Grüßen"},
    "lt": {"subject": "Roboto Unitree G1 nuoma — {company}", "offer_subject": "Roboto nuomos pasiūlymas — {company}",
           "when": " jūsų renginiui {date}",
           "first_offer": "norėčiau priminti apie humanoidinio roboto Unitree G1 nuomos pasiūlymą{when}, "
                          "kurį išsiuntėme {sent}. Ar turėjote galimybę su juo susipažinti?",
           "first": "norėčiau priminti dėl humanoidinio roboto Unitree G1 nuomos{when}. Ar galiu kuo nors padėti?",
           "hello": "Laba diena,",
           "help": "Mielai atsakysiu į klausimus, pritaikysiu detales arba rezervuosiu datą — tiesiog atsakykite "
                   "į šį laišką arba paskambinkite.",
           "bye": "Pagarbiai,"},
}


def followup_draft(lead, sender, lang=None):
    """Gotowy follow-up w języku klienta (lead_language): nawiązuje do ostatniej oferty wysłanej z CRM
    (temat „Re: …”, data), do dnia wynajmu, jeśli jeszcze przed nami, i kończy się podpisem nadawcy.
    Powitanie zawsze bez imienia."""
    lang = lang if lang in _FU_TEXT else lead_language(lead)
    t = _FU_TEXT[lang]
    offer = (SentOffer.query.filter(SentOffer.lead_id == lead.id, SentOffer.sent_at.isnot(None))
             .order_by(SentOffer.sent_at.desc()).first())
    today = to_local(datetime.utcnow()).date()
    event = sane_event_date(lead.event_date)
    dfmt = "%Y-%m-%d" if lang == "lt" else "%d.%m.%Y"  # Litwa: 2026-12-12, Niemcy: 12.12.2026
    when = t["when"].format(date=f"{event:{dfmt}}") if event and event.date() >= today else ""
    if offer:
        subject = offer.subject or t["offer_subject"].format(company=lead.company)
        if not subject.lower().startswith("re:"):
            subject = "Re: " + subject
        first = t["first_offer"].format(when=when, sent=f"{to_local(offer.sent_at):{dfmt}}")
    else:
        subject = t["subject"].format(company=lead.company)
        first = t["first"].format(when=when)
    signature = "\n".join(x for x in (sender["full_name"], "33Bots", sender["email"],
                                       mail_phone(sender["phone"], lead_market(lead))) if x)
    body = f"{t['hello']}\n\n{first}\n\n{t['help']}\n\n{t['bye']}\n{signature}"
    return human_text(subject), human_text(body)


@app.route("/crm/api/activity/<int:aid>/followup", methods=["GET", "POST"])
def api_activity_followup(aid):
    """GET: szkic follow-upu do okienka; POST: wysyła go ze skrzynki nadawcy, zamyka zadanie
    i zapisuje treść w komentarzach leada."""
    act = db.session.get(Activity, aid)
    lead = followup_lead(act)
    if not lead:
        return jsonify({"error": "To zadanie nie jest otwartym mailem do leada z adresem e-mail."}), 400
    user = get_current_user(request)
    senders = [market_sender(u, lead_market(lead)) for u in USERS if user_can_send_offers(u)]
    if not senders:
        return jsonify({"error": "Nikt nie ma e-maila i telefonu w Profilu — nie ma z czego wysłać."}), 400
    args = request.form if request.method == "POST" else request.args
    lang = args.get("lang") if args.get("lang") in FOLLOWUP_LANGS else lead_language(lead)
    wanted = args.get("sender", type=int)
    sender = (next((u for u in senders if u["id"] == wanted), None)
              or next((u for u in senders if u["id"] == act.assigned_to), None)
              or next((u for u in senders if u["id"] == user["id"]), None) or senders[0])
    to_addr = ", ".join(lead.emails)
    if request.method == "GET":
        subject, body = followup_draft(lead, sender, lang)
        return jsonify({"to": to_addr, "recipients": lead.emails, "company": lead.company, "subject": subject, "body": body,
                        "lang": lang, "langs": FOLLOWUP_LANGS, "sender_id": sender["id"],
                        "senders": [{"id": u["id"], "name": f"{u['full_name']} ({u['email']})"} for u in senders]})
    if request.form.get("to") is not None:  # adresaci wybrani w okienku (domyślnie wszystkie adresy leada)
        recipients, bad = check_recipients(request.form.get("to"))
        if bad:
            return jsonify({"error": bad}), 400
        to_addr = ", ".join(recipients)
    subject = request.form.get("subject", "").strip()[:300]
    body = request.form.get("body", "").replace("\r\n", "\n").strip()[:10000]
    if not subject or not body:
        return jsonify({"error": "Uzupełnij temat i treść."}), 400
    err = _smtp_send(to_addr, subject, body, sender["name"], sender=sender)
    if err:
        return jsonify({"error": f"Mail nie wyszedł: {err}"}), 502
    now = datetime.utcnow()
    act.done, act.done_at = True, now
    act.outcome = act.outcome or "bez_odpowiedzi"  # follow-up mailem wysłany — czekamy na odpowiedź
    db.session.add(Activity(
        lead_id=lead.id, kind="note", done=True, done_at=now, assigned_to=user["id"],
        notes=f"📨 Follow-up ({FOLLOWUP_LANGS[lang]}) wysłany do {to_addr} (od: {sender['full_name']}, {sender['email']})\nTemat: {subject}\n---\n{body}",
    ))
    # następny krok: telefon za 5 dni (automatyczny telefon po ofercie przesuwa się na ten termin)
    call = ensure_followup_call(lead, days=FOLLOWUP_AFTER_EMAIL_DAYS, after="follow-upie wysłanym")
    if call is None:  # ktoś już ręcznie zaplanował telefon — to on jest następnym krokiem
        call = Activity.query.filter(Activity.lead_id == lead.id, Activity.kind == "call",
                                     Activity.done.isnot(True)).order_by(Activity.due_date).first()
    db.session.commit()
    who = USERS_BY_ID.get(call.assigned_to, {}).get("name", "?") if call else ""
    nxt = f"📞 telefon {call.due_date:%d.%m} ({who})" if call and call.due_date else None
    return jsonify({"ok": True, "warning": _smtp_send.last_warning, "next": nxt})


def _filtered_leads(search, assigned):
    query = Lead.query.filter(Lead.clickup_pending.isnot(True))
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(
            Lead.company.ilike(like),
            Lead.contact_name.ilike(like),
            Lead.contact_email.ilike(like),
            Lead.contact_email2.ilike(like),
            Lead.contact_phone.ilike(like),
            Lead.notes.ilike(like),
        ))
    if assigned:
        try:
            query = query.filter_by(assigned_to=int(assigned))
        except ValueError:
            pass
    return query.order_by(Lead.created_at.desc()).all()


@app.route("/crm/leads")
def crm_leads():
    user = get_current_user(request)
    view = request.args.get("view", "kanban")
    search = request.args.get("q", "").strip()
    assigned = request.args.get("assigned", "")

    leads = _filtered_leads(search, assigned)
    by_stage = {s: [] for s in STAGES}
    for lead in leads:
        by_stage.get(lead.stage, by_stage["Nowy"]).append(lead)

    # lista domyślnie bez straconych
    show_lost = request.args.get("show_lost") == "1"
    list_leads = leads if show_lost else [l for l in leads if l.stage != "Stracony"]

    return render_template(
        "crm/leads.html",
        user=user, users=USERS, USERS_BY_ID=USERS_BY_ID,
        leads=leads, by_stage=by_stage,
        list_leads=list_leads, show_lost=show_lost,
        stages=STAGES, stage_colors=STAGE_COLORS,
        view=view, search=search, assigned=assigned,
        robot_models=ROBOT_MODELS,
        inactivity_fn=lead_inactivity,
        inactivity_color_fn=lead_inactivity_color,
        pending_fn=pending_activities,
        urgency_fn=activity_urgency,
        today=date.today(),
    )


@app.route("/crm/clickup-inbox")
def crm_clickup_inbox():
    user = get_current_user(request)
    search = request.args.get("q", "").strip()
    # najnowsze na górze
    query = Lead.query.filter_by(clickup_pending=True)
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(
            Lead.company.ilike(like),
            Lead.contact_name.ilike(like),
            Lead.contact_email.ilike(like),
            Lead.contact_email2.ilike(like),
            Lead.contact_phone.ilike(like),
            Lead.notes.ilike(like),
        ))
    pending_leads = query.order_by(Lead.created_at.desc()).all()
    total_pending = Lead.query.filter_by(clickup_pending=True).count()
    return render_template(
        "crm/clickup_inbox.html",
        user=user, users=USERS, USERS_BY_ID=USERS_BY_ID,
        pending_leads=pending_leads, search=search, total_pending=total_pending,
    )


def _apply_clickup_review_fields(lead, form):
    """Bezpiecznie nadpisuje pola leada z okna akceptacji — brakujący klucz w formularzu
    zostawia dotychczasową wartość zamiast ją czyścić."""
    lead.company = (form.get("company", lead.company) or lead.company or "").strip() or lead.company
    lead.contact_name = form.get("contact_name", lead.contact_name or "").strip()
    lead.contact_phone = form.get("contact_phone", lead.contact_phone or "").strip()
    lead.contact_email = form.get("contact_email", lead.contact_email or "").strip()
    lead.contact_email2 = form.get("contact_email2", lead.contact_email2 or "").strip() or None
    robot = form.get("robot_model", lead.robot_model or "").strip()
    lead.robot_model = ROBOT_MODEL_ALIASES.get(robot, robot)
    val = form.get("value_pln", type=float)
    if val is not None:
        lead.value_pln = val
    cur = form.get("currency", lead.currency or "EUR").strip().upper()
    lead.currency = cur if cur in CURRENCIES else (lead.currency or "EUR")
    lead.location = form.get("location", lead.location or "").strip()
    try:
        assigned = int(form.get("assigned_to", lead.assigned_to or 1))
    except (TypeError, ValueError):
        assigned = lead.assigned_to or 1
    lead.assigned_to = assigned if assigned in USERS_BY_ID else (lead.assigned_to or 1)
    if "event_dates" in form:
        set_lead_event_days(
            lead,
            parse_event_dates(form.get("event_dates", "")),
            parse_event_locations(form.get("event_locations", "")),
        )


@app.route("/crm/leads/<int:lead_id>/clickup/accept", methods=["POST"])
def crm_clickup_accept(lead_id):
    lead = db.session.get(Lead, lead_id)
    if not lead or not lead.clickup_pending:
        abort(404)
    _apply_clickup_review_fields(lead, request.form)
    lead.clickup_pending = False
    db.session.add(StageHistory(lead_id=lead.id, from_stage=None, to_stage=lead.stage, changed_by=None))
    db.session.commit()
    if request.args.get("next") == "detail":
        return redirect(url_for("crm_lead_detail", lead_id=lead.id))
    return redirect(url_for("crm_clickup_inbox"))


@app.route("/crm/leads/export.csv")
def crm_export_leads():
    import csv, io

    search = request.args.get("q", "").strip()
    assigned = request.args.get("assigned", "")
    leads = _filtered_leads(search, assigned)

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([
        "ID", "Firma", "Osoba kontaktowa", "E-mail", "E-mail 2", "Telefon", "Etap", "Ocena leada",
        "Robot", "Dni wynajmu", "Wartość", "Waluta", "Lokalizacja", "Źródło",
        "Przypisany", "Wynajem od", "Wynajem do", "Konkretne dni wynajmu", "Utworzony", "Notatki",
    ])
    for lead in leads:
        writer.writerow([
            lead.id, lead.company, lead.contact_name or "", lead.contact_email or "", lead.contact_email2 or "",
            lead.contact_phone or "", lead.stage, lead.quality or "Średni", lead.robot_model or "",
            lead.rental_days or "", int(lead.value_pln) if lead.value_pln else "",
            lead.currency or M(lead_market(lead))["currency"], lead.location or "",
            lead.source or "", USERS_BY_ID.get(lead.assigned_to, {}).get("name", ""),
            lead.event_date.strftime("%Y-%m-%d") if lead.event_date else "",
            lead.event_end_date.strftime("%Y-%m-%d") if lead.event_end_date else "",
            ", ".join(
                ed.event_date.strftime("%Y-%m-%d") + (f" ({ed.location})" if ed.location else "")
                for ed in lead.event_days
            ),
            lead.created_at.strftime("%Y-%m-%d") if lead.created_at else "",
            (lead.notes or "").replace("\r\n", " ").replace("\n", " "),
        ])

    # BOM, żeby Excel poprawnie otwierał polskie znaki
    resp = make_response(buf.getvalue().encode("utf-8-sig"))
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f"attachment; filename=leady_{date.today().isoformat()}.csv"
    return resp


def _save_lead_fields(lead, form, actor_id=None):
    lead.company = form.get("company", lead.company or "").strip()
    lead.contact_name = form.get("contact_name", "").strip()
    lead.contact_email = form.get("contact_email", "").strip()
    if "contact_email2" in form:
        lead.contact_email2 = form.get("contact_email2", "").strip() or None
    lead.contact_phone = form.get("contact_phone", "").strip()
    robot = form.get("robot_model", "").strip()
    lead.robot_model = ROBOT_MODEL_ALIASES.get(robot, robot)
    lead.rental_days = form.get("rental_days", type=int)
    lead.value_pln = form.get("value_pln", type=float)
    cur = form.get("currency", lead.currency or "EUR").strip().upper()
    lead.currency = cur if cur in CURRENCIES else (lead.currency or "EUR")
    qual = form.get("quality", lead.quality or "Średni").strip()
    lead.quality = qual if qual in QUALITY_LEVELS else (lead.quality or "Średni")
    lead.notes = form.get("notes", "").strip()
    try:
        assigned = int(form.get("assigned_to", lead.assigned_to or 1))
    except (TypeError, ValueError):
        assigned = lead.assigned_to or 1
    lead.assigned_to = assigned if assigned in USERS_BY_ID else (lead.assigned_to or 1)
    lead.source = form.get("source", "").strip()
    lead.location = form.get("location", "").strip()
    if form.get("client_type") in sales_rules.CLIENT_TYPES:
        lead.client_type = form.get("client_type")
    if "country" in form:
        c = (form.get("country") or "").strip().upper()
        lead.country = c if c in sales_rules.COUNTRIES else None
    # brak wybranych dni w kalendarzu = zostaw ręcznie wpisane "Dni wynajmu" z pola wyżej
    # (set_lead_event_days z pustą listą tylko czyści event_date/event_end_date, nie rental_days)
    msgs = set_lead_event_days(
        lead,
        parse_event_dates(form.get("event_dates", "")),
        parse_event_locations(form.get("event_locations", "")),
    ) or []
    # etap na końcu — telefon po ofercie trafia do opiekuna już zmienionego w tym samym formularzu
    new_stage = form.get("stage", lead.stage)
    if new_stage in STAGES and new_stage != lead.stage:
        if lead.id:
            gate = gated_stage_change(lead, new_stage, actor_id, gate_form_data(form))
            if gate["message"]:
                msgs.append(gate["message"])
        else:
            gate = sales_rules.stage_gate(lead, new_stage, gate_form_data(form),
                                          event_dates=[d.event_date for d in lead.event_days]) \
                if sales_rules.gates_enabled(os.environ) else {"message": ""}
            if gate["message"]:
                msgs.append(gate["message"] + " — lead zapisany jako Nowy")
                lead.stage = "Nowy"
            else:
                lead.stage = new_stage
                data = gate_form_data(form)
                if new_stage == "Stracony":
                    lead.lost_reason, lead.lost_comment = data.get("lost_reason") or None, data.get("lost_comment") or None
                if new_stage in WON_STAGES:
                    lead.win_confirmation = data.get("win_confirmation") or None
                    lead.contract_signed = data.get("win_confirmation") == "umowa"
    return msgs


_include("leady_widoki")


def _callback_leads_query():
    return all_markets(Lead.query)  # numer może należeć do leada z Litwy albo z Niemiec


@app.route("/crm/oddzwonil", methods=["POST"])
def crm_callback():
    """Odpowiedź na „czy ktoś oddzwaniał?”: none = nikt (do jutra), later = za godzinę, phone = numer, który oddzwonił —
    zapis jako udany kontakt przy leadzie (zeruje licznik nieodebranych prób)."""
    data = request.get_json(silent=True) or request.form
    action = data.get("action")
    today = to_local(datetime.utcnow()).date().isoformat()
    if action in ("none", "later"):
        resp = jsonify({"ok": True})
        if action == "none":
            resp.set_cookie(CALLBACK_COOKIE, today, max_age=24 * 3600, samesite="Lax")
        else:
            resp.set_cookie(CALLBACK_LATER_COOKIE, "1", max_age=3600, samesite="Lax")
        return resp
    key = sales_rules.phone_key(data.get("phone"))
    if not key:
        return jsonify({"ok": False, "error": "Wpisz numer telefonu (co najmniej 7 cyfr)."}), 400
    found = [l for l in _callback_leads_query().filter(Lead.contact_phone.isnot(None), Lead.contact_phone != "").all()
             if sales_rules.phone_key(l.contact_phone) == key]
    if not found:
        return jsonify({"ok": False, "not_found": True, "phone": data.get("phone"),
                        "error": "Nie ma leada z tym numerem — możesz dodać go jako nowy lead."})
    found.sort(key=lambda l: (l.stage in ACTIVE_STAGES, l.created_at or datetime.min), reverse=True)
    lead, user = found[0], get_current_user(request)
    note = (data.get("note") or "").strip()[:500]
    db.session.add(Activity(lead_id=lead.id, kind="call", done=True, done_at=datetime.utcnow(), outcome="odebral",
                            assigned_to=user["id"],
                            notes=f"{sales_rules.CALLBACK_PREFIX} ({to_local(datetime.utcnow()):%d.%m %H:%M}) — zapisane z pytania "
                                  f"„czy ktoś oddzwaniał?”" + (f": {note}" if note else "")))
    db.session.commit()
    return jsonify({"ok": True, "lead": {"id": lead.id, "company": lead.company, "stage": lead.stage,
                                         "url": url_for("crm_lead_detail", lead_id=lead.id)},
                    "others": [{"id": l.id, "company": l.company, "url": url_for("crm_lead_detail", lead_id=l.id)}
                               for l in found[1:5]]})


@app.context_processor
def _sales_helpers():
    return {"lead_failed_attempts": lead_failed_attempts, "lead_response_minutes": lead_response_minutes,
            "response_label": sales_rules.response_label, "callback_ask": callback_ask_due()}


@app.route("/crm/wersja")
def crm_version():
    """Która wersja kodu działa na serwerze (commit z Railway) — do sprawdzenia, czy wdrożenie weszło."""
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")
    return jsonify({"commit": sha[:7] or "nieznany", "branch": os.environ.get("RAILWAY_GIT_BRANCH", ""),
                    "deployment": os.environ.get("RAILWAY_DEPLOYMENT_ID", "")})


# ---------- AUDYT OFERT: DRUGI KROK PO OFERCIE ----------
OFFER_AUDIT_DAYS = 45
OFFER_AUDIT_KEY = "offer_steps_audit_v1"
OFFER_AUDIT_REPORT_KEY = "offer_steps_audit_report"
FOLLOWUP_STEP_PREFIX = "✉️ Drugi krok: follow-up"
OFFER_AUDIT_CLEANUP_KEY = "offer_steps_audit_cleanup_v1"
OFFER_CONTACT_GRACE = timedelta(hours=2)  # sam mail z ofertą (i jego kopia w Wysłanych) to jeszcze nie kontakt po ofercie


def _audit_leads_query():
    return all_markets(Lead.query)  # obie rynki (LT i DE) — także z zadania w tle


_include("audyt_ofert")


@app.route("/crm/audyt-ofert", methods=["GET", "POST"])
def crm_offer_audit():
    if request.method == "POST":
        rows = audit_offer_second_steps(days=request.form.get("dni", OFFER_AUDIT_DAYS, type=int) or OFFER_AUDIT_DAYS,
                                        apply=True, actor_id=get_current_user(request)["id"])
        set_setting(OFFER_AUDIT_REPORT_KEY, json.dumps({"at": f"{to_local(datetime.utcnow()):%Y-%m-%d %H:%M}", "rows": rows},
                                                       ensure_ascii=False, default=str)[:200000])
        db.session.commit()
        return redirect(url_for("crm_offer_audit"))
    try:
        last = json.loads(get_setting(OFFER_AUDIT_REPORT_KEY) or "{}")
    except ValueError:
        last = {}
    code = current_market()  # strona pokazuje oglądany rynek (LT albo DE)
    last["rows"] = [r for r in last.get("rows", []) if r.get("market") == code]
    return render_template("crm/audyt_ofert.html", last=last, days=OFFER_AUDIT_DAYS,
                           rows=[r for r in audit_offer_second_steps(apply=False) if r["market"] == code])


def offer_second_step_kind(lead):
    """Drugi krok po ofercie w CRM LT/DE: follow-up mailem (CRM przygotuje go w języku klienta)."""
    return "email"


@app.route("/crm/skutecznosc", methods=["GET", "POST"])
def crm_sales_panel():
    if request.method == "POST":
        t = sales_targets()
        for c in ("PL", "DE", "LT", "RO"):
            for k in ("realny", "ambitny"):
                raw = (request.form.get(f"{c}_{k}") or "").replace(" ", "").replace(",", ".")
                if raw:
                    try:
                        t.setdefault(c, {"cur": "PLN" if c == "PL" else "EUR"})[k] = float(raw)
                    except ValueError:
                        pass
                elif f"{c}_{k}" in request.form and k in t.get(c, {}):
                    t[c].pop(k)
        set_setting(SALES_TARGETS_KEY, json.dumps(t))
        raw = (request.form.get("eur_rate") or "").replace(",", ".").strip()
        if raw:
            try:
                if 3.0 < float(raw) < 7.0:
                    set_setting("fx_EUR", f"{float(raw):.4f}")
                    set_setting("fx_EUR_manual", "1")
                    set_setting("fx_EUR_date", f"ręcznie {date.today().isoformat()}")
            except ValueError:
                pass
        elif request.form.get("eur_auto"):
            set_setting("fx_EUR_manual", "0")
            set_setting("fx_EUR_date", "")
        db.session.commit()
        return redirect(url_for("crm_sales_panel", dni=request.args.get("dni", 90)))
    refresh_eur_rate()
    days = max(30, min(request.args.get("dni", 90, type=int) or 90, 365))
    return render_template("crm/skutecznosc.html", p=build_sales_panel(days), targets=sales_targets())


_include("dostarczalnosc")


@app.route("/crm/leads/<int:lead_id>/delete", methods=["POST"])
def crm_delete_lead(lead_id):
    lead = db.session.get(Lead, lead_id)
    if lead:
        # maile ze skrzynek, oferty i stare formularze zostają w historii, tylko tracą powiązanie z usuwanym
        # leadem (bez tego Postgres nie pozwoli go usunąć); Customer Journey i jego dokumenty znikają razem z nim
        for model in (InboxMessage, CampaignRecipient, SentOffer, ContractRequest, MailTask):
            model.query.filter_by(lead_id=lead.id).update({"lead_id": None}, synchronize_session=False)
        JourneyMail.query.filter_by(lead_id=lead.id).update({"lead_id": None, "pending": False}, synchronize_session=False)
        _journey_badge_reset()
        if lead.clickup_task_id:  # usunięty / odrzucony — synchronizacja z ClickUp nie doda go ponownie
            set_setting(f"clickup_dup:{lead.clickup_task_id}"[:64], "usunięty")
        db.session.delete(lead)
        db.session.commit()
    return redirect(url_for("crm_leads"))


_include("duplikaty")


_include("zadania_widoki")


# ---------- LOGISTYKA (operatorzy, flota, trasy) ----------

LOGISTICS_DEFAULT_BASE = os.environ.get("LOGISTICS_BASE", "Warszawa")
ROBOT_KINDS = {"G1": "Unitree G1", "B2": "Unitree B2 (piesek)"}
LOGISTICS_TENTATIVE_STAGES = ["Oferta"]
PL_WEEKDAYS = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]
LOGISTICS_MAX_RANGE_DAYS = 186
GEO_RETRY_DAYS = 3  # po tylu dniach ponownie pytamy OpenStreetMap o nieznalezione miejsce
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_COUNTRIES = "pl,cz,sk,de,lt,lv,ee,at,hu,ro,ua"
_nominatim_lock = threading.Lock()
_nominatim_last = [0.0]


def robot_need(model):
    """Model robota z leada -> które roboty z floty są potrzebne (G1/B2). Brak modelu = G1."""
    m = (model or "").lower()
    if m.startswith("oba") or ("g1" in m and ("b2" in m or "piesek" in m)):
        return ["G1", "B2"]
    if "b2" in m or "piesek" in m:
        return ["B2"]
    return ["G1"]


def _nominatim_lookup(q):
    """OpenStreetMap: ("ok", (lat, lon, etykieta)) | ("none", None) | ("error", None)."""
    try:
        with _nominatim_lock:
            # regulamin Nominatim: najwyżej 1 zapytanie na sekundę
            wait = 1.1 - (time.time() - _nominatim_last[0])
            if wait > 0:
                time.sleep(wait)
            _nominatim_last[0] = time.time()
            r = requests.get(_NOMINATIM_URL, params={
                "q": q, "format": "jsonv2", "limit": 1,
                "countrycodes": _NOMINATIM_COUNTRIES, "accept-language": "pl",
            }, headers={"User-Agent": "33bots-CRM/1.0 (33bots.pl)"}, timeout=4)
        if r.status_code != 200:
            return "error", None
        data = r.json()
    except Exception:
        return "error", None
    if not data:
        return "none", None
    try:
        lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
    except (KeyError, TypeError, ValueError):
        return "none", None
    parts = [p.strip() for p in (data[0].get("display_name") or q).split(",") if p.strip()]
    return "ok", (lat, lon, ", ".join(parts[:2])[:200])


_include("logistyka_plan")


# ---------- KALKULATOR WYCENY (czas na miejscu + dojazd od granicy PL–LT) ----------

def price_quotes(destination, hours, km=None, departures=None, remote_budget=2, night=0.0, days=1, code=None):
    """Wycena dla miejsca (albo podanych km) z każdego punktu wyjazdu rynku (Litwa: granica w Budzisku,
    Niemcy: Frankfurt/Oder i Görlitz) po cenniku rynku. Zwraca dict: place (nazwa), unknown (nie znaleziono
    miejsca), options {klucz: {label, km, quote}} — od najbliższego punktu, best (klucz najbliższego)."""
    r = pricing.rates(code or current_market())
    deps = [k for k in (departures or list(r["departures"])) if k in r["departures"]] or list(r["departures"])
    point, label = (None, "")
    if km is None:
        point, label = _Locator(remote_budget)(destination)
    out = {"place": label or (destination or "").strip(), "unknown": km is None and point is None, "options": {},
           "best": None, "code": r["code"]}
    rows = []
    for key in deps:
        if km is not None:
            dist = km
        elif point is not None:
            dist = logistics.road_km(r["departures"][key][2], point)
        else:
            continue
        rows.append((dist, key))
    for dist, key in sorted(rows):
        out["options"][key] = {"label": r["departures"][key][0], "km": dist,
                               "quote": pricing.quote(hours, dist, night, days, r)}
    out["best"] = next(iter(out["options"]), None)
    return out


def _lead_place(lead):
    """Miejsce realizacji leada do wyceny: najbliższy dzień z własną lokalizacją albo lokalizacja leada."""
    today = date.today()
    days = sorted(lead.event_days, key=lambda d: (d.event_date < today, d.event_date))
    return next((d.location for d in days if d.location), None) or lead.location or ""


def _calc_params(src, code=None):
    r = pricing.rates(code or current_market())
    hours = src.get("h", type=int) or 8
    if hours not in pricing.HOUR_FACTORS:
        hours = 8
    dep = src.get("z") if src.get("z") in r["departures"] else ""  # "" = najbliższy punkt wyjazdu
    km_raw = (src.get("km") or "").replace(",", ".").strip()
    try:
        km = max(0.0, min(float(km_raw), 5000.0)) if km_raw else None
    except ValueError:
        km = None
    start = pricing.parse_start(src.get("od"))
    days = max(1, min(src.get("dni", type=int) or 1, pricing.MAX_DAYS))
    return hours, dep, km, start, days


def _calc_lead(lead_id):
    """Lead do kalkulatora (także z drugiego rynku — wtedy kalkulator przełącza się na jego rynek)."""
    lead = all_markets(Lead.query.filter(Lead.id == (lead_id or 0))).first() if lead_id else None
    if lead is not None and has_request_context():
        g.market_force = lead_market(lead)
    return lead


@app.route("/crm/kalkulator")
def crm_calculator():
    lead = _calc_lead(request.args.get("lead", type=int))
    code = lead_market(lead) if lead else current_market()
    m, r = markets.get(code), pricing.rates(code)
    hours, dep, km, start, days = _calc_params(request.args, code)
    night = pricing.night_hours(start, hours)
    dest = (request.args.get("cel") or "").strip()[:200]
    if lead and not dest and "cel" not in request.args:
        dest = lt_extract_place(lead)
    hours_note = ""
    if lead and "h" not in request.args:  # godziny z formularza / treści zapytania
        s, e, dur, hours_note = lt_extract_hours(lead)
        hours = next((h for h in sorted(pricing.HOUR_FACTORS) if dur <= h), max(pricing.HOUR_FACTORS))
        if s is not None and "od" not in request.args:
            start = s
        night = pricing.night_hours(start, hours)
    if lead and "dni" not in request.args and lead.event_days:
        days = max(1, min(len(lead.event_days), pricing.MAX_DAYS))
    result = price_quotes(dest, hours, km, night=night, days=days, code=code) if (dest or km is not None) else None
    if not dep:  # bez wyboru: najbliższy punkt wyjazdu (Niemcy: Frankfurt/Oder albo Görlitz)
        dep = (result or {}).get("best") or next(iter(r["departures"]))
    message = ""
    opt = (result or {}).get("options", {}).get(dep) if result else None
    if opt:
        price_txt = f"{opt['quote']['rounded']:,}".replace(",", " ")
        time_txt = (f"{pricing.fmt_hour(start)}–{pricing.fmt_hour(start + hours)}" if start is not None
                    else f"{pricing.fmt_num(hours)} {m['hours_unit']}")
        message = lt_offer_message(lead, _lt_dates_text(lead) if lead else "", time_txt, dest or result["place"],
                                   price_txt, code=code)
    return render_template(
        "crm/calculator.html", lead=lead, dest=dest, hours=hours, dep=dep, km=km, result=result,
        message=message, hours_note=hours_note,
        wa_digits=lt_phone_digits(lead.contact_phone, code) if lead else "",
        start=start, start_text=pricing.fmt_hour(start) if start is not None else "",
        end_text=pricing.fmt_hour(start + hours) if start is not None else "", night=night,
        days=days, day_options=[(d, pricing.days_label(d), pricing.day_discount(d))
                                for d in range(1, pricing.MAX_DAYS + 1)],
        departures={k: v[0] for k, v in r["departures"].items()},
        departures_from={k: v[1] for k, v in r["departures"].items()},
        hour_options=sorted(pricing.HOUR_FACTORS, reverse=True), calc_market=m,
        cfg={"base": r["base"], "rate": r["km"], "share": int(pricing.TRAVEL_MAX_SHARE * 100),
             "round": pricing.ROUND_TO, "night_from": pricing.NIGHT_FROM, "night_to": pricing.NIGHT_TO,
             "night_mult": pricing.fmt_num(pricing.NIGHT_MULT), "lodging_km": pricing.LODGING_KM,
             "lodging": r["lodging"], "calc_from": m["calc_from"], "min": r.get("min") or 0,
             "disc": {d: int(p * 100) for d, p in pricing.DAY_DISCOUNTS.items()}},
        fmt_num=pricing.fmt_num,
        fmt_zl=pricing.fmt_zl,
    )


@app.route("/crm/kalkulator/zapisz", methods=["POST"])
def crm_calculator_save():
    """Ustawia wartość leada na wyliczoną cenę i zapisuje rozbicie wyceny w komentarzu."""
    lead = _calc_lead(request.form.get("lead", type=int))
    if not lead:
        abort(404)
    code = lead_market(lead)
    hours, dep, km, start, days = _calc_params(request.form, code)
    dest = (request.form.get("cel") or "").strip()[:200]
    res = price_quotes(dest, hours, km, departures=[dep] if dep else None, remote_budget=1,
                       night=pricing.night_hours(start, hours), days=days, code=code)
    opt = res["options"].get(dep or res["best"] or "")
    if not opt:
        return redirect(url_for("crm_calculator", lead=lead.id, cel=dest, h=hours, z=dep or None))
    q = opt["quote"]
    value = q["rounded"] if request.form.get("which") == "rounded" else q["total"]
    user = get_current_user(request)
    lead.value_pln, lead.currency = float(value), "EUR"
    db.session.add(Activity(
        lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(), assigned_to=user["id"],
        notes=f"🧮 {pricing.describe(q, opt['label'], res['place'])}\nWartość leada ustawiona na {pricing.fmt_zl(value)} netto.",
    ))
    db.session.commit()
    return redirect(url_for("crm_lead_detail", lead_id=lead.id))


_include("logistyka_widoki")


# ---------- USTAWIENIA (AppSetting) ----------

SETTING_DEFAULTS = {
    "umowa_wzor": journey.DEFAULT_TEMPLATE,  # tekst umowy (z dawnego generatora — mógł być zmieniony)
    "umowa_wzor_de": journey.DEFAULT_TEMPLATE_DE,  # umowa po niemiecku (rynek Niemcy)
    "umowa_vat": "0",  # LT: odwrotne obciążenie
}


def mkey(base, code=None):
    """Klucz ustawienia osobny dla rynku: Litwa — dawna nazwa (np. jr_template_docx), Niemcy — z końcówką _de."""
    code = code or current_market()
    return base if code == "lt" else f"{base}_{code}"


def get_setting(key):
    row = db.session.get(AppSetting, key)
    return row.value if row and row.value is not None else SETTING_DEFAULTS.get(key, "")


def set_setting(key, value):
    row = db.session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key)
        db.session.add(row)
    row.value = value


def to_local(dt):
    """Czas z bazy (UTC) -> czas warszawski do wyświetlenia."""
    if not dt or WARSAW_TZ is None:
        return dt
    return dt.replace(tzinfo=timezone.utc).astimezone(WARSAW_TZ).replace(tzinfo=None)


def _pdf_response(pdf_bytes, filename):
    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/crm/oferty")
def crm_offers():
    drafts = SentOffer.query.filter(SentOffer.sent_at.is_(None), _not_compose_offer()).order_by(SentOffer.created_at.desc()).all()
    sent = SentOffer.query.filter(SentOffer.sent_at.isnot(None)).order_by(SentOffer.sent_at.desc()).limit(50).all()
    return render_template("crm/offers.html", drafts=drafts, sent=sent)


@app.route("/crm/oferty/<int:oid>", methods=["GET", "POST"])
def crm_offer_review(oid):
    offer = db.session.get(SentOffer, oid)
    if not offer:
        abort(404)
    user = get_current_user(request)
    error = warning = None
    changes = []
    if request.method == "POST" and not offer.sent_at:
        action = request.form.get("action")
        if action == "odrzuc":
            lead_id = offer.lead_id
            db.session.delete(offer)
            db.session.commit()
            return redirect(url_for("crm_lead_detail", lead_id=lead_id) if lead_id else url_for("crm_offers"))
        recipients, error = check_recipients(request.form.get("to_addr", ""))
        to_addr = ", ".join(recipients)
        subject = request.form.get("subject", "").strip()
        body = request.form.get("body", "").replace("\r\n", "\n").strip()
        if error:
            pass
        elif not (subject and body):
            error = "Temat i treść maila nie mogą być puste."
        else:
            offer.to_addr, offer.subject, offer.body = to_addr[:200], subject[:300], body
            db.session.commit()
            if action == "wyslij":
                error, warning, changes = deliver_offer(offer, user)
                if not error and not warning:
                    return redirect(url_for("crm_offer_review", oid=offer.id, wyslano=1))
    return render_template(
        "crm/offer_review.html", offer=offer, error=error, warning=warning, changes=changes,
        sender=market_sender(USERS_BY_ID.get(offer.sender_id) or user,  # skrzynka rynku leada (33bots.de / .lt)
                             lead_market(db.session.get(Lead, offer.lead_id) if offer.lead_id else None)),
        opts=_offer_options(offer),
        offer_val=offer_value(offer),
        just_sent=bool(request.args.get("wyslano")) or (offer.sent_at and request.method == "POST"),
    )


@app.route("/crm/oferty/<int:oid>/<path:filename>")
def crm_offer_pdf(oid, filename):
    offer = db.session.get(SentOffer, oid)
    if not offer or not offer.pdf:
        abort(404)
    return _pdf_response(offer.pdf, offer.filename)


# ---------- CUSTOMER JOURNEY (posprzedaż: umowa → baza wiedzy → logistyka → wydarzenie → po wydarzeniu) ----------
# Każdy lead na etapie Wygrany ma swój Journey. CRM przygotowuje maile (umowa DOCX, osobno instrukcja bazy wiedzy,
# przypomnienia, prośba o opinię), ale wysyła je zawsze człowiek. Odesłane umowy i bazy wiedzy CRM sam
# wyłapuje w skrzynkach (kontakt@ + osobiste) i odhacza; niepewne przypadki zostawia do potwierdzenia.

JOURNEY_STAGES = ("Wygrany", "Zrealizowany")
JOURNEY_POLL_SECONDS = max(120, int(os.environ.get("JOURNEY_POLL_SECONDS", 300)))
JOURNEY_SCAN_DAYS = 60  # pierwszy przegląd skrzynki sięga tyle dni wstecz
JOURNEY_MAX_FILE = 20 * 1024 * 1024
JOURNEY_NEW_DOC_DAYS = 3  # dokument od klienta starszy niż tyle dni nie świeci się jako „NOWY”
JOURNEY_EDITABLE_EXT = {".docx", ".doc", ".odt", ".rtf", ".docm"}
JOURNEY_CONTRACT_RESULTS = ("umowa_od_klienta", "umowa_i_baza_od_klienta", "umowa_wyslana", "umowa_i_baza_wyslane")
JOURNEY_MAIL_RESULTS = {
    "umowa_od_klienta": "klient odesłał umowę",
    "baza_od_klienta": "klient przysłał bazę wiedzy",
    "umowa_i_baza_od_klienta": "klient odesłał umowę i bazę wiedzy",
    "umowa_wyslana": "wysłano umowę do klienta",
    "baza_prosba": "wysłano prośbę o bazę wiedzy",
    "umowa_i_baza_wyslane": "wysłano umowę i instrukcję bazy wiedzy",
    "inne": "inna korespondencja",
    "crm": "wysłane z CRM",
}
DOC_KINDS = {
    "umowa_podpisana": "Umowa od klienta (podpisana)",
    "umowa_wyslana": "Umowa wysłana do klienta",
    "baza_wiedzy": "Baza wiedzy",
    "faktura": "Faktura",
    "inny": "Inny dokument",
}
FREE_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "wp.pl", "o2.pl", "onet.pl", "onet.eu", "op.pl", "interia.pl", "interia.eu",
    "poczta.onet.pl", "vp.pl", "tlen.pl", "gazeta.pl", "go2.pl", "spoko.pl", "poczta.fm", "int.pl", "yahoo.com",
    "outlook.com", "hotmail.com", "live.com", "msn.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
    "aol.com", "gmx.com", "gmx.de", "seznam.cz", "mail.ru",
}
_JOURNEY_LOCK = threading.Lock()
_JOURNEY_SCAN = {"at": None, "info": "", "errors": [], "boxes": []}
_JOURNEY_BADGE = {}  # rynek -> (czas, liczba)
_MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def journey_cfg():
    cfg = {}
    for key, default in journey.DEFAULTS.items():
        try:
            cfg[key] = max(1, min(365, int(float(get_setting(f"jr_{key}") or default))))
        except ValueError:
            cfg[key] = default
    return cfg


def journey_owner():
    """Kto prowadzi posprzedaż: od niego wychodzą maile i do niego idą zadania (domyślnie Jerzy)."""
    try:
        uid = int(get_setting("jr_owner") or 3)
    except ValueError:
        uid = 3
    return USERS_BY_ID.get(uid) or USERS_BY_ID.get(3) or USERS[0]


def market_sender(user, code):
    """Nadawca maili do klienta z rynku `code` — zawsze skrzynka w domenie rynku (33bots.lt / 33bots.de):
    1) skrzynka tej osoby z listy rynku (o.latek@33bots.de …), 2) jej osobna skrzynka z Railway (SMTP_USER_<IMIĘ>)
    w domenie rynku, 3) wspólna kontakt@ rynku podpisana imieniem osoby, 4) inna skrzynka rynku — wtedy nadawcą
    jest jej właściciel (nigdy cudze imię z cudzym adresem). Bez skrzynek rynku — dawne ustawienia osoby."""
    user = user or USERS[0]
    base = {k: v for k, v in user.items() if k not in ("login", "pwd", "market")}
    dom = markets.get(code)["domain"]
    boxes = [b for b in mail_boxes(code) if b["pwd"]]
    own = next((b for b in boxes if b.get("user_id") == base.get("id")), None)
    if own:
        return dict(base, login=own["login"], pwd=own["pwd"], email=own["login"], market=own["market"])
    login, pwd = _mailbox_for(base)
    if login and pwd and login.lower().endswith("@" + dom):
        return dict(base, login=login, pwd=pwd, email=login, market=code)
    shared = next((b for b in boxes if b["login"].split("@")[0] in ("kontakt", "info", "hi")), None)
    if shared:
        return dict(base, login=shared["login"], pwd=shared["pwd"], email=shared["login"], market=shared["market"])
    if boxes:
        b = boxes[0]
        owner = USERS_BY_ID.get(b.get("user_id")) or base
        return dict(owner, login=b["login"], pwd=b["pwd"], email=b["login"], market=b["market"])
    return user


def journey_sender(lead):
    """Od kogo idą umowa, baza wiedzy i przypomnienia: opiekun leada (ze skrzynki jego rynku), bez opiekuna — osoba z ⚙️."""
    return market_sender(USERS_BY_ID.get(lead.assigned_to) or journey_owner(), lead_market(lead))


_include("journey_stan")


def _journey_badge_reset():
    _JOURNEY_BADGE.clear()


def journey_badge():
    """Ilu klientów oglądanego rynku czeka na akcję człowieka (licznik w menu) — liczone najwyżej raz na minutę."""
    code = getattr(g, "market_filter", None) or current_market() if has_request_context() else DEFAULT_MARKET
    t, n = _JOURNEY_BADGE.get(code, (0.0, 0))
    if time.time() - t < 60:
        return n
    try:
        n = sum(1 for v in journey_board_views() if v["attention"])
    except Exception:
        n = 0
    _JOURNEY_BADGE[code] = (time.time(), n)
    return n


def journey_board_views(stages=("Wygrany",)):
    """Karty klientów z Customer Journey (zapytania zbiorcze, bez N+1 na licznikach).
    Domyślnie tablica (Wygrany); archiwum = stages=("Zrealizowany",)."""
    rows = (Journey.query.join(Lead).filter(Lead.stage.in_(stages), Lead.clickup_pending.isnot(True))
            .options(db.contains_eager(Journey.lead).selectinload(Lead.event_days)).all())
    if not rows:
        return []
    ids = [j.lead_id for j in rows]
    unseen = dict(db.session.query(JourneyDocument.lead_id, db.func.count(JourneyDocument.id))
                  .filter(JourneyDocument.lead_id.in_(ids), JourneyDocument.seen.is_(False))
                  .group_by(JourneyDocument.lead_id).all())
    pending = dict(db.session.query(JourneyMail.lead_id, db.func.count(JourneyMail.id))
                   .filter(JourneyMail.lead_id.in_(ids), JourneyMail.pending.is_(True))
                   .group_by(JourneyMail.lead_id).all())
    today, cfg = _local_today(), journey_cfg()
    return [journey_view(j, j.lead, today, cfg, unseen.get(j.lead_id, 0), pending.get(j.lead_id, 0)) for j in rows]


def ensure_journey(lead, needs_review=False):
    """Journey dla wygranego leada (tworzy przy pierwszym wejściu na Wygrany)."""
    if lead.journey:
        return lead.journey
    j = Journey(lead=lead, needs_review=needs_review)
    if lead.contract_signed:
        j.contract_status, j.contract_signed_via, j.contract_signed_at = "podpisana", "recznie", datetime.utcnow()
    db.session.add(j)
    _journey_badge_reset()
    return j


def ensure_journeys():
    """Brakujące Journey dla wygranych. Przy pierwszym uruchomieniu leady sprzed Customer Journey dostają
    „do potwierdzenia” (nie wiadomo, co już do nich poszło) i przenosimy dokumenty z dawnego formularza umowy."""
    launched = get_setting("jr_launched")
    missing = Lead.query.filter(Lead.stage.in_(JOURNEY_STAGES), Lead.clickup_pending.isnot(True),
                                ~Lead.journey.has(), own_leads()).all()
    for lead in missing:
        # zrealizowane od razu do archiwum — bez pytania „co już poszło?”
        ensure_journey(lead, needs_review=not launched and lead.stage == "Wygrany")
    if not launched:
        _migrate_contract_forms()
        set_setting("jr_launched", _local_today().isoformat())
    if not get_setting("jr_fix_backfill"):
        _journey_fix_backfill()
        set_setting("jr_fix_backfill", _local_today().isoformat())
    if not get_setting("jr_archive_v1"):
        Journey.query.filter(Journey.needs_review.is_(True),
                             Journey.lead_id.in_(db.session.query(Lead.id).filter(Lead.stage == "Zrealizowany"))) \
            .update({"needs_review": False}, synchronize_session=False)
        set_setting("jr_archive_v1", _local_today().isoformat())
    db.session.commit()
    return len(missing)


_include("journey_umowa")


def journey_contract_fields(j, lead):
    """Pola umowy do okienka: z leada, nadpisane tym, co człowiek poprawił przy poprzedniej wysyłce."""
    days = journey_days(lead)
    places = [ed.location for ed in lead.event_days if ed.location]
    base = {
        "nazwa_wydarzenia": "",
        "daty_wydarzenia": ", ".join(f"{d:%d.%m.%Y}" for d in days),
        "godziny": "",
        "miejsce_wydarzenia": "; ".join(dict.fromkeys(places)) or (lead.location or ""),
        "charakter_wydarzenia": "",
        "rodzaj_przestrzeni": "",
        "robot": lead.robot_model or "Unitree G1",
        "cena_netto": f"{lead.value_pln:g}" if lead.value_pln else "",
        "waluta": lead.currency or "EUR",
        "vat": str(get_setting("umowa_vat") or "0"),
    }
    base.update({k: str(v) for k, v in _jjson(j.contract_fields).items() if k in base})
    return base


def journey_contract_docx(lead, fields):
    code = lead_market(lead)
    ctx = journey.contract_context(fields, lead.id, _local_today(), lead.contact_email or "", lead.contact_phone or "",
                                   code=code)
    custom = get_setting(mkey("jr_template_docx", code))
    if custom:
        import base64
        return journey.fill_docx(base64.b64decode(custom), ctx)
    return journey.render_docx(contract_template(code), ctx)


def contract_template(code):
    """Tekst umowy rynku (Litwa — po litewsku, Niemcy — po niemiecku)."""
    return get_setting(mkey("umowa_wzor", code)) or journey.default_template(code)


def journey_contract_filename(lead):
    word = {"lt": "Sutartis", "de": "Vertrag"}.get(lead_market(lead), "Sutartis")
    return f"{word}_33Bots_{journey.slug(lead.company)}.docx"


def _journey_kb_instruction(code="lt"):
    with open(journey.kb_instruction(code)[0], "rb") as fh:
        return fh.read()


def journey_draft(j, lead, kind):
    """Szkic maila do akceptu: adresat, temat, treść, załączniki (+ pola umowy do poprawienia)."""
    owner = journey_sender(lead)
    cfg = journey_cfg()
    subject, body = journey.email_draft(
        kind, lead.company, lead.robot_model, journey_days(lead), owner, _local_today(), cfg["kb_deadline"],
        sent_on=_local_date(j.contract_sent_at),
        review_link=(get_setting("jr_review_link") or "").strip(), prev_subject=j.contract_subject or "",
        code=lead_market(lead),
    )
    attachments = []
    if kind in ("umowa", "umowa_przypomnienie", "umowa_baza"):
        attachments.append(journey_contract_filename(lead) + " (do uzupełnienia i podpisu)")
    if kind in ("baza", "baza_przypomnienie", "umowa_baza"):
        attachments.append(journey.kb_instruction(lead_market(lead))[1])
    login, _ = _mailbox_for(owner)
    warnings = []
    if not lead.emails:
        warnings.append("Lead nie ma adresu e-mail — wpisz go w polu „Do”.")
    if not login:
        warnings.append("Wysyłka maili nie jest skonfigurowana (SMTP w Railway) — mail nie wyjdzie.")
    elif owner.get("email") and login.lower() != owner["email"].lower():
        key = re.sub(r"[^A-Z0-9]", "_", owner["name"].upper())
        warnings.append(f"Skrzynka {owner['name']} nie jest podpięta (SMTP_PASS_{key}) — mail wyjdzie z {login}.")
    return {
        "kind": kind, "label": journey.EMAIL_KINDS[kind], "company": lead.company,
        "to": ", ".join(lead.emails), "recipients": lead.emails, "from": f"{owner.get('full_name') or owner['name']} <{login or owner.get('email', '')}>",
        "subject": human_text(subject), "body": human_text(body), "attachments": attachments, "warnings": warnings,
        "fields": journey_contract_fields(j, lead) if kind in ("umowa", "umowa_baza") else None,
        "field_labels": journey.EDITABLE_FIELDS if kind in ("umowa", "umowa_baza") else [],
    }


def _journey_note(lead, text, author_id):
    db.session.add(Activity(lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(),
                            assigned_to=author_id, notes=text))


def journey_send(j, lead, kind, to_addr, subject, body, fields, user):
    """Wysyła zatwierdzony mail ze skrzynki opiekuna leada i odhacza krok. Zwraca błąd albo None."""
    owner = journey_sender(lead)
    attachments, contract = [], None
    if kind in ("umowa", "umowa_baza"):
        allowed = journey_contract_fields(j, lead)
        fields = {k: str(v).strip()[:300] for k, v in (fields or {}).items() if k in allowed}
        contract = journey_contract_docx(lead, fields)
        attachments.append((journey_contract_filename(lead), contract, journey.DOCX_MIME))
    elif kind == "umowa_przypomnienie":
        last = (JourneyDocument.query.filter_by(lead_id=lead.id, kind="umowa_wyslana")
                .order_by(JourneyDocument.created_at.desc()).first())
        if last and last.data and (last.filename or "").lower().endswith(".docx"):
            attachments.append((last.filename, last.data, last.mime or journey.DOCX_MIME))
        else:
            attachments.append((journey_contract_filename(lead),
                                journey_contract_docx(lead, journey_contract_fields(j, lead)), journey.DOCX_MIME))
    if kind in ("baza", "baza_przypomnienie", "umowa_baza"):
        kb_path, kb_name = journey.kb_instruction(lead_market(lead))
        attachments.append((kb_name, _journey_kb_instruction(lead_market(lead)), "application/pdf"))
    err = _smtp_send(to_addr, subject, body, owner["name"], attachments=attachments or None, sender=owner)
    if err:
        return err
    now = datetime.utcnow()
    if _smtp_send.last_msgid:
        j.msgids = ((j.msgids or "") + " " + _smtp_send.last_msgid).strip()[-4000:]
    if kind in ("umowa", "umowa_baza"):
        if j.contract_status == "podpisana":
            pass  # wysłana jeszcze raz już po podpisaniu — status zostaje
        else:
            journey_set_contract(j, lead, "do_wyslania")
            journey_set_contract(j, lead, "wyslana", when=now)
        j.contract_subject, j.contract_fields = subject[:300], json.dumps(fields, ensure_ascii=False)
        doc = JourneyDocument(lead_id=lead.id, kind="umowa_wyslana", filename=journey_contract_filename(lead),
                              mime=journey.DOCX_MIME, data=contract, source=f"wysłana z CRM do {to_addr}",
                              created_by=user["id"], received_at=now)
        db.session.add(doc)
    elif kind == "umowa_przypomnienie":
        j.contract_reminded_at, j.contract_reminders = now, (j.contract_reminders or 0) + 1
    if kind in ("baza", "umowa_baza"):
        if j.kb_status == "brak":  # wysłana jeszcze raz już po otrzymaniu — status zostaje
            journey_set_kb(j, "wyslana", when=now)
    elif kind == "baza_przypomnienie":
        j.kb_reminded_at, j.kb_reminders = now, (j.kb_reminders or 0) + 1
    elif kind == "opinia":
        after = _jjson(j.after)
        after["opinia"] = True
        j.after = json.dumps(after)
        _journey_maybe_close(j)
    j.needs_review = False  # człowiek zajął się tym klientem — pytanie „co już poszło?” niepotrzebne
    j.updated_at = now
    _journey_note(lead, f"📨 {journey.EMAIL_KINDS[kind]} — wysłane do {to_addr} (od: {owner.get('full_name') or owner['name']})\n"
                        f"Temat: {subject}\n---\n{body}", user["id"])
    db.session.commit()
    _journey_badge_reset()
    if kind in ("umowa", "umowa_baza"):
        _journey_convert_async(doc.id)
    return None


_include("journey_dokumenty")


# --- skrzynki: co klient odesłał, co poszło od nas spoza CRM ---

def journey_mailboxes():
    """(host, [(login, hasło)]) — kontakt@ (INBOX_IMAP_*) i osobiste skrzynki z hasłem
    IMAP_PASS_<IMIĘ> albo SMTP_PASS_<IMIĘ> (te same skrzynki na tym samym serwerze)."""
    host = os.environ.get("IMAP_HOST", "").strip() or os.environ.get("SMTP_HOST", "").strip()
    boxes = [(b["login"], b["pwd"]) for b in _inbox_boxes()]  # kontakt@ obu rynków
    boxes += [(b["login"], b["pwd"]) for b in mail_boxes("all") if b["pwd"]]  # o.latek@33bots.lt / .de …
    for u in USERS:
        key = re.sub(r"[^A-Z0-9]", "_", u["name"].upper())
        pwd = (os.environ.get(f"IMAP_PASS_{key}", "") or os.environ.get(f"SMTP_PASS_{key}", "")).strip()
        login = (os.environ.get(f"IMAP_USER_{key}", "") or os.environ.get(f"SMTP_USER_{key}", "")).strip() or u.get("email", "")
        if pwd and login:
            boxes.append((login, pwd))
    seen, out = set(), []
    for login, pwd in boxes:
        if login.lower() not in seen:
            seen.add(login.lower())
            out.append((login, pwd))
    return (host, out) if host else (None, [])


_include("journey_poczta")


def _journey_scheduler():
    time.sleep(25)
    while True:
        try:
            with app.app_context():
                ensure_journeys()
                if journey_mailboxes()[1]:
                    scan_journey_mail()
                journey_convert_pending()
                db.session.remove()
            health_beat("journey")
        except Exception as e:
            app.logger.exception("journey_scheduler")
            health_fail("journey", e)
        time.sleep(JOURNEY_POLL_SECONDS)


if os.environ.get("JOURNEY_SCAN", "1") != "0":
    _start_worker("journey", _journey_scheduler)


# --- widoki ---

_include("journey_widoki")


@app.route("/crm/journey/<int:lead_id>/umowa.docx", methods=["POST"])
def crm_journey_contract_preview(lead_id):
    """Podgląd umowy z polami z okienka (przed wysyłką) — DOCX do pobrania."""
    j, lead = _journey_or_404(lead_id)
    allowed = journey_contract_fields(j, lead)
    fields = {k: (request.form.get(k) or "").strip()[:300] for k in allowed}
    resp = make_response(journey_contract_docx(lead, fields))
    resp.headers["Content-Type"] = journey.DOCX_MIME
    resp.headers["Content-Disposition"] = f'attachment; filename="{journey_contract_filename(lead)}"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/crm/journey/<int:lead_id>/wyslij", methods=["POST"])
def crm_journey_send(lead_id):
    j, lead = _journey_or_404(lead_id)
    f = request.form
    kind = f.get("rodzaj", "")
    if kind not in journey.EMAIL_KINDS:
        return jsonify({"error": "nieznany rodzaj maila"}), 400
    recipients, bad = check_recipients(f.get("to"))
    to_addr = ", ".join(recipients)
    subject = (f.get("subject") or "").strip()[:300]
    body = (f.get("body") or "").replace("\r\n", "\n").strip()[:20000]
    if bad:
        return jsonify({"error": bad}), 400
    if not subject or not body:
        return jsonify({"error": "Uzupełnij temat i treść."}), 400
    fields = {k: f.get(f"pole_{k}", "") for k in journey_contract_fields(j, lead)} if kind in ("umowa", "umowa_baza") else None
    err = journey_send(j, lead, kind, to_addr, subject, body, fields, get_current_user(request))
    if err:
        return jsonify({"error": f"Mail nie wyszedł: {err}"}), 502
    return jsonify({"ok": True, "warning": _smtp_send.last_warning})


_include("journey_widoki_dokumenty")


@app.route("/crm/journey/ustawienia", methods=["GET", "POST"])
def crm_journey_settings():
    msg = err = None
    unknown = []
    if request.method == "POST":
        action = request.form.get("action")
        if action == "zapisz":
            for key, default in journey.DEFAULTS.items():
                raw = (request.form.get(key) or "").strip()
                try:
                    value = int(raw)
                    if not 1 <= value <= 365:
                        raise ValueError
                except ValueError:
                    err = f"Pole „{key}” musi być liczbą dni od 1 do 365."
                    break
                set_setting(f"jr_{key}", str(value))
            owner = request.form.get("owner", type=int)
            if owner in USERS_BY_ID:
                set_setting("jr_owner", str(owner))
            set_setting("jr_bank_account", (request.form.get("bank_account") or "").strip()[:60])
            set_setting("jr_bank_name", (request.form.get("bank_name") or "").strip()[:100])
            set_setting("jr_ksef", "1" if request.form.get("ksef") else "0")
            pay = (request.form.get("payment_days") or "14").strip()
            if pay.isdigit() and int(pay) <= 120:
                set_setting("jr_payment_days", pay)
            else:
                err = err or "Termin płatności: liczba dni od 0 do 120."
            link = (request.form.get("review_link") or "").strip()[:500]
            if link and not link.startswith(("https://", "http://")):
                err = err or "Link do opinii musi zaczynać się od https://"
            else:
                set_setting("jr_review_link", link)
            vat = (request.form.get("vat") or "23").strip().replace(",", ".")
            try:
                if not 0 <= float(vat) <= 100:
                    raise ValueError
                set_setting("umowa_vat", vat)
            except ValueError:
                err = err or "VAT musi być liczbą od 0 do 100."
            if not err:
                db.session.commit()
                msg = "Zapisano."
            else:
                db.session.rollback()
        elif action == "wzor":
            up = request.files.get("wzor")
            data = up.read(5 * 1024 * 1024 + 1) if up else b""
            if not data or len(data) > 5 * 1024 * 1024:
                err = "Wybierz plik DOCX (do 5 MB)."
            else:
                err, unknown = journey.check_docx_template(data)
                if not err:
                    import base64
                    set_setting(mkey("jr_template_docx"), base64.b64encode(data).decode())
                    set_setting(mkey("jr_template_name"), journey.safe_filename(up.filename)[:60])
                    db.session.commit()
                    msg = "Wgrano własny wzór umowy."
        elif action == "domyslny":
            set_setting(mkey("jr_template_docx"), "")
            set_setting(mkey("jr_template_name"), "")
            db.session.commit()
            msg = "Wrócono do wzoru z CRM."
    return render_template(
        "crm/journey_settings.html", cfg=journey_cfg(), owner=journey_owner(), msg=msg, err=err, unknown=unknown,
        review_link=get_setting("jr_review_link") or "", vat=get_setting("umowa_vat") or "0",
        template_name=get_setting(mkey("jr_template_name")) or "", fields=journey.FIELDS_DOC,
        boxes=journey_mailboxes()[1], scan=_JOURNEY_SCAN, soffice=bool(journey.soffice_path()),
        bank_account=get_setting("jr_bank_account") or "", bank_name=get_setting("jr_bank_name") or "",
        payment_days=get_setting("jr_payment_days") or "14", ksef=get_setting("jr_ksef") != "0",
        infakt=bool(infakt_key()),
    )


@app.route("/crm/journey/ustawienia/wzor.docx")
def crm_journey_template_docx():
    """Obecny wzór jako DOCX: wgrany albo nasz tekst z polami {{ … }} — do poprawienia w Wordzie i wgrania."""
    custom = get_setting(mkey("jr_template_docx"))
    if custom:
        import base64
        data = base64.b64decode(custom)
    else:
        ctx = {k: "{{ " + k + " }}" for k in journey.KNOWN_FIELDS}
        data = journey.render_docx(contract_template(current_market()), ctx)
    resp = make_response(data)
    resp.headers["Content-Type"] = journey.DOCX_MIME
    resp.headers["Content-Disposition"] = f'attachment; filename="Wzor_umowy_33Bots_{current_market().upper()}.docx"'
    return resp


# --- faktura po wydarzeniu: inFakt API v3 (INFAKT_API_KEY) + dane do faktury z NIP (Biała lista MF) ---
# Człowiek klika „Wystaw fakturę” i sprawdza dane w okienku; CRM: faktura w inFakt → KSeF → PDF → mail
# do klienta (od prowadzącego posprzedaż) → PDF w dokumentach → „Faktura wystawiona” odhaczona.

INFAKT_API = os.environ.get("INFAKT_API_URL", "https://api.infakt.pl/api/v3").rstrip("/")
MF_WHITELIST_API = "https://wl-api.mf.gov.pl/api/search/nip"
INVOICE_TAX_SYMBOLS = ("23", "8", "5", "0", "np", "zw")


def infakt_key():
    return os.environ.get("INFAKT_API_KEY", "").strip()


def _infakt_error_text(detail):
    if isinstance(detail, dict):
        errs = (detail.get("invoice_errors") or detail.get("errors") or detail.get("error")
                or detail.get("message") or detail.get("processing_description") or detail)
        if isinstance(errs, dict):
            return "; ".join(f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in errs.items())[:400]
        return str(errs)[:400]
    return str(detail)[:400]


def _infakt(method, path, **kw):
    """Zapytanie do inFakt. Zwraca (JSON albo bajty PDF, błąd albo None)."""
    key = infakt_key()
    if not key:
        return None, "brak INFAKT_API_KEY w Railway (klucz API z inFakt: Ustawienia konta → API)"
    try:
        r = requests.request(method, INFAKT_API + path, timeout=30,
                             headers={"X-inFakt-ApiKey": key, "Accept": "application/json"}, **kw)
    except requests.RequestException as e:
        return None, f"inFakt nie odpowiada ({type(e).__name__})"
    if r.status_code >= 400:
        try:
            detail = r.json()
        except ValueError:
            detail = r.text[:300]
        return None, f"inFakt {r.status_code}: {_infakt_error_text(detail)}"
    if r.content[:5] == b"%PDF-":
        return r.content, None
    try:
        return r.json(), None
    except ValueError:
        return r.content, None


def infakt_bank_account():
    """(numer konta, bank) z ustawień Customer Journey albo domyślnego konta w inFakt."""
    acc, name = (get_setting("jr_bank_account") or "").strip(), (get_setting("jr_bank_name") or "").strip()
    if acc:
        return acc, name, None
    res, err = _infakt("GET", "/bank_accounts.json")
    if err:
        return "", "", f"nie znam konta bankowego do faktury — wpisz je w ustawieniach Customer Journey ({err})"
    items = res.get("entities") if isinstance(res, dict) else res
    items = [i for i in (items or []) if isinstance(i, dict) and i.get("account_number")]
    if not items:
        return "", "", "w inFakt nie ma konta bankowego — wpisz je w ustawieniach Customer Journey"
    best = next((i for i in items if i.get("default")), items[0])
    return best.get("account_number", ""), best.get("bank_name", ""), None


def nip_lookup(nip):
    """Nazwa i adres firmy z Białej listy podatników VAT (Ministerstwo Finansów, bez klucza)."""
    d = journey.nip_digits(nip)
    if not journey.nip_valid(d):
        return None, "niepoprawny NIP (10 cyfr, zgodna suma kontrolna)"
    try:
        r = requests.get(f"{MF_WHITELIST_API}/{d}", params={"date": date.today().isoformat()}, timeout=15)
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        return None, f"Biała lista nie odpowiada ({type(e).__name__}) — wpisz dane ręcznie"
    subject = ((data or {}).get("result") or {}).get("subject")
    if not subject:
        return None, "tego NIP-u nie ma na Białej liście (np. firma nie jest czynnym podatnikiem VAT) — wpisz dane ręcznie"
    street, postcode, city = journey.parse_mf_address(subject.get("workingAddress") or subject.get("residenceAddress") or "")
    return {"nip": d, "name": subject.get("name") or "", "street": street, "postcode": postcode, "city": city,
            "status": subject.get("statusVat") or ""}, None


def _lead_billing_from(lead, f):
    lead.nip = journey.nip_digits(f.get("nip")) if journey.nip_digits(f.get("nip")) else (f.get("nip") or "").strip()[:20] or None
    lead.billing_name = (f.get("name") or "").strip()[:300] or None
    lead.billing_street = (f.get("street") or "").strip()[:200] or None
    lead.billing_postcode = (f.get("postcode") or "").strip()[:20] or None
    lead.billing_city = (f.get("city") or "").strip()[:120] or None
    country = M(lead_market(lead))["country"]
    lead.billing_country = ((f.get("country") or country).strip().upper()[:2]) or country
    lead.billing_email = ", ".join(email_list(f.get("email")))[:200] or None


def journey_invoice_draft(j, lead):
    """Okienko „Wystaw fakturę”: dane nabywcy (z leada, NIP z odesłanej umowy, Biała lista), pozycja, daty, mail."""
    owner = market_sender(journey_owner(), lead_market(lead))
    today = _local_today()
    days = journey_days(lead)
    hints, warnings = [], []
    nip = lead.nip or ""
    if not nip:
        for d in reversed(lead.journey_documents):
            if d.kind == "umowa_podpisana":
                text = journey.extract_text(d.filename, d.data or b"", 30000) or journey.extract_text("u.pdf", d.pdf or b"", 30000)
                nip = journey.find_nip(text)
                if nip:
                    hints.append("NIP odczytany z odesłanej umowy — sprawdź.")
                    break
    fields = {
        "nip": nip, "name": lead.billing_name or "", "street": lead.billing_street or "",
        "postcode": lead.billing_postcode or "", "city": lead.billing_city or "",
        "country": (lead.billing_country or "LT").upper(), "email": lead.billing_email or ", ".join(lead.emails),
    }
    if nip and not fields["name"] and fields["country"] == "PL":
        info, err = nip_lookup(nip)
        if info:
            fields.update({k: info[k] for k in ("name", "street", "postcode", "city")})
            hints.append("Nazwa i adres z Białej listy VAT (Ministerstwo Finansów).")
        else:
            hints.append(err)
    past = [d for d in days if d <= today]
    try:
        pay_days = max(0, min(120, int(get_setting("jr_payment_days") or 14)))
    except ValueError:
        pay_days = 14
    foreign = fields["country"] != "PL"
    vat = "np" if foreign else str(get_setting("umowa_vat") or "23").split(".")[0]
    fields.update({
        "service": journey.invoice_service_name(lead.robot_model, days, _jjson(j.contract_fields).get("nazwa_wydarzenia", ""),
                                                code=lead_market(lead)),
        "net": f"{lead.value_pln:g}" if lead.value_pln else "",
        "currency": lead.currency or "EUR",
        "vat": vat if vat in INVOICE_TAX_SYMBOLS else "23",
        "invoice_date": today.isoformat(),
        "sale_date": (max(past) if past else (days[-1] if days else today)).isoformat(),
        "payment_date": (today + timedelta(days=pay_days)).isoformat(),
        "ksef": "1" if get_setting("jr_ksef") != "0" else "",
        "send": "1",
    })
    subject, body = journey.invoice_email("{numer}", days, today + timedelta(days=pay_days), owner, code=lead_market(lead))
    if not infakt_key():
        warnings.append("Brak INFAKT_API_KEY w Railway — faktura nie wyjdzie, dopóki nie dodasz klucza API z inFakt.")
    if j.invoice_uuid and not j.invoice_uuid.startswith("task:"):
        warnings.append(f"Faktura {j.invoice_number or ''} jest już wystawiona — drugiej nie wystawisz z CRM (korekty w inFakt).")
    return {"company": lead.company, "fields": fields, "subject": human_text(subject), "body": human_text(body), "hints": hints,
            "warnings": warnings, "tax_symbols": INVOICE_TAX_SYMBOLS, "issued": bool(j.invoice_uuid and not j.invoice_uuid.startswith("task:")),
            "from": f"{owner.get('full_name') or owner['name']}",
            "recipients": email_list(lead.billing_email, *lead.emails)}


def _invoice_form_errors(f):
    if not (f.get("name") or "").strip():
        return "Podaj nazwę nabywcy."
    country = (f.get("country") or M()["country"]).strip().upper()
    if country == "PL" and f.get("nip") and not journey.nip_valid(f.get("nip")):
        return "NIP jest niepoprawny."
    if journey.to_grosze(f.get("net")) is None:
        return "Podaj kwotę netto."
    if f.get("vat") not in INVOICE_TAX_SYMBOLS:
        return "Wybierz stawkę VAT."
    for key, label in (("invoice_date", "wystawienia"), ("sale_date", "sprzedaży"), ("payment_date", "płatności")):
        try:
            date.fromisoformat(f.get(key) or "")
        except ValueError:
            return f"Niepoprawna data {label}."
    if f.get("send") and check_recipients(f.get("email"))[1]:
        return "Podaj e-mail, na który wysłać fakturę (kilka adresów — po przecinku)."
    return None


def journey_issue_invoice(lead_id, f, user_id):
    """Zadanie w tle: faktura w inFakt → (KSeF) → PDF → mail do klienta → dokument i odhaczenie."""
    lead = db.session.get(Lead, lead_id)
    j = lead.journey if lead else None
    if not j:
        return {"error": "nie ma takiego klienta w Customer Journey"}
    if j.invoice_uuid and not j.invoice_uuid.startswith("task:"):
        return {"error": f"Faktura {j.invoice_number} jest już wystawiona — korekty rób w inFakt."}
    _lead_billing_from(lead, f)
    db.session.commit()
    if j.invoice_uuid and j.invoice_uuid.startswith("task:"):
        ref = j.invoice_uuid[5:]  # poprzednia próba nie doczekała się odpowiedzi — dokończ ją, nie wystawiaj drugiej
    else:
        account, bank, err = infakt_bank_account()
        if err:
            return {"error": err}
        country = (f.get("country") or "LT").strip().upper()
        inv = {
            "payment_method": "transfer", "bank_account": account, "bank_name": bank,
            "currency": (f.get("currency") or "EUR").upper()[:3],
            "invoice_date": f["invoice_date"], "sale_date": f["sale_date"], "payment_date": f["payment_date"],
            "client_company_name": f["name"].strip()[:300], "client_tax_code": journey.nip_digits(f.get("nip")) or (f.get("nip") or "").strip(),
            "client_street": (f.get("street") or "").strip(), "client_post_code": (f.get("postcode") or "").strip(),
            "client_city": (f.get("city") or "").strip(), "client_country": country,
            "services": [{"name": (f.get("service") or "Usługa udostępnienia robota humanoidalnego").strip()[:250],
                          "tax_symbol": f["vat"], "unit": "usł.", "quantity": 1,
                          "unit_net_price": journey.to_grosze(f["net"])}],
        }
        if country != "PL":
            inv["sale_type"] = "service"
        res, err = _infakt("POST", "/async/invoices.json", json={"invoice": inv})
        if err:
            return {"error": err}
        ref = (res or {}).get("invoice_task_reference_number")
        if not ref:
            return {"error": f"inFakt nie zwrócił numeru zlecenia: {_infakt_error_text(res)}"}
        j.invoice_uuid = f"task:{ref}"
        db.session.commit()
    uuid = None
    for _ in range(30):
        st, err = _infakt("GET", f"/async/invoices/status/{ref}.json")
        if err:
            return {"error": err}
        code = (st or {}).get("processing_code")
        if code == 201:
            uuid = st.get("invoice_uuid")
            break
        if code and int(code) >= 400:
            j.invoice_uuid = None
            db.session.commit()
            return {"error": "inFakt nie wystawił faktury: " + _infakt_error_text(st)}
        time.sleep(2)
    if not uuid:
        return {"error": "inFakt jeszcze nie potwierdził faktury — kliknij „Wystaw fakturę” za chwilę (CRM dokończy tę samą, nie wystawi drugiej)."}
    inv, err = _infakt("GET", f"/invoices/{uuid}.json")
    number = "" if err else ((inv or {}).get("number") or "")
    j.invoice_uuid, j.invoice_number, j.invoice_at = uuid, number[:64], datetime.utcnow()
    after = _jjson(j.after)
    after["faktura"] = True
    j.after = json.dumps(after)
    _journey_maybe_close(j)
    db.session.commit()
    notes = [f"Faktura {number} wystawiona w inFakt"]
    if f.get("ksef"):
        _, err = _infakt("POST", f"/invoices/{uuid}/send_to_ksef.json")
        notes.append("wysłana do KSeF" if not err else f"KSeF: {err}")
    pdf, err = _infakt("GET", f"/invoices/{uuid}/pdf.json", params={"document_type": "original", "locale": "en"})
    if err or not journey.is_pdf(pdf if isinstance(pdf, bytes) else b""):
        notes.append(f"PDF nie pobrał się ({err or 'pusta odpowiedź'}) — wyślij ją z inFakt")
        _journey_note(lead, "🧾 " + ", ".join(notes), user_id)
        db.session.commit()
        return {"url": f"/crm/leads/{lead.id}#journey", "info": ", ".join(notes)}
    fname = f"Faktura_{journey.slug(number.replace('/', '_')) if number else uuid[:8]}.pdf"
    doc = journey_add_document(lead, "faktura", fname, pdf, "application/pdf", f"inFakt ({number or uuid})", user_id=user_id)
    doc.pdf = pdf
    if f.get("send"):
        owner = market_sender(journey_owner(), lead_market(lead))
        to_addr = f["email"].strip()
        subject = (f.get("subject") or "Faktura {numer} — 33Bots").replace("{numer}", number)[:300]
        body = (f.get("body") or "").replace("{numer}", number)[:20000]
        err = _smtp_send(to_addr, subject, body, owner["name"], attachments=[(fname, pdf, "application/pdf")], sender=owner)
        if err:
            notes.append(f"mail NIE wyszedł ({err}) — PDF jest w dokumentach klienta")
        else:
            notes.append(f"PDF wysłany do {to_addr}")
            if _smtp_send.last_msgid:
                j.msgids = ((j.msgids or "") + " " + _smtp_send.last_msgid).strip()[-4000:]
    _journey_note(lead, "🧾 " + ", ".join(notes), user_id)
    db.session.commit()
    _journey_badge_reset()
    return {"url": f"/crm/leads/{lead.id}#journey", "info": ", ".join(notes)}


@app.route("/crm/nip/<nip>")
def crm_nip_lookup(nip):
    info, err = nip_lookup(nip)
    return jsonify(info if info else {"error": err}), (200 if info else 400)


@app.route("/crm/leads/<int:lead_id>/dane-faktury", methods=["POST"])
def crm_lead_billing(lead_id):
    lead = db.session.get(Lead, lead_id)
    if not lead:
        abort(404)
    _lead_billing_from(lead, request.form)
    db.session.commit()
    return redirect(url_for("crm_lead_detail", lead_id=lead.id) + "#faktura")


@app.route("/crm/journey/<int:lead_id>/faktura", methods=["GET", "POST"])
def crm_journey_invoice(lead_id):
    j, lead = _journey_or_404(lead_id)
    if request.method == "GET":
        return jsonify(journey_invoice_draft(j, lead))
    f = {k: (request.form.get(k) or "").strip() for k in (
        "nip", "name", "street", "postcode", "city", "country", "email", "service", "net", "currency", "vat",
        "invoice_date", "sale_date", "payment_date", "ksef", "send", "subject", "body")}
    f["body"] = (request.form.get("body") or "").replace("\r\n", "\n").strip()
    err = _invoice_form_errors(f)
    if err:
        return jsonify({"error": err}), 400
    if not infakt_key():
        return jsonify({"error": "Brak INFAKT_API_KEY w Railway — dodaj klucz API z inFakt (Ustawienia konta → API)."}), 400
    jid = _start_job("CRM wystawia fakturę", journey_issue_invoice, lead.id, f, get_current_user(request)["id"])
    return jsonify({"jid": jid, "poll": url_for("crm_job", jid=jid, format="json")})


# ---------- ASYSTENT AI (czat z Claude'em, wykonuje polecenia na CRM) ----------

ASSISTANT_MODEL = "claude-opus-5"
ASSISTANT_MAX_TURNS = 8

ASSISTANT_TOOLS = [
    {
        "name": "find_leads",
        "description": "Szuka leadów po nazwie firmy, osobie kontaktowej, telefonie, mailu, lokalizacji lub notatce. Zwraca skróty (id, firma, etap, wartość, przypisany). Zawsze szukaj leada zanim go zmienisz — nie zgaduj ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Fragment nazwy/osoby/telefonu/maila/miasta"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_lead",
        "description": "Pełne dane jednego leada: kontakt, etap, wartość, dni wynajmu, zadania, komentarze.",
        "input_schema": {
            "type": "object",
            "properties": {"lead_id": {"type": "integer"}},
            "required": ["lead_id"],
        },
    },
    {
        "name": "create_lead",
        "description": "Tworzy nowego leada. Wymagana tylko nazwa firmy — resztę podawaj, jeśli użytkownik ją określił. "
                       "Gdy klient z tym samym telefonem/e-mailem był dodany w ostatnich 30 dniach, zwraca błąd z tym leadem "
                       "(zaktualizuj go zamiast dublować); allow_duplicate=true tylko dla naprawdę osobnego zlecenia.",
        "input_schema": {
            "type": "object",
            "properties": {
                "allow_duplicate": {"type": "boolean", "description": "true = to osobne zlecenie tego samego klienta"},
                "market": {"type": "string", "enum": ["lt", "de"],
                           "description": "Rynek leada: lt = Litwa, de = Niemcy (bez podania: z telefonu/maila, "
                                          "inaczej rynek oglądany w CRM)"},
                "company": {"type": "string"},
                "contact_name": {"type": "string"},
                "contact_phone": {"type": "string"},
                "contact_email": {"type": "string"},
                "contact_email2": {"type": "string", "description": "Drugi e-mail klienta (opcjonalnie)"},
                "nip": {"type": "string", "description": "NIP klienta (do faktury)"},
                "stage": {"type": "string", "enum": STAGES},
                "robot_model": {"type": "string", "enum": ROBOT_MODELS},
                "value": {"type": "number", "description": "Wartość wynajmu (liczba)"},
                "currency": {"type": "string", "enum": CURRENCIES},
                "quality": {"type": "string", "enum": QUALITY_LEVELS},
                "location": {"type": "string"},
                "source": {"type": "string"},
                "notes": {"type": "string"},
                "assigned_to": {"type": "string", "description": "Imię użytkownika CRM (Oliwier/Bartek/Jerzy)"},
                "event_dates": {"type": "array", "items": {"type": "string"}, "description": "Konkretne dni wynajmu, format YYYY-MM-DD (mogą być niekolejne)"},
                "event_locations": {"type": "object", "description": "Opcjonalnie inna lokalizacja na konkretny dzień: {\"YYYY-MM-DD\": \"Miasto\"}", "additionalProperties": {"type": "string"}},
                "closed_on": {"type": "string", "description": "Data zamknięcia YYYY-MM-DD (kiedy lead faktycznie został wygrany/stracony) — od niej zależy miesiąc w Statystykach. Tylko dla etapu Wygrany/Stracony; podaj, gdy lead trafia do CRM później niż był zamknięty."},
                "client_type": {"type": "string", "enum": list(sales_rules.CLIENT_TYPES), "description": "Typ klienta: firma (firma/instytucja), prywatna (osoba prywatna), agencja (agencja eventowa), partner (firma robotyczna/partner). Wymagany przed wysłaniem oferty i przy Wygrany."},
                "country": {"type": "string", "enum": list(sales_rules.COUNTRIES), "description": "Kraj klienta / wydarzenia (PL, LT, DE, RO…) — panel skuteczności; pusty = podpowiedź z danych"},
                "lost_reason": {"type": "string", "enum": list(sales_rules.LOST_REASONS), "description": "WYMAGANY przy stage=Stracony: cena, konkurencja (inny dostawca), brak_decyzji, brak_kontaktu, odrzucony (słaby klient), termin (zajęty / brak robota), inne (+ lost_comment)"},
                "lost_comment": {"type": "string", "description": "Komentarz do powodu straty (wymagany przy lost_reason=inne)"},
                "win_confirmation": {"type": "string", "enum": list(sales_rules.WIN_CONFIRMATIONS), "description": "WYMAGANE przy stage=Wygrany (chyba że contract_signed): umowa = umowa podpisana, mail = pisemne potwierdzenie mailem. Wygrany wymaga też co najmniej jednego dnia wynajmu (od 2025) i wartości > 0."},
                "confirm_warning": {"type": "boolean", "description": "true = sprawdzono kwotę: wartość leada inna niż ostatnia wysłana oferta (ostrzeżenie przy Wygrany)"},
            },
            "required": ["company"],
        },
    },
    {
        "name": "update_lead",
        "description": "Zmienia istniejącego leada. Podawaj TYLKO pola do zmiany — reszta zostaje bez zmian. event_dates (jeśli podane) ZASTĘPUJE cały zestaw dni wynajmu. "
                       "Bramki etapów: Stracony wymaga lost_reason; Wygrany wymaga win_confirmation (umowa/mail), client_type, dnia wynajmu od 2025 i wartości > 0; "
                       "Oferta (z Nowy) wymaga client_type. Bez nich zwraca błąd po polsku i nic nie zmienia.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "integer"},
                "company": {"type": "string"},
                "contact_name": {"type": "string"},
                "contact_phone": {"type": "string"},
                "contact_email": {"type": "string"},
                "contact_email2": {"type": "string", "description": "Drugi e-mail klienta (opcjonalnie)"},
                "nip": {"type": "string", "description": "NIP klienta (do faktury)"},
                "stage": {"type": "string", "enum": STAGES},
                "robot_model": {"type": "string", "enum": ROBOT_MODELS},
                "value": {"type": "number"},
                "currency": {"type": "string", "enum": CURRENCIES},
                "quality": {"type": "string", "enum": QUALITY_LEVELS},
                "location": {"type": "string"},
                "source": {"type": "string"},
                "notes": {"type": "string"},
                "assigned_to": {"type": "string"},
                "contract_signed": {"type": "boolean"},
                "event_dates": {"type": "array", "items": {"type": "string"}},
                "event_locations": {"type": "object", "additionalProperties": {"type": "string"}},
                "closed_on": {"type": "string", "description": "Data zamknięcia YYYY-MM-DD (kiedy lead faktycznie został wygrany/stracony) — od niej zależy miesiąc w Statystykach. Tylko dla etapu Wygrany/Stracony; podaj, gdy lead trafia do CRM później niż był zamknięty."},
                "client_type": {"type": "string", "enum": list(sales_rules.CLIENT_TYPES), "description": "Typ klienta: firma (firma/instytucja), prywatna (osoba prywatna), agencja (agencja eventowa), partner (firma robotyczna/partner). Wymagany przed wysłaniem oferty i przy Wygrany."},
                "country": {"type": "string", "enum": list(sales_rules.COUNTRIES), "description": "Kraj klienta / wydarzenia (PL, LT, DE, RO…) — panel skuteczności; pusty = podpowiedź z danych"},
                "lost_reason": {"type": "string", "enum": list(sales_rules.LOST_REASONS), "description": "WYMAGANY przy stage=Stracony: cena, konkurencja (inny dostawca), brak_decyzji, brak_kontaktu, odrzucony (słaby klient), termin (zajęty / brak robota), inne (+ lost_comment)"},
                "lost_comment": {"type": "string", "description": "Komentarz do powodu straty (wymagany przy lost_reason=inne)"},
                "win_confirmation": {"type": "string", "enum": list(sales_rules.WIN_CONFIRMATIONS), "description": "WYMAGANE przy stage=Wygrany (chyba że contract_signed): umowa = umowa podpisana, mail = pisemne potwierdzenie mailem. Wygrany wymaga też co najmniej jednego dnia wynajmu (od 2025) i wartości > 0."},
                "confirm_warning": {"type": "boolean", "description": "true = sprawdzono kwotę: wartość leada inna niż ostatnia wysłana oferta (ostrzeżenie przy Wygrany)"},
            },
            "required": ["lead_id"],
        },
    },
    {
        "name": "add_task",
        "description": "Dodaje zadanie. Z lead_id — zadanie przy leadzie; bez lead_id — zadanie własne (niezwiązane z firmą).",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "integer"},
                "kind": {"type": "string", "enum": [k for k in ACTIVITY_KINDS if k != "note"], "description": "call = telefon, sms, email, online, meeting, task = inne zadanie (np. zlecone komuś)"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "notes": {"type": "string"},
                "assigned_to": {"type": "string", "description": "Imię użytkownika CRM"},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "list_tasks",
        "description": "Lista otwartych zadań, opcjonalnie tylko jednej osoby.",
        "input_schema": {
            "type": "object",
            "properties": {"assigned_to": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "complete_task",
        "description": "Oznacza zadanie jako zrobione. Telefon / SMS / mail przy leadzie WYMAGA outcome (wynik kontaktu): "
                       "telefon: odebral | nie_odebral; SMS i mail: odpisal | bez_odpowiedzi — liczą się do limitu 3 nieudanych prób. "
                       "Telefon potwierdzający po ofercie (odebrany) wymaga odpowiedzi: dotarla, decyzja_kiedy, decyduje, porownanie "
                       "— zapisują się w leadzie.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "outcome": {"type": "string", "enum": list(sales_rules.OUTCOMES)},
                "dotarla": {"type": "string", "description": "Telefon potwierdzający: czy oferta dotarła?"},
                "decyzja_kiedy": {"type": "string", "description": "Telefon potwierdzający: kiedy zapadnie decyzja?"},
                "decyduje": {"type": "string", "description": "Telefon potwierdzający: kto decyduje?"},
                "porownanie": {"type": "string", "description": "Telefon potwierdzający: z kim nas porównują?"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "add_comment",
        "description": "Dodaje komentarz do leada.",
        "input_schema": {
            "type": "object",
            "properties": {"lead_id": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["lead_id", "text"],
        },
    },
    {
        "name": "send_email",
        "description": "Wysyła PRAWDZIWEGO e-maila ze skrzynki firmowej (podpisanego imieniem rozmówcy). Jeśli mail dotyczy leada, podaj lead_id — wysyłka zapisze się w jego komentarzach.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Adres e-mail odbiorcy"},
                "subject": {"type": "string", "description": "Temat maila"},
                "body": {"type": "string", "description": "Treść maila (zwykły tekst, z podpisem). Do klienta ZAWSZE w języku rynku CRM "
                                                           "(LT CRM — po litewsku, DE CRM — po niemiecku); po polsku tylko do zespołu 33bots"},
                "lead_id": {"type": "integer", "description": "Opcjonalnie: lead, którego dotyczy mail"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "prepare_offer",
        "description": "Przygotowuje ofertę PDF w firmowym layoucie 33bots razem z mailem do klienta i zapisuje ją w CRM "
                       "jako SZKIC DO SPRAWDZENIA (zakładka Oferty). Nic nie wysyła — użytkownik ogląda PDF i mail w CRM, "
                       "może poprawić treść maila i sam klika „Wyślij”. Wtedy mail z PDF w załączniku wychodzi ze skrzynki "
                       "nadawcy, kopia trafia do Wysłanych, lead przechodzi na etap Oferta i dostaje wartość. CRM NIE dodaje "
                       "zadań sam — następny krok (np. telefon po ofercie) ustawia człowiek: zapytaj, co i kiedy, i dodaj "
                       "go add_task. Zwraca link do podglądu — podaj go użytkownikowi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Adres e-mail klienta"},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Treść maila (zwykły tekst, z podpisem nadawcy)"},
                "lead_id": {"type": "integer", "description": "Lead, którego dotyczy oferta"},
                "sender": {"type": "string", "description": "Kto wysyła i jest kontaktem w ofercie: Oliwier albo Bartek (domyślnie rozmówca)"},
                "value": {"type": "number", "description": "Opcjonalnie: wartość netto, która po wysyłce trafi do leada"},
                "followup_days": {"type": "integer", "description": "Używane tylko przy włączonych automatach zadań (AUTO_TASKS=1): za ile dni po wysyłce zadanie follow-up"},
                "offer": {
                    "type": "object",
                    "description": "Treść PDF. Kontakt (imię, e-mail, telefon) dopisuje się sam z nadawcy. "
                                   "Limity długości są twarde (tekst musi zmieścić się w layoucie) — za długie pole odrzuca całą ofertę.",
                    "properties": {
                        "client": {"type": "string", "maxLength": 80, "description": "Oficjalna, krótka nazwa klienta (max 80 znaków)"},
                        "headline": {"type": "string", "maxLength": 40, "description": "Tytuł okładki, domyślnie 'Robot humanoidalny' (max 40 znaków)"},
                        "subtitle": {"type": "string", "maxLength": 220, "description": "Zdanie pod tytułem okładki (max 220 znaków)"},
                        "cover": {"type": "array", "minItems": 1, "maxItems": 4,
                                  "description": "3 pola ramki okładki, np. Przygotowano dla / Termin / Godziny (4. = Kontakt dopisze się sam)",
                                  "items": {"type": "object", "properties": {
                                      "label": {"type": "string", "maxLength": 22, "description": "max 22 znaki"},
                                      "value": {"type": "string", "maxLength": 34, "description": "max 34 znaki"}}}},
                        "intro": {"type": "string", "maxLength": 600, "description": "Akapit na stronie 2: co robi robot + doświadczenie (max 600 znaków)"},
                        "cards": {"type": "array", "minItems": 2, "maxItems": 2,
                                  "description": "Dokładnie 2 karty, np. ROLA ROBOTA i PRZYGOTOWANIE",
                                  "items": {"type": "object", "properties": {
                                      "label": {"type": "string", "maxLength": 24, "description": "max 24 znaki"},
                                      "title": {"type": "string", "maxLength": 32, "description": "max 32 znaki"},
                                      "items": {"type": "array", "minItems": 1, "maxItems": 4,
                                                "description": "1–4 krótkie punkty, KAŻDY max 90 znaków",
                                                "items": {"type": "string", "maxLength": 90}}}}},
                        "price_title": {"type": "string", "maxLength": 60, "description": "np. 'Cały dzień obecności robota' (max 60 znaków)"},
                        "price_sub": {"type": "string", "maxLength": 90, "description": "np. 'Częstochowa · 6.11.2026' (max 90 znaków)"},
                        "price": {"type": "string", "maxLength": 20, "description": "np. '1 300 €' (netto, max 20 znaków)"},
                        "included": {"type": "string", "maxLength": 300, "description": "Opcjonalnie: zdanie 'W cenie: ...' (max 300 znaków)"},
                        "note": {"type": "string", "maxLength": 160, "description": "Opcjonalnie: tekst w ramce kontaktu (max 160 znaków)"},
                        "section_title": {"type": "string", "maxLength": 40, "description": "max 40 znaków"},
                        "language": {"type": "string", "description": "Język oferty — zawsze język rynku CRM (ustawia się sam)"},
                    },
                    "required": ["client", "intro", "cards", "price_title", "price", "cover"],
                },
            },
            "required": ["to", "subject", "body", "offer"],
        },
    },
    {
        "name": "monthly_revenue",
        "description": "Przychód (wartość realizacji) wg miesięcy: wygrane leady przypisane do miesiąca, w którym odbywa się wynajem (pierwszy dzień) — łącznie z przyszłymi, już zabukowanymi. Użyj do pytań typu 'ile zarobimy w październiku', 'porównaj wrzesień z październikiem', 'ile już zrealizowaliśmy, a ile zostało do realizacji'. Każdy miesiąc ma podział: realized (etap Zrealizowany — wynajem już się odbył) i to_realize (etap Wygrany — jeszcze przed nami); totals = suma całości. Zwraca też wygrane bez ustawionej daty wynajmu (nieprzypisane do żadnego miesiąca).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "quote_price",
        "description": f"Kalkulator wyceny z cennika 33bots (EUR): 8 h na miejscu {pricing.BASE_8H} € netto, 4 h = 6/8, 2 h = 1/2, "
                       f"plus dojazd {pricing.KM_RATE:.2f} €/km w jedną stronę × 2 liczony od granicy PL–LT, nocleg zawsze {pricing.LODGING_COST} € za każdy dzień; każda godzina "
                       "po 22:00 × 1,5; dojazd z noclegiem max 30% ceny (inaczej cena rośnie); 2 dni −10%, "
                       "3+ dni −15% od całości. "
                       "Liczy od granicy PL–LT (Budzisko). Podaj miejsce (miasto/adres) albo km od granicy. "
                       "Używaj zawsze, gdy trzeba wycenić wynajem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "destination": {"type": "string", "description": "Miejsce wydarzenia, np. 'Gdańsk' albo adres"},
                "km": {"type": "number", "description": "Zamiast miejsca: odległość w jedną stronę w km"},
                "hours": {"type": "integer", "enum": sorted(pricing.HOUR_FACTORS), "description": "Czas na miejscu (domyślnie 8)"},
                "departure": {"type": "string", "enum": list(pricing.DEPARTURES.values()), "description": "Skąd wyjazd (domyślnie oba warianty)"},
                "start": {"type": "string", "description": "Godzina rozpoczęcia HH:MM — godziny po 22:00 liczą się × 1,5"},
                "night_hours": {"type": "number", "description": "Zamiast start: ile godzin wypada po 22:00"},
                "days": {"type": "integer", "description": "Ile dni z rzędu (domyślnie 1); 2 dni −10%, 3+ −15%"},
            },
            "required": [],
        },
    },
    {
        "name": "search_mail",
        "description": "Szuka w mailach, które CRM przeczytał ze skrzynki kontakt@ (zakładka Skrzynka): po słowach z tematu, "
                       "treści albo adresu nadawcy. Zwraca listę (data, od, temat, fragment, powiązany lead). Podaj message_id, "
                       "żeby dostać pełną treść jednego maila. Użyj, gdy trzeba ustalić, skąd jest lead albo co klient napisał. "
                       "Uwaga: są tu tylko maile z kontakt@ od czasu włączenia Skrzynki — nie prywatne skrzynki osób.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Słowa do znalezienia (wszystkie muszą wystąpić), np. 'LCK Lublin'"},
                "days": {"type": "integer", "description": "Tylko maile z ostatnich N dni (domyślnie wszystkie)"},
                "message_id": {"type": "integer", "description": "Pełna treść tego maila (id z wyników)"},
            },
            "required": [],
        },
    },
    {
        "name": "team_reports",
        "description": "Raporty dnia i historia pracy osób z zespołu (rangi: Zarząd, Kierownik, Pracownik, Wykonawca). Dla "
                       "każdego dnia: stan raportu (w terminie / spóźniony / brak / dzień wolny), liczby zebrane przez CRM "
                       "(telefony odebrane i nieodebrane, maile, oferty, obsłużone leady, odhaczone zadania, wygrane, czas "
                       "aktywności), zlecone zadania zrobione i niezrobione z powodami, treść raportu (co zrobione, blokery, "
                       "plan). Do tego podsumowanie okresu i średnie wg dnia tygodnia (by_weekday) — do pytań typu „w które "
                       "dni jest najmniej efektywny”, „ile zleconych zadań robi”, „czy raportuje w terminie”. Tylko odczyt; "
                       "zwraca osoby, których raporty pytający może widzieć.",
        "input_schema": {"type": "object", "properties": {
            "person": {"type": "string", "description": "Imię osoby, np. Jerzy (domyślnie wszyscy z obowiązkiem raportu)"},
            "days": {"type": "integer", "description": "Ile ostatnich dni (7–370, domyślnie 60)"}}, "required": []},
    },
    {
        "name": "crm_health",
        "description": "Strażnik CRM: czy wszystko działa. Stan każdego pracownika w tle (poczta każdej skrzynki, import "
                       "z ClickUp, przenoszenie leadów z CRM PL i wygranych do PL, Customer Journey, przegląd poczty, "
                       "automaty), alarmy podzielone na needs_human (tylko to, czego bez człowieka się nie da: klucz, "
                       "hasło, ustawienie; z diagnozą) i auto_fixing (CRM naprawia sam albo błąd w kodzie do naprawy "
                       "przez Claude'a: how=code — popraw kod i wdroż), cisza w leadach LT / DE, historia. Tylko odczyt. "
                       "refresh=true najpierw robi świeże sprawdzenie (bez diagnozy).",
        "input_schema": {"type": "object", "properties": {"refresh": {"type": "boolean"}}, "required": []},
    },
    {
        "name": "sales_performance",
        "description": "Panel skuteczności sprzedaży (zakładka Skuteczność): wartość wygranych wg miesiąca ZAMKNIĘCIA, osobno dla "
                       "Polski, Niemiec, Litwy i Rumunii, na tle celów (PL 150 tys. zł realnie / 200 tys. ambitnie, DE 30 tys. EUR, "
                       "LT 15 tys. EUR) z prognozą na koniec miesiąca; skuteczność wg typu klienta i opiekuna, powody strat, mediana "
                       "czasu reakcji, anulacje po wygranej i otwarte leady od 10 000 zł z datą następnego kroku. Łączy CRM PL i LT/DE.",
        "input_schema": {"type": "object", "properties": {
            "period_days": {"type": "integer", "description": "Okres dla skuteczności, powodów strat i czasu reakcji (domyślnie 90)"}},
            "required": []},
    },
    {
        "name": "offer_audit",
        "description": "Audyt ofert (Raporty → Audyt ofert): aktywne leady (Nowy/Oferta), do których w ostatnich 45 dniach poszła "
                       "oferta, z informacją, czy mają zaplanowany drugi krok (w PL telefon, w LT/DE follow-up mailem), oraz "
                       "raport ostatniego audytu (co zostało dodane). Tylko odczyt.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "closed_leads",
        "description": "Leady zamknięte (Wygrany/Stracony) w danym miesiącu — jak w tabeli 'Ostatnie 12 miesięcy' w Statystykach: "
                       "wg OBECNEGO etapu leada i daty, kiedy na niego trafił (lead wygrany, a potem oznaczony jako Stracony, "
                       "jest tylko stracony). changed_after_win = wygrane w tym miesiącu, które później zmieniły etap. "
                       "Użyj do pytań typu 'co zamknęliśmy w sierpniu', 'ile wygraliśmy w lipcu'.",
        "input_schema": {
            "type": "object",
            "properties": {"month": {"type": "string", "description": "Miesiąc w formacie YYYY-MM, np. 2026-08"}},
            "required": ["month"],
        },
    },
    {
        "name": "logistics_plan",
        "description": "Plan logistyki realizacji (zakładka Logistyka): kto (operator) z którym robotem jedzie na który dzień realizacji, "
                       "szacowane trasy i km operatorów, problemy (brak operatora, operator/robot w dwóch miejscach naraz, za mało robotów) "
                       "i propozycje zmian skracających trasy. Użyj do pytań typu 'kto jutro jedzie gdzie', 'czy mamy konflikty w październiku'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD, domyślnie dziś"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD, domyślnie 13 dni po date_from"},
                "include_tentative": {"type": "boolean", "description": "Uwzględnij też leady na etapie Oferta"},
            },
            "required": [],
        },
    },
    {
        "name": "assign_crew",
        "description": "Przypisuje operatora i/lub robota do dnia realizacji leada (tworzy albo zmienia ekipę). "
                       "Operatorów i roboty dodaje się w zakładce Logistyka — tu podajesz ich nazwy. "
                       "Ekipa podwójna (2 roboty i 2 operatorów w jednym miejscu, np. 2× G1): double=true "
                       "i second_operator / second_robot. "
                       "Gdy danego dnia jest już kilka ekip, podaj crew_id (z logistics_plan) albo new_crew=true.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "integer"},
                "date": {"type": "string", "description": "Dzień realizacji YYYY-MM-DD"},
                "operator": {"type": "string", "description": "Imię operatora"},
                "robot": {"type": "string", "description": "Nazwa robota z floty, np. 'G1 #1'"},
                "double": {"type": "boolean", "description": "true = ekipa podwójna (2 roboty, 2 operatorów), false = zwykła"},
                "second_operator": {"type": "string", "description": "Imię 2. operatora (ekipa podwójna)"},
                "second_robot": {"type": "string", "description": "Nazwa 2. robota (ekipa podwójna)"},
                "city": {"type": "string", "description": "Miasto, jeśli ta ekipa jedzie gdzie indziej niż lokalizacja leada"},
                "notes": {"type": "string"},
                "crew_id": {"type": "integer", "description": "Konkretna ekipa do zmiany"},
                "new_crew": {"type": "boolean", "description": "Dodaj kolejną ekipę tego dnia (równoległa lokalizacja)"},
            },
            "required": ["lead_id", "date"],
        },
    },
]


_include("poczta_wyslane")




# Teksty do klientów (maile, szkice, oferty, wiadomości na komunikatory) bez „AI-owej” typografii: długie myślniki
# (—, –), wielokropek jako jeden znak, strzałki, punktory, ozdobne cudzysłowy. Zostaje zwykły krótki „-”.
_AI_TYPO = str.maketrans({"—": "-", "–": "-", "―": "-", "‒": "-", "−": "-", "•": "-", "▪": "-", "●": "-", "→": "->",
                          "⇒": "->", "➜": "->", "➔": "->", "…": "...", "„": '"', "”": '"', "“": '"', "«": '"', "»": '"',
                          "‘": "'", "’": "'", "\u00a0": " ", "\u202f": " ", "\u2009": " "})
HUMAN_TYPO_RULE = ("Pisz jak człowiek w zwykłym mailu, w każdym języku (także po litewsku i niemiecku): NIGDY nie używaj "
                   "długich myślników (— ani –), wielokropka jako jednego znaku, strzałek, punktorów • ani ozdobników. "
                   "Zamiast myślnika postaw przecinek, dwukropek albo kropkę; zwykły krótki łącznik „-” jest w porządku. "
                   "Cudzysłowy zwykłe \"…\".")


def human_text(text):
    """Tekst do klienta (mail, szkic, oferta) bez typowych dla AI znaków — patrz _AI_TYPO."""
    if not isinstance(text, str) or not text:
        return text
    return text.translate(_AI_TYPO)


def human_deep(value):
    """human_text na wszystkich tekstach w dict / liście (np. pola oferty PDF)."""
    if isinstance(value, str):
        return human_text(value)
    if isinstance(value, dict):
        return {k: human_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [human_deep(v) for v in value]
    return value


def _smtp_send(to_addr, subject, body, sender_name, attachments=None, sender=None, headers=None, cc=None):
    """Wysyła jeden mail przez SMTP (te same zmienne co raport dzienny + skrzynki osób).
    attachments: lista (nazwa_pliku, bajty, mime) — np. PDF oferty.
    sender: użytkownik z USERS — mail wychodzi z jego skrzynki, jeśli jest skonfigurowana.
    Zwraca None przy sukcesie albo opis problemu (ostrzeżenie o kopii w Wysłanych trafia do
    _smtp_send.last_warning)."""
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.utils import formataddr, formatdate, make_msgid
    _smtp_send.last_warning = _smtp_send.last_msgid = None
    subject, body = human_text(subject), human_text(body)  # do klienta nigdy nie idą „AI-owe” myślniki
    skey = re.sub(r"[^A-Z0-9]", "_", (sender or {}).get("name", "").upper())
    host = (os.environ.get(f"SMTP_HOST_{skey}", "").strip() if skey else "") or os.environ.get("SMTP_HOST", "").strip()
    login, pwd = _mailbox_for(sender)
    if not (host and login and pwd):
        return "SMTP nieskonfigurowany — ustaw SMTP_HOST oraz SMTP_PASS_<IMIĘ> (albo SMTP_USER/SMTP_PASS) w Railway (web → Variables)"
    port = int((os.environ.get(f"SMTP_PORT_{skey}", "").strip() if skey else "") or os.environ.get("SMTP_PORT", 587))
    display = (sender or {}).get("full_name") or sender_name
    body = strip_pl_phone(body, dict(sender or {}, email=(sender or {}).get("email") or login))
    if attachments:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body, "plain", "utf-8"))
        for fname, data, mime in attachments:
            maintype, _, subtype = (mime or "application/octet-stream").partition("/")
            part = MIMEApplication(data, _subtype=subtype or "octet-stream")
            part.add_header("Content-Disposition", "attachment", filename=fname)
            msg.attach(part)
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((f"{display} | 33bots", login))
    recipients = email_list(*(to_addr if isinstance(to_addr, (list, tuple)) else [to_addr]))
    if not recipients:
        return f"niepoprawny adres odbiorcy: {to_addr or '(pusty)'}"
    msg["To"] = ", ".join(recipients)
    cc = [a for a in email_list(*(cc or [])) if a not in recipients]
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=login.split("@")[-1])
    _smtp_send.last_msgid = msg["Message-ID"]  # Customer Journey rozpoznaje po nim odpowiedzi klienta
    for hk, hv in (headers or {}).items():
        msg[hk] = hv.replace("{login}", login)
    raw = msg.as_bytes()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as s:
                s.login(login, pwd)
                s.sendmail(login, recipients + cc, raw)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls()
                s.login(login, pwd)
                s.sendmail(login, recipients + cc, raw)
    except (smtplib.SMTPException, OSError) as e:
        return f"wysyłka nie powiodła się: {type(e).__name__}"
    _smtp_send.last_warning = _imap_save_sent(
        raw, login, pwd, host=(os.environ.get(f"IMAP_HOST_{skey}", "").strip() or
                                os.environ.get(f"SMTP_HOST_{skey}", "").strip()) if skey else None)
    return None


_smtp_send.last_warning = _smtp_send.last_msgid = None


_include("oferty_pomocnicze")


def deliver_offer(offer, actor):
    """Wysyła szkic oferty (mail + PDF) ze skrzynki nadawcy i aktualizuje leada.
    Zwraca (błąd albo None, ostrzeżenie albo None, lista zmian w leadzie)."""
    if offer.sent_at:
        return "ta oferta została już wysłana", None, []
    type_err = offer_client_type_error(db.session.get(Lead, offer.lead_id) if offer.lead_id else None)
    if type_err:
        return type_err, None, []
    sender = market_sender(USERS_BY_ID.get(offer.sender_id) or actor,
                           lead_market(db.session.get(Lead, offer.lead_id) if offer.lead_id else None))
    err = _smtp_send(offer.to_addr, offer.subject, offer.body, sender["name"],
                     attachments=[(offer.filename, offer.pdf, "application/pdf")], sender=sender)
    if err:
        return err, None, []
    warning = _smtp_send.last_warning
    offer.sent_at = datetime.utcnow()
    opts = _offer_options(offer)
    changes = []
    lead = offer.lead
    if lead:
        db.session.add(Activity(
            lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(), assigned_to=actor["id"],
            notes=f"📄 Oferta {offer.price} netto wysłana do {offer.to_addr} (od: {sender['full_name']})\n"
                  f"PDF: {_offer_url(offer)}\nTemat: {offer.subject}\n---\n{offer.body}",
        ))
        if lead.stage == "Nowy" and record_stage_change(lead, "Oferta", actor["id"]):
            changes.append("etap → Oferta")
        call = ensure_followup_call(lead) if lead.stage in ACTIVE_STAGES else None
        if call:
            changes.append(f"telefon {call.due_date:%d.%m} ({USERS_BY_ID.get(call.assigned_to, {}).get('name', '?')})")
        value, currency = offer_value(offer)
        if value is not None:
            lead.value_pln = value
            if currency:
                lead.currency = currency
            changes.append(f"wartość {value:g} {currency or lead.currency or 'EUR'}")
            if lead.stage in ACTIVE_STAGES:  # telefon potwierdzający zależy od kwoty oferty — ten sam, nie drugi
                ensure_followup_call(lead)
        if not lead.contact_email:
            lead.contact_email = offer.to_addr
        days = opts.get("followup_days", 3)
        if days and auto_tasks_on():  # bez automatów następny krok po ofercie ustawia człowiek
            due = datetime.combine(date.today() + timedelta(days=int(days)), datetime.min.time())
            db.session.add(Activity(lead_id=lead.id, kind="email", due_date=due, assigned_to=sender["id"],
                                    notes=f"Follow-up oferty ({offer.price} netto) wysłanej {date.today():%d.%m}"))
            changes.append(f"follow-up {due:%d.%m}")
    db.session.commit()
    return None, warning, changes


def _assistant_resolve_user(val, fallback):
    """Imię (Oliwier/Bartek/Jerzy) albo id -> id użytkownika CRM."""
    if val is None or val == "":
        return fallback
    try:
        uid = int(val)
        return uid if uid in USERS_BY_ID else fallback
    except (TypeError, ValueError):
        pass
    name = str(val).strip().lower()
    for u in USERS:
        if u["name"].lower() == name:
            return u["id"]
    return fallback


def _assistant_lead_brief(lead):
    au = USERS_BY_ID.get(lead.assigned_to, {})
    return {
        "id": lead.id, "company": lead.company, "stage": lead.stage,
        "contact_name": lead.contact_name or "", "contact_phone": lead.contact_phone or "",
        "value": lead.value_pln, "currency": lead.currency or "EUR",
        "location": lead.location or "", "assigned_to": au.get("name", ""),
        "event_days": [ed.event_date.isoformat() for ed in lead.event_days],
        "client_type": lead.client_type or None, "country": lead_country(lead),
        **({"lost_reason": lead.lost_reason, "lost_comment": lead.lost_comment} if lead.stage == "Stracony" else {}),
    }


_include("asystent_pola")


def _assistant_execute(name, inp, user):
    """Wykonuje narzędzie asystenta. Zwraca (wynik-json-string, opis-akcji-albo-None)."""
    import json as _json
    if name == "team_reports":
        return _json.dumps(team_tool(inp, user), ensure_ascii=False, default=str), None
    if name == "crm_health":
        if inp.get("refresh"):
            health_check_pass(diag=False, fix=False)
        r = health_report()
        return _json.dumps({"all_ok": not r["alarms"], "checked_at_local": r["checked_text"],
                            "watchdog_running": not r["watch_stale"], "needs_human": r["alarms"],
                            "auto_fixing": r["auto"], "jobs": r["jobs"], "history": r["log"]},
                           ensure_ascii=False, default=str), None
    if name == "find_leads":
        q = str(inp.get("query", "")).strip()
        if not q:
            return _json.dumps({"error": "pusty query"}), None
        like = f"%{q}%"
        leads = Lead.query.filter(db.or_(
            Lead.company.ilike(like), Lead.contact_name.ilike(like),
            Lead.contact_phone.ilike(like), Lead.contact_email.ilike(like), Lead.contact_email2.ilike(like),
            Lead.location.ilike(like), Lead.notes.ilike(like),
        )).order_by(Lead.created_at.desc()).limit(20).all()
        return _json.dumps({"leads": [_assistant_lead_brief(l) for l in leads]}, ensure_ascii=False), None

    if name == "get_lead":
        lead = db.session.get(Lead, int(inp.get("lead_id", 0) or 0))
        if not lead:
            return _json.dumps({"error": "nie ma leada o tym id"}), None
        data = _assistant_lead_brief(lead)
        data.update({
            "contact_email": lead.contact_email or "", "contact_email2": lead.contact_email2 or "",
            "robot_model": lead.robot_model or "",
            "quality": lead.quality or "Średni", "source": lead.source or "",
            "nip": lead.nip or "", "dane_do_faktury": ", ".join(x for x in (lead.billing_name, lead.billing_street, " ".join(y for y in (lead.billing_postcode, lead.billing_city) if y)) if x),
            "notes": lead.notes or "", "contract_signed": bool(lead.contract_signed),
            "rental_days": lead.rental_days,
            "client_type_hint": client_type_hint(lead)[0], "win_confirmation": lead.win_confirmation,
            "failed_contact_attempts": lead_failed_attempts(lead),
            "response_minutes": lead_response_minutes(lead),
            "confirm_call_answers": json.loads(lead.call_answers) if lead.call_answers else None,
            **({"closed_on": f"{lead_closed_on(lead):%Y-%m-%d}" if lead_closed_on(lead) else None}
               if lead.stage in CLOSED_STAGES else {}),
            "event_days": [
                {"date": ed.event_date.isoformat(), "location": ed.location or (lead.location or "")}
                for ed in lead.event_days
            ],
            "tasks": [
                {"id": a.id, "kind": a.kind, "done": a.done,
                 "due": a.due_date.strftime("%Y-%m-%d") if a.due_date else None,
                 "assigned_to": USERS_BY_ID.get(a.assigned_to, {}).get("name", ""),
                 "notes": a.notes or ""}
                for a in lead.activities if a.kind != "note"
            ],
            "comments": [
                {"author": USERS_BY_ID.get(a.assigned_to, {}).get("name", ""), "text": a.notes or ""}
                for a in lead.activities if a.kind == "note"
            ],
        })
        if lead.journey and lead.stage in JOURNEY_STAGES:
            data["customer_journey"] = journey_brief(lead.journey, lead)
        return _json.dumps(data, ensure_ascii=False), None

    if name == "create_lead":
        company = str(inp.get("company", "")).strip()
        if not company:
            return _json.dumps({"error": "brak nazwy firmy"}), None
        if not inp.get("allow_duplicate"):
            same = find_recent_duplicate(inp.get("contact_email"), inp.get("contact_phone"))
            if same:
                return _json.dumps({
                    "error": f"ten klient już jest w CRM (dodany w ostatnich {DUP_WINDOW_DAYS} dniach) — zaktualizuj "
                             "istniejącego leada (update_lead / add_comment) zamiast dodawać nowego; jeśli to naprawdę "
                             "osobne zlecenie, wywołaj create_lead z allow_duplicate=true",
                    "existing": _assistant_lead_brief(same),
                }, ensure_ascii=False), None
        mcode = str(inp.get("market") or "").lower()
        if mcode not in markets.MARKETS:  # rynek: podany → z telefonu / maila / kraju → oglądany w CRM
            mcode = markets.detect(inp.get("contact_phone"), [inp.get("contact_email")],
                                   inp.get("billing_country"), inp.get("location")) or current_market()
        lead = Lead(company=company, stage="Nowy", assigned_to=user["id"], currency="EUR", quality="Średni",
                    market=mcode)
        err = _assistant_apply_lead_fields(lead, inp, user)
        if err:
            return _json.dumps({"error": err + " — lead NIE został utworzony"}, ensure_ascii=False), None
        db.session.add(lead)
        db.session.flush()
        db.session.add(StageHistory(lead_id=lead.id, from_stage=None, to_stage=lead.stage, changed_by=user["id"]))
        if lead.stage == "Oferta":
            ensure_followup_call(lead)
        err = _assistant_closed_on(lead, inp, user)
        if err:
            db.session.rollback()
            return _json.dumps({"error": err + " — lead NIE został utworzony"}, ensure_ascii=False), None
        db.session.commit()
        return _json.dumps({"ok": True, "lead_id": lead.id}, ensure_ascii=False), f"Utworzono leada #{lead.id} {lead.company}"

    if name == "update_lead":
        lead = db.session.get(Lead, int(inp.get("lead_id", 0) or 0))
        if not lead:
            return _json.dumps({"error": "nie ma leada o tym id"}), None
        err = _assistant_apply_lead_fields(lead, inp, user) or _assistant_closed_on(lead, inp, user)
        if err:
            db.session.rollback()
            return _json.dumps({"error": err + " — nic nie zostało zmienione"}, ensure_ascii=False), None
        db.session.commit()
        out = _assistant_lead_brief(lead)
        if getattr(lead, "_mcp_warnings", None):
            out["warnings"] = lead._mcp_warnings
        if lead.stage in CLOSED_STAGES:
            closed = lead_closed_on(lead)
            out["closed_on"] = closed.strftime("%Y-%m-%d") if closed else None
        return _json.dumps({"ok": True, "lead": out}, ensure_ascii=False), f"Zaktualizowano leada #{lead.id} {lead.company}"

    if name == "add_task":
        lead = db.session.get(Lead, int(inp["lead_id"])) if inp.get("lead_id") else None
        if inp.get("lead_id") and not lead:
            return _json.dumps({"error": "nie ma leada o tym id"}), None
        kind = inp.get("kind") if inp.get("kind") in ACTIVITY_KINDS else "call"
        due = parse_date(str(inp.get("due_date", "")).strip())
        if not lead and not due:
            due = datetime.combine(date.today(), datetime.min.time())
        assigned = _assistant_resolve_user(inp.get("assigned_to"), (lead.assigned_to if lead else None) or user["id"])
        act = Activity(lead_id=lead.id if lead else None, kind=kind, due_date=due,
                       notes=str(inp.get("notes", "")).strip(), assigned_to=assigned, created_by=user["id"])
        db.session.add(act)
        db.session.commit()
        who = USERS_BY_ID.get(assigned, {}).get("name", "?")
        target = f"leada #{lead.id} {lead.company}" if lead else "zadanie własne"
        return _json.dumps({"ok": True, "task_id": act.id}, ensure_ascii=False), f"Dodano zadanie ({ACTIVITY_KIND_LABELS.get(kind, kind)}) dla {who} — {target}"

    if name == "list_tasks":
        q = Activity.query.filter_by(done=False).filter(Activity.kind != "note")
        uid = _assistant_resolve_user(inp.get("assigned_to"), None)
        if uid:
            q = q.filter(Activity.assigned_to == uid)
        tasks = [
            {"id": a.id, "kind": a.kind, "due": a.due_date.strftime("%Y-%m-%d") if a.due_date else None,
             "lead": a.lead.company if a.lead else "Zadanie własne",
             "assigned_to": USERS_BY_ID.get(a.assigned_to, {}).get("name", ""), "notes": a.notes or "",
             "ordered_by": (USERS_BY_ID.get(a.created_by, {}).get("name")
                            if a.created_by and a.created_by != a.assigned_to else None)}
            for a in q.order_by(Activity.due_date).limit(50).all()
        ]
        return _json.dumps({"tasks": tasks}, ensure_ascii=False), None

    if name == "complete_task":
        a = db.session.get(Activity, int(inp.get("task_id", 0) or 0))
        if not a:
            return _json.dumps({"error": "nie ma zadania o tym id"}), None
        if a.done:
            return _json.dumps({"ok": True, "note": "zadanie było już odhaczone"}), None
        answers = {k: inp.get(k) for k, _q in sales_rules.CONFIRM_QUESTIONS if inp.get(k)}
        err = complete_activity(a, user["id"], inp.get("outcome"), answers)
        if err:
            db.session.rollback()
            return _json.dumps({"error": err["error"], "outcomes": list(err.get("outcomes", {})),
                                "questions": err.get("questions") or None}, ensure_ascii=False), None
        db.session.commit()
        return _json.dumps({"ok": True}), f"Odhaczono zadanie #{a.id}"

    if name == "add_comment":
        lead = db.session.get(Lead, int(inp.get("lead_id", 0) or 0))
        if not lead:
            return _json.dumps({"error": "nie ma leada o tym id"}), None
        text = str(inp.get("text", "")).strip()
        if not text:
            return _json.dumps({"error": "pusty komentarz"}), None
        db.session.add(Activity(lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(),
                                notes=text, assigned_to=user["id"]))
        db.session.commit()
        return _json.dumps({"ok": True}), f"Dodano komentarz do leada #{lead.id} {lead.company}"

    if name == "send_email":
        to_addr = str(inp.get("to", "")).strip()
        subject = human_text(str(inp.get("subject", "")).strip())
        body = human_text(str(inp.get("body", "")).strip())
        if not (to_addr and subject and body):
            return _json.dumps({"error": "wymagane: to, subject i body"}), None
        recipients, bad = check_recipients(to_addr)
        if bad:
            return _json.dumps({"error": bad}, ensure_ascii=False), None
        to_addr = ", ".join(recipients)
        external = [a for a in recipients if a.split("@")[-1].lower() not in OWN_DOMAINS]
        lead = db.session.get(Lead, int(inp["lead_id"])) if inp.get("lead_id") else None
        if lead is None and external:
            lead = next((x for x in (_find_lead_by_contact(a) for a in external) if x), None)
        mcode = lead_market(lead)
        bad_lang = wrong_language(mcode, subject, body) if external else None
        if bad_lang:
            return _json.dumps({"error": f"Mail do klienta musi być {markets.get(mcode)['lang_in_pl']} (wykryto: {bad_lang}). "
                                         f"Napisz temat i treść jeszcze raz {markets.get(mcode)['lang_in_pl']} i wyślij ponownie."},
                               ensure_ascii=False), None
        err = _smtp_send(to_addr, subject, body, user["name"], sender=market_sender(user, mcode) if external else user)
        if err:
            return _json.dumps({"error": err}), None
        if lead:
            db.session.add(Activity(
                lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(),
                notes=f"📧 {user['name']} wysłał(a) maila do {to_addr}\nTemat: {subject}\n---\n{body}",
                assigned_to=user["id"],
            ))
            db.session.commit()
        return _json.dumps({"ok": True, "sent_to": to_addr}), f"Wysłano maila do {to_addr}: {subject}"

    if name == "prepare_offer":
        inp = human_deep(dict(inp))  # mail i treść PDF bez „AI-owych” myślników
        to_addr = str(inp.get("to", "")).strip()
        subject = str(inp.get("subject", "")).strip()
        body = str(inp.get("body", "")).strip()
        if not (to_addr and subject and body):
            return _json.dumps({"error": "wymagane: to, subject i body"}), None
        recipients, bad = check_recipients(to_addr)
        if bad:
            return _json.dumps({"error": bad}, ensure_ascii=False), None
        to_addr = ", ".join(recipients)[:200]
        lead = None
        if inp.get("lead_id"):
            lead = db.session.get(Lead, int(inp["lead_id"]))
            if not lead:
                return _json.dumps({"error": "nie ma leada o tym id"}), None
        sender = market_sender(USERS_BY_ID.get(_assistant_resolve_user(inp.get("sender"), user["id"]), user),
                               lead_market(lead))
        if not user_can_send_offers(sender):
            others = ", ".join(u["name"] for u in USERS if user_can_send_offers(u))
            return _json.dumps({"error": f"{sender['name']} nie ma w CRM firmowego e-maila/telefonu — uzupełnij je "
                                         f"w Profilu (menu na dole → profil) albo wybierz nadawcę: {others}"},
                               ensure_ascii=False), None
        offer_in = dict(inp.get("offer") or {})
        mcode = lead_market(lead)
        lang_in = markets.get(mcode)["lang_in_pl"]
        offer_in["language"] = markets.get(mcode)["lang"]  # oferta zawsze w języku rynku leada — nigdy po angielsku
        pdf_text = " ".join(str(offer_in.get(k) or "") for k in ("subtitle", "intro", "price_title", "included", "note")) + " " + \
            " ".join(" ".join([str(cd.get("title") or "")] + [str(x) for x in (cd.get("items") or [])])
                     for cd in (offer_in.get("cards") or []) if isinstance(cd, dict))
        bad_lang = wrong_language(mcode, subject, body) or wrong_language(mcode, pdf_text)
        if bad_lang:
            return _json.dumps({"error": f"oferta: mail i PDF muszą być w całości {lang_in} "
                                         f"(wykryto: {bad_lang}). Przepisz temat, treść maila i wszystkie pola PDF "
                                         f"{lang_in}."}, ensure_ascii=False), None
        inp = dict(inp, offer=offer_in)
        try:
            data = offers.normalize(offer_in)
            pdf = offers.render_offer_pdf(data, (sender["full_name"], sender["email"], sender["phone"]))
        except offers.OfferError as e:
            return _json.dumps({"error": f"oferta: {e}"}, ensure_ascii=False), None
        value = None
        if inp.get("value") not in (None, ""):
            try:
                value = float(inp["value"])
            except (TypeError, ValueError):
                value = None
        days = inp.get("followup_days")
        try:
            days = 3 if days in (None, "") else max(0, int(days))
        except (TypeError, ValueError):
            days = 3
        offer = SentOffer(
            lead_id=lead.id if lead else None, filename=offers.offer_filename(data["client"]), pdf=pdf,
            data=_json.dumps({"offer": inp.get("offer"), "value": value, "followup_days": days,
                              "prepared_by": user["id"]}, ensure_ascii=False),
            to_addr=to_addr, subject=subject, body=body, price=data["price"], sender_id=sender["id"],
        )
        db.session.add(offer)
        db.session.commit()
        target = f" (lead #{lead.id} {lead.company})" if lead else ""
        return _json.dumps({"ok": True, "status": "szkic — czeka na sprawdzenie i wysyłkę w CRM",
                            "review_url": _offer_url(offer, review=True), "pdf_url": _offer_url(offer),
                            "offer_id": offer.id}, ensure_ascii=False), \
            f"Przygotowano ofertę {data['price']} dla {to_addr}{target} — do sprawdzenia w CRM"

    if name == "quote_price":
        hours = int(inp.get("hours") or 8)
        if hours not in pricing.HOUR_FACTORS:
            return _json.dumps({"error": f"hours: {sorted(pricing.HOUR_FACTORS)}"}), None
        km = inp.get("km")
        try:
            km = max(0.0, float(km)) if km not in (None, "") else None
        except (TypeError, ValueError):
            return _json.dumps({"error": "km musi być liczbą"}), None
        dest = str(inp.get("destination") or "").strip()
        if km is None and not dest:
            return _json.dumps({"error": "podaj destination albo km"}), None
        wanted = [k for k, v in pricing.DEPARTURES.items() if v == inp.get("departure")] or None
        start = pricing.parse_start(inp.get("start"))
        if inp.get("start") and start is None:
            return _json.dumps({"error": "start w formacie HH:MM, np. 20:00"}), None
        try:
            night = float(inp["night_hours"]) if inp.get("night_hours") not in (None, "") else pricing.night_hours(start, hours)
        except (TypeError, ValueError):
            return _json.dumps({"error": "night_hours musi być liczbą"}), None
        days = max(1, min(int(inp.get("days") or 1), pricing.MAX_DAYS))
        res = price_quotes(dest, hours, km, departures=wanted, night=night, days=days)
        if not res["options"]:
            return _json.dumps({"error": f"nie umiem znaleźć miejsca „{dest}” — podaj miasto albo km"},
                               ensure_ascii=False), None
        return _json.dumps({
            "place": res["place"],
            "options": [{"from": o["label"], "km_one_way": round(o["km"]), **{k: o["quote"][k] for k in
                         ("days", "base", "night", "night_extra", "onsite", "travel", "round_trips", "lodging",
                          "nights", "raise_by", "subtotal", "discount", "total", "rounded")},
                         "travel_share_pct": round(o["quote"]["travel_share"] * 100, 1),
                         "breakdown": pricing.describe(o["quote"], o["label"], res["place"])}
                        for o in res["options"].values()],
            "note": "ceny netto; km szacunkowe (linia prosta × 1,25); rounded = zaokrąglone w górę (EUR)",
        }, ensure_ascii=False), None

    if name == "search_mail":
        if inp.get("message_id"):
            m = db.session.get(InboxMessage, int(inp["message_id"]))
            if not m:
                return _json.dumps({"error": "nie ma takiego maila"}), None
            return _json.dumps({
                "id": m.id, "received": f"{to_local(m.received_at):%Y-%m-%d %H:%M}" if m.received_at else None,
                "from": m.from_addr, "subject": m.subject, "status": m.status, "lead_id": m.lead_id,
                "body": (m.body or "")[:8000],
            }, ensure_ascii=False), None
        words = [w for w in re.split(r"\s+", str(inp.get("query") or "").strip()) if w][:6]
        q = InboxMessage.query
        for w in words:
            like = f"%{w}%"
            q = q.filter(db.or_(InboxMessage.subject.ilike(like), InboxMessage.body.ilike(like),
                                InboxMessage.from_addr.ilike(like)))
        if inp.get("days"):
            q = q.filter(InboxMessage.received_at >= datetime.utcnow() - timedelta(days=max(1, int(inp["days"]))))
        total = q.count()
        out = []
        for m in q.order_by(InboxMessage.received_at.desc()).limit(15).all():
            body = m.body or ""
            pos = min([i for i in (body.lower().find(w.lower()) for w in words) if i >= 0] or [0])
            out.append({
                "id": m.id, "received": f"{to_local(m.received_at):%Y-%m-%d %H:%M}" if m.received_at else None,
                "from": m.from_addr, "subject": m.subject, "status": m.status, "lead_id": m.lead_id,
                "snippet": re.sub(r"\s+", " ", body[max(0, pos - 120):pos + 280]).strip(),
            })
        oldest = db.session.query(db.func.min(InboxMessage.received_at)).scalar()
        return _json.dumps({
            "found": total, "messages": out,
            "searched_since": f"{to_local(oldest):%Y-%m-%d}" if oldest else None,
            "note": "tylko maile z kontakt@ przeczytane przez CRM; pełna treść: message_id",
        }, ensure_ascii=False), None

    if name == "closed_leads":
        raw = str(inp.get("month", "")).strip()
        m = re.match(r"^(\d{4})-(\d{2})$", raw)
        if not m:
            return _json.dumps({"error": "month w formacie YYYY-MM, np. 2026-08"}), None
        yy, mm = int(m.group(1)), int(m.group(2))
        won, lost = [], []
        for lead, stage, when in sorted(closed_lead_events(), key=lambda x: x[2]):
            if (when.year, when.month) != (yy, mm):
                continue
            entry = _assistant_lead_brief(lead)
            entry["closed_on"] = when.strftime("%Y-%m-%d")
            if stage != "Wygrany":
                entry["lost_reason_label"] = sales_rules.LOST_REASONS.get(lead.lost_reason or "", "brak (sprzed obowiązkowego powodu)")
            (won if stage == "Wygrany" else lost).append(entry)
        # wygrane w tym miesiącu, które potem zmieniły etap (np. jednak Stracony) — już nie liczą się jako wygrane
        changed = {}
        for h in StageHistory.query.filter(StageHistory.to_stage == "Wygrany", StageHistory.changed_at.isnot(None)).all():
            when = to_local(h.changed_at)
            if h.lead and h.lead.stage not in WON_STAGES and not h.lead.clickup_pending and (when.year, when.month) == (yy, mm):
                entry = _assistant_lead_brief(h.lead)
                entry.update({"won_on": when.strftime("%Y-%m-%d"), "current_stage": h.lead.stage})
                changed[h.lead.id] = entry
        return _json.dumps({
            "month": raw,
            "won": won, "lost": lost, "changed_after_win": list(changed.values()),
            "note": "wg obecnego etapu leada i daty, kiedy na niego trafił",
        }, ensure_ascii=False), None

    if name == "offer_audit":
        try:
            last = json.loads(get_setting(OFFER_AUDIT_REPORT_KEY) or "{}")
        except ValueError:
            last = {}
        rows = audit_offer_second_steps(apply=False)
        return _json.dumps({"current": rows, "missing": sum(1 for r in rows if not r["had_step"] and not r.get("contact")),
                            "contact_after_offer": sum(1 for r in rows if not r["had_step"] and r.get("contact")),
                            "last_audit": {"at": last.get("at"), "added": [r for r in last.get("rows", []) if r.get("added")],
                                           "removed": [r for r in last.get("rows", []) if r.get("removed")]}},
                           ensure_ascii=False, default=str), None

    if name == "sales_performance":
        p = build_sales_panel(max(30, min(int(inp.get("period_days") or 90), 365)))
        brief = lambda l: {"id": l.id, "company": l.company, "stage": l.stage}
        return _json.dumps({
            "months": p["months"], "eur_pln_rate": p["rate"],
            "countries": [{k: v for k, v in c.items()} for c in p["countries"]],
            "by_client_type": p["by_type"], "by_owner": p["by_owner"], "lost_reasons": p["reasons"],
            "response_time_minutes": p["response"],
            "cancellations_after_win": [{**brief(c["lead"]), "when": f"{c['when']:%Y-%m-%d}", "reason": c["reason"], "value_pln": c["pln"]}
                                        for c in p["cancels"]],
            "open_leads_10k": [{**brief(b["lead"]), "value_pln": b["pln"],
                                "next_step": f"{b['next']:%Y-%m-%d}" if b["next"] else None} for b in p["big_open"]],
            "note": "wartości netto; EUR przeliczane po kursie NBP; miesiąc = miesiąc zamknięcia (wygranej)",
        }, ensure_ascii=False, default=str), None

    if name == "monthly_revenue":
        def add(d, cur, v):
            if v:
                d[cur] = d.get(cur, 0) + v
        months = {}
        no_date = {"count": 0, "value": {}, "leads": []}
        totals = {"realized": {}, "to_realize": {}}
        for lead in Lead.query.filter(Lead.stage.in_(WON_STAGES), Lead.clickup_pending.isnot(True)).all():
            cur = lead.currency or "EUR"
            part = "realized" if lead.stage == "Zrealizowany" else "to_realize"
            add(totals[part], cur, lead.value_pln)
            if sane_event_date(lead.event_date):
                k = f"{lead.event_date.year:04d}-{lead.event_date.month:02d}"
                m = months.setdefault(k, {"count": 0, "value": {}, "realized": {}, "to_realize": {}})
                m["count"] += 1
                add(m["value"], cur, lead.value_pln)
                add(m[part], cur, lead.value_pln)
            else:
                no_date["count"] += 1
                add(no_date["value"], cur, lead.value_pln)
                no_date["leads"].append({"id": lead.id, "company": lead.company, "stage": lead.stage,
                                         "value": lead.value_pln, "currency": cur})
        out = {
            "months": [{"month": k, **months[k]} for k in sorted(months)],
            "totals": totals,
            "won_without_rental_date": no_date,
            "note": "wartości wg miesiąca pierwszego dnia wynajmu; miesiące bez wygranych pominięte",
        }
        return _json.dumps(out, ensure_ascii=False), None

    if name == "logistics_plan":
        d_from = sane_event_date(parse_dt(str(inp.get("date_from") or "").strip()))
        d_from = d_from.date() if d_from else date.today()
        d_to = sane_event_date(parse_dt(str(inp.get("date_to") or "").strip()))
        d_to = d_to.date() if d_to else d_from + timedelta(days=13)
        if d_to < d_from:
            d_from, d_to = d_to, d_from
        d_to = min(d_to, d_from + timedelta(days=LOGISTICS_MAX_RANGE_DAYS))
        plan = build_logistics(d_from, d_to, bool(inp.get("include_tentative")), remote_budget=2)
        days_out = []
        for day in plan["days"]:
            evs = []
            for ev in day["events"]:
                lead = ev["lead"]
                evs.append({
                    "lead_id": lead.id, "company": lead.company, "stage": lead.stage,
                    "location": ev["label"] or ev["place_text"], "robot_model": lead.robot_model or "Unitree G1",
                    "crews": [{
                        "crew_id": cv["crew"].id,
                        "operator": cv["crew"].operator.name if cv["crew"].operator else None,
                        "robot": cv["crew"].robot.name if cv["crew"].robot else None,
                        **({"double": True,
                            "second_operator": cv["crew"].operator2.name if cv["crew"].operator2 else None,
                            "second_robot": cv["crew"].robot2.name if cv["crew"].robot2 else None,
                            "same_car": cv["carpool"]} if cv["crew"].is_double else {}),
                        "city": cv["crew"].city or "", "notes": cv["crew"].notes or "",
                    } for cv in ev["crews"]],
                })
            days_out.append({"date": day["date"].isoformat(), "events": evs})
        routes_out = []
        for r in plan["routes"]:
            if r.get("unknown_base"):
                routes_out.append({"operator": r["op"].name, "base": r["base_label"], "error": "nieznana baza"})
                continue
            trips = []
            for t in r["trips"]:
                legs = " · ".join(
                    f"{l['place']} na miejscu" if l.get("local") else f"nocleg {l['place']}" if l.get("stay")
                    else f"{l['from']} → {l['to']} ≈{logistics.fmt_km(l['km'])} km"
                    for l in t["legs"]
                )
                trips.append(f"{_fmt_d(t['start'])}{'–' + _fmt_d(t['end']) if t['end'] != t['start'] else ''}: {legs}")
            routes_out.append({
                "operator": r["op"].name, "base": r["base_label"], "km": round(r["km"]),
                "drive_hours": round(r["hours"], 1), "nights": r["nights"], "trips": trips,
                **({"rides_along": [f"{_fmt_d(x['date'])} {x['company']} ({x['place']}) — jako 2. operator, "
                                    f"kierowca: {x['driver']}" for x in r["rides"]]} if r.get("rides") else {}),
            })
        return _json.dumps({
            "range": [d_from.isoformat(), d_to.isoformat()],
            "days": days_out,
            "problems": [w["text"] for w in plan["warnings"]],
            "suggestions": [
                {"text": s["text"], "saving_km": round(s["saving_km"])} if s["kind"] != "assign"
                else {"text": s["text"], "extra_km": round(s["extra_km"])}
                for s in plan["suggestions"]
            ],
            "routes": routes_out,
            "operators": [{"name": o.name, "base": o.base_city or LOGISTICS_DEFAULT_BASE, "active": bool(o.active)}
                          for o in plan["operators"]],
            "fleet": [{"name": r.name, "model": r.model, "active": bool(r.active)} for r in plan["robots"]],
            "note": "km szacunkowe (linia prosta × 1,25); przypisania zmieniasz przez assign_crew",
        }, ensure_ascii=False), None

    if name == "assign_crew":
        lead = db.session.get(Lead, int(inp.get("lead_id") or 0))
        if not lead:
            return _json.dumps({"error": "nie ma takiego leada"}), None
        dt = parse_dt(str(inp.get("date") or "").strip())
        if not dt:
            return _json.dumps({"error": "date w formacie YYYY-MM-DD"}), None
        day = dt.date()
        lead_days = [ed.event_date for ed in lead.event_days]
        if day not in lead_days:
            return _json.dumps({"error": f"{lead.company} nie ma realizacji {day.isoformat()}",
                                "event_days": [d.isoformat() for d in lead_days]}, ensure_ascii=False), None

        def find_by_name(model, value):
            wanted = logistics.normalize(value)
            items = model.query.all()
            exact = [x for x in items if logistics.normalize(x.name) == wanted]
            if exact:
                return exact[0], items
            partial = [x for x in items if logistics.normalize(x.name).startswith(wanted)]
            return (partial[0] if len(partial) == 1 else None), items

        found = {}
        for field, model in (("operator", Operator), ("second_operator", Operator),
                             ("robot", RobotUnit), ("second_robot", RobotUnit)):
            if not inp.get(field):
                continue
            item, items = find_by_name(model, str(inp[field]))
            if not item:
                if model is Operator:
                    return _json.dumps({"error": f"nie znam operatora „{inp[field]}” — dodaj go w zakładce Logistyka",
                                        "operators": [o.name for o in items]}, ensure_ascii=False), None
                return _json.dumps({"error": f"nie znam robota „{inp[field]}”",
                                    "fleet": [r.name for r in items]}, ensure_ascii=False), None
            found[field] = item
        op, robot = found.get("operator"), found.get("robot")
        crews = Crew.query.filter_by(lead_id=lead.id, event_date=day).order_by(Crew.id).all()
        if inp.get("crew_id"):
            crew = next((c for c in crews if c.id == int(inp["crew_id"])), None)
            if not crew:
                return _json.dumps({"error": "ta ekipa nie należy do tego dnia"}), None
        elif inp.get("new_crew") or not crews:
            crew = Crew(lead_id=lead.id, event_date=day)
            db.session.add(crew)
        elif len(crews) == 1:
            crew = crews[0]
        else:
            return _json.dumps({"error": "tego dnia jest kilka ekip — podaj crew_id albo new_crew=true", "crews": [
                {"crew_id": c.id, "operator": " + ".join(o.name for o in c.operators_list()) or None,
                 "robot": " + ".join(r.name for r in c.robots_list()) or None, "city": c.city or ""} for c in crews
            ]}, ensure_ascii=False), None
        if op:
            crew.operator_id = op.id
        if robot:
            crew.robot_id = robot.id
        if inp.get("double") is not None or found.get("second_operator") or found.get("second_robot"):
            crew.robots_count = 2 if (inp.get("double") is not False) else 1
        if crew.is_double:
            if found.get("second_operator"):
                crew.operator2_id = found["second_operator"].id
            if found.get("second_robot"):
                crew.robot2_id = found["second_robot"].id
            if crew.operator_id and crew.operator_id == crew.operator2_id:
                db.session.rollback()
                return _json.dumps({"error": "ten sam operator na obu miejscach — podaj innego second_operator"},
                                   ensure_ascii=False), None
            if crew.robot_id and crew.robot_id == crew.robot2_id:
                db.session.rollback()
                return _json.dumps({"error": "ten sam robot na obu miejscach — podaj innego second_robot"},
                                   ensure_ascii=False), None
        else:
            crew.operator2_id = crew.robot2_id = None
        if inp.get("city") is not None:
            crew.city = str(inp["city"]).strip()[:200] or None
        if inp.get("notes") is not None:
            crew.notes = str(inp["notes"]).strip()[:300] or None
        db.session.commit()
        who = ", ".join([o.name for o in crew.operators_list()] + [r.name for r in crew.robots_list()])
        out = {"ok": True, "crew_id": crew.id, "operator": crew.operator.name if crew.operator else None,
               "robot": crew.robot.name if crew.robot else None}
        if crew.is_double:
            out.update({"double": True, "second_operator": crew.operator2.name if crew.operator2 else None,
                        "second_robot": crew.robot2.name if crew.robot2 else None})
        return _json.dumps(out, ensure_ascii=False), \
            f"Logistyka: {lead.company} {day.strftime('%d.%m')} → {who or 'ekipa bez przypisań'}" \
            f"{' (ekipa podwójna)' if crew.is_double else ''}"

    return _json.dumps({"error": f"nieznane narzędzie {name}"}), None


def _assistant_system_prompt(user):
    users_txt = ", ".join(f"{u['name']} (id {u['id']})" for u in USERS)
    return (
        "Jesteś asystentem CRM firmy 33bots (wynajem robotów Unitree). "
        "To jest CRM na Litwę i Niemcy: każdy lead ma rynek (lt albo de), kwoty w EUR. Maile do klienta pisz ZAWSZE "
        "w języku jego rynku — Litwa: po litewsku ('Laba diena,' … 'Pagarbiai,'), Niemcy: po niemiecku ('Guten Tag,' … "
        "'Mit freundlichen Grüßen', forma 'Sie') — nigdy po angielsku ani po polsku. "
        f"Użytkownik ogląda teraz rynek: {M()['name']} — nowe leady trafiają na ten rynek. "
        "Z użytkownikiem rozmawiaj w jego języku (polski, litewski albo niemiecki). "
        f"Dziś jest {date.today().isoformat()}. Rozmawiasz z użytkownikiem: {user['name']}. "
        f"Użytkownicy CRM: {users_txt}; Jerzy to pracownik do zadań. "
        f"Etapy leada: {', '.join(STAGES)} (Zrealizowany = wygrany po ostatnim dniu realizacji — CRM przenosi "
        f"sam; w statystykach liczy się jako wygrany). Roboty: {', '.join(ROBOT_MODELS)}. "
        "Do wyceny wynajmu używaj narzędzia quote_price (cennik + dojazd od granicy PL–LT). "
        f"Waluty: {', '.join(CURRENCIES)} (kwota bez waluty = EUR). Oceny leada: {', '.join(QUALITY_LEVELS)}.\n"
        "Zasady:\n"
        "- Zanim zmienisz leada, znajdź go przez find_leads — nigdy nie zgaduj ID.\n"
        "- Jeśli pasuje kilka leadów albo polecenie jest niejasne, dopytaj zamiast zgadywać.\n"
        "- Nie możesz niczego usuwać z CRM.\n"
        "- Daty przekazuj w formacie YYYY-MM-DD; określenia typu 'jutro' przeliczaj względem dzisiejszej daty.\n"
        "- Dni wynajmu mogą być niekolejne i mogą mieć różne miasta różnego dnia (event_locations).\n"
        "- Umowy i bazy wiedzy prowadzi zakładka Customer Journey: CRM sam przygotowuje maile z umową (DOCX) "
        "i osobno z instrukcją bazy wiedzy, ale wysyła je człowiek po akcepcie — NIE wysyłaj umów ani przypomnień o nich przez "
        "send_email. Status umowy i bazy wiedzy widać w get_lead (customer_journey).\n"
        "- Logistyka realizacji: logistics_plan pokazuje, kto (operator) z którym robotem jedzie gdzie, km tras, konflikty "
        "i propozycje; assign_crew przypisuje operatora/robota do dnia realizacji. Operatorów i flotę dodaje się w zakładce Logistyka.\n"
        "- Możesz wysyłać PRAWDZIWE e-maile (send_email) — wychodzą ze skrzynki firmowej, podpisane imieniem rozmówcy. "
        "Gdy mail dotyczy leada, znajdź jego adres przez find_leads/get_lead i podaj lead_id (wysyłka zapisze się w komentarzach). "
        "Jeśli użytkownik nie podyktował treści, napisz krótki, profesjonalny mail po polsku i podpisz: imię rozmówcy, zespół 33bots. "
        "Wysyłaj od razu, gdy polecenie jest jednoznaczne; dopytaj tylko, gdy adresat albo cel maila są niejasne. Po wysłaniu zacytuj, co poszło i do kogo.\n"        "- Oferty z PDF: prepare_offer tworzy ofertę w firmowym layoucie i mail jako szkic w CRM — nic nie wysyła. "
        "Podaj użytkownikowi link do podglądu; wysyłkę klika on sam w CRM. Nie wysyłaj ofert przez send_email. "
        "Nazwa klienta = oficjalna nazwa firmy; pisz 'Unitree G1' i 'personalizacja osobowości i wiedzy robota'.\n"

        "- Odpowiadaj po polsku, krótko i konkretnie. Po wykonaniu zmian podsumuj w 1-2 zdaniach, co zrobiłeś."
    )


_include("asystent_czat")
OWN_DOMAINS = {d.strip().lower() for d in os.environ.get("OWN_DOMAINS", "33bots.pl,33bots.com,33bots.lt,33bots.de").split(",")
               if d.strip()}

_JOBS = {}
_JOBS_LOCK = threading.Lock()
_INBOX_LOCK = threading.Lock()


def _start_job(kind, fn, *args):
    """Uruchamia fn(*args) w tle. fn zwraca dict z 'url' (dokąd przejść po sukcesie) i 'info'."""
    jid = secrets.token_urlsafe(9)
    with _JOBS_LOCK:
        for k in [k for k, v in _JOBS.items() if time.time() - v["started"] > 3600]:
            _JOBS.pop(k, None)
        _JOBS[jid] = {"status": "pending", "kind": kind, "started": time.time()}

    def run():
        with app.app_context():
            try:
                res = fn(*args) or {}
                _JOBS[jid].update(status="error" if res.get("error") else "done", **res)
            except Exception as e:  # zadanie w tle nie może ubić aplikacji
                db.session.rollback()
                _JOBS[jid].update(status="error", error=f"nieoczekiwany błąd: {type(e).__name__}: {e}"[:300])
            finally:
                db.session.remove()

    threading.Thread(target=run, daemon=True).start()
    return jid


def _ai_client():
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return None
    import anthropic
    return anthropic.Anthropic()


# Te modele odrzucają wymuszone narzędzie (tool_choice tool/any → 400) — od razu tool_choice=auto, bez straconego zapytania.
_NO_FORCED_TOOL_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-fable-5-2",
                          "claude-mythos-5-1", "claude-mythos-5-2")


def _ai_forced_tool(model, system, user_text, tool, max_tokens=4096):
    """Wywołanie Claude'a, które ma skończyć się użyciem jednego narzędzia — zwraca jego argumenty.
    Najpierw wymusza narzędzie (tool_choice=tool); modele, które tego nie obsługują (np. Sonnet 5.5),
    dostają od razu tool_choice=auto z wyraźnym poleceniem w treści i jedną ponowną próbą."""
    import anthropic
    client = _ai_client()
    if client is None:
        raise RuntimeError("brak ANTHROPIC_API_KEY w Railway (web → Variables)")
    nudge = f"\n\nOdpowiedz WYŁĄCZNIE wywołaniem narzędzia {tool['name']} — bez tekstu."
    messages = [{"role": "user", "content": user_text + nudge}]
    forced_ok = not str(model).startswith(_NO_FORCED_TOOL_MODELS)

    def pick(resp):
        for block in resp.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                return dict(block.input or {})
        return None

    try:
        if forced_ok:
            resp = client.messages.create(
                model=model, max_tokens=max_tokens, system=system, tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]}, messages=messages,
            )
            found = pick(resp)
            if found is not None:
                return found
    except anthropic.BadRequestError as e:
        if "tool_choice" not in str(e):
            raise
    for _ in range(2):
        resp = client.messages.create(
            model=model, max_tokens=max(max_tokens, 16000), system=system, tools=[tool],
            tool_choice={"type": "auto"}, messages=messages,
        )
        found = pick(resp)
        if found is not None:
            return found
        messages = messages + [
            {"role": "assistant", "content": resp.content},
            {"role": "user", "content": f"Wywołaj teraz narzędzie {tool['name']} z kompletnymi danymi."},
        ]
    raise RuntimeError("Claude nie zwrócił wyniku — spróbuj ponownie")


# ----- skrzynka kontakt@ -----

INBOX_TOOL = {
    "name": "zapisz_zapytanie",
    "description": "Zapisuje wynik analizy maila: czy to zapytanie klienta o robota i dane do leada.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_inquiry": {"type": "boolean", "description": "true = zapytanie o wynajem/zakup robota, wycenę, współpracę z klientem"},
            "reason": {"type": "string", "description": "1 zdanie: dlaczego tak (np. 'formularz ze strony', 'newsletter', 'faktura', 'spam')"},
            "company": {"type": "string", "description": "Oficjalna nazwa firmy/instytucji; dla osoby prywatnej imię i nazwisko"},
            "contact_name": {"type": "string"},
            "contact_email": {"type": "string", "description": "Adres klienta (przy formularzu z treści, nie adres formspree)"},
            "contact_phone": {"type": "string"},
            "location": {"type": "string", "description": "Miasto / miejsce wydarzenia"},
            "event_dates": {"type": "array", "items": {"type": "string"}, "description": "Dni wydarzenia YYYY-MM-DD (tylko konkretne)"},
            "robot_model": {"type": "string", "enum": ROBOT_MODELS + [""], "description": "Robot, jeśli wynika z treści; G1 = humanoid, piesek = B2"},
            "quality": {"type": "string", "enum": QUALITY_LEVELS, "description": "Dobry = konkretna data/firma/budżet; Słaby = ogólnik"},
            "summary": {"type": "string", "description": "Notatka do leada po polsku, 1-4 zdania: co, kiedy, gdzie, ile osób, godziny, oczekiwania"},
        },
        "required": ["is_inquiry", "reason", "company", "summary"],
    },
}


def _inbox_system_prompt():
    return (
        "Analizujesz maile, które przyszły na firmową skrzynkę kontakt@ (Litwa: 33bots.lt, Niemcy: 33bots.de) — firma "
        "33bots wynajmuje roboty humanoidalne "
        "Unitree G1 i roboty-psy na eventy oraz sprzedaje roboty. Zapytanie (is_inquiry=true) to każdy mail od "
        "potencjalnego klienta: formularz ze strony (Formspree: 'New form submission'), prośba o ofertę, wycenę, "
        "dostępność, zakup, współpracę eventową. NIE są zapytaniami: newslettery, reklamy, faktury, powiadomienia "
        "systemów, oferty sprzedaży czegoś dla 33bots, spam, odbicia maili. "
        f"Dziś jest {date.today().isoformat()} — daty bez roku licz jako najbliższe przyszłe. "
        "Wypełnij pola tylko tym, co wynika z maila; nie zgaduj telefonu ani maila. Odpowiadasz wyłącznie narzędziem."
    )


def _inbox_boxes():
    """Skrzynki zapytań: [{market, host, login, pwd}] — Litwa: INBOX_IMAP_USER/PASS albo kontakt@33bots.lt,
    Niemcy: INBOX_IMAP_USER_DE/PASS_DE albo kontakt@33bots.de (hasło MAIL_PASSWORD / zmiennej skrzynki)."""
    host = os.environ.get("IMAP_HOST", "").strip() or os.environ.get("SMTP_HOST", "").strip()
    out = []
    for code in markets.MARKETS:
        sfx = "" if code == "lt" else f"_{code.upper()}"
        login = os.environ.get(f"INBOX_IMAP_USER{sfx}", "").strip()
        pwd = os.environ.get(f"INBOX_IMAP_PASS{sfx}", "").strip()
        bhost = host
        if not (login and pwd):
            box = mail_box(markets.get(code)["inquiry_box"])
            if box and box["pwd"]:
                login, pwd, bhost = box["login"], box["pwd"], box["host"] or host
        if bhost and login and pwd:
            out.append({"market": code, "host": bhost, "login": login.lower(), "pwd": pwd})
    return out


def _inbox_config(code=None):
    """(host, login, hasło) skrzynki zapytań rynku (domyślnie oglądanego) albo (None, None, None)."""
    code = code or current_market()
    box = next((b for b in _inbox_boxes() if b["market"] == code), None)
    return (box["host"], box["login"], box["pwd"]) if box else (None, None, None)


def _mail_text(msg):
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", "replace")
    if part.get_content_type() == "text/html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
        import html as _html
        text = _html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _find_lead_by_contact(email_addr=None, phone=None):
    if email_addr:
        addr = email_addr.strip().lower()
        lead = Lead.query.filter(db.or_(db.func.lower(Lead.contact_email) == addr,
                                        db.func.lower(Lead.contact_email2) == addr)) \
            .order_by(Lead.created_at.desc()).first()
        if lead:
            return lead
    digits = re.sub(r"\D", "", phone or "")[-9:]
    if len(digits) == 9:
        for lead in Lead.query.filter(Lead.contact_phone.isnot(None)).order_by(Lead.created_at.desc()).limit(2000):
            if re.sub(r"\D", "", lead.contact_phone or "")[-9:] == digits:
                return lead
    return None


# ---------- DUPLIKATY (ten sam klient wpisany dwa razy — np. mail od agencji + ClickUp) ----------

DUP_WINDOW_DAYS = 30  # ten sam telefon/e-mail dodany w tylu dniach = najpewniej to samo zapytanie
_DUP_STAGE_RANK = {"Nowy": 0, "Stracony": 1.5, "Oferta": 2, "Wygrany": 5, "Zrealizowany": 6}


def _phone_key(phone):
    digits = re.sub(r"\D", "", phone or "")
    return digits[-9:] if len(digits) >= 9 else None


def _email_key(email):
    e = (email or "").strip().lower()
    return e if "@" in e and e.rsplit("@", 1)[-1] not in OWN_DOMAINS else None


def _lead_keys(lead):
    keys = [("tel", _phone_key(lead.contact_phone)), ("mail", _email_key(lead.contact_email)),
            ("mail", _email_key(lead.contact_email2))]
    return list(dict.fromkeys(k for k in keys if k[1]))


def find_recent_duplicate(email=None, phone=None, days=DUP_WINDOW_DAYS, exclude_id=None):
    """Lead z tym samym telefonem albo e-mailem dodany w ostatnich `days` dniach (najstarszy) albo None."""
    since = datetime.utcnow() - timedelta(days=days)
    pk, ek = _phone_key(phone), _email_key(email)
    if not pk and not ek:
        return None
    for lead in all_markets(Lead.query.filter(Lead.created_at >= since).order_by(Lead.created_at, Lead.id)).all():
        if lead.id != exclude_id and ((pk and _phone_key(lead.contact_phone) == pk)
                                      or (ek and ek in (_email_key(lead.contact_email), _email_key(lead.contact_email2)))):
            return lead
    return None


def duplicate_groups(window_days=DUP_WINDOW_DAYS):
    """Grupy leadów z tym samym telefonem albo e-mailem, dodanych najwyżej `window_days` dni
    po sobie (łańcuchowo). Każda grupa: lista leadów, pierwszy = proponowany główny."""
    leads = [l for l in Lead.query.all() if l.created_at]
    parent = {l.id: l.id for l in leads}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_key = {}
    for l in leads:
        for key in _lead_keys(l):
            by_key.setdefault(key, []).append(l)
    for group in by_key.values():
        group.sort(key=lambda l: l.created_at)
        for a, b in zip(group, group[1:]):
            if (b.created_at - a.created_at).days <= window_days:
                parent[find(a.id)] = find(b.id)
    clusters = {}
    for l in leads:
        clusters.setdefault(find(l.id), []).append(l)
    return [sorted(g, key=_keeper_order, reverse=True) for g in clusters.values() if len(g) > 1]


def _keeper_order(lead):
    """Który z duplikatów zostaje: zaakceptowany, dalej w lejku, z kwotą, z historią; przy remisie starszy."""
    return (not lead.clickup_pending, _DUP_STAGE_RANK.get(lead.stage, 0), lead.value_pln is not None,
            len(lead.activities), -lead.id)


IMPORT_SOURCES = ("ClickUp", "Mail kontakt@", "Formularz 33bots.lt", "Mail kontakt@33bots.lt", "Mail kontakt@33bots.de",
                  "Formularz 33bots.de")


_include("duplikaty_scalanie")


def process_inbox_message(rec, force=False):
    """Klasyfikuje mail i tworzy leada albo dopisuje go do istniejącego. Ustawia rec.status."""
    sender = (rec.from_addr or "").lower()
    is_form = "formspree" in sender or "new form submission" in (rec.subject or "").lower()
    if not force and not is_form:
        lead = _find_lead_by_contact(sender)
        if lead:
            header = "📥 Mail od klienta"
            if _inbox_accept_pending(lead):
                header += " — zatwierdzony z poczekalni ClickUp"
            db.session.add(Activity(lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(),
                                    assigned_to=lead.assigned_to or 1, notes=_inbox_note(rec, header)))
            rec.status, rec.lead_id, rec.reason = "dopisany", lead.id, "nadawca jest już w CRM"
            return
    text = (f"Od: {rec.from_addr}\nTemat: {rec.subject}\nData: {rec.received_at}\n\n{(rec.body or '')[:12000]}")
    if force:
        text += "\n\n(Użytkownik potwierdził, że to zapytanie klienta — ustaw is_inquiry=true i wyciągnij dane.)"
    res = _ai_forced_tool(INBOX_AI_MODEL, _inbox_system_prompt(), text, INBOX_TOOL, max_tokens=1500)
    if not res.get("is_inquiry") and not force:
        rec.status, rec.reason = "pominiety", str(res.get("reason") or "")[:300]
        return
    contact_email = str(res.get("contact_email") or "").strip()
    if not contact_email and not is_form and "@" in sender:
        contact_email = sender
    lead = _find_lead_by_contact(contact_email, res.get("contact_phone"))
    if lead:
        updated = _inbox_refresh_lead(lead, res, contact_email, rec.received_at)
        if _inbox_accept_pending(lead):
            updated.insert(0, "zatwierdzony z poczekalni ClickUp")
        header = "📥 Kolejne zapytanie od tego klienta"
        if updated:
            header += " — zaktualizowano: " + ", ".join(updated)
        db.session.add(Activity(lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(),
                                assigned_to=lead.assigned_to or 1, notes=_inbox_note(rec, header)))
        rec.status, rec.lead_id, rec.reason = "dopisany", lead.id, "klient jest już w CRM"
        return
    assignee = USERS_BY_ID.get(_assistant_resolve_user(os.environ.get("INBOX_ASSIGN_TO", "Oliwier"), 1), USERS[0])
    fields = {
        "company": (str(res.get("company") or "").strip() or res.get("contact_name") or contact_email or "Nowe zapytanie")[:200],
        "contact_name": res.get("contact_name") or None, "contact_email": contact_email or None,
        "contact_phone": res.get("contact_phone") or None, "location": res.get("location") or None,
        "notes": res.get("summary") or None, "quality": res.get("quality"), "stage": "Nowy",
        "source": f"Formularz {markets.get(rec.market)['domain']}" if is_form else f"Mail kontakt@{markets.get(rec.market)['domain']}",
        "event_dates": res.get("event_dates") or None, "market": rec.market or DEFAULT_MARKET,
    }
    if res.get("robot_model") in ROBOT_MODELS:
        fields["robot_model"] = res["robot_model"]
    out, _ = _assistant_execute("create_lead", dict(fields, allow_duplicate=True), assignee)
    lead_id = json.loads(out).get("lead_id")
    if not lead_id:
        rec.status, rec.reason = "blad", f"nie udało się utworzyć leada: {out}"[:300]
        return
    db.session.add(Activity(lead_id=lead_id, kind="note", done=True, done_at=datetime.utcnow(),
                            assigned_to=assignee["id"], notes=_inbox_note(rec, "📥 Zapytanie z kontakt@")))
    rec.status, rec.lead_id, rec.reason = "lead", lead_id, str(res.get("reason") or "")[:300]


def sync_inbox(max_messages=60):
    """Czyta nowe maile ze skrzynek zapytań (kontakt@33bots.lt i kontakt@33bots.de; IMAP, bez oznaczania jako
    przeczytane) i przetwarza je — nowy lead dostaje rynek skrzynki. Zwraca dict z podsumowaniem."""
    boxes = _inbox_boxes()
    if not boxes:
        return {"error": "skrzynka nieskonfigurowana — ustaw MAIL_PASSWORD (albo INBOX_IMAP_USER i INBOX_IMAP_PASS) w Railway"}
    if _ai_client() is None:
        return {"error": "brak ANTHROPIC_API_KEY w Railway — bez niego CRM nie rozpozna zapytań"}
    if not _INBOX_LOCK.acquire(blocking=False):
        return {"info": "sprawdzanie skrzynki już trwa"}
    counts = {"lead": 0, "dopisany": 0, "pominiety": 0, "blad": 0}
    errors = []
    try:
        for box in boxes:
            err = _sync_inbox_box(box, counts, max_messages)
            if err:
                errors.append(f"{box['login']}: {err}")
    finally:
        _INBOX_LOCK.release()
    info = (f"nowe leady: {counts['lead']}, dopisane do istniejących: {counts['dopisany']}, "
            f"pominięte: {counts['pominiety']}, błędy: {counts['blad']}")
    if errors and len(errors) == len(boxes):
        return {"error": "połączenie ze skrzynką nie powiodło się — " + "; ".join(errors)[:300], "counts": counts}
    return {"info": info + (" · " + "; ".join(errors) if errors else ""), "counts": counts}


def _sync_inbox_box(box, counts, max_messages):
    import email as _email
    import email.policy
    import imaplib
    from email.utils import parseaddr, parsedate_to_datetime
    try:
        last = db.session.query(db.func.max(InboxMessage.received_at)).filter(
            db.or_(InboxMessage.mailbox == box["login"],
                   db.and_(InboxMessage.mailbox.is_(None), InboxMessage.market == box["market"]))).scalar()
        since = (last - timedelta(days=1)) if last else \
            datetime.utcnow() - timedelta(days=int(os.environ.get("INBOX_BACKFILL_DAYS", 3)))
        im = imaplib.IMAP4_SSL(box["host"], int(os.environ.get("IMAP_PORT", 993)), timeout=30)
        try:
            im.login(box["login"], box["pwd"])
            im.select("INBOX", readonly=True)
            typ, data = im.uid("search", None, "SINCE", since.strftime("%d-%b-%Y"))
            uids = (data[0] or b"").split()[-max_messages:]
            for uid in uids:
                typ, parts = im.uid("fetch", uid, "(BODY.PEEK[])")
                raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), None)
                if not raw:
                    continue
                msg = _email.message_from_bytes(raw, policy=_email.policy.default)
                key = (str(msg.get("Message-ID") or "").strip()
                       or "sha1:" + hashlib.sha1(raw[:20000]).hexdigest())[:320]
                if all_markets(InboxMessage.query.filter_by(message_key=key)).first():
                    continue  # ten sam mail (np. wysłany na obie skrzynki) — tylko raz
                from_addr = parseaddr(str(msg.get("From") or ""))[1].lower()
                try:
                    received = parsedate_to_datetime(str(msg.get("Date"))).astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    received = datetime.utcnow()
                rec = InboxMessage(message_key=key, from_addr=from_addr[:200], subject=str(msg.get("Subject") or "")[:300],
                                   received_at=received, body=_mail_text(msg)[:20000], mailbox=box["login"],
                                   market=box["market"])
                db.session.add(rec)
                auto = str(msg.get("Auto-Submitted") or "no").lower() != "no" or \
                    str(msg.get("Precedence") or "").lower() in ("bulk", "list", "junk") or \
                    from_addr.startswith(("mailer-daemon", "postmaster"))
                domain = from_addr.rsplit("@", 1)[-1]
                if domain in OWN_DOMAINS:
                    rec.status, rec.reason = "pominiety", "mail z naszej domeny"
                elif auto and "formspree" not in from_addr:
                    rec.status, rec.reason = "pominiety", "automatyczna wiadomość"
                else:
                    try:
                        process_inbox_message(rec)
                    except Exception as e:
                        rec.status, rec.reason = "blad", f"{type(e).__name__}: {e}"[:300]
                counts[rec.status] = counts.get(rec.status, 0) + 1
                db.session.commit()
        finally:
            try:
                im.logout()
            except Exception:
                pass
    except (imaplib.IMAP4.error, OSError) as e:
        db.session.rollback()
        return f"{type(e).__name__}: {e}"[:200]
    return None


def _inbox_scheduler():
    interval = max(60, int(os.environ.get("INBOX_POLL_SECONDS", 300)))
    time.sleep(20)
    while True:
        try:
            with app.app_context():
                res = sync_inbox() or {}
                db.session.remove()
            health_beat("inbox", not res.get("error"), info=res.get("info") or "", error=res.get("error") or "")
        except Exception as e:
            app.logger.exception("inbox_scheduler")
            health_fail("inbox", e)
        time.sleep(interval)


if any(os.environ.get(k, "").strip() for k in ("INBOX_IMAP_USER", "INBOX_IMAP_USER_DE", "MAIL_PASSWORD")):
    _start_worker("inbox", _inbox_scheduler)


@app.route("/crm/skrzynka")
def crm_inbox():
    host, login, _ = _inbox_config()
    msgs = InboxMessage.query.order_by(InboxMessage.received_at.desc()).limit(150).all()
    return render_template("crm/inbox.html", msgs=msgs, mailbox=login, configured=bool(host),
                           ai_ready=bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
                           poll_min=max(1, int(os.environ.get("INBOX_POLL_SECONDS", 300)) // 60))


@app.route("/crm/skrzynka/sprawdz", methods=["POST"])
def crm_inbox_check():
    jid = _start_job("Sprawdzanie skrzynki", lambda: dict(sync_inbox(), url="/crm/skrzynka"))
    return redirect(url_for("crm_job", jid=jid))


@app.route("/crm/skrzynka/<int:mid>/<action>", methods=["POST"])
def crm_inbox_action(mid, action):
    rec = db.session.get(InboxMessage, mid)
    if not rec or action not in ("lead", "ponow"):
        abort(404)

    def job():
        r = db.session.get(InboxMessage, mid)
        process_inbox_message(r, force=(action == "lead"))
        db.session.commit()
        if r.lead_id:
            return {"url": f"/crm/leads/{r.lead_id}", "info": "Gotowe"}
        return {"url": "/crm/skrzynka", "info": r.reason or r.status}
    return redirect(url_for("crm_job", jid=_start_job("Przetwarzanie maila", job)))


# ----- oferta pisana przez Claude z poziomu CRM -----

OFFER_AI_ATTEMPTS = 3  # pierwsza próba + 2 poprawki po błędzie walidacji


# folder ze zdjęciami z realizacji — trafia do maili z ofertą jako „zdjęcia”
OFFER_PHOTOS_URL = os.environ.get("OFFER_PHOTOS_URL", "").strip() or \
    "https://drive.google.com/drive/folders/1fOaECfc5eNKigB8xzW0z9rlq4jqr5CmK"


def _offer_ai_system_prompt(code=None):
    m = M(code)
    return (
        m["ai_offer"] + "\n"
        "Jesteś handlowcem firmy 33bots (wynajem i sprzedaż robotów humanoidalnych Unitree G1 oraz robotów-psów). "
        f"Dziś jest {date.today().isoformat()}. Przygotowujesz ofertę PDF i mail do klienta narzędziem prepare_offer.\n"
        "ZASADY OFERTY (pola offer):\n"
        "- client: oficjalna nazwa klienta (firmy/instytucji), nie robocza nazwa leada.\n"
        "- headline zwykle 'Robot humanoidalny'; subtitle: 1 zdanie, czym robot będzie na wydarzeniu klienta.\n"
        "- cover: 3 pola, np. PRZYGOTOWANO DLA / TERMIN / GODZINY albo CZAS / CENA (kontakt dopisze się sam).\n"
        "- intro: co robot robi (wita gości, rozmawia w oparciu o przygotowaną bazę wiedzy, tańczy, pozuje do zdjęć) "
        "i doświadczenie: 'Realizowaliśmy projekty m.in. dla Fundacji Perspektywy, Grupy Rekord, Adamed i DSV.'\n"
        "- cards: dokładnie 2 karty po 3 krótkie punkty (KAŻDY punkt max 90 znaków — dłuższy nie zmieści się w PDF): "
        "ROLA ROBOTA (dopasowana do wydarzenia) i PRZYGOTOWANIE "
        "('Dopasowanie do wydarzenia': osobowość i baza wiedzy robota pod wydarzenie / dedykowany operator przez cały czas "
        "wydarzenia / wsparcie techniczne na miejscu).\n"
        "- price: kwota netto w formacie '1 300 €'; price_title np. '4 godziny obecności robota'; price_sub: miejsce · data.\n"
        "- Trzymaj się limitów długości z opisu narzędzia (np. cover.value max 34 znaki, karta: tytuł max 32, punkt max 90) — "
        "szczegóły wydarzenia, które się nie mieszczą, opisz w mailu, nie w PDF.\n"
        "- included domyślnie: 'W cenie: dojazd, dedykowany operator, personalizacja osobowości i wiedzy robota oraz "
        "wsparcie techniczne na miejscu. Bez ukrytych opłat.'\n"
        "ZASADY MAILA (body):\n"
        "- profesjonalny, czytelny, krótkie akapity, bez linii-separatorów, bez nagłówków WIELKIMI LITERAMI, bez kolokwializmów;\n"
        "- zwrot 'Dzień dobry,' (albo 'Dzień dobry Pani X,' gdy znamy imię), podziękowanie za zapytanie, informacja o ofercie w załączniku;\n"
        "- cena netto i co zawiera; 1-2 zdania, jak robot sprawdzi się na tym konkretnym wydarzeniu;\n"
        f"- zdjęcia z realizacji — osobna linijka podpisana jako zdjęcia (np. 'Zdjęcia z realizacji:'): {OFFER_PHOTOS_URL}\n"
        "- w mailu NIE wymieniaj klientów, dla których pracowaliśmy, i NIE dodawaj żadnych innych linków (np. do nagrań) — "
        "jedyny link w mailu to link do zdjęć powyżej;\n"
        "- gdy brakuje daty/miejsca/godzin — poproś o nie; oferta ważna 14 dni;\n"
        "- podpis: 'Z pozdrowieniami,' imię i nazwisko nadawcy, '33Bots', jego e-mail i telefon.\n"
        "NIGDY nie pisz 'Edward Warchocki' — tylko 'Unitree G1'. Pisz 'personalizacja osobowości i wiedzy robota'. "
        "Nie wspominaj o ofertach konkurencji. Nie wymyślaj ceny — weź ją z polecenia; gdy jej brak, użyj wartości leada. "
        + m["ai_rule"] + " "
    )


def _offer_tool():
    return next(t for t in ASSISTANT_TOOLS if t["name"] == "prepare_offer")


def generate_offer_with_ai(lead_id, instruction, sender_id, actor_id, previous_id=None):
    lead = db.session.get(Lead, lead_id)
    if not lead:
        return {"error": "nie ma takiego leada"}
    sender = market_sender(USERS_BY_ID.get(sender_id, USERS[0]), lead_market(lead))
    actor = USERS_BY_ID.get(actor_id, USERS[0])
    notes = Activity.query.filter_by(lead_id=lead.id, kind="note").order_by(Activity.created_at.desc()).limit(6).all()
    days = ", ".join(d.event_date.isoformat() for d in lead.event_days) or "brak"
    ctx = [
        f"LEAD #{lead.id}: {lead.company}",
        f"Osoba: {lead.contact_name or '-'} · e-mail: {lead.contact_email or '-'} · tel.: {lead.contact_phone or '-'}",
        f"Miejsce: {lead.location or '-'} · dni: {days} · robot: {lead.robot_model or '-'} · "
        f"wartość w CRM: {lead.value_pln if lead.value_pln is not None else '-'} {lead.currency or 'EUR'}",
        f"Notatki leada: {lead.notes or '-'}",
        "",
        "OSTATNIE WPISY (najnowsze pierwsze — tu zwykle jest treść zapytania klienta):",
    ]
    for a in notes:
        ctx.append(f"[{a.created_at:%d.%m.%Y}] {(a.notes or '')[:3000]}")
    if previous_id:
        prev = db.session.get(SentOffer, previous_id)
        if prev:
            ctx += ["", "POPRZEDNIA WERSJA (popraw ją zgodnie z uwagami, resztę zostaw):",
                    f"Do: {prev.to_addr}\nTemat: {prev.subject}\nTreść maila:\n{prev.body}",
                    f"Dane PDF: {json.dumps(_offer_options(prev).get('offer'), ensure_ascii=False)}"]
    mcode = lead_market(lead)
    ctx += ["", f"NADAWCA i kontakt w ofercie: {sender['full_name']}, {sender['email']}, {sender['phone']}",
            *([] if mail_phone(sender["phone"], mcode) == sender["phone"] else
              [f"PODPIS MAILA: bez numeru telefonu{(' — użyj ' + mail_phone(sender['phone'], mcode)) if mail_phone(sender['phone'], mcode) else ''}"
               " (telefon jest tylko w PDF oferty)."]),
            "", "POLECENIE UŻYTKOWNIKA:", instruction.strip() or "Przygotuj ofertę na podstawie zapytania."]
    base_prompt = "\n".join(ctx)
    prompt, res = base_prompt, {}
    for attempt in range(OFFER_AI_ATTEMPTS):
        try:
            args = _ai_forced_tool(OFFER_AI_MODEL, _offer_ai_system_prompt(lead.market), prompt, _offer_tool(), max_tokens=6000)
        except Exception as e:
            return {"error": f"Claude nie przygotował oferty: {type(e).__name__}: {e}"[:300]}
        args["lead_id"] = lead.id
        args["sender"] = sender["name"]
        if not str(args.get("to") or "").strip():
            args["to"] = ", ".join(lead.emails)
        out, _ = _assistant_execute("prepare_offer", args, actor)
        res = json.loads(out)
        # błąd treści PDF (np. za długi punkt) Claude potrafi sam poprawić — odsyłamy mu go zamiast się poddawać
        if not str(res.get("error", "")).startswith("oferta:"):
            break
        prompt = (base_prompt + "\n\nTWOJA POPRZEDNIA PRÓBA ZOSTAŁA ODRZUCONA: " + res["error"]
                  + "\nPopraw dokładnie to (skróć za długie pola, trzymaj się limitów z opisu narzędzia), resztę zostaw bez zmian."
                  + "\nPoprzednie dane PDF: " + json.dumps(args.get("offer"), ensure_ascii=False))
    if res.get("error"):
        hint = " — Claude nie zmieścił się w limitach mimo poprawek; spróbuj ponownie" if str(res["error"]).startswith("oferta:") else ""
        return {"error": res["error"] + hint}
    if previous_id:
        prev = db.session.get(SentOffer, previous_id)
        if prev and not prev.sent_at:
            db.session.delete(prev)
            db.session.commit()
    return {"url": f"/crm/oferty/{res['offer_id']}", "info": "Oferta gotowa do sprawdzenia"}


@app.route("/crm/leads/<int:lead_id>/oferta-ai", methods=["POST"])
def crm_lead_offer_ai(lead_id):
    if not db.session.get(Lead, lead_id):
        abort(404)
    user = get_current_user(request)
    sender_id = _assistant_resolve_user(request.form.get("sender"), user["id"])
    instruction = request.form.get("instruction", "")[:2000]
    jid = _start_job("Claude pisze ofertę", generate_offer_with_ai, lead_id, instruction, sender_id, user["id"])
    return redirect(url_for("crm_job", jid=jid))


@app.route("/crm/oferty/<int:oid>/popraw", methods=["POST"])
def crm_offer_ai_fix(oid):
    offer = db.session.get(SentOffer, oid)
    if not offer or offer.sent_at or not offer.lead_id:
        abort(404)
    user = get_current_user(request)
    feedback = request.form.get("feedback", "")[:2000]
    jid = _start_job("Claude poprawia ofertę", generate_offer_with_ai, offer.lead_id,
                     "Popraw ofertę: " + feedback, offer.sender_id, user["id"], offer.id)
    return redirect(url_for("crm_job", jid=jid))


@app.route("/crm/zadania/<jid>")
def crm_job(jid):
    job = _JOBS.get(jid)
    if not job:
        abort(404)
    if request.args.get("format") == "json":
        return jsonify({k: v for k, v in job.items() if k in ("status", "url", "info", "error", "lead_ids", "draft", "offer")})
    return render_template("crm/job.html", jid=jid, job=job)


# ---------- BLITZKRIEG: jedna wiadomość do wielu leadów, wysyłana powoli ----------
# Każdy mail idzie osobno (jeden adresat, zwykły tekst) ze skrzynki nadawcy, w równych odstępach
# (np. 5/h = co 12 min) i tylko w ustawionych godzinach — żeby nie trafić domeną do spamu.

CAMPAIGN_MAX_RATE = 20
def campaign_default_body(code=None):
    return M(code)["campaign_body"]


def campaign_footer(code=None):
    return M(code)["campaign_footer"]


_CAMPAIGN_LOCK = threading.Lock()


def _first_name(full):
    parts = str(full or "").strip().split()
    return parts[0] if parts else ""


def render_campaign_text(text, rec):
    """Podstawia {imie} i {firma}. Gdy imienia brak, „Dzień dobry {imie},” → „Dzień dobry,”."""
    first = _first_name(rec.name)
    out = re.sub(r"\s?\{imie\}", (" " + first) if first else "", text or "")
    return out.replace("{firma}", rec.company or "")


def _warsaw_now():
    return datetime.now(WARSAW_TZ) if WARSAW_TZ else datetime.now()


def campaign_stats(c):
    counts = {"czeka": 0, "wyslany": 0, "blad": 0, "pominiety": 0}
    for r in c.recipients:
        counts[r.status] = counts.get(r.status, 0) + 1
    per_day = max(1, (c.hour_to or 24) - (c.hour_from or 0)) * c.rate_per_hour
    hours = counts["czeka"] / max(1, c.rate_per_hour)
    return {"counts": counts, "total": len(c.recipients), "hours_left": round(hours, 1),
            "days_left": round(counts["czeka"] / per_day, 1), "interval_min": round(60 / max(1, c.rate_per_hour))}


def _campaign_send_one(c, rec):
    mc = c.market or (lead_market(rec.lead) if rec.lead_id else DEFAULT_MARKET)
    sender = market_sender(USERS_BY_ID.get(c.sender_id, USERS[0]), mc)
    body = render_campaign_text(c.body, rec)
    unsub = {"lt": "NE", "de": "NEIN"}.get(mc, "NIE")
    err = _smtp_send(rec.email, render_campaign_text(c.subject, rec), body, sender["name"], sender=sender,
                     headers={"List-Unsubscribe": "<mailto:{login}?subject=" + unsub + ">"})
    rec.sent_at = datetime.utcnow()
    c.last_sent_at = rec.sent_at
    if err:
        rec.status, rec.error = "blad", err[:300]
        return False
    rec.status = "wyslany"
    if rec.lead_id:
        db.session.add(Activity(lead_id=rec.lead_id, kind="note", done=True, done_at=datetime.utcnow(),
                                assigned_to=sender["id"],
                                notes=f"📣 Blitzkrieg „{c.name}” — wysłano do {rec.email}\nTemat: {c.subject}"))
    return True


def campaign_tick():
    """Wysyła co najwyżej po jednym mailu z każdej trwającej kampanii, jeśli minął odstęp."""
    if not _CAMPAIGN_LOCK.acquire(blocking=False):
        return
    try:
        hour = _warsaw_now().hour
        for c in Campaign.query.filter_by(status="trwa").all():
            if not ((c.hour_from or 0) <= hour < (c.hour_to or 24)):
                continue
            interval = 3600.0 / max(1, min(CAMPAIGN_MAX_RATE, c.rate_per_hour))
            if c.last_sent_at and (datetime.utcnow() - c.last_sent_at).total_seconds() < interval:
                continue
            rec = CampaignRecipient.query.filter_by(campaign_id=c.id, status="czeka") \
                .order_by(CampaignRecipient.id).first()
            if rec is None:
                c.status = "koniec"
            else:
                _campaign_send_one(c, rec)
                if not CampaignRecipient.query.filter_by(campaign_id=c.id, status="czeka").count():
                    c.status = "koniec"
            db.session.commit()
    finally:
        _CAMPAIGN_LOCK.release()


def _campaign_scheduler():
    time.sleep(30)
    while True:
        try:
            with app.app_context():
                campaign_tick()
                db.session.remove()
            health_beat("kampanie")
        except Exception as e:
            app.logger.exception("campaign_scheduler")
            health_fail("kampanie", e)
        time.sleep(60)


_start_worker("campaign", _campaign_scheduler)


CAMPAIGN_AI_TOOL = {
    "name": "zapisz_wiadomosc",
    "description": "Zapisuje temat i treść wiadomości do wysłania wielu klientom.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "body": {"type": "string", "description": "Treść z {imie} w powitaniu ('Dzień dobry {imie},'); bez podpisu firmowego na końcu poza 'Z pozdrowieniami,' + imię i nazwisko"},
        },
        "required": ["subject", "body"],
    },
}


def _campaign_ai_draft(instruction, sender):
    sender = market_sender(sender, current_market())
    system = (
        M()["ai_campaign"] + " Poniższe zasady stosuj, ale tekst ma być w tym języku.\n"
        "Piszesz krótką wiadomość e-mail firmy 33bots (wynajem i sprzedaż robotów humanoidalnych Unitree G1) "
        "wysyłaną do wielu klientów naraz — każdy dostaje ją osobno. Ton profesjonalny i ciepły, krótkie akapity, "
        "bez linii-separatorów, bez nagłówków WIELKIMI LITERAMI, bez kolokwializmów, bez nachalnej sprzedaży. "
        "Możesz użyć {firma} (nazwa firmy podstawi się sama). "
        f"Zakończ: '{M()['bye']}' / {sender['full_name']} / 33Bots / {sender['email']} / {sender['phone']}. "
        "Nigdy nie pisz 'Edward Warchocki' — tylko 'Unitree G1'.\n" + HUMAN_TYPO_RULE
    )
    return _ai_forced_tool(INBOX_AI_MODEL, system, instruction, CAMPAIGN_AI_TOOL, max_tokens=2000)


def _campaign_form_leads():
    return Lead.query.filter(Lead.clickup_pending.isnot(True), own_leads()).order_by(Lead.stage, Lead.company).all()


CAMPAIGN_PICK_MODEL = os.environ.get("CAMPAIGN_PICK_MODEL", OFFER_AI_MODEL)
CAMPAIGN_PICK_TOOL = {
    "name": "wybierz_odbiorcow",
    "description": "Zapisuje leady wybrane jako odbiorcy kampanii.",
    "input_schema": {
        "type": "object",
        "properties": {
            "lead_ids": {"type": "array", "items": {"type": "integer"}, "description": "id wybranych leadów (liczba po #)"},
            "summary": {"type": "string", "description": "1–2 zdania po polsku: wg jakiego kryterium wybrałeś i jakie przypadki były wątpliwe"},
        },
        "required": ["lead_ids", "summary"],
    },
}


def _campaign_lead_line(lead):
    """Jedna linijka o leadzie dla Claude'a: tyle, żeby ocenić typ klienta i etap (z e-maila tylko domena)."""
    domain = (lead.contact_email or "").strip().rsplit("@", 1)[-1].lower()
    days = [ed.event_date for ed in lead.event_days if sane_event_date(ed.event_date)]
    note = re.sub(r"\s+", " ", lead.notes or "").strip()[:220]
    parts = [
        f"#{lead.id}", lead.company or "", lead.contact_name or "", "@" + domain, lead.stage,
        f"źródło: {lead.source}" if lead.source else "", lead.location or "",
        f"{lead.value_pln:g} {lead.currency or M(lead_market(lead))['currency']}" if lead.value_pln else "",
        "wynajem " + ", ".join(f"{d:%d.%m.%Y}" for d in days[:3]) if days else "", note,
    ]
    return " | ".join(p for p in parts if p)


def pick_campaign_leads(audience, code=None):
    """Claude wybiera z leadów z adresem e-mail tych, którzy pasują do opisu grupy (np. „agencje eventowe
    ze zrealizowanych”). Zadanie w tle (_start_job) — zwraca lead_ids i krótkie uzasadnienie.
    Tylko leady rynku kampanii (`code`), bo w tle nie ma filtra rynku."""
    leads = [l for l in _campaign_form_leads() if EMAIL_RE.match((l.contact_email or "").strip())
             and (code is None or lead_market(l) == code)]
    if not leads:
        return {"error": "w CRM nie ma leadów z adresem e-mail"}
    system = (
        "Wybierasz odbiorców kampanii mailowej firmy 33bots (wynajem robotów humanoidalnych Unitree G1 na eventy) "
        "z listy leadów CRM. Etapy: Nowy (zapytanie), Oferta (oferta wysłana), Wygrany (wynajem przed nami), "
        "Zrealizowany (wynajem już się odbył), Stracony. Wybierz WYŁĄCZNIE leady pasujące do opisu użytkownika. "
        "Typ klienta (agencja eventowa, szkoła/przedszkole, urząd/gmina, firma, osoba prywatna itd.) oceniaj po nazwie "
        "firmy, domenie e-maila (gmail/wp/onet itp. = zwykle osoba prywatna) i notatce. Warunki o etapie, mieście, "
        "dacie czy kwocie traktuj ściśle. Gdy nie masz pewności, czy lead pasuje — pomiń go i wspomnij o tym w podsumowaniu."
    )
    text = (f"Opis grupy odbiorców: {audience}\n\n"
            "Leady (id | firma | osoba | domena e-maila | etap | źródło | miejsce | wartość | dni wynajmu | notatka):\n"
            + "\n".join(_campaign_lead_line(l) for l in leads))
    res = _ai_forced_tool(CAMPAIGN_PICK_MODEL, system, text, CAMPAIGN_PICK_TOOL, max_tokens=4000)
    valid, ids = {l.id for l in leads}, []
    for x in res.get("lead_ids") or []:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i in valid and i not in ids:
            ids.append(i)
    return {"lead_ids": ids, "info": str(res.get("summary") or "").strip()[:600]}


@app.route("/crm/blitzkrieg/wybierz", methods=["POST"])
def crm_campaign_pick():
    audience = request.form.get("audience", "").strip()[:500]
    if not audience:
        return jsonify({"error": "Napisz Claude'owi, do kogo ma trafić kampania."}), 400
    jid = _start_job("Claude wybiera odbiorców", pick_campaign_leads, audience, current_market())
    return jsonify({"jid": jid, "poll": url_for("crm_job", jid=jid, format="json")})


@app.route("/crm/blitzkrieg")
def crm_campaigns():
    camps = Campaign.query.order_by(Campaign.created_at.desc()).all()
    return render_template("crm/campaigns.html", camps=camps, stats={c.id: campaign_stats(c) for c in camps})


@app.route("/crm/blitzkrieg/nowa", methods=["GET", "POST"])
def crm_campaign_new():
    user = get_current_user(request)
    f = request.form
    form = {
        "name": f.get("name", ""), "subject": f.get("subject", ""),
        "body": f.get("body", campaign_default_body()).replace("\r\n", "\n"),
        "sender": f.get("sender", user["name"]), "rate": f.get("rate", "5"),
        "hour_from": f.get("hour_from", "8"), "hour_to": f.get("hour_to", "20"),
        "stages": f.getlist("stages"), "lead_ids": {int(x) for x in f.getlist("lead_ids") if x.isdigit()},
        "instruction": f.get("instruction", ""), "footer": f.get("footer", "1" if request.method == "GET" else ""),
        "audience": f.get("audience", ""),
    }
    error = None
    if request.method == "POST":
        sender = USERS_BY_ID.get(_assistant_resolve_user(form["sender"], user["id"]), user)
        if f.get("action") == "ai":
            if not form["instruction"].strip():
                error = "Napisz Claude'owi, o czym ma być wiadomość."
            else:
                try:
                    res = _campaign_ai_draft(form["instruction"], sender)
                    form["subject"] = human_text(str(res.get("subject") or form["subject"]))
                    form["body"] = human_text(str(res.get("body") or form["body"]))
                except Exception as e:
                    error = f"Claude nie napisał wiadomości: {type(e).__name__}: {e}"[:300]
        else:
            try:
                rate = max(1, min(CAMPAIGN_MAX_RATE, int(form["rate"])))
                h_from = max(0, min(23, int(form["hour_from"])))
                h_to = max(h_from + 1, min(24, int(form["hour_to"])))
            except ValueError:
                rate, h_from, h_to = 5, 8, 20
            chosen = {}
            for lead in _campaign_form_leads():
                if lead.stage in form["stages"] or lead.id in form["lead_ids"]:
                    email_addr = (lead.contact_email or "").strip()
                    if EMAIL_RE.match(email_addr) and email_addr.lower() not in chosen:
                        chosen[email_addr.lower()] = lead
            if not form["name"].strip() or not form["subject"].strip() or not form["body"].strip():
                error = "Uzupełnij nazwę kampanii, temat i treść."
            elif not chosen:
                error = "Nie wybrano żadnego leada z adresem e-mail."
            else:
                body = form["body"].strip()
                if form["footer"] and campaign_footer().strip() not in body:
                    body += campaign_footer()
                c = Campaign(name=form["name"].strip()[:200], subject=form["subject"].strip()[:300], body=body,
                             sender_id=sender["id"], rate_per_hour=rate, hour_from=h_from, hour_to=h_to,
                             created_by=user["id"])
                db.session.add(c)
                db.session.flush()
                for email_addr, lead in chosen.items():
                    db.session.add(CampaignRecipient(campaign_id=c.id, lead_id=lead.id, email=lead.contact_email.strip(),
                                                     name=lead.contact_name, company=lead.company))
                db.session.commit()
                return redirect(url_for("crm_campaign", cid=c.id))
    leads = _campaign_form_leads()
    stage_counts = {s: sum(1 for l in leads if l.stage == s and EMAIL_RE.match((l.contact_email or "").strip()))
                    for s in STAGES}
    return render_template("crm/campaign_new.html", form=form, leads=leads, stage_counts=stage_counts,
                           error=error, max_rate=CAMPAIGN_MAX_RATE, email_re=EMAIL_RE)


@app.route("/crm/blitzkrieg/<int:cid>", methods=["GET", "POST"])
def crm_campaign(cid):
    c = db.session.get(Campaign, cid)
    if not c:
        abort(404)
    user = get_current_user(request)
    msg = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "start" and c.status in ("szkic", "pauza"):
            c.status = "trwa"
            c.started_at = c.started_at or datetime.utcnow()
        elif action == "pauza" and c.status == "trwa":
            c.status = "pauza"
        elif action == "usun" and c.status in ("szkic", "pauza", "koniec"):
            db.session.delete(c)
            db.session.commit()
            return redirect(url_for("crm_campaigns"))
        elif action == "usun_odbiorce" and c.status in ("szkic", "pauza"):
            rec = db.session.get(CampaignRecipient, request.form.get("rid", type=int) or 0)
            if rec and rec.campaign_id == c.id and rec.status == "czeka":
                db.session.delete(rec)
        elif action == "test":
            me = USERS_BY_ID.get(user["id"], user)
            if not me.get("email"):
                msg = ("err", "Twoje konto nie ma firmowego e-maila — uzupełnij go w Profilu (menu na dole → profil).")
            else:
                first = next((r for r in c.recipients), None)
                fake = CampaignRecipient(email=me["email"], name=(first.name if first else me["full_name"]),
                                         company=(first.company if first else "33bots"))
                tsender = market_sender(USERS_BY_ID.get(c.sender_id, me), c.market)
                err = _smtp_send(me["email"], "[TEST] " + render_campaign_text(c.subject, fake),
                                 render_campaign_text(c.body, fake), tsender["name"], sender=tsender)
                msg = ("err", f"Test nie wyszedł: {err}") if err else ("ok", f"Test wysłany na {me['email']}.")
        db.session.commit()
        if msg is None:
            return redirect(url_for("crm_campaign", cid=c.id))
    preview_rec = next((r for r in c.recipients if r.status == "czeka"), None) or (c.recipients[0] if c.recipients else None)
    preview = (render_campaign_text(c.subject, preview_rec), render_campaign_text(c.body, preview_rec)) if preview_rec else None
    return render_template("crm/campaign.html", c=c, st=campaign_stats(c), preview=preview, preview_rec=preview_rec,
                           sender=USERS_BY_ID.get(c.sender_id, {}), msg=msg)


# ---------- KONEKTOR MCP (CRM w zwykłym czacie claude.ai, w ramach abonamentu) ----------
# Te same narzędzia co asystent ✨, ale wystawione jako serwer MCP (Streamable HTTP).
# Podpinasz w claude.ai: Ustawienia → Konektory → Dodaj niestandardowy konektor →
# URL: https://<twoja-apka>/mcp/<MCP_SECRET>/<imię> (np. .../mcp/tajnyklucz/oliwier).
# Zero kosztów API — Claude w czacie działa w ramach abonamentu.

_include("konektor_mcp")


# ---------- LITEWSKIE LEADY: automatyczny mail z o.latek@33bots.lt + formularz z danymi do wyceny ----------
# Do litewskich klientów nie dzwonimy. Nowy litewski lead (telefon +370, e-mail .lt, miasto na Litwie)
# dostaje sam, po litewsku, maila ze skrzynki 33bots.lt z prośbą o dane do wyceny i linkiem do krótkiego
# formularza; wpisane dane wpadają prosto do leada. Wysyła się tylko do leadów dodanych po włączeniu
# funkcji (starsze — przyciskiem na karcie leada) i tylko gdy jest skonfigurowana skrzynka LT
# (SMTP_PASS_LT, opcjonalnie SMTP_USER_LT / SMTP_HOST_LT / SMTP_PORT_LT).

LT_CITIES = ("vilnius", "kaunas", "klaipėda", "klaipeda", "šiauliai", "siauliai", "panevėžys", "panevezys",
             "alytus", "marijampolė", "marijampole", "palanga", "druskininkai", "utena", "jonava", "mažeikiai",
             "mazeikiai", "lietuva", "lithuania", "litwa", "litauen", "wilno", "kowno", "kłajpeda")
LT_SINCE_KEY = "lt_mail_since"
LT_POLL_SECONDS = 60  # nowy litewski lead dostaje maila najpóźniej minutę po wpadnięciu do CRM


def lt_sender(code=None):
    """Nadawca automatycznych maili do klientów rynku: skrzynka o.latek@33bots.lt / .de (wspólne hasło MAIL_PASSWORD
    albo SMTP_USER_LT / SMTP_PASS_LT itd.)."""
    code = code or current_market()
    owner = USERS_BY_ID.get(1) or USERS[0]
    box = mail_box(markets.get(code)["own_email"])
    s = {"id": owner["id"], "name": owner["name"], "full_name": owner.get("full_name") or owner["name"],
         "email": markets.get(code)["own_email"], "phone": owner.get("phone") or "", "market": code}
    if box and box["pwd"]:
        s.update(login=box["login"], pwd=box["pwd"], email=box["login"])
    elif code == "lt" and os.environ.get("SMTP_PASS_LT", "").strip():  # dawne ustawienie (klucz „LT”)
        s.update(name="LT", email=os.environ.get("SMTP_USER_LT", "").strip() or s["email"])
    return s


def lt_mailbox_ready(code=None):
    s = lt_sender(code)
    return bool(s.get("pwd") or (s["name"] == "LT" and os.environ.get("SMTP_PASS_LT", "").strip()))


def is_lithuanian(lead):
    """Zostawione dla zgodności szablonów: każdy lead ma swój rynek (Litwa / Niemcy) i dostaje maile w jego języku."""
    return lead is not None


_PUBLIC_BASE = {"url": ""}


PUBLIC_BASE_KEY = "public_base_url"
BAD_LINK_MARK = "WPISZ_ADRES"  # zostało z wzoru zmiennych — takiego adresu nigdy nie wysyłamy
_BASE_RE = re.compile(r"^https?://(?!localhost\b|127\.|0\.0\.0\.0|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)"
                      r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(:\d+)?$")


def valid_public_base(url):
    """Czy to prawdziwy publiczny adres CRM (https://….up.railway.app), a nie wzór, localhost albo adres wewnętrzny."""
    url = (url or "").strip().rstrip("/")
    return bool(_BASE_RE.match(url)) and ".internal" not in url and BAD_LINK_MARK not in url.upper()


@app.before_request
def _remember_public_base():
    """Adres, pod którym ludzie otwierają CRM — do linków w mailach, gdy PUBLIC_URL nie jest ustawiony poprawnie."""
    if _PUBLIC_BASE["url"]:
        return
    base = request.url_root.rstrip("/")
    if base.startswith("http://") and base.split("/")[2].endswith((".up.railway.app", ".railway.app")):
        base = "https://" + base[len("http://"):]
    if valid_public_base(base):
        _PUBLIC_BASE["url"] = base
        try:
            if get_setting(PUBLIC_BASE_KEY) != base:
                set_setting(PUBLIC_BASE_KEY, base)
                db.session.commit()
        except Exception:
            db.session.rollback()


def public_base_url():
    """Adres CRM do linków w mailach: poprawny PUBLIC_URL, inaczej zapamiętany adres strony; pusty = nie wysyłaj linków."""
    env = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
    if valid_public_base(env):
        return env
    if _PUBLIC_BASE["url"]:
        return _PUBLIC_BASE["url"]
    try:
        saved = get_setting(PUBLIC_BASE_KEY)
    except Exception:
        saved = ""
    if valid_public_base(saved):
        _PUBLIC_BASE["url"] = saved
        return saved
    return ""


def lead_form_url(lead):
    if not lead.form_token:
        lead.form_token = secrets.token_urlsafe(18)
    base = public_base_url()
    return f"{base}/f/{lead.form_token}" if base else ""


def lt_mail_draft(lead):
    code = lead_market(lead)
    m = markets.get(code)
    sender = lt_sender(code)
    link = lead_form_url(lead)
    form = m["ask_form"].format(link=link) if link else m["ask_noform"]
    body = (m["ask_hello"] + form + m["ask_list"] + m["ask_bye"] + "\n"
            f"{sender['full_name']}\n33Bots\n{sender['email']}" + (f"\n{mail_phone(sender['phone'], code)}"
                                                                      if mail_phone(sender["phone"], code) else ""))
    return sender, human_text(m["ask_subject"]), human_text(body)


LT_ALREADY_SENT = "już wysłany"


def _claim_lt_mail(lead_id):
    """Atomowo rezerwuje wysyłkę (lt_mail_at z NULL na teraz). False = ktoś (automat albo drugie
    kliknięcie) właśnie wysłał — wtedy nie wysyłamy drugi raz."""
    res = db.session.execute(db.update(Lead).where(Lead.id == lead_id, Lead.lt_mail_at.is_(None))
                             .values(lt_mail_at=datetime.utcnow()))
    db.session.commit()
    return res.rowcount == 1


def send_lt_mail(lead, actor_id=None, resend=False):
    """Wysyła mail LT do wszystkich adresów leada. Zwraca błąd albo None (i zapisuje w leadzie).
    Bez resend=True wysyła tylko, jeśli mail jeszcze nie poszedł (zwraca LT_ALREADY_SENT)."""
    if not lead.emails:
        return "lead nie ma adresu e-mail"
    if not lt_mailbox_ready(lead_market(lead)):
        return (f"skrzynka {markets.get(lead_market(lead))['own_email']} nie jest podpięta — ustaw MAIL_PASSWORD "
                "(albo SMTP_USER_/SMTP_PASS_ tej skrzynki) w Railway")
    lead_id = lead.id
    if not resend and not _claim_lt_mail(lead_id):
        return LT_ALREADY_SENT
    lead = db.session.get(Lead, lead_id)
    sender, subject, body = lt_mail_draft(lead)
    to_addr = ", ".join(lead.emails)
    err = _smtp_send(to_addr, subject, body, sender["name"], sender=sender)
    if err:
        if not resend:  # zwolnij rezerwację — automat spróbuje ponownie
            db.session.execute(db.update(Lead).where(Lead.id == lead_id).values(lt_mail_at=None))
            db.session.commit()
        return err
    lead.lt_mail_at = datetime.utcnow()
    db.session.add(Activity(
        lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(),
        assigned_to=actor_id or lead.assigned_to or sender["id"],
        notes=f"{markets.get(lead_market(lead))['flag']} Mail z prośbą o dane do wyceny wysłany do {to_addr} (od: {sender['email']})\n"
              f"Temat: {subject}\n---\n{body}"))
    db.session.commit()
    return None


LT_NO_QUOTE = "brak danych do wyceny"
LT_QUOTE_SENT_KEY = "lt_quote_sent:{}"


def lt_auto_quote_enabled(code="lt"):
    """Czy wycena wychodzi do klienta SAMA (automat dla nowych leadów i po wypełnieniu formularza).
    Litwa: tak (LT_AUTO_QUOTE=0 wyłącza). Niemcy: nigdy — CRM tylko generuje mail, wysyła człowiek."""
    if not markets.get(code)["auto_send"]:
        return False
    return (os.environ.get("LT_AUTO_QUOTE", "").strip() or markets.get(code)["auto_quote_default"]) != "0"


def lt_quote_sender(lead):
    """Nadawca wyceny: skrzynka opiekuna leada w domenie rynku (o.latek@33bots.lt / .de, b.wysocki@…),
    LT_QUOTE_SENDER wymusza osobę, bez skrzynki osoby — kontakt@ rynku."""
    code = lead_market(lead)
    forced = os.environ.get("LT_QUOTE_SENDER", "").strip().lower()
    user = (next((u for u in USERS if u["name"].lower() == forced), None) if forced else None) or \
        USERS_BY_ID.get(lead.assigned_to) or USERS[0]
    s = market_sender(user, code)
    if s.get("login"):
        return s
    return lt_sender(code) if lt_mailbox_ready(code) else None


def _when_phrase(when, code):
    """Data do zdania z ceną: LT ' 2026-12-12', DE ' am 12.12.2026' / ' vom 12.–14.12.2026' (kilka dni z rzędu)."""
    if not when:
        return ""
    if code == "lt":
        return " " + when
    return f" vom {when}" if "–" in when and "," not in when else f" am {when}"


def lt_quote_mail_draft(lead, q, sender, fixed=False):
    """Mail z ofertą (PDF w załączniku): cena, co robot robi na wydarzeniu, co jest w cenie, zdjęcia. Bez formularzy."""
    m = markets.get(lead_market(lead))
    when = q.get("when") or ""
    price_txt = f"{q['price']:,}".replace(",", " ")
    subject = m["quote_subject"] + (f" {when}" if when else "")
    lines = [m["quote_hello"], "", m["quote_fixed"] if fixed else m["quote_thanks"], "",
             m["quote_price"].format(when=_when_phrase(when, m["code"]), time=q["time"], place=q["place"], price=price_txt), "",
             m["quote_does"], "", m["quote_included"], "", m["quote_photos"], OFFER_PHOTOS_URL, "",
             m["quote_reserve"], "", m["bye"],
             "\n".join(x for x in (sender.get("full_name") or sender.get("name"), "33Bots", sender.get("email"),
                                   mail_phone(sender.get("phone"), m["code"])) if x)]
    return human_text(subject), human_text("\n".join(lines))


def auto_offer_pdf(lead, q, sender):
    """PDF oferty (ten sam wygląd co oferty z CRM) w języku rynku leada: (dane, nazwa pliku, bajty)."""
    m = markets.get(lead_market(lead))
    o = m["offer_pdf"]
    price_txt = f"{q['price']:,}".replace(",", " ") + " €"
    when = q.get("when") or o["soon"]
    data = offers.normalize({
        "language": m["lang"], "client": (lead.company or "")[:80] or "33Bots", "headline": o["headline"],
        "subtitle": o["subtitle"],
        "cover": [{"label": o["cover"][0], "value": when[:34]}, {"label": o["cover"][1], "value": (q["place"] or "")[:34]},
                  {"label": o["cover"][2], "value": (q["time"] or "")[:34]}],
        "intro": o["intro"],
        "cards": [{"label": lab, "title": title, "items": items} for lab, title, items in o["cards"]],
        "price_title": o["price_title"], "price_sub": " · ".join(x for x in (q["place"], q.get("when")) if x)[:90],
        "price": price_txt,
    })
    pdf = offers.render_offer_pdf(data, (sender.get("full_name") or sender.get("name") or "", sender.get("email") or "",
                                         sender.get("phone") or ""))
    return data, offers.offer_filename(data["client"]), pdf


def lt_quote_auto_ok(q, code="lt"):
    """Czy wycenę można wysłać bez człowieka: znane godziny (nie założone), ≤ 8 h, rozsądna odległość od granicy."""
    return bool(q.get("price")) and not q.get("assumed") and not q.get("over8") and \
        0 <= q.get("km", 99999) <= markets.get(code)["max_km_auto"]


def send_lt_quote_mail(lead, actor_id=None, force=False, subject=None, body=None, fixed=False):
    """Automatyczna wycena mailem ze skrzynki rynku leada (33bots.lt / .de). Zwraca None (wysłano), LT_NO_QUOTE (brak danych —
    użyj formularza), LT_ALREADY_SENT albo opis błędu."""
    if not lead.emails:
        return "lead nie ma adresu e-mail"
    if get_setting(LT_QUOTE_SENT_KEY.format(lead.id)) and not force:
        return LT_ALREADY_SENT
    if not lead.client_type:
        if actor_id is None or fixed:  # automat / poprawka oferty, która już raz poszła — typ z podpowiedzi
            lead.client_type = client_type_hint(lead)[0]
        else:
            type_err = offer_client_type_error(lead)
            if type_err:
                return type_err
    with app.test_request_context(base_url=public_base_url() or "http://localhost"):
        q = lt_quote(lead, remote_budget=2)
    if not lt_quote_auto_ok(q, lead_market(lead)) and not force:
        return LT_NO_QUOTE
    if not q.get("price"):
        return LT_NO_QUOTE
    sender = lt_quote_sender(lead)
    if not sender:
        return f"brak skrzynki {markets.get(lead_market(lead))['domain']} (MAIL_PASSWORD albo SMTP_USER_/SMTP_PASS_ w Railway)"
    lead_id = lead.id
    if lead.lt_mail_at is None and not _claim_lt_mail(lead_id):
        return LT_ALREADY_SENT
    lead = db.session.get(Lead, lead_id)
    draft_subject, draft_body = lt_quote_mail_draft(lead, q, sender, fixed=fixed)
    subject = (subject or "").strip()[:300] or draft_subject  # człowiek mógł poprawić wygenerowany mail
    body = (body or "").replace("\r\n", "\n").strip()[:10000] or draft_body
    if BAD_LINK_MARK in body.upper() or re.search(r"https?://\S+/f/[A-Za-z0-9_-]{16,}", body):
        return "w treści jest link do formularza — usuń go przed wysłaniem"
    to_addr = ", ".join(lead.emails)
    try:
        offer_data, pdf_name, pdf = auto_offer_pdf(lead, q, sender)
    except Exception as e:
        db.session.execute(db.update(Lead).where(Lead.id == lead_id, Lead.lt_mail_at.isnot(None),
                                                 Lead.stage == "Nowy").values(lt_mail_at=None))
        db.session.commit()
        return f"PDF oferty się nie wygenerował: {type(e).__name__}: {e}"[:200]
    err = _smtp_send(to_addr, subject, body, sender["name"], attachments=[(pdf_name, pdf, "application/pdf")],
                     sender=sender)
    if err:
        db.session.execute(db.update(Lead).where(Lead.id == lead_id, Lead.lt_mail_at.isnot(None),
                                                 Lead.stage == "Nowy").values(lt_mail_at=None))
        db.session.commit()
        return err
    now = datetime.utcnow()
    actor = actor_id or lead.assigned_to or sender["id"]
    lead.lt_mail_at = lead.lt_mail_at or now
    lead.value_pln, lead.currency = float(q["price"]), "EUR"
    set_setting(LT_QUOTE_SENT_KEY.format(lead_id), now.isoformat(timespec="seconds"))
    db.session.add(SentOffer(lead_id=lead_id, filename=pdf_name, pdf=pdf, to_addr=to_addr, subject=subject, body=body,
                             price=offer_data["price"], sender_id=sender.get("id"), sent_at=now,
                             data=json.dumps({"auto": True, "breakdown": q.get("breakdown")}, ensure_ascii=False)))
    db.session.add(Activity(
        lead_id=lead_id, kind="email", done=True, done_at=now, assigned_to=actor,
        notes=f"💶 {'Oferta' if actor_id else 'Automatyczna oferta'} {q['price']} € (mail + PDF) wysłana do {to_addr} (od: {sender['email']})\n"
              f"Temat: {subject}\n🧮 {q['breakdown']}\n---\n{body}"))
    if lead.stage == "Nowy":
        record_stage_change(lead, "Oferta", actor)
        db.session.flush()
        # do Litwinów nie dzwonimy — drugi krok to follow-up mailem
        for a in Activity.query.filter(Activity.lead_id == lead_id, Activity.kind == "call",
                                       Activity.done.isnot(True)).all():
            if (a.notes or "").startswith(AUTO_CALL_PREFIX):
                a.kind = "email"
                a.due_date = datetime.combine(to_local(now).date() + timedelta(days=3), datetime.min.time())
                a.notes = f"Follow-up mailem po wycenie {q['price']} € z {to_local(now):%d.%m}."
    for a in Activity.query.filter(Activity.lead_id == lead_id, Activity.done.isnot(True),
                                   Activity.notes.like("Przygotuj i wyślij ofertę%")).all():
        a.done, a.done_at = True, now  # oferta poszła automatycznie
    db.session.commit()
    return None


def _send_quote_in_background(lead_id):
    def run():
        time.sleep(2)
        with app.app_context():
            try:
                apply_user_profiles()
                lead = db.session.get(Lead, lead_id)
                if lead and lead.stage in ("Nowy", "Oferta") and not get_setting(LT_QUOTE_SENT_KEY.format(lead_id)):
                    err = send_lt_quote_mail(lead)
                    if err and err not in (LT_NO_QUOTE, LT_ALREADY_SENT):
                        db.session.add(Activity(lead_id=lead_id, kind="note", done=True, done_at=datetime.utcnow(),
                                                assigned_to=lead.assigned_to or USERS[0]["id"],
                                                notes=f"⚠️ Automatyczna wycena po formularzu nie wyszła: {err}"))
                        db.session.commit()
            except Exception:
                db.session.rollback()
                app.logger.exception("lt_quote_mail")
            finally:
                db.session.remove()
    threading.Thread(target=run, daemon=True).start()


def quote_mail_preview(lead, q):
    """Wygenerowany mail z wyceną do podglądu w leadzie (temat, treść, nadawca) albo None (brak ceny / skrzynki)."""
    if not q or not q.get("price"):
        return None
    sender = lt_quote_sender(lead)
    if not sender:
        return None
    subject, body = lt_quote_mail_draft(lead, q, sender)
    return {"subject": subject, "body": body, "from": sender["email"],
            "sent_at": get_setting(LT_QUOTE_SENT_KEY.format(lead.id)) or ""}


app.jinja_env.globals.update(quote_mail_preview=quote_mail_preview)


BADLINK_FIXED_KEY = "badlink_fixed:{}"


_BADLINK_ADDR_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_badlink_cache = {}  # rynek -> (czas, wynik) — lista liczy się z treści maili, okno alarmów pyta o nią na każdej stronie
BADLINK_DAYS = 60  # błędny link pojawił się niedawno — starszych maili nie przeszukujemy


def _badlink_scan(code):
    """Leady rynku `code`, do których ze skrzynki FAKTYCZNIE wyszedł mail z błędnym linkiem (adres-wzór z listy
    zmiennych) — oferta z ceną albo prośba o dane. Źródło to folder Wysłane w Poczcie (to, co klient naprawdę dostał),
    nie notatki w CRM. Bez leadów, które dostały już poprawkę albo zostały pominięte. Zamknięty lead albo taki, z którym
    po tym mailu był już kontakt, zostaje na liście z ostrzeżeniem (i nie idzie w „Wyślij wszystkim”)."""
    since = datetime.utcnow() - timedelta(days=BADLINK_DAYS)
    mails = (all_markets(MailMessage.query.filter(MailMessage.folder == "sent", MailMessage.date >= since,
                                                  MailMessage.body_text.like(f"%{BAD_LINK_MARK}%")))
             .order_by(MailMessage.date).all())
    found = {}
    for m in mails:
        for addr in _BADLINK_ADDR_RE.findall(f"{m.to_addrs or ''} {m.cc_addrs or ''}"):
            addr = addr.lower()
            lead = (all_markets(Lead.query.filter(Lead.market == code, db.or_(
                db.func.lower(db.func.trim(Lead.contact_email)) == addr,
                db.func.lower(db.func.trim(Lead.contact_email2)) == addr)))
                .order_by(Lead.created_at.desc()).first())
            if lead is not None and lead.id not in found:
                found[lead.id] = (lead, m, addr)
    out = []
    for lead, m, addr in found.values():
        if get_setting(BADLINK_FIXED_KEY.format(lead.id)):
            continue
        when = m.date or datetime.min
        warn = []
        if lead.stage in ("Wygrany", "Zrealizowany", "Stracony"):
            warn.append(f"lead jest już „{lead.stage}”")
        q = mail_query_for_addrs(lead.emails)
        if q is not None:
            reply = all_markets(q.filter(MailMessage.folder == "in", MailMessage.date > when)
                                .order_by(MailMessage.date)).first()
            if reply is not None:
                warn.append(f"klient odpisał {to_local(reply.date):%d.%m %H:%M}")
            other = all_markets(q.filter(MailMessage.folder == "sent", MailMessage.date > when, MailMessage.id != m.id,
                                         db.not_(MailMessage.body_text.like(f"%{BAD_LINK_MARK}%")))
                                .order_by(MailMessage.date)).first()
            if other is not None:
                warn.append(f"po tym mailu poszedł już inny: „{(other.subject or '')[:60]}” {to_local(other.date):%d.%m %H:%M}")
        call = all_markets(Activity.query.filter(Activity.lead_id == lead.id, Activity.done.is_(True),
                                                 Activity.kind.in_(("call", "meeting")),
                                                 db.or_(Activity.done_at > when, Activity.created_at > when))).first()
        if call is not None:
            warn.append("po tym mailu był telefon / spotkanie")
        out.append((lead, {"when": to_local(m.date) if m.date else None, "to": addr, "box": m.mailbox,
                           "subject": m.subject_pl or m.subject or "", "mail_id": m.id, "warn": warn}))
    return out


def bad_link_leads(code=None, with_evidence=False, max_age=120):
    """Lista z _badlink_scan (z pamięci podręcznej na 2 minuty). with_evidence=True → [(lead, dowód)], inaczej [lead]."""
    code = code or current_market()
    hit = _badlink_cache.get(code)
    if hit is None or time.time() - hit[0] > max_age:
        hit = (time.time(), [(l.id, ev) for l, ev in _badlink_scan(code)])
        _badlink_cache[code] = hit
    out = []
    for lead_id, ev in hit[1]:
        lead = db.session.get(Lead, lead_id)
        if lead is not None:
            out.append((lead, ev) if with_evidence else lead)
    return out


def send_badlink_fix(lead, actor_id):
    """Poprawka dla klienta z błędnym linkiem: pełna oferta z PDF (gdy da się policzyć cenę). Zwraca błąd albo None."""
    err = send_lt_quote_mail(lead, actor_id=actor_id, force=True, fixed=True)
    if err in (LT_NO_QUOTE, LT_ALREADY_SENT):
        return "brak godzin albo miejsca — nie da się policzyć ceny; napisz do klienta ręcznie (✉️ Napisz)"
    if err:
        return err
    set_setting(BADLINK_FIXED_KEY.format(lead.id), datetime.utcnow().isoformat(timespec="seconds"))
    db.session.commit()
    _badlink_cache.clear()
    return None


@app.route("/crm/poprawki")
def crm_badlink_list():
    rows = []
    for l, ev in bad_link_leads(with_evidence=True, max_age=0):
        q = lt_quote(l, remote_budget=1)
        rows.append({"lead": l, "price": q.get("price"), "missing": q.get("missing") or [], "ev": ev,
                     "ok": bool(q.get("price")) and not ev["warn"]})
    return render_template("crm/poprawki.html", rows=rows, msg=request.args.get("msg", ""))


@app.route("/crm/poprawki/<int:lead_id>", methods=["POST"])
def crm_badlink_fix(lead_id):
    lead = db.session.get(Lead, lead_id) or abort(404)
    err = send_badlink_fix(lead, get_current_user(request)["id"])
    if err:
        db.session.rollback()
    return redirect(url_for("crm_badlink_list", msg=(f"⚠️ {lead.company}: {err}" if err else f"✅ {lead.company}: poprawiona oferta z PDF wysłana")))


@app.route("/crm/poprawki/<int:lead_id>/pomin", methods=["POST"])
def crm_badlink_skip(lead_id):
    lead = db.session.get(Lead, lead_id) or abort(404)
    set_setting(BADLINK_FIXED_KEY.format(lead.id), "pominięty " + datetime.utcnow().isoformat(timespec="seconds"))
    db.session.commit()
    _badlink_cache.clear()
    return redirect(url_for("crm_badlink_list", msg=f"{lead.company}: pominięty — nic nie wysłano"))


@app.route("/crm/poprawki/wszystkim", methods=["POST"])
def crm_badlink_fix_all():
    user_id = get_current_user(request)["id"]
    ids = [l.id for l, ev in bad_link_leads(with_evidence=True, max_age=0) if not ev["warn"]]  # ostrzeżone — tylko ręcznie

    def job():
        ok, failed = 0, []
        for lid in ids:
            lead = db.session.get(Lead, lid)
            err = send_badlink_fix(lead, user_id) if lead else "brak leada"
            if err:
                db.session.rollback()
                failed.append(f"{lead.company if lead else lid}: {err}")
            else:
                ok += 1
        return {"info": (f"Wysłano poprawionych ofert: {ok}" + (f"; nie wyszło {len(failed)}: " + "; ".join(failed[:5])
                                                                  if failed else ""))[:600], "url": "/crm/poprawki"}
    return redirect(url_for("crm_job", jid=_start_job("Poprawione oferty z PDF", job)))


@app.route("/crm/leads/<int:lead_id>/oferta-auto.pdf")
def crm_lt_offer_pdf(lead_id):
    """Podgląd PDF oferty, który idzie w załączniku maila z wyceną."""
    lead = db.session.get(Lead, lead_id) or abort(404)
    q = lt_quote(lead, remote_budget=1)
    sender = lt_quote_sender(lead) or lt_sender(lead_market(lead))
    if not q.get("price"):
        abort(404)
    _data, name, pdf = auto_offer_pdf(lead, q, sender)
    resp = make_response(pdf)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f"inline; filename*=UTF-8''{quote(name)}"
    return resp


@app.route("/crm/leads/<int:lead_id>/lt-wycena-mail", methods=["POST"])
def crm_lt_quote_mail(lead_id):
    """Ręcznie: wyślij wycenę mailem teraz (np. gdy automat czekał na dane albo po zmianie)."""
    lead = db.session.get(Lead, lead_id) or abort(404)
    err = send_lt_quote_mail(lead, actor_id=get_current_user(request)["id"], force=True,
                             subject=request.form.get("subject"), body=request.form.get("body"))
    if err == LT_NO_QUOTE:
        err = "brak godzin albo miejsca — uzupełnij je w leadzie"
    return redirect(url_for("crm_lead_detail", lead_id=lead.id, msg=("⚠️ " + err) if err else "✅ Wycena wysłana mailem"))


def lt_auto_mail():
    """Nowe litewskie leady (od włączenia funkcji) bez wysłanego maila — wyślij: od razu wycenę, gdy znamy
    godziny i miejsce, inaczej prośbę o dane (formularz). Zwraca liczbę wysłanych."""
    if os.environ.get("LT_AUTO_MAIL", "1") == "0" or not any(lt_mailbox_ready(mc) for mc in markets.MARKETS):
        return 0
    apply_user_profiles()  # w wątku w tle nie ma before_request — dane nadawców z Profilu
    since = get_setting(LT_SINCE_KEY)
    if not since:  # pierwsze uruchomienie: od teraz — starszych leadów nie ruszamy
        set_setting(LT_SINCE_KEY, datetime.utcnow().isoformat(timespec="seconds"))
        db.session.commit()
        return 0
    since_dt = datetime.fromisoformat(since)
    sent = 0
    for lead in Lead.query.filter(Lead.created_at >= since_dt, Lead.lt_mail_at.is_(None), Lead.stage == "Nowy",
                                  Lead.clickup_pending.isnot(True)  # czeka w „Z ClickUp” — nic do klienta przed akceptacją
                                  ).order_by(Lead.created_at).limit(50).all():
        mc = lead_market(lead)
        if not lead.emails or not lt_mailbox_ready(mc):
            continue
        if not markets.get(mc)["auto_send"]:
            continue  # Niemcy: żadnych automatycznych maili do klienta — wycena czeka wygenerowana w leadzie
        tries_key = f"lt_tries:{lead.id}"
        tries = int(get_setting(tries_key) or 0)
        if tries >= 3:
            continue
        if not lt_auto_quote_enabled(mc):
            continue
        err = send_lt_quote_mail(lead)
        if err in (LT_NO_QUOTE, LT_ALREADY_SENT):
            continue  # brak godzin / miejsca — nic nie wychodzi samo; lead czeka w oknie alarmu na człowieka
        if err:
            db.session.rollback()
            set_setting(tries_key, str(tries + 1))
            db.session.add(Activity(lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(),
                                    assigned_to=lead.assigned_to or USERS[0]["id"],
                                    notes=f"⚠️ Automatyczny mail ({markets.get(mc)['name']}) nie wyszedł (próba {tries + 1}/3): {err}"))
            db.session.commit()
        else:
            sent += 1
    return sent


def lt_backlog(code=None):
    """Starsze leady rynku (domyślnie oglądanego; etap Nowy, z e-mailem), które nie dostały jeszcze maila
    z prośbą o dane. Rynek podany wprost — działa też w wątku w tle (bez filtra zapytania)."""
    code = code or current_market()
    return [l for l in Lead.query.filter(Lead.stage == "Nowy", Lead.lt_mail_at.is_(None), Lead.market == code)
            .order_by(Lead.created_at).all() if l.emails]


def _lt_scheduler():
    time.sleep(40)
    while True:
        try:
            with app.app_context():
                lt_auto_mail()
                db.session.remove()
            health_beat("lt_mail")
        except Exception as e:
            app.logger.exception("lt_scheduler")
            health_fail("lt_mail", e)
        time.sleep(LT_POLL_SECONDS)


if os.environ.get("LT_AUTO_MAIL", "1") != "0":
    _start_worker("lt", _lt_scheduler)


app.jinja_env.globals.update(is_lithuanian=is_lithuanian, lt_mailbox_ready=lt_mailbox_ready, lt_backlog=lt_backlog,
                             lt_sender=lt_sender, lt_auto_quote_enabled=lt_auto_quote_enabled)


# ---------- CENA Z FORMULARZA + GOTOWA WIADOMOŚĆ NA WHATSAPP / VIBER ----------
# Z formularza LT znamy godziny („Godziny: 14:00–18:00” w notatce), miejsce i dni. Z tego kalkulator liczy cenę
# (pakiet 2 / 4 / 8 h, km od granicy, nocleg), zaokrągloną do pełnych setek (od 25 € w górę), a CRM składa
# gotową wiadomość po litewsku do skopiowania albo otwarcia w WhatsApp / Viber.
_LT_FORM_HOURS = re.compile(r"Godziny:\s*(\d{1,2}(?:[:.]\d{2})?)\s*[–-]\s*(\d{1,2}(?:[:.]\d{2})?)")
# godziny i długość z treści zapytania — po polsku, litewsku, niemiecku i angielsku
_LT_RANGE_COLON = re.compile(r"(?<![\d:])(\d{1,2}:\d{2})\s*(?:Uhr\s*)?(?:-|–|—|do|iki|to|bis)\s*(\d{1,2}:\d{2})(?![\d:])", re.I)
_LT_RANGE_WORDS = re.compile(r"\b(?:od|nuo|from|von|ab)\s*(\d{1,2}(?:[:.]\d{2})?)\s*(?:Uhr\s*)?(?:do|iki|to|bis|-|–)\s*"
                             r"(\d{1,2}(?:[:.]\d{2})?)\b", re.I)
_LT_RANGE_UNIT = re.compile(r"(?<![\d.:])(\d{1,2})\s*(?:Uhr\s*)?(?:[-–]|bis)\s*(\d{1,2})\s*(?:val|h\b|godz|Uhr)", re.I)
_LT_MORE_THAN = re.compile(r"(?:daugiau\s+nei|ponad|wi[eę]cej\s+ni[zż]|more\s+than|mehr\s+als|über)\s*(\d{1,2})\s*"
                           r"(?:val|h\b|godz|hour|Std|Stunde)", re.I)
_LT_DURATION = re.compile(r"(?<![\d:.])(\d{1,2}(?:[.,]5)?)\s*(?:val\w*|h\b|godz\w*|hours?\b|Std\b\.?|Stunden?\b|-?stündig\w*)", re.I)
_LT_FULL_DAY = re.compile(r"ca[lł]y\s+dzie[nń]|vis[aą]\s+dien[aą]|all\s+day|full\s+day|ganzt[aä]gig|ganzen\s+tag|ganzer\s+tag",
                          re.I)
_LT_PLACE = re.compile(r"(?<![^\W\d_])(?:Miejsce(?:\s+wydarzenia)?|Vieta|Renginio\s+vieta|Veranstaltungsort|Ort|Location)\s*[:\-–]\s*"
                       r"([^\n;]+)", re.I)
LT_DEFAULT_HOURS = float(os.environ.get("LT_DEFAULT_HOURS", "4") or 4)


def _lt_texts(lead):
    """Teksty leada do szukania danych: notatki, potem komentarze i zadania (od najnowszych)."""
    acts = sorted(lead.activities, key=lambda a: a.created_at or datetime.min, reverse=True)
    return [(lead.notes or "").replace("_", " ")] + [(a.notes or "").replace("_", " ") for a in acts]


def lt_extract_hours(lead):
    """(start albo None, koniec albo None, długość w h, skąd) — z formularza, z godzin w treści
    („18:00–22:00”, „od 18 do 22”, „nuo 18 iki 22”) albo z samej długości („iki 4 valandų”, „do 4 godzin”)."""
    texts = _lt_texts(lead)

    def rng(a, b, src):
        s, e = pricing.parse_start(a), pricing.parse_start(b)
        if s is None or e is None:
            return None
        dur = (e - s) % 24 or 24
        return (s, e, dur, src) if 0.5 <= dur <= 16 else None

    for rx, src in ((_LT_FORM_HOURS, "formularz"), (_LT_RANGE_COLON, "treść"), (_LT_RANGE_WORDS, "treść"),
                    (_LT_RANGE_UNIT, "treść")):
        for text in texts:
            found = rx.findall(text)
            for a, b in reversed(found):
                got = rng(a, b, src)
                if got:
                    return got
    for text in texts:
        if _LT_FULL_DAY.search(text):
            return None, None, 8.0, "treść (cały dzień)"
        m = _LT_MORE_THAN.search(text)
        if m:
            return None, None, float(m.group(1)) + 0.5, "treść"
        for m in _LT_DURATION.finditer(text):
            dur = float(m.group(1).replace(",", "."))
            if 0.5 <= dur <= 16:
                return None, None, dur, "treść"
    return None, None, LT_DEFAULT_HOURS, "założone"


def client_place(text, code):
    """Miejsce do tekstu dla klienta w jego języku: 'Wilno' → 'Vilnius', 'Monachium' → 'München'.
    Dłuższy opis (adres, hala) zostaje bez zmian."""
    norm = logistics.normalize(text)
    key = logistics.CITY_ALIASES.get(norm, norm)
    return markets.CITY_NATIVE.get(code, {}).get(key) or (text or "").strip()


def lt_extract_place(lead):
    """Miejsce wydarzenia (formularz, lokalizacja leada, „Miejsce:/Vieta:/Ort:” w treści albo znane miasto
    w treści) — nazwą w języku klienta."""
    code = lead_market(lead)
    place = _lead_place(lead)
    if place:
        return client_place(place, code)
    for text in _lt_texts(lead):
        m = _LT_PLACE.search(text)
        if m:
            val = re.sub(r"[\s.,]*\d{4}[\s./-]\d{1,2}[\s./-]\d{1,2}.*$", "", m.group(1)).strip(" .,-–")
            if val:
                return client_place(val[:200], code)
    for text in _lt_texts(lead):
        hit = logistics.match_city(text)
        if hit:
            return markets.CITY_NATIVE.get(code, {}).get(hit[0]) or logistics.city_label(hit[0])
    return ""


def lt_form_hours(lead):  # zgodność wsteczna
    s, e, dur, src = lt_extract_hours(lead)
    return (s, e, dur) if s is not None else None


def lt_offer_message(lead, when, time_txt, place, price_txt, code=None):
    m = markets.get(code or lead_market(lead))
    owner = (USERS_BY_ID.get(lead.assigned_to) or USERS[0]) if lead else USERS[0]
    when_txt = _when_phrase(when, m["code"])
    lines = [m["msg_hello"], "", m["msg_price"].format(when=when_txt, time=time_txt, place=place, price=price_txt), "",
             m["msg_included"], "", m["msg_reserve"], "", f"{owner.get('full_name') or owner['name']}, 33bots"]
    return human_text("\n".join(lines))


def lt_welcome_message(lead, when, code=None):
    """Pierwsza wiadomość na Viber / WhatsApp: powitanie od handlowca i pytanie o wydarzenie, bez ceny (cena w rozmowie)."""
    m = markets.get(code or lead_market(lead))
    owner = (USERS_BY_ID.get(lead.assigned_to) or USERS[0]) if lead else USERS[0]
    full = owner.get("full_name") or owner["name"]
    lines = [m["msg_hello"], "", m["msg_intro"].format(name=full.split()[0], when=_when_phrase(when, m["code"])), "",
             m["msg_question"], "", m["msg_bye"], f"{full}, 33bots"]
    return human_text("\n".join(lines))


def _lt_msg_links(lead, code, message):
    """Wiadomość i linki do WhatsApp / Viber / SMS — także dla leada bez ceny (np. bez miejsca)."""
    from urllib.parse import quote as _q
    digits = lt_phone_digits(lead.contact_phone, code)
    return {"phone_digits": digits, "market": code, "message": message, "message_price": "",
            "whatsapp": f"https://wa.me/{digits}?text={_q(message)}" if digits else "",
            "viber": f"viber://chat?number=%2B{digits}" if digits else "",
            "sms": f"sms:+{digits}?&body={_q(message)}" if digits else ""}


def lt_phone_digits(phone, code="lt"):
    """Numer do linków wa.me / viber: '+370 612 34567' / '861234567' → '37061234567', '0151 2345678' → '491512345678'."""
    m = markets.get(code)
    d = re.sub(r"\D", "", phone or "")
    if d.startswith("00"):
        d = d[2:]
    elif any(d.startswith(p) for p in m["phone_codes"]) and len(d) >= 10:
        pass
    elif code == "lt" and d.startswith("86") and len(d) == 9:  # 8 6xx xxxxx
        d = "370" + d[1:]
    elif code == "lt" and d.startswith("6") and len(d) == 8:
        d = "370" + d
    elif code != "lt" and d.startswith("0") and len(d) >= 9:  # 0151 2345678
        d = m["phone_codes"][0] + d[1:]
    return d if len(d) >= 10 else ""


def _lt_dates_text(lead):
    days = sorted(d.event_date for d in lead.event_days)
    if not days:
        return ""
    if markets.get(lead_market(lead))["date_style"] == "dots":  # Niemcy: 12.12.2026, 12.–14.12.2026
        if len(days) == 1:
            return f"{days[0]:%d.%m.%Y}"
        if all((b - a).days == 1 for a, b in zip(days, days[1:])):
            return f"{days[0]:%d.%m.}–{days[-1]:%d.%m.%Y}"
        return ", ".join(f"{d:%d.%m.%Y}" for d in days)
    if len(days) == 1:
        return f"{days[0]:%Y-%m-%d}"
    if all((b - a).days == 1 for a, b in zip(days, days[1:])):
        return f"{days[0]:%Y-%m-%d}–{days[-1]:%m-%d}"
    return ", ".join(f"{d:%Y-%m-%d}" for d in days)


def lt_quote(lead, remote_budget=1):
    """Automatyczna cena dla leada (cennik i granica rynku leada): godziny i miejsce z formularza albo z treści
    zapytania (brak godzin = 4 h). Zwraca dict z ceną, rozbiciem i gotową wiadomością albo {'missing': [...]}."""
    code = lead_market(lead)
    m = markets.get(code)
    place = lt_extract_place(lead)
    if not place:  # bez ceny, ale z wiadomością na komunikator (powitanie / z kontekstu) i linkami
        return {"missing": ["miejsca"], **_lt_msg_links(lead, code, lt_welcome_message(lead, _lt_dates_text(lead), code))}
    start, end, dur, hours_src = lt_extract_hours(lead)
    tier = next((h for h in sorted(pricing.HOUR_FACTORS) if dur <= h), max(pricing.HOUR_FACTORS))
    days = max(1, min(len(lead.event_days) or 1, pricing.MAX_DAYS))
    night = pricing.night_hours(start, tier) if start is not None else 0.0
    res = price_quotes(place, tier, None, remote_budget=remote_budget, night=night, days=days, code=code)
    opt = res["options"].get(res["best"] or "")
    if not opt:
        return {"missing": [f"km do „{place}” (wpisz w kalkulatorze)"], "place": place,
                **_lt_msg_links(lead, code, lt_welcome_message(lead, _lt_dates_text(lead), code))}
    q = opt["quote"]
    price = calc_price = pricing.round_price(q["total"])
    if start is not None:
        time_txt = f"{pricing.fmt_hour(start)}–{pricing.fmt_hour(end)}"
    else:
        time_txt = f"{pricing.fmt_num(tier)} {m['hours_unit']}"
    ov = quote_override(lead)  # cena / godziny poprawione ręcznie w leadzie — mail i PDF biorą je zamiast kalkulatora
    if ov.get("price"):
        price = int(ov["price"])
    if ov.get("time"):
        time_txt = ov["time"]
    price_txt = f"{price:,}".replace(",", " ")
    when = _lt_dates_text(lead)
    message = lt_offer_message(lead, when, time_txt, place, price_txt, code=code)
    welcome = lt_welcome_message(lead, when, code)  # pierwsza wiadomość na komunikator — bez ceny
    digits = lt_phone_digits(lead.contact_phone, code)
    from urllib.parse import quote as _q
    template_params = [when or m["soon"], time_txt, place[:200], price_txt]
    return {
        "template_params": template_params, "phone_digits": digits, "market": code,
        "price": price, "exact": q["total"], "hours": tier, "duration": dur, "time": time_txt, "place": place,
        "override": bool(ov), "calc_price": calc_price,
        "hours_src": hours_src, "assumed": hours_src == "założone", "departure": opt["label"],
        "km": int(round(opt["km"])), "days": days, "when": when, "over8": dur > max(pricing.HOUR_FACTORS),
        "breakdown": pricing.describe(q, opt["label"], res["place"] or place),
        "message": welcome, "message_price": message,
        "whatsapp": f"https://wa.me/{digits}?text={_q(welcome)}" if digits else "",
        "viber": f"viber://chat?number=%2B{digits}" if digits else "",
        "sms": f"sms:+{digits}?&body={_q(welcome)}" if digits else "",
        "calc_url": url_for("crm_calculator", lead=lead.id, cel=place, h=tier,
                            od=pricing.fmt_hour(start) if start is not None else None, dni=days),
    }


QUOTE_OVERRIDE_KEY = "quote_override:{}"


def quote_override(lead):
    """Ręczna cena / godziny oferty leada (panel „Cena” w leadzie): {"price": int, "time": str} albo {}."""
    try:
        return json.loads(get_setting(QUOTE_OVERRIDE_KEY.format(lead.id)) or "{}") if lead and lead.id else {}
    except ValueError:
        return {}


@app.route("/crm/leads/<int:lead_id>/oferta-edycja", methods=["POST"])
def crm_quote_override(lead_id):
    """Zmiana ceny i godzin w automatycznej ofercie (mail + PDF przeliczają się same). Pusta cena = z kalkulatora."""
    lead = db.session.get(Lead, lead_id) or abort(404)
    raw = re.sub(r"[^\d]", "", request.form.get("price") or "")
    time_txt = (request.form.get("time") or "").strip()[:60]
    data = {}
    if raw and 100 <= int(raw) <= 500000:
        data["price"] = int(raw)
    if time_txt:
        data["time"] = time_txt
    set_setting(QUOTE_OVERRIDE_KEY.format(lead.id), json.dumps(data) if data else "")
    if data:
        db.session.add(Activity(lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(),
                                assigned_to=get_current_user(request)["id"],
                                notes="✏️ Oferta poprawiona ręcznie: " + ", ".join(
                                    x for x in ((f"cena {data['price']} € netto" if data.get("price") else ""),
                                                (f"czas {data['time']}" if data.get("time") else "")) if x)))
    db.session.commit()
    return redirect(url_for("crm_lead_detail", lead_id=lead.id) + "#lt-price")


@app.route("/crm/leads/<int:lead_id>/lt-cena", methods=["POST"])
def crm_lt_set_price(lead_id):
    """Ustawia wartość leada na cenę z formularza i zapisuje rozbicie w komentarzu."""
    lead = db.session.get(Lead, lead_id) or abort(404)
    q = lt_quote(lead, remote_budget=2)
    if q.get("price"):
        lead.value_pln, lead.currency = float(q["price"]), "EUR"
        db.session.add(Activity(lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(),
                                assigned_to=get_current_user(request)["id"],
                                notes=f"🧮 {q['breakdown']}\nWartość leada ustawiona na {q['price']} € netto "
                                      f"(pełne setki, od 25 € w górę)."))
        db.session.commit()
    return redirect(url_for("crm_lead_detail", lead_id=lead.id))


@app.route("/crm/leads/<int:lead_id>/lt-wiadomosc", methods=["POST"])
def crm_lt_message_sent(lead_id):
    """Zapisuje w leadzie, że wiadomość poszła na WhatsApp / Viber / SMS (wysłana ręcznie przez handlowca)."""
    lead = db.session.get(Lead, lead_id) or abort(404)
    channel = {"whatsapp": "WhatsApp", "sms": "SMS"}.get(request.form.get("channel"), "Viber")
    text = (request.form.get("text") or "").strip()[:3000]
    now = datetime.utcnow()
    db.session.add(Activity(lead_id=lead.id, kind="email", done=True, done_at=now,
                            assigned_to=get_current_user(request)["id"],
                            notes=f"💬 Wiadomość wysłana na {channel}\n---\n{text}"))
    db.session.commit()
    return jsonify({"ok": True})


# ---------- VIBER Z TELEFONU (kod QR) ----------
# Piszemy z własnego Vibera (nie bot — Bot API pisze tylko do subskrybentów). Na komputerze link viber:// działa tylko
# z zainstalowanym Viber Desktop. Bez niego: kod QR w leadzie → telefon otwiera stronę z gotowym tekstem i przyciskiem
# „Otwórz Viber” (czat z numerem klienta). Losowy kod, ważny 30 min, bez logowania; nic nie wychodzi samo.
VIBER_QR_PREFIX = "viber_qr:"
VIBER_QR_TTL = timedelta(minutes=30)


def qr_svg(data, px=4, quiet=4):
    """Kod QR jako SVG (czarny na białym — skanuje się też przy ciemnym motywie)."""
    from reportlab.graphics.barcode.qr import QrCodeWidget
    w = QrCodeWidget(data, barLevel="M")
    w.draw()
    q = w.qr
    n = q.getModuleCount()
    size = n + 2 * quiet
    path = "".join(f"M{c + quiet},{r + quiet}h1v1h-1z" for r in range(n) for c in range(n) if q.isDark(r, c))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size * px}" '
            f'height="{size * px}" shape-rendering="crispEdges" role="img" aria-label="Kod QR">'
            f'<rect width="{size}" height="{size}" fill="#fff"/><path d="{path}" fill="#000"/></svg>')


def _viber_qr_entry(code):
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,40}", code or ""):
        return None
    row = db.session.get(AppSetting, VIBER_QR_PREFIX + code)
    try:
        d = json.loads(row.value) if row and row.value else None
        ts = datetime.fromisoformat(d["ts"]) if d else None
    except (ValueError, KeyError, TypeError):
        return None
    return d if ts and datetime.utcnow() - ts <= VIBER_QR_TTL else None


@app.route("/crm/leads/<int:lead_id>/viber-qr", methods=["POST"])
def crm_viber_qr(lead_id):
    """Kod QR: ta sama wiadomość (z okienka, po poprawkach) do wysłania z Vibera na telefonie."""
    lead = db.session.get(Lead, lead_id) or abort(404)
    digits = lt_phone_digits(lead.contact_phone, lead_market(lead))
    if not digits:
        return jsonify({"ok": False, "error": "Brak poprawnego numeru telefonu klienta"}), 400
    for row in AppSetting.query.filter(AppSetting.key.startswith(VIBER_QR_PREFIX, autoescape=True)).all():
        if row.key.startswith(VIBER_QR_PREFIX) and not _viber_qr_entry(row.key[len(VIBER_QR_PREFIX):]):
            db.session.delete(row)  # stare kody
    code = secrets.token_urlsafe(12)
    set_setting(VIBER_QR_PREFIX + code, json.dumps({
        "lead": lead.id, "digits": digits, "text": (request.form.get("text") or "").replace("\r\n", "\n").strip()[:3000],
        "ts": datetime.utcnow().isoformat(), "user": get_current_user(request)["id"]}))
    db.session.commit()
    url = url_for("viber_phone", code=code, _external=True)
    return jsonify({"ok": True, "url": url, "svg": qr_svg(url)})


@app.route("/f/v/<code>", methods=["GET", "POST"])
def viber_phone(code):
    """Strona na telefon (z kodu QR): gotowy tekst + „Otwórz Viber”. POST = klik „Otwórz Viber” → wpis w leadzie."""
    d = _viber_qr_entry(code)
    if not d:
        if request.method == "POST":
            return jsonify({"ok": False}), 410
        return render_template("crm/viber_phone.html", d=None), 410
    if request.method == "POST":
        if not d.get("logged"):
            lead = all_markets(Lead.query).filter_by(id=d["lead"]).first()
            if lead:
                db.session.add(Activity(lead_id=lead.id, kind="email", done=True, done_at=datetime.utcnow(),
                                        assigned_to=d.get("user"),
                                        notes=f"💬 Wiadomość wysłana na Viber (z telefonu)\n---\n{d['text']}"))
            d["logged"] = True
            set_setting(VIBER_QR_PREFIX + code, json.dumps(d))
            db.session.commit()
        return jsonify({"ok": True})
    plain = f"viber://chat?number=%2B{d['digits']}"
    resp = make_response(render_template("crm/viber_phone.html", d=d, plain=plain,
                                         viber=plain + (f"&draft={quote(d['text'], safe='')}" if d["text"] else "")))
    resp.headers.update({"Cache-Control": "no-store", "X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"})
    return resp


# ---------- WIADOMOŚĆ NA KOMUNIKATOR Z KONTEKSTU (Claude) ----------
# „✨ Generuj z kontekstu”: Claude czyta maile, notatki, komentarze i etap leada i pisze krótką wiadomość na Viber /
# WhatsApp od handlowca — do czego nawiązać, co dopowiedzieć, o co zapytać. To tylko tekst w okienku: wysyła człowiek.
LT_MSG_AI_KEY = "lt_msg_ai:{}"
LT_MSG_AI_MODEL = os.environ.get("MSG_AI_MODEL", "").strip() or "claude-opus-5-5"
LT_MSG_TOOL = {
    "name": "write_message",
    "description": "Wiadomość na Viber/WhatsApp do klienta, jej polskie tłumaczenie i krótkie uzasadnienie dla zespołu.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "language": {"type": "string", "enum": ["lt", "de", "pl", "en"], "description": "Język wiadomości"},
            "message": {"type": "string", "description": "Wiadomość do klienta: powitanie, treść, podpis"},
            "message_pl": {"type": "string", "description": "Ta sama wiadomość po polsku (dla zespołu)"},
            "hook": {"type": "string",
                     "description": "1–2 zdania po polsku: do czego wiadomość nawiązuje i dlaczego (co z kontekstu)"},
        },
        "required": ["language", "message", "message_pl", "hook"],
    },
}


def _lt_msg_context(lead):
    """Cały kontekst leada dla Claude'a: dane i cena z CRM, korespondencja mailowa (oryginały), historia w CRM."""
    parts = [f"DZIŚ: {date.today():%Y-%m-%d}"]
    base = _mail_draft_context(lead_market(lead), lead_id=lead.id)
    if base:
        parts.append(base)
    mails = list(reversed(lead_mails(lead, limit=15)))
    if mails:
        lines = []
        for mm in mails:
            fresh, _old = poczta.split_quote(mm.body_text or "")
            lines.append(f"[{_dt(mm.date)}] {'KLIENT' if mm.folder == 'in' else 'MY'} (język: {mm.lang or '?'}) — "
                         f"{mm.subject or ''}\n{fresh.strip()[:1500]}")
        parts.append("KORESPONDENCJA MAILOWA (od najstarszej):\n\n" + "\n\n".join(lines))
    acts = sorted(lead.activities, key=lambda a: a.created_at or datetime.min, reverse=True)[:12]
    if acts:
        parts.append("HISTORIA W CRM (od najnowszych — komentarze, telefony, wysłane wiadomości, zadania):\n" + "\n".join(
            f"- [{_dt(a.created_at)}] {a.kind}{' (zrobione)' if a.done else ''}: {(a.notes or '').strip()[:400]}"
            for a in acts if (a.notes or "").strip()))
    return "\n\n".join(parts)


def lt_msg_ai(lead_id, writer_id, hint=""):
    """Claude pisze wiadomość na komunikator na podstawie kontekstu leada (zadanie w tle) i zapamiętuje ją w leadzie."""
    lead = all_markets(Lead.query).filter_by(id=lead_id).first()
    if not lead:
        return {"error": "Nie ma takiego leada"}
    code = lead_market(lead)
    m = markets.get(code)
    writer = USERS_BY_ID.get(writer_id) or USERS_BY_ID.get(lead.assigned_to) or USERS[0]
    full = writer.get("full_name") or writer["name"]
    client_langs = [mm.lang for mm in lead_mails(lead, limit=15) if mm.folder == "in" and mm.lang]
    system = (
        f"Piszesz krótką wiadomość na Viber/WhatsApp, którą {full} z 33Bots sam wyśle klientowi ze swojego telefonu. "
        "33Bots wynajmuje humanoidalnego robota Unitree G1 na wydarzenia (targi, konferencje, eventy firmowe) razem "
        "z operatorem: dojazd, przygotowanie robota, dopasowanie osobowości i wiedzy robota do wydarzenia, wsparcie "
        "na miejscu; robot wita gości, rozmawia na podstawie przygotowanej bazy wiedzy, tańczy, pozuje do zdjęć, "
        "może wystąpić na scenie.\n"
        "Najpierw przeczytaj cały kontekst (maile, notatki, komentarze, etap) i ustal: na czym stanęła rozmowa, czego "
        "klient chce, co go hamuje (cena, termin, scenariusz…), kto na kogo czeka. Potem napisz wiadomość, która "
        "naturalnie wraca do tej rozmowy i daje klientowi łatwy powód, żeby odpisać: nawiąż do konkretu z rozmowy, "
        "dodaj jedną przydatną informację albo propozycję i zakończ jednym prostym pytaniem.\n"
        "Styl: jak człowiek piszący z telefonu — 2–5 krótkich zdań (najwyżej ok. 600 znaków), ciepło i konkretnie, "
        "bez sloganów, list, nagłówków i linków; najwyżej jedna emotka. Klient zna nas z maili — możesz krótko "
        "nawiązać, że piszesz też tutaj. Nie pisz, że wiadomość jest automatyczna.\n"
        f"Język: ten, w którym pisze klient (języki jego maili: {', '.join(dict.fromkeys(client_langs)) or 'brak maili'}); "
        f"gdy klient nie pisał — {m['lang_in_pl']}. Jak native speaker, forma grzecznościowa. Litewski: powitanie "
        "„Laba diena!” bez imienia (wołacz łatwo pomylić). Niemiecki: forma „Sie”. Polski: „Dzień dobry”, forma "
        "Pan/Pani (gdy płeć niepewna — bez form osobowych). Nigdy po angielsku, chyba że klient pisze po angielsku.\n"
        f"Na końcu, w osobnej linii, podpis: {full}, 33bots\n"
        "Fakty: nie wymyślaj cen, rabatów, terminów, dostępności ani obietnic. Cenę podaj tylko, jeśli pasuje do "
        "sytuacji i jest w kontekście (cena z kalkulatora CRM albo cena, którą już podaliśmy) — nigdy jej sam nie "
        "obniżaj; inna cena albo rabat tylko, gdy są we wskazówce użytkownika albo w notatkach zespołu. Jeśli "
        "wydarzenie już minęło albo klient odmówił — uprzejmie, bez naciskania (np. zapytaj o kolejne wydarzenia). "
        "Nie pisz 'Edward Warchocki' — tylko 'Unitree G1'.\n" + HUMAN_TYPO_RULE
    )
    task = ((f"WSKAZÓWKA UŻYTKOWNIKA: {hint}\n\n" if hint else "") + _lt_msg_context(lead)
            + "\n\nNapisz wiadomość na Viber/WhatsApp do tego klienta.")
    res = {}
    for _attempt in range(2):
        res = _ai_forced_tool(LT_MSG_AI_MODEL, system, task, LT_MSG_TOOL, max_tokens=3000)
        lang = res.get("language") or m["lang"]
        seen = poczta.detect_lang(res.get("message") or "")
        if str(res.get("message") or "").strip() and (not seen or seen == lang):
            break
        task += (f"\n\nUWAGA: poprzednia wiadomość nie była w języku {lang} (wykryto: {seen or 'pusto'}). "
                 "Napisz ją w całości w jednym, właściwym języku.")
    else:
        return {"error": "Claude nie napisał poprawnej wiadomości — spróbuj jeszcze raz"}
    msg = human_text(str(res["message"]).strip())
    if full not in msg:
        msg += f"\n{full}, 33bots"
    draft = {"message": msg, "message_pl": human_text(str(res.get("message_pl") or "").strip()),
             "hook": str(res.get("hook") or "").strip(), "language": res.get("language") or m["lang"],
             "ts": datetime.utcnow().isoformat(timespec="seconds"), "by": writer_id}
    set_setting(LT_MSG_AI_KEY.format(lead.id), json.dumps(draft))
    db.session.commit()
    return {"draft": draft, "info": "gotowe"}


def lt_msg_saved(lead_id):
    """Ostatnia wiadomość z kontekstu wygenerowana dla leada (albo None)."""
    try:
        d = json.loads(get_setting(LT_MSG_AI_KEY.format(lead_id)) or "null")
    except ValueError:
        return None
    return d if isinstance(d, dict) and d.get("message") else None


@app.route("/crm/leads/<int:lead_id>/lt-wiadomosc/generuj", methods=["POST"])
def crm_lt_msg_ai(lead_id):
    """✨ Generuj z kontekstu — w tle (Claude potrzebuje 10–30 s), okienko odpytuje /crm/job."""
    lead = db.session.get(Lead, lead_id) or abort(404)
    if _ai_client() is None:
        return jsonify({"error": "Brak ANTHROPIC_API_KEY w Railway — Claude nie może pisać wiadomości."}), 400
    jid = _start_job("Claude pisze wiadomość", lt_msg_ai, lead.id, get_current_user(request)["id"],
                     (request.form.get("hint") or "").strip()[:1000])
    return jsonify({"jid": jid, "poll": url_for("crm_job", jid=jid, format="json")})


app.jinja_env.globals.update(lt_quote=lt_quote, lt_msg_saved=lt_msg_saved)


# ---------- WHATSAPP (oficjalne WhatsApp Cloud API od Meta) ----------
# Railway → Variables: WHATSAPP_TOKEN (stały token System User), WHATSAPP_PHONE_ID (Phone number ID),
# WHATSAPP_TEMPLATE (nazwa zatwierdzonego szablonu z ceną, 4 pola: data, godziny, miejsce, cena),
# opcjonalnie WHATSAPP_TEMPLATE_LANG (domyślnie lt), WHATSAPP_API_VERSION (domyślnie v22.0),
# Niemcy: WHATSAPP_TEMPLATE_DE (+ WHATSAPP_TEMPLATE_LANG_DE, domyślnie de) — szablon po niemiecku,
# WHATSAPP_VERIFY_TOKEN (dowolne hasło do podpięcia webhooka), WHATSAPP_APP_SECRET (sprawdzanie podpisu webhooka).
# Pierwsza wiadomość do klienta = szablon (wymóg Meta). Gdy klient odpisze, przez 24 h idzie zwykły tekst z okienka.
WA_LAST_IN_KEY = "wa_in:{}"
WA_WINDOW = timedelta(hours=24)


def wa_ready():
    return bool(os.environ.get("WHATSAPP_TOKEN", "").strip() and os.environ.get("WHATSAPP_PHONE_ID", "").strip())


def _wa_post(payload):
    ver = os.environ.get("WHATSAPP_API_VERSION", "").strip() or "v22.0"
    url = f"https://graph.facebook.com/{ver}/{os.environ['WHATSAPP_PHONE_ID'].strip()}/messages"
    try:
        r = requests.post(url, json=payload, timeout=20,
                          headers={"Authorization": f"Bearer {os.environ['WHATSAPP_TOKEN'].strip()}"})
        data = r.json() if r.content else {}
    except (requests.RequestException, ValueError) as e:
        return None, f"brak połączenia z WhatsApp ({type(e).__name__})"
    if r.status_code >= 400 or "error" in data:
        err = data.get("error") or {}
        return None, f"WhatsApp odrzucił wiadomość: {err.get('message') or r.status_code}" + (
            f" ({err.get('error_data', {}).get('details')})" if isinstance(err.get("error_data"), dict) else "")
    return (data.get("messages") or [{}])[0].get("id"), None


def wa_window_open(lead):
    last = get_setting(WA_LAST_IN_KEY.format(lead.id))
    try:
        return bool(last) and datetime.utcnow() - datetime.fromisoformat(last) < WA_WINDOW
    except ValueError:
        return False


def wa_send_quote(lead, text, actor_id):
    """Wysyła cenę na WhatsApp: w oknie 24 h zwykły tekst, poza nim zatwierdzony szablon. Zwraca błąd albo None."""
    q = lt_quote(lead, remote_budget=2)
    code = lead_market(lead)
    digits = lt_phone_digits(lead.contact_phone, code)
    if not digits:
        return "lead nie ma poprawnego numeru telefonu"
    if wa_window_open(lead) and text:
        payload = {"messaging_product": "whatsapp", "to": digits, "type": "text", "text": {"body": text[:4096]}}
        how = "zwykła wiadomość (klient pisał w ostatnich 24 h)"
    else:
        # szablon w języku rynku: Litwa — WHATSAPP_TEMPLATE (lt), Niemcy — WHATSAPP_TEMPLATE_DE (de)
        tvar = "WHATSAPP_TEMPLATE" if code == "lt" else f"WHATSAPP_TEMPLATE_{code.upper()}"
        name = os.environ.get(tvar, "").strip()
        if not name:
            return f"brak {tvar} w Railway (nazwa zatwierdzonego szablonu {markets.get(code)['lang_in_pl']})"
        if not q.get("template_params"):
            return "brak ceny — uzupełnij godziny i miejsce"
        params = [{"type": "text", "text": re.sub(r"\s+", " ", str(p)).strip()[:300] or "—"} for p in q["template_params"]]
        payload = {"messaging_product": "whatsapp", "to": digits, "type": "template",
                   "template": {"name": name, "language": {"code": (os.environ.get(
                       "WHATSAPP_TEMPLATE_LANG" if code == "lt" else f"WHATSAPP_TEMPLATE_LANG_{code.upper()}", "").strip()
                       or markets.get(code)["whatsapp_lang"])},
                                "components": [{"type": "body", "parameters": params}]}}
        how = f"szablon „{name}”"
        text = q.get("message_price") or text
    msg_id, err = _wa_post(payload)
    if err:
        return err
    now = datetime.utcnow()
    for a in Activity.query.filter(Activity.lead_id == lead.id, Activity.done.isnot(True),
                                   Activity.notes.like("Odpowiedz klientowi na WhatsApp%")).all():
        a.done, a.done_at = True, now  # odpowiedź poszła — zadanie „Odpowiedz…” się zamyka
    db.session.add(Activity(lead_id=lead.id, kind="email", done=True, done_at=now, assigned_to=actor_id,
                            notes=f"💬 WhatsApp wysłany przez CRM ({how})\n---\n{text}"))
    db.session.commit()
    return None


@app.route("/crm/leads/<int:lead_id>/whatsapp", methods=["POST"])
def crm_whatsapp_send(lead_id):
    lead = db.session.get(Lead, lead_id) or abort(404)
    if not wa_ready():
        return jsonify({"error": "WhatsApp nie jest podpięty (WHATSAPP_TOKEN i WHATSAPP_PHONE_ID w Railway)."}), 400
    err = wa_send_quote(lead, (request.form.get("text") or "").strip(), get_current_user(request)["id"])
    if err:
        return jsonify({"error": err}), 502
    return jsonify({"ok": True})


def _wa_lead_for(digits):
    """Lead po numerze nadawcy (porównanie końcówki numeru)."""
    tail = digits[-8:]
    if not tail:
        return None
    for lead in Lead.query.filter(Lead.contact_phone.isnot(None)).order_by(Lead.created_at.desc()).all():
        d = lt_phone_digits(lead.contact_phone, lead_market(lead)) or re.sub(r"\D", "", lead.contact_phone or "")
        if d.endswith(tail):
            return lead
    return None


@app.route("/whatsapp/webhook", methods=["GET", "POST"])
def whatsapp_webhook():
    """Webhook Meta: GET = weryfikacja przy podpinaniu, POST = wiadomości od klientów (trafiają do komentarzy leada)."""
    if request.method == "GET":
        token = os.environ.get("WHATSAPP_VERIFY_TOKEN", "").strip()
        if token and request.args.get("hub.mode") == "subscribe" and hmac.compare_digest(
                request.args.get("hub.verify_token", ""), token):
            return request.args.get("hub.challenge", ""), 200
        return "forbidden", 403
    secret = os.environ.get("WHATSAPP_APP_SECRET", "").strip()
    if secret:
        sig = request.headers.get("X-Hub-Signature-256", "")
        good = "sha256=" + hmac.new(secret.encode(), request.get_data(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            return "bad signature", 403
    data = request.get_json(silent=True) or {}
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for m in value.get("messages", []):
                digits = re.sub(r"\D", "", m.get("from", ""))
                lead = _wa_lead_for(digits)
                if not lead:
                    continue
                kind = m.get("type")
                if kind == "text":
                    body = (m.get("text") or {}).get("body", "")
                elif kind in ("button", "interactive"):
                    body = (m.get("button") or {}).get("text") or json.dumps(m.get("interactive", {}), ensure_ascii=False)[:500]
                else:
                    body = f"[{kind}]"
                set_setting(WA_LAST_IN_KEY.format(lead.id), datetime.utcnow().isoformat(timespec="seconds"))
                db.session.add(Activity(lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(),
                                        assigned_to=lead.assigned_to or USERS[0]["id"],
                                        notes=f"💬 WhatsApp od klienta:\n{body[:3000]}"))
                db.session.add(Activity(lead_id=lead.id, kind="email", due_date=datetime.utcnow(),
                                        assigned_to=lead.assigned_to or USERS[0]["id"],
                                        notes="Odpowiedz klientowi na WhatsApp (napisał — 24 h na zwykłą odpowiedź)."))
    db.session.commit()
    return "ok", 200


app.jinja_env.globals.update(wa_ready=wa_ready, wa_window_open=wa_window_open)


# ---------- PRZENIESIENIE LITEWSKICH LEADÓW Z POLSKIEGO CRM ----------
# W Railway: PL_DATABASE_URL = adres bazy polskiego CRM (Add Reference → Postgres z projektu PL).
# Przycisk „Przenieś” kopiuje litewskie leady razem z zadaniami, komentarzami, historią etapów, ofertami,
# Customer Journey i dokumentami, a dopiero po udanym zapisie tutaj usuwa je z polskiego CRM.
# Potem CRM sam co 2 minuty przenosi nowe litewskie i niemieckie leady, które wpadną do polskiego CRM.
import pl_move  # noqa: E402

PL_MOVE_AUTO_KEY = "pl_move_auto"  # litewskie: włączane pierwszym kliknięciem „Przenieś”
PL_MOVE_AUTO_DE_KEY = "pl_move_auto_de"  # niemieckie: przenoszą się same od razu ("0" = wyłączone)
PL_MOVE_LAST_KEY = "pl_move_last"
PL_WON_LAST_KEY = "pl_won_last"
_pl_move_lock = threading.Lock()
_pl_won_lock = threading.Lock()
_pl_count_cache = {"at": 0.0, "n": {}, "err": ""}


def pl_move_configured():
    return bool(os.environ.get("PL_DATABASE_URL", "").strip())


def pl_move_auto(code):
    if code == "de":
        return get_setting(PL_MOVE_AUTO_DE_KEY) != "0"
    return get_setting(PL_MOVE_AUTO_KEY) == "1"


def pl_pending_count(max_age=300, code=None):
    """Ile leadów rynku `code` (lt / de) czeka w polskim CRM (z pamięci podręcznej na 5 minut)."""
    if not pl_move_configured():
        return None
    code = code or current_market()
    if time.time() - _pl_count_cache["at"] > max_age:
        try:
            eng = pl_move.engine()
            meta = pl_move._meta(eng)
            _pl_count_cache.update(n={c: len(pl_move.find_market(eng, c, meta)) for c in markets.MARKETS},
                                   err="", at=time.time())
        except Exception as e:  # zła baza / brak sieci — strona działa dalej
            _pl_count_cache.update(n={}, err=f"{type(e).__name__}: {e}"[:300], at=time.time())
    return _pl_count_cache["n"].get(code)


PL_MOVE_STATE_KEY = "pl_move_state"  # postęp w bazie: widać go po restarcie i z każdego procesu


def pl_move_state():
    try:
        st = json.loads(get_setting(PL_MOVE_STATE_KEY) or "{}")
    except ValueError:
        st = {}
    beat = st.get("heartbeat")
    try:
        alive = bool(beat) and datetime.utcnow() - datetime.fromisoformat(beat) < timedelta(minutes=3)
    except ValueError:
        alive = False
    st["running"] = bool(st.get("running")) and alive
    st["stalled"] = bool(st.get("running_flag")) and not alive and not st.get("finished_at")
    return st


def _save_move_state(state, **extra):
    st = dict(state, **extra, heartbeat=datetime.utcnow().isoformat(timespec="seconds"))
    st["running_flag"] = st.get("running", False)
    try:
        set_setting(PL_MOVE_STATE_KEY, json.dumps(st, ensure_ascii=False, default=str)[:20000])
        db.session.commit()
    except Exception:
        db.session.rollback()


def _move_progress(state, st, code, moved_before, errors_before):
    """Postęp całego przenoszenia (kilka rynków po kolei) z postępu jednego rynku."""
    out = dict(state)
    out.update(st)
    out.update(done=moved_before + (st.get("done") or 0), errors=errors_before + list(st.get("errors") or []),
               total=(state.get("total_before") or 0) + (st.get("total") or 0),
               current=f"{code.upper()} {st.get('current') or ''}".strip() if st.get("current") else "")
    return out


def run_pl_move(actor_id=None, codes=None):
    """Przenosi teraz leady z PL: rynki `codes` (domyślnie te z włączonym automatem). Zwraca (przeniesione, błędy)."""
    codes = [c for c in (codes or [c for c in markets.MARKETS if pl_move_auto(c)]) if c in markets.MARKETS]
    if not codes:
        return 0, []
    if not _pl_move_lock.acquire(blocking=False):
        return 0, ["przenoszenie już trwa"]
    state = {"running": True, "started_at": datetime.utcnow().isoformat(timespec="seconds"), "finished_at": None,
             "markets": codes}
    moved_all, errors_all = 0, []
    try:
        _save_move_state(state)
        for code in codes:
            sub = {}
            moved, errors = pl_move.move_all(pl_move.engine(), db, {
                "Lead": Lead, "LeadEventDate": LeadEventDate, "Activity": Activity, "ActivityComment": ActivityComment,
                "StageHistory": StageHistory, "Crew": Crew, "Journey": Journey, "JourneyMail": JourneyMail,
                "JourneyDocument": JourneyDocument, "ContractRequest": ContractRequest, "SentOffer": SentOffer,
                "InboxMessage": InboxMessage,
            }, actor_id or USERS[0]["id"], progress=sub, code=code,
                on_step=lambda st, code=code: _save_move_state(_move_progress(state, st, code, moved_all, errors_all),
                                                               running=True))
            moved_all += moved
            errors_all += [f"{code.upper()} {e}" for e in errors]
            state.update(done=moved_all, errors=errors_all, total_before=(state.get("total_before") or 0) + (sub.get("total") or 0),
                         total=(state.get("total_before") or 0) + (sub.get("total") or 0))
        set_setting(PL_MOVE_LAST_KEY, f"{datetime.utcnow().isoformat(timespec='seconds')}|{moved_all}|{'; '.join(errors_all)[:500]}")
        db.session.commit()
        _pl_count_cache["at"] = 0.0
        return moved_all, errors_all
    except Exception as e:
        db.session.rollback()
        errors_all.append(f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}")
        state["errors"] = errors_all
        app.logger.exception("pl_move")
        return moved_all, errors_all
    finally:
        _save_move_state(state, running=False, current="", finished_at=datetime.utcnow().isoformat(timespec="seconds"))
        _pl_move_lock.release()


# Jednorazowa poprawka (07.11.2026): wygrane z Litwy oznaczone w CRM LT dopiero 04.11 — prawdziwa data wygranej
# z korespondencji (Panevėžio kolegija: rezerwacja 20.01.2027 potwierdzona 23.07; Galley: potwierdzenie 30.10).
WIN_DATE_FIX_KEY = "win_date_fix_v1"
WIN_DATE_FIXES = (("laima.mikalajuniene@panko.lt", date(2026, 7, 23)), ("kamile@galley.lt", date(2026, 10, 30)))


def apply_win_date_fixes():
    """Raz: ustawia „Wygrany dnia” leadom z WIN_DATE_FIXES (tylko wygranym, po adresie klienta). Zwraca listę zmian."""
    if get_setting(WIN_DATE_FIX_KEY) or not _claim_once(WIN_DATE_FIX_KEY + "_lock"):
        return None
    done = []
    for email, day in WIN_DATE_FIXES:
        for lead in all_markets(Lead.query.filter(db.func.lower(Lead.contact_email) == email,
                                                  Lead.stage.in_(WON_STAGES))).all():
            entry = _closing_entry(lead)
            if entry is not None and entry.changed_at and to_local(entry.changed_at).date() == day:
                continue
            if set_lead_closed_on(lead, day) is None:  # błąd (etap / data) zwraca przed jakąkolwiek zmianą
                done.append(f"#{lead.id} {lead.company}: wygrany {day:%d.%m.%Y}")
    set_setting(WIN_DATE_FIX_KEY, ("; ".join(done) or "bez zmian")[:500])
    db.session.commit()
    return done


def sync_won_to_pl():
    """Wygrane leady LT i DE → kopia w polskim CRM (tylko logistyka: dni, miejsca, robot, kontakt na miejscu).
    Odświeża kopię, gdy coś się zmieni (data, miejsce, etap); cofnięty z wygranej trafia tam jako Stracony.
    Do PL wracają TYLKO wygrane. Zwraca (wysłane / odświeżone, błędy)."""
    if not pl_move_configured() or not _pl_won_lock.acquire(blocking=False):
        return 0, []
    done, errors = 0, []
    try:
        try:
            apply_win_date_fixes()
        except Exception:
            db.session.rollback()
            app.logger.exception("win_date_fixes")
        eng = pl_move.engine()
        meta = pl_move._meta(eng)
        leads = all_markets(Lead.query.filter(db.or_(Lead.stage.in_(pl_move.WON), Lead.pl_copy_id.isnot(None)))
                            .order_by(Lead.id)).all()
        for lead in leads:
            code = lead.market if lead.market in pl_move.COPY_SOURCES else "lt"
            try:
                entry = _closing_entry(lead) if lead.stage in CLOSED_STAGES else None
                closed_at = entry.changed_at if entry else None
                if lead.pl_copy_id and lead.pl_copy_hash == pl_move.copy_payload(lead, code, closed_at)[2]:
                    continue  # nic się nie zmieniło od ostatniej wysyłki (też data wygranej)
                pl_id, mark = pl_move.push_copy(eng, meta, lead, code, closed_at=closed_at)
                lead.pl_copy_id, lead.pl_copy_hash = pl_id, mark
                db.session.commit()
                done += 1
            except Exception as e:
                db.session.rollback()
                errors.append(f"#{lead.id} {lead.company}: {type(e).__name__}: {str(e).splitlines()[0][:160] if str(e) else ''}")
        set_setting(PL_WON_LAST_KEY, f"{datetime.utcnow().isoformat(timespec='seconds')}|{done}|{'; '.join(errors)[:500]}")
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        errors.append(f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}")
        app.logger.exception("pl_won")
    finally:
        _pl_won_lock.release()
    return done, errors


def _pl_move_background(actor_id, codes=None):
    with app.app_context():
        try:
            run_pl_move(actor_id, codes)
            sync_won_to_pl()
        finally:
            db.session.remove()


@app.route("/crm/przenies-z-pl", methods=["GET", "POST"])
def crm_pl_move():
    if request.method == "POST":
        code = request.form.get("market") if request.form.get("market") in markets.MARKETS else current_market()
        # od teraz nowe leady tego rynku przenoszą się same; samo przenoszenie idzie w tle (może trwać kilka minut)
        set_setting(PL_MOVE_AUTO_DE_KEY if code == "de" else PL_MOVE_AUTO_KEY, "1")
        db.session.commit()
        if not pl_move_state().get("running"):
            threading.Thread(target=_pl_move_background, args=(get_current_user(request)["id"], [code]), daemon=True).start()
            time.sleep(1.5)
        return redirect(url_for("crm_pl_move"))
    st = pl_move_state()
    running = st.get("running")
    last = (get_setting(PL_MOVE_LAST_KEY) or "").split("|", 2)
    won_last = (get_setting(PL_WON_LAST_KEY) or "").split("|", 2)
    counts = {}
    if not running:
        pl_pending_count(max_age=0)
        counts = dict(_pl_count_cache["n"])
    return render_template("crm/pl_move.html", configured=pl_move_configured(), counts=counts,
                           error="" if running else _pl_count_cache["err"], running=running, progress=st,
                           auto={c: pl_move_auto(c) for c in markets.MARKETS}, last=last if len(last) == 3 else None,
                           won_last=won_last if len(won_last) == 3 else None,
                           won_total=all_markets(Lead.query.filter(Lead.pl_copy_id.isnot(None))).count(),
                           moved_total={c: all_markets(Lead.query.filter(Lead.pl_id.isnot(None), Lead.market == c)).count()
                                        for c in markets.MARKETS})


PL_MOVE_EVERY = max(60, int(os.environ.get("PL_MOVE_EVERY", 120)))  # s — nowy lead LT/DE znika z polskiego CRM po ~2 min
PL_WON_EVERY = 600  # s — wygrane do polskiego CRM


def _pl_move_scheduler():
    time.sleep(30)
    last_won = 0.0
    while True:
        try:
            with app.app_context():
                moved, errors = run_pl_move()  # rynki z włączonym automatem (niemieckie — zawsze)
                db.session.remove()
            errors = [e for e in errors or [] if "już trwa" not in str(e)]
            health_beat("pl_move", not errors, info=f"przeniesione: {moved}", error="; ".join(map(str, errors[:3])))
        except Exception as e:
            app.logger.exception("pl_move")
            health_fail("pl_move", e)
        if time.time() - last_won >= PL_WON_EVERY:
            last_won = time.time()
            try:
                with app.app_context():
                    done, errors = sync_won_to_pl()
                    db.session.remove()
                health_beat("pl_won", not errors, info=f"wysłane / odświeżone: {done}",
                            error="; ".join(map(str, (errors or [])[:3])))
            except Exception as e:
                app.logger.exception("pl_won")
                health_fail("pl_won", e)
        time.sleep(PL_MOVE_EVERY)


if pl_move_configured() and os.environ.get("PL_MOVE", "1") != "0":
    _start_worker("pl_move", _pl_move_scheduler)

app.jinja_env.globals.update(pl_move_configured=pl_move_configured, pl_pending_count=pl_pending_count)


@app.route("/crm/lt-mail/zalegle", methods=["POST"])
def crm_lt_mail_backlog():
    """Jednym kliknięciem: mail LT do wszystkich starszych litewskich leadów bez maila (w tle)."""
    user_id = get_current_user(request)["id"]
    code = current_market()  # zadanie w tle nie ma filtra rynku — bierze rynek, który człowiek oglądał

    def job():
        ok, failed = 0, []
        for lead in lt_backlog(code):
            err = send_lt_mail(lead, actor_id=user_id)
            if err == LT_ALREADY_SENT:
                continue
            if err:
                db.session.rollback()
                failed.append(f"#{lead.id} {lead.company}: {err}")
            else:
                ok += 1
        info = f"Wysłano {ok} maili z prośbą o dane" + (f"; nie wyszło {len(failed)}: " + "; ".join(failed[:5]) if failed else "")
        return {"info": info[:500], "url": "/crm/leads?view=list"}
    return redirect(url_for("crm_job", jid=_start_job("Maile do starszych leadów", job)))


@app.route("/crm/leads/<int:lead_id>/lt-mail", methods=["POST"])
def crm_lt_mail(lead_id):
    lead = db.session.get(Lead, lead_id) or abort(404)
    err = send_lt_mail(lead, actor_id=get_current_user(request)["id"], resend=request.form.get("resend") == "1")
    if err == LT_ALREADY_SENT:
        return redirect(url_for("crm_lead_detail", lead_id=lead.id))  # automat właśnie wysłał — bez duplikatu
    if err:
        db.session.rollback()
        db.session.add(Activity(lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(),
                                assigned_to=get_current_user(request)["id"], notes=f"⚠️ Mail LT nie wyszedł: {err}"))
        db.session.commit()
    return redirect(url_for("crm_lead_detail", lead_id=lead.id))


@app.route("/f/<token>", methods=["GET", "POST"])
def lead_form(token):
    """Publiczny formularz dla klienta (po litewsku): dane do wyceny trafiają prosto do leada."""
    lead = Lead.query.filter_by(form_token=token).first() if len(token) >= 16 else None
    if not lead:
        abort(404)
    if request.method == "GET":
        return _render_lead_form(lead, done=False)
    f = request.form
    clean = {k: (f.get(k) or "").strip()[:500] for k in
             ("date", "date_to", "place", "time_from", "time_to", "kind", "guests", "phone", "comment")}
    days = []
    d1, d2 = parse_date(clean["date"]), parse_date(clean["date_to"])
    if d1:
        d1 = d1.date()
        d2 = d2.date() if d2 and d2.date() >= d1 else d1
        days = [d1 + timedelta(days=i) for i in range(min((d2 - d1).days, 13) + 1)]
    if days:
        merged = sorted({d.event_date for d in lead.event_days} | set(days))
        set_lead_event_days(lead, merged, {d.event_date: d.location for d in lead.event_days if d.location})
    if clean["place"]:
        lead.location = clean["place"][:200]
    if clean["phone"] and not lead.contact_phone:
        lead.contact_phone = clean["phone"][:50]
    labels = [("Data", f"{clean['date']}{' – ' + clean['date_to'] if clean['date_to'] and clean['date_to'] != clean['date'] else ''}"),
              ("Miejsce", clean["place"]), ("Godziny", f"{clean['time_from']}–{clean['time_to']}".strip("–")),
              ("Rodzaj wydarzenia", clean["kind"]), ("Goście", clean["guests"]), ("Telefon", clean["phone"]),
              ("Komentarz", clean["comment"])]
    summary = "\n".join(f"{k}: {v}" for k, v in labels if v)
    if summary:
        lead.notes = ((lead.notes or "").rstrip() + f"\n\n[formularz {lead_market(lead).upper()}] "
                      + summary.replace("\n", "; ")).strip()
    lead.form_filled_at = datetime.utcnow()
    owner = lead.assigned_to or USERS[0]["id"]
    db.session.add(Activity(lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(), assigned_to=owner,
                            notes=f"📝 Klient wypełnił formularz z danymi do wyceny\n{summary or '(bez danych)'}"))
    today = datetime.combine(to_local(datetime.utcnow()).date(), datetime.min.time())
    if auto_tasks_on():
        db.session.add(Activity(lead_id=lead.id, kind="email", due_date=today, assigned_to=owner,
                                notes=f"Przygotuj i wyślij ofertę — klient ({markets.get(lead_market(lead))['name']}) "
                                      "wypełnił formularz (dane w komentarzu)."))
    db.session.commit()
    _notify_form_filled(lead.id, summary)
    if lt_auto_quote_enabled(lead_market(lead)):
        _send_quote_in_background(lead.id)
    return _render_lead_form(lead, done=True)


def _render_lead_form(lead, done):
    """Formularz dla klienta w języku rynku leada (Litwa: litewski, Niemcy: niemiecki)."""
    code = lead_market(lead)
    m = markets.get(code)
    return render_template("crm/lead_form.html", lead=lead, done=done, fm=m["form"], fm_lang=m["html_lang"],
                           fm_place=client_place(lead.location or "", code) if lead.location else "",
                           fm_contact=lt_sender(code)["email"])


def _notify_form_filled(lead_id, summary):
    """Powiadomienie mailem, że klient wypełnił formularz — na skrzynkę rynku leada (o.latek@33bots.lt / .de);
    inne adresy: LT_NOTIFY_EMAILS (Litwa), NOTIFY_EMAILS_DE (Niemcy)."""
    lead = all_markets(Lead.query.filter(Lead.id == lead_id)).first()
    if lead is None:
        return
    code = lead_market(lead)
    m = markets.get(code)
    env = "LT_NOTIFY_EMAILS" if code == "lt" else f"NOTIFY_EMAILS_{code.upper()}"
    to = [e.strip() for e in os.environ.get(env, m["own_email"]).split(",") if e.strip()]
    if not to or not lt_mailbox_ready(code):
        return

    def run():
        with app.app_context():
            try:
                lead = db.session.get(Lead, lead_id)
                link = f"{public_base_url()}/crm/leads/{lead_id}" if public_base_url() else ""
                body = (f"Klient ({m['name']}) wypełnił formularz z danymi do wyceny.\n\n"
                        f"Lead: {lead.company}\nKontakt: {lead.contact_name or '—'} · {', '.join(lead.emails) or '—'}"
                        f" · {lead.contact_phone or '—'}\n\n{summary or '(bez danych)'}\n\n"
                        + (f"Otwórz w CRM: {link}\n" if link else "")
                        + ("\nW CRM czeka zadanie „Przygotuj i wyślij ofertę”." if auto_tasks_on()
                           else "\nDane są w leadzie — ustal w nim następny krok (np. oferta)."))
                sender = lt_sender(code)
                err = _smtp_send(", ".join(to), f"📝 Formularz wypełniony {m['flag']}: {lead.company}", body,
                                 sender["name"], sender=sender)
                if err:
                    db.session.add(Activity(lead_id=lead_id, kind="note", done=True, done_at=datetime.utcnow(),
                                            assigned_to=lead.assigned_to or USERS[0]["id"],
                                            notes=f"⚠️ Powiadomienie o formularzu nie wyszło: {err}"))
                    db.session.commit()
            except Exception:
                db.session.rollback()
            finally:
                db.session.remove()
    threading.Thread(target=run, daemon=True).start()


# ---------- POCZTA: skrzynki firmowe w CRM + tłumaczenie na żywo ----------
# Zakładka „Poczta”: Odebrane i Wysłane wszystkich skrzynek rynku (domyślnie o.latek@ i kontakt@33bots.lt),
# razem z archiwum. CRM synchronizuje je w tle do tabeli mail_messages (tylko odczyt IMAP), każdy mail
# pokazuje obok oryginału po polsku (Claude, tłumaczenie zapisane w bazie) i pozwala pisać maile po polsku
# z tłumaczeniem na język klienta — wysyłka z wybranej skrzynki, z kopią w Wysłanych.
MAIL_LANG_NAMES = {"pl": "polski", "lt": "litewski", "de": "niemiecki", "en": "angielski"}
MAIL_LANG_FLAGS = {"pl": "🇵🇱", "lt": "🇱🇹", "de": "🇩🇪", "en": "🇬🇧"}
MAIL_SYNC_SECONDS = int(os.environ.get("MAIL_SYNC_SECONDS", "120") or 120)
MAIL_BATCH = int(os.environ.get("MAIL_SYNC_BATCH", "300") or 300)
MAIL_TRANSLATE_MODEL = os.environ.get("TRANSLATE_AI_MODEL", "").strip() or INBOX_AI_MODEL
MAIL_TRANSLATE_STRONG_MODEL = os.environ.get("TRANSLATE_AI_MODEL_STRONG", "").strip() or OFFER_AI_MODEL
MAIL_AUTO_TRANSLATE_DAYS = int(os.environ.get("MAIL_AUTO_TRANSLATE_DAYS", "30") or 30)
_mail_sync_lock = threading.Lock()


def _env_pairs():
    """(login, hasło) ze wszystkich znanych zmiennych skrzynek w Railway."""
    pairs = []
    env = os.environ
    for k, v in env.items():
        for pre_user, pre_pass in (("SMTP_USER_", "SMTP_PASS_"), ("IMAP_USER_", "IMAP_PASS_"), ("MAIL_USER_", "MAIL_PASS_")):
            if k.startswith(pre_user) and v.strip():
                pwd = env.get(pre_pass + k[len(pre_user):], "").strip()
                if pwd:
                    pairs.append((v.strip().lower(), pwd))
    for u, p in (("INBOX_IMAP_USER", "INBOX_IMAP_PASS"), ("SMTP_USER", "SMTP_PASS")):
        if env.get(u, "").strip() and env.get(p, "").strip():
            pairs.append((env[u].strip().lower(), env[p].strip()))
    return pairs


def mail_boxes(code="all"):
    """Skrzynki zakładki Poczta: [{login, pwd, host, port, label, key, market, user_id}].
    Lista rynku: MAILBOXES_LT / MAILBOXES_DE (po przecinku) albo domyślna (o.latek@ i kontakt@33bots.lt/.de);
    code="all" — wszystkie rynki. Hasło z pasującej zmiennej (SMTP_/IMAP_/MAIL_USER_X + _PASS_X, INBOX_IMAP_*),
    a gdy brak — MAIL_PASSWORD (wspólne hasło skrzynek)."""
    if code == "all":
        return [b for mc in markets.MARKETS for b in mail_boxes(mc)]
    m = markets.get(code)
    raw = os.environ.get(f"MAILBOXES_{m['code'].upper()}", "").strip() or (
        os.environ.get("MAILBOXES", "").strip() if m["code"] == "lt" else "") or m["mailboxes"]
    pairs = dict(reversed(_env_pairs()))
    common = os.environ.get("MAIL_PASSWORD", "").strip()
    host = os.environ.get("IMAP_HOST", "").strip() or os.environ.get("SMTP_HOST", "").strip()
    out = []
    for login in [x.strip().lower() for x in raw.split(",") if x.strip()]:
        pwd = pairs.get(login) or common
        key = re.sub(r"[^A-Z0-9]", "_", login.split("@")[0].upper() + "_" + login.split("@")[-1].split(".")[-1].upper())
        owner = next((u for u in USERS if (u.get("email") or "").split("@")[0].lower() == login.split("@")[0]), None)
        local = login.split("@")[0]
        label = (owner.get("full_name") if owner else None) or (
            "Oliwier Latek" if local.startswith("o.latek") else "33Bots" if local in ("kontakt", "info", "hi") else login)
        out.append({"login": login, "pwd": pwd, "key": key, "label": label, "market": m["code"],
                    "host": os.environ.get(f"IMAP_HOST_{key}", "").strip() or host,
                    "port": int(os.environ.get(f"IMAP_PORT_{key}", "").strip() or os.environ.get("IMAP_PORT", 993)),
                    "user_id": owner["id"] if owner else None})
    return out


def mail_box(login):
    return next((b for b in mail_boxes() if b["login"] == (login or "").lower()), None)


def _mail_state_key(login, folder):
    return f"mailsync:{login}:{folder}"[:64]


def _mail_state(login, folder):
    try:
        return json.loads(get_setting(_mail_state_key(login, folder)) or "{}")
    except ValueError:
        return {}


def _mail_set_state(login, folder, **kw):
    st = _mail_state(login, folder)
    st.update(kw)
    set_setting(_mail_state_key(login, folder), json.dumps(st, default=str)[:2000])


def _mail_store(box, kind, uidvalidity, uid, data, archive=False):
    """Zapisuje jeden mail. Wysłany z CRM (uid=None, ten sam Message-ID) dostaje tylko uid z Wysłanych."""
    if data["message_id"]:
        pending = MailMessage.query.filter_by(mailbox=box["login"], folder=kind, uid=None,
                                              message_id=data["message_id"]).first()
        if pending:
            pending.uid, pending.uidvalidity = uid, uidvalidity
            return pending
    if MailMessage.query.filter_by(mailbox=box["login"], folder=kind, uidvalidity=uidvalidity, uid=uid).first():
        return None
    old_mail = archive and data["date"] and data["date"] < datetime.utcnow() - timedelta(days=2)
    row = MailMessage(mailbox=box["login"], market=box["market"], folder=kind, uid=uid, uidvalidity=uidvalidity,
                      seen=bool(kind == "sent" or old_mail),  # archiwum nie świeci się jako nowe
                      **{k: v for k, v in data.items() if k not in ("attachments", "truncated")})
    row.attachments = json.dumps(data["attachments"], ensure_ascii=False)
    row.truncated = data["truncated"]
    lang = poczta.detect_lang(f"{data['subject']}\n{data['body_text'][:3000]}")
    row.lang = lang or None
    if lang == "pl":  # po polsku — tłumaczenie niepotrzebne
        row.subject_pl, row.body_pl, row.translated_at = row.subject, None, datetime.utcnow()
    db.session.add(row)
    return row


def mail_sync_box(box, max_per_folder=MAIL_BATCH):
    """Jedna porcja synchronizacji skrzynki (najstarsze nieściągnięte najpierw — archiwum dochodzi stopniowo).
    Zwraca dict {nowe, zostało, błąd}."""
    res = {"new": 0, "left": 0, "error": ""}
    if not (box["pwd"] and box["host"]):
        res["error"] = "brak hasła w Railway (MAIL_PASSWORD albo SMTP_/IMAP_PASS dla tej skrzynki)"
        return res
    try:
        im = poczta.connect(box["host"], box["port"], box["login"], box["pwd"])
    except poczta.MailboxError as e:
        res["error"] = str(e)
        return res
    try:
        folders = [("in", "INBOX")]
        sent = poczta.sent_folder(im, os.environ.get("IMAP_SENT_FOLDER", "").strip())
        if sent:
            folders.append(("sent", sent))
        for kind, fname in folders:
            try:
                validity = poczta.select(im, fname)
            except poczta.MailboxError as e:
                res["error"] = str(e)
                continue
            st = _mail_state(box["login"], kind)
            last = int(st.get("last_uid") or 0) if st.get("uidvalidity") == validity else 0
            archive = bool(st.get("archive", True)) if last else True  # pierwsze pobieranie = archiwum
            uids = poczta.uids_after(im, last)
            batch = uids[:max_per_folder]
            res["left"] += max(0, len(uids) - len(batch))
            for i in range(0, len(batch), 25):
                chunk = batch[i:i + 25]
                for uid, raw, big in poczta.fetch_batch(im, chunk):
                    try:
                        data = poczta.parse(raw, truncated=big)
                    except Exception:
                        continue
                    if _mail_store(box, kind, validity, uid, data, archive=archive) is not None:
                        res["new"] += 1
                _mail_set_state(box["login"], kind, uidvalidity=validity, last_uid=chunk[-1],
                                total=int(st.get("total") or 0) + len(chunk))
                st = _mail_state(box["login"], kind)
                db.session.commit()
            _mail_set_state(box["login"], kind, uidvalidity=validity, left=max(0, len(uids) - len(batch)),
                            synced_at=datetime.utcnow().isoformat(timespec="seconds"), error="",
                            archive=len(uids) > len(batch))  # archiwum skończone = kolejne maile są „nowe”
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        res["error"] = f"{type(e).__name__}: {str(e)[:150]}"
    finally:
        poczta.close(im)
    for kind in ("in", "sent"):
        if res["error"]:
            _mail_set_state(box["login"], kind, error=res["error"])
    db.session.commit()
    return res


def mail_sync_all():
    if not _mail_sync_lock.acquire(blocking=False):
        return {}
    try:
        return {b["login"]: mail_sync_box(b) for b in mail_boxes()}
    finally:
        _mail_sync_lock.release()


# --- tłumaczenie ---

MAIL_TRANSLATE_TOOL = {
    "name": "translation",
    "description": "Tłumaczenie maila.",
    "input_schema": {"type": "object", "properties": {
        "source_lang": {"type": "string", "description": "Kod języka oryginału: lt, de, en, pl, ru, lv…"},
        "subject": {"type": "string", "description": "Temat po przetłumaczeniu"},
        "body": {"type": "string", "description": "Treść po przetłumaczeniu"},
    }, "required": ["source_lang", "subject", "body"]},
}


def ai_translate(subject, body, target="pl", tone_hint="", model=None, insist=False):
    """(język źródłowy, temat, treść) przetłumaczone na `target` przez Claude. Rzuca wyjątek przy błędzie."""
    name = {"pl": "polski", "lt": "litewski", "de": "niemiecki", "en": "angielski"}.get(target, target)
    name_en = {"pl": "Polish", "lt": "Lithuanian", "de": "German", "en": "English"}.get(target, target)
    system = (f"Jesteś tłumaczem korespondencji firmy 33Bots (wynajem humanoidalnych robotów Unitree G1 na eventy). "
              f"Tłumacz wiernie na język {name} ({name_en}) — wynik (subject i body) MUSI być w całości w języku {name}, "
              f"niezależnie od języka oryginału (niemiecki, litewski, angielski…). Zachowaj akapity, listy, nazwiska, "
              f"nazwy firm, adresy, numery, kwoty, daty i linki. Nie dodawaj komentarzy ani wyjaśnień. "
              f"Tylko gdy oryginał już jest w całości w języku {name}, przepisz go bez zmian."
              + (f" {tone_hint}" if tone_hint else "")
              + (f" UWAGA: poprzednia próba zwróciła tekst w złym języku — tym razem przetłumacz KAŻDE zdanie "
                 f"na język {name}." if insist else ""))
    args = _ai_forced_tool(model or MAIL_TRANSLATE_MODEL, system, f"TEMAT:\n{subject or ''}\n\nTREŚĆ:\n{body or ''}",
                           MAIL_TRANSLATE_TOOL, max_tokens=8000)
    return (str(args.get("source_lang") or "")[:8].lower(), str(args.get("subject") or "")[:600],
            str(args.get("body") or ""))


def translation_ok(src_body, out_body, target):
    """Czy tłumaczenie naprawdę jest w języku docelowym (a nie np. oryginał przepisany bez zmian)."""
    out = (out_body or "").strip()
    src = (src_body or "").strip()
    if not src:
        return True
    if not out:
        return False
    src_lang = poczta.detect_lang(src)
    out_lang = poczta.detect_lang(out)
    if out_lang and out_lang != target:
        return False
    if src_lang and src_lang != target and " ".join(out.split()) == " ".join(src.split()):
        return False
    return True


def ai_translate_checked(subject, body, target="pl", tone_hint=""):
    """ai_translate ze sprawdzeniem języka: zły wynik → druga próba mocniejszym modelem z wyraźnym poleceniem.
    Gdy dalej źle — wyjątek (lepiej błąd niż oryginał pokazany jako tłumaczenie)."""
    lang, subj, text = ai_translate(subject, body, target, tone_hint)
    if translation_ok(body, text, target):
        return lang, subj, text
    lang, subj, text = ai_translate(subject, body, target, tone_hint, model=MAIL_TRANSLATE_STRONG_MODEL, insist=True)
    if translation_ok(body, text, target):
        return lang, subj, text
    raise RuntimeError(f"model zwrócił tekst, który nie jest po {'polsku' if target == 'pl' else target}")


def mail_translate(row, force=False):
    """Tłumaczy mail na polski (nowa część; cytowaną historię zostawia w oryginale). Zwraca błąd albo None."""
    if row.translated_at and not force:
        return None
    fresh, _quoted = poczta.split_quote(row.body_text or "")
    try:
        lang, subj, body = ai_translate_checked(row.subject or "", fresh[:12000], "pl")
    except Exception as e:
        row.translate_error = f"{type(e).__name__}: {str(e)[:200]}"
        db.session.commit()
        return row.translate_error
    row.lang = lang or row.lang
    row.subject_pl, row.body_pl = subj, body
    row.translated_at, row.translate_error = datetime.utcnow(), None
    db.session.commit()
    return None


MAIL_SUBJECTS_TOOL = {
    "name": "subjects",
    "description": "Przetłumaczone tematy maili.",
    "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {
        "type": "object", "properties": {"id": {"type": "integer"}, "subject": {"type": "string"}},
        "required": ["id", "subject"]}}}, "required": ["items"]},
}


def mail_translate_subjects(rows):
    """Tłumaczy na polski tematy wielu maili jednym zapytaniem (lista w Poczcie)."""
    todo = [r for r in rows if not r.subject_pl and (r.subject or "").strip()]
    if not todo:
        return {}
    listing = "\n".join(f"{r.id}: {r.subject}" for r in todo[:40])
    args = _ai_forced_tool(MAIL_TRANSLATE_MODEL,
                           "Przetłumacz tematy maili na język polski (Polish) — każdy temat w obcym języku (niemiecki, "
                           "litewski, angielski…) MUSI być po polsku. Prefiksy AW:/WG:/Ats:/Re:/Fwd: zamień na Re:/Fwd:, "
                           "zachowaj nazwy firm i numery. Tylko temat już po polsku przepisz bez zmian.", listing, MAIL_SUBJECTS_TOOL, max_tokens=4000)
    got = {}
    for item in args.get("items") or []:
        try:
            rid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        got[rid] = str(item.get("subject") or "")[:600]
    for r in todo:
        if r.id not in got:
            continue
        if got[r.id].strip() == (r.subject or "").strip() and poczta.detect_lang(r.subject) not in ("", "pl"):
            got.pop(r.id)  # „przetłumaczony” temat został w obcym języku — przetłumaczy go tłumaczenie treści
            continue
        r.subject_pl = got[r.id]
    db.session.commit()
    return got


MAIL_RETRANSLATE_KEY = "mail_retranslate_v1"


def mail_fix_bad_translations(days=120):
    """Raz: maile, których „tłumaczenie” zostało w obcym języku (np. niemiecki przepisany bez zmian), idą do
    ponownego tłumaczenia."""
    if get_setting(MAIL_RETRANSLATE_KEY):
        return 0
    since = datetime.utcnow() - timedelta(days=days)
    n = 0
    for r in all_markets(MailMessage.query.filter(MailMessage.translated_at.isnot(None),
                                                  MailMessage.date >= since)).all():
        if (r.lang or "") == "pl":
            continue
        fresh = poczta.split_quote(r.body_text or "")[0]
        if not translation_ok(fresh, r.body_pl, "pl"):
            r.body_pl = r.subject_pl = r.translated_at = r.translate_error = None
            n += 1
        elif r.subject_pl and r.subject and r.subject_pl.strip() == r.subject.strip() and \
                poczta.detect_lang(r.subject) not in ("", "pl"):
            r.subject_pl = None
            n += 1
    set_setting(MAIL_RETRANSLATE_KEY, f"{datetime.utcnow():%Y-%m-%d %H:%M} ({n})")
    db.session.commit()
    return n


def mail_auto_translate(limit=15):
    """W tle: tłumaczy świeże maile od klientów (ostatnie MAIL_AUTO_TRANSLATE_DAYS dni) zanim ktoś je otworzy."""
    if not _ai_client():
        return 0
    since = datetime.utcnow() - timedelta(days=MAIL_AUTO_TRANSLATE_DAYS)
    rows = (MailMessage.query.filter(MailMessage.translated_at.is_(None), MailMessage.translate_error.is_(None),
                                     MailMessage.date >= since)
            .order_by(MailMessage.date.desc()).limit(limit).all())
    for r in rows:
        mail_translate(r)
    return len(rows)


def _mail_scheduler():
    time.sleep(20)
    while True:
        try:
            res = None
            with app.app_context():
                if mail_boxes():
                    res = mail_sync_all()
                    mail_fix_bad_translations()
                    mail_auto_translate()
                db.session.remove()
            for login, r in (res or {}).items():  # {} = poprzednia synchronizacja jeszcze trwa
                health_beat(f"poczta:{login}", not r.get("error"), info=f"{r.get('new', 0)} nowych",
                            error=r.get("error") or "")
        except Exception as e:
            app.logger.exception("mail_sync")
            health_fail("poczta", e)
        time.sleep(MAIL_SYNC_SECONDS)


if os.environ.get("MAIL_SYNC", "1") != "0":
    _start_worker("mail", _mail_scheduler)


# --- powiązanie z leadami ---

def mail_query_for_addrs(addrs):
    addrs = [a.strip().lower() for a in addrs if a and "@" in a]
    if not addrs:
        return None
    return MailMessage.query.filter(db.or_(*[MailMessage.addrs.like(f"% {a} %") for a in addrs]))


def mail_lead_for(row):
    """Lead, z którym związany jest mail (adres klienta w Od/Do/DW)."""
    own = _own_addresses()
    for addr in (row.addrs or "").split():
        if addr in own or addr.split("@")[-1] in OWN_DOMAINS:
            continue
        lead = Lead.query.filter(db.or_(db.func.lower(Lead.contact_email) == addr,
                                        db.func.lower(Lead.contact_email2) == addr)).order_by(Lead.created_at.desc()).first()
        if lead:
            return lead
    return None


def mail_unseen_count():
    try:
        return MailMessage.query.filter(MailMessage.folder == "in", MailMessage.seen.isnot(True),
                                        MailMessage.date >= datetime.utcnow() - timedelta(days=30)).count()
    except Exception:
        db.session.rollback()
        return 0


def _mail_json(row):
    return json.loads(row.attachments or "[]")


def mail_signature(box, user=None):
    """Podpis: właściciel skrzynki osobistej, a przy wspólnej (kontakt@) — osoba, która pisze."""
    owner = USERS_BY_ID.get(box.get("user_id")) if box.get("user_id") else None
    if box["login"].startswith("o.latek"):
        owner = owner or USERS_BY_ID.get(1)
    owner = owner or user
    name = (owner or {}).get("full_name") or box["label"]
    phone = mail_phone((owner or {}).get("phone") or "", box.get("market"))
    return "\n".join(x for x in (markets.get(box.get("market"))["bye"], name, "33Bots", box["login"], phone) if x)


# --- trasy ---

@app.route("/crm/poczta")
def crm_mail():
    box = request.args.get("box", "").strip().lower()
    folder = request.args.get("f", "in")
    q = request.args.get("q", "").strip()
    lead_id = request.args.get("lead", type=int)
    page = max(1, request.args.get("p", type=int) or 1)
    query = MailMessage.query
    if box:
        query = query.filter(MailMessage.mailbox == box)
    if folder in ("in", "sent"):
        query = query.filter(MailMessage.folder == folder)
    lead = db.session.get(Lead, lead_id) if lead_id else None
    if lead:
        addrs = [a.lower() for a in lead.emails]
        query = query.filter(db.or_(*[MailMessage.addrs.like(f"% {a} %") for a in addrs])) if addrs else query.filter(db.false())
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(MailMessage.subject.ilike(like), MailMessage.subject_pl.ilike(like),
                                    MailMessage.addrs.ilike(like), MailMessage.from_name.ilike(like),
                                    MailMessage.body_text.ilike(like), MailMessage.body_pl.ilike(like)))
    total = query.count()
    rows = query.order_by(MailMessage.date.desc().nullslast(), MailMessage.id.desc()).offset((page - 1) * 50).limit(50).all()
    sel = db.session.get(MailMessage, request.args.get("m", type=int) or 0)
    thread, sel_lead, atts = [], None, []
    if sel:
        if not sel.seen:
            sel.seen = True
            db.session.commit()
        if sel.thread_key:
            thread = (MailMessage.query.filter(MailMessage.thread_key == sel.thread_key, MailMessage.id != sel.id)
                      .order_by(MailMessage.date).limit(30).all())
        sel_lead = mail_lead_for(sel)
        atts = _mail_json(sel)
    status = []
    for b in mail_boxes(current_market()):
        sti, sts = _mail_state(b["login"], "in"), _mail_state(b["login"], "sent")
        status.append({"login": b["login"], "label": b["label"], "ready": bool(b["pwd"] and b["host"]),
                       "count": MailMessage.query.filter_by(mailbox=b["login"]).count(),
                       "left": int(sti.get("left") or 0) + int(sts.get("left") or 0),
                       "synced_at": sti.get("synced_at") or "", "error": sti.get("error") or sts.get("error") or ""})
    fresh, quoted = poczta.split_quote(sel.body_text or "") if sel else ("", "")
    quoted_lang = poczta.detect_lang(quoted) if quoted else ""
    return render_template(
        "crm/poczta.html", rows=rows, sel=sel, thread=thread, sel_lead=sel_lead, atts=atts, fresh=fresh, quoted=quoted,
        quoted_tr=bool(quoted) and quoted_lang != "pl",
        box=box, folder=folder, q=q, page=page, total=total, pages=max(1, (total + 49) // 50), lead=lead,
        status=status, lang_names=MAIL_LANG_NAMES, lang_flags=MAIL_LANG_FLAGS, target_lang=M()["lang"],
        ai_ok=bool(_ai_client()), syncing=_mail_sync_lock.locked(),
    )


@app.route("/crm/poczta/sync", methods=["POST"])
def crm_mail_sync():
    def run():
        with app.app_context():
            try:
                mail_sync_all()
                mail_auto_translate()
            finally:
                db.session.remove()
    if not _mail_sync_lock.locked():
        threading.Thread(target=run, daemon=True).start()
        time.sleep(2)
    return redirect(request.referrer or url_for("crm_mail"))


@app.route("/crm/poczta/<int:mid>/tlumacz", methods=["POST"])
def crm_mail_translate(mid):
    row = db.session.get(MailMessage, mid) or abort(404)
    err = mail_translate(row, force=request.form.get("force") == "1")
    if err:
        return jsonify({"error": f"Tłumaczenie nie wyszło: {err}"}), 502
    return jsonify({"ok": True, "lang": row.lang, "subject": row.subject_pl or row.subject,
                    "body": row.body_pl if row.body_pl is not None else poczta.split_quote(row.body_text or "")[0]})


QUOTED_TRANSLATE_MAX = 12000  # tyle znaków historii tłumaczymy (dłuższa — początek, z dopiskiem)


@app.route("/crm/poczta/<int:mid>/tlumacz-cytat", methods=["POST"])
def crm_mail_translate_quoted(mid):
    """Tłumaczenie wcześniejszej korespondencji (cytatu) na polski — przy pierwszym rozwinięciu, potem z bazy."""
    row = db.session.get(MailMessage, mid) or abort(404)
    _fresh, quoted = poczta.split_quote(row.body_text or "")
    if not quoted.strip():
        return jsonify({"ok": True, "body": ""})
    if row.quoted_pl and request.form.get("force") != "1":
        return jsonify({"ok": True, "body": row.quoted_pl})
    part = quoted[:QUOTED_TRANSLATE_MAX]
    try:
        _lang, _subj, body = ai_translate_checked("", part, "pl")
    except Exception as e:
        return jsonify({"error": f"Tłumaczenie nie wyszło: {type(e).__name__}: {str(e)[:160]}"}), 502
    if len(quoted) > QUOTED_TRANSLATE_MAX:
        body += "\n\n[… dalsza część historii bez tłumaczenia — jest w oryginale obok]"
    row.quoted_pl = body
    db.session.commit()
    return jsonify({"ok": True, "body": body})


@app.route("/crm/poczta/tematy", methods=["POST"])
def crm_mail_translate_subjects():
    ids = [int(x) for x in (request.form.get("ids") or "").split(",") if x.isdigit()][:40]
    rows = MailMessage.query.filter(MailMessage.id.in_(ids)).all() if ids else []
    try:
        got = mail_translate_subjects(rows)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}"}), 502
    return jsonify({"ok": True, "subjects": {str(k): v for k, v in got.items()}})


@app.route("/crm/poczta/<int:mid>/zalacznik/<int:n>")
def crm_mail_attachment(mid, n):
    row = db.session.get(MailMessage, mid) or abort(404)
    box = mail_box(row.mailbox)
    if not box or row.uid is None:
        abort(404)
    try:
        im = poczta.connect(box["host"], box["port"], box["login"], box["pwd"])
    except poczta.MailboxError:
        abort(502)
    try:
        fname = "INBOX" if row.folder == "in" else poczta.sent_folder(im, os.environ.get("IMAP_SENT_FOLDER", "").strip())
        validity = poczta.select(im, fname)
        if validity != row.uidvalidity:
            abort(404)
        raw = poczta.fetch_raw(im, row.uid)
    finally:
        poczta.close(im)
    got = poczta.attachment_bytes(raw, n) if raw else None
    if not got:
        abort(404)
    name, mime, data = got
    resp = make_response(data)
    resp.headers["Content-Type"] = mime or "application/octet-stream"
    inline = mime in ("application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp")
    resp.headers["Content-Disposition"] = f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(name)}"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "sandbox"
    return resp


@app.route("/crm/poczta/<int:mid>/nowy-lead", methods=["POST"])
def crm_mail_new_lead(mid):
    """Nowy lead z maila (np. zapytanie, które przyszło prosto na skrzynkę)."""
    row = db.session.get(MailMessage, mid) or abort(404)
    existing = mail_lead_for(row)
    if existing:
        return redirect(url_for("crm_lead_detail", lead_id=existing.id))
    addr = row.from_addr if row.folder == "in" else (row.to_addrs or "").split(",")[0].strip()
    domain = addr.split("@")[-1] if addr else ""
    company = row.from_name if row.folder == "in" and row.from_name else (domain.split(".")[0].capitalize() if domain else "Lead z maila")
    user = get_current_user(request)
    lead = Lead(company=company[:200], contact_name=(row.from_name or "")[:200] if row.folder == "in" else "",
                contact_email=addr, stage="Nowy", assigned_to=user["id"], currency="EUR", source="Mail",
                market=row.market or current_market(),
                notes=f"Z maila „{row.subject or ''}” ({row.date:%Y-%m-%d} )".replace(" )", ")") if row.date else "")
    db.session.add(lead)
    db.session.flush()
    db.session.add(StageHistory(lead_id=lead.id, from_stage=None, to_stage="Nowy", changed_by=user["id"]))
    db.session.add(Activity(lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(), assigned_to=user["id"],
                            notes=f"📬 Mail od {row.from_addr}: {row.subject or ''}\n---\n{(row.body_text or '')[:3000]}"
                                  + (f"\n---\n🇵🇱 {row.body_pl[:3000]}" if row.body_pl else "")))
    db.session.commit()
    return redirect(url_for("crm_lead_detail", lead_id=lead.id))


@app.route("/crm/poczta/przetlumacz", methods=["POST"])
def crm_mail_translate_draft():
    """Tłumaczenie szkicu w okienku pisania: polski → język klienta (albo odwrotnie: to=pl)."""
    target = request.form.get("to", M()["lang"])
    if target not in MAIL_LANG_NAMES:
        target = M()["lang"]
    subject = (request.form.get("subject") or "")[:500]
    body = (request.form.get("body") or "")[:12000]
    if not (subject.strip() or body.strip()):
        return jsonify({"ok": True, "subject": "", "body": ""})
    tone = ("To mail firmy do klienta: grzeczny, profesjonalny, naturalny język biznesowy jak u native speakera "
            "(w litewskim forma grzecznościowa „Jūs”, w niemieckim „Sie”).") if target != "pl" else ""
    try:
        _lang, subj, text = ai_translate_checked(subject, body, target, tone_hint=tone)
    except Exception as e:
        return jsonify({"error": f"Tłumaczenie nie wyszło: {type(e).__name__}"}), 502
    return jsonify({"ok": True, "subject": subj, "body": text})


# --- Claude pisze maila (odpowiedź albo nowy) — po polsku i od razu w języku klienta ---
MAIL_REPLY_AI_MODEL = os.environ.get("MAIL_REPLY_AI_MODEL", "").strip() or "claude-opus-5-5"
MAIL_DRAFT_TOOL = {
    "name": "write_email",
    "description": "Gotowy mail do klienta: wersja po polsku (do wglądu dla zespołu) i wersja w języku klienta (wysyłana).",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "subject_pl": {"type": "string", "description": "Temat po polsku"},
            "body_pl": {"type": "string", "description": "Treść po polsku — bez podpisu i bez formuły pożegnalnej"},
            "subject_out": {"type": "string", "description": "Ten sam temat w języku klienta"},
            "body_out": {"type": "string",
                         "description": "Ta sama treść w języku klienta — bez podpisu i bez formuły pożegnalnej"},
        },
        "required": ["subject_pl", "body_pl", "subject_out", "body_out"],
    },
}


def _dt(value):
    return f"{value:%Y-%m-%d %H:%M}" if value else "bez daty"


def _mail_draft_context(code, reply_id=None, lead_id=None, pasted="", to=""):
    """Tekst z kontekstem dla Claude'a: mail, na który odpowiadamy (z wątkiem), dane leada i policzona cena."""
    parts = []
    reply = db.session.get(MailMessage, reply_id) if reply_id else None
    lead = db.session.get(Lead, lead_id) if lead_id else None
    if reply:
        fresh, _old = poczta.split_quote(reply.body_text or "")
        who = f"{reply.from_name} <{reply.from_addr}>" if reply.from_name else reply.from_addr
        parts.append(f"MAIL, NA KTÓRY ODPOWIADAMY ({'od klienta' if reply.folder == 'in' else 'wysłany przez nas'}, "
                     f"{_dt(reply.date)} od {who}):\nTemat: {reply.subject or ''}\n{fresh[:8000]}"
                     + (f"\n\n(Tłumaczenie na polski: {reply.body_pl[:6000]})" if reply.body_pl else ""))
        if reply.thread_key:
            earlier = (MailMessage.query.filter(MailMessage.thread_key == reply.thread_key, MailMessage.id != reply.id)
                       .order_by(MailMessage.date.desc()).limit(6).all())
            for m in reversed(earlier):
                f2, _ = poczta.split_quote(m.body_text or "")
                parts.append(f"WCZEŚNIEJ W WĄTKU ({'klient' if m.folder == 'in' else 'my'}, {_dt(m.date)}): "
                             f"{m.subject or ''}\n{f2[:1500]}")
        lead = lead or mail_lead_for(reply)
    if lead:
        days = ", ".join(f"{d.event_date:%Y-%m-%d}" for d in sorted(lead.event_days, key=lambda d: d.event_date))
        info = [f"Firma: {lead.company}", f"Osoba: {lead.contact_name or '—'}", f"Etap: {lead.stage}",
                f"Dni wydarzenia: {days or '—'}", f"Miejsce: {lead.location or '—'}"]
        if lead.value_pln:
            info.append(f"Wartość w CRM: {lead.value_pln:g} {lead.currency or 'EUR'} netto")
        try:
            with app.test_request_context(base_url=public_base_url() or "http://localhost"):
                q = lt_quote(lead, remote_budget=1)
        except Exception:
            q = {}
        if q.get("price"):
            info.append(f"Cena z kalkulatora CRM: {q['price']} € netto ({q['time']}, {q['place']}"
                        + (f", {q['when']}" if q.get("when") else "") + ")"
                        + (" — godziny ZAŁOŻONE, nie od klienta" if q.get("assumed") else ""))
        if lead.notes:
            info.append(f"Notatki: {lead.notes[:1500]}")
        notes = [a.notes for a in sorted(lead.activities, key=lambda a: a.created_at or datetime.min, reverse=True)
                 if a.kind == "note" and a.notes][:3]
        if notes:
            info.append("Ostatnie komentarze w CRM:\n" + "\n".join("- " + n[:500] for n in notes))
        parts.append("DANE LEADA Z CRM:\n" + "\n".join(info))
    elif to:
        parts.append(f"Adresat: {to}")
    if pasted.strip():
        parts.append(f"TEKST WKLEJONY PRZEZ UŻYTKOWNIKA (np. stary mail albo notatka):\n{pasted[:10000]}")
    return "\n\n".join(parts)


def mail_ai_draft(code, instruction, pasted="", reply_id=None, lead_id=None, to="", writer_id=None,
                  draft_pl="", draft_out="", is_reply=False, offer_price=""):
    """Claude pisze maila: polska wersja + ta sama treść w języku rynku (lt/de). Zadanie w tle — zwraca {'draft': …}."""
    m = markets.get(code)
    writer = USERS_BY_ID.get(writer_id) or USERS[0]
    system = (
        "Piszesz maile firmy 33Bots do klientów. 33Bots wynajmuje humanoidalnego robota Unitree G1 na wydarzenia "
        "(targi, konferencje, eventy firmowe) razem z operatorem: dojazd, przygotowanie robota na miejscu, "
        "dopasowanie osobowości i wiedzy robota do wydarzenia, wsparcie techniczne; robot wita gości, rozmawia "
        "na podstawie przygotowanej bazy wiedzy, tańczy i pozuje do zdjęć. Ceny netto w EUR. "
        f"Zdjęcia z realizacji: {OFFER_PHOTOS_URL}\n"
        f"Klient jest z rynku: {m['name']} — wersja dla klienta (subject_out, body_out) ZAWSZE {m['lang_in_pl']}, "
        "naturalnie, jak native speaker, forma grzecznościowa (" + ("„Jūs”" if code == "lt" else "„Sie”") + "); "
        "nigdy po angielsku. subject_pl i body_pl to dokładnie ta sama treść po polsku (dla zespołu).\n"
        f"Piszesz w imieniu: {writer.get('full_name') or writer['name']} (pierwsza osoba). "
        f"Powitanie: {m['ai_greeting'].split('…')[0].strip()} "
        + ("(bez imienia — litewski wołacz łatwo pomylić)" if code == "lt" else
           "(albo „Sehr geehrte Frau X,” / „Sehr geehrter Herr X,”, gdy znasz nazwisko)") + ". "
        "NIE dodawaj formuły pożegnalnej ani podpisu — podpis z pożegnaniem dokleja się sam.\n"
        "Zasady: odpowiadaj konkretnie na to, o co pyta klient, i na polecenie użytkownika. Nie wymyślaj cen, dat, "
        "dostępności ani obietnic — używaj tylko tego, co jest w poleceniu albo w danych z CRM; jeśli czegoś "
        "brakuje (np. godzin, miejsca), grzecznie dopytaj. Krótkie akapity, ton ciepły i profesjonalny, bez "
        "nagłówków i list w punktach, chyba że polecenie o nie prosi. Nigdy nie wstawiaj linków do formularzy. "
        "Nie pisz 'Edward Warchocki' — tylko 'Unitree G1'.\n" + HUMAN_TYPO_RULE
    )
    ctx = _mail_draft_context(code, reply_id, lead_id, pasted, to)
    task = (f"POLECENIE UŻYTKOWNIKA: {instruction.strip() or 'Napisz odpowiedź na tego maila.'}\n\n"
            + (ctx + "\n\n" if ctx else "")
            + (f"OBECNY SZKIC (popraw go zgodnie z poleceniem):\nPL: {draft_pl[:4000]}\n"
               f"{m['lang_name_pl']}: {draft_out[:4000]}\n\n" if (draft_pl.strip() or draft_out.strip()) else "")
            + ("To odpowiedź w istniejącym wątku — temat zostaw jako „Re: …”." if is_reply else "")
            + (f"\nDo maila dołączona jest oferta PDF (cena {offer_price} netto) — napisz, że jest w załączniku, "
               "i podaj tę cenę." if offer_price else ""))
    res = {}
    for attempt in range(2):
        res = _ai_forced_tool(MAIL_REPLY_AI_MODEL, system, task, MAIL_DRAFT_TOOL, max_tokens=4000)
        bad = wrong_language(code, res.get("subject_out"), res.get("body_out"))
        if not bad and str(res.get("body_out") or "").strip():
            break
        task += (f"\n\nUWAGA: poprzednia wersja dla klienta nie była {m['lang_in_pl']} (wykryto: {bad or 'pusto'}). "
                 f"Napisz subject_out i body_out w całości {m['lang_in_pl']}.")
    else:
        return {"error": f"Claude nie napisał wersji {m['lang_in_pl']} — spróbuj jeszcze raz"}
    draft = {k: human_text(str(res.get(k) or "").strip()) for k in ("subject_pl", "body_pl", "subject_out", "body_out")}
    return {"draft": draft, "info": "gotowe"}


@app.route("/crm/poczta/generuj", methods=["POST"])
def crm_mail_ai_draft():
    """✨ w okienku pisania: Claude pisze maila (w tle — długie odpowiedzi nie blokują serwera)."""
    if _ai_client() is None:
        return jsonify({"error": "Brak ANTHROPIC_API_KEY w Railway — Claude nie może pisać maili."}), 400
    f = request.form
    instruction = (f.get("instruction") or "").strip()[:3000]
    pasted = (f.get("pasted") or "")[:12000]
    reply_id, lead_id = f.get("reply", type=int), f.get("lead", type=int)
    if not instruction and not pasted.strip() and not reply_id:
        return jsonify({"error": "Napisz Claude'owi, co ma być w mailu (albo wklej maila, na który odpowiadasz)."}), 400
    if reply_id and not db.session.get(MailMessage, reply_id):
        reply_id = None
    if lead_id and not db.session.get(Lead, lead_id):
        lead_id = None
    jid = _start_job("Claude pisze maila", mail_ai_draft, current_market(), instruction, pasted, reply_id, lead_id,
                     (f.get("to") or "")[:500], get_current_user(request)["id"], (f.get("draft_pl") or "")[:6000],
                     (f.get("draft_out") or "")[:6000], bool(reply_id), (f.get("offer") or "")[:40])
    return jsonify({"jid": jid, "poll": url_for("crm_job", jid=jid, format="json")})


# --- oferta PDF z okna pisania maila ---
COMPOSE_OFFER_TOOL = {
    "name": "offer_pdf",
    "description": "Treść oferty PDF (2 strony, firmowy wygląd 33Bots) dopasowana do wydarzenia klienta.",
    "input_schema": {
        "type": "object",
        "properties": {
            "client": {"type": "string", "description": "Oficjalna nazwa klienta (firma / instytucja), max 80 znaków"},
            "subtitle": {"type": "string", "description": "1 zdanie pod tytułem okładki: czym robot będzie na tym wydarzeniu (max 200 znaków)"},
            "cover": {"type": "array", "description": "3 pola okładki, np. data / miejsce / godziny",
                      "items": {"type": "object", "properties": {
                          "label": {"type": "string", "description": "max 20 znaków, WIELKIE LITERY"},
                          "value": {"type": "string", "description": "max 32 znaki"}}, "required": ["label", "value"]}},
            "intro": {"type": "string", "description": "Akapit strony 2 (max 330 znaków): co robot zrobi na tym wydarzeniu — "
                                                       "wita gości, rozmawia na podstawie bazy wiedzy, tańczy, pozuje do zdjęć"},
            "cards": {"type": "array", "description": "Dokładnie 2 karty: rola robota na wydarzeniu i przygotowanie",
                      "items": {"type": "object", "properties": {
                          "label": {"type": "string", "description": "max 22 znaki, WIELKIE LITERY"},
                          "title": {"type": "string", "description": "max 30 znaków"},
                          "items": {"type": "array", "items": {"type": "string"},
                                    "description": "dokładnie 3 krótkie punkty, każdy max 55 znaków"}},
                          "required": ["label", "title", "items"]}},
            "price_title": {"type": "string", "description": "np. czas obecności robota (max 55 znaków)"},
            "price_sub": {"type": "string", "description": "miejsce · data (max 85 znaków)"},
        },
        "required": ["client", "subtitle", "cover", "intro", "cards", "price_title", "price_sub"],
    },
}


def _price_text(raw, currency="€"):
    """'2100' / '2 100' / '2100 eur' → '2 100 €'; tekst z walutą zostaje."""
    raw = (raw or "").strip()
    digits = re.sub(r"[^\d]", "", raw)
    if digits and re.fullmatch(r"[\d\s.,]*(eur|€|euro)?", raw.lower().replace(" ", "")):
        return f"{int(digits):,}".replace(",", " ") + f" {currency}"
    return raw[:20]


def _compose_offer_sender(box_login, writer_id):
    box = mail_box(box_login) or {}
    owner = USERS_BY_ID.get(box.get("user_id")) or USERS_BY_ID.get(writer_id) or USERS[0]
    return dict(owner, email=box.get("login") or owner.get("email") or "")


def mail_offer_pdf(code, instruction, price, reply_id=None, lead_id=None, writer_id=None, box_login=""):
    """Zadanie w tle: Claude przygotowuje treść oferty PDF w języku rynku; zapisuje szkic (SentOffer) do dołączenia."""
    m = markets.get(code)
    reply = db.session.get(MailMessage, reply_id) if reply_id else None
    lead = db.session.get(Lead, lead_id) if lead_id else (mail_lead_for(reply) if reply else None)
    sender = _compose_offer_sender(box_login, writer_id)
    if not price and lead:
        with app.test_request_context(base_url="http://localhost"):
            q = lt_quote(lead, remote_budget=1)
        price = str(q.get("price") or (int(lead.value_pln) if lead.value_pln else ""))
    price = _price_text(price)
    if not price:
        return {"error": "Podaj cenę netto oferty (np. 2 100 €)."}
    ctx = _mail_draft_context(code, reply.id if reply else None, lead.id if lead else None)
    system = (m["ai_offer"] + "\nPrzygotowujesz TYLKO treść oferty PDF (mail pisze się osobno) — narzędziem offer_pdf. "
              f"Wszystkie pola {m['lang_in_pl']}, nigdy po angielsku ani po polsku. Cena jest już ustalona: {price} netto "
              "(nie wpisuj innych kwot). Trzymaj się limitów długości z opisu narzędzia — PDF ma stały układ. "
              "Pisz konkretnie o TYM wydarzeniu klienta (miejsce, charakter, goście), bez wymyślania faktów.")
    text = (f"POLECENIE: {instruction.strip() or 'Przygotuj ofertę na podstawie maila i danych z CRM.'}\n\n{ctx}")
    data = None
    note = ""
    for attempt in range(3):
        try:
            args = _ai_forced_tool(OFFER_AI_MODEL, system, text + note, COMPOSE_OFFER_TOOL, max_tokens=3000)
        except Exception as e:
            note = ""
            if attempt == 2:
                app.logger.warning("mail_offer_pdf AI: %s", e)
            continue
        cards = [{"label": str(k.get("label") or "")[:24], "title": str(k.get("title") or "")[:32],
                  "items": [str(i)[:90] for i in (k.get("items") or [])][:3]} for k in (args.get("cards") or [])[:2]]
        joined = " ".join([str(args.get("subtitle") or ""), str(args.get("intro") or "")] +
                          [k["title"] + " " + " ".join(k["items"]) for k in cards])
        bad = wrong_language(code, joined)
        if bad:
            note = f"\n\nUWAGA: poprzednia wersja nie była {m['lang_in_pl']} (wykryto: {bad}). Napisz wszystko {m['lang_in_pl']}."
            continue
        offer_in = {"language": m["lang"], "client": str(args.get("client") or (lead.company if lead else "") or "33Bots")[:80],
                    "headline": m["offer_pdf"]["headline"], "subtitle": str(args.get("subtitle") or "")[:220],
                    "cover": [{"label": str(x.get("label") or "")[:22], "value": str(x.get("value") or "")[:34]}
                              for x in (args.get("cover") or [])[:3] if isinstance(x, dict)] or
                             [{"label": m["offer_pdf"]["cover"][0], "value": m["offer_pdf"]["soon"]}],
                    "intro": str(args.get("intro") or "")[:600], "cards": cards,
                    "price_title": str(args.get("price_title") or m["offer_pdf"]["price_title"])[:60],
                    "price_sub": str(args.get("price_sub") or "")[:90], "price": price}
        try:
            data = offers.normalize(offer_in)
            pdf = offers.render_offer_pdf(data, (sender.get("full_name") or sender.get("name") or "", sender["email"],
                                                 sender.get("phone") or ""))
            break
        except offers.OfferError as e:
            data = None
            note = f"\n\nPOPRAWKA: PDF się nie zmieścił ({e}). Skróć intro i punkty kart."
    if data is None:  # awaryjnie: stała treść rynku — oferta i tak musi się dać dołączyć
        o = m["offer_pdf"]
        offer_in = {"language": m["lang"], "client": ((lead.company if lead else "") or "33Bots")[:80],
                    "headline": o["headline"], "subtitle": o["subtitle"],
                    "cover": [{"label": o["cover"][0], "value": o["soon"]}], "intro": o["intro"],
                    "cards": [{"label": a, "title": b, "items": i} for a, b, i in o["cards"]],
                    "price_title": o["price_title"], "price_sub": "", "price": price}
        data = offers.normalize(offer_in)
        pdf = offers.render_offer_pdf(data, (sender.get("full_name") or sender.get("name") or "", sender["email"],
                                             sender.get("phone") or ""))
    offer = SentOffer(lead_id=lead.id if lead else None, filename=offers.offer_filename(data["client"]), pdf=pdf,
                      data=json.dumps({"compose": True, "offer": offer_in}, ensure_ascii=False),
                      price=data["price"], sender_id=sender.get("id"))
    db.session.add(offer)
    db.session.commit()
    return {"offer": {"id": offer.id, "filename": offer.filename, "price": offer.price,
                      "url": f"/crm/oferty/{offer.id}/{offer.filename}"}, "info": "gotowe"}


@app.route("/crm/poczta/oferta", methods=["POST"])
def crm_mail_offer_pdf():
    """📄 w okienku pisania: oferta PDF do dołączenia (w tle)."""
    if _ai_client() is None:
        return jsonify({"error": "Brak ANTHROPIC_API_KEY w Railway."}), 400
    f = request.form
    reply_id, lead_id = f.get("reply", type=int), f.get("lead", type=int)
    if reply_id and not db.session.get(MailMessage, reply_id):
        reply_id = None
    if lead_id and not db.session.get(Lead, lead_id):
        lead_id = None
    jid = _start_job("Claude przygotowuje ofertę PDF", mail_offer_pdf, current_market(), (f.get("instruction") or "")[:2000],
                     (f.get("price") or "")[:30], reply_id, lead_id, get_current_user(request)["id"], (f.get("from") or "")[:200])
    return jsonify({"jid": jid, "poll": url_for("crm_job", jid=jid, format="json")})


def _quote_for_reply(row, lang):
    when = f"{to_local(row.date):%Y-%m-%d %H:%M}" if row.date else ""
    who = f"{row.from_name} <{row.from_addr}>" if row.from_name else row.from_addr
    intro = {"lt": f"{when}, {who} rašė:", "de": f"Am {when} schrieb {who}:", "en": f"On {when}, {who} wrote:",
             "pl": f"{when}, {who} napisał(a):"}.get(lang, f"{when}, {who}:")
    body = "\n".join("> " + line for line in (row.body_text or "").split("\n")[:200])
    return f"\n\n{intro}\n{body}"


@app.route("/crm/poczta/nowa", methods=["GET", "POST"])
def crm_mail_compose():
    boxes = [b for b in mail_boxes(current_market()) if b["pwd"]]
    user = get_current_user(request)
    if request.method == "POST":
        return _mail_send_from_form(boxes, user)
    reply = db.session.get(MailMessage, request.args.get("reply", type=int) or 0)
    fwd = db.session.get(MailMessage, request.args.get("fwd", type=int) or 0)
    lead = db.session.get(Lead, request.args.get("lead", type=int) or 0)
    to, cc, subject, subject_pl, quoted, from_box = request.args.get("to", ""), "", "", "", "", ""
    if reply:
        from_box = reply.mailbox
        if reply.folder == "in":
            to = reply.from_addr
        else:
            to = reply.to_addrs or ""
        if request.args.get("all") == "1":
            own = {b["login"] for b in mail_boxes()}
            others = [a for a in ((reply.to_addrs or "") + "," + (reply.cc_addrs or "")).split(",")
                      if a.strip() and a.strip() not in own and a.strip() != to]
            cc = ", ".join(dict.fromkeys(a.strip() for a in others))
        s = reply.subject or ""
        subject = s if re.match(r"(?i)^(re|aw|ats)\s*:", s) else f"Re: {s}"
        sp = reply.subject_pl or s
        subject_pl = sp if re.match(r"(?i)^(re|aw|ats)\s*:", sp) else f"Re: {sp}"
        quoted = _quote_for_reply(reply, M()["lang"])
        lead = lead or mail_lead_for(reply)
    elif fwd:
        from_box = fwd.mailbox
        s = fwd.subject or ""
        subject = s if re.match(r"(?i)^(fwd?|wg|pers)\s*:", s) else f"Fwd: {s}"
        subject_pl = f"Fwd: {fwd.subject_pl or s}"
        quoted = _quote_for_reply(fwd, M()["lang"])
    elif lead:
        to = to or ", ".join(lead.emails)
    if not from_box:
        mine = next((b for b in boxes if b.get("user_id") == user["id"]), None)
        from_box = (mine or (boxes[0] if boxes else {"login": ""}))["login"]
    draft = db.session.get(Activity, request.args.get("draft", type=int) or 0)
    draft_body = ""
    if draft and (draft.notes or "").startswith(sales_rules.PRIVATE_DRAFT_PREFIX) and "\n---\n" in draft.notes:
        head, draft_body = draft.notes.split("\n---\n", 1)
        m = re.search(r"^Temat: (.+)$", head, re.M)
        subject = subject or (m.group(1).strip() if m else "")
        lead = lead or draft.lead
        to = to or (", ".join(draft.lead.emails) if draft.lead else "")
    task = db.session.get(MailTask, request.args.get("task", type=int) or 0)
    ai_instruction = (request.args.get("ai") or (task.reply_hint if task else "") or "")[:2000]
    offer_price = ""
    if lead:
        try:
            q = lt_quote(lead, remote_budget=1)
            offer_price = _price_text(str(q.get("price") or (int(lead.value_pln) if lead.value_pln else "")))
        except Exception:
            db.session.rollback()
    return render_template(
        "crm/poczta_nowa.html", offer_price=offer_price, boxes=boxes, from_box=from_box, to=to, cc=cc, subject=subject, subject_pl=subject_pl,
        quoted=quoted, task=task, ai_instruction=ai_instruction, ai_auto=request.args.get("auto") == "1",
        reply=reply, fwd=fwd, lead=lead, target_lang=M()["lang"], lang_names=MAIL_LANG_NAMES,
        draft=draft if draft_body else None, draft_body=draft_body,
        lang_flags=MAIL_LANG_FLAGS, signatures={b["login"]: mail_signature(b, user) for b in boxes}, ai_ok=bool(_ai_client()),
    )


def _mail_send_from_form(boxes, user):
    f = request.form
    box = next((b for b in boxes if b["login"] == (f.get("from") or "").lower()), None)
    if not box:
        return jsonify({"error": "Wybierz skrzynkę nadawcy (brak hasła tej skrzynki w Railway)."}), 400
    recipients, bad = check_recipients(f.get("to"))
    if bad:
        return jsonify({"error": bad}), 400
    cc = []
    if (f.get("cc") or "").strip():
        cc, bad = check_recipients(f.get("cc"))
        if bad:
            return jsonify({"error": bad}), 400
    subject = (f.get("subject_out") or f.get("subject") or "").strip()[:300]
    body = (f.get("body_out") or "").replace("\r\n", "\n").strip()
    if not subject or not body:
        return jsonify({"error": "Uzupełnij temat i treść (w języku klienta — kliknij „Przetłumacz”)."}), 400
    if f.get("signature") == "1":
        body = body.rstrip() + "\n\n" + mail_signature(box, user)
    body += (f.get("quoted") or "").replace("\r\n", "\n")[:60000]
    files = []
    total = 0
    for up in request.files.getlist("files"):
        if not up or not up.filename:
            continue
        data = up.read()
        total += len(data)
        if total > 15 * 1024 * 1024:
            return jsonify({"error": "Załączniki razem ponad 15 MB."}), 400
        files.append((journey.safe_filename(up.filename), data, up.mimetype or "application/octet-stream"))
    offer = db.session.get(SentOffer, f.get("offer_id", type=int) or 0)
    if offer and not offer.sent_at:
        olead_chk = db.session.get(Lead, f.get("lead", type=int) or 0) or (
            db.session.get(Lead, offer.lead_id) if offer.lead_id else None)
        type_err = offer_client_type_error(olead_chk)
        if type_err:
            return jsonify({"error": type_err}), 400
        files.append((offer.filename, offer.pdf, "application/pdf"))
    reply = db.session.get(MailMessage, f.get("reply", type=int) or 0)
    headers = {}
    if reply and reply.message_id:
        headers["In-Reply-To"] = reply.message_id
        headers["References"] = ((reply.refs or "") + " " + reply.message_id).strip()[-3000:]
    owner = USERS_BY_ID.get(box.get("user_id")) or {}
    sender = {"name": box["key"], "login": box["login"], "pwd": box["pwd"], "id": user["id"],
              "full_name": owner.get("full_name") or box["label"], "email": box["login"]}
    err = _smtp_send(recipients, subject, body, box["label"], attachments=files or None,
                     sender=sender, headers=headers, cc=cc)
    if err:
        return jsonify({"error": f"Mail nie wyszedł: {err}"}), 502
    now = datetime.utcnow()
    msg_id = _smtp_send.last_msgid or ""
    body_pl = (f.get("body") or "").strip()
    row = MailMessage(mailbox=box["login"], market=box["market"], folder="sent", uid=None, message_id=msg_id[:400],
                      in_reply_to=headers.get("In-Reply-To"), refs=headers.get("References"),
                      thread_key=(reply.thread_key if reply else msg_id)[:400], from_addr=box["login"],
                      from_name=sender["full_name"], to_addrs=", ".join(recipients), to_names=", ".join(recipients),
                      cc_addrs=", ".join(cc), addrs=" " + " ".join(sorted(set([box["login"]] + recipients + cc))) + " ",
                      subject=subject, date=now, body_text=body[:poczta.BODY_LIMIT], snippet=re.sub(r"\s+", " ", body)[:300],
                      attachments=json.dumps([{"n": i, "name": n, "type": m, "size": len(d)} for i, (n, d, m) in enumerate(files)]),
                      lang=markets.get(box["market"])["lang"], subject_pl=(f.get("subject") or subject)[:600],
                      body_pl=body_pl or None, translated_at=now, seen=True)
    db.session.add(row)
    db.session.flush()
    lead = db.session.get(Lead, f.get("lead", type=int) or 0) or mail_lead_for(row)
    if lead:
        for d in Activity.query.filter(Activity.lead_id == lead.id, Activity.done.isnot(True),
                                       Activity.notes.like(sales_rules.PRIVATE_DRAFT_PREFIX + "%")).all():
            d.done, d.done_at, d.outcome = True, now, "bez_odpowiedzi"  # pierwsza odpowiedź wysłana — czekamy
        db.session.add(Activity(lead_id=lead.id, kind="email", done=True, done_at=now, assigned_to=user["id"],
                                notes=f"✉️ Mail z CRM do {', '.join(recipients)} (od: {box['login']})\nTemat: {subject}\n---\n"
                                      f"{body.split(chr(10) + chr(10) + '>')[0][:4000]}"
                                      + (f"\n---\n🇵🇱 {body_pl[:3000]}" if body_pl else "")))
    if offer and not offer.sent_at:  # oferta PDF poszła z tym mailem — jak oferta z „Ofert”
        offer.sent_at, offer.to_addr, offer.subject = now, ", ".join(recipients), subject
        offer.body = body.split(chr(10) + chr(10) + ">")[0][:20000]
        offer.sender_id = sender.get("id")
        offer.data = (offer.data or "").replace('"compose": true', '"compose": false')
        olead = lead or (db.session.get(Lead, offer.lead_id) if offer.lead_id else None)
        if olead:
            offer.lead_id = olead.id
            if olead.stage == "Nowy":
                record_stage_change(olead, "Oferta", user["id"])
            value, currency = offer_value(offer)
            if value is not None:
                olead.value_pln = value
                olead.currency = currency or olead.currency
            if olead.stage in ACTIVE_STAGES:
                ensure_followup_call(olead)
            if auto_tasks_on():
                due = datetime.combine(date.today() + timedelta(days=3), datetime.min.time())
                db.session.add(Activity(lead_id=olead.id, kind="email", due_date=due, assigned_to=user["id"],
                                        notes=f"Follow-up oferty ({offer.price} netto) wysłanej {date.today():%d.%m}"))
    task = db.session.get(MailTask, f.get("task", type=int) or 0)
    if task and task.status == "open":  # odpowiedź na sprawę z przeglądu poczty — sprawa załatwiona
        task.status, task.done_at, task.done_by, task.done_how = "done", now, user["id"], "odpowiedź wysłana"
    db.session.commit()
    return jsonify({"ok": True, "url": url_for("crm_mail", f="sent", m=row.id), "warning": _smtp_send.last_warning})


def lead_mails(lead, limit=15):
    q = mail_query_for_addrs(lead.emails) if lead else None
    if q is None:
        return []
    return q.order_by(MailMessage.date.desc().nullslast()).limit(limit).all()


app.jinja_env.globals.update(mail_unseen_count=mail_unseen_count, mail_boxes=mail_boxes, lead_mails=lead_mails,
                             mail_lang_flags=MAIL_LANG_FLAGS)


# ---------- STRONY BŁĘDÓW (ładne zamiast gołego „Not Found”) ----------
# Klient z Litwy (formularz /f/…) widzi komunikat po litewsku z adresem do kontaktu, zespół w CRM — po polsku.
# Zapytania API / fetch dostają dalej JSON.
from werkzeug.exceptions import HTTPException as _HTTPException

_ERR_TEXT = {
    "lt": {404: ("🔗", "Nuoroda nebegalioja", "Ši forma nebeprieinama. Parašykite mums — mielai padėsime:"),
           500: ("🛠️", "Įvyko klaida", "Bandykite dar kartą po kelių minučių arba parašykite mums:")},
    "pl": {404: ("🔎", "Nie ma takiej strony", "Ten adres nie istnieje albo element został usunięty."),
           500: ("🛠️", "Coś poszło nie tak", "Spróbuj ponownie za chwilę. Jeśli błąd się powtarza, daj znać."),
           "other": ("⚠️", "Nie udało się", "Tej operacji nie da się teraz wykonać.")},
}


def _wants_json():
    return (request.path.startswith(("/crm/api/", "/mcp/")) or request.is_json
            or request.headers.get("X-Requested-With") == "fetch"
            or "application/json" in request.headers.get("Accept", ""))


def _customer_market():
    token = request.path.split("/")[2] if request.path.count("/") >= 2 else ""
    try:
        lead = Lead.query.filter_by(form_token=token).first() if len(token) >= 16 else None
        if lead is not None:
            return lead_market(lead)
    except Exception:
        db.session.rollback()
    best = request.accept_languages.best_match(["de", "lt"]) if request.accept_languages else None
    return best or DEFAULT_MARKET


def _error_page(code):
    if _wants_json():
        return jsonify({"error": {404: "nie znaleziono", 500: "błąd serwera"}.get(code, "błąd")}), code
    customer = request.path.startswith("/f/")
    if customer:  # strona dla klienta: język rynku leada albo przeglądarki (de / lt)
        mc = _customer_market()
        m = markets.get(mc)
        icon, title, message = m["errors"].get(code) or m["errors"][500]
        return render_template("crm/error.html", lang=m["html_lang"], icon=icon, title=title, message=message,
                               contact=lt_sender(mc)["email"], back_url=None, back_label="", code=""), code
    texts = _ERR_TEXT["pl"]
    icon, title, message = texts.get(code) or texts.get("other") or texts[500]
    return render_template(
        "crm/error.html", lang="pl", icon=icon, title=title, message=message,
        contact="",
        back_url=None if customer else url_for("crm_dashboard"), back_label="Wróć do CRM",
        code="" if customer else f"Błąd {code}"), code


@app.errorhandler(_HTTPException)
def _http_error(e):
    if e.code and e.code < 400 or e.code in (401,):
        return e
    return _error_page(e.code or 500)


@app.errorhandler(Exception)
def _server_error(e):
    if isinstance(e, _HTTPException):
        return _http_error(e)
    try:
        db.session.rollback()
    except Exception:
        pass
    app.logger.exception("Nieobsłużony błąd: %s", e)
    return _error_page(500)


# ---------- ALARMY: nowe leady do obsłużenia DZIŚ + sprawy z poczty (przegląd Claude'a) ----------
# Każdy nowy lead musi być obsłużony tego samego dnia (telefon, mail, oferta). Dopóki nie jest, na każdej stronie
# CRM wyskakuje okno, które przeszkadza w pracy — znika dopiero, gdy lead jest obsłużony: sam (wysłany mail,
# oferta, wycena, WhatsApp, zapisany telefon) albo kliknięciem „Obsłużony”. Drugi alarm: Claude codziennie
# (MAIL_REVIEW_HOURS, domyślnie 12 i 15) przegląda pocztę i wypisuje sprawy, które czekają na nas — pokazują się
# w oknie od MAIL_TASKS_SHOW_FROM (domyślnie 16:00), aż każda zostanie załatwiona.
from sqlalchemy.exc import IntegrityError as _IntegrityError  # noqa: E402

LEAD_ALERT_DAYS = int(os.environ.get("LEAD_ALERT_DAYS", "2") or 2)  # przy pierwszym uruchomieniu: leady z tylu dni
LEAD_ALERT_SINCE_KEY = "lead_alert_since"
MAIL_REVIEW_HOURS = [int(h) for h in (os.environ.get("MAIL_REVIEW_HOURS", "12,15") or "12,15").split(",")
                     if h.strip().isdigit() and 0 <= int(h) <= 23]
MAIL_REVIEW_DAYS = int(os.environ.get("MAIL_REVIEW_DAYS", "14") or 14)
MAIL_TASKS_SHOW_FROM = int(os.environ.get("MAIL_TASKS_SHOW_FROM", "16") or 16)
MAIL_REVIEW_AI_MODEL = os.environ.get("MAIL_REVIEW_AI_MODEL", "").strip() or "claude-opus-5-5"
ALERT_SNOOZE_COOKIE = "alert_snooze"
ALERT_SNOOZE_MIN = 10
_HANDLED_NOTE_PREFIXES = ("📨", "📄", "✉️", "📞")
_AUTO_SENDER = re.compile(r"(no-?reply|do-?not-?reply|newsletter|mailer-daemon|postmaster|notification|notify|bounce|"
                          r"calendar-|invitations?@|news@|marketing@|info@mailchimp|@linkedin|@facebook|@google\.com)", re.I)


class MailTask(db.Model):
    """Sprawa z poczty do załatwienia (z przeglądu Claude'a): nieodpisany mail, prośba o umowę, spotkanie…"""
    __tablename__ = "mail_tasks"
    id = db.Column(db.Integer, primary_key=True)
    market = db.Column(db.String(2), nullable=False, default=_default_market, index=True)
    mail_id = db.Column(db.Integer, db.ForeignKey("mail_messages.id"), nullable=True)  # mail, na który odpowiadamy
    thread_key = db.Column(db.String(400), index=True)
    mailbox = db.Column(db.String(200))
    counterpart = db.Column(db.String(300))
    subject = db.Column(db.String(600))
    title = db.Column(db.String(300))
    reason = db.Column(db.Text)
    action = db.Column(db.Text)
    reply_hint = db.Column(db.Text)
    priority = db.Column(db.String(20), default="normalne")
    status = db.Column(db.String(20), nullable=False, default="open", index=True)  # open | done | dismissed
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    done_at = db.Column(db.DateTime)
    done_by = db.Column(db.Integer)
    done_how = db.Column(db.String(60))
    checked_at = db.Column(db.DateTime)  # kiedy agent sprawdził w poczcie i CRM, czy sprawa nie jest już załatwiona
    check_note = db.Column(db.Text)  # co znalazł: dowód (data, skrzynka, temat) albo czego brakuje

    mail = db.relationship("MailMessage")
    lead = db.relationship("Lead")


def _claim_once(key):
    """Jednorazowa blokada w bazie (kilka procesów serwera — tylko jeden wykona zadanie)."""
    try:
        db.session.add(AppSetting(key=key[:64], value=datetime.utcnow().isoformat(timespec="seconds")))
        db.session.commit()
        return True
    except _IntegrityError:
        db.session.rollback()
        return False


def lead_alert_since():
    """Od kiedy pilnujemy nowych leadów (ustawiane raz: przy pierwszym uruchomieniu — LEAD_ALERT_DAYS dni wstecz)."""
    raw = get_setting(LEAD_ALERT_SINCE_KEY)
    if not raw:
        start = (datetime.utcnow() - timedelta(days=LEAD_ALERT_DAYS)).isoformat(timespec="seconds")
        try:
            db.session.add(AppSetting(key=LEAD_ALERT_SINCE_KEY, value=start))
            db.session.commit()
            raw = start
        except _IntegrityError:
            db.session.rollback()
            raw = get_setting(LEAD_ALERT_SINCE_KEY) or start
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return datetime.utcnow() - timedelta(days=LEAD_ALERT_DAYS)


def unhandled_leads(code=None):
    """Nowe leady rynku `code` (domyślnie oglądanego), z którymi nikt się jeszcze nie skontaktował."""
    code = code or current_market()
    q = all_markets(Lead.query.filter(Lead.market == code, Lead.created_at >= lead_alert_since(),
                                      Lead.handled_at.is_(None), Lead.lt_mail_at.is_(None), Lead.stage == "Nowy",
                                      Lead.clickup_pending.isnot(True)))
    return q.order_by(Lead.created_at).all()


def mark_lead_handled(lead, user_id=None, how="ręcznie", note=None):
    if lead.handled_at is None:
        lead.handled_at, lead.handled_by, lead.handled_how = datetime.utcnow(), user_id, how[:40]
    if note:
        db.session.add(Activity(lead_id=lead.id, kind="call" if how == "telefon" else "note", done=True,
                                done_at=datetime.utcnow(), assigned_to=user_id or lead.assigned_to or USERS[0]["id"],
                                outcome="odebral" if how == "telefon" else None, notes=note))


@_sa_event.listens_for(_OrmSession, "after_flush")
def _auto_handle_leads(session, _ctx):
    """Kontakt z klientem zapisany w CRM (wysłany mail, oferta, wycena, WhatsApp, odbyty telefon) = lead obsłużony."""
    ids = set()
    for obj in session.new:
        if isinstance(obj, Activity) and obj.lead_id and obj.done and (
                obj.kind in ("call", "sms", "email", "meeting") or (obj.notes or "").startswith(_HANDLED_NOTE_PREFIXES)):
            ids.add(obj.lead_id)
    for obj in session.dirty:
        if isinstance(obj, Activity) and obj.lead_id and obj.done and obj.kind in ("call", "sms", "email", "meeting"):
            ids.add(obj.lead_id)
    if ids:
        session.connection().execute(
            db.update(Lead).where(Lead.id.in_(ids), Lead.handled_at.is_(None))
            .values(handled_at=datetime.utcnow(), handled_how="kontakt z CRM"))


def _lead_alert_row(lead):
    created = to_local(lead.created_at) if lead.created_at else None
    age_h = (datetime.utcnow() - lead.created_at).total_seconds() / 3600 if lead.created_at else 0
    return {"id": lead.id, "company": lead.company, "contact": lead.contact_name or "", "phone": lead.contact_phone or "",
            "email": ", ".join(lead.emails), "has_email": bool(lead.emails), "source": lead.source or "",
            "when": f"{created:%d.%m %H:%M}" if created else "", "age_h": round(age_h, 1),
            "old": bool(created and created.date() < to_local(datetime.utcnow()).date()),
            "notes": " ".join((lead.notes or "").split())[:220], "market": lead.market,
            "resp_min": lead_response_minutes(lead), "target": sales_rules.RESPONSE_TARGET_MIN,
            "late": (lead_response_minutes(lead) or 0) > sales_rules.RESPONSE_TARGET_MIN,
            "draft_id": next((a.id for a in lead.activities if not a.done
                              and (a.notes or "").startswith(sales_rules.PRIVATE_DRAFT_PREFIX)), None)}


def mail_tasks_open(code=None):
    code = code or current_market()
    return (all_markets(MailTask.query.filter(MailTask.market == code, MailTask.status == "open"))
            .order_by(MailTask.priority.desc(), MailTask.created_at).all())


def mail_tasks_visible():
    return to_local(datetime.utcnow()).hour >= MAIL_TASKS_SHOW_FROM


def alerts_state(code=None):
    """Co pokazać w oknie alarmów: nowe leady (zawsze) i sprawy z poczty (od MAIL_TASKS_SHOW_FROM)."""
    code = code or current_market()
    leads = [_lead_alert_row(l) for l in unhandled_leads(code)]
    others = {mc: len(unhandled_leads(mc)) for mc in markets.MARKETS if mc != code}
    tasks = mail_tasks_open(code)
    show_tasks = mail_tasks_visible()
    # pasek „klienci z błędnym linkiem” wyłączony — sprawa zamknięta (lista zostaje pod /crm/poprawki, bez przypominania)
    return {"leads": leads, "other": {mc: n for mc, n in others.items() if n}, "badlinks": 0,
            "tasks": [_mail_task_row(t) for t in tasks] if show_tasks else [], "tasks_waiting": len(tasks),
            "show_tasks": show_tasks, "show_from": MAIL_TASKS_SHOW_FROM}


def _mail_task_row(t):
    return {"id": t.id, "title": t.title or t.subject or "", "reason": t.reason or "", "action": t.action or "",
            "counterpart": t.counterpart or "", "subject": t.subject or "", "priority": t.priority or "normalne",
            "mail_id": t.mail_id, "lead_id": t.lead_id, "when": f"{to_local(t.created_at):%d.%m %H:%M}" if t.created_at else "",
            "reply_hint": t.reply_hint or "", "check_note": t.check_note or ""}


_ALERT_QUIET_ENDPOINTS = {"crm_mail_compose", "crm_calculator", "crm_offer_review", "crm_job", "crm_lead_detail",
                          "crm_profile", "crm_login", "crm_mail", "crm_badlink_list"}


@app.context_processor
def _inject_alerts():
    if not has_request_context() or request.path.startswith(("/f/", "/static", "/mcp")) or request.endpoint in (
            None, "lead_form", "crm_login"):
        return {}
    try:
        st = alerts_state()
    except Exception:
        db.session.rollback()
        return {}
    try:
        snooze = float(request.cookies.get(ALERT_SNOOZE_COOKIE) or 0)
    except ValueError:
        snooze = 0
    st["quiet"] = request.endpoint in _ALERT_QUIET_ENDPOINTS or time.time() < snooze
    st["snooze_min"] = ALERT_SNOOZE_MIN
    return {"alerts": st}


@app.route("/crm/api/alerty")
def crm_alerts_api():
    st = alerts_state()
    return jsonify({"leads": [l["id"] for l in st["leads"]], "lead_count": len(st["leads"]),
                    "task_count": len(st["tasks"]), "other": st["other"]})


@app.route("/crm/leads/<int:lead_id>/obsluzony", methods=["POST"])
def crm_lead_handled(lead_id):
    lead = db.session.get(Lead, lead_id) or abort(404)
    user = get_current_user(request)
    how = request.form.get("how", "inne")
    notes = {"telefon": "📞 Zadzwoniono do klienta — lead obsłużony (panel nowych leadów)",
             "inne": "✅ Lead obsłużony (panel nowych leadów)"}
    extra = (request.form.get("note") or "").strip()[:1000]
    mark_lead_handled(lead, user["id"], "telefon" if how == "telefon" else "ręcznie",
                      note=notes.get(how, notes["inne"]) + (f"\n{extra}" if extra else ""))
    db.session.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True})
    return redirect(request.referrer or url_for("crm_dashboard"))


@app.route("/crm/alarm/drzemka", methods=["POST"])
def crm_alert_snooze():
    resp = jsonify({"ok": True, "until": time.time() + ALERT_SNOOZE_MIN * 60})
    resp.set_cookie(ALERT_SNOOZE_COOKIE, str(time.time() + ALERT_SNOOZE_MIN * 60), max_age=ALERT_SNOOZE_MIN * 60,
                    samesite="Lax")
    return resp


# --- przegląd poczty przez Claude'a ---

MAIL_REVIEW_TOOL = {
    "name": "mail_review",
    "description": "Sprawy z poczty, które czekają na ruch firmy 33Bots.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "items": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "thread": {"type": "integer", "description": "Numer wątku z listy"},
                    "title": {"type": "string", "description": "Krótko po polsku, co trzeba zrobić (np. „Wysłać umowę — Messe GmbH”)"},
                    "reason": {"type": "string", "description": "Po polsku: dlaczego — czego klient chce / co obiecaliśmy, od kiedy czeka"},
                    "action": {"type": "string", "description": "Po polsku: konkretny następny krok"},
                    "reply_hint": {"type": "string", "description": "Po polsku polecenie dla Claude'a piszącego odpowiedź (co odpisać)"},
                    "priority": {"type": "string", "enum": ["pilne", "normalne"]},
                },
                "required": ["thread", "title", "reason", "action", "reply_hint", "priority"]}},
        },
        "required": ["items"],
    },
}


def _own_addresses():
    return {b["login"] for b in mail_boxes()} | {(u.get("email") or "").lower() for u in USERS if u.get("email")}


def _is_external(addr, own):
    addr = (addr or "").lower()
    return bool(addr) and addr not in own and addr.rsplit("@", 1)[-1] not in OWN_DOMAINS and not _AUTO_SENDER.search(addr)


def _review_threads(code, days=MAIL_REVIEW_DAYS, limit=40):
    """Wątki z ostatnich dni z udziałem klienta (nie automaty, nie my) — do oceny przez Claude'a."""
    since = datetime.utcnow() - timedelta(days=days)
    own = _own_addresses()
    rows = (all_markets(MailMessage.query.filter(MailMessage.market == code, MailMessage.date >= since))
            .order_by(MailMessage.date).all())
    threads = {}
    for r in rows:
        threads.setdefault(r.thread_key or r.message_id or f"id{r.id}", []).append(r)
    handled = {t.thread_key: t for t in all_markets(MailTask.query.filter(MailTask.market == code)).all()}
    out = []
    for key, msgs in threads.items():
        inbound = [m for m in msgs if m.folder == "in" and _is_external(m.from_addr, own)]
        if not inbound:
            continue
        last_in = inbound[-1]
        prev = handled.get(key)
        if prev and (prev.status == "open" or (prev.done_at and last_in.date and last_in.date <= prev.done_at)):
            continue  # już jest na liście albo załatwione i klient od tego czasu nic nie napisał
        out.append((msgs[-1].folder == "in", msgs[-1].date or datetime.min, key, msgs, last_in))
    out.sort(key=lambda x: (not x[0], -(x[1] - datetime.min).total_seconds()))
    return out[:limit]


def _crm_state_line(lead):
    """Co już poszło do klienta z CRM poza wątkiem (oferta, umowa) — żeby przegląd nie robił sprawy „wyślij umowę”,
    gdy umowa poszła z Customer Journey osobnym mailem."""
    if lead is None:
        return ""
    bits = [f"lead „{lead.company}”, etap {lead.stage}"]
    o = (all_markets(SentOffer.query.filter(SentOffer.lead_id == lead.id, SentOffer.sent_at.isnot(None)))
         .order_by(SentOffer.sent_at.desc()).first())
    if o is not None:
        bits.append(f"ostatnia oferta wysłana z CRM {_dtl(o.sent_at)}")
    j = lead.journey
    if j is not None and j.contract_sent_at:
        bits.append(f"umowa {j.contract_status} (wysłana {_dtl(j.contract_sent_at)})")
    return "CRM (poza tym wątkiem): " + "; ".join(bits)


def _thread_digest(i, msgs, own, lead=None):
    lines = [f"### WĄTEK {i}: {msgs[-1].subject or '(bez tematu)'}"]
    for m in msgs[-5:]:
        who = "KLIENT" if m.folder == "in" and _is_external(m.from_addr, own) else (
            "MY (33Bots)" if m.folder == "sent" or (m.from_addr or "").lower() in own else "INNY")
        fresh = poczta.split_quote(m.body_text or "")[0]
        text = (m.body_pl if m.body_pl and m.lang not in ("pl", None) else fresh) or ""
        lines.append(f"[{who} · {m.from_addr} · {_dt(m.date)} · {m.mailbox}]\n{' '.join(text.split())[:700]}")
    state = _crm_state_line(lead)
    if state:
        lines.append(state)
    return "\n".join(lines)


def mail_review(code, actor_id=None):
    """Claude przegląda pocztę rynku (odebrane i wysłane) i zapisuje sprawy, które czekają na nas. Zwraca dict."""
    mail_tasks_autoclose(code)
    cand = _review_threads(code)
    if not cand:
        return {"info": "Brak wątków do sprawdzenia", "new": 0}
    own = _own_addresses()
    today = to_local(datetime.utcnow())
    system = (
        "Jesteś asystentem firmy 33Bots (wynajem humanoidalnego robota Unitree G1 na eventy). Przeglądasz skrzynki "
        "firmowe i szukasz spraw, które CZEKAJĄ NA RUCH 33Bots i mogą nam umknąć. Sprawa do załatwienia to np.: "
        "klient napisał, a my nie odpisaliśmy; klient prosi o ofertę, wycenę, umowę, fakturę, spotkanie, telefon, "
        "zdjęcia; klient przypomina się drugi raz; obiecaliśmy coś wysłać i nie widać, że wysłaliśmy; termin "
        "wydarzenia się zbliża, a brakuje ustaleń. NIE są sprawą: spam, newslettery, reklamy i oferty sprzedaży "
        "dla nas (np. ktoś chce nam coś sprzedać lub umówić demo swojego produktu), automatyczne powiadomienia, "
        "zaproszenia kalendarza, odpowiedzi automatyczne, wątki, w których ostatni ruch jest po stronie klienta "
        "(odpisaliśmy i czekamy na niego) i niczego nie obiecaliśmy. Gdy nie ma żadnej sprawy — zwróć pustą listę. "
        f"Dziś jest {today:%Y-%m-%d %H:%M} (czas polski). Wszystkie pola pisz po polsku. „pilne” tylko gdy klient "
        "się przypomina, termin jest bliski albo czeka dłużej niż 2 dni robocze. Linia „CRM (poza tym wątkiem)” "
        "mówi, co już poszło do klienta z CRM osobnym mailem — nie twórz sprawy „wyślij umowę / ofertę”, gdy widać "
        "tam, że umowa / oferta poszła po prośbie klienta."
    )
    text = "\n\n".join(_thread_digest(i, msgs, own, mail_lead_for(last))
                       for i, (_a, _d, _k, msgs, last) in enumerate(cand))
    res = _ai_forced_tool(MAIL_REVIEW_AI_MODEL, system, text, MAIL_REVIEW_TOOL, max_tokens=8000)
    new, created = 0, []
    for item in res.get("items") or []:
        try:
            idx = int(item.get("thread"))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(cand):
            continue
        _a, _d, key, msgs, last_in = cand[idx]
        if all_markets(MailTask.query.filter(MailTask.thread_key == key, MailTask.status == "open")).first():
            continue
        lead = mail_lead_for(last_in)
        created.append(MailTask(
            market=code, mail_id=last_in.id, thread_key=key, mailbox=last_in.mailbox,
            counterpart=(f"{last_in.from_name} <{last_in.from_addr}>" if last_in.from_name else last_in.from_addr)[:300],
            subject=(last_in.subject or "")[:600], title=str(item.get("title") or "")[:300],
            reason=str(item.get("reason") or "")[:2000], action=str(item.get("action") or "")[:2000],
            reply_hint=str(item.get("reply_hint") or "")[:2000],
            priority="pilne" if item.get("priority") == "pilne" else "normalne", lead_id=lead.id if lead else None))
        new += 1
    db.session.add_all(created)
    for t in created:
        _task_lead(t, fix=True)  # lead z rynku sprawy (adres klienta)
    db.session.commit()
    closed = 0
    if created:  # zanim sprawa trafi na listę: agent szuka w poczcie i CRM, czy to już nie jest zrobione
        try:
            closed = verify_mail_tasks(created)["closed"]
        except Exception:
            db.session.rollback()
            app.logger.exception("verify_mail_tasks")
    info = f"Przejrzano {len(cand)} wątków — nowe sprawy: {new - closed}"
    if closed:
        info += f" (+{closed} już załatwionych — zamknięte po sprawdzeniu poczty)"
    return {"info": info, "new": new - closed, "url": "/crm/poczta?sprawy=1"}


def mail_tasks_autoclose(code=None):
    """Sprawa znika sama, gdy w wątku poszła od nas odpowiedź (z CRM albo z programu pocztowego) albo gdy umowa /
    oferta, o którą chodziło, poszła w osobnym mailu lub z CRM (mail_tasks_close_proven)."""
    q = MailTask.query.filter(MailTask.status == "open")
    if code:
        q = q.filter(MailTask.market == code)
    n = 0
    for t in all_markets(q).all():
        if not t.thread_key:
            continue
        sent = (all_markets(MailMessage.query.filter(MailMessage.thread_key == t.thread_key,
                                                     MailMessage.folder == "sent",
                                                     MailMessage.date > (t.mail.date if t.mail and t.mail.date
                                                                         else t.created_at)))
                .first())
        if sent:
            t.status, t.done_at, t.done_how = "done", datetime.utcnow(), "odpowiedź wysłana"
            n += 1
    if n:
        db.session.commit()
    return n + mail_tasks_close_proven(code)


def _market_switch(resp, lead):
    """Lead z drugiego rynku — przełączamy oglądany rynek, żeby strona leada się otworzyła."""
    if getattr(lead, "market", None) in markets.MARKETS and lead.market != current_market():
        resp.set_cookie(MARKET_COOKIE, lead.market, max_age=3600 * 24 * 365, samesite="Lax")
    return resp


# --- sprawy z poczty: agent sprawdza w poczcie i CRM, czy sprawa nie jest już załatwiona ---
# (np. „wyślij ofertę”, a oferta poszła osobnym mailem albo z CRM). Nic nie wysyła — tylko zamyka sprawę z dowodem.
_include("sprawy_z_poczty")


def mail_tasks_close_proven(code=None):
    """Bez Claude'a, przy każdym obiegu poczty: sprawy „wyślij umowę / ofertę”, przy których umowa / oferta już
    poszła po ostatnim mailu klienta, zamykają się same z dowodem (widać je w „Zamknięte po sprawdzeniu poczty”)."""
    q = MailTask.query.filter(MailTask.status == "open")
    if code:
        q = q.filter(MailTask.market == code)
    n = 0
    now = datetime.utcnow()
    for t in all_markets(q).all():
        if (t.done_how or "") == MAIL_TASK_REOPENED:
            continue
        proof = _task_done_proof(t)
        if proof:
            t.status, t.done_at, t.done_how, t.checked_at, t.check_note = "done", now, MAIL_TASK_DONE_HOW, now, proof
            n += 1
    if n:
        db.session.commit()
    return n


_include("sprawy_z_poczty_sprawdzanie")


def _mail_review_scheduler():
    time.sleep(60)
    while True:
        errs, info = [], "przegląd poczty wyłączony: brak klucza Claude'a albo skrzynek"
        try:
            with app.app_context():
                if _ai_client() is not None and mail_boxes():
                    info = ""
                    now = to_local(datetime.utcnow())
                    for h in MAIL_REVIEW_HOURS:
                        if now.hour >= h:
                            for mc in markets.MARKETS:
                                if _claim_once(f"mrev:{mc}:{now:%Y-%m-%d}:{h}"):
                                    try:
                                        mail_review(mc)
                                    except Exception as e:
                                        db.session.rollback()
                                        app.logger.exception("mail_review")
                                        errs.append(f"przegląd poczty {mc}: {type(e).__name__}: {e}")
                    mail_tasks_autoclose()
                    if _claim_once(f"mtchk:{now:%Y-%m-%d %H}"):
                        try:
                            res = verify_mail_tasks()
                            if res.get("error"):
                                errs.append(f"sprawdzanie spraw: {res['error']}")
                        except Exception as e:
                            db.session.rollback()
                            app.logger.exception("verify_mail_tasks")
                            errs.append(f"sprawdzanie spraw: {type(e).__name__}: {e}")
                db.session.remove()
            health_beat("przeglad", not errs, info=info, error="; ".join(errs))
        except Exception as e:
            app.logger.exception("mail_review_scheduler")
            health_fail("przeglad", e)
        time.sleep(300)


if os.environ.get("MAIL_REVIEW", "1") != "0":
    _start_worker("mail_review", _mail_review_scheduler)

app.jinja_env.globals.update(mail_tasks_open=mail_tasks_open, mail_tasks_visible=mail_tasks_visible,
                             mail_tasks_autoclosed=mail_tasks_autoclosed,
                             review_hours=MAIL_REVIEW_HOURS, tasks_from=MAIL_TASKS_SHOW_FROM)


# ---------- STRAŻNIK CRM: ocena czujników i ciągłości pracy, alarmy w CRM, diagnoza Claude'a ----------
# Co kwadrans: czy każdy pracownik w tle działa (czujniki health_beat), czy leady (LT i DE) i maile przychodzą jak
# zwykle, czy przenoszenie z CRM PL działa. Alarm: pasek na górze CRM + strona „Stan CRM” + narzędzie crm_health
# w czacie. Nic nie naprawia i nic nie wysyła; alarm znika sam, gdy wszystko wróci do normy.
HEALTH_ALARMS_KEY = "health_alarms"
HEALTH_LOG_KEY = "health_log"
HEALTH_CHECK_KEY = "health_check_at"
HEALTH_AI_MODEL = os.environ.get("HEALTH_AI_MODEL", "").strip() or "claude-sonnet-5-5"
HEALTH_DIAG_PER_PASS = 3
HEALTH_DIAG_TRIES = 2
HEALTH_QUIET_DAYS = 45  # z ilu dni strażnik zna „zwykły rytm” leadów i maili
health.set_local_time(to_local)

HEALTH_HINTS = {
    "automaty": "Wątek automatów w tle (realizacje, duplikaty, kroki sprzedaży). Wyłącznik: AUTO_REALIZE, SALES_AUTOMATION.",
    "clickup": "Railway → Variables: CLICKUP_API_TOKEN i CLICKUP_LIST_ID (lista litewska).",
    "clickup_de": "Railway → Variables: CLICKUP_API_TOKEN_DE (token z workspace'u ClickUp z niemiecką listą), "
                  "opcjonalnie CLICKUP_LIST_ID_DE; bez niego CRM sam szuka niemieckiej listy.",
    "journey": "Customer Journey czyta skrzynki LT/DE (hasła IMAP_PASS_* / SMTP_PASS_* w Railway) i zamienia "
               "dokumenty na PDF.",
    "inbox": "Skrzynki zapytań: INBOX_IMAP_USER / INBOX_IMAP_USER_DE i hasła (MAIL_PASSWORD albo INBOX_IMAP_PASS*); "
             "rozpoznawanie zapytań: ANTHROPIC_API_KEY.",
    "kampanie": "Wątek kampanii (Blitzkrieg) — wysyła tylko kampanie uruchomione ręcznie.",
    "lt_mail": "Pierwsze maile do nowych leadów LT/DE: skrzynki SMTP_PASS_LT / SMTP_USER_LT (wyłącznik LT_AUTO_MAIL).",
    "pl_move": "Przenoszenie nowych leadów LT/DE z CRM PL: PL_DATABASE_URL (adres bazy CRM PL w Railway). Błąd "
               "połączenia = zmieniony adres bazy PL albo baza PL niedostępna.",
    "pl_won": "Wygrane LT/DE wracają do CRM PL (logistyka): PL_DATABASE_URL.",
    "poczta": "Hasło skrzynki w Railway (IMAP_PASS_* albo SMTP_PASS_*, MAIL_PASSWORD), serwer IMAP_HOST_LT / IMAP_HOST. "
              "Błąd logowania = zmienione albo wygasłe hasło skrzynki.",
    "przeglad": "Przegląd poczty i sprawdzanie spraw przez Claude'a: ANTHROPIC_API_KEY (i środki na koncie Anthropic).",
    "raport": "Poranny raport: REPORT_EMAILS (adresaci), REPORT_FROM (imię osoby, z której skrzynki wychodzi; jej hasło "
              "SMTP_PASS_<IMIĘ>) albo wspólna skrzynka SMTP_USER / SMTP_PASS; do tego SMTP_HOST.",
    "quiet:leads": "Brak nowych leadów dłużej niż zwykle: sprawdź formularz na stronie (LT / DE), reklamy, ClickUp i "
                   "czujnik „Przenoszenie leadów z CRM PL”.",
    "quiet:mail": "Brak nowych maili w skrzynce dłużej niż zwykle: zaloguj się do skrzynki w przeglądarce (czy poczta "
                  "przychodzi) i sprawdź czujnik tej skrzynki.",
}


def _claim_every(key, seconds):
    """Blokada „raz na tyle sekund” w jednym wierszu bazy (przy kilku procesach serwera wykona tylko jeden)."""
    now = datetime.utcnow()
    if db.session.get(AppSetting, key) is None:
        return _claim_once(key)
    n = db.session.execute(db.update(AppSetting).where(
        AppSetting.key == key, AppSetting.value < health.iso(now - timedelta(seconds=seconds)))
        .values(value=health.iso(now))).rowcount
    db.session.commit()
    return n == 1


def _setting_json(key, default):
    try:
        return json.loads(get_setting(key) or "null") or default
    except ValueError:
        return default


def health_jobs():
    """Pracownicy w tle włączeni w tym CRM: klucz czujnika → (nazwa, najdłuższa dopuszczalna przerwa)."""
    env = os.environ.get
    jobs = {}

    def add(key, name, minutes, on=True):
        if on:
            jobs[key] = (name, timedelta(minutes=minutes))

    add("automaty", "Automaty sprzedaży (realizacje, duplikaty, następne kroki, audyt ofert)", 45,
        env("AUTO_REALIZE", "1") != "0")
    add("clickup", "Import leadów z ClickUp (lista litewska)", 30, bool(env("CLICKUP_API_TOKEN") and env("CLICKUP_LIST_ID")))
    add("clickup_de", "Import niemieckich zgłoszeń z ClickUp", 30, clickup_de_configured())
    add("journey", "Customer Journey (umowy i bazy wiedzy ze skrzynek)", 45, env("JOURNEY_SCAN", "1") != "0")
    add("inbox", "Zapytania ze skrzynek kontakt@ (LT / DE)", 30,
        any(env(k, "").strip() for k in ("INBOX_IMAP_USER", "INBOX_IMAP_USER_DE", "MAIL_PASSWORD")))
    add("kampanie", "Kampanie (Blitzkrieg)", 15)
    add("lt_mail", "Pierwsze maile do nowych leadów LT/DE", 15, env("LT_AUTO_MAIL", "1") != "0")
    pl_on = pl_move_configured() and env("PL_MOVE", "1") != "0"
    add("pl_move", "Przenoszenie nowych leadów LT/DE z CRM PL", max(20, PL_MOVE_EVERY * 10 // 60), pl_on)
    add("pl_won", "Wygrane LT/DE do CRM PL (logistyka)", max(60, PL_WON_EVERY * 5 // 60), pl_on)
    if env("MAIL_SYNC", "1") != "0":
        for b in mail_boxes():
            add(f"poczta:{b['login']}", f"Poczta {b['login']}", 30)
    add("przeglad", "Przegląd poczty przez Claude'a i sprawdzanie spraw", 30, env("MAIL_REVIEW", "1") != "0")
    add("raport", "Poranny raport mailowy", 26 * 60, report_wanted())  # niezamówiony raport to nie awaria
    return jobs


def _health_beats():
    out = {}
    for row in AppSetting.query.filter(AppSetting.key.like(HEALTH_BEAT_PREFIX + "%")).all():
        try:
            out[row.key[len(HEALTH_BEAT_PREFIX):]] = json.loads(row.value or "{}")
        except ValueError:
            continue
    return out


def health_lead_markets():
    """Rynki, na których strażnik pilnuje dopływu leadów: [(klucz, nazwa, zapytanie)]."""
    return [(code, f"Nowe leady {m['flag']} {m['name']}", all_markets(Lead.query.filter(Lead.market == code)))
            for code, m in markets.MARKETS.items()]


def health_continuity(now=None):
    """Sygnały ciągłości (bez czujników): cisza w leadach i w skrzynkach dłuższa niż zwykle.
    Zwraca {klucz alarmu: alarm}."""
    now = now or datetime.utcnow()
    since = now - timedelta(days=HEALTH_QUIET_DAYS)
    found = {}
    for code, name, q in health_lead_markets():
        times = [r[0] for r in q.filter(Lead.created_at >= since).with_entities(Lead.created_at).all()]
        text = health.quiet_alarm(times, now)
        if text:
            found[f"quiet:leads:{code}"] = {"level": "yellow", "title": f"{name}: cisza dłuższa niż zwykle", "detail": text}
    # niemieckie zgłoszenia z ClickUp — obojętnie, czy przyszły wprost, czy przez CRM PL
    times = [r[0] for r in all_markets(db.session.query(Lead.created_at).filter(
        Lead.source == CLICKUP_DE_SOURCE, Lead.created_at >= since)).all()]
    text = health.quiet_alarm(times, now)
    if text:
        found["quiet:leads:clickup_de"] = {"level": "yellow", "title": "Niemieckie zgłoszenia z ClickUp: cisza dłuższa "
                                                                       "niż zwykle", "detail": text}
    if os.environ.get("MAIL_SYNC", "1") != "0":
        for b in mail_boxes():
            times = [r[0] for r in all_markets(db.session.query(MailMessage.date).filter(
                MailMessage.mailbox == b["login"], MailMessage.folder == "in", MailMessage.date >= since)).all()]
            text = health.quiet_alarm(times, now)
            if text:
                found[f"quiet:mail:{b['login']}"] = {"level": "yellow", "title": f"Poczta {b['login']}: brak nowych maili",
                                                     "detail": text}
    return found


def health_robot_alarms():
    """Robot i agenci ElevenLabs są tylko w CRM PL."""
    return {}


def _health_hint(key, alarm):
    job = alarm.get("job") or ""
    base = job.split(":", 1)[0] if job else ""
    if key.startswith("quiet:leads"):
        return HEALTH_HINTS["quiet:leads"]
    if key.startswith("quiet:mail"):
        return HEALTH_HINTS["quiet:mail"]
    if key.startswith("robot:"):
        return HEALTH_HINTS["robot:"]
    hint = HEALTH_HINTS.get(job) or HEALTH_HINTS.get(base) or ""
    if "stoi" in (alarm.get("title") or ""):
        hint += (" Wątek nie zgłasza się — zwykle pomaga restart usługi w Railway (Deployments → Restart); "
                 "jeśli wraca, to błąd w kodzie.")
    return hint.strip()


HEALTH_DIAG_TOOL = {
    "name": "health_diagnosis",
    "description": "Diagnoza problemu w CRM po polsku, dla osoby nietechnicznej.",
    "strict": True,
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "cause": {"type": "string", "description": "Najbardziej prawdopodobna przyczyna: 1–2 proste zdania"},
            "fix": {"type": "string", "description": "Co zrobić: najwyżej 3 krótkie kroki (gdzie kliknąć: Railway → "
                                                     "Variables, ClickUp, skrzynka pocztowa…)"},
            "urgent": {"type": "boolean", "description": "true, gdy przez to mogą przepadać leady albo maile od klientów"},
        },
        "required": ["cause", "fix", "urgent"],
    },
}
HEALTH_DIAG_SYSTEM = (
    "Jesteś technicznym asystentem CRM firmy 33Bots dla rynków Litwa i Niemcy (wynajem robota Unitree G1). CRM to "
    "aplikacja Flask na Railway z bazą Postgres, kod na GitHubie; konfiguracja (klucze, hasła skrzynek, adres bazy "
    "CRM PL) jest w Railway → Variables. Strażnik CRM wykrył problem. Napisz po polsku, prosto, dla osoby "
    "nietechnicznej: najbardziej prawdopodobną przyczynę i co zrobić. Opieraj się tylko na podanym błędzie, opisie "
    "i podpowiedzi; nie wymyślaj nazw zmiennych spoza podpowiedzi. Gdy błąd wygląda na chwilowy po stronie "
    "zewnętrznej usługi (timeout, 5xx), napisz, że zwykle mija sam i kiedy zacząć się martwić. Cisza w leadach albo "
    "w skrzynce nie musi być awarią — napisz, co sprawdzić. " + HUMAN_TYPO_RULE
)


def health_diagnose(alarm):
    """Diagnoza Claude'a dla alarmu: {"cause", "fix", "urgent", "at"}."""
    text = (f"Problem: {alarm.get('title')}\nSzczegóły: {alarm.get('detail') or '-'}\n"
            f"Od: {health.fmt(health.parse(alarm.get('since')))} (czas polski)\n"
            f"Podpowiedź (jak to działa w tym CRM): {alarm.get('hint') or '-'}")
    res = _ai_forced_tool(HEALTH_AI_MODEL, HEALTH_DIAG_SYSTEM, text, HEALTH_DIAG_TOOL, max_tokens=900)
    return {"cause": human_text(str(res.get("cause") or ""))[:600], "fix": human_text(str(res.get("fix") or ""))[:900],
            "urgent": bool(res.get("urgent")), "at": health.iso(datetime.utcnow())}


HEALTH_RESTART_KEY = "health_restart_at"
HEALTH_RESTART_EVERY_H = 6  # zawieszony wątek: łagodny restart procesu najwyżej raz na tyle godzin
HEALTH_AUTOFIX_GAP = timedelta(minutes=10)
JOB_WORKERS = {"automaty": "realize", "raport": "report", "clickup": "clickup", "clickup_de": "clickup",
               "journey": "journey", "robot": "journey", "inbox": "inbox", "kampanie": "campaign", "lt_mail": "lt",
               "poczta": "mail", "przeglad": "mail_review", "pl_move": "pl_move", "pl_won": "pl_move"}


def _under_gunicorn():
    import sys
    return any("gunicorn" in (a or "") for a in sys.argv[:2])


def _health_restart(job):
    """Wątek pracownika stoi: martwy — uruchom ponownie; żywy, ale zawieszony (np. na połączeniu) — łagodny
    restart procesu serwera (gunicorn od razu stawia nowy, z wszystkimi wątkami), najwyżej raz na kilka godzin."""
    name = JOB_WORKERS.get((job or "").split(":", 1)[0])
    entry = _WORKERS.get(name) if name else None
    if entry is None:
        return "ten pracownik nie działa w tym procesie — ruszy po najbliższym restarcie serwera"
    target, thread = entry
    if not thread.is_alive():
        _start_worker(name, target)
        return f"wątek „{name}” uruchomiony ponownie"
    if not _under_gunicorn():
        return "wątek zawieszony — restart procesu tylko na serwerze"
    last = health.parse(get_setting(HEALTH_RESTART_KEY))
    if last and datetime.utcnow() - last < timedelta(hours=HEALTH_RESTART_EVERY_H):
        return "wątek zawieszony — proces był niedawno restartowany, kolejna próba później"
    set_setting(HEALTH_RESTART_KEY, health.iso(datetime.utcnow()))
    db.session.commit()
    import signal
    threading.Timer(10, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    return "wątek zawieszony — łagodny restart procesu CRM (nowy startuje sam w kilka sekund)"


def _health_robot_retry():
    return "robot i agenci ElevenLabs są w CRM PL"


def _health_rediscover():
    ids = clickup_de_lists(discover=True, force=True)
    return f"lista ClickUp szukana od nowa: {', '.join(ids) if ids else 'nie znaleziono'}"


def health_autofix(cur, now=None):
    """Naprawy bez człowieka dla alarmów „auto” (restart wątku, lista ClickUp). Błąd w kodzie naprawia Claude,
    chwilowy błąd mija sam. Każda próba zapisana przy alarmie; po limicie prób alarm idzie do człowieka."""
    now = now or datetime.utcnow()
    actions = {"restart": lambda a: _health_restart(a.get("job")), "robot": lambda a: _health_robot_retry(),
               "rediscover": lambda a: _health_rediscover()}
    done = []
    for key, a in cur.items():
        if a.get("who") != "auto" or a.get("how") not in actions:
            continue
        last = health.parse(a.get("auto_at"))
        if last and now - last < HEALTH_AUTOFIX_GAP:
            continue
        try:
            note = actions[a["how"]](a)
        except Exception as e:
            db.session.rollback()
            app.logger.exception("health_autofix")
            note = f"naprawa się nie udała: {type(e).__name__}: {e}"[:300]
        a["auto_tries"] = int(a.get("auto_tries") or 0) + 1
        a["auto_at"], a["auto_note"] = health.iso(now), note
        done.append(key)
    return done


def health_check_pass(diag=True, fix=True):
    """Jeden obieg strażnika: czujniki + ciągłość → alarmy (nowe / trwające / zamknięte), podział „naprawia CRM /
    Claude” albo „potrzebny człowiek”, naprawy automatyczne i diagnoza alarmów dla człowieka."""
    now = datetime.utcnow()
    jobs = health_jobs()
    found, states = health.job_alarms(_health_beats(), jobs, now, HEALTH_STARTED_AT)
    try:
        found.update(health_continuity(now))
    except Exception as e:
        db.session.rollback()
        app.logger.exception("health_continuity")
        found["check:continuity"] = {"level": "yellow", "title": "Strażnik: nie udało się sprawdzić ciągłości pracy",
                                     "detail": f"{type(e).__name__}: {e}"[:400]}
    for key, alarm in found.items():
        alarm["hint"] = _health_hint(key, alarm)
    prev = _setting_json(HEALTH_ALARMS_KEY, {})
    cur, new, closed = health.merge_alarms(prev, found, now)
    escalated = []
    for key, alarm in cur.items():
        who, how = health.classify(key, alarm, now)
        if who == "human" and (prev.get(key) or {}).get("who") == "auto":
            escalated.append(key)
        alarm["who"] = who
        alarm["how"] = how if who == "auto" else None
        alarm["why"] = how if who == "human" else None
    fixed = health_autofix(cur, now) if fix else []
    log = _setting_json(HEALTH_LOG_KEY, [])
    for key in new:
        log.insert(0, {"at": health.iso(now), "event": "open", "level": cur[key]["level"], "title": cur[key]["title"],
                       "who": cur[key]["who"]})
    for key in escalated:
        log.insert(0, {"at": health.iso(now), "event": "escalate", "level": cur[key]["level"],
                       "title": cur[key]["title"], "who": "human"})
    for key in fixed:
        log.insert(0, {"at": health.iso(now), "event": "autofix", "level": cur[key]["level"],
                       "title": cur[key]["title"], "note": cur[key].get("auto_note"), "who": "auto"})
    for key in closed:
        log.insert(0, {"at": health.iso(now), "event": "close", "level": prev[key].get("level"),
                       "title": prev[key].get("title"), "since": prev[key].get("since"), "who": prev[key].get("who")})
    set_setting(HEALTH_ALARMS_KEY, json.dumps(cur, ensure_ascii=False))
    set_setting(HEALTH_LOG_KEY, json.dumps(log[:60], ensure_ascii=False))
    set_setting(HEALTH_CHECK_KEY, health.iso(now))
    db.session.commit()
    diagnosed = 0
    if diag and _ai_client() is not None:  # diagnoza po ludzku tylko tam, gdzie potrzebny jest człowiek
        todo = [a["key"] for a in health.human_alarms(cur)
                if not a.get("diag") and int(a.get("diag_tries") or 0) < HEALTH_DIAG_TRIES][:HEALTH_DIAG_PER_PASS]
        for key in todo:
            try:
                cur[key]["diag"] = health_diagnose(cur[key])
                diagnosed += 1
            except Exception:
                app.logger.exception("health_diagnose")
                cur[key]["diag_tries"] = int(cur[key].get("diag_tries") or 0) + 1
            set_setting(HEALTH_ALARMS_KEY, json.dumps(cur, ensure_ascii=False))
            db.session.commit()
    return {"alarms": cur, "states": states, "jobs": jobs, "new": new, "closed": closed, "diagnosed": diagnosed,
            "fixed": fixed, "escalated": escalated}


def health_alarm_list():
    """Alarmy, które wymagają człowieka (najpierw czerwone) — pasek i licznik w menu; raz na zapytanie.
    To, co CRM / Claude naprawia sam, widać tylko na stronie „Stan CRM”."""
    cache = request.environ if has_request_context() else {}
    if "crm.health_alarms" not in cache:
        try:
            cache["crm.health_alarms"] = health.human_alarms(_setting_json(HEALTH_ALARMS_KEY, {}))
        except Exception:
            db.session.rollback()
            cache["crm.health_alarms"] = []
    return cache["crm.health_alarms"]


def health_report():
    """Pełny obraz dla strony „Stan CRM” i narzędzia crm_health w czacie (tylko odczyt)."""
    now = datetime.utcnow()
    jobs, beats = health_jobs(), _health_beats()
    rows = []
    for key, (name, max_age) in jobs.items():
        st, text = health.job_state(beats.get(key), max_age, now, HEALTH_STARTED_AT)
        rows.append({"key": key, "name": name, "state": st, "text": text})
    checked = health.parse(get_setting(HEALTH_CHECK_KEY))
    alarms = _setting_json(HEALTH_ALARMS_KEY, {})
    auto = [dict(a, how_text=health.HOW_TEXT.get(a.get("how"), "")) for a in health.sorted_alarms(alarms)
            if a.get("who") != "human"]
    return {"alarms": health.human_alarms(alarms), "auto": auto, "jobs": rows,
            "log": _setting_json(HEALTH_LOG_KEY, [])[:25], "checked_at": checked,
            "checked_text": health.fmt(checked),
            "watch_stale": checked is None or now - checked > timedelta(minutes=health.CHECK_MINUTES * 4),
            "every_min": health.CHECK_MINUTES, "robot": "robot_agents_plan" in globals()}


_include("straznik_widoki")


_include("sprawy_widoki")


@app.route("/crm/sprawy/przeglad", methods=["POST"])
def crm_mail_review_now():
    if _ai_client() is None:
        return redirect(url_for("crm_mail"))
    code = current_market()
    return redirect(url_for("crm_job", jid=_start_job("Claude przegląda pocztę", mail_review, code)))


_include("nastepny_krok")
_include("raporty_dnia")

with app.app_context():
    db.create_all()
    ensure_schema()
    apply_user_profiles()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", 5001)),
        debug=os.environ.get("FLASK_DEBUG", "") == "1",
    )
