# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("silnik_porzadku") po wyglądzie — działa w przestrzeni nazw crm.py.
# Temat: SILNIK PORZĄDKU — poczta i etapy pilnują, żeby w CRM nie było bałaganu
#
# 1) Poczta → leady. Każdy nowy mail od klienta, który pasuje do leada (adres z leada), czyta Claude i rozpoznaje:
#    - akceptacja / zamówienie (przyjmuje ofertę, prosi o umowę, fakturę, potwierdza termin i cenę) → lead na „Wygrany”
#      (gdy są data i kwota — bramki etapu jak przy człowieku; inaczej powiadomienie, czego brakuje),
#    - rezygnacja → powiadomienie z propozycją „Stracony” (sam nie zamyka — decyduje człowiek),
#    - pytanie / prośba → powiadomienie „klient czeka na odpowiedź”.
#    Zawsze: zadania „pogoni” (follow-up, przypomnienie, telefon po ofercie, drugi krok), które powstały PRZED tym mailem,
#    stają się nieaktualne — klient się odezwał. Stracony lead, który pisze znowu → powiadomienie „odezwał się”.
# 2) Higiena etapów (co 10 min): wygrany / zrealizowany / stracony lead nie ma otwartych zadań sprzed zamknięcia
#    (zadania dodane przez człowieka po zamknięciu zostają).
# 3) NIC nie usuwa leadów ani danych: zadanie z treścią od człowieka zamienia się w komentarz „🧹 Nieaktualne: …”
#    (zostaje w historii, nie liczy się jako zrobione), automatyczne przypomnienia (bez autora) znikają — przy leadzie
#    zostaje jeden komentarz, ile i dlaczego. Każda decyzja silnika = komentarz przy leadzie + powiadomienie opiekuna.
# 4) Powiadomienia (dzwonek 🔔): wygrane całego zespołu (z pokazem 🏆), decyzje silnika, „klient odpisał”.

MAIL_ENGINE_KEY = "mail_engine_last_id"
MAIL_ENGINE_MAX_AGE_H = 72  # starszych maili silnik nie analizuje (np. po długiej przerwie w synchronizacji)
MAIL_ENGINE_AI_MODEL = os.environ.get("MAIL_ENGINE_AI_MODEL", "").strip() or INBOX_AI_MODEL
CLEAN_PREFIX = "🧹 Nieaktualne"
# zadania „pogoni” — ich celem było doczekać się reakcji klienta; mail od klienta je unieważnia
_CHASE_RE = re.compile(r"(?i)(follow-?up|przypomn|drugi krok|po ofercie|zapytaj o decyzj|telefon potwierdzaj|ponow|"
                       r"nie odebra|zadzwoń do osoby prywatnej|czy oferta dotarła|pierwsza odpowiedź)")
_CHASE_KINDS = ("call", "email", "sms")


class Notice(db.Model):
    """Powiadomienie w dzwonku dla jednej osoby (user_id)."""
    __tablename__ = "notices"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    user_id = db.Column(db.Integer, index=True)
    lead_id = db.Column(db.Integer, index=True)
    kind = db.Column(db.String(20))  # win | lost | reply | clean | info | blocked
    title = db.Column(db.String(300))
    body = db.Column(db.Text)
    read_at = db.Column(db.DateTime)
    celebrate = db.Column(db.Boolean, default=False)  # pokaz 🏆 przy następnym wejściu do CRM
    celebrated_at = db.Column(db.DateTime)


def notify(user_ids, kind, title, body="", lead=None, celebrate=False):
    """Powiadomienie dla osób (id albo lista id; None = cały zespół)."""
    if user_ids is None:
        user_ids = [u["id"] for u in USERS]
    elif isinstance(user_ids, int):
        user_ids = [user_ids]
    for uid in dict.fromkeys(u for u in user_ids if u in USERS_BY_ID):
        db.session.add(Notice(user_id=uid, kind=kind, title=title[:300], body=(body or "")[:4000],
                              lead_id=getattr(lead, "id", None), celebrate=celebrate))


def notices_for(uid, limit=15):
    try:
        return (Notice.query.filter(Notice.user_id == uid).order_by(Notice.read_at.isnot(None), Notice.created_at.desc())
                .limit(limit).all())
    except Exception:
        db.session.rollback()
        return []


def notices_unread(uid):
    try:
        return Notice.query.filter(Notice.user_id == uid, Notice.read_at.is_(None)).count()
    except Exception:
        db.session.rollback()
        return 0


def notice_to_celebrate(uid):
    """Najnowsza nieobejrzana wygrana do pokazania 🏆 (po zalogowaniu / wejściu na stronę)."""
    try:
        return (Notice.query.filter(Notice.user_id == uid, Notice.celebrate.is_(True), Notice.celebrated_at.is_(None))
                .order_by(Notice.created_at.desc()).first())
    except Exception:
        db.session.rollback()
        return None


@app.route("/crm/powiadomienia/<int:nid>")
def crm_notice_open(nid):
    n = db.session.get(Notice, nid)
    if n is not None and n.user_id == get_current_user(request)["id"]:
        n.read_at = n.read_at or datetime.utcnow()
        db.session.commit()
        if n.lead_id:
            return redirect(url_for("crm_lead_detail", lead_id=n.lead_id))
    return redirect(url_for("crm_dashboard"))


@app.route("/crm/api/powiadomienia/przeczytane", methods=["POST"])
def crm_notices_read():
    uid = get_current_user(request)["id"]
    Notice.query.filter(Notice.user_id == uid, Notice.read_at.is_(None)).update({"read_at": datetime.utcnow()})
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/crm/api/powiadomienia/<int:nid>/pokazane", methods=["POST"])
def crm_notice_celebrated(nid):
    n = db.session.get(Notice, nid)
    if n is not None and n.user_id == get_current_user(request)["id"]:
        n.celebrated_at = datetime.utcnow()
        db.session.commit()
    return jsonify({"ok": True})


def _money(lead):
    return f"{fmt_pln(lead.value_pln)} {lead.currency or M(lead_market(lead))['currency']}" if lead.value_pln else ""


def notify_team_win(lead, actor_id=None, how=""):
    """Wygrana = powiadomienie z pokazem 🏆 dla całego zespołu (osoba, która sama przeciągnęła leada, widziała pokaz)."""
    who = USERS_BY_ID.get(actor_id, {}).get("name", "") if actor_id else ""
    title = f"🏆 Wygrana: {lead.company}" + (f" · {_money(lead)}" if lead.value_pln else "")
    body = (how or "Lead oznaczony jako wygrany") + (f" — {who}" if who else "") + "."
    body = body[0].upper() + body[1:]
    others = [u["id"] for u in USERS if u["id"] != actor_id]
    notify(others, "win", title, body, lead, celebrate=True)


# ---- zadania: nieaktualne zamiast skasowanych ----

def retire_task(act, reason):
    """Zadanie przestało być potrzebne. Automatyczne (bez autora) — znika; od człowieka — zamienia się w komentarz
    w historii leada (nie liczy się jako zrobione). Automatyczne, pod którym ktoś coś dopisał (komentarz, wynik próby
    kontaktu), też zostaje jako komentarz — ludzka praca nigdy nie znika. Zwraca 'deleted' albo 'note'."""
    if act.created_by is None and not act.comments and not act.outcome:
        db.session.delete(act)
        return "deleted"
    label = ACTIVITY_KIND_LABELS.get(act.kind, act.kind)
    due = f" (termin {act.due_date:%d.%m})" if act.due_date else ""
    act.notes = f"{CLEAN_PREFIX} — {reason}: {label}{due}" + (f" — {act.notes}" if act.notes else "")
    act.kind, act.done, act.done_at, act.outcome = "note", True, datetime.utcnow(), None
    return "note"


def retire_tasks(lead, tasks, reason):
    """Zamyka listę zadań leada i zostawia przy nim jeden komentarz z podsumowaniem. Zwraca liczbę zadań."""
    tasks = list(tasks)
    if not tasks:
        return 0
    kinds = [ACTIVITY_KIND_LABELS.get(a.kind, a.kind).lower() for a in tasks]
    auto = sum(1 for a in tasks if retire_task(a, reason) == "deleted")
    if auto:
        db.session.add(Activity(lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(),
                                assigned_to=lead.assigned_to or USERS[0]["id"],
                                notes=f"{CLEAN_PREFIX} — {reason}: zamknięto {len(tasks)} zadań ({', '.join(kinds)})."))
    return len(tasks)


CLOSED_STAGES_ALL = tuple(WON_STAGES) + ("Stracony",)


def _closed_since(lead):
    """Od kiedy lead jest na obecnym (zamkniętym) etapie — ostatni wpis historii z tym etapem."""
    at = (db.session.query(db.func.max(StageHistory.changed_at))
          .filter(StageHistory.lead_id == lead.id, StageHistory.to_stage == lead.stage).scalar())
    return at or datetime.utcnow()


def stage_hygiene_pass():
    """Wygrane, zrealizowane i stracone leady bez otwartych zadań sprzed zamknięcia. Zwraca liczbę zadań."""
    n = 0
    leads = all_markets(Lead.query.filter(Lead.stage.in_(CLOSED_STAGES_ALL), Lead.id.in_(
        db.session.query(Activity.lead_id).filter(Activity.done.isnot(True), Activity.kind != "note")))).all()
    for lead in leads:
        since = _closed_since(lead)
        stale = [a for a in lead_open_tasks(lead.id).all() if (a.created_at or datetime.min) <= since]
        reason = {"Wygrany": "lead wygrany (dalej Customer Journey)", "Zrealizowany": "lead zrealizowany",
                  "Stracony": "lead stracony"}.get(lead.stage, "lead zamknięty")
        n += retire_tasks(lead, stale, reason)
    if n:
        db.session.commit()
    return n


# ---- poczta → leady ----

MAIL_ENGINE_TOOL = {
    "name": "client_mail",
    "description": "Co klient chce w tym mailu — w kontekście sprzedaży wynajmu robota.",
    "strict": True,
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "intent": {"type": "string", "enum": ["akceptacja", "rezygnacja", "pytanie", "dokumenty", "inne"],
                       "description": "akceptacja = wyraźnie zamawia / rezerwuje: przyjmuje ofertę, prosi o umowę, proformę "
                                      "lub fakturę zaliczkową, potwierdza termin i cenę. rezygnacja = odmawia, wybrał "
                                      "innego dostawcę, odwołuje. pytanie = pyta, negocjuje, prosi o coś, czeka na "
                                      "odpowiedź. dokumenty = odsyła umowę, dane, materiały. inne = reszta."},
            "confidence": {"type": "string", "enum": ["wysoka", "średnia", "niska"]},
            "needs_reply": {"type": "boolean", "description": "Czy klient czeka na naszą odpowiedź"},
            "summary": {"type": "string", "description": "Jedno zdanie po polsku: czego chce klient"},
        },
        "required": ["intent", "confidence", "needs_reply", "summary"],
    },
}


def _engine_mail_text(m, lead):
    fresh = poczta.split_quote(m.body_text or "")[0]
    text = (m.body_pl if m.body_pl and m.lang not in ("pl", None) else fresh) or ""
    offer = (SentOffer.query.filter(SentOffer.lead_id == lead.id, SentOffer.sent_at.isnot(None))
             .order_by(SentOffer.sent_at.desc()).first())
    ctx = [f"Lead: {lead.company}, etap {lead.stage}"]
    if offer is not None:
        ctx.append(f"ostatnia oferta wysłana {to_local(offer.sent_at):%d.%m} ({offer.price or ''})")
    if lead.value_pln:
        ctx.append(f"wartość {_money(lead)}")
    return f"{'; '.join(ctx)}\nTemat: {m.subject or ''}\nMail klienta:\n{' '.join(text.split())[:3000]}"


def _classify_client_mail(m, lead):
    if _ai_client() is None:
        return None
    system = ("Jesteś asystentem firmy 33Bots (wynajem humanoidalnego robota Unitree G1 z operatorem na eventy). "
              "Oceniasz JEDEN mail od klienta. „akceptacja” tylko wtedy, gdy klient jednoznacznie zamawia: przyjmuje "
              "ofertę, prosi o umowę albo fakturę / proformę, potwierdza termin i cenę. Pytania, negocjacje ceny, "
              "„zastanowimy się”, „proszę o więcej informacji” to „pytanie”. „rezygnacja” tylko przy wyraźnej odmowie. "
              "Pewność „wysoka” tylko, gdy z treści nie ma wątpliwości.")
    return _ai_forced_tool(MAIL_ENGINE_AI_MODEL, system, _engine_mail_text(m, lead), MAIL_ENGINE_TOOL, max_tokens=600)


def _lead_note(lead, text):
    db.session.add(Activity(lead_id=lead.id, kind="note", done=True, done_at=datetime.utcnow(),
                            assigned_to=lead.assigned_to or USERS[0]["id"], notes=text))


def handle_client_mail(m, lead, res=None):
    """Jeden nowy mail klienta przypisany do leada → porządek w leadzie. Zwraca opis, co zrobiono."""
    owner = lead.assigned_to or USERS[0]["id"]
    when = f"{to_local(m.date):%d.%m %H:%M}" if m.date else ""
    if lead.stage == "Stracony":
        _lead_note(lead, f"🔄 Stracony lead się odezwał ({when}) — temat: {m.subject or '(bez tematu)'}")
        notify(owner, "info", f"🔄 {lead.company} się odezwał", "Lead był stracony — sprawdź, czy wraca do gry.", lead)
        return "stracony odezwał się"
    if lead.stage not in ACTIVE_STAGES:
        return "pominięty (wygrany — prowadzi Customer Journey)"
    chase = [a for a in lead_open_tasks(lead.id).all()
             if a.kind in _CHASE_KINDS and (a.created_at or datetime.min) < (m.date or datetime.utcnow())
             and (a.created_by is None or _CHASE_RE.search(a.notes or ""))]
    done = []
    if chase:
        retire_tasks(lead, chase, f"klient odpisał {when}")
        done.append(f"nieaktualne zadania: {len(chase)}")
    res = res if res is not None else _classify_client_mail(m, lead)
    if not res:
        return "; ".join(done) or "bez analizy (brak klucza Claude'a)"
    intent, conf, summary = res.get("intent"), res.get("confidence"), str(res.get("summary") or "")[:500]
    if intent == "akceptacja" and conf == "wysoka":
        ct = lead.client_type or client_type_hint(lead)[0]
        gate = gated_stage_change(lead, "Wygrany", None, {"win_confirmation": "mail", "client_type": ct})
        if gate.get("changed"):
            _lead_note(lead, f"🏆 Wygrany z maila klienta ({when}): {summary}\n(Silnik porządku — jeśli to pomyłka, "
                             "cofnij etap w nagłówku leada.)")
            notify_team_win(lead, None, f"klient potwierdził mailem ({when}): {summary}")  # cały zespół, z opiekunem
            done.append("Wygrany")
        else:
            _lead_note(lead, f"✅ Klient potwierdził zamówienie mailem ({when}): {summary}\nDo oznaczenia jako Wygrany "
                             f"brakuje: {gate.get('message')}")
            notify(owner, "blocked", f"✅ {lead.company} potwierdza zamówienie", f"Uzupełnij i oznacz jako wygrany: "
                                                                                  f"{gate.get('message')}", lead)
            done.append("akceptacja — brakuje danych")
    elif intent == "rezygnacja" and conf in ("wysoka", "średnia"):
        _lead_note(lead, f"⚠️ Klient chyba rezygnuje ({when}): {summary}")
        notify(owner, "lost", f"⚠️ {lead.company} chyba rezygnuje", f"{summary} — oznacz jako Stracony, jeśli tak.", lead)
        done.append("rezygnacja?")
    elif res.get("needs_reply"):
        notify(owner, "reply", f"✉️ {lead.company} czeka na odpowiedź", summary, lead)
        done.append("czeka na odpowiedź")
    return "; ".join(done) or "bez zmian"


def mail_engine_pass(limit=40):
    """Nowe maile od klientów (od ostatniego obiegu) → handle_client_mail. Pierwszy obieg tylko zapamiętuje miejsce —
    historii nie przerabia. Zwraca liczbę przetworzonych maili."""
    last = get_setting(MAIL_ENGINE_KEY)
    top = db.session.query(db.func.max(MailMessage.id)).scalar() or 0
    if last is None or not str(last).isdigit():
        set_setting(MAIL_ENGINE_KEY, str(top))
        db.session.commit()
        return 0
    rows = (MailMessage.query.filter(MailMessage.id > int(last), MailMessage.folder == "in")
            .order_by(MailMessage.id).limit(limit).all())
    own, done = _own_addresses(), 0
    since = datetime.utcnow() - timedelta(hours=MAIL_ENGINE_MAX_AGE_H)
    for m in rows:
        try:
            lead = mail_lead_for(m) if _is_external(m.from_addr, own) and (m.date or since) >= since else None
            if lead is not None and not is_market_copy(lead) and not lead.clickup_pending:
                handle_client_mail(m, lead)
                done += 1
        except Exception:
            db.session.rollback()
            app.logger.exception("mail_engine")
        set_setting(MAIL_ENGINE_KEY, str(m.id))
        db.session.commit()
    if not rows and top > int(last):  # nowe maile tylko w „wysłanych” — przesuń wskaźnik
        set_setting(MAIL_ENGINE_KEY, str(top))
        db.session.commit()
    return done


_MONTHS_IN = ["styczniu", "lutym", "marcu", "kwietniu", "maju", "czerwcu", "lipcu", "sierpniu", "wrześniu", "październiku",
              "listopadzie", "grudniu"]


def win_stats(lead):
    """Liczby do pokazu 🏆 (static/wygrana.js): kto zamknął, która to wygrana rynku w tym miesiącu, suma miesiąca,
    ile wygranych ma ta osoba i czy to rekord miesiąca. Liczą się leady, które nadal są wygrane / zrealizowane."""
    code = lead_market(lead)
    cur = lead.currency or M(code)["currency"]
    last = (StageHistory.query.filter(StageHistory.lead_id == lead.id, StageHistory.to_stage == "Wygrany")
            .order_by(StageHistory.changed_at.desc()).first())
    who_id = last.changed_by if last else None
    now = to_local(datetime.utcnow())
    start = datetime(now.year, now.month, 1) - timedelta(hours=3)  # historia jest w UTC — zapas na strefę czasu
    rows = (db.session.query(StageHistory.lead_id, StageHistory.changed_by)
            .filter(StageHistory.to_stage == "Wygrany", StageHistory.changed_at >= start).all())
    ids = {r[0] for r in rows} | {lead.id}
    won = [l for l in Lead.query.filter(Lead.id.in_(ids), Lead.stage.in_(WON_STAGES)).all() if lead_market(l) == code]
    same = [l for l in won if (l.currency or M(code)["currency"]) == cur]
    value = float(lead.value_pln or 0)
    others = [float(l.value_pln or 0) for l in same if l.id != lead.id]
    mine = {r[0] for r in rows if who_id and r[1] == who_id} & {l.id for l in won}
    return {"id": lead.id, "company": lead.company, "value": value, "currency": cur,
            "who": USERS_BY_ID.get(who_id, {}).get("name", ""), "who_id": who_id,
            "by_mail": who_id is None and lead.win_confirmation == "mail",
            "n": len(won), "month": _MONTHS_IN[now.month - 1], "sum": fmt_pln(sum(float(l.value_pln or 0) for l in same)),
            "mine": len(mine), "record": bool(others) and value > max(others)}


@app.route("/crm/api/leads/<int:lead_id>/wygrana")
def crm_api_win_stats(lead_id):
    lead = db.session.get(Lead, lead_id)
    if lead is None:
        return jsonify({}), 404
    return jsonify(win_stats(lead))


def celebration_for(uid):
    """Dane do pokazu 🏆 dla osoby (najnowsza nieobejrzana wygrana) albo None."""
    n = notice_to_celebrate(uid)
    if n is None:
        return None
    lead = db.session.get(Lead, n.lead_id) if n.lead_id else None
    return {"id": n.id, "lead_id": lead.id if lead else None, "company": lead.company if lead else n.title,
            "value": float(lead.value_pln or 0) if lead else 0,
            "currency": (lead.currency or M(lead_market(lead))["currency"]) if lead else ""}


app.jinja_env.globals.update(notices_for=notices_for, notices_unread=notices_unread, celebration_for=celebration_for)
