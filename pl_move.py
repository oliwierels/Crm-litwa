"""Przenoszenie litewskich i niemieckich leadów z bazy polskiego CRM do CRM LT/DE (rynek lt / de).

Obie aplikacje mają ten sam schemat bazy (LT CRM to kopia polskiego), więc wiersze kopiujemy
kolumna w kolumnę, przepinając tylko identyfikatory (lead, zadanie, mail). Kolejność jest bezpieczna:
najpierw zapis w LT (commit), dopiero potem usunięcie w PL — w jednej transakcji, tak jak robi to
przycisk „Usuń leada” w polskim CRM (maile, oferty i formularze zostają w PL, tylko bez leada).
"""
import os
import re
from datetime import datetime

from sqlalchemy import MetaData, create_engine, delete, insert, inspect, select, update

LT_CITIES = ("vilnius", "kaunas", "klaipėda", "klaipeda", "šiauliai", "siauliai", "panevėžys", "panevezys",
             "alytus", "marijampolė", "marijampole", "palanga", "druskininkai", "utena", "jonava", "mažeikiai",
             "mazeikiai", "lietuva", "lithuania", "litwa", "litauen", "wilno", "kowno", "kłajpeda")
_EMAIL_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")

_TABLES = ("leads", "lead_event_dates", "activities", "activity_comments", "stage_history", "logistics_crews",
           "journeys", "journey_mails", "journey_documents", "contract_requests", "sent_offers", "inbox_messages",
           "campaign_recipients", "app_settings")

_engine = None


def engine():
    global _engine
    if _engine is None:
        url = os.environ.get("PL_DATABASE_URL", "").strip()
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        _engine = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=0, pool_recycle=300,
                                connect_args={"connect_timeout": 15, "options": "-c statement_timeout=120000"}
                                if url.startswith("postgresql") else {})
    return _engine


def _meta(eng):
    meta = MetaData()
    present = set(inspect(eng).get_table_names())
    meta.reflect(bind=eng, only=[t for t in _TABLES if t in present])
    return meta


def is_lithuanian(row, places):
    """Ta sama reguła co w polskim CRM: telefon +370, e-mail .lt, kraj LT albo miasto na Litwie."""
    digits = re.sub(r"\D", "", row.get("contact_phone") or "")
    if digits.startswith("00370"):
        digits = digits[2:]
    if digits.startswith("370") and len(digits) >= 11:
        return True
    emails = _EMAIL_RE.findall(" ".join(str(row.get(k) or "") for k in ("contact_email", "contact_email2")))
    if any(e.lower().endswith(".lt") for e in emails):
        return True
    if (row.get("billing_country") or "").upper() == "LT":
        return True
    place = " ".join([row.get("location") or ""] + places).lower()
    if any(re.search(r"(?<![a-ząčęėįšųūž])" + re.escape(c), place) for c in LT_CITIES) or _LT_PLACE.search(place):
        return True
    return bool(_LT_PLACE_LT.search(row.get("notes") or ""))  # „Wydarzenie … w Vilniaus rajonas” w opisie zapytania


# Miasta na Litwie z litewskimi odmianami (Vilniaus, Vilniuje, Kauno, Kaune…) — ta sama reguła co w polskim CRM.
# W opisie zapytania liczą się tylko formy litewskie (nie „Litwa”/„Wilno” — polski klient może o Litwę tylko zapytać).
_LT_PLACE_LT_STEMS = (r"vilni(?:us|aus|uje|ų)", r"kaun(?:as|o|e)\b", r"klaip[eė]d", r"[sš]iauli(?:ai|ų|uose)\b",
                      r"panev[eė][zž]", r"alyt(?:us|aus|uje)\b", r"marijampol", r"palang(?:a|os|oje)\b", r"druskinink",
                      r"uten(?:a|os|oje)\b", r"jonav(?:a|os|oje)\b", r"ma[zž]eiki", r"lietuv", r"trak(?:ai|ų|uose)\b",
                      r"k[eė]daini", r"taurag[eė]", r"ukmerg", r"visagin", r"bir[sš]ton", r"gargžd", r"gargzd")
_LT_PLACE_LT = re.compile(r"(?<![a-ząćęłńóśźżčėįšųū])(?:" + "|".join(_LT_PLACE_LT_STEMS) + ")", re.I)
_LT_PLACE = re.compile(r"(?<![a-ząćęłńóśźżčėįšųū])(?:" + "|".join(_LT_PLACE_LT_STEMS + (
    r"lithuan", r"litw(?:a|ie|y|ę|ą)\b", r"litauen", r"wiln(?:o|ie|a)\b", r"kown(?:o|ie|a)\b", r"kłajped")) + ")", re.I)


_DE_TLDS = (".de", ".at", ".ch")
_PHONE_LANG = (("370", "lt"), ("49", "de"), ("43", "de"), ("41", "de"))


def is_german(row, places):
    """Ta sama reguła co zakładka „Niemcy” w polskim CRM (lead_language == "de"): kraj DE/AT/CH, e-mail
    .de/.at/.ch albo telefon +49/+43/+41. Firma z Polski na targach w Niemczech zostaje w PL."""
    if is_lithuanian(row, places):
        return False
    country = (row.get("billing_country") or "").upper()
    if country in ("DE", "AT", "CH"):
        return True
    emails = [e.lower() for e in _EMAIL_RE.findall(" ".join(str(row.get(k) or "") for k in ("contact_email", "contact_email2")))]
    if any(e.endswith(_DE_TLDS) for e in emails):
        return True
    raw = re.sub(r"[^\d+]", "", row.get("contact_phone") or "")
    digits = raw.lstrip("+")
    if digits.startswith("00"):
        digits = digits[2:]
    elif not raw.startswith("+"):
        digits = ""  # numer bez kierunkowego = polski
    if digits and not digits.startswith("48") and len(digits) >= 10:
        return next((lang for code, lang in _PHONE_LANG if digits.startswith(code)), "en") == "de"
    return False


# Wygrane leady LT/DE wracają do polskiego CRM — tylko do logistyki (ekipa, robot, trasa). Kopia ma source
# „CRM LT” / „CRM DE” i flagę przed nazwą; polski CRM nie wysyła do niej maili i nie liczy jej w statystykach,
# a przenoszenie poniżej jej nie rusza (inaczej kopia wracałaby w kółko do CRM LT/DE).
COPY_SOURCES = {"lt": "CRM LT", "de": "CRM DE"}
COPY_FLAGS = {"lt": "🇱🇹", "de": "🇩🇪"}
WON = ("Wygrany", "Zrealizowany")


def is_copy(row):
    return (row.get("source") or "") in COPY_SOURCES.values()


def find_market(eng, code, meta=None):
    """Lista (wiersz leada, miejsca z dni wynajmu) leadów rynku `code` (lt / de) w polskiej bazie."""
    meta = meta or _meta(eng)
    check = is_lithuanian if code == "lt" else is_german
    leads, days = meta.tables["leads"], meta.tables.get("lead_event_dates")
    with eng.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(select(leads))]
        places = {}
        if days is not None and "location" in days.c:
            for r in conn.execute(select(days.c.lead_id, days.c.location)):
                if r.location:
                    places.setdefault(r.lead_id, []).append(r.location)
    return [(r, places.get(r["id"], [])) for r in rows if not is_copy(r) and check(r, places.get(r["id"], []))]


def find_lithuanian(eng, meta=None):
    return find_market(eng, "lt", meta)


def find_german(eng, meta=None):
    return find_market(eng, "de", meta)


def _cols(model, row, skip=("id",)):
    names = {c.key for c in model.__table__.columns}
    return {k: v for k, v in row.items() if k in names and k not in skip}


def _copy_lead(conn, meta, db, M, row, actor_id, code="lt"):
    """Kopiuje jednego leada z dziećmi do bazy LT/DE (bez commita). Zwraca nowy Lead."""
    pl_id = row["id"]
    t = meta.tables
    lead = M["Lead"](**_cols(M["Lead"], row))
    lead.pl_id = pl_id
    lead.market = code  # lt = litewski, de = niemiecki
    db.session.add(lead)
    db.session.flush()

    def rows(table, col="lead_id", value=pl_id):
        tb = t.get(table)
        if tb is None or col not in tb.c:
            return []
        return [dict(r._mapping) for r in conn.execute(select(tb).where(tb.c[col] == value))]

    for r in rows("lead_event_dates"):
        db.session.add(M["LeadEventDate"](**dict(_cols(M["LeadEventDate"], r), lead_id=lead.id)))
    for r in rows("stage_history"):
        db.session.add(M["StageHistory"](**dict(_cols(M["StageHistory"], r), lead_id=lead.id)))
    for r in rows("activities"):
        act = M["Activity"](**dict(_cols(M["Activity"], r), lead_id=lead.id))
        db.session.add(act)
        db.session.flush()
        for cm in rows("activity_comments", "activity_id", r["id"]):
            db.session.add(M["ActivityComment"](**dict(_cols(M["ActivityComment"], cm), activity_id=act.id)))
    for r in rows("logistics_crews"):  # operatorzy i roboty to osobne listy w każdym CRM — przypisanie od nowa
        db.session.add(M["Crew"](**dict(_cols(M["Crew"], r), lead_id=lead.id, operator_id=None, robot_id=None,
                                        operator2_id=None, robot2_id=None)))
    for r in rows("journeys"):
        db.session.add(M["Journey"](**dict(_cols(M["Journey"], r), lead_id=lead.id)))
    mail_ids = {}
    for r in rows("journey_mails"):
        same = M["JourneyMail"].query.filter_by(message_key=r.get("message_key")).first() if r.get("message_key") else None
        if same is not None:  # ten mail CRM LT już zna (np. przeczytał go ze swojej skrzynki) — tylko przypnij do leada
            if same.lead_id is None:
                same.lead_id = lead.id
            mail_ids[r["id"]] = same.id
            continue
        jm = M["JourneyMail"](**dict(_cols(M["JourneyMail"], r), lead_id=lead.id))
        db.session.add(jm)
        db.session.flush()
        mail_ids[r["id"]] = jm.id
    for r in rows("journey_documents"):
        db.session.add(M["JourneyDocument"](**dict(_cols(M["JourneyDocument"], r), lead_id=lead.id,
                                                   mail_id=mail_ids.get(r.get("mail_id")))))
    for name, model in (("contract_requests", "ContractRequest"), ("sent_offers", "SentOffer"),
                        ("inbox_messages", "InboxMessage")):
        for r in rows(name):
            data = dict(_cols(M[model], r), lead_id=lead.id)
            if model == "InboxMessage" and "message_key" in data:
                if M[model].query.filter_by(message_key=data["message_key"]).first():
                    continue  # ten mail już jest w LT (np. przyszedł też na skrzynkę LT)
            db.session.add(M[model](**data))
    db.session.add(M["Activity"](
        lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(), assigned_to=row.get("assigned_to") or actor_id,
        notes=f"📦 Przeniesiony z polskiego CRM (tam lead #{pl_id}) razem z historią, zadaniami i dokumentami.",
    ))
    return lead


def _delete_in_pl(conn, meta, pl_id, clickup_task_id, code="lt"):
    """Usuwa leada w PL tak jak przycisk „Usuń” w polskim CRM i blokuje ponowny import z ClickUp."""
    t = meta.tables
    for name in ("inbox_messages", "campaign_recipients", "sent_offers", "contract_requests"):
        if name in t and "lead_id" in t[name].c:
            conn.execute(update(t[name]).where(t[name].c.lead_id == pl_id).values(lead_id=None))
    if "journey_mails" in t:
        values = {"lead_id": None}
        if "pending" in t["journey_mails"].c:
            values["pending"] = False
        if "journey_documents" in t and "mail_id" in t["journey_documents"].c:
            mails = select(t["journey_mails"].c.id).where(t["journey_mails"].c.lead_id == pl_id)
            conn.execute(update(t["journey_documents"]).where(t["journey_documents"].c.mail_id.in_(mails))
                         .values(mail_id=None))
        conn.execute(update(t["journey_mails"]).where(t["journey_mails"].c.lead_id == pl_id).values(**values))
    if "activity_comments" in t and "activities" in t:
        acts = select(t["activities"].c.id).where(t["activities"].c.lead_id == pl_id)
        conn.execute(delete(t["activity_comments"]).where(t["activity_comments"].c.activity_id.in_(acts)))
    for name in ("activities", "lead_event_dates", "stage_history", "logistics_crews", "journey_documents", "journeys"):
        if name in t and "lead_id" in t[name].c:
            conn.execute(delete(t[name]).where(t[name].c.lead_id == pl_id))
    conn.execute(delete(t["leads"]).where(t["leads"].c.id == pl_id))
    if clickup_task_id and "app_settings" in t:
        key = f"clickup_dup:{clickup_task_id}"[:64]
        exists = conn.execute(select(t["app_settings"].c.key).where(t["app_settings"].c.key == key)).first()
        if not exists:
            conn.execute(insert(t["app_settings"]).values(key=key, value=f"przeniesiony do CRM {code.upper()}"))


def _mark_copy_in_pl(conn, meta, pl_id, row, code):
    leads = meta.tables["leads"]
    flag = COPY_FLAGS[code]
    company = row.get("company") or ""
    values = {"source": COPY_SOURCES[code], "company": company if company.startswith(flag) else f"{flag} {company}"[:200]}
    conn.execute(update(leads).where(leads.c.id == pl_id).values(**values))


def copy_payload(lead, code):
    """Pola kopii w polskim CRM (to, czego potrzebuje logistyka) + dni wynajmu. Zwraca (pola, dni, odcisk)."""
    flag = COPY_FLAGS[code]
    company = lead.company or ""
    stage = lead.stage if lead.stage in WON + ("Stracony",) else "Stracony"  # cofnięty z wygranej — znika z logistyki
    value = lead.value_pln
    info = (f"{flag} Wygrany lead z CRM {code.upper()} (tam lead #{lead.id}) — tutaj tylko do logistyki: ekipa, robot, trasa.\n"
            f"Klienta prowadzi CRM {code.upper()} (maile, umowa, faktura) — zmiany danych rób tam, tutaj nadpiszą się same.")
    if lead.stage not in WON:
        info += f"\nW CRM {code.upper()} etap: {lead.stage}."
    if value:
        info += f"\nWartość: {value:,.0f} {lead.currency or 'EUR'}".replace(",", " ")
    if (lead.notes or "").strip():
        info += "\n---\n" + lead.notes.strip()[:4000]
    fields = {
        "company": (company if company.startswith(flag) else f"{flag} {company}")[:200],
        "contact_name": lead.contact_name, "contact_email": lead.contact_email, "contact_email2": lead.contact_email2,
        "contact_phone": lead.contact_phone, "stage": stage, "robot_model": lead.robot_model,
        "rental_days": lead.rental_days, "location": lead.location, "event_date": lead.event_date,
        "event_end_date": lead.event_end_date, "contract_signed": bool(lead.contract_signed),
        "assigned_to": lead.assigned_to, "source": COPY_SOURCES[code], "notes": info,
        "billing_country": (lead.billing_country or code.upper())[:2],
    }
    days = [(d.event_date, d.location) for d in lead.event_days]
    import hashlib
    import json
    mark = hashlib.sha1(json.dumps([fields, days], default=str, sort_keys=True).encode()).hexdigest()
    return fields, days, mark


def push_copy(eng, meta, lead, code, now=None):
    """Zapisuje / odświeża kopię wygranego leada w bazie polskiego CRM. Zwraca (id w PL, odcisk)."""
    now = now or datetime.utcnow()
    t = meta.tables
    leads, days_t, hist = t["leads"], t.get("lead_event_dates"), t.get("stage_history")
    fields, days, mark = copy_payload(lead, code)
    fields = {k: v for k, v in fields.items() if k in leads.c}
    with eng.begin() as conn:
        pl_id, old_stage = lead.pl_copy_id, None
        if pl_id:
            cur = conn.execute(select(leads.c.stage).where(leads.c.id == pl_id)).first()
            if cur is None:
                pl_id = None  # kopię ktoś usunął w PL — wygrany musi tam być, zakładamy ją od nowa
            else:
                old_stage = cur.stage
                conn.execute(update(leads).where(leads.c.id == pl_id).values(**fields))
        if not pl_id:
            extra = {"created_at": now, "handled_at": now, "handled_how": f"CRM {code.upper()}"[:40],
                     "currency": lead.currency or "EUR", "value_pln": None, "clickup_pending": False,
                     "quality": getattr(lead, "quality", None)}
            values = dict(fields, **{k: v for k, v in extra.items() if k in leads.c})
            pl_id = conn.execute(insert(leads).values(**values)).inserted_primary_key[0]
        if hist is not None and old_stage != fields.get("stage"):
            row = {"lead_id": pl_id, "from_stage": old_stage, "to_stage": fields.get("stage"), "changed_at": now}
            conn.execute(insert(hist).values(**{k: v for k, v in row.items() if k in hist.c}))
        if days_t is not None:
            key = lambda d: (str(d[0]), d[1] or "")
            have = sorted(((r.event_date, r.location) for r in conn.execute(
                select(days_t.c.event_date, days_t.c.location).where(days_t.c.lead_id == pl_id))), key=key)
            if [key(d) for d in have] != [key(d) for d in sorted(days, key=key)]:
                conn.execute(delete(days_t).where(days_t.c.lead_id == pl_id))
                for d, loc in days:
                    conn.execute(insert(days_t).values(lead_id=pl_id, event_date=d, location=loc))
    return pl_id, mark


def move_all(eng, db, M, actor_id, limit=200, progress=None, on_step=None, code="lt"):
    """Przenosi leady rynku `code` (lt / de) z PL. Zwraca (liczba przeniesionych, lista problemów).
    on_step(stan) jest wołane przed i po każdym leadzie — zapisuje postęp, żeby było go widać po restarcie."""
    meta = _meta(eng)
    found = find_market(eng, code, meta)[:limit]
    state = progress if progress is not None else {}
    state.update(total=len(found), done=0, current="", errors=[])
    if on_step:
        on_step(state)
    moved, errors = 0, state["errors"]
    for row, _places in found:
        pl_id = row["id"]
        state["current"] = f"#{pl_id} {row.get('company') or ''}"[:120]
        if on_step:
            on_step(state)
        try:
            won = row.get("stage") in WON
            existing = M["Lead"].query.filter_by(pl_id=pl_id).first()
            if existing is None:
                with eng.connect() as conn:
                    lead = _copy_lead(conn, meta, db, M, row, actor_id, code)
                if won:
                    lead.pl_copy_id = pl_id  # ten sam wiersz w PL zostaje kopią do logistyki
                db.session.commit()  # najpierw bezpiecznie zapisane w LT…
            with eng.begin() as conn:  # …dopiero potem usunięte w PL
                if won:  # wygrany: w PL zostaje (ekipy, trasy, dokumenty) — tylko jako kopia z CRM LT/DE
                    _mark_copy_in_pl(conn, meta, pl_id, row, code)
                else:
                    _delete_in_pl(conn, meta, pl_id, row.get("clickup_task_id"), code)
            moved += 1
            state["done"] = moved
        except Exception as e:
            db.session.rollback()
            errors.append(f"#{pl_id} {row.get('company')}: {type(e).__name__}: {str(e).splitlines()[0][:160]}")
    state["current"] = ""
    if on_step:
        on_step(state)
    return moved, errors
