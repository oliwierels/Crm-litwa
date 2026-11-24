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
    assert all(n.lead_id == lead.id for n in wins)
    assert [n.user_id for n in wins if n.celebrate] == [lead.assigned_to], "pokaz 🏆 tylko u opiekuna (jego wygrana)"
    notes = " ".join(a.notes for a in crm.Activity.query.filter_by(lead_id=lead.id, kind="note").all())
    assert "Wygrany z maila" in notes and "cofnij etap" in notes, "decyzja silnika jest opisana przy leadzie"


def test_acceptance_without_event_date_still_wins(app):
    """Klient przyjmuje ofertę, a dzień ustali później — lead i tak jest wygrany (data nie jest wymagana)."""
    lead = mk("Bez Daty", email="biuro@bez-daty-test.pl", value_pln=5000, client_type="firma")
    out = crm.handle_client_mail(mail(lead), lead, dict(ACCEPT))
    crm.db.session.commit()
    assert crm.db.session.get(crm.Lead, lead.id).stage == "Wygrany", out
    assert not notices(kind="blocked")


def test_acceptance_without_value_only_notifies_owner(app):
    lead = mk("Bez Kwoty", email="biuro@bez-kwoty-test.pl", client_type="firma")
    out = crm.handle_client_mail(mail(lead), lead, dict(ACCEPT))
    crm.db.session.commit()
    assert crm.db.session.get(crm.Lead, lead.id).stage == "Oferta", out
    blocked = notices(kind="blocked")
    assert [n.user_id for n in blocked] == [JERZY] and "wartości" in blocked[0].body
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
    monkeypatch.setattr(crm, "_ai_client", lambda: object())
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
    assert "/pokazane" not in html, "wygrana Oliwiera: pokaz 🏆 tylko na jego koncie, u Bartka tylko dzwonek"
    assert all(not n.celebrate for n in notices(kind="win"))
    assert crm.notice_to_celebrate(BARTEK) is None and crm.notice_to_celebrate(JERZY) is None
    n = notices(BARTEK, "win")[0]
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


def test_mail_engine_retries_when_claude_fails(app, monkeypatch):
    """Claude chwilowo nie działa → mail nie przepada: wskaźnik stoi, a po 3 nieudanych próbach idzie dalej.
    Bez klucza Claude'a silnik nic nie przerabia i nie przesuwa wskaźnika."""
    lead = mk("Ponawianie", email="biuro@ponawianie-test.pl", value_pln=6000, client_type="firma")
    crm.set_setting(crm.MAIL_ENGINE_KEY, str(crm.db.session.query(crm.db.func.max(crm.MailMessage.id)).scalar() or 0))
    crm.set_setting(crm.MAIL_ENGINE_FAIL_KEY, "")
    crm.db.session.commit()
    m = mail(lead)
    start = crm.get_setting(crm.MAIL_ENGINE_KEY)
    assert crm.mail_engine_pass() == 0 and crm.get_setting(crm.MAIL_ENGINE_KEY) == start, "bez klucza — czekamy"
    monkeypatch.setattr(crm, "_ai_client", lambda: object())
    calls = []

    def boom(mm, ll):
        calls.append(mm.id)
        raise TimeoutError("Claude nie odpowiada")
    monkeypatch.setattr(crm, "_classify_client_mail", boom)
    crm.mail_engine_pass()
    crm.mail_engine_pass()
    assert calls == [m.id, m.id] and crm.get_setting(crm.MAIL_ENGINE_KEY) == start, "ten sam mail ponawiany"
    crm.mail_engine_pass()
    assert crm.get_setting(crm.MAIL_ENGINE_KEY) == str(m.id), "po 3 próbach — dalej, żeby jeden mail nie blokował reszty"
    assert crm.db.session.get(crm.Lead, lead.id).stage == "Oferta"


def test_one_task_per_lead_once(app):
    """Raz po wdrożeniu: lead z kilkoma otwartymi zadaniami (dawne automaty) zostaje z jednym; treść usuniętych
    automatycznych zadań zostaje w komentarzu, zadanie od człowieka — jako komentarz „🧹 Nieaktualne”."""
    for key in (crm.ONE_TASK_KEY, crm.ONE_TASK_KEY + "_lock"):
        crm.AppSetting.query.filter_by(key=key).delete()
    crm.db.session.commit()
    days = lambda n: datetime.utcnow() - timedelta(days=n)  # noqa: E731
    # A: stary telefon + partia po ofercie (telefon i follow-up mailem) → zostaje telefon z najnowszej partii
    a = mk("Jedno Zadanie A")
    a_old = task(a, "Zadzwoń — nowy lead", created_at=days(10))
    a_call = task(a, "📞 Drugi krok: telefon po ofercie wysłanej 03.11", created_at=days(2))
    a_mail = task(a, "Follow-up oferty (6 000 zł netto) wysłanej 03.11", kind="email", created_at=days(2))
    # B: zadanie od człowieka wygrywa z nowszym automatycznym „Zdecyduj”
    b = mk("Jedno Zadanie B")
    b_human = task(b, "Oddzwonić po 15:00", created_by=OLIWIER, created_at=days(5))
    task(b, "⚖️ Zdecyduj: 2 nieudane próby kontaktu z rzędu.", kind="decision", created_at=days(1))
    # C: dwa od człowieka → zostaje nowsze, starsze staje się komentarzem
    c = mk("Jedno Zadanie C")
    c_old = task(c, "Wysłać zdjęcia z realizacji", kind="email", created_by=JERZY, created_at=days(4))
    c_new = task(c, "Zapytać o godziny", created_by=JERZY, created_at=days(1))
    # D: osoba prywatna — gotowy szkic pierwszej odpowiedzi ważniejszy niż telefon z tej samej partii
    d = mk("Jedno Zadanie D")
    d_draft = task(d, crm.sales_rules.PRIVATE_DRAFT_PREFIX + " — szkic gotowy.\nTemat: Robot\n---\nDzień dobry, ...",
                   kind="email", created_at=days(3))
    task(d, "📞 Zadzwoń do osoby prywatnej (najwyżej 2 próby)", created_at=days(3))
    # E: jedno zadanie — bez zmian; zadania własne (bez leada) — bez zmian
    e = mk("Jedno Zadanie E")
    e_only = task(e, "Telefon w piątek")
    own = [crm.Activity(lead_id=None, kind="call", assigned_to=OLIWIER, notes=f"Własne {i}") for i in range(2)]
    crm.db.session.add_all(own)
    crm.db.session.commit()

    assert crm.one_task_per_lead_once() >= 5
    open_ids = lambda lead: [x.id for x in crm.lead_open_tasks(lead.id).all()]  # noqa: E731
    assert open_ids(a) == [a_call.id] and open_ids(b) == [b_human.id] and open_ids(c) == [c_new.id]
    assert open_ids(d) == [d_draft.id] and open_ids(e) == [e_only.id]
    assert crm.db.session.get(crm.Activity, a_old.id) is None and crm.db.session.get(crm.Activity, a_mail.id) is None
    note = crm.Activity.query.filter(crm.Activity.lead_id == a.id, crm.Activity.kind == "note",
                                     crm.Activity.notes.like(crm.CLEAN_PREFIX + "%")).one().notes
    assert "jedno zadanie na leada" in note and "Follow-up oferty (6 000 zł netto)" in note and "Zadzwoń — nowy lead" in note
    c_note = crm.db.session.get(crm.Activity, c_old.id)
    assert c_note.kind == "note" and c_note.notes.startswith(crm.CLEAN_PREFIX) and "Wysłać zdjęcia" in c_note.notes
    assert all(not x.done for x in own), "zadania własne bez zmian"
    assert crm.Lead.query.filter(crm.Lead.id.in_([a.id, b.id, c.id, d.id, e.id])).count() == 5, "leady zostają"
    report = crm.get_setting(crm.ONE_TASK_KEY)
    assert report and '"tasks"' in report
    task(a, "Nowe zadanie od człowieka", created_by=JERZY)
    assert crm.one_task_per_lead_once() is None and len(open_ids(a)) == 2, "tylko raz — to nie jest stała reguła"
    for x in own:
        crm.db.session.delete(x)
    crm.db.session.commit()


def test_old_team_celebrations_are_quieted_once(app):
    """Zaległe pokazy cudzych wygranych (sprzed zmiany zasady) nie wyskakują na innych kontach — raz po wdrożeniu."""
    for key in (crm.QUIET_CELEBRATIONS_KEY, crm.QUIET_CELEBRATIONS_KEY + "_lock"):
        crm.AppSetting.query.filter_by(key=key).delete()
    lead = mk("Stary Pokaz", value_pln=5000, assigned_to=OLIWIER)
    crm.notify([JERZY, BARTEK], "win", "🏆 Wygrana: Stary Pokaz", "Oliwier", lead, celebrate=True)
    crm.db.session.commit()
    assert crm.notice_to_celebrate(JERZY) is not None
    assert crm.quiet_team_celebrations_once() >= 2
    assert crm.notice_to_celebrate(JERZY) is None and crm.notice_to_celebrate(BARTEK) is None
    assert len(notices(JERZY, "win")) == 1, "wpis w dzwonku zostaje"
    assert crm.quiet_team_celebrations_once() is None, "tylko raz"


def test_win_replay_script_is_per_account():
    js = open("static/wygrana.js", encoding="utf-8").read()
    assert "w.by === me" in js, "pokaz po przeładowaniu tylko dla tej samej osoby (nie po przelogowaniu)"
