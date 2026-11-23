# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("rynki_filtr") po modelach — działa w przestrzeni nazw crm.py.
# Temat: filtr rynku (kilka rynków w jednym CRM), przełącznik rynku, rynek w szablonach
#
# W serwisie z kilkoma rynkami (CRM LT/DE) zapytania z przeglądarki pokazują tylko leady oglądanego rynku — także
# ich zadania, oferty, umowy, dokumenty i maile (filtr dokładany automatycznie do każdego zapytania ORM). Wątki
# w tle widzą wszystko. Serwis z jednym rynkiem (CRM PL) nie filtruje niczego.
from sqlalchemy import event as _sa_event, select as _sa_select  # noqa: E402
from sqlalchemy.orm import Session as _OrmSession, with_loader_criteria  # noqa: E402

_MARKET_FREE_ENDPOINTS = {"static", "lead_form", "mcp_endpoint", "whatsapp_webhook", "set_ui_lang", "set_market",
                          "crm_login", "crm_logout", "index"}


def _many_markets():
    return len(markets.MARKETS) > 1


def _lead_children():
    return (Activity, LeadEventDate, StageHistory, Crew, Journey, JourneyMail, JourneyDocument, SentOffer,
            ContractRequest, CampaignRecipient)


@_sa_event.listens_for(_OrmSession, "do_orm_execute")
def _market_criteria(state):
    if not state.is_select or state.execution_options.get("all_markets") or not has_request_context():
        return
    code = getattr(g, "market_filter", None)
    if not code:
        return
    opts = [with_loader_criteria(Lead, lambda cls: cls.market == code, include_aliases=True),
            with_loader_criteria(MailMessage, lambda cls: cls.market == code, include_aliases=True),
            with_loader_criteria(Campaign, lambda cls: cls.market == code, include_aliases=True),
            with_loader_criteria(InboxMessage, lambda cls: cls.market == code, include_aliases=True),
            with_loader_criteria(MailTask, lambda cls: cls.market == code, include_aliases=True)]
    for model in _lead_children():
        opts.append(with_loader_criteria(
            model, lambda cls: db.or_(cls.lead_id.is_(None),
                                      cls.lead_id.in_(_sa_select(Lead.id).where(Lead.market == code))),
            include_aliases=True))
    state.statement = state.statement.options(*opts)


def all_markets(query):
    """Zapytanie bez filtra rynku (np. szukanie duplikatów, link do leada z drugiego rynku)."""
    return query.execution_options(all_markets=True)


@app.before_request
def _set_market_filter():
    for key in ("market_force", "market_switched", "market_filter"):  # nic nie przechodzi z poprzedniego zapytania
        g.pop(key, None)
    if not _many_markets():  # jeden rynek — nic do filtrowania
        return None
    if request.endpoint in _MARKET_FREE_ENDPOINTS or request.endpoint is None or request.path.startswith("/f/"):
        return None
    code = current_market()
    lead_id = (request.view_args or {}).get("lead_id")
    if lead_id:  # link do leada z drugiego rynku — przełącz się na jego rynek
        other = all_markets(db.session.query(Lead.market).filter(Lead.id == lead_id)).scalar()
        if other and other != code:
            g.market_force = other
            g.market_switched = other
            code = other
    g.market_filter = code
    return None


@app.after_request
def _remember_switched_market(resp):
    switched = getattr(g, "market_switched", None) if has_request_context() else None
    if switched:
        resp.set_cookie(MARKET_COOKIE, switched, max_age=3600 * 24 * 365, samesite="Lax")
    return resp


@app.route("/crm/rynek/<code>")
def set_market(code):
    resp = redirect(url_for("crm_dashboard") if "/leads/" in (request.referrer or "") else (request.referrer or "/crm/"))
    if code in markets.MARKETS:
        resp.set_cookie(MARKET_COOKIE, code, max_age=3600 * 24 * 365, samesite="Lax")
    return resp


@app.context_processor
def _inject_market():
    code = current_market()
    return {"mkt": M(code), "mkt_code": code, "all_mkts": markets.MARKETS, "crm_brand": M(code)["brand"]}
