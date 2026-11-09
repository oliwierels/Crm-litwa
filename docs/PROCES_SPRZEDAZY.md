# Proces sprzedaży 33Bots w CRM (P1–P3, listopad 2026)

Reguły są w `sales_rules.py`. To ten sam plik w CRM PL (repo Moja-apka) i CRM LT/DE (repo Crm-litwa).
CRM stosuje je jednakowo w interfejsie, w API (`/crm/api/...`) i w narzędziach MCP.

## Krok 1 — rozpoznanie (skrót)

| # | Pytanie | Odpowiedź | Gdzie |
|---|---|---|---|
| 1 | Stos i wdrożenie | Flask + SQLAlchemy, PostgreSQL. Hosting na Railway (Nixpacks, gunicorn), wdrożenie automatycznie z gałęzi `main`. CRM LT/DE to kopia kodu PL z przełącznikiem rynku (`market.py`: lt/de, EUR, inne skrzynki i teksty). Narzędzia MCP są w obu CRM (`/mcp/<MCP_SECRET>/<imię>`). Zmianę można sprawdzić lokalnie testami (`python -m pytest tests`) i na środowisku testowym Railway. | `crm.py`, `railway.json`, `Procfile` |
| 2 | Model leada | Etapy w `STAGES` (Nowy, Oferta, Wygrany, Zrealizowany, Stracony). Zmiana etapu idzie przez `record_stage_change` z historią w `stage_history`. `closed_on` wynika z historii (`lead_closed_on`, `set_lead_closed_on`). | `crm.py` |
| 3 | Automatyzacje | Opis w tabeli poniżej. | `crm.py` |
| 4 | Typ klienta i kraj | Przed zmianami nie było takich pól. `quality` to ocena leada (Dobry/Średni/Słaby). Kraj był tylko w danych do faktury (`billing_country`). Teraz są pola `client_type` i `country`. | `crm.py` |
| 5 | Zadania i kontakt | Zadania (`activities`) powstają ręcznie, przez `add_task` albo automatycznie: telefon po ofercie, follow-up mailem, „Zadzwonić ponownie”. Wcześniej wynik zapisywał tylko przycisk „Nie odebrał” (komentarz 📵). Teraz jest pole `outcome` i licznik prób. | `crm.py` |
| 6 | Czas reakcji | Moment wpłynięcia zapytania to `created_at`, pierwsza odpowiedź to `handled_at` (okno nowych leadów). Teraz liczony jest czas w minutach pracy. | `crm.py` |
| 7 | Oferty | `prepare_offer` robi szkic PDF, wysyłka idzie przez `deliver_offer`. Wartość leada ustawia się z ceny w PDF tylko przy ofercie wysłanej z CRM. VULCAN miał ofertę wysłaną poza CRM, z kwotą tylko w notatce. Teraz Wygrany ostrzega o rozbieżności, a raport Porządki ją pokazuje. `quote_price` liczy z cennika w `pricing.py`. | `crm.py`, `pricing.py` |
| 8 | Umowy | `contract_signed` ustawia Customer Journey (`journey_set_contract`): umowa odesłana mailem, przycisk albo ręcznie. Świeża wygrana zerowała ją. Teraz „umowa podpisana” przy Wygrany zostaje. | `crm.py`, `journey.py` |
| 9 | Maile | SMTP z `SMTP_HOST`, skrzynki osób `SMTP_PASS_<IMIĘ>`. Wysłane trafiają do Poczty (folder Wysłane) i jako notatki przy leadzie. SPF/DKIM/DMARC sprawdza strona „Dostarczalność” (DNS na żywo). | `crm.py` |
| 10 | Statystyki | „Ostatnie 12 miesięcy”, `closed_leads` i panel skuteczności liczą z `closed_lead_events()`. `monthly_revenue` liczy wg miesiąca wynajmu. Nowe wskaźniki korzystają z tych samych funkcji. | `crm.py` |
| 11 | Daty eventów | Kalendarz w formularzu (`event_dates`), MCP (`event_dates`), formularz klienta. Rok 0002 brał się z literówek w starych `event_date`, a przy starcie aplikacji backfill rozpisywał zakres do 366 dni. Teraz pomija absurdalne daty i nie rozpisuje więcej niż 62 dni. | `crm.py` |
| 12 | Uprawnienia i testy | Etap może zmienić każdy zalogowany. Nie ma ról, więc nie ma wyjątku dla admina. Automaty CRM nie są blokowane, a `STAGE_GATES=0` wyłącza bramki awaryjnie. Testy: `python -m pytest tests`. | `tests/` |

### Mapa automatyzacji

| Automat | Co go uruchamia | Gdzie / konfiguracja |
|---|---|---|
| Lead z maila na kontakt@ (AI czyta zapytanie) | co kilka minut, skrzynka kontakt@ | `_inbox_scheduler`; zmienne `INBOX_*` |
| Leady z ClickUp | co kilka minut | `_clickup_scheduler`; `CLICKUP_API_TOKEN`, `CLICKUP_LIST_ID` (Litwa) i `CLICKUP_LIST_ID_DE` (Niemcy — świeże od razu na tablicę, źródło „ClickUp DE”) |
| Telefon po ofercie (teraz: telefon potwierdzający) | wysłana oferta albo etap Oferta | `ensure_followup_call` |
| Follow-up mailem po ofercie (zadanie) | wysłana oferta (3 dni) | `deliver_offer` |
| Customer Journey: umowa, przypomnienia, dokumenty z maili | etap Wygrany; skan skrzynek co 5 min | `ensure_journeys`, `_journey_scheduler`; `JOURNEY_SCAN` |
| Wygrany → Zrealizowany po ostatnim dniu | co 10 min | `auto_realize_leads`; `AUTO_REALIZE` |
| Scalanie duplikatów | co 10 min | `auto_merge_duplicates` |
| Okno nowych leadów (obsłużyć dziś) i przegląd poczty przez Claude 12:00 i 15:00 | każda strona; harmonogram | `alerts_state`, `mail_review`; `MAIL_REVIEW_HOURS` |
| Raport dzienny e-mail (do zespołu) | codziennie | `_report_scheduler` |
| Kampanie (Blitzkrieg) | ręczny start kampanii | `_campaign_scheduler` |
| Mail LT z formularzem | **wyłączony** (`LT_AUTO_MAIL=1` go włącza); litewskie leady obsługuje CRM LT | `lt_auto_mail` |
| Mail DE z linkiem do terminu rozmowy | nie znaleziono w kodzie żadnego CRM (brak linku do kalendarza) — CRM DE nic nie wysyła sam; oferty DE generuje człowiek | CRM LT/DE, `market.py` |
| **Nowe (P2):** szkic i telefon dla osób prywatnych, telefon po odpowiedzi klienta, „Zdecyduj” po 3 próbach, zapis przekroczenia czasu reakcji | co 10 min | `sales_automation_pass`; `SALES_AUTOMATION=0` je wyłącza. Tylko zadania i szkice, **nic nie wysyła**. |

## Krok 2 — decyzje

Przyjęte według propozycji z briefu. Zmiana decyzji to zmiana stałej w `sales_rules.py`.

1. **Powody straty:**
   - cena lub budżet,
   - wybrali innego dostawcę,
   - brak decyzji (np. klienta agencji),
   - brak kontaktu,
   - odrzuciliśmy sami (słaby klient),
   - termin zajęty lub brak robota,
   - inne (komentarz obowiązkowy).
2. **Wygrany:**
   - umowa podpisana **albo** pisemne potwierdzenie mailem, do wyboru przy zmianie etapu,
   - do tego dzień wynajmu od 2025 r., wartość > 0 i typ klienta,
   - wartość różna od ostatniej oferty daje ostrzeżenie do potwierdzenia,
   - brak zaliczki bez zmian.
3. **Typ klienta:** firma/instytucja, osoba prywatna, agencja eventowa, firma robotyczna/partner. Wymagany najpóźniej przy wysłaniu oferty.
4. **Telefon potwierdzający:**
   - następny dzień roboczy po ofercie,
   - dla firm, agencji i partnerów oraz każdej oferty od 10 000 zł (EUR po kursie),
   - dzwonimy pn–pt 9:00–17:00,
   - 4 pytania; odpowiedzi zapisują się w leadzie.
5. **Limit prób:** 3 nieudane z rzędu (osoby prywatne: 2). Liczą się telefon nieodebrany oraz SMS lub mail bez odpowiedzi. Po limicie powstaje zadanie „⚖️ Zdecyduj” dla opiekuna, bez automatycznej straty.
6. **Czas reakcji:** cel 30 min w godzinach pracy pn–pt 9:00–18:00 (zapytanie po 18:00 liczy się od 9:00 następnego dnia roboczego). Alert w oknie nowych leadów widzą wszyscy.
7. **Osoby prywatne:**
   - szkic do zatwierdzenia, nie wysyłany sam,
   - pakiet 2 h z ceną z kalkulatora albo „2 h od 2 750 zł netto + dojazd”,
   - pytanie o budżet,
   - telefon od razu (najwyżej 2 próby) i ponownie, gdy klient odpisze.
8. **Dopłata za pilne zlecenia:** na razie nie, bo to zmiana cennika dla klientów.
9. **Panel skuteczności:**
   - widzą wszyscy,
   - cele i kurs EUR są edytowalne na stronie panelu,
   - kurs EUR pobierany jest codziennie z NBP,
   - panel łączy CRM PL i wygrane z CRM LT/DE (ich kopie w bazie PL).
10. **Test A/B telefonu:** na razie nie.
11. **Porządki w danych:** raport do ręcznej poprawy, CRM niczego sam nie nadpisuje. Zmiany idą w obu CRM.

## Co zmienia się w codziennej pracy (do wiki)

1. **Stracony = powód.** Przy przeciągnięciu na Stracony CRM pyta o powód, a przy „inne” o komentarz. Bez powodu lead nie zmieni etapu (w MCP też).
2. **Wygrany = umowa albo pisemne potwierdzenie mailem, data wydarzenia i kwota.** Bez nich lead nie wejdzie na Wygrany. Gdy kwota różni się od wysłanej oferty, CRM pyta, czy to na pewno dobrze.
3. **Typ klienta przed ofertą.** Firma, osoba prywatna, agencja albo partner. CRM podpowiada typ, ale bez wybranego typu oferta nie wyjdzie.
4. **Dzień po ofercie dzwonimy** do firm, agencji i partnerów oraz przy ofertach od 10 000 zł. Zadanie ma 4 pytania: czy dotarła, kiedy decyzja, kto decyduje, z kim nas porównują. Odpowiedzi wpisujemy przy odhaczaniu.
5. **Każdy telefon, SMS i mail odhaczamy z wynikiem:** odebrał / nie odebrał albo odpisał / bez odpowiedzi. Po 3 nieudanych próbach z rzędu CRM daje zadanie „⚖️ Zdecyduj”: zamykamy jako Stracony (brak kontaktu) albo zmieniamy kanał.
6. **Nowe zapytanie odpowiadamy w 30 minut pracy** (pn–pt 9–18; zapytanie z wieczora liczy się od 9:00). Okno nowych leadów pokazuje, ile lead czeka. Po przekroczeniu robi się czerwone.
7. **Osoby prywatne:** CRM sam przygotowuje szkic pierwszej odpowiedzi (krótki pakiet, cena, pytanie o budżet) i zadanie telefonu. Szkic sprawdzamy i wysyłamy z Poczty, dzwonimy najwyżej 2 razy.
8. **Codziennie od 16:00 CRM pyta na każdym komputerze: „Czy ktoś do Ciebie oddzwaniał?”** Wpisujemy numer — rozmowa zapisuje się przy leadzie (i zeruje nieodebrane próby) — albo „Nikt nie oddzwaniał”.
9. **Daty:** nie da się zapisać daty sprzed 2025 r. Przy zakresie powyżej 14 dni CRM ostrzega.
10. **Raporty → Skuteczność** pokazuje wynik miesiąca na tle celów z prognozą, skuteczność typów i osób, powody strat, czas reakcji, anulacje i duże leady bez następnego kroku. **Porządki w danych** to lista do poprawy. **Dostarczalność** to stan SPF, DKIM i DMARC.
11. **Raporty → Audyt ofert:** każda oferta z ostatnich 45 dni ma mieć drugi krok (follow-up mailem). Lead, z którym po ofercie był już kontakt (follow-up, telefon, SMS, WhatsApp, mail z Poczty albo odpowiedź klienta), drugiego kroku nie dostaje — w raporcie ma „kontakt po ofercie”.
12. **Customer Journey:** „Przypomnij” przy umowie i bazie wiedzy jest dostępne od razu po wysłaniu (szary przycisk). Po terminie przypomnienia przycisk robi się niebieski i trafia do „Do zrobienia”. Zawsze to tylko szkic maila do akceptu.
13. **Sprawy z poczty (okno od 16:00):** zanim sprawa trafi na listę, Claude szuka w poczcie (wszystkie skrzynki i wątki z klientem) i w CRM, czy to już nie jest zrobione — np. „wyślij ofertę”, a oferta poszła osobnym mailem. Zrobione zamyka z dowodem, potem sprawdza otwarte sprawy co godzinę, gdy coś się zmieni. Zamknięte widać w Poczcie („Zamknięte po sprawdzeniu poczty”) z przyciskiem „↩️ Przywróć”. „👤 Otwórz lead” znajduje leada po adresie klienta.

## Pytanie „Czy ktoś oddzwaniał?”

Raz dziennie, w dni robocze od 16:00, na każdym komputerze osobno: CRM rozpoznaje komputer po ciasteczku przeglądarki, nie po adresie IP. Trzy osoby w jednym biurze mają zwykle ten sam adres IP, więc IP by ich nie rozróżniło. Odpowiedzi do wyboru: numer telefonu (zapis jako odebrany telefon przy leadzie), „Nikt nie oddzwaniał” (do jutra) albo × (pytanie wraca za godzinę).
