"""Zespół w CRM: rangi, obowiązkowy raport dnia (blokada, spóźnienie, dzień wolny, zwolnienie), zadania zlecone
(zrobione / niezrobione z powodem), rozmowa pod raportem, eksport i narzędzie team_reports. Ten sam plik w CRM PL
i LT/DE (w LT/DE raport dnia jest domyślnie wyłączony — testy włączają go zmienną DAY_REPORTS)."""
import json
from datetime import datetime, timedelta

import pytest

import crm
import team

LT = hasattr(crm.Lead, "market")
OLIWIER, BARTEK, JERZY = 1, 2, 3
MARK = "ZT "  # początek notatki zadań z tego testu (sprzątanie)


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


def _clean():
    crm.db.session.rollback()
    crm.DayReport.query.delete()
    crm.AppSetting.query.filter(crm.AppSetting.key.like("team_%")).delete(synchronize_session=False)
    for a in crm.all_markets(crm.Activity.query.filter(crm.Activity.notes.like(MARK + "%"))).all():
        crm.db.session.delete(a)
    crm.db.session.commit()


@pytest.fixture(autouse=True)
def fresh(app, monkeypatch):
    monkeypatch.setenv("DAY_REPORTS", "1")
    monkeypatch.setattr(team, "is_required_day", lambda d: True)  # test nie zależy od dnia tygodnia ani świąt
    monkeypatch.setattr(crm, "_ai_client", lambda: None)
    _clean()
    yield
    _clean()


def client(uid):
    c = crm.app.test_client()
    c.set_cookie("crm_user", str(uid))
    if LT:
        c.set_cookie("crm_market", "lt")
    return c


def today():
    return crm._local_today()


def since(uid, days_ago):
    crm.set_setting(crm.TEAM_SINCE_KEY.format(uid), (today() - timedelta(days=days_ago)).isoformat())
    crm.db.session.commit()


def task(notes, to=JERZY, by=BARTEK, days=0, kind="task", lead=None):
    a = crm.Activity(kind=kind, notes=MARK + notes, assigned_to=to, created_by=by, lead_id=lead.id if lead else None,
                     due_date=datetime.combine(today() + timedelta(days=days), datetime.min.time()))
    crm.db.session.add(a)
    crm.db.session.commit()
    return a


def report(uid, day):
    crm.db.session.expire_all()
    return crm.DayReport.query.filter_by(user_id=uid, day=day).first()


def post(uid, day, **form):
    return client(uid).post(f"/crm/raport/{day.isoformat()}", data=form)


GOOD = "Obdzwoniłem oferty z zeszłego tygodnia i wysłałem dwie umowy."


def test_default_ranks():
    by_id = {u["id"]: crm.team_role(u) for u in crm.USERS}
    assert by_id[OLIWIER]["key"] == by_id[BARTEK]["key"] == "zarzad" and by_id[JERZY]["key"] == "kierownik"
    assert not crm.team_must_report(crm.USERS_BY_ID[BARTEK]) and crm.team_must_report(crm.USERS_BY_ID[JERZY])
    assert "task" in crm.ACTIVITY_KINDS and crm.ACTIVITY_KIND_LABELS["task"] == "Zadanie"


def test_missing_report_blocks_crm_only_for_reporting_rank():
    assert "Najpierw raport dnia" not in client(BARTEK).get("/crm/").get_data(as_text=True)
    html = client(JERZY).get("/crm/").get_data(as_text=True)
    assert "Najpierw raport dnia" not in html, "pierwszy dzień: obowiązek liczy się od dziś, nic nie zalega"
    assert crm.team_since(crm.USERS_BY_ID[JERZY]) == today()
    since(JERZY, 2)
    html = client(JERZY).get("/crm/").get_data(as_text=True)
    assert "Najpierw raport dnia" in html and f"/crm/raport/{(today() - timedelta(days=2)).isoformat()}" in html
    r = client(JERZY).get("/crm/raport")
    assert r.status_code == 302 and r.headers["Location"].endswith((today() - timedelta(days=2)).isoformat()), "najstarszy brak"
    form = client(JERZY).get(r.headers["Location"]).get_data(as_text=True)
    assert "Najpierw raport dnia" not in form and 'name="summary"' in form and "Brakuje raportów za" in form
    assert "Najpierw raport dnia" not in client(BARTEK).get("/crm/").get_data(as_text=True)
    for back in (2, 1):
        assert post(JERZY, today() - timedelta(days=back), status="work", summary=GOOD).status_code == 302
    assert "Najpierw raport dnia" not in client(JERZY).get("/crm/").get_data(as_text=True)
    assert report(JERZY, today() - timedelta(days=1)).late is True


def test_report_needs_text_and_answer_for_every_delegated_task():
    since(JERZY, 0)
    ordered = task("Zamów 20 koszulek dla operatorów")
    own = task("Własne zadanie Jerzego", by=JERZY)
    future = task("Zlecone na za tydzień", days=7)
    form = client(JERZY).get(f"/crm/raport/{today().isoformat()}").get_data(as_text=True)
    assert "Zamów 20 koszulek" in form and f'name="task_{ordered.id}"' in form
    assert f'name="task_{own.id}"' not in form and f'name="task_{future.id}"' not in form
    assert "Zlecone na za tydzień" in form, "przyszłe widać, ale bez rozliczania"

    r = post(JERZY, today(), status="work", summary="krótko")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "co najmniej" in html and "zaznacz „zrobione” albo „niezrobione”" in html
    assert report(JERZY, today()) is None
    r = post(JERZY, today(), status="work", summary=GOOD, **{f"task_{ordered.id}": "not_done"})
    assert "napisz, dlaczego niezrobione" in r.get_data(as_text=True) and report(JERZY, today()) is None

    r = post(JERZY, today(), status="work", summary=GOOD, blockers="Brak rozmiarówki od ekipy",
             **{f"task_{ordered.id}": "not_done", f"reason_{ordered.id}": "Hurtownia zamknięta do środy"})
    assert r.status_code == 302
    rep = report(JERZY, today())
    snap = json.loads(rep.tasks)
    assert rep.late is False and rep.summary == GOOD and [t["status"] for t in snap] == ["not_done"]
    assert snap[0]["reason"] == "Hurtownia zamknięta do środy" and snap[0]["by"] == "Bartek"
    a = crm.db.session.get(crm.Activity, ordered.id)
    assert not a.done and any(c.text.startswith(crm.TEAM_NOT_DONE_PREFIX) and "Hurtownia" in c.text for c in a.comments)
    mine = [t for t in crm.team_ordered_by(BARTEK) if t["id"] == ordered.id]
    assert mine and "Hurtownia zamknięta" in mine[0]["reason"], "zlecający widzi powód przy zadaniu"

    # dzisiejszy raport można poprawić do końca dnia — tym razem zadanie zrobione
    view = client(JERZY).get(f"/crm/raport/{today().isoformat()}").get_data(as_text=True)
    assert "Popraw (do końca dnia)" in view and "Hurtownia zamknięta do środy" in view
    r = post(JERZY, today(), status="work", summary=GOOD + " Koszulki zamówione.", **{f"task_{ordered.id}": "done"})
    assert r.status_code == 302
    assert crm.db.session.get(crm.Activity, ordered.id).done
    rep = report(JERZY, today())
    assert [t["status"] for t in json.loads(rep.tasks)] == ["done"] and json.loads(rep.stats)["tasks_done"] >= 1
    assert crm.DayReport.query.filter_by(user_id=JERZY).count() == 1


def test_delegated_call_needs_contact_outcome():
    since(JERZY, 0)
    mk = {"market": "lt"} if LT else {}
    lead = crm.Lead(company="Raport Dnia Sp. z o.o.", stage="Oferta", assigned_to=JERZY, contact_phone="600100200", **mk)
    crm.db.session.add(lead)
    crm.db.session.commit()
    call = task("Oddzwoń w sprawie terminu", kind="call", lead=lead)
    try:
        r = post(JERZY, today(), status="work", summary=GOOD, **{f"task_{call.id}": "done"})
        assert r.status_code == 200 and "Podaj wynik kontaktu" in r.get_data(as_text=True)
        assert not crm.db.session.get(crm.Activity, call.id).done and report(JERZY, today()) is None
        r = post(JERZY, today(), status="work", summary=GOOD, **{f"task_{call.id}": "done", f"outcome_{call.id}": "odebral"})
        assert r.status_code == 302
        a = crm.db.session.get(crm.Activity, call.id)
        assert a.done and a.outcome == "odebral"
        stats = json.loads(report(JERZY, today()).stats)
        assert stats["calls_ok"] == 1 and stats["tasks_done"] == 1
    finally:
        crm.db.session.rollback()
        crm.db.session.delete(crm.all_markets(crm.Lead.query.filter(crm.Lead.id == lead.id)).first())
        crm.db.session.commit()


def test_late_day_off_waive_and_closed_past_report():
    since(JERZY, 3)
    d3, d2, d1 = (today() - timedelta(days=n) for n in (3, 2, 1))
    assert post(JERZY, d2, status="work", summary=GOOD).status_code == 302
    assert report(JERZY, d2).late is True
    r = post(JERZY, d1, status="off")
    assert r.status_code == 200 and "Wybierz powód dnia wolnego" in r.get_data(as_text=True)
    assert post(JERZY, d1, status="off", off_reason="urlop", off_note="zaplanowany").status_code == 302
    off = report(JERZY, d1)
    assert off.status == "off" and off.off_reason == "Urlop — zaplanowany"
    assert post(JERZY, d2, status="work", summary=GOOD + " Dopisek po czasie.").status_code == 403, "miniony raport jest zamknięty"
    assert post(JERZY, today() + timedelta(days=1), status="work", summary=GOOD).status_code == 404

    waive = {"user_id": JERZY, "day": d3.isoformat()}
    assert client(JERZY).post("/crm/zespol/zwolnij", data=waive).status_code == 403, "sam siebie nie zwolni"
    assert client(BARTEK).post("/crm/zespol/zwolnij", data=waive).status_code == 302
    w = report(JERZY, d3)
    assert w.status == "off" and "Bartek" in w.off_reason
    assert "Najpierw raport dnia" not in client(JERZY).get("/crm/").get_data(as_text=True)

    data = crm.team_person_report(crm.USERS_BY_ID[JERZY], 30)
    states = {r["day"]: r["state"] for r in data["rows"]}
    assert states[d2] == "late" and states[d1] == "off" and states[d3] == "off" and states[today()] == "today"
    total = data["summary"]["total"]
    assert total["reports_late"] == 1 and total["off"] == 2 and total["reports_missing"] == 0 and total["on_time_pct"] == 0


def test_comments_unread_and_who_sees_what():
    since(JERZY, 0)
    assert post(JERZY, today(), status="work", summary=GOOD).status_code == 302
    rep = report(JERZY, today())

    def unread(uid):
        with crm.app.test_request_context("/crm/", headers={"Cookie": f"crm_user={uid}"}):
            return crm.team_badges()["unread"]

    assert unread(BARTEK) == 1 and unread(JERZY) == 0
    page = client(BARTEK).get(f"/crm/raporty/{rep.id}")
    assert page.status_code == 200 and GOOD in page.get_data(as_text=True) and 'name="summary"' not in page.get_data(as_text=True)
    assert unread(BARTEK) == 0
    r = client(BARTEK).post(f"/crm/raporty/{rep.id}/komentarz", data={"text": "A co z umową dla Gillette?"})
    assert r.status_code == 302 and unread(JERZY) == 1 and unread(BARTEK) == 0
    own = client(JERZY).get(f"/crm/raport/{today().isoformat()}").get_data(as_text=True)
    assert "A co z umową dla Gillette?" in own and unread(JERZY) == 0
    assert client(JERZY).get(f"/crm/raporty/{rep.id}").status_code == 302, "własny raport otwiera się pod adresem dnia"

    boss = crm.DayReport(user_id=BARTEK, day=today(), status="work", summary="Raport zarządu")
    crm.db.session.add(boss)
    crm.db.session.commit()
    assert client(JERZY).get(f"/crm/raporty/{boss.id}").status_code == 403, "Kierownik nie widzi raportów Zarządu"
    assert client(OLIWIER).get(f"/crm/raporty/{boss.id}").status_code == 200
    names = [u["name"] for u in crm._team_visible(crm.USERS_BY_ID[JERZY])]
    assert names == ["Jerzy"] and len(crm._team_visible(crm.USERS_BY_ID[OLIWIER])) == len(crm.USERS)


def test_ranks_are_set_by_management_and_no_data_rank_sees_only_reports():
    role = {"user_id": JERZY, "role": "wykonawca"}
    assert client(JERZY).post("/crm/zespol/rola", data=role).status_code == 403
    assert client(OLIWIER).post("/crm/zespol/rola", data={"user_id": JERZY, "role": "szef"}).status_code == 400
    assert client(OLIWIER).post("/crm/zespol/rola", data=role).status_code == 302
    assert crm.team_role(crm.USERS_BY_ID[JERZY])["key"] == "wykonawca"
    c = client(JERZY)
    r = c.get("/crm/leads")
    assert r.status_code == 302 and r.headers["Location"].endswith("/crm/raport")
    assert c.get("/crm/").status_code == 302 and c.get("/crm/api/activities").status_code == 403
    assert c.post("/crm/tasks/quick-add", data={"notes": MARK + "nie wolno"}).status_code == 403
    team_page = c.get("/crm/zespol")
    html = team_page.get_data(as_text=True)
    assert team_page.status_code == 200 and "Lista leadów" not in html and "Raport dnia" in html
    assert c.get(f"/crm/raport/{today().isoformat()}").status_code == 200
    assert client(BARTEK).get("/crm/leads").status_code == 200

    # w Zarządzie zawsze ktoś zostaje
    assert client(OLIWIER).post("/crm/zespol/rola", data={"user_id": BARTEK, "role": "kierownik"}).status_code == 302
    r = client(OLIWIER).post("/crm/zespol/rola", data={"user_id": OLIWIER, "role": "pracownik"})
    assert r.status_code == 302 and crm.team_role(crm.USERS_BY_ID[OLIWIER])["key"] == "zarzad"
    assert client(BARTEK).post("/crm/zespol/rola", data={"user_id": JERZY, "role": "kierownik"}).status_code == 403, "już nie Zarząd"


def test_delegating_from_team_page_and_history_export():
    since(JERZY, 1)
    r = client(BARTEK).post("/crm/tasks/quick-add", data={"assigned_to": JERZY, "kind": "task", "next": "zespol",
                                                      "notes": MARK + "Policz koszty transportu na grudzień",
                                                      "due_date": today().isoformat()})
    assert r.status_code == 302 and "/crm/zespol" in r.headers["Location"]
    a = crm.all_markets(crm.Activity.query.filter(crm.Activity.notes == MARK + "Policz koszty transportu na grudzień")).first()
    assert a.created_by == BARTEK and a.assigned_to == JERZY and a.kind == "task"
    assert post(JERZY, today() - timedelta(days=1), status="work", summary=GOOD).status_code == 302
    assert post(JERZY, today(), status="work", summary=GOOD,
                **{f"task_{a.id}": "not_done", f"reason_{a.id}": "Czekam na cennik przewoźnika"}).status_code == 302

    page = client(BARTEK).get(f"/crm/zespol?osoba={JERZY}&dni=90")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "Policz koszty transportu" in html and "Czekam na cennik przewoźnika" in html
    assert "spóźniony" in html and "Średnio w dniu tygodnia" in html

    js = client(BARTEK).get(f"/crm/zespol/eksport.json?osoba={JERZY}&dni=30")
    data = json.loads(js.get_data(as_text=True))
    assert js.status_code == 200 and data["person"] and data["role"] == "Kierownik" and data["report_required"] is True
    days = {d["date"]: d for d in data["days"]}
    assert days[today().isoformat()]["state"] == "ok" and days[(today() - timedelta(days=1)).isoformat()]["state"] == "late"
    nd = days[today().isoformat()]["ordered_tasks_not_done"]
    assert nd and nd[0]["reason"] == "Czekam na cennik przewoźnika" and nd[0]["ordered_by"] == "Bartek"
    assert data["total"]["reports_late"] == 1 and data["total"]["ordered_not_done"] == 1 and len(data["by_weekday"]) >= 5
    csv = client(BARTEK).get(f"/crm/zespol/eksport.csv?osoba={JERZY}").get_data(as_text=True)
    assert "Data;Dzień tygodnia;Stan raportu" in csv and "Czekam na cennik przewoźnika" in csv
    assert client(BARTEK).get("/crm/zespol/eksport.xml").status_code == 404
    # Kierownik nie pobierze cudzej historii — dostaje własną
    mine = json.loads(client(JERZY).get(f"/crm/zespol/eksport.json?osoba={BARTEK}").get_data(as_text=True))
    assert mine["role"] == "Kierownik"


def test_connector_tool_gives_history_for_analysis():
    since(JERZY, 0)
    assert post(JERZY, today(), status="work", summary=GOOD).status_code == 302
    out = crm.team_tool({"person": "jerzy", "days": 30}, crm.USERS_BY_ID[BARTEK])
    person = out["people"][0]
    assert out["reports_enabled_in_this_crm"] and person["role"] == "Kierownik" and person["days"][-1]["summary"] == GOOD
    assert set(team.METRIC_KEYS) <= set(person["days"][-1]["metrics"]) and "by_weekday" in person and "total" in person
    assert [p["role"] for p in crm.team_tool({}, crm.USERS_BY_ID[BARTEK])["people"]] == ["Kierownik"], "domyślnie: osoby z raportem"
    assert "error" in crm.team_tool({"person": "oliwier"}, crm.USERS_BY_ID[JERZY]), "Kierownik nie czyta raportów Zarządu"
    text, _extra = crm._assistant_execute("team_reports", {"person": "Jerzy"}, crm.USERS_BY_ID[BARTEK])
    assert json.loads(text)["people"][0]["period"]["days"] == 60
    assert any(t["name"] == "team_reports" for t in crm.ASSISTANT_TOOLS)


def test_switch_turns_reports_off(monkeypatch):
    since(JERZY, 3)
    assert "Najpierw raport dnia" in client(JERZY).get("/crm/").get_data(as_text=True)
    monkeypatch.setenv("DAY_REPORTS", "0")
    assert "Najpierw raport dnia" not in client(JERZY).get("/crm/").get_data(as_text=True)
    assert "raport dnia jest wyłączony" in client(BARTEK).get("/crm/zespol").get_data(as_text=True)
    r = client(JERZY).get("/crm/raport")
    assert r.status_code == 302 and r.headers["Location"].endswith("/crm/zespol"), "wyłączony raport: nie ma formularza"
    assert client(JERZY).post(f"/crm/raport/{today().isoformat()}", data={"summary": GOOD}).status_code == 302
    assert report(JERZY, today()) is None
    monkeypatch.delenv("DAY_REPORTS")
    assert crm.team_reports_on() is (not LT), "domyślnie: CRM PL tak, CRM LT/DE nie (jeden raport na osobę)"
