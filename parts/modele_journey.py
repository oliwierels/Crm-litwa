# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("modele_journey") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: modele Customer Journey: maile, dokumenty, dawne prośby o umowę

class JourneyMail(db.Model):
    """Mail ze skrzynek (kontakt@ i osobistych) przypisany do klienta z Customer Journey — każdy tylko raz."""
    __tablename__ = "journey_mails"
    id = db.Column(db.Integer, primary_key=True)
    message_key = db.Column(db.String(320), unique=True, nullable=False, index=True)
    mailbox = db.Column(db.String(200))
    folder = db.Column(db.String(10))  # in (odebrane) | sent (wysłane)
    from_addr = db.Column(db.String(200))
    to_addrs = db.Column(db.String(500))
    subject = db.Column(db.String(300))
    received_at = db.Column(db.DateTime)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=True, index=True)
    result = db.Column(db.String(40))  # co CRM rozpoznał (JOURNEY_MAIL_RESULTS)
    confidence = db.Column(db.String(20))  # wysoka | niska | potwierdzone | odrzucone
    summary = db.Column(db.String(500))
    pending = db.Column(db.Boolean, default=False)  # niepewne — czeka na potwierdzenie człowieka
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    lead = db.relationship("Lead")


class JourneyDocument(db.Model):
    """Dokument klienta: wysłana i podpisana umowa, baza wiedzy, inne — oryginał i wersja PDF."""
    __tablename__ = "journey_documents"
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False, index=True)
    kind = db.Column(db.String(30), nullable=False)  # umowa_wyslana | umowa_podpisana | baza_wiedzy | inny
    filename = db.Column(db.String(200))
    mime = db.Column(db.String(120))
    data = db.Column(db.LargeBinary)
    pdf = db.Column(db.LargeBinary, nullable=True)  # None = nie udało się (jeszcze) przekonwertować
    pdf_tries = db.Column(db.Integer, default=0)
    source = db.Column(db.String(200))
    mail_id = db.Column(db.Integer, db.ForeignKey("journey_mails.id"), nullable=True)
    status = db.Column(db.String(20), default="ok")  # ok | do_sprawdzenia (CRM nie jest pewny, czym to jest)
    seen = db.Column(db.Boolean, default=True)  # False = nowy dokument od klienta, jeszcze nieobejrzany
    created_by = db.Column(db.Integer)
    received_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    lead = db.relationship("Lead", backref=db.backref(
        "journey_documents", cascade="all, delete-orphan", order_by="JourneyDocument.created_at"))
    mail = db.relationship("JourneyMail")


class ContractRequest(db.Model):
    """Dawny formularz danych do umowy (usunięty) — tabela zostaje, jej PDF-y i dane przenosimy do dokumentów."""
    __tablename__ = "contract_requests"
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=True)  # None = jeszcze nie dodany do leada
    label = db.Column(db.String(200))  # dla kogo (gdy link nie jest z leada)
    status = db.Column(db.String(20), nullable=False, default="nowy")  # nowy | wypelniony | uniewazniony
    data = db.Column(db.Text)  # JSON z formularza
    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime)
    submitted_at = db.Column(db.DateTime)
    submit_ip = db.Column(db.String(64))
    submit_agent = db.Column(db.String(300))
    submit_count = db.Column(db.Integer, default=0)
    pdf = db.Column(db.LargeBinary)  # ostatnio wygenerowana umowa
    pdf_generated_at = db.Column(db.DateTime)

    lead = db.relationship(
        "Lead", backref=db.backref("contract_requests", cascade="all", order_by="ContractRequest.created_at"),
    )
