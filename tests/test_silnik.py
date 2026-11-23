"""Silnik porządku (parts/silnik_porzadku.py, ten sam plik w CRM PL i LT/DE): mail klienta porządkuje leada
(wygrana z maila, nieaktualne zadania „pogoni”, powiadomienia), zamknięte leady nie mają starych zadań,
a niczego od człowieka nie kasujemy — zadanie zamienia się w komentarz „🧹 Nieaktualne”."""
from datetime import date, datetime, timedelta

import pytest

import crm

CODE = crm.markets.DEFAULT
OLIWIER, BARTEK, JERZY = 1, 2, 3
MADE = []


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


@pytest.fixture(autouse=True)
def defaults(app, monkeypatch):
    monkeypatch.delenv("AUTO_TASKS", raising=False)
    monkeypatch.delenv("STAGE_GATES", raising=False)
    monkeypatch.setattr(crm, "_ai_client", lambda: None)
    crm.Notice.query.delete()
    crm.db.session.commit()
    yield
    crm.db.session.rollback()
    for lead_id in MADE:
        lead = crm.db.session.get(crm.Lead, lead_id)
        if lead is not None:
            for a in crm.lead_open_tasks(lead.id).all():
                a.done = True
            lead.stage = "Stracony"
    MADE.clear()
    crm.Notice.query.delete()
    crm.db.session.commit()


@pytest.fixture
def client(app):
    c = crm.app.test_client()
    c.set_cookie("crm_user", str(BARTEK))
    if len(crm.markets.MARKETS) > 1:
        c.set_cookie("crm_market", CODE)
    return c


def mk(company, stage="Oferta", email=None, **kw):
    lead = crm.Lead(company=company, stage=stage, assigned_to=kw.pop("assigned_to", JERZY), market=CODE,
                    contact_email=email, currency=crm.M(CODE)["currency"], **kw)
    crm.db.session.add(lead)
    crm.db.session.commit()
    MADE.append(lead.id)
    return lead


def task(lead, notes, kind="call", created_by=None, created_at=None):
    a = crm.Activity(lead_id=lead.id, kind=kind, assigned_to=lead.assigned_to, created_by=created_by, notes=notes,
                     due_date=datetime.combine(date.today() + timedelta(days=3), datetime.min.time()))
    if created_at:
        a.created_at = created_at
    crm.db.session.add(a)
    crm.db.session.commit()
    return a


def mail(lead, subject="Re: oferta", hours_ago=0, sender=None):
    sender = sender or lead.contact_email
    m = crm.MailMessage(mailbox="kontakt@33bots.pl", market=CODE, folder="in", from_addr=sender, subject=subject,
                        addrs=f" {sender} kontakt@33bots.pl ", body_text="Dzień dobry, przyjmujemy ofertę.",
                        date=datetime.utcnow() - timedelta(hours=hours_ago))
    crm.db.session.add(m)
    crm.db.session.commit()
    return m


def notices(uid=None, kind=None):
    q = crm.Notice.query
    if uid is not None:
        q = q.filter(crm.Notice.user_id == uid)
    if kind is not None:
        q = q.filter(crm.Notice.kind == kind)
    return q.all()


ACCEPT = {"intent": "akceptacja", "confidence": "wysoka", "needs_reply": True,
          "summary": "Klient przyjmuje ofertę i prosi o umowę."}


def test_retire_task_keeps_human_work_as_history(app):
    lead = mk("Porządek Historia")
    auto = task(lead, "Przypomnienie: follow-up po ofercie")
    human = task(lead, "Zadzwonić i zapytać o decyzję", created_by=OLIWIER)
    assert crm.retire_tasks(lead, [auto, human], "klient odpisał") == 2
    crm.db.session.commit()
    assert crm.db.session.get(crm.Activity, auto.id) is None, "automatyczne przypomnienie znika"
    kept = crm.db.session.get(crm.Activity, human.id)
    assert kept.kind == "note" and kept.done and kept.notes.startswith(crm.CLEAN_PREFIX)
    assert "Zadzwonić i zapytać o decyzję" in kept.notes and "klient odpisał" in kept.notes
    summary = [a for a in crm.Activity.query.filter_by(lead_id=lead.id, kind="note").all() if "zamknięto 2" in a.notes]
    assert summary, "przy leadzie zostaje komentarz, ile zadań i dlaczego"
    assert crm.db.session.get(crm.Lead, lead.id) is not None


def test_auto_task_with_human_comment_is_kept(app):
    lead = mk("Porządek Komentarz")
    auto = task(lead, "Przypomnienie: telefon po ofercie")
    crm.db.session.add(crm.ActivityComment(activity_id=auto.id, author_id=JERZY, text="📵 Nie odebrał, spróbować w piątek"))
    crm.db.session.commit()
    assert crm.retire_task(auto, "lead wygrany") == "note"
    crm.db.session.commit()
    kept = crm.db.session.get(crm.Activity, auto.id)
    assert kept is not None and kept.kind == "note" and [c.text for c in kept.comments] == ["📵 Nie odebrał, spróbować w piątek"]


def test_acceptance_mail_wins_lead_and_notifies_team(app):
    lead = mk("Akceptacja Sp. z o.o.", email="szef@akceptacja-test.pl", value_pln=9000, client_type="firma")
    crm.set_lead_event_days(lead, [date.today() + timedelta(days=40)], {})
    chase = task(lead, "Telefon po ofercie — zapytaj o decyzję", created_by=JERZY,
                 created_at=datetime.utcnow() - timedelta(days=2))
    other = task(lead, "Przygotować scenariusz pokazu", kind="meeting", created_by=JERZY)
    out = crm.handle_client_mail(mail(lead), lead, dict(ACCEPT))
    crm.db.session.commit()
    lead = crm.db.session.get(crm.Lead, lead.id)
    assert lead.stage == "Wygrany" and lead.win_confirmation == "mail", out
    assert crm.db.session.get(crm.Activity, chase.id).kind == "note", "telefon „po ofercie” jest nieaktualny"
    assert crm.lead_open_tasks(lead.id).all() == [], "wygrany lead nie ma zadań z lejka"
    assert crm.db.session.get(crm.Activity, other.id).notes.startswith(crm.CLEAN_PREFIX)
    wins = notices(kind="win")
    assert {n.user_id for n in wins} == {u["id"] for u in crm.USERS}, "cały zespół dostaje powiadomienie o wygranej"
    assert all(n.celebrate for n in wins) and all(n.lead_id == lead.id for n in wins)
    notes = " ".join(a.notes for a in crm.Activity.query.filter_by(lead_id=lead.id, kind="note").all())
    assert "Wygrany z maila" in notes and "cofnij etap" in notes, "decyzja silnika jest opisana przy leadzie"


def test_acceptance_without_event_date_only_notifies_owner(app):
    lead = mk("Bez Daty", email="biuro@bez-daty-test.pl", value_pln=5000, client_type="firma")
    out = crm.handle_client_mail(mail(lead), lead, dict(ACCEPT))
    crm.db.session.commit()
    assert crm.db.session.get(crm.Lead, lead.id).stage == "Oferta", out
    blocked = notices(kind="blocked")
    assert [n.user_id for n in blocked] == [JERZY] and "daty wydarzenia" in blocked[0].body
    assert not notices(kind="win")


def test_question_retires_chase_but_keeps_other_tasks(app):
    lead = mk("Pytanie Test", email="ola@pytanie-test.pl")
    old_chase = task(lead, "Follow-up po ofercie", created_by=OLIWIER, created_at=datetime.utcnow() - timedelta(days=1))
    prep = task(lead, "Wycenić dodatkowy dzień", kind="email", created_by=OLIWIER,
                created_at=datetime.utcnow() - timedelta(days=1))
    res = {"intent": "pytanie", "confidence": "wysoka", "needs_reply": True, "summary": "Pyta o dodatkowy dzień."}
    crm.handle_client_mail(mail(lead), lead, res)
    crm.db.session.commit()
    assert crm.db.session.get(crm.Lead, lead.id).stage == "Oferta"
    assert crm.db.session.get(crm.Activity, old_chase.id).kind == "note"
    assert [a.id for a in crm.lead_open_tasks(lead.id).all()] == [prep.id], "zadanie z treścią spoza „pogoni” zostaje"
    assert [n.user_id for n in notices(kind="reply")] == [JERZY]


def test_chase_task_added_after_mail_stays(app):
    lead = mk("Po Mailu", email="kasia@po-mailu-test.pl")
    m = mail(lead, hours_ago=2)
    newer = task(lead, "Follow-up — oddzwonić", created_by=OLIWIER)  # człowiek dodał po przeczytaniu maila
    crm.handle_client_mail(m, lead, {"intent": "inne", "confidence": "niska", "needs_reply": False, "summary": "x"})
    crm.db.session.commit()
    assert [a.id for a in crm.lead_open_tasks(lead.id).all()] == [newer.id]


def test_lost_lead_writing_again_is_flagged_not_reopened(app):
    lead = mk("Wraca Stracony", stage="Stracony", email="jan@wraca-test.pl")
    crm.handle_client_mail(mail(lead), lead, dict(ACCEPT))
    crm.db.session.commit()
    assert crm.db.session.get(crm.Lead, lead.id).stage == "Stracony", "silnik nie wskrzesza leadów sam"
    assert [n.kind for n in notices(JERZY)] == ["info"]


def test_resignation_suggests_lost_but_does_not_close(app):
    lead = mk("Rezygnacja", email="anna@rezygnacja-test.pl")
    res = {"intent": "rezygnacja", "confidence": "wysoka", "needs_reply": False, "summary": "Wybrali inną firmę."}
    crm.handle_client_mail(mail(lead), lead, res)
    crm.db.session.commit()
    assert crm.db.session.get(crm.Lead, lead.id).stage == "Oferta", "„Stracony” zaznacza człowiek (powód straty)"
    assert [n.kind for n in notices(JERZY)] == ["lost"]


def test_won_lead_mail_is_left_to_customer_journey(app):
    lead = mk("Już Wygrany", stage="Wygrany", email="piotr@juz-wygrany-test.pl", value_pln=7000)
    assert "pominięty" in crm.handle_client_mail(mail(lead), lead, dict(ACCEPT))
    assert not notices()


def test_stage_hygiene_retires_tasks_from_before_closing(app):
    lead = mk("Zrealizowany Stary", stage="Wygrany")
    stale = task(lead, "Zadzwonić za miesiąc", created_by=OLIWIER, created_at=datetime.utcnow() - timedelta(days=30))
    lead.stage = "Zrealizowany"  # np. dawny import — bez record_stage_change
    crm.db.session.add(crm.StageHistory(lead_id=lead.id, from_stage="Wygrany", to_stage="Zrealizowany",
                                        changed_at=datetime.utcnow() - timedelta(days=1)))
    crm.db.session.commit()
    fresh = task(lead, "Wysłać zdjęcia z eventu", kind="email", created_by=OLIWIER)
    assert crm.stage_hygiene_pass() >= 1
    assert crm.db.session.get(crm.Activity, stale.id).kind == "note"
    assert [a.id for a in crm.lead_open_tasks(lead.id).all()] == [fresh.id], "zadanie dodane po zamknięciu zostaje"
    assert crm.db.session.get(crm.Lead, lead.id).stage == "Zrealizowany"


def test_mail_engine_pass_reads_only_new_client_mail(app, monkeypatch):
    row = crm.db.session.get(crm.AppSetting, crm.MAIL_ENGINE_KEY)
    if row is not None:
        crm.db.session.delete(row)
        crm.db.session.commit()
    lead = mk("Obieg Poczty", email="zamowienie@obieg-test.pl", value_pln=6000, client_type="firma")
    crm.set_lead_event_days(lead, [date.today() + timedelta(days=30)], {})
    crm.db.session.commit()
    old = mail(lead)
    seen = []
    monkeypatch.setattr(crm, "_classify_client_mail", lambda m, l: seen.append(m.id) or dict(ACCEPT))
    assert crm.mail_engine_pass() == 0, "pierwszy obieg tylko zapamiętuje miejsce — historii nie przerabia"
    assert seen == [] and crm.db.session.get(crm.Lead, lead.id).stage == "Oferta"
    stale = mail(lead, hours_ago=crm.MAIL_ENGINE_MAX_AGE_H + 5)
    own = mail(lead, sender="jerzy@33bots.pl")
    stranger = mail(lead, sender="ktos@nieznany-test.pl")
    stranger.addrs = " ktos@nieznany-test.pl kontakt@33bots.pl "
    fresh = mail(lead)
    crm.db.session.commit()
    assert crm.mail_engine_pass() == 1
    assert seen == [fresh.id], "stare, własne i nieprzypisane maile pomijane"
    assert crm.db.session.get(crm.Lead, lead.id).stage == "Wygrany"
    assert crm.get_setting(crm.MAIL_ENGINE_KEY) == str(fresh.id)
    assert crm.mail_engine_pass() == 0 and seen == [fresh.id], "każdy mail tylko raz"
    assert old.id < stale.id < own.id


def test_human_win_notifies_others_and_bell_shows_it(app, client):
    lead = mk("Dzwonek Wygrana", value_pln=4000, client_type="firma", assigned_to=OLIWIER)
    crm.set_lead_event_days(lead, [date.today() + timedelta(days=20)], {})
    crm.db.session.commit()
    gate = crm.gated_stage_change(lead, "Wygrany", OLIWIER, {"win_confirmation": "umowa"})
    crm.db.session.commit()
    assert gate["changed"], gate
    assert sorted(n.user_id for n in notices(kind="win")) == sorted(u["id"] for u in crm.USERS if u["id"] != OLIWIER)
    html = client.get("/crm/").get_data(as_text=True)
    assert "Dzwonek Wygrana" in html and "Co się wydarzyło" in html
    assert "/pokazane" in html, "Bartek zobaczy pokaz 🏆 wygranej kolegi"
    n = notices(BARTEK, "win")[0]
    assert client.post(f"/crm/api/powiadomienia/{n.id}/pokazane").status_code == 200
    assert crm.notice_to_celebrate(BARTEK) is None
    r = client.get(f"/crm/powiadomienia/{n.id}")
    assert r.status_code == 302 and f"/crm/leads/{lead.id}" in r.headers["Location"]
    assert crm.db.session.get(crm.Notice, n.id).read_at is not None
    other = notices(JERZY, "win")[0]
    client.get(f"/crm/powiadomienia/{other.id}")  # cudzego powiadomienia nie da się „przeczytać”
    assert crm.db.session.get(crm.Notice, other.id).read_at is None
    assert client.post("/crm/api/powiadomienia/przeczytane").status_code == 200
    assert crm.notices_unread(BARTEK) == 0 and crm.notices_unread(JERZY) == 1


def test_win_show_numbers_and_script(app, client):
    """Pokaz wygranej (static/wygrana.js): kto zamknął, która to wygrana w miesiącu, suma i rekord."""
    small = mk("Pokaz Mała", value_pln=3000, client_type="firma", assigned_to=JERZY)
    big = mk("Pokaz Duża", value_pln=990000, client_type="firma", assigned_to=JERZY)
    for lead in (small, big):
        crm.set_lead_event_days(lead, [date.today() + timedelta(days=15)], {})
        crm.db.session.commit()
        assert crm.gated_stage_change(lead, "Wygrany", JERZY, {"win_confirmation": "umowa"})["changed"]
        crm.db.session.commit()
    d = client.get(f"/crm/api/leads/{big.id}/wygrana").get_json()
    assert d["who"] == crm.USERS_BY_ID[JERZY]["name"] and d["who_id"] == JERZY and not d["by_mail"]
    assert d["n"] >= 2 and d["mine"] >= 2 and d["record"] is True and d["currency"] == crm.M(CODE)["currency"]
    assert client.get(f"/crm/api/leads/{small.id}/wygrana").get_json()["record"] is False
    assert client.get("/crm/api/leads/999999/wygrana").status_code == 404
    html = client.get("/crm/").get_data(as_text=True)
    assert "static/wygrana.js" in html and "window.CRM_ME" in html
    js = open(crm.os.path.join(crm.app.static_folder, "wygrana.js"), encoding="utf-8").read()
    assert "window.crmCelebrate" in js and "/wygrana`" in js and "prefers-reduced-motion" in js
