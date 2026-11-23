# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("zadania_pomocnicze") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: pomocnicze: pilność zadań, cisza w leadzie, zapis zmiany etapu, podpowiedź typu klienta

def activity_urgency(act):
    if act.done:
        return "done"
    if not act.due_date:
        return "future"
    today = date.today()
    if isinstance(act.due_date, datetime):
        d = act.due_date.date()
    else:
        d = act.due_date
    delta = (d - today).days
    if delta < 0:
        return "overdue"
    if delta == 0:
        return "today"
    if delta <= 3:
        return "soon"
    return "future"


def lead_inactivity(lead):
    timestamps = [t for t in [lead.created_at] if t]
    for a in lead.activities:
        if a.created_at:
            timestamps.append(a.created_at)
        if a.done_at:
            timestamps.append(a.done_at)
    if not timestamps:
        return 0
    last = max(timestamps)
    return (datetime.utcnow() - last).days


def lead_inactivity_color(days):
    if days < 3:
        return "green"
    if days < 7:
        return "yellow"
    return "red"


def pending_activities(lead):
    # notatki (kind=note) to komentarze, nie zadania
    acts = [a for a in lead.activities if not a.done and a.kind != "note"]
    return sorted(acts, key=lambda a: (a.due_date is None, a.due_date or date.max))


ACTIVE_STAGES = [s for s in STAGES if s not in WON_STAGES + ("Stracony",)]


def record_stage_change(lead, new_stage, actor_id, reason=None, note=None):
    """Zmień etap leada i zapisz wpis w historii. Zwraca True, gdy etap się zmienił.
    Bez bramek — to robią automaty CRM; zmiany od ludzi (UI, API, MCP) idą przez gated_stage_change."""
    if new_stage not in STAGES or new_stage == lead.stage:
        return False
    was_open = lead.stage not in WON_STAGES
    db.session.add(StageHistory(
        lead_id=lead.id, from_stage=lead.stage, to_stage=new_stage, changed_by=actor_id, reason=reason, note=note,
    ))
    if new_stage == "Wygrany" and lead.stage != "Zrealizowany":
        lead.contract_signed = False  # świeża wygrana — umowa jeszcze do podpisania
    if lead.stage == "Stracony" and new_stage != "Stracony":
        lead.lost_reason = lead.lost_comment = None  # wrócił do gry (powód zostaje w historii etapów)
    lead.stage = new_stage
    if new_stage == "Oferta":
        ensure_followup_call(lead)  # oferta poszła (z CRM albo mailem poza nim) — za tydzień telefon
    if new_stage == "Wygrany":
        ensure_journey(lead)  # wygrany wpada do Customer Journey (umowa do wysłania)
    if new_stage in WON_STAGES and was_open:
        clear_open_tasks(lead)  # wygrany: dalej prowadzi Customer Journey — otwarte zadania z lejka znikają
    return True


def last_sent_offer_value(lead):
    """Kwota ostatniej wysłanej oferty (z PDF) — do ostrzeżenia, gdy wartość leada jest inna (przypadek VULCAN)."""
    if not lead or not lead.id:
        return None
    o = (SentOffer.query.filter(SentOffer.lead_id == lead.id, SentOffer.sent_at.isnot(None))
         .order_by(SentOffer.sent_at.desc()).first())
    return offer_value(o)[0] if o else None


def client_type_hint(lead):
    """(klucz, etykieta, uzasadnienie) podpowiedzi typu klienta — dla UI, MCP i automatów."""
    key, why = sales_rules.suggest_client_type(lead.company, lead.contact_name, lead.emails, getattr(lead, "nip", None),
                                               lead.notes)
    return key, sales_rules.CLIENT_TYPES[key], why
