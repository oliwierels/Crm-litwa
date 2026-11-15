"""Strażnik CRM w działaniu: czujniki pracowników w tle → alarm dla człowieka (pasek + strona „Stan CRM”, diagnoza
Claude'a) albo naprawa bez człowieka (chwilowe błędy, restart wątku, błąd w kodzie), cisza w leadach, robot bez
osobowości, narzędzie crm_health w czacie.
Bez prawdziwego Claude'a i bez wysyłki."""
import json
from datetime import datetime, timedelta

import pytest

import crm

LT = hasattr(crm.MailMessage, "market")


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


@pytest.fixture
def clean(app, monkeypatch):
    for row in crm.AppSetting.query.filter(crm.AppSetting.key.like(crm.HEALTH_BEAT_PREFIX + "%")).all():
        crm.db.session.delete(row)
    for key in (crm.HEALTH_ALARMS_KEY, crm.HEALTH_LOG_KEY):
        crm.set_setting(key, "")
    crm.db.session.commit()
    monkeypatch.setattr(crm, "_smtp_send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("bez wysyłki")))
    monkeypatch.setattr(crm, "health_continuity", lambda now=None: {})
    monkeypatch.setattr(crm, "_ai_client", lambda: None)
    yield


def client():
    c = crm.app.test_client()
    if LT:
        c.set_cookie("crm_market", "lt")
    return c


def test_human_alarm_with_diagnosis_and_clears(app, clean, monkeypatch):
    """Błąd, którego bez człowieka nie naprawi się (zły klucz) — pasek, diagnoza, czat; po naprawie znika."""
    assert "kampanie" in crm.health_jobs()
    crm.health_beat("kampanie", True, info="ok")
    for _ in range(2):
        crm.health_beat("kampanie", False, error="HTTPError: 401 Client Error: Unauthorized (invalid api key)")
    assert not crm.health_check_pass()["alarms"], "dwa błędy z rzędu to jeszcze nie alarm"
    crm.health_beat("kampanie", False, error="HTTPError: 401 Client Error: Unauthorized (invalid api key)")
    res = crm.health_check_pass()
    alarm = res["alarms"]["job:kampanie"]
    assert alarm["level"] == "red" and alarm["title"] == "Kampanie (Blitzkrieg): nie działa" and alarm["who"] == "human"
    assert "401" in alarm["detail"] and "Blitzkrieg" in alarm["hint"] and res["new"] == ["job:kampanie"]

    # pasek na górze CRM, licznik w menu i strona „Stan CRM”
    c = client()
    html = c.get("/crm/").get_data(as_text=True)
    assert "Strażnik CRM:" in html and "Kampanie (Blitzkrieg): nie działa" in html
    page = c.get("/crm/stan").get_data(as_text=True)
    assert "Wymaga Twojej akcji (1)" in page and "Unauthorized" in page and "Pracownicy w tle" in page
    assert "diagnoza Claude'a pojawi się" in page

    # diagnoza Claude'a — raz na alarm
    calls = []

    def fake(model, system, text, tool, max_tokens=4096):
        calls.append(text)
        assert tool["name"] == "health_diagnosis" and "Kampanie (Blitzkrieg): nie działa" in text
        assert "Unauthorized" in text and "Railway" in system
        return {"cause": "Klucz wygasł albo został zmieniony.", "fix": "1. Wygeneruj nowy klucz.", "urgent": False}

    monkeypatch.setattr(crm, "_ai_client", lambda: object())
    monkeypatch.setattr(crm, "_ai_forced_tool", fake)
    assert crm.health_check_pass()["diagnosed"] == 1
    assert crm.health_check_pass()["diagnosed"] == 0 and len(calls) == 1, "trwający alarm bez ponownej diagnozy"
    page = c.get("/crm/stan").get_data(as_text=True)
    assert "Klucz wygasł" in page and "Wygeneruj nowy klucz" in page

    # w czacie (konektor / asystent)
    out = json.loads(crm._assistant_execute("crm_health", {}, crm.USERS_BY_ID[1])[0])
    assert out["all_ok"] is False and out["needs_human"][0]["diag"]["cause"].startswith("Klucz wygasł")
    assert any(j["key"] == "kampanie" and j["state"] == "failing" for j in out["jobs"])
    assert "crm_health" in [t["name"] for t in crm._mcp_tools_payload()]

    # wróciło do normy — alarm znika sam, w historii zostaje ślad
    crm.health_beat("kampanie", True)
    res = crm.health_check_pass()
    assert not res["alarms"] and res["closed"] == ["job:kampanie"]
    assert "Strażnik CRM:" not in c.get("/crm/").get_data(as_text=True)
    page = c.get("/crm/stan").get_data(as_text=True)
    assert "Nic nie wymaga Twojej akcji" in page and "wróciło do normy" in page


def test_transient_and_code_errors_are_fixed_without_human(app, clean, monkeypatch):
    """Chwilowy błąd (np. zajęta baza) i błąd w kodzie: bez paska u człowieka, bez diagnozy — tylko w sekcji
    „naprawiane automatycznie”. Gdy naprawa nie pomaga (6 h / 24 h), alarm przechodzi do człowieka."""
    monkeypatch.setattr(crm, "_ai_client", lambda: object())
    monkeypatch.setattr(crm, "_ai_forced_tool", lambda *a, **k: (_ for _ in ()).throw(AssertionError("bez diagnozy")))
    for _ in range(3):
        crm.health_beat("kampanie", False, error="OperationalError: database is locked")
    alarm = crm.health_check_pass()["alarms"]["job:kampanie"]
    assert alarm["who"] == "auto" and alarm["how"] == "wait"
    c = client()
    assert "Strażnik CRM:" not in c.get("/crm/").get_data(as_text=True), "nic dla człowieka"
    page = c.get("/crm/stan").get_data(as_text=True)
    assert "Nic nie wymaga Twojej akcji" in page and "Naprawiane automatycznie" in page and "ponawia sam" in page
    out = json.loads(crm._assistant_execute("crm_health", {}, crm.USERS_BY_ID[1])[0])
    assert out["all_ok"] is True and out["auto_fixing"][0]["how"] == "wait"

    # po 6 h chwilowy błąd dalej trwa — człowiek (z diagnozą)
    cur = json.loads(crm.get_setting(crm.HEALTH_ALARMS_KEY))
    cur["job:kampanie"]["since"] = (datetime.utcnow() - timedelta(hours=7)).isoformat(timespec="seconds")
    crm.set_setting(crm.HEALTH_ALARMS_KEY, json.dumps(cur))
    crm.db.session.commit()
    monkeypatch.setattr(crm, "_ai_forced_tool", lambda *a, **k: {"cause": "Baza przeciążona.", "fix": "Sprawdź Railway.",
                                                                 "urgent": True})
    res = crm.health_check_pass()
    assert res["alarms"]["job:kampanie"]["who"] == "human" and res["escalated"] == ["job:kampanie"]
    assert "nie pomogła od 7 h" in res["alarms"]["job:kampanie"]["why"] and res["diagnosed"] == 1
    assert "Strażnik CRM:" in c.get("/crm/").get_data(as_text=True)

    # błąd w kodzie — naprawia Claude (bez paska u człowieka)
    crm.health_beat("kampanie", True)
    crm.health_check_pass()
    for _ in range(3):
        crm.health_beat("kampanie", False, error="AttributeError: 'NoneType' object has no attribute 'stage'")
    alarm = crm.health_check_pass()["alarms"]["job:kampanie"]
    assert alarm["who"] == "auto" and alarm["how"] == "code"


def test_dead_worker_restarted_automatically(app, clean, monkeypatch):
    """Wątek pracownika umarł (stoi) — strażnik uruchamia go ponownie sam; po 3 nieudanych próbach — człowiek."""
    import threading
    started = []
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    monkeypatch.setitem(crm._WORKERS, "campaign", (lambda: started.append(1), dead))
    beat = crm.health.beat_update({}, True, now=datetime.utcnow() - timedelta(hours=3))
    crm.set_setting(crm.HEALTH_BEAT_PREFIX + "kampanie", json.dumps(beat))
    crm.db.session.commit()
    res = crm.health_check_pass()
    alarm = res["alarms"]["job:kampanie"]
    assert alarm["title"] == "Kampanie (Blitzkrieg): stoi" and alarm["who"] == "auto" and alarm["how"] == "restart"
    assert res["fixed"] == ["job:kampanie"] and "uruchomiony ponownie" in alarm["auto_note"]
    for _ in range(50):
        if started:
            break
        import time
        time.sleep(0.02)
    assert started == [1] and crm._WORKERS["campaign"][1] is not dead
    assert crm.health_check_pass()["fixed"] == [], "kolejna próba nie wcześniej niż po 10 min"
    cur = json.loads(crm.get_setting(crm.HEALTH_ALARMS_KEY))
    cur["job:kampanie"]["auto_tries"] = 3
    crm.set_setting(crm.HEALTH_ALARMS_KEY, json.dumps(cur))
    crm.db.session.commit()
    assert crm.health_check_pass()["alarms"]["job:kampanie"]["who"] == "human", "3 próby bez skutku — człowiek"


def test_claim_every(app, clean):
    key = "health_test_lock"
    assert crm._claim_every(key, 600) and not crm._claim_every(key, 600)
    row = crm.db.session.get(crm.AppSetting, key)
    row.value = (datetime.utcnow() - timedelta(minutes=11)).isoformat(timespec="seconds")
    crm.db.session.commit()
    assert crm._claim_every(key, 600) and not crm._claim_every(key, 600)


def test_quiet_leads_alarm(app, clean, monkeypatch):
    monkeypatch.undo()  # prawdziwa ciągłość, ale na własnych leadach testu
    monkeypatch.setattr(crm, "_ai_client", lambda: None)
    mk = {"market": "lt"} if LT else {}
    start = datetime.utcnow() - timedelta(days=10)
    for i in range(20):
        lead = crm.Lead(company=f"Cisza {i}", stage="Nowy", assigned_to=1, **mk)
        lead.created_at = start + timedelta(hours=6 * i)  # ostatni ~5 dni temu, zwykle co 6 h
        crm.db.session.add(lead)
    crm.db.session.commit()
    monkeypatch.setattr(crm, "health_lead_markets",
                        lambda: [("test", "Nowe leady (test)", crm.all_markets(crm.Lead.query.filter(crm.Lead.company.like("Cisza %"))))])
    monkeypatch.setattr(crm, "mail_boxes", lambda: [])
    found = crm.health_continuity()
    alarm = found["quiet:leads:test"]
    assert alarm["level"] == "yellow" and "cisza dłuższa niż zwykle" in alarm["title"] and "zwykle co 6 h" in alarm["detail"]


@pytest.mark.skipif(not hasattr(crm, "robot_agents_plan"), reason="robot tylko w CRM PL")
def test_robot_without_persona_tomorrow(app, monkeypatch):
    lead = crm.Lead(company="Gala Robota", stage="Wygrany", assigned_to=2)
    crm.db.session.add(lead)
    crm.db.session.commit()
    tomorrow = crm._local_today() + timedelta(days=1)
    monkeypatch.setattr(crm.robot_agent, "api_key", lambda: "x")
    monkeypatch.setattr(crm, "robot_agents_plan", lambda: {"Benio": {"day": tomorrow, "lead_id": lead.id, "lead": lead},
                                                         "Jerzy": {"day": tomorrow + timedelta(days=5), "lead_id": 1}})
    states = {"Benio": {"lead_id": 999}}
    monkeypatch.setattr(crm, "robot_agent_state", lambda a: states.get(a, {}))
    found = crm.health_robot_alarms()
    assert list(found) == ["robot:Benio"] and "brak osobowości na wydarzenie jutro" in found["robot:Benio"]["title"]
    assert "Gala Robota" in found["robot:Benio"]["title"]
    states["Benio"] = {"lead_id": lead.id, "error": "401 invalid api key"}
    assert crm.health_robot_alarms()["robot:Benio"]["level"] == "red"
    states["Benio"] = {"lead_id": lead.id}
    assert crm.health_robot_alarms() == {}
