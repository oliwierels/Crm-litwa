# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("journey_umowa") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: Customer Journey: status umowy i bazy wiedzy, przenoszenie

def _journey_fix_backfill():
    """Po pierwszym przeglądzie skrzynek (przed poprawką): stare dokumenty bez „NOWY”, a leady, przy których
    CRM znalazł już umowę w skrzynce, bez pytania „co już poszło?”."""
    cutoff = datetime.utcnow() - timedelta(days=JOURNEY_NEW_DOC_DAYS)
    JourneyDocument.query.filter(JourneyDocument.seen.is_(False), JourneyDocument.received_at < cutoff) \
        .update({"seen": True}, synchronize_session=False)
    known = {lid for (lid,) in db.session.query(JourneyMail.lead_id).filter(
        JourneyMail.result.in_(JOURNEY_CONTRACT_RESULTS), JourneyMail.pending.isnot(True),
        JourneyMail.confidence.in_(("wysoka", "potwierdzone"))).distinct()}
    for j in Journey.query.filter(Journey.needs_review.is_(True)).all():
        if j.lead_id in known:
            j.needs_review = False
    _journey_badge_reset()


_OLD_FORM_LABELS = {
    "typ": "Zamawiam jako", "nazwa": "Nazwa / imię i nazwisko", "reprezentant": "Osoba podpisująca",
    "funkcja": "Funkcja", "ulica": "Adres", "kod": "Kod pocztowy", "miasto": "Miejscowość", "nip": "NIP",
    "regon": "REGON", "krs": "KRS", "sad": "Sąd rejestrowy", "email": "E-mail", "telefon": "Telefon",
    "email_faktury": "E-mail do faktury", "nazwa_wydarzenia": "Nazwa wydarzenia", "miejsce": "Miejsce wydarzenia",
    "godz_od": "Godzina od", "godz_do": "Godzina do", "charakter_wydarzenia": "Charakter wydarzenia",
    "rodzaj_przestrzeni": "Przestrzeń", "osoba_na_miejscu": "Osoba na miejscu", "tel_na_miejscu": "Telefon na miejscu",
    "uwagi": "Uwagi",
}


def _migrate_contract_forms():
    """Dawny formularz umowy został usunięty: jego PDF-y i wpisane dane trafiają do dokumentów leada."""
    for cr in ContractRequest.query.filter(ContractRequest.lead_id.isnot(None)).all():
        name = journey.slug(cr.lead.company if cr.lead else "klient")
        if cr.pdf:
            db.session.add(JourneyDocument(
                lead_id=cr.lead_id, kind="umowa_wyslana", filename=f"Umowa_{name}_{cr.id}.pdf", mime="application/pdf",
                data=cr.pdf, pdf=cr.pdf, source="dawny generator umów", received_at=cr.pdf_generated_at or cr.created_at,
            ))
        data = _jjson(cr.data)
        if cr.status == "wypelniony" and data:
            lines = [f"{_OLD_FORM_LABELS.get(k, k)}: {v}" for k, v in data.items() if str(v).strip()]
            if cr.submitted_at:
                lines.append(f"\nWypełniono: {to_local(cr.submitted_at):%d.%m.%Y %H:%M}")
            text = "\n".join(lines)
            db.session.add(JourneyDocument(
                lead_id=cr.lead_id, kind="inny", filename=f"Dane_do_umowy_{name}_{cr.id}.txt",
                mime="text/plain", data=text.encode("utf-8"), pdf=journey.text_pdf("Dane do umowy z formularza", text),
                source="dawny formularz umowy", received_at=cr.submitted_at or cr.created_at,
            ))


def journey_set_contract(j, lead, status, via=None, when=None):
    """Jedno miejsce zmiany statusu umowy — trzyma w zgodzie lead.contract_signed (kanban, dashboard)."""
    when = when or datetime.utcnow()
    if status == "podpisana":
        if j.contract_status != "podpisana":
            j.contract_signed_at, j.contract_signed_via = when, via or "recznie"
        if via == "mail" and not j.contract_sent_at:
            j.contract_sent_at = when
    elif status == "wyslana":
        j.contract_sent_at = j.contract_sent_at or when
        j.contract_signed_at = j.contract_signed_via = None
    else:
        j.contract_sent_at = j.contract_signed_at = j.contract_signed_via = j.contract_reminded_at = None
        j.contract_reminders = 0
    j.contract_status = status
    lead.contract_signed = status == "podpisana"
    j.updated_at = datetime.utcnow()
    _journey_badge_reset()


def journey_set_kb(j, status, when=None):
    when = when or datetime.utcnow()
    if status == "otrzymana":
        j.kb_received_at = j.kb_received_at if j.kb_status == "otrzymana" else when
        j.kb_sent_at = j.kb_sent_at or when
    elif status == "wyslana":
        j.kb_sent_at = j.kb_sent_at or when
        j.kb_received_at = None
    else:
        j.kb_sent_at = j.kb_received_at = j.kb_reminded_at = None
        j.kb_reminders = 0
    j.kb_status = status
    j.updated_at = datetime.utcnow()
    _journey_badge_reset()


def journey_move(j, lead, col, actor_id):
    """Karta przeciągnięta ręcznie: wszystko przed kolumną = zrobione, krok tej kolumny = jeszcze nie.
    CRM przyjmuje to bez dyskusji (np. umowa podpisana na miejscu bez maila). Archiwum to etap leada
    (Zrealizowany), więc tam się nie przeciąga."""
    if col not in {k for k, _, _ in journey.BOARD_COLUMNS} or lead.stage != "Wygrany":
        return False
    if col == "umowa":
        journey_set_contract(j, lead, "do_wyslania")
    elif col == "czekamy":
        journey_set_contract(j, lead, "wyslana")
    elif j.contract_status != "podpisana":
        journey_set_contract(j, lead, "podpisana", via="recznie")
    if col == "podpisane" and j.kb_status == "otrzymana":
        journey_set_kb(j, "wyslana" if j.kb_sent_at else "brak")
    elif col == "odhaczone" and j.kb_status != "otrzymana":
        journey_set_kb(j, "otrzymana")
    j.needs_review = False
    j.updated_at = datetime.utcnow()
    _journey_badge_reset()
    return True
