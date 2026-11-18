# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("raporty_dnia") tuż przed db.create_all() — działa w przestrzeni nazw crm.py.
# Temat: zespół — rangi, obowiązkowy raport dnia, zlecone zadania, zestawienia pracy (reguły: team.py)
#
# Jak to działa:
# - ranga osoby (team.ROLES) decyduje, czy ma dostęp do danych, czy musi składać raport dnia i czyje raporty widzi;
# - zadanie utworzone przez inną osobę (Activity.created_by != assigned_to) to zadanie ZLECONE: w raporcie dnia trzeba
#   przy każdym z terminem do dziś zaznaczyć „zrobione” albo „niezrobione” z powodem (powód trafia też do zadania);
# - brak raportu z dnia roboczego blokuje CRM tej osobie (okno, nie przekierowanie), aż go uzupełni — jako spóźniony;
# - liczby dnia (telefony, maile, oferty, leady, etapy) CRM zbiera sam z tego, co osoba zrobiła w CRM;
# - historia: strona Zespół (dni, średnie wg dnia tygodnia), eksport CSV / JSON i narzędzie team_reports w konektorze.
# Nic nie wysyła się do klientów. Raport dnia włącza DAY_REPORTS=1 (domyślnie: CRM PL tak, CRM LT/DE nie).
import team
from flask import has_request_context as _team_in_request


class DayReport(db.Model):
    """Raport dnia jednej osoby (albo zgłoszony dzień wolny) — jeden na osobę i dzień."""
    __tablename__ = "day_reports"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    day = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(10), nullable=False, default="work")  # work | off (dzień wolny)
    off_reason = db.Column(db.String(200))
    summary = db.Column(db.Text)  # co zrobiłem
    blockers = db.Column(db.Text)  # co mnie zablokowało / czego potrzebuję
    plan = db.Column(db.Text)  # plan na jutro
    tasks = db.Column(db.Text)  # JSON: zlecone zadania rozliczone w tym raporcie (zrobione / niezrobione + powód)
    stats = db.Column(db.Text)  # JSON: liczby dnia w chwili złożenia raportu
    comments = db.Column(db.Text)  # JSON: rozmowa pod raportem [{by, name, at, text}]
    seen = db.Column(db.Text)  # JSON: {id osoby: kiedy ostatnio otworzyła raport}
    late = db.Column(db.Boolean, default=False)  # złożony po dniu, którego dotyczy
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("user_id", "day", name="uq_day_report_user_day"),)


TEAM_ROLE_KEY = "team_role:{}"
TEAM_SINCE_KEY = "team_report_from:{}"
TEAM_REPORT_ENDPOINTS = {"crm_day_report", "crm_day_report_day", "crm_day_report_view", "crm_day_report_comment"}
# strony dostępne dla rangi bez danych klientów (Wykonawca): raport, zespół (własna historia), profil, zmiana konta
TEAM_NO_DATA_OK = TEAM_REPORT_ENDPOINTS | {"crm_team", "crm_team_export", "set_user", "crm_profile"}
TEAM_NOT_DONE_PREFIX = "❌ Raport dnia"


def team_reports_on():
    """Raport dnia włączony w tym CRM. Domyślnie tylko w CRM PL — jedna osoba składa jeden raport."""
    default = "0" if hasattr(Lead, "market") else "1"
    return os.environ.get("DAY_REPORTS", default).strip() != "0"


def _team_cache(name, make):
    """Wynik raz na zapytanie (pasek, licznik w menu i blokada pytają o to samo)."""
    if not _team_in_request():
        return make()
    if name not in request.environ:
        request.environ[name] = make()
    return request.environ[name]


def _tj(text, default):
    try:
        value = json.loads(text) if text else None
    except ValueError:
        value = None
    return value if isinstance(value, type(default)) else default


def team_saved_roles():
    return _team_cache("crm.team_roles", lambda: {
        r.key.split(":", 1)[1]: r.value for r in AppSetting.query.filter(AppSetting.key.like("team_role:%")).all()})


def team_role(user):
    return team.role(user, team_saved_roles())


def team_must_report(user):
    return team_reports_on() and team_role(user)["report"]


def team_since(user, create=False):
    """Od kiedy osoba ma obowiązek raportu (pierwszy dzień w CRM z rangą z raportem) — None, gdy jeszcze nie zaczęła."""
    key = TEAM_SINCE_KEY.format(user["id"])
    raw = get_setting(key)
    if not raw and create:
        raw = _local_today().isoformat()
        set_setting(key, raw)
        db.session.commit()
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def team_reports(uid, d0=None, d1=None):
    q = DayReport.query.filter(DayReport.user_id == uid)
    if d0:
        q = q.filter(DayReport.day >= d0)
    if d1:
        q = q.filter(DayReport.day <= d1)
    return {r.day: r for r in q.all()}


# --- co osoba zrobiła w CRM (liczby dnia) ---

def _team_user_boxes(user):
    boxes = {(user.get("email") or "").strip().lower()}
    try:
        boxes |= {b["login"].lower() for b in mail_boxes() if b.get("user_id") == user["id"]}
    except Exception:
        pass
    return {b for b in boxes if b}


def team_events(user, d0, d1):
    """Czynności osoby zapisane w CRM w dniach d0–d1 (czas lokalny): [(chwila, klucz liczby, ile, opis albo None)]."""
    uid = user["id"]
    lo = datetime.combine(d0, datetime.min.time()) - timedelta(days=1)  # baza trzyma UTC — zapas na strefę czasową
    hi = datetime.combine(d1, datetime.min.time()) + timedelta(days=2)
    ev = []

    def add(when_utc, key, amount, text=None):
        when = to_local(when_utc) if when_utc else None
        if when is not None and d0 <= when.date() <= d1:
            ev.append((when, key, amount, text))

    def where(company, lead_id=True):
        return company or ("lead" if lead_id else "zadanie własne")

    acts = all_markets(db.session.query(Activity.done_at, Activity.kind, Activity.outcome, Activity.lead_id,
                                        Activity.created_by, Lead.company)
                       .outerjoin(Lead, Lead.id == Activity.lead_id)
                       .filter(Activity.assigned_to == uid, Activity.done.is_(True), Activity.done_at >= lo,
                               Activity.done_at < hi)).all()
    for done_at, kind, outcome, lead_id, created_by, company in acts:
        if kind == "note":
            if created_by == uid:
                add(done_at, "notes", 1, f"📝 Komentarz — {where(company, lead_id)}")
            continue
        result = f" ({sales_rules.OUTCOMES[outcome].lower()})" if outcome in sales_rules.OUTCOMES else ""
        add(done_at, "tasks_done", 1, f"{ACTIVITY_KIND_ICONS.get(kind, '•')} {ACTIVITY_KIND_LABELS.get(kind, kind)} "
                                      f"odhaczone — {where(company, lead_id)}{result}")
        if kind == "call" and lead_id:
            add(done_at, "calls_fail" if outcome == "nie_odebral" else "calls_ok", 1)
    comments = all_markets(db.session.query(ActivityComment.created_at, ActivityComment.text, Activity.lead_id, Lead.company)
                           .join(Activity, Activity.id == ActivityComment.activity_id)
                           .outerjoin(Lead, Lead.id == Activity.lead_id)
                           .filter(ActivityComment.author_id == uid, ActivityComment.created_at >= lo,
                                   ActivityComment.created_at < hi)).all()
    for at, text, lead_id, company in comments:
        if (text or "").startswith("📵"):
            add(at, "calls_fail", 1, f"📵 Nie odebrał — {where(company, lead_id)}")
        elif not (text or "").startswith(TEAM_NOT_DONE_PREFIX):
            add(at, "notes", 1, f"💬 Komentarz do zadania — {where(company, lead_id)}")
    offers = all_markets(db.session.query(SentOffer.sent_at, SentOffer.to_addr, Lead.company)
                         .outerjoin(Lead, Lead.id == SentOffer.lead_id)
                         .filter(SentOffer.sender_id == uid, SentOffer.sent_at >= lo, SentOffer.sent_at < hi)).all()
    for at, to_addr, company in offers:
        add(at, "offers", 1, f"📄 Oferta wysłana — {company or to_addr or ''}")
    boxes = _team_user_boxes(user)
    if boxes:
        mails = all_markets(db.session.query(MailMessage.date, MailMessage.subject)
                            .filter(MailMessage.folder == "sent", db.func.lower(MailMessage.mailbox).in_(sorted(boxes)),
                                    MailMessage.date >= lo, MailMessage.date < hi)).all()
        for at, subject in mails:
            add(at, "mails_sent", 1, f"✉️ Mail wysłany — „{(subject or '')[:70]}”")
    handled = all_markets(db.session.query(Lead.handled_at, Lead.company)
                          .filter(Lead.handled_by == uid, Lead.handled_at >= lo, Lead.handled_at < hi)).all()
    for at, company in handled:
        add(at, "leads_handled", 1, f"🆕 Nowy lead obsłużony — {company}")
    stages = all_markets(db.session.query(StageHistory.changed_at, StageHistory.to_stage, Lead.company, Lead.value_pln,
                                          Lead.currency)
                         .join(Lead, Lead.id == StageHistory.lead_id)
                         .filter(StageHistory.changed_by == uid, StageHistory.changed_at >= lo,
                                 StageHistory.changed_at < hi,
                                 StageHistory.to_stage.in_(("Oferta", "Wygrany", "Stracony")))).all()
    for at, stage, company, value, currency in stages:
        if stage == "Oferta":
            add(at, "stage_offer", 1, f"➡️ Etap Oferta — {company}")
        elif stage == "Wygrany":
            add(at, "wins", 1, f"🏆 Wygrany — {company}")
            add(at, "wins_pln", to_pln(value, currency))
        else:
            add(at, "lost", 1, f"✖️ Stracony — {company}")
    return sorted(ev, key=lambda e: e[0])


def team_work_days(user, d0, d1):
    """{dzień: liczby dnia} — to, co osoba zrobiła w CRM (team.METRICS)."""
    return team.fold_events([(when, key, n) for when, key, n, _text in team_events(user, d0, d1)])


# --- zadania zlecone przez inną osobę ---

def _team_task_row(a, state):
    needs = bool(a.lead_id and a.kind in sales_rules.OUTCOME_KINDS and sales_rules.gates_enabled(os.environ))
    allowed = ("odebral", "nie_odebral") if a.kind == "call" else ("odpisal", "bez_odpowiedzi")
    lead = all_markets(Lead.query.filter(Lead.id == a.lead_id)).first() if a.lead_id else None
    return {"id": a.id, "what": " ".join((a.notes or "").split())[:300] or ACTIVITY_KIND_LABELS.get(a.kind, a.kind),
            "kind": a.kind, "icon": ACTIVITY_KIND_ICONS.get(a.kind, "•"),
            "lead": lead.company if lead is not None else "", "lead_id": a.lead_id,
            "by": USERS_BY_ID.get(a.created_by, {}).get("name", "?"), "by_id": a.created_by,
            "to": USERS_BY_ID.get(a.assigned_to, {}).get("name", "?"), "to_id": a.assigned_to,
            "due": a.due_date.date().isoformat() if a.due_date else None, "state": state,
            "needs_outcome": needs, "outcomes": {o: sales_rules.OUTCOMES[o] for o in allowed} if needs else {}}


def team_ordered_tasks(uid, day):
    """Zadania zlecone osobie przez kogoś innego: (do rozliczenia w raporcie z `day`, pozostałe otwarte — przyszłe
    i bez terminu). Stan: open (trzeba zaznaczyć), done (odhaczone tego dnia), done_later (odhaczone po tym dniu)."""
    start = datetime.combine(day, datetime.min.time()) - timedelta(days=1)
    q = all_markets(Activity.query.filter(Activity.assigned_to == uid, Activity.kind != "note",
                                          Activity.created_by.isnot(None), Activity.created_by != uid,
                                          db.or_(Activity.done.isnot(True), Activity.done_at >= start)))
    due_list, later = [], []
    for a in q.order_by(Activity.due_date, Activity.id).all():
        due = a.due_date.date() if a.due_date else None
        if a.done:
            done_day = to_local(a.done_at).date() if a.done_at else None
            if done_day == day:
                due_list.append(_team_task_row(a, "done"))
            elif done_day and done_day > day and due and due <= day:
                due_list.append(_team_task_row(a, "done_later"))
        elif due is not None and due <= day:
            due_list.append(_team_task_row(a, "open"))
        else:
            later.append(_team_task_row(a, "later"))
    return due_list, later


def team_ordered_by(uid, days=14):
    """Zadania, które osoba zleciła innym: otwarte (z ostatnim powodem „niezrobione”) i zrobione z ostatnich dni."""
    base = all_markets(Activity.query.filter(Activity.created_by == uid, Activity.assigned_to != uid,
                                             Activity.kind != "note"))
    today = _local_today()
    rows = []
    for a in base.filter(Activity.done.isnot(True)).order_by(Activity.due_date, Activity.id).limit(100).all():
        row = _team_task_row(a, "open")
        due = a.due_date.date() if a.due_date else None
        row["overdue"] = (today - due).days if due and due < today else 0
        row["reason"] = next((c.text for c in reversed(a.comments) if (c.text or "").startswith(TEAM_NOT_DONE_PREFIX)), "")
        rows.append(row)
    since = datetime.utcnow() - timedelta(days=days)
    for a in base.filter(Activity.done.is_(True), Activity.done_at >= since).order_by(Activity.done_at.desc()).limit(40).all():
        row = _team_task_row(a, "done")
        row["done_at"] = f"{to_local(a.done_at):%d.%m %H:%M}" if a.done_at else ""
        rows.append(row)
    return rows


# --- blokada i liczniki ---

def _team_day_label(d):
    return f"{team.WEEKDAYS_SHORT[d.weekday()]} {d:%d.%m}"


def team_gate():
    """Dla szablonu: czego brakuje zalogowanej osobie — brakujące raporty z poprzednich dni (blokują CRM) i dzisiejszy
    raport (przypomnienie od team.REMIND_HOUR). None, gdy osoba nie ma obowiązku raportu."""
    def make():
        if not team_reports_on():
            return None
        user = get_current_user(request)
        if not team_role(user)["report"]:
            return None
        today = _local_today()
        since = team_since(user, create=True)
        filed = set(team_reports(user["id"], today - timedelta(days=60)))
        missing = team.missing_days(filed, since, today)
        today_due = team.is_required_day(today) and today not in filed and since is not None and since <= today
        on_report = request.endpoint in TEAM_REPORT_ENDPOINTS
        return {"missing": [{"iso": d.isoformat(), "label": _team_day_label(d)} for d in missing],
                "today_due": today_due, "block": bool(missing) and not on_report,
                "remind": (today_due and not missing and not on_report
                           and to_local(datetime.utcnow()).hour >= team.REMIND_HOUR)}
    try:
        return _team_cache("crm.team_gate", make)
    except Exception:
        db.session.rollback()
        app.logger.exception("team_gate")
        return None


def _team_last_touch(r):
    """(kiedy, kto) ostatnio zmienił raport: ostatni komentarz albo złożenie / poprawka przez autora."""
    comments = _tj(r.comments, [])
    if comments:
        return str(comments[-1].get("at") or ""), comments[-1].get("by")
    when = r.updated_at or r.created_at
    return (when.isoformat(timespec="seconds") if when else ""), r.user_id


def team_badges():
    """Liczniki w menu: todo — brakujące raporty i zlecone zadania z terminem do dziś; unread — raporty i komentarze,
    których zalogowana osoba jeszcze nie widziała (ostatnie 30 dni)."""
    def make():
        user = get_current_user(request)
        me, role = user["id"], team_role(user)
        out = {"todo": 0, "unread": 0, "on": team_reports_on(), "data": role["data"]}
        gate = team_gate()
        if gate:
            out["todo"] += len(gate["missing"]) + (1 if gate["today_due"] else 0)
        today = _local_today()
        out["todo"] += all_markets(Activity.query.filter(
            Activity.assigned_to == me, Activity.kind != "note", Activity.done.isnot(True),
            Activity.created_by.isnot(None), Activity.created_by != me,
            Activity.due_date < datetime.combine(today + timedelta(days=1), datetime.min.time()))).count()
        for r in DayReport.query.filter(DayReport.day >= today - timedelta(days=30)).all():
            target = USERS_BY_ID.get(r.user_id)
            if target is None or not team.can_view(role["key"], me, team_role(target)["key"], r.user_id):
                continue
            touched, by = _team_last_touch(r)
            if by != me and str(_tj(r.seen, {}).get(str(me)) or "") < touched:
                out["unread"] += 1
        return out
    try:
        return _team_cache("crm.team_badges", make)
    except Exception:
        db.session.rollback()
        app.logger.exception("team_badges")
        return {"todo": 0, "unread": 0, "on": False, "data": True}


app.jinja_env.globals.update(team_gate=team_gate, team_badges=team_badges)


@app.before_request
def _team_data_gate():
    """Ranga bez danych klientów (Wykonawca) widzi tylko raport, swoje zlecone zadania i profil."""
    ep = request.endpoint
    if ep is None or ep in PUBLIC_ENDPOINTS or ep in TEAM_NO_DATA_OK:
        return None
    try:
        if team_role(get_current_user(request))["data"]:
            return None
    except Exception:
        db.session.rollback()
        return None
    if (request.headers.get("X-Requested-With") == "fetch" or request.is_json or request.path.startswith("/crm/api/")
            or request.method != "GET"):
        return jsonify({"error": "Twoja ranga nie ma dostępu do danych klientów"}), 403
    return redirect(url_for("crm_day_report"))


# --- raport dnia: formularz, zapis, podgląd, rozmowa ---

def _team_save_report(user, d, report, form):
    """Zapisuje raport dnia `d` osoby. Zwraca listę błędów (pusta = zapisane)."""
    uid, today, now = user["id"], _local_today(), datetime.utcnow()
    errors, snapshot, off_text = [], [], None
    status = "off" if form.get("status") == "off" else "work"
    summary, blockers, plan = (form.get(k, "").strip()[:6000] for k in ("summary", "blockers", "plan"))
    if status == "off":
        reason, note = form.get("off_reason", ""), form.get("off_note", "").strip()[:150]
        if reason not in team.OFF_REASONS:
            errors.append("Wybierz powód dnia wolnego.")
        elif reason == "inne" and not note:
            errors.append("Dzień wolny „inne” — dopisz krótko, jaki to powód.")
        off_text = team.OFF_REASONS.get(reason, "") + (f" — {note}" if note else "")
    else:
        if len(summary) < team.MIN_SUMMARY:
            errors.append(f"Napisz, co zrobiłeś — co najmniej {team.MIN_SUMMARY} znaków.")
        for t in team_ordered_tasks(uid, d)[0]:
            entry = {k: t[k] for k in ("id", "what", "kind", "lead", "lead_id", "by", "by_id", "due")}
            name = f"Zadanie „{t['what'][:60]}”"
            if t["state"] != "open":
                entry["status"] = t["state"]
            else:
                choice = form.get(f"task_{t['id']}")
                reason = form.get(f"reason_{t['id']}", "").strip()[:500]
                if choice == "done":
                    a = all_markets(Activity.query.filter(Activity.id == t["id"])).first()
                    err = complete_activity(a, uid, form.get(f"outcome_{t['id']}")) if a is not None else None
                    if err:
                        errors.append(f"{name}: {err['error']}"
                                      + (" — ten telefon odhacz w leadzie (4 pytania)." if err.get("questions") else ""))
                    entry["status"], entry["note"] = "done", reason
                elif choice == "not_done":
                    if len(reason) < team.MIN_REASON:
                        errors.append(f"{name}: napisz, dlaczego niezrobione.")
                    entry["status"], entry["reason"] = "not_done", reason
                else:
                    errors.append(f"{name}: zaznacz „zrobione” albo „niezrobione”.")
            snapshot.append(entry)
    if errors:
        db.session.rollback()
        return errors
    for e in snapshot:  # powód niezrobienia widać też przy zadaniu (dla osoby, która je zleciła)
        if e.get("status") == "not_done":
            text = f"{TEAM_NOT_DONE_PREFIX} {d:%d.%m}: niezrobione — {e['reason']}"
            if not ActivityComment.query.filter_by(activity_id=e["id"], author_id=uid, text=text).first():
                db.session.add(ActivityComment(activity_id=e["id"], author_id=uid, text=text))
    db.session.flush()
    stats = team_work_days(user, d, d).get(d) or team.empty_metrics()
    if report is None:
        report = DayReport(user_id=uid, day=d, created_at=now, late=d < today)
        db.session.add(report)
    report.status, report.off_reason = status, off_text
    report.summary, report.blockers, report.plan = summary, blockers, plan
    report.tasks, report.stats = json.dumps(snapshot, ensure_ascii=False), json.dumps(stats, ensure_ascii=False)
    report.updated_at = now
    db.session.commit()
    return []


def _team_render_report(viewer, owner, d, report, editable, errors=(), form=None):
    today = _local_today()
    mine = viewer["id"] == owner["id"]
    edit = mine and editable and (report is None or bool(errors) or request.args.get("edytuj") == "1")
    due_list, later = team_ordered_tasks(owner["id"], d) if edit else ([], [])
    events = team_events(owner, d, d)
    stats = team.fold_events([(w, k, n) for w, k, n, _t in events]).get(d) or team.empty_metrics()
    if report is not None and not edit:
        seen = _tj(report.seen, {})
        seen[str(viewer["id"])] = datetime.utcnow().isoformat(timespec="seconds")
        report.seen = json.dumps(seen)
        db.session.commit()
    own_open = all_markets(Activity.query.filter(
        Activity.assigned_to == owner["id"], Activity.kind != "note", Activity.done.isnot(True),
        db.or_(Activity.created_by.is_(None), Activity.created_by == owner["id"]),
        Activity.due_date < datetime.combine(d + timedelta(days=1), datetime.min.time()))).count()
    return render_template(
        "crm/raport_dnia.html", owner=owner, mine=mine, day=d, today=today, report=report, edit=edit,
        editable=mine and editable, errors=list(errors), form=form or {}, due_list=due_list, later=later,
        snapshot=_tj(report.tasks, []) if report is not None else [], stats=stats, own_open=own_open,
        timeline=[(f"{w:%H:%M}", text) for w, _k, _n, text in events if text][:60],
        comments=_tj(report.comments, []) if report is not None else [], metrics=team.METRICS, team=team,
        required=team_must_report(owner) and team.is_required_day(d), day_label=_team_day_label(d),
        role=team_role(owner), saved=request.args.get("ok") == "1", gate=team_gate() if mine else None,
        users_by_id=USERS_BY_ID)


@app.route("/crm/raport")
def crm_day_report():
    """Mój raport dnia: najpierw najstarszy brakujący, inaczej dzisiejszy."""
    if not team_reports_on():
        return redirect(url_for("crm_team"))
    user, today = get_current_user(request), _local_today()
    target = today
    if team_must_report(user):
        filed = set(team_reports(user["id"], today - timedelta(days=60)))
        missing = team.missing_days(filed, team_since(user, create=True), today)
        if missing:
            target = missing[0]
    return redirect(url_for("crm_day_report_day", day=target.isoformat()))


@app.route("/crm/raport/<day>", methods=["GET", "POST"])
def crm_day_report_day(day):
    if not team_reports_on():
        return redirect(url_for("crm_team"))
    user, today = get_current_user(request), _local_today()
    try:
        d = date.fromisoformat(day)
    except ValueError:
        abort(404)
    if d > today or d < today - timedelta(days=370):
        abort(404)
    report = DayReport.query.filter_by(user_id=user["id"], day=d).first()
    editable = report is None or d == today  # dzisiejszy można poprawiać do końca dnia; starszy jest zamknięty
    errors = []
    if request.method == "POST":
        if not editable:
            abort(403)
        errors = _team_save_report(user, d, report, request.form)
        if not errors:
            return redirect(url_for("crm_day_report_day", day=day, ok=1))
        report = DayReport.query.filter_by(user_id=user["id"], day=d).first()
    return _team_render_report(user, user, d, report, editable, errors, request.form if errors else None)


def _team_report_for(rid):
    """Raport i jego autor, jeśli zalogowana osoba może go zobaczyć (inaczej 403 / 404)."""
    r = db.session.get(DayReport, rid) or abort(404)
    viewer, owner = get_current_user(request), USERS_BY_ID.get(r.user_id) or abort(404)
    if not team.can_view(team_role(viewer)["key"], viewer["id"], team_role(owner)["key"], owner["id"]):
        abort(403)
    return r, viewer, owner


@app.route("/crm/raporty/<int:rid>")
def crm_day_report_view(rid):
    r, viewer, owner = _team_report_for(rid)
    if viewer["id"] == owner["id"]:
        return redirect(url_for("crm_day_report_day", day=r.day.isoformat()))
    return _team_render_report(viewer, owner, r.day, r, False)


@app.route("/crm/raporty/<int:rid>/komentarz", methods=["POST"])
def crm_day_report_comment(rid):
    r, viewer, owner = _team_report_for(rid)
    text = request.form.get("text", "").strip()[:2000]
    if text:
        now = datetime.utcnow().isoformat(timespec="seconds")
        comments = _tj(r.comments, [])
        comments.append({"by": viewer["id"], "name": viewer["name"], "at": now, "text": text})
        seen = _tj(r.seen, {})
        seen[str(viewer["id"])] = now
        r.comments, r.seen = json.dumps(comments, ensure_ascii=False), json.dumps(seen)
        db.session.commit()
    return redirect(url_for("crm_day_report_view", rid=rid) + "#rozmowa")


# --- zespół: historia, zestawienia, zlecone zadania, rangi, eksport ---

def team_person_report(person, days):
    """Dni osoby z ostatnich `days` dni (od najnowszego): stan raportu, liczby z CRM, rozliczone zlecone zadania
    + podsumowanie okresu i średnie wg dnia tygodnia."""
    today = _local_today()
    d0 = today - timedelta(days=days - 1)
    reports = team_reports(person["id"], d0, today)
    since = team_since(person) if team_must_report(person) else None
    metrics = team_work_days(person, d0, today)
    rows, d = [], today
    while d >= d0:
        r = reports.get(d)
        state = team.day_state(d, {"status": r.status, "late": r.late} if r else None, today, since) \
            if (since or r) else "free"
        m = metrics.get(d) or team.empty_metrics()
        snapshot = _tj(r.tasks, []) if r else []
        active = any(m.get(k) for k in team.METRIC_KEYS)
        if state != "free" or active:
            rows.append({"day": d, "weekday": team.WEEKDAYS_SHORT[d.weekday()], "state": state, "report": r,
                         "metrics": m, "active": active,
                         "ordered_done": sum(1 for t in snapshot if t.get("status") in ("done", "done_later")),
                         "ordered_open": sum(1 for t in snapshot if t.get("status") == "not_done"),
                         "not_done": [t for t in snapshot if t.get("status") == "not_done"]})
        d -= timedelta(days=1)
    return {"rows": rows, "summary": team.summarize(rows), "since": since, "from": d0, "to": today}


def team_export(person, days, text_limit=None):
    """Dane do analizy (eksport JSON i konektor): dni chronologicznie, liczby, raporty, podsumowanie, dni tygodnia."""
    data = team_person_report(person, days)
    cut = (lambda s: (s or "")[:text_limit]) if text_limit else (lambda s: s or "")
    out = []
    for row in reversed(data["rows"]):
        r, m = row["report"], row["metrics"]
        out.append({
            "date": row["day"].isoformat(), "weekday": team.WEEKDAYS[row["day"].weekday()], "state": row["state"],
            "state_label": team.STATES[row["state"]],
            "submitted_at_local": f"{to_local(r.created_at):%Y-%m-%d %H:%M}" if r is not None and r.created_at else None,
            "metrics": dict({k: m.get(k) or 0 for k in team.METRIC_KEYS}, active_from=m.get("active_from") or "",
                            active_to=m.get("active_to") or ""),
            "ordered_tasks_done": row["ordered_done"],
            "ordered_tasks_not_done": [{"what": cut(t.get("what")), "ordered_by": t.get("by"), "reason": cut(t.get("reason"))}
                                       for t in row["not_done"]],
            "summary": cut(r.summary) if r is not None else "", "blockers": cut(r.blockers) if r is not None else "",
            "plan": cut(r.plan) if r is not None else "",
            "off_reason": (r.off_reason or "") if r is not None and r.status == "off" else "",
            "comments": len(_tj(r.comments, [])) if r is not None else 0})
    role = team_role(person)
    return {"person": person.get("full_name") or person["name"], "role": role["name"],
            "report_required": team_must_report(person),
            "report_required_since": data["since"].isoformat() if data["since"] else None,
            "period": {"from": data["from"].isoformat(), "to": data["to"].isoformat(), "days": days},
            "metric_names": dict(team.METRICS), "state_names": team.STATES,
            "total": data["summary"]["total"], "by_weekday": data["summary"]["by_weekday"], "days": out}


def _team_visible(viewer):
    key = team_role(viewer)["key"]
    return [u for u in USERS if team.can_view(key, viewer["id"], team_role(u)["key"], u["id"])]


def _team_pick_person(viewer):
    visible = _team_visible(viewer)
    pid = request.args.get("osoba", type=int)
    return (next((u for u in visible if u["id"] == pid), None)
            or next((u for u in visible if u["id"] != viewer["id"] and team_must_report(u)), None) or viewer), visible


def _team_pick_days():
    days = request.args.get("dni", type=int)
    return days if days in (30, 90, 180, 365) else 30


@app.route("/crm/zespol")
def crm_team():
    viewer = get_current_user(request)
    role = team_role(viewer)
    person, visible = _team_pick_person(viewer)
    days = _team_pick_days()
    data = team_person_report(person, days)
    return render_template(
        "crm/zespol.html", viewer=viewer, role=role, person=person, visible=visible, days=days, data=data,
        person_role=team_role(person), person_must=team_must_report(person), team=team, metrics=team.METRICS,
        ordered=team_ordered_by(viewer["id"]) if role["data"] else [], mine=team_ordered_tasks(viewer["id"], _local_today()),
        roles=[{"user": u, "role": team_role(u), "since": team_since(u)} for u in USERS], reports_on=team_reports_on(),
        can_waive=role["rank"] > team_role(person)["rank"], msg=request.args.get("msg", ""),
        assignable=[u for u in USERS if team.can_assign(role["key"], viewer["id"], u["id"])], today=_local_today())


@app.route("/crm/zespol/rola", methods=["POST"])
def crm_team_role():
    viewer = get_current_user(request)
    if not team_role(viewer)["manage"]:
        abort(403)
    uid, key = request.form.get("user_id", type=int), request.form.get("role", "")
    if uid not in USERS_BY_ID or key not in team.ROLES:
        abort(400)
    others = [u for u in USERS if u["id"] != uid and team_role(u)["manage"]]
    if not team.ROLES[key]["manage"] and not others:
        return redirect(url_for("crm_team", msg="Ktoś musi zostać w Zarządzie — najpierw nadaj tę rangę innej osobie.") + "#rangi")
    set_setting(TEAM_ROLE_KEY.format(uid), key)
    db.session.commit()
    return redirect(url_for("crm_team", msg=f"{USERS_BY_ID[uid]['name']}: ranga {team.ROLES[key]['name']}.") + "#rangi")


@app.route("/crm/zespol/zwolnij", methods=["POST"])
def crm_team_waive():
    """Osoba z wyższą rangą zwalnia kogoś z raportu za dany dzień (np. cały dzień na realizacji)."""
    viewer = get_current_user(request)
    person = USERS_BY_ID.get(request.form.get("user_id", type=int)) or abort(404)
    try:
        d = date.fromisoformat(request.form.get("day", ""))
    except ValueError:
        abort(400)
    if team_role(viewer)["rank"] <= team_role(person)["rank"] or d > _local_today():
        abort(403)
    if not DayReport.query.filter_by(user_id=person["id"], day=d).first():
        db.session.add(DayReport(user_id=person["id"], day=d, status="off", late=False,
                                 off_reason=f"zwolniony z raportu przez: {viewer['name']}"[:200]))
        db.session.commit()
    return redirect(url_for("crm_team", osoba=person["id"], dni=_team_pick_days()))


@app.route("/crm/zespol/eksport.<fmt>")
def crm_team_export(fmt):
    """Historia pracy osoby do analizy: CSV (Excel) albo JSON (np. do wklejenia w Claude'a)."""
    viewer = get_current_user(request)
    person, _visible = _team_pick_person(viewer)
    days = _team_pick_days()
    data = team_export(person, days)
    name = f"raporty_{person['name']}_{data['period']['from']}_{data['period']['to']}"
    if fmt == "json":
        resp = make_response(json.dumps(data, ensure_ascii=False, indent=1))
        resp.headers["Content-Type"] = "application/json; charset=utf-8"
    elif fmt == "csv":
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Data", "Dzień tygodnia", "Stan raportu", "Raport złożony", *[label for _k, label in team.METRICS],
                    "Aktywność od", "Aktywność do", "Zlecone zrobione", "Zlecone niezrobione", "Powody niezrobionych",
                    "Co zrobiłem", "Co mnie zablokowało", "Plan na jutro", "Dzień wolny"])
        for day in data["days"]:
            m = day["metrics"]
            w.writerow([day["date"], day["weekday"], day["state_label"], day["submitted_at_local"] or "",
                        *[str(m[k]).replace(".", ",") for k in team.METRIC_KEYS], m["active_from"], m["active_to"],
                        day["ordered_tasks_done"], len(day["ordered_tasks_not_done"]),
                        " | ".join(f"{t['what']} — {t['reason']}" for t in day["ordered_tasks_not_done"]),
                        day["summary"], day["blockers"], day["plan"], day["off_reason"]])
        resp = make_response("﻿" + buf.getvalue())
        resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    else:
        abort(404)
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}.{fmt}"'
    return resp


def team_tool(inp, user):
    """Narzędzie team_reports (czat / konektor): historia pracy osób, których raporty widzi pytający."""
    days = max(7, min(int(inp.get("days") or 60), 370))
    visible = _team_visible(user)
    want = str(inp.get("person") or "").strip().lower()
    if want:
        people = [u for u in visible if want in (u["name"].lower(), (u.get("full_name") or "").lower())
                  or want in (u.get("full_name") or "").lower().split()]
        if not people:
            return {"error": "nie ma takiej osoby albo nie masz dostępu do jej raportów",
                    "available": [u["name"] for u in visible]}
    else:
        people = [u for u in visible if team_must_report(u)] or [user]
    limit = None if days <= 45 else 400 if days <= 120 else 200
    return {"reports_enabled_in_this_crm": team_reports_on(), "people": [team_export(u, days, limit) for u in people],
            "note": "Liczby dnia (metrics) CRM zbiera sam z czynności zapisanych w CRM; active_h to czas od pierwszej do "
                    "ostatniej czynności dnia. by_weekday = średnie z dni przepracowanych wg dnia tygodnia."}
