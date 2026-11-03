"""Przenoszenie litewskich leadów z bazy polskiego CRM do LT CRM.

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
    return any(re.search(r"(?<![a-ząčęėįšųūž])" + re.escape(c), place) for c in LT_CITIES)


def find_lithuanian(eng, meta=None):
    """Lista (wiersz leada, miejsca z dni wynajmu) litewskich leadów w polskiej bazie."""
    meta = meta or _meta(eng)
    leads, days = meta.tables["leads"], meta.tables.get("lead_event_dates")
    with eng.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(select(leads))]
        places = {}
        if days is not None and "location" in days.c:
            for r in conn.execute(select(days.c.lead_id, days.c.location)):
                if r.location:
                    places.setdefault(r.lead_id, []).append(r.location)
    return [(r, places.get(r["id"], [])) for r in rows if is_lithuanian(r, places.get(r["id"], []))]


def _cols(model, row, skip=("id",)):
    names = {c.key for c in model.__table__.columns}
    return {k: v for k, v in row.items() if k in names and k not in skip}


def _copy_lead(conn, meta, db, M, row, actor_id):
    """Kopiuje jednego leada z dziećmi do bazy LT (bez commita). Zwraca nowy Lead."""
    pl_id = row["id"]
    t = meta.tables
    lead = M["Lead"](**_cols(M["Lead"], row))
    lead.pl_id = pl_id
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


def _delete_in_pl(conn, meta, pl_id, clickup_task_id):
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
            conn.execute(insert(t["app_settings"]).values(key=key, value="przeniesiony do LT CRM"))


def move_all(eng, db, M, actor_id, limit=200, progress=None, on_step=None):
    """Przenosi litewskie leady z PL. Zwraca (liczba przeniesionych, lista problemów).
    on_step(stan) jest wołane przed i po każdym leadzie — zapisuje postęp, żeby było go widać po restarcie."""
    meta = _meta(eng)
    found = find_lithuanian(eng, meta)[:limit]
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
            existing = M["Lead"].query.filter_by(pl_id=pl_id).first()
            if existing is None:
                with eng.connect() as conn:
                    _copy_lead(conn, meta, db, M, row, actor_id)
                db.session.commit()  # najpierw bezpiecznie zapisane w LT…
            with eng.begin() as conn:  # …dopiero potem usunięte w PL
                _delete_in_pl(conn, meta, pl_id, row.get("clickup_task_id"))
            moved += 1
            state["done"] = moved
        except Exception as e:
            db.session.rollback()
            errors.append(f"#{pl_id} {row.get('company')}: {type(e).__name__}: {str(e).splitlines()[0][:160]}")
    state["current"] = ""
    if on_step:
        on_step(state)
    return moved, errors
