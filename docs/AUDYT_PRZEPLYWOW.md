# Audyt przepływów danych CRM (PL i LT/DE) — 23.11.2026

Cel: żaden lead nie ginie, żaden mail nie idzie do złej osoby albo ze złą treścią, CRM nie usuwa danych sam z siebie.
Każdy punkt: co sprawdzono → wynik → co pilnuje tego w kodzie (i test).

## 1. Wejście leadów

| Przepływ | Wynik | Pilnuje |
|---|---|---|
| Formularz na stronie (Formspree → kontakt@) | ✅ lead albo dopisanie do istniejącego; adres klienta z treści formularza, nigdy `noreply@formspree.io` | `process_inbox_message` + `parts/formularze.py` (`test_inbox_form_never_stores_machine_address`) |
| Mail od klienta na kontakt@ | ✅ ostatnie 14 dni: pominięte tylko spam, phishing i przypomnienie o płatności od dostawcy | import zapytań (Claude), status w „Skrzynka” |
| Zapytania z zagranicy w CRM PL (DE / LT) | ✅ przechodzą do CRM LT/DE (np. TECHCONSULT, Vilniaus paukštynas — są tam na etapie Oferta) | przenoszenie rynków |
| ClickUp PL / LT / DE | ✅ import działa (strażnik); LT: od 18.11 brak nowych wpisów w samym ClickUp (źródło: kampania / integracja Meta) | strażnik „Nowe leady LT: cisza” |
| Duplikaty | ✅ automatycznie scalane tylko nietknięte kopie z importów; wszystko przechodzi do głównego leada; **poprawka**: inne daty z kopii zapisują się w komentarzu | `parts/duplikaty_scalanie.py` (`test_merge_keeps_duplicate_dates_as_note`) |

## 2. Wyjście maili (wszystkie ścieżki: Poczta, oferty, Customer Journey, Blitzkrieg, maile LT, raporty)

| Ryzyko | Wynik | Pilnuje |
|---|---|---|
| Mail na adres automatu (no-reply, Formspree, mailer-daemon) | **było możliwe** („Odpowiedz” na powiadomienie formularza) → teraz wysyłka odmawia | `_smtp_send` → `outgoing_mail_problem` (`test_nothing_goes_to_machine_or_with_form_quote`) |
| Cytat powiadomienia Formspree z linkiem „wypisz się” w mailu do klienta | **poszedł 23.11 do leada #1023** → teraz odpowiedź na formularz nie cytuje powiadomienia, a wysyłka odmawia treści z takim cytatem | `parts/formularze.py`, okienko pisania (`test_send_reply_to_form_has_no_quote_and_no_thread`) |
| „Odpowiedz” na powiadomienie formularza | **adresat = Formspree, temat „Re: New submission…”** → teraz adresat = klient (lead / treść formularza), temat rynku, nowy wątek | `crm_mail_compose` (`test_reply_to_form_writes_to_client_without_quote`) |
| Odpowiedź na mail bez `thread_key` (CRM LT/DE) | **błąd zapisu po wysłaniu** → poprawione | `_mail_send_from_form` |
| Follow-upy Blitzkrieg | ✅ osobna treść dla każdego, w wątku NASZEGO maila, start dopiero gdy wszystkie napisane, odbiorca bez treści nie dostaje nic | `parts/blitzkrieg_dane.py` (`test_blitzkrieg.py`) |

## 3. Etapy i zadania

| Przepływ | Wynik | Pilnuje |
|---|---|---|
| Klient akceptuje ofertę / prosi o umowę mailem | ✅ Wygrany przez te same bramki co u człowieka (data, kwota); braki → powiadomienie; wszystko odwracalne | `parts/silnik_porzadku.py` (`test_silnik.py`) |
| Mail klienta, gdy w CRM wisi „przypomnij / follow-up” | ✅ zadanie „pogoni” sprzed maila staje się nieaktualne (komentarz, nie kasowanie) | silnik porządku |
| Wygrany / zrealizowany / stracony lead z otwartym zadaniem | ✅ co 10 min zadania sprzed zamknięcia → „🧹 Nieaktualne” | `stage_hygiene_pass` |
| Claude chwilowo nie działa | ✅ silnik nie przeskakuje maili (do 3 prób), bez klucza czeka | `mail_engine_pass` (`test_mail_engine_retries_when_claude_fails`) |

## 4. Usuwanie danych — co CRM może usunąć sam

| Miejsce | Co usuwa | Ocena |
|---|---|---|
| Scalanie duplikatu | kopię z importu (po przeniesieniu komentarzy, zadań, ofert, maili, dat) | ✅ tylko nietknięte kopie |
| Import ClickUp | nowo dodaną kopię klienta, który już jest w CRM (dane i notatka trafiają do istniejącego) | ✅ |
| Silnik porządku | automatyczne przypomnienie bez autora, bez komentarza i bez wyniku | ✅ reszta zostaje jako komentarz |
| Audyt ofert | własne automatyczne „drugie kroki” bez wyniku i bez komentarzy, gdy po ofercie był kontakt | ✅ |
| Nowa wersja oferty z Claude | poprzedni NIEWYSŁANY szkic tej oferty | ✅ |
| Leady | tylko człowiek (przycisk „Usuń”); maile, oferty i formularze zostają w historii | ✅ |

## Do zrobienia dalej
- Przypomnienia o płatnościach od dostawców (np. NPROFIT na kontakt@) nie są leadami i nie trafiają do „Spraw z poczty” —
  warto ustalić, kto je obsługuje.
- Stare szkice „pierwszej odpowiedzi” mają w treści podpis Oliwiera — przy otwarciu CRM podpisuje je osobą, która otwiera.
