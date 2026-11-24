# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("kalendarz") w miejscu, w którym kalendarz był wcześniej w crm.py.
# Temat: KALENDARZ — dwa widoki
#
# - „Zlecenia” (domyślny): same zlecenia — wygrane i zrealizowane leady z dniami wynajmu. Bez telefonów, maili i zadań.
#   Pod kalendarzem lista wygranych z terminem do ustalenia (bez daty).
# - „Wszystko”: jak dawniej — zadania (telefony, maile, spotkania…) i wszystkie terminy wydarzeń, także z ofert.
# Kolory nie są wpisane na sztywno: API daje klasy (ev-order, ev-task ev-overdue…), a kolory biorą się ze zmiennych
# motywu w templates/crm/calendar.html — działają w jasnym i ciemnym motywie.

CAL_VIEWS = ("zlecenia", "wszystko")


def calendar_view(arg):
    return arg if arg in CAL_VIEWS else "zlecenia"


@app.route("/crm/calendar")
def crm_calendar():
    user = get_current_user(request)
    view = calendar_view(request.args.get("widok"))
    undated = []
    if view == "zlecenia":  # wygrane bez dnia wynajmu — „📅 Termin do ustalenia”
        undated = (Lead.query.filter(Lead.stage == "Wygrany", Lead.clickup_pending.isnot(True), ~Lead.event_days.any())
                   .order_by(Lead.created_at.desc()).limit(50).all())
    return render_template("crm/calendar.html", user=user, users=USERS, cal_view=view, undated=undated)


def _cal_range(start, end):
    """Zakres z FullCalendar (ISO z godziną i strefą) → (pierwszy dzień, dzień po ostatnim) albo None."""
    def day(s):
        try:
            return datetime.strptime((s or "")[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return day(start), day(end)


def _order_classes(lead):
    if lead.stage == "Zrealizowany":
        return ["ev-order-done"]
    if lead.stage in WON_STAGES:
        return ["ev-order"] + ([] if lead.contract_signed else ["ev-nocontract"])
    return ["ev-tentative"]  # termin z oferty / nowego leada — jeszcze nie zlecenie


def calendar_events(start=None, end=None, view="wszystko"):
    """Wydarzenia do FullCalendar. view="zlecenia": tylko wygrane / zrealizowane leady z dniami wynajmu."""
    first, after = _cal_range(start, end)
    orders_only = view == "zlecenia"
    events = []
    if not orders_only:
        q = Activity.query.filter(Activity.due_date.isnot(None), Activity.kind != "note")
        if first:
            q = q.filter(Activity.due_date >= first)
        if after:
            q = q.filter(Activity.due_date < after + timedelta(days=1))
        for a in q.all():
            s = serialize_activity(a)
            events.append({
                "id": f"act-{a.id}",
                "title": f"{ACTIVITY_KIND_ICONS.get(a.kind, '•')} {s['lead_company'] or 'Zadanie własne'}",
                "start": s["due_date"],
                "allDay": True,
                "classNames": ["ev-task", f"ev-{s['urgency']}"] + (["done-event"] if a.done else []),
                "extendedProps": {**s, "event_type": "activity"},
            })

    stages = Lead.stage.in_(WON_STAGES) if orders_only else (Lead.stage != "Stracony")
    for lead in Lead.query.filter(Lead.event_date.isnot(None), stages, Lead.clickup_pending.isnot(True)).all():
        ordered = lead.event_days  # już posortowane po dacie (relationship order_by)
        if not ordered:
            continue

        def eff_loc(ed):
            return ed.location or lead.location or ""

        # ciągłe dni w tym samym miejscu = jeden pasek; osobne dni albo zmiana miasta = osobne wydarzenia
        runs = [[ordered[0]]]
        for ed in ordered[1:]:
            prev = runs[-1][-1]
            if (ed.event_date - prev.event_date).days == 1 and eff_loc(ed) == eff_loc(prev):
                runs[-1].append(ed)
            else:
                runs.append([ed])
        classes = _order_classes(lead)
        owner = USERS_BY_ID.get(lead.assigned_to, {}).get("name", "")
        for run in runs:
            run_start, run_end = run[0].event_date, run[-1].event_date
            if first and run_end < first.date():
                continue
            if after and run_start > after.date():
                continue
            days = len(run)
            loc = eff_loc(run[0])
            city = loc.split(",")[0].strip()[:30]
            title = (("📝 " if "ev-nocontract" in classes else "") + (lead.company or "")
                     + (f" · {city}" if city else "") + (f" ({days} dni)" if days > 1 else ""))
            event = {
                "id": f"evt-{lead.id}-{run_start.isoformat()}",
                "title": title,
                "start": run_start.isoformat(),
                "allDay": True,
                "classNames": classes,
                "extendedProps": {
                    "event_type": "lead_event",
                    "lead_id": lead.id,
                    "lead_company": lead.company,
                    "lead_contact": lead.contact_name or "",
                    "contact_phone": lead.contact_phone or "",
                    "robot_model": lead.robot_model or "",
                    "value_pln": lead.value_pln,
                    "currency": lead.currency or M(lead_market(lead))["currency"],
                    "location": loc,
                    "rental_days": days,
                    "stage": lead.stage,
                    "order": lead.stage in WON_STAGES,
                    "contract_signed": bool(lead.contract_signed),
                    "owner": owner,
                },
            }
            if days > 1:
                event["end"] = (run_end + timedelta(days=1)).isoformat()  # FullCalendar: koniec wyłączny
            events.append(event)
    return events


@app.route("/crm/api/activities")
def api_activities():
    return jsonify(calendar_events(request.args.get("start"), request.args.get("end"),
                                   calendar_view(request.args.get("widok")) if request.args.get("widok") else "wszystko"))
