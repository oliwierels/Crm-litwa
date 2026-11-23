# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("wyglad") po opiekunie leada — działa w przestrzeni nazw crm.py.
# Temat: drobne pomocniki wyglądu dla szablonów (wspólny base.html dla obu CRM)
#
# - has_route("nazwa_widoku"): czy dany CRM ma tę stronę (menu pokazuje tylko istniejące, np. „Do obdzwonienia” w PL)
# - av_style(id): awatar osoby w jednym stylu w całym CRM


def has_route(endpoint):
    return endpoint in app.view_functions


# ---- statystyki użycia stron: która strona CRM jest naprawdę otwierana (żeby wyrzucać nieużywane na podstawie danych) ----

class PageView(db.Model):
    __tablename__ = "page_views"
    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, nullable=False, index=True)
    endpoint = db.Column(db.String(80), nullable=False)
    user_id = db.Column(db.Integer)
    views = db.Column(db.Integer, default=0, nullable=False)
    __table_args__ = (db.UniqueConstraint("day", "endpoint", "user_id", name="uq_page_views_day_ep_user"),)


_PAGE_VIEW_SKIP = ("static", "lead_form", "crm_login", "crm_logout", "set_user", "set_market", "set_ui_lang")


@app.after_request
def _count_page_view(resp):
    """GET strony HTML CRM z odpowiedzią 200 = +1 dla (dzień, strona, osoba). API, pliki i błędy się nie liczą.
    Licznik nie może nic zepsuć: każdy błąd jest połykany."""
    try:
        ep = request.endpoint or ""
        if (request.method != "GET" or resp.status_code != 200 or not ep.startswith("crm_") or ep in _PAGE_VIEW_SKIP
                or "/api/" in request.path or not (resp.mimetype or "").startswith("text/html")):
            return resp
        if db.session.new or db.session.dirty or db.session.deleted:
            return resp  # widok zostawił niezapisane zmiany — nie zapisujemy ich przy okazji licznika
        uid = get_current_user(request)["id"]
        day = to_local(datetime.utcnow()).date()
        n = db.session.execute(db.update(PageView).where(PageView.day == day, PageView.endpoint == ep, PageView.user_id == uid)
                               .values(views=PageView.views + 1)).rowcount
        if not n:
            db.session.execute(db.insert(PageView).values(day=day, endpoint=ep, user_id=uid, views=1))
        db.session.commit()
    except Exception:
        db.session.rollback()
    return resp


def page_usage(days=30):
    """[(strona, wejść, osób, ostatnio)] z ostatnich `days` dni, od najczęściej otwieranych + strony bez wejść."""
    since = to_local(datetime.utcnow()).date() - timedelta(days=days)
    rows = (db.session.query(PageView.endpoint, db.func.sum(PageView.views), db.func.count(db.distinct(PageView.user_id)),
                             db.func.max(PageView.day))
            .filter(PageView.day >= since).group_by(PageView.endpoint).all())
    seen = {r[0] for r in rows}
    pages = sorted(r for r in app.view_functions if r.startswith("crm_") and r not in _PAGE_VIEW_SKIP
                   and any(rule.endpoint == r and "GET" in (rule.methods or ()) and not rule.arguments and "/api/" not in rule.rule
                           for rule in app.url_map.iter_rules()))
    out = sorted(((ep, int(v or 0), int(u or 0), last) for ep, v, u, last in rows), key=lambda r: -r[1])
    return out + [(ep, 0, 0, None) for ep in pages if ep not in seen]


# ---- leady, które same do nas napisały (formularz na stronie, mail na kontakt@) — lepsze niż reklama z ClickUp ----

_DIRECT_SOURCE = re.compile(r"(?i)^\s*(formularz|formspree|mail|e-?mail|kontakt@|strona|www)")
_FORM_SOURCE = re.compile(r"(?i)^\s*(formularz|formspree|strona|www)")


def is_direct_lead(lead_or_source):
    """Lead z formularza 33bots.pl/.lt/.de albo z maila (źródło „Formularz …”, „Mail kontakt@…”, „Mail”) — nie z ClickUp.
    Przyjmuje leada albo samo źródło (np. z okna nowych leadów)."""
    src = lead_or_source if isinstance(lead_or_source, str) else (getattr(lead_or_source, "source", None) or "")
    return bool(_DIRECT_SOURCE.search(src or "")) and "clickup" not in (src or "").lower()


def direct_label(lead_or_source):
    src = lead_or_source if isinstance(lead_or_source, str) else (getattr(lead_or_source, "source", None) or "")
    return "Formularz" if _FORM_SOURCE.search(src or "") else "Mail"


@app.template_filter("money_parts")
def money_parts(totals):
    """['503 715 PLN', '16 300 EUR'] — kafelki pokazują główną walutę dużo, pozostałe małą linią pod spodem."""
    return fmt_money(totals).split(" + ")


def av_style(uid):
    """Styl kółka z inicjałem osoby (jasne tło w kolorze osoby, mocny tekst)."""
    color = USERS_BY_ID.get(uid, {}).get("color", "#94a3b8")
    return f"background:{color}1f;color:{color};box-shadow:inset 0 0 0 1px {color}40"


@app.route("/crm/api/szukaj")
def crm_api_search():
    """Szukajka Ctrl+K: leady po firmie, osobie, telefonie, mailu i miejscu (najpierw otwarte, potem najnowsze)."""
    q = (request.args.get("q") or "").strip()[:80]
    if len(q) < 2:
        return jsonify({"leads": []})
    like = f"%{q}%"
    digits = re.sub(r"\D", "", q)
    conds = [Lead.company.ilike(like), Lead.contact_name.ilike(like), Lead.contact_email.ilike(like),
             Lead.contact_email2.ilike(like), Lead.location.ilike(like)]
    if len(digits) >= 3:
        conds.append(db.func.replace(Lead.contact_phone, " ", "").ilike(f"%{digits}%"))
    rows = (all_markets(Lead.query.filter(db.or_(*conds), Lead.clickup_pending.isnot(True)))
            .order_by(db.case((Lead.stage.in_(ACTIVE_STAGES), 0), else_=1), Lead.created_at.desc()).limit(8).all())
    out = []
    for l in rows:
        owner = USERS_BY_ID.get(l.assigned_to) or {}
        out.append({"id": l.id, "company": l.company, "stage": l.stage, "color": STAGE_COLORS.get(l.stage, "#888"),
                    "contact": l.contact_name or "", "owner": owner.get("name", ""),
                    "value": f"{fmt_pln(l.value_pln)} {l.currency or ''}".strip() if l.value_pln else ""})
    return jsonify({"leads": out})


app.jinja_env.globals.update(has_route=has_route, av_style=av_style, page_usage=page_usage, is_direct_lead=is_direct_lead,
                             direct_label=direct_label)
