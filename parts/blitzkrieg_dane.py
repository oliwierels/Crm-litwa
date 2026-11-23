# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("blitzkrieg_dane") po kodzie Blitzkriega — działa w przestrzeni nazw crm.py.
# Temat: BLITZKRIEG PO DANYCH — Claude szuka leadów nie tylko po cechach (kobiety, duże firmy, fundacje…), ale po
# tym, co CRM wie o leadzie: kiedy wpadł, czy ktoś go obsłużył, czy była wycena / oferta, kiedy, czy klient odpisał
# po ofercie, od ilu dni cisza, opiekun, źródło (poczta / ClickUp), termin wydarzenia, otwarte zadania.
#
# 1) Claude zamienia opis grupy na FILTR DANYCH (narzędzie filtr_leadow) — sam filtr liczy Python, dokładnie
#    (daty, dni, „bez odpowiedzi po ofercie”), bez zgadywania przez model.
# 2) Jeśli w opisie są też cechy jakościowe (płeć, typ klienta, branża, miasto…), Claude wybiera z PRZEFILTROWANYCH
#    leadów, widząc przy każdym fakty z CRM.
# 3) Follow-upy: kampania „osobny follow-up dla każdego” — Claude pisze każdemu klientowi własną wiadomość
#    (oferta, cena, termin, ostatnie maile), w języku klienta, jako odpowiedź w wątku naszego ostatniego maila.
#    To szkic: można poprawić każdą treść, a nic nie wychodzi bez „🚀 Blitzkrieg!” (i dalej powoli, X na godzinę).

BK_MAIL_DAYS = 400          # ile dni poczty bierzemy pod uwagę przy „kiedy klient pisał / kiedy pisaliśmy”
BK_MAX_LINES = 700          # ile leadów najwyżej pokazujemy Claude'owi w kroku jakościowym
BK_FOLLOWUP_MODEL = os.environ.get("BLITZ_FOLLOWUP_MODEL", "").strip() or OFFER_AI_MODEL


def _bk_days(dt, today=None):
    if not dt:
        return None
    today = today or to_local(datetime.utcnow()).date()
    return (today - to_local(dt).date()).days


def bk_facts(leads):
    """Fakty z CRM dla listy leadów: {lead_id: dict}. Kilka zapytań zbiorczych (nie po jednym na leada)."""
    ids = [l.id for l in leads]
    if not ids:
        return {}
    today = to_local(datetime.utcnow()).date()
    sent_offer, any_offer = {}, set()
    for lid, sent_at, price in (db.session.query(SentOffer.lead_id, SentOffer.sent_at, SentOffer.price)
                                .filter(SentOffer.lead_id.in_(ids)).all()):
        any_offer.add(lid)
        if sent_at and (lid not in sent_offer or sent_at > sent_offer[lid][0]):
            sent_offer[lid] = (sent_at, price or "")
    offer_stage = dict(db.session.query(StageHistory.lead_id, db.func.max(StageHistory.changed_at))
                       .filter(StageHistory.lead_id.in_(ids), StageHistory.to_stage == "Oferta")
                       .group_by(StageHistory.lead_id).all())
    quoted = {r[0] for r in db.session.query(Activity.lead_id).filter(
        Activity.lead_id.in_(ids), Activity.notes.like("🧮%")).distinct().all()}
    last_act = dict(db.session.query(Activity.lead_id, db.func.max(db.func.coalesce(Activity.done_at, Activity.created_at)))
                    .filter(Activity.lead_id.in_(ids), db.or_(Activity.done.is_(True), Activity.kind == "note"))
                    .group_by(Activity.lead_id).all())
    next_task = dict(db.session.query(Activity.lead_id, db.func.min(Activity.due_date))
                     .filter(Activity.lead_id.in_(ids), Activity.done.isnot(True), Activity.kind != "note")
                     .group_by(Activity.lead_id).all())
    open_tasks = {r[0] for r in db.session.query(Activity.lead_id).filter(
        Activity.lead_id.in_(ids), Activity.done.isnot(True), Activity.kind != "note").distinct().all()}
    contacted = {r[0] for r in _contact_done_ids().all()}
    # poczta: kiedy klient do nas pisał, kiedy my do niego (adresy z leada)
    own = _own_addresses()
    last_in, last_out = {}, {}
    since = datetime.utcnow() - timedelta(days=BK_MAIL_DAYS)
    for folder, d, frm, addrs in (db.session.query(MailMessage.folder, MailMessage.date, MailMessage.from_addr, MailMessage.addrs)
                                  .filter(MailMessage.date >= since).all()):
        if not d:
            continue
        if folder == "in":
            a = (frm or "").strip().lower()
            if a and (a not in last_in or d > last_in[a]):
                last_in[a] = d
        else:
            for a in (addrs or "").split():
                if a not in own and (a not in last_out or d > last_out[a]):
                    last_out[a] = d
    out = {}
    for l in leads:
        emails = [e.lower() for e in l.emails]
        l_in = max([last_in[e] for e in emails if e in last_in], default=None)
        l_out = max([last_out[e] for e in emails if e in last_out], default=None)
        offer_at, price = sent_offer.get(l.id, (None, ""))
        offer_at = offer_at or offer_stage.get(l.id)
        days = [ed.event_date for ed in l.event_days if sane_event_date(ed.event_date)]
        future = [d for d in days if d >= today]
        last_contact = max([t for t in (last_act.get(l.id), l_in, l_out, l.created_at) if t], default=None)
        out[l.id] = {
            "created_days": _bk_days(l.created_at, today),
            "unhandled": l.stage == "Nowy" and l.handled_at is None and l.lt_mail_at is None and l.id not in contacted,
            "offer_days": _bk_days(offer_at, today), "offer_price": price,
            "quoted": l.id in any_offer or l.id in quoted or bool(l.value_pln) or offer_at is not None,
            "client_wrote_days": _bk_days(l_in, today), "we_wrote_days": _bk_days(l_out, today),
            "reply_after_offer": bool(offer_at and l_in and l_in > offer_at),
            "silent_days": _bk_days(last_contact, today),
            "open_task": l.id in open_tasks, "next_task_days": (-_bk_days(next_task[l.id], today)
                                                               if next_task.get(l.id) else None),
            "event_future": bool(future), "event_dates": [f"{d:%d.%m.%Y}" for d in (future or days)[:3]],
            "direct": is_direct_lead(l), "owner": USERS_BY_ID.get(l.assigned_to, {}).get("name", ""),
        }
    return out


def _bk_ago(n):
    return "dziś" if n == 0 else ("wczoraj" if n == 1 else f"{n} dni temu")


def bk_fact_line(lead, f):
    """Jedna linijka o leadzie dla Claude'a: cechy + fakty z CRM (z e-maila tylko domena)."""
    domain = (lead.contact_email or "").strip().rsplit("@", 1)[-1].lower()
    note = re.sub(r"\s+", " ", lead.notes or "").strip()[:180]
    facts = [f"wpadł {_bk_ago(f['created_days'])}" if f["created_days"] is not None else "",
             "NIEOBSŁUŻONY" if f["unhandled"] else "",
             (f"oferta {_bk_ago(f['offer_days'])}" + (f" ({f['offer_price']})" if f["offer_price"] else "")
              + ("; klient odpisał po ofercie" if f["reply_after_offer"] else "; BEZ odpowiedzi po ofercie"))
             if f["offer_days"] is not None else ("wycena bez wysłanej oferty" if f["quoted"] else ""),
             f"klient pisał {_bk_ago(f['client_wrote_days'])}" if f["client_wrote_days"] is not None else "",
             f"cisza od {f['silent_days']} dni" if f["silent_days"] else "",
             "z poczty/formularza" if f["direct"] else "", f"opiekun {f['owner']}" if f["owner"] else "",
             ("wydarzenie " + ", ".join(f["event_dates"])) if f["event_dates"] else "",
             "ma otwarte zadanie" if f["open_task"] else "bez zadania"]
    parts = [f"#{lead.id}", lead.company or "", lead.contact_name or "", "@" + domain, lead.stage,
             f"źródło: {lead.source}" if lead.source else "", lead.location or "",
             f"{lead.value_pln:g} {lead.currency or M(lead_market(lead))['currency']}" if lead.value_pln else "",
             " · ".join(x for x in facts if x), note]
    return " | ".join(p for p in parts if p)


BK_FILTER_TOOL = {
    "name": "filtr_leadow",
    "description": "Zamienia opis grupy odbiorców na filtr danych z CRM. Pola, których opis nie dotyczy — pomiń.",
    "input_schema": {
        "type": "object",
        "properties": {
            "stages": {"type": "array", "items": {"type": "string", "enum": list(STAGES)},
                       "description": "Etapy. Gdy opis mówi o follow-upie, zapomnianych, braku odpowiedzi albo nieobsłużonych "
                                      "— tylko aktywne (Nowy, Oferta), chyba że opis mówi inaczej."},
            "created_within_days": {"type": "integer", "description": "wpadł w ciągu ostatnich N dni (np. „ostatnie 4 dni” = 4)"},
            "created_before_days": {"type": "integer", "description": "wpadł dawniej niż N dni temu"},
            "unhandled": {"type": "boolean", "description": "true = nikt się jeszcze nie odezwał do klienta (nieobsłużony)"},
            "offer_sent": {"type": "boolean", "description": "true = dostał ofertę; false = oferty jeszcze nie dostał"},
            "offer_older_than_days": {"type": "integer", "description": "ostatnia oferta wysłana co najmniej N dni temu"},
            "offer_within_days": {"type": "integer", "description": "ostatnia oferta wysłana w ciągu ostatnich N dni"},
            "no_reply_after_offer": {"type": "boolean", "description": "true = po ostatniej ofercie klient nic nie odpisał"},
            "quoted": {"type": "boolean", "description": "true = była wycena (kalkulator, kwota w leadzie albo oferta)"},
            "silent_days": {"type": "integer", "description": "brak jakiegokolwiek kontaktu (mail, telefon, notatka) od co najmniej N dni"},
            "client_wrote_within_days": {"type": "integer", "description": "klient pisał do nas w ciągu ostatnich N dni"},
            "direct": {"type": "boolean", "description": "true = sam napisał (formularz / mail), false = z reklamy (ClickUp)"},
            "owner": {"type": "string", "description": "imię opiekuna leada"},
            "event_future": {"type": "boolean", "description": "true = ma termin wydarzenia w przyszłości"},
            "has_open_task": {"type": "boolean", "description": "true = ma otwarte zadanie, false = bez zadania"},
            "qualitative": {"type": "string", "description": "cechy, których nie da się sprawdzić danymi (np. kobiety, duże "
                                                             "firmy, fundacje, szkoły, Warszawa, agencje). Pusty, gdy brak."},
            "summary": {"type": "string", "description": "jedno zdanie po polsku: jak rozumiesz prośbę"},
        },
        "required": ["qualitative", "summary"],
    },
}


def bk_apply_filter(leads, facts, flt):
    """Filtr danych (wynik narzędzia filtr_leadow) liczony w Pythonie. Zwraca (leady, opis_warunków)."""
    def num(k):
        v = flt.get(k)
        try:
            return int(v) if v is not None and str(v).strip() != "" else None
        except (TypeError, ValueError):
            return None
    conds = []
    stages = [s for s in (flt.get("stages") or []) if s in STAGES]
    if stages:
        conds.append(("etap: " + ", ".join(stages), lambda l, f: l.stage in stages))
    n = num("created_within_days")
    if n is not None:
        conds.append((f"wpadł w ciągu {n} dni", lambda l, f, n=n: f["created_days"] is not None and f["created_days"] <= n))
    n = num("created_before_days")
    if n is not None:
        conds.append((f"wpadł ponad {n} dni temu", lambda l, f, n=n: f["created_days"] is not None and f["created_days"] > n))
    for key, label_t, label_f, fn in (
            ("unhandled", "nieobsłużony", "obsłużony", lambda f: f["unhandled"]),
            ("offer_sent", "dostał ofertę", "bez oferty", lambda f: f["offer_days"] is not None),
            ("no_reply_after_offer", "bez odpowiedzi po ofercie", "odpisał po ofercie",
             lambda f: f["offer_days"] is not None and not f["reply_after_offer"]),
            ("quoted", "była wycena", "bez wyceny", lambda f: f["quoted"]),
            ("direct", "z poczty/formularza", "z ClickUp", lambda f: f["direct"]),
            ("event_future", "wydarzenie w przyszłości", "bez przyszłego terminu", lambda f: f["event_future"]),
            ("has_open_task", "ma zadanie", "bez zadania", lambda f: f["open_task"])):
        v = flt.get(key)
        if isinstance(v, bool):
            conds.append((label_t if v else label_f, lambda l, f, fn=fn, v=v: bool(fn(f)) == v))
    n = num("offer_older_than_days")
    if n is not None:
        conds.append((f"oferta sprzed ≥{n} dni", lambda l, f, n=n: f["offer_days"] is not None and f["offer_days"] >= n))
    n = num("offer_within_days")
    if n is not None:
        conds.append((f"oferta z ostatnich {n} dni", lambda l, f, n=n: f["offer_days"] is not None and f["offer_days"] <= n))
    n = num("silent_days")
    if n is not None:
        conds.append((f"cisza ≥{n} dni", lambda l, f, n=n: (f["silent_days"] or 0) >= n))
    n = num("client_wrote_within_days")
    if n is not None:
        conds.append((f"klient pisał w ciągu {n} dni",
                      lambda l, f, n=n: f["client_wrote_days"] is not None and f["client_wrote_days"] <= n))
    owner = str(flt.get("owner") or "").strip().lower()
    if owner:
        conds.append((f"opiekun {owner.title()}", lambda l, f, o=owner: f["owner"].lower().startswith(o[:4])))
    picked = [l for l in leads if all(fn(l, facts[l.id]) for _, fn in conds)]
    return picked, " · ".join(c for c, _ in conds)


def pick_leads_by_data(audience, leads):
    """Krok 1: opis → filtr danych (Claude) → dokładne liczenie (Python). Krok 2 (gdy są cechy jakościowe): Claude
    wybiera z przefiltrowanych, widząc fakty z CRM. Zwraca {lead_ids, info}."""
    leads = [l for l in leads if EMAIL_RE.match((l.contact_email or "").strip())]
    if not leads:
        return {"error": "w CRM nie ma leadów z adresem e-mail"}
    today = to_local(datetime.utcnow()).date()
    system = (f"Dziś jest {today:%d.%m.%Y}. CRM firmy 33bots (wynajem humanoidalnego robota Unitree G1 na eventy). "
              "Etapy: Nowy (zapytanie), Oferta (oferta wysłana), Wygrany, Zrealizowany, Stracony. Zamień opis grupy "
              "odbiorców na filtr danych. Warunki liczbowe (dni, daty) wpisz dokładnie. Wszystko, czego nie da się "
              "sprawdzić danymi (płeć, typ klienta, branża, wielkość firmy, miasto), wpisz do „qualitative”.")
    flt = _ai_forced_tool(CAMPAIGN_PICK_MODEL, system, f"Opis grupy odbiorców: {audience}", BK_FILTER_TOOL, max_tokens=800)
    facts = bk_facts(leads)
    picked, conds = bk_apply_filter(leads, facts, flt)
    qual = str(flt.get("qualitative") or "").strip()
    info = [f"🔎 Filtr z danych CRM: {conds or 'bez warunków danych'} → {len(picked)} leadów z e-mailem."]
    if qual and picked:
        system2 = (f"Dziś jest {today:%d.%m.%Y}. Wybierasz odbiorców kampanii mailowej firmy 33bots z listy leadów, "
                   "które już spełniają warunki danych. Wybierz WYŁĄCZNIE te, które pasują do opisu cech. Typ klienta "
                   "oceniaj po nazwie firmy, osobie, domenie e-maila (gmail/wp/onet = zwykle osoba prywatna) i notatce; "
                   "płeć po imieniu osoby kontaktowej. Gdy nie masz pewności — pomiń i napisz o tym w podsumowaniu.")
        lines = "\n".join(bk_fact_line(l, facts[l.id]) for l in picked[:BK_MAX_LINES])
        res = _ai_forced_tool(CAMPAIGN_PICK_MODEL, system2, f"Opis grupy: {audience}\nCechy do sprawdzenia: {qual}\n\n"
                              f"Leady (id | firma | osoba | domena | etap | źródło | miejsce | wartość | fakty z CRM | notatka):\n"
                              f"{lines}", CAMPAIGN_PICK_TOOL, max_tokens=4000)
        valid, ids = {l.id for l in picked}, []
        for x in res.get("lead_ids") or []:
            try:
                i = int(x)
            except (TypeError, ValueError):
                continue
            if i in valid and i not in ids:
                ids.append(i)
        info.append(f"✨ Cechy „{qual}”: {len(ids)}. {str(res.get('summary') or '').strip()}")
        if len(picked) > BK_MAX_LINES:
            info.append(f"(Claude przejrzał pierwsze {BK_MAX_LINES} z {len(picked)} — zawęź opis, żeby objąć wszystkie.)")
    else:
        ids = [l.id for l in picked]
    return {"lead_ids": ids, "info": " ".join(info)[:900], "understood": str(flt.get("summary") or "")[:300]}


# ---- follow-upy: osobna wiadomość dla każdego klienta ----

BK_FOLLOWUP_TOOL = {
    "name": "zapisz_followup",
    "description": "Zapisuje temat i treść follow-upu do jednego klienta.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "Temat (krótki). Gdy jest wątek naszego ostatniego maila — pomiń, "
                                                         "CRM odpowie w tym wątku („Re: …”)."},
            "body": {"type": "string", "description": "Treść z powitaniem i podpisem (bez cytowania starych maili)."},
        },
        "required": ["subject", "body"],
    },
}


def _bk_last_thread(lead):
    """Nasz ostatni mail do klienta (wątek do odpowiedzi) — tylko wysłany przez nas na adres z leada."""
    q = mail_query_for_addrs(lead.emails)
    if q is None:
        return None
    client = [e.lower() for e in lead.emails]
    for m in q.filter(MailMessage.folder == "sent").order_by(MailMessage.date.desc()).limit(10).all():
        if m.message_id and any(a in (m.addrs or "").split() for a in client):
            return m
    return None


def _bk_client_last(lead):
    q = mail_query_for_addrs(lead.emails)
    if q is None:
        return None
    return q.filter(MailMessage.folder == "in").order_by(MailMessage.date.desc()).first()


def bk_sender(c):
    user = USERS_BY_ID.get(c.sender_id, USERS[0])
    ms = globals().get("market_sender")  # CRM LT/DE: nadawca ze skrzynki rynku
    return ms(user, c.market) if ms else user


def draft_followup(lead, sender, instruction=""):
    """Claude pisze follow-up do jednego klienta (dane leada, oferta, maile). Zwraca (temat, treść, wątek|None)."""
    f = bk_facts([lead])[lead.id]
    lang = lead_language(lead) if "lead_language" in globals() else "pl"
    lang_name = MAIL_LANG_NAMES.get(lang, "polski")
    offer = (SentOffer.query.filter(SentOffer.lead_id == lead.id, SentOffer.sent_at.isnot(None))
             .order_by(SentOffer.sent_at.desc()).first())
    thread, client = _bk_last_thread(lead), _bk_client_last(lead)
    ctx = [f"Klient: {lead.company}" + (f", osoba: {lead.contact_name}" if lead.contact_name else ""),
           f"Etap: {lead.stage}; {bk_fact_line(lead, f)}"]
    if offer is not None:
        ctx.append(f"Nasza oferta wysłana {to_local(offer.sent_at):%d.%m.%Y}, cena {offer.price or '—'} netto. "
                   f"Fragment maila z ofertą: {' '.join((offer.body or '').split())[:600]}")
    if thread is not None:
        ctx.append(f"Nasz ostatni mail ({to_local(thread.date):%d.%m.%Y}), temat „{thread.subject}”: "
                   f"{' '.join(poczta.split_quote(thread.body_text or '')[0].split())[:500]}")
    if client is not None:
        ctx.append(f"Ostatni mail klienta ({to_local(client.date):%d.%m.%Y}): "
                   f"{' '.join(poczta.split_quote(client.body_text or '')[0].split())[:500]}")
    system = (
        "Piszesz krótki, osobisty follow-up (60–120 słów) od firmy 33Bots (wynajem humanoidalnego robota Unitree G1 "
        f"z operatorem na eventy) do JEDNEGO klienta. Język: {lang_name}. Nawiąż konkretnie do jego sprawy (oferta, "
        "termin, miejsce), przypomnij się uprzejmie i zakończ jednym prostym pytaniem (np. czy termin aktualny, czy "
        "pomóc coś doprecyzować). Bez nacisku, bez wymyślania faktów, cen ani rabatów — tylko to, co w danych. "
        "Bez cytowania starych maili, bez linii-separatorów. Zacznij od zwykłego powitania („Dzień dobry,” po polsku). "
        f"Podpis: {sender.get('full_name') or sender['name']} / 33Bots / {sender.get('email', '')} / {sender.get('phone', '')}. "
        "Nigdy nie pisz 'Edward Warchocki' — tylko 'Unitree G1'.\n" + HUMAN_TYPO_RULE)
    text = (f"POLECENIE: {instruction.strip() or 'Follow-up: przypomnij się i zapytaj o decyzję.'}\n\n" + "\n".join(ctx))
    res = _ai_forced_tool(BK_FOLLOWUP_MODEL, system, text, BK_FOLLOWUP_TOOL, max_tokens=1200)
    body = human_text(str(res.get("body") or "").strip())
    subject = str(res.get("subject") or "").strip()
    if thread is not None:
        s = thread.subject or subject
        subject = s if re.match(r"(?i)^(re|odp|aw|ats)\s*:", s) else f"Re: {s}"
    return subject[:300], body, thread


def draft_campaign_followups(cid, instruction=""):
    """Zadanie w tle: Claude pisze follow-up każdemu odbiorcy kampanii, który jeszcze nie ma treści."""
    c = db.session.get(Campaign, cid)
    if c is None:
        return {"error": "nie ma takiej kampanii"}
    sender, done, failed = bk_sender(c), 0, []
    for rec in [r for r in c.recipients if r.status == "czeka" and not (r.body or "").strip()]:
        lead = db.session.get(Lead, rec.lead_id) if rec.lead_id else None
        if lead is None:
            continue
        try:
            subject, body, thread = draft_followup(lead, sender, instruction)
            if not body:
                raise ValueError("pusta treść")
            rec.subject, rec.body = subject or c.subject, body
            if thread is not None:
                rec.in_reply_to = thread.message_id
                rec.refs = ((thread.refs or "") + " " + thread.message_id).strip()[-900:]
            done += 1
        except Exception as e:
            failed.append(f"{rec.company}: {type(e).__name__}")
            app.logger.warning("followup %s: %s", rec.id, e)
        db.session.commit()
    info = f"Claude napisał {done} follow-upów."
    if failed:
        info += " Nie udało się: " + ", ".join(failed[:5])
    return {"info": info}


def campaign_message(c, rec):
    """(temat, treść, nagłówki) do wysłania temu odbiorcy albo None (follow-up bez treści — nie wysyłamy nic)."""
    if getattr(c, "personal", False):
        if not (rec.body or "").strip():
            return None
        headers = {}
        if rec.in_reply_to:
            headers = {"In-Reply-To": rec.in_reply_to, "References": rec.refs or rec.in_reply_to}
        return rec.subject or c.subject, rec.body, headers
    return render_campaign_text(c.subject, rec), render_campaign_text(c.body, rec), {}


def campaign_missing_drafts(c):
    return [r for r in c.recipients if r.status == "czeka" and not (r.body or "").strip()] if c.personal else []


@app.route("/crm/blitzkrieg/<int:cid>/followupy", methods=["POST"])
def crm_campaign_followups(cid):
    c = db.session.get(Campaign, cid)
    if c is None or not c.personal:
        abort(404)
    if _ai_client() is None:
        return jsonify({"error": "Brak ANTHROPIC_API_KEY w Railway."}), 400
    jid = _start_job("Claude pisze follow-upy", draft_campaign_followups, c.id, (request.form.get("instruction") or "")[:1000])
    return jsonify({"jid": jid, "poll": url_for("crm_job", jid=jid, format="json")})


@app.route("/crm/blitzkrieg/<int:cid>/odbiorca/<int:rid>", methods=["POST"])
def crm_campaign_recipient_edit(cid, rid):
    """Poprawka treści follow-upu jednego odbiorcy (tylko przed wysyłką)."""
    c, rec = db.session.get(Campaign, cid), db.session.get(CampaignRecipient, rid)
    if c is None or rec is None or rec.campaign_id != c.id:
        abort(404)
    if rec.status == "czeka" and c.status != "trwa":
        rec.subject = (request.form.get("subject") or rec.subject or "").strip()[:300]
        rec.body = (request.form.get("body") or "").replace("\r\n", "\n").strip()
        db.session.commit()
    return redirect(url_for("crm_campaign", cid=c.id) + f"#r{rec.id}")


app.jinja_env.globals.update(campaign_missing_drafts=campaign_missing_drafts)
