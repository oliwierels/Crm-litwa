# Porządki w kodzie CRM (PL i LT/DE) — plan i stan

Cel: **jeden kod w jednym repozytorium**, wdrażany do dwóch serwisów Railway (CRM PL i CRM LT/DE).
Bazy danych i zmienne zostają osobne, kod jest wspólny.

## Zasady (zawsze)

- Małe kroki, każdy osobno do cofnięcia. Porządki nie mieszają się z nowymi funkcjami.
- W bazie niczego nie usuwamy (tylko dodawanie kolumn). Kod usuwamy tylko wtedy, gdy na pewno nic go nie używa.
- Przed i po każdym kroku: pełne testy obu CRM i `tests/test_pages_open.py` (każda strona się otwiera, bez
  internetu i bez wysyłki).
- Pliki wspólne są identyczne w obu repozytoriach — zmiana w jednym = ta sama zmiana w drugim (sprawdzić `md5sum`).

## Stan (23.11.2026)

**Rynki w kodzie (R1).** `market.py` (identyczny w obu repozytoriach) zna trzy rynki: `pl`, `lt`, `de` — język,
waluta, domena i skrzynki, teksty do klienta, ustawienia (np. `auto_send`, `translate_mail`, `contract_vat`,
`contractors`, `offer_second_step`). Serwis włącza swoje rynki zmienną **`CRM_MARKETS`** (Railway: CRM PL `pl`,
CRM LT/DE `lt,de`). Kod nie pyta „czy to CRM PL”, tylko o ustawienie rynku leada.

**Wspólne modele i baza (R2a).** Te same modele i to samo `ensure_schema` w obu CRM: każdy lead, mail, sprawa
z poczty, kampania i zapytanie ma rynek. Migracja tylko dodaje kolumny i uzupełnia rynek starych wierszy
(sprawdzane na kopii bazy założonej kodem z produkcji — na SQLite i na Postgresie).

**Wspólne funkcje (R2b–e).** Waluty, kraj, marka, podpis maila, Poczta (skrzynki rynku, zapis maili, przegląd
poczty przez Claude osobno dla każdego rynku, tłumaczenie włączane ustawieniem rynku), alarm nowych leadów
(jedna reguła: nieodebrany telefon to jeszcze nie kontakt), statystyki zamkniętych leadów, Customer Journey
(umowa, maile, instrukcja bazy wiedzy według rynku leada; wybór wykonawcy Bartosz / Oliwier tylko na rynku
z `contractors`).

**Wspólne pliki** (identyczne w Moja-apka i Crm-litwa): `market.py`, `journey.py`, `robot_agent.py`,
`sales_rules.py`, `health.py`, `team.py`, `logistics.py`, `poczta.py`, **cały katalog `parts/`**, `static/docs/`
(instrukcje bazy wiedzy PL / LT / DE), CI `.github/workflows/testy.yml`; wspólne testy m.in. `test_mail_market.py`,
`test_mail_task_check.py`, `test_pages_open.py`, `test_next_step.py`, `test_daily_report.py`.

**`parts/` — wspólne części `crm.py`:** `crm.py` wczytuje każdą część przez `_include("nazwa")` w miejscu, w którym
kod był wcześniej, w tej samej przestrzeni nazw. Jak zmieniać część: edytować plik w `parts/` i skopiować go do
drugiego repozytorium (`diff -rq parts ../…/parts`).

**Duplikacja `crm.py`** (pomiar 23.11, funkcje najwyższego poziomu): 198 identycznych (3,0 tys. linii; na starcie
R2: 105), 74 różnych (ok. 2,7 tys.), 52 tylko w PL (ok. 0,9 tys.: robot / ElevenLabs, koszty z paragonów, inFakt,
lista do obdzwonienia), 77 tylko w LT/DE (ok. 1,2 tys.: przenoszenie z PL, wyceny i maile LT, WhatsApp / Viber,
tłumaczenie interfejsu).

**Testy i CI:** każdy push uruchamia pełne testy dwa razy — na SQLite i na Postgresie 16 (jak produkcja;
`TEST_DATABASE_URL` w `tests/conftest.py`).

**Usunięte jako martwe:** `app.py`, `render.yaml`, nieużywane funkcje `_setting_number`, `in_cur`,
`logistics.daterange`, wpis „granica” w tabeli miast.

**ClickUp — 3 listy, 3 importy:** polska → CRM PL; litewska i niemiecka → CRM LT/DE (`CLICKUP_API_TOKEN_DE` +
`CLICKUP_LIST_ID_DE` w serwisie LT/DE; zgłoszenia sprzed podłączenia nie stają się leadami). Poczta (kontakt@,
skrzynki) to osobna droga: zapytania z maili.

## Kolejne etapy

1. **R2 dalej** — ujednolicić pozostałe różne funkcje tematami (ClickUp, oferty i pisanie maili, asystent,
   strażnik, inFakt i faktury, statystyki) i dołożyć do drugiego CRM funkcje jednostronne, uśpione ustawieniem rynku
   albo brakiem klucza (robot, koszty, WhatsApp / Viber, przenoszenie z PL). Cel: oba `crm.py` identyczne.
2. **R3** — wspólne szablony, `pricing.py`, `offers.py`.
3. **R4 — jedno repozytorium**: serwis CRM LT/DE w Railway przepięty na Moja-apka (`CRM_MARKETS=lt,de` już
   ustawione), testy w CI dla obu ustawień rynków; potem `parts/` jako zwykłe moduły zamiast `exec`.
   Crm-litwa zostaje jako archiwum.

## Do zrobienia przez zespół (Railway)

- Kopie zapasowe obu baz przed etapem 3.
