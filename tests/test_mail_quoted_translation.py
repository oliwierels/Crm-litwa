"""Poczta LT/DE: tłumaczenie wcześniejszej korespondencji (cytatu) obok oryginału."""
from datetime import datetime

import pytest

import crm

BODY = ("Laba diena,\načiū už pasiūlymą, deja, mums jis per brangus.\n\n"
        "From: o.latek@33bots.lt <o.latek@33bots.lt>\nSent: Wednesday, November 4, 2026 5:09 PM\n"
        "Šioje žinutėje ir bet kokiuose jos prieduose pateikiama informacija yra konfidenciali.\n"
        "Laba diena, Olegai, ačiū už atsakymą.")


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


def test_quoted_translated_on_demand_and_cached(app, monkeypatch):
    calls = []

    def fake(subject, body, target, tone_hint=None):
        calls.append(body)
        return "lt", "", "Ta wiadomość i załączniki są poufne. Dzień dobry, Olegu, dziękuję za odpowiedź."
    monkeypatch.setattr(crm, "ai_translate_checked", fake)
    monkeypatch.setattr(crm, "_ai_client", lambda: object())
    row = crm.MailMessage(mailbox="o.latek@33bots.lt", market="lt", folder="in", uid=901, uidvalidity="1",
                          message_id="<q1>", thread_key="<q1>", from_addr="o.gerasimovic@adbaltic.lt",
                          subject="Re: pasiūlymas", date=datetime.utcnow(), body_text=BODY, lang="lt",
                          body_pl="Dzień dobry, dziękuję za ofertę, niestety jest dla nas za droga.",
                          addrs=" o.gerasimovic@adbaltic.lt o.latek@33bots.lt ")
    crm.db.session.add(row)
    crm.db.session.commit()
    c = app.test_client()
    c.set_cookie("crm_market", "lt")
    html = c.get(f"/crm/poczta?m={row.id}").get_data(as_text=True)
    assert "Wcześniejsza korespondencja (cytat) — z tłumaczeniem" in html and 'id="pm-quoted-pl"' in html
    r = c.post(f"/crm/poczta/{row.id}/tlumacz-cytat")
    assert r.json["ok"] and "poufne" in r.json["body"]
    assert "konfidenciali" in calls[0] and "per brangus" not in calls[0]  # tłumaczona tylko historia
    r = c.post(f"/crm/poczta/{row.id}/tlumacz-cytat")
    assert len(calls) == 1  # drugi raz z bazy, bez kosztów
    html = c.get(f"/crm/poczta?m={row.id}").get_data(as_text=True)
    assert "Ta wiadomość i załączniki są poufne" in html
