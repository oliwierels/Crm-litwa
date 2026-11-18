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
`team.py` (rangi i raport dnia), `logistics.py`, `poczta.py`, **cały katalog `parts/`**; testy: `test_sales_rules.py`,
`test_health_rules.py`, `test_health_crm.py`, `test_mail_task_check.py`, `test_pages_open.py`,
`test_ai_forced_tool.py`, `test_team_rules.py`, `test_team_reports.py`; szablony zespołu: `raport_dnia.html`,
`zespol.html`, `_raport_gate.html`.

**`parts/` — wspólne części `crm.py` (39 plików tematycznych, ok. 4,5 tys. linii):** modele, logowanie, bramki
etapów, follow-upy, automaty, czujniki i strona strażnika, daty wydarzeń, raport dzienny, ClickUp (mapowanie,
import), użytkownicy, statystyki, próby kontaktu, zadania, karta leada, audyt ofert, dostarczalność, duplikaty,
logistyka (plan i strony), Customer Journey (stan, umowa, dokumenty, poczta, strony), poczta wysłana, oferty,
asystent, konektor MCP, sprawy z poczty. `crm.py` wczytuje każdą część przez `_include("nazwa")` dokładnie
w miejscu, w którym kod był wcześniej, w tej samej przestrzeni nazw — zachowanie bez zmian (sprawdzone odciskiem:
nazwy, adresy stron, kolejność obsługi zapytań, tabele — identyczne przed i po). `crm.py`: PL 12,8 → 8,3 tys.
linii, LT/DE 13,4 → 8,9 tys. W `crm.py` zostaje to, co się (jeszcze) różni między CRM-ami.

Jak zmieniać część: edytować plik w `parts/` i skopiować go do drugiego repozytorium (`diff -rq parts ../…/parts`).
Kolejne części powstają tak samo: funkcja ujednolicona w obu `crm.py` → przeniesiona do `parts/`.

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

1. **Wspólne części z `crm.py`** — zrobione dla wszystkich identycznych bloków (`parts/`). Dalej: ujednolicać
   funkcje, które różnią się drobiazgami (np. `sync_inbox`, `mail_ai_draft`, `_assistant_execute`, `mail_boxes`,
   `health_jobs`), i przenosić je do `parts/`.
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
