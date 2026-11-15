# Porządki w kodzie CRM (PL i LT/DE) — plan i stan

Cel: **jeden kod w jednym repozytorium**, wdrażany do dwóch serwisów Railway (CRM PL i CRM LT/DE).
Bazy danych i zmienne zostają osobne, kod jest wspólny.

## Zasady (zawsze)

- Małe kroki, każdy osobno do cofnięcia. Porządki nie mieszają się z nowymi funkcjami.
- W bazie niczego nie usuwamy (tylko dodawanie kolumn). Kod usuwamy tylko wtedy, gdy na pewno nic go nie używa.
- Przed i po każdym kroku: pełne testy obu CRM i `tests/test_pages_open.py` (każda strona się otwiera, bez
  internetu i bez wysyłki).
- Pliki wspólne są identyczne w obu repozytoriach — zmiana w jednym = ta sama zmiana w drugim (sprawdzić `md5sum`).

## Stan (15.11.2026)

**Wspólne pliki** (identyczne w Moja-apka i Crm-litwa): `sales_rules.py`, `health.py` (strażnik CRM),
`logistics.py`, `poczta.py`; testy: `test_sales_rules.py`, `test_health_rules.py`, `test_health_crm.py`,
`test_mail_task_check.py`, `test_pages_open.py`, `test_ai_forced_tool.py`.

**Usunięte jako martwe:** `app.py` (stary planer stolików, nic go nie uruchamiało), `render.yaml` (Render — nie
używany), nieużywane funkcje `_setting_number`, `in_cur`, `logistics.daterange`, wpis „granica” w tabeli miast.

**ClickUp — 3 listy, 3 importy:**
- lista polska → CRM PL (`CLICKUP_API_TOKEN`, `CLICKUP_LIST_ID` w serwisie PL);
- lista litewska → CRM LT/DE (`CLICKUP_API_TOKEN`, `CLICKUP_LIST_ID` w serwisie LT/DE);
- lista niemiecka → CRM, w którego ustawieniach jest `CLICKUP_API_TOKEN_DE` (listę znajduje sam; opcjonalnie
  `CLICKUP_LIST_ID_DE`). Docelowo **CRM LT/DE**. Przejściowo klucz jest w serwisie PL — wtedy PL importuje i
  przenoszenie z PL oddaje leady do CRM DE. Gdyby klucz był w obu serwisach naraz, przenoszenie z PL rozpozna
  zadanie ClickUp, które już jest w LT/DE, i usunie duplikat w PL (bez drugiej kopii).
- Poczta (kontakt@, skrzynki) to osobna droga: zapytania z maili, nie z ClickUp.

**Duplikacja `crm.py`** (PL ↔ LT/DE, pomiar 15.11): 319 identycznych funkcji (4,8 tys. linii), 149 różnych
(ok. 4,4 tys.), 60 tylko w PL (ok. 1 tys.: robot / ElevenLabs, koszty z paragonów, inFakt, ClickUp DE),
103 tylko w LT/DE (ok. 1,5 tys.: rynki, tłumaczenia, przenoszenie z PL, maile LT, Viber).

## Kolejne etapy

1. **Wspólne moduły z `crm.py`** — najpierw tematy, w których funkcje są już identyczne: import ClickUp,
   synchronizacja poczty, przegląd poczty i sprawy, Customer Journey, oferty, asystent i konektor. Każdy moduł
   ten sam plik w obu repozytoriach; `crm.py` się kurczy.
2. **Ujednolicenie różnic** — to, czym PL różni się od LT/DE, jako ustawienia rynku (`market.py`), nie osobny kod.
   PL = rynek „pl”, funkcje tylko-PL (robot, inFakt) włączane ustawieniem serwisu.
3. **Jedno repozytorium** — serwis CRM LT/DE w Railway przepięty na repozytorium Moja-apka (zmienna rynków,
   np. `CRM_MARKETS=lt,de`, w PL `pl`). Najpierw próbny serwis Railway na kopii bazy, potem przełączenie.
   Crm-litwa zostaje jako archiwum.
4. **Po przeniesieniu klucza ClickUp DE do LT/DE** — usunąć z CRM PL import niemieckiej listy.

## Do zrobienia przez zespół (Railway)

- Przenieść `CLICKUP_API_TOKEN_DE` (i `CLICKUP_LIST_ID_DE`, jeśli jest) z serwisu **CRM PL** do serwisu
  **CRM LT/DE**: dodać w LT/DE, potem usunąć w PL. Strona „Stan CRM” w LT/DE pokaże „Import niemieckich
  zgłoszeń z ClickUp: OK”.
- Kopie zapasowe obu baz przed etapem 3.
