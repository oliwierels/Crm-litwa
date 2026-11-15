# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("journey_stan") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: Customer Journey: dni, stan i widok klienta

def _jjson(text):
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _local_today():
    return to_local(datetime.utcnow()).date()


def _local_date(dt):
    return to_local(dt).date() if dt else None


def journey_days(lead):
    return sorted(ed.event_date for ed in lead.event_days if sane_event_date(ed.event_date))


def journey_state(j):
    """Statusy w postaci dla reguł z journey.py (daty po polsku, bez godzin)."""
    from types import SimpleNamespace
    return SimpleNamespace(
        contract_status=j.contract_status, contract_sent_at=_local_date(j.contract_sent_at),
        contract_reminded_at=_local_date(j.contract_reminded_at), contract_reminders=j.contract_reminders or 0,
        kb_status=j.kb_status, kb_sent_at=_local_date(j.kb_sent_at), kb_reminded_at=_local_date(j.kb_reminded_at),
        kb_reminders=j.kb_reminders or 0, after=_jjson(j.after), needs_review=bool(j.needs_review),
    )


def journey_view(j, lead, today=None, cfg=None, unseen=None, pending=None):
    """Wszystko, co pokazuje karta klienta: kolumna, kolor, co zrobić, dni do wydarzenia."""
    today = today or _local_today()
    cfg = cfg or journey_cfg()
    if unseen is None:
        unseen = JourneyDocument.query.filter_by(lead_id=lead.id, seen=False).count()
    if pending is None:
        pending = JourneyMail.query.filter_by(lead_id=lead.id, pending=True).count()
    st = journey_state(j)
    days = journey_days(lead)
    event = journey.next_event_day(days, today)
    acts = journey.actions(st, lead.stage, days, today, cfg, unseen, pending)
    return {
        "j": j, "lead": lead, "st": st, "col": journey.column(st, lead.stage),
        "urgency": journey.urgency(st, lead.stage, days, today, cfg), "actions": acts,
        "attention": any(a[3] for a in acts), "days": days, "event": event,
        "left": (event - today).days if event else None,
        "after": st.after, "unseen": unseen, "pending": pending,
        "kb_deadline": (days[0] - timedelta(days=cfg["kb_deadline"])) if days else None,
    }


def journey_brief(j, lead):
    """Skrót dla asystenta / konektora."""
    v = journey_view(j, lead)
    return {
        "etap": journey.COLUMN_LABELS[v["col"]],
        "umowa": journey.CONTRACT_STATUSES.get(j.contract_status, j.contract_status)
                 + (f" ({to_local(j.contract_signed_at):%Y-%m-%d}, {'odesłana mailem' if j.contract_signed_via == 'mail' else 'oznaczona ręcznie'})"
                    if j.contract_status == "podpisana" and j.contract_signed_at else "")
                 + (f" (wysłana {to_local(j.contract_sent_at):%Y-%m-%d})" if j.contract_status == "wyslana" and j.contract_sent_at else ""),
        "baza_wiedzy": journey.KB_STATUSES.get(j.kb_status, j.kb_status),
        "do_zrobienia": [a[1] for a in v["actions"]],
        "dokumenty": [{"rodzaj": DOC_KINDS.get(d.kind, d.kind), "plik": d.filename,
                       "kiedy": f"{to_local(d.received_at or d.created_at):%Y-%m-%d}"} for d in lead.journey_documents],
    }
