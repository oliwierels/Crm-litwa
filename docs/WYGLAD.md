# Wygląd CRM (wspólny dla CRM PL i CRM LT/DE)

## Zasady

- **Firmowy ciemny motyw domyślnie** (barwy z ofert PDF 33bots: tło #0A0C10, panele #15171C, akcent #2F6FE0 / #7FA8F5,
  nagłówki Poppins), jasny jednym kliknięciem: ☀ / 🌙 na pasku u góry (albo w menu konta); wybór zostaje w przeglądarce.
- **Menu: jedna pozycja na sekcję** (Mój dzień, Leady, Poczta, Oferty, Customer Journey, Kalendarz, Logistyka, Kampanie,
  Zespół, Raporty, System); strony sekcji są zakładkami u góry strony (np. Leady: Tablica / Lista / Do obdzwonienia /
  Z ClickUp / Duplikaty).
- **Szukajka Ctrl+K** (albo „/”): leady po firmie, osobie, telefonie, mailu + strony + akcje.
- **Dzwonek** 🔔: przypomnienia, które nie blokują pracy (raport dnia, strażnik, „czy ktoś oddzwaniał?”, leady czekające
  u innych). Blokują: nowy lead u jego opiekuna i raport dnia — brak raportu z poprzednich dni od razu, a dzisiejszego:
  od 15:00 okno „Pamiętaj o raporcie dnia” (do odłożenia na 30 min) + jeden mail przypomnienia, od 17:00 blokada CRM
  (`DAY_REPORT_BLOCK_HOUR`), dopóki raport nie jest złożony.
- **Wygrana** 🏆 (`static/wygrana.js`): przejście leada na „Wygrany” (tablica, nagłówek, edycja) to pokaz jak główna
  wygrana w kasynie (klimat i grafika kasyna, nazwy nasze): szkolny dzwonek „brrrr” i trzęsienie ziemi strony →
  WYGRANA! (wybuch i fontanna monet, licznik kwoty, światełka dookoła) → „JERZY ZAMYKA TEMAT!” (imię osoby) →
  „TEMAT ZAMKNIĘTY!” (armata pieniędzy, fajerwerki, fanfara) → tańczący robot, samoloty z banerami, smok i lampiony,
  oklaski oraz która to wygrana w miesiącu, suma miesiąca i „REKORD MIESIĄCA”. Dźwięk (syntezowany, bez plików)
  wyłącza 🔊 w rogu; Esc / „Dawaj następnego! 🚀” zamyka; przy ograniczonych animacjach w systemie — spokojna karta.
  Reszta zespołu dostaje powiadomienie w dzwonku i ten sam pokaz przy następnym wejściu do CRM.
- **Leady z poczty** ✦: lead, który sam napisał (źródło „Formularz …”, „Mail kontakt@…”, „Mail”), ma na tablicy złotą
  ramkę z poświatą i plakietkę FORMULARZ / MAIL; na liście złoty pasek, w nagłówku leada złote źródło. Przycisk
  „✦ Z poczty” nad tablicą pokazuje tylko takie leady (`is_direct_lead` w `parts/wyglad.py`).
- **Kwoty w kilku walutach** na kafelkach: waluta rynku duża, pozostałe małą linią pod spodem (`money_parts`).
- **Statystyki użycia stron** (System → Stan CRM): każde otwarcie strony; strony z zerem po kilku tygodniach to kandydaci
  do usunięcia.
- **Kolory tylko ze zmiennych** w `templates/crm/base.html` (`--bg`, `--panel`, `--panel2`, `--border`, `--text`, `--muted`,
  `--accent`, `--green`, `--yellow`, `--red`, `--blue`, `--purple` i wersje `-soft` na tła). Nowe ekrany nie wpisują kolorów
  na sztywno — wtedy działają w obu motywach.
- **Bez zewnętrznych CDN**: ikony (Bootstrap Icons), czcionka (Inter) i kalendarz (FullCalendar) są w `static/`.
- **Jedno miejsce na tworzenie**: „Nowy lead” i „Zadanie” są na górze menu bocznego (klawisz N = nowy lead). Pływający
  przycisk w rogu to tylko asystent.
- **Zalogowana osoba** jest na dole menu. „Zmień osobę” wylogowuje (przy haśle CRM) — nie da się już kliknięciem udawać
  kogoś innego. Nowe zadanie domyślnie przypisuje się do zalogowanej osoby.

## Karta leada

1. **Nagłówek** (`_lead_header.html`): firma, ocena, źródło, aktywność; etap jako pasek (klik = zmiana etapu z bramkami);
   opiekun (zmiana przenosi jego otwarte zadania i zostawia komentarz „🔁 Opiekun: …”), kontakt, wartość, wydarzenie
   i następny krok. Szybkie akcje: Zadzwoń, Napisz, Oferta, Wyceń.
2. **Szeroka kolumna pracy**: dodaj zadanie → do zrobienia → komentarze → korespondencja → zrobione → historia etapów.
3. **Panel boczny**: szczegóły, proces sprzedaży, oferta z Claude, oferty, Customer Journey, dane do faktury (zwinięte).

## Alarmy

- Okno „nowy lead do obsłużenia dziś” widzi **opiekun leada** (lead bez opiekuna — każdy). W CRM PL nowe leady ma Jerzy,
  więc pozostałych nie blokują cudze leady.
- Paski (raport dnia, strażnik) mają jeden styl `.notice` (`notice-warn`, `notice-danger`).

## Wspólne pliki szablonów

Te pliki są identyczne w obu repozytoriach (różnice rynku robią warunki: `all_mkts|length > 1`, `has_route(...)`,
`ui_langs is defined`, `mkt.currency`): `base.html`, `_alerts.html`, `_callback.html`, `_raport_gate.html`, `_health.html`,
`_lead_header.html`, `dashboard.html`, `leads.html`, `login.html`. Tłumaczenia interfejsu LT: `lt_strings.py` (CRM LT/DE).

## Silnik porządku (`parts/silnik_porzadku.py`)

- Co 2 minuty (po synchronizacji poczty) każdy **nowy mail klienta przypisany do leada** czyta Claude:
  akceptacja / prośba o umowę (pewna) → lead na **Wygrany** (bramki jak u człowieka: data, kwota; potwierdzenie „mail”),
  przy brakach — powiadomienie, czego brakuje; rezygnacja → powiadomienie z propozycją „Stracony” (zamyka człowiek);
  pytanie → „klient czeka na odpowiedź”. Stracony lead, który pisze → „odezwał się”.
- Zadania „pogoni” (follow-up, przypomnienie, telefon po ofercie) sprzed maila klienta stają się nieaktualne.
- Co 10 minut: wygrany, zrealizowany i stracony lead nie ma otwartych zadań sprzed zamknięcia.
- **Nic nie znika**: zadanie od człowieka zamienia się w komentarz „🧹 Nieaktualne — powód: …”; automatyczne
  przypomnienie (bez autora) jest usuwane, a przy leadzie zostaje komentarz, ile zadań i dlaczego. Leadów silnik nie usuwa.
- Każda decyzja = komentarz przy leadzie + powiadomienie w dzwonku (sekcja „Co się wydarzyło”).
- Jednorazowo po wdrożeniu (24.11.2026, `ONE_TASK_KEY`): lead z kilkoma otwartymi zadaniami po dawnych automatach
  zostaje z jednym — od człowieka przed automatycznym, z najnowszej partii, w partii: „Zdecyduj” → szkic pierwszej
  odpowiedzi → telefon → mail. Treść zamkniętych zadań zostaje w komentarzu przy leadzie. To nie jest stała reguła.
