"""Data wygranej z CRM LT/DE trafia do kopii w polskim CRM: poprawiona „Wygrany dnia” (i jednorazowa poprawka
Laimy / Galley) przesuwa wygraną w PL do właściwego miesiąca."""
import os
import tempfile
from datetime import date, datetime

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, select

import crm
import pl_move


@pytest.fixture(scope="module")
def app():
    with crm.app.app_context():
        yield crm.app


@pytest.fixture
def pl_db(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(prefix="pl-db-"), "pl.db")
    eng = create_engine(f"sqlite:///{path}")
    md = MetaData()  # tabele jak w polskim CRM: te same kolumny, bez wymaganych pól tylko z CRM LT (np. market)
    for name in ("leads", "stage_history", "lead_event_dates"):
        Table(name, md, *[Column(c.name, c.type, primary_key=c.primary_key, nullable=c.primary_key is False)
                          for c in crm.db.metadata.tables[name].columns])
    md.create_all(eng)
    monkeypatch.setenv("PL_DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setattr(pl_move, "_engine", None)
    yield eng
    pl_move._engine = None


def won_lt(company, email, won_at):
    lead = crm.Lead(company=company, stage="Wygrany", assigned_to=1, currency="EUR", value_pln=2100, market="lt",
                    contact_email=email)
    crm.db.session.add(lead)
    crm.db.session.commit()
    crm.db.session.add(crm.StageHistory(lead_id=lead.id, from_stage="Oferta", to_stage="Wygrany", changed_at=won_at))
    crm.db.session.commit()
    return lead


def pl_win_dates(eng, pl_id):
    with eng.connect() as c:
        t = pl_move._meta(eng).tables["stage_history"]
        return [r.changed_at for r in c.execute(select(t.c.changed_at).where(t.c.lead_id == pl_id, t.c.to_stage == "Wygrany"))]


def test_fix_and_sync_win_dates(app, pl_db):
    marked = datetime(2026, 11, 4, 10, 0)  # w CRM LT oznaczone jako wygrane dopiero 04.11
    galley = won_lt("Galley Group Lithuania", "kamile@galley.lt", marked)
    laima = won_lt("Lead litwa", "laima.mikalajuniene@panko.lt", marked)
    grey = won_lt("GREY Vilnius", "svidrashome@gmail.com", datetime(2026, 11, 7, 16, 0))
    crm.set_setting(crm.WIN_DATE_FIX_KEY, "")  # jak przed wdrożeniem
    crm.db.session.commit()

    done, errors = crm.sync_won_to_pl()
    assert not errors and done >= 3  # w bazie testów mogą być też wygrane z innych testów
    for lead in (galley, laima, grey):
        crm.db.session.refresh(lead)
        assert lead.pl_copy_id
    assert crm.to_local(crm._closing_entry(galley).changed_at).date() == date(2026, 10, 30)
    assert crm.to_local(crm._closing_entry(laima).changed_at).date() == date(2026, 7, 23)
    assert crm._closing_entry(grey).changed_at == datetime(2026, 11, 7, 16, 0)  # GREY bez zmian — wygrany w listopadzie
    assert [d.date() for d in pl_win_dates(pl_db, galley.pl_copy_id)] == [date(2026, 10, 30)]
    assert [d.date() for d in pl_win_dates(pl_db, laima.pl_copy_id)] == [date(2026, 7, 23)]
    assert crm.apply_win_date_fixes() is None  # jednorazowo

    # później ktoś poprawia „Wygrany dnia” w CRM LT — kopia w PL dostaje nową datę przy następnej synchronizacji
    assert crm.set_lead_closed_on(grey, date(2026, 11, 6), 1) is None
    crm.db.session.commit()
    done, errors = crm.sync_won_to_pl()
    assert not errors and done == 1
    assert [d.date() for d in pl_win_dates(pl_db, grey.pl_copy_id)] == [date(2026, 11, 6)]
    assert crm.sync_won_to_pl() == (0, [])  # bez zmian — bez ponownej wysyłki
