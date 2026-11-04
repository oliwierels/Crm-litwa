"""Rynki CRM: Litwa (lt) i Niemcy (de) w jednej aplikacji.

Każdy lead ma swój rynek (Lead.market). Wszystko, co idzie do klienta — maile, oferta PDF, umowa,
formularz, wycena, wiadomość na WhatsApp/Viber — jest w języku rynku leada. Tutaj są teksty i ustawienia
rynków; crm.py, journey.py, offers.py i pricing.py biorą je stąd.
"""
import os

DEFAULT = "lt"


def _env(name, default):
    return os.environ.get(name, "").strip() or default


MARKETS = {
    "lt": {
        "code": "lt", "lang": "lt", "flag": "🇱🇹", "name": "Litwa", "country": "LT", "domain": "33bots.lt",
        "brand": "33bots CRM 🇱🇹", "html_lang": "lt",
        "lang_name_pl": "litewski", "lang_in_pl": "po litewsku",
        "mailboxes": "o.latek@33bots.lt,kontakt@33bots.lt",
        "own_email": "o.latek@33bots.lt", "inquiry_box": "kontakt@33bots.lt",
        "phone_codes": ("370",), "mobile_local": ("8", 9),  # krajowy zapis komórki: 8 6xx xxxxx
        "hours_unit": "val.", "date_style": "iso",
        "auto_quote_default": "1",
        "auto_send": True,  # Litwa: nowy lead od razu dostaje mail (wycena albo prośba o dane)
        "whatsapp_lang": "lt",
        # --- formularz dla klienta (/f/…) ---
        "form": {
            "title": "33Bots – renginio informacija",
            "done_h1": "Ačiū! Informaciją gavome.",
            "done_sub": "Paruošime pasiūlymą ir atsiųsime jį el. paštu kuo greičiau.",
            "h1": "Renginio informacija",
            "sub": "Trys trumpi atsakymai — ir paruošime pasiūlymą humanoidinio roboto Unitree G1 nuomai.",
            "date": "Renginio data", "multi": "Renginys trunka kelias dienas", "date_to": "Paskutinė diena",
            "place": "Miestas", "place_ph": "pvz. Vilnius", "from": "Nuo", "to": "Iki",
            "more": "Daugiau informacijos (nebūtina)", "kind": "Renginio pobūdis",
            "kind_ph": "pvz. paroda, konferencija, įmonės vakarėlis", "guests": "Numatomas svečių skaičius",
            "comment": "Pageidavimai", "send": "Siųsti",
        },
        "errors": {404: ("🔗", "Nuoroda nebegalioja", "Ši forma nebeprieinama. Parašykite mums — mielai padėsime:"),
                   500: ("🛠️", "Įvyko klaida", "Bandykite dar kartą po kelių minučių arba parašykite mums:")},
        # --- mail z prośbą o dane (formularz) ---
        "ask_subject": "33Bots – humanoidinio roboto nuoma: kelios detalės pasiūlymui",
        "ask_hello": "Laba diena,\n\ndėkojame už užklausą dėl humanoidinio roboto Unitree G1 nuomos!\n\n",
        "ask_form": "Kad galėtume paruošti tikslų pasiūlymą, prašome užpildyti trumpą formą (užtruks 30 sekundžių):\n"
                    "{link}\n\nArba tiesiog atsakykite į šį laišką ir nurodykite:\n",
        "ask_noform": "Kad galėtume paruošti tikslų pasiūlymą, prašome atsakyti į šį laišką ir nurodyti:\n",
        "ask_list": "– renginio datą (-as),\n– miestą,\n– renginio laiką (nuo–iki),\n"
                    "– renginio pobūdį ir numatomą svečių skaičių.\n\nPasiūlymą atsiųsime kuo greičiau.\n\n",
        "ask_bye": "Pagarbiai",
        # --- automatyczna wycena mailem ---
        "quote_subject": "33Bots – humanoidinio roboto nuomos pasiūlymas",
        "quote_hello": "Laba diena,",
        "quote_thanks": "dėkojame už užklausą dėl humanoidinio roboto Unitree G1 nuomos! Prisegame pasiūlymą PDF formatu.",
        "quote_fixed": ("atsiprašome – ankstesniame mūsų laiške buvo klaidinga nuoroda. Siunčiame pasiūlymą dar kartą, "
                        "šįkart su PDF priedu."),
        "quote_does": ("Ką robotas veiks jūsų renginyje:\n"
                       "– pasitiks svečius ir pakvies juos prie jūsų stendo ar scenos,\n"
                       "– bendraus su svečiais ir atsakys į jų klausimus – remdamasis jūsų įmonei paruošta žinių baze,\n"
                       "– šoks, mojuos ir pozuos bendroms nuotraukoms,\n"
                       "– taps ryškiu renginio akcentu ir puikiu turiniu socialiniams tinklams."),
        "quote_price": "Jūsų renginiui{when} ({time}, {place}) roboto nuomos kaina – {price} € be PVM.",
        "quote_included": "Į kainą įskaičiuota:\n– atvykimas ir roboto paruošimas vietoje,\n"
                          "– asmeninis operatorius visą renginio laiką,\n"
                          "– roboto asmenybės ir žinių pritaikymas jūsų renginiui,\n– techninė pagalba vietoje.",
        "quote_robot": "Robotas pasitiks svečius, bendraus remdamasis paruošta žinių baze, šoks ir pozuos nuotraukoms.",
        "quote_photos": "Nuotraukos iš mūsų renginių:",
        "quote_fix": "Jei renginio detalės kitokios (data, laikas, vieta), patikslinkite jas čia – kainą atnaujinsime:",
        "quote_reserve": ("Ar rezervuojame šią datą? Užtenka atsakyti į šį laišką – mielai atsakysiu ir į visus "
                          "klausimus.\nPasiūlymas galioja 14 dienų."),
        "offer_pdf": {
            "headline": "Humanoidinis robotas",
            "subtitle": "Unitree G1 jūsų renginyje – pasitinka svečius, bendrauja, šoka ir pozuoja nuotraukoms.",
            "cover": ("DATA", "VIETA", "LAIKAS"), "soon": "Pagal susitarimą",
            "intro": ("Unitree G1 – ryškiausias jūsų renginio akcentas: pasitinka svečius, bendrauja remdamasis jūsų "
                      "įmonei paruošta žinių baze, šoka ir pozuoja nuotraukoms. Visą laiką robotą aptarnauja mūsų "
                      "operatorius."),
            "cards": [("ROBOTO VAIDMUO", "Ką robotas darys", [
                "Pasitiks svečius prie stendo ar scenos",
                "Bendraus ir atsakys į klausimus apie jūsų įmonę",
                "Šoks ir pozuos bendroms nuotraukoms"]),
                ("PASIRUOŠIMAS", "Kaip pasiruošiame", [
                    "Pritaikome roboto asmenybę ir žinias",
                    "Atvykstame iš anksto ir viską paruošiame",
                    "Operatorius ir techninė pagalba vietoje"])],
            "price_title": "Roboto nuoma su operatoriumi",
        },
        "bye": "Pagarbiai,",
        # --- wiadomość na WhatsApp / Viber ---
        "msg_hello": "Laba diena!",
        "msg_price": "Dėkojame už užklausą. Humanoidinio roboto Unitree G1 nuoma jūsų renginiui{when} ({time}, {place}) "
                     "kainuotų {price} € be PVM.",
        "msg_included": "Į kainą įskaičiuota: atvykimas, asmeninis operatorius, roboto pritaikymas jūsų renginiui "
                        "ir techninė pagalba vietoje.",
        "msg_reserve": "Ar rezervuojame šią datą? Mielai atsakysiu į klausimus.",
        "soon": "artimiausiu metu",
        # --- kampanie ---
        "campaign_body": "Laba diena,\n\n\n\nPagarbiai,\n",
        "campaign_footer": "\n\n—\nJei nenorite gauti tokių mūsų žinučių, tiesiog atsakykite „NE“.",
        # --- polecenia dla Claude'a (po polsku, bo czyta je model) ---
        "ai_offer": ("PISZESZ DO KLIENTÓW Z LITWY: mail i PDF w całości PO LITEWSKU, ceny w EUR — nigdy po angielsku "
                     "ani po polsku. Przykłady poniżej są po polsku — użyj litewskich odpowiedników: 'Laba diena,' "
                     "(bez imienia), 'Pagarbiai,', included: 'Į kainą įskaičiuota: atvykimas, asmeninis operatorius, "
                     "roboto asmenybės ir žinių pritaikymas bei techninė pagalba vietoje. Jokių paslėptų mokesčių.', "
                     "cover np. PARENGTA / DATA / KAINA, karty np. ROBOTO VAIDMUO / PASIRUOŠIMAS, "
                     "temat: 'Pasiūlymas – humanoidinis robotas …'."),
        "ai_rule": "KLIENT JEST Z LITWY: mail i oferta ZAWSZE PO LITEWSKU, ceny w EUR ('2 100 €').",
        "ai_campaign": ("PISZESZ PO LITEWSKU (klienci z Litwy). Zacznij od 'Laba diena,' (bez imienia — litewski wołacz "
                        "łatwo pomylić), zakończ 'Pagarbiai,' + podpis."),
        "ai_greeting": "'Laba diena,' … 'Pagarbiai,'",
        # --- kalkulator ---
        "price_base": 2100, "price_km": 0.75, "price_lodging": 70,
        "departures": {"granica_lt": ("Granica PL–LT (Budzisko)", "od granicy", (54.38, 23.02))},
        "calc_from": "od granicy PL–LT (Budzisko)",
        "max_km_auto": 500,
    },
    "de": {
        "code": "de", "lang": "de", "flag": "🇩🇪", "name": "Niemcy", "country": "DE", "domain": "33bots.de",
        "brand": "33bots CRM 🇩🇪", "html_lang": "de",
        "lang_name_pl": "niemiecki", "lang_in_pl": "po niemiecku",
        "mailboxes": "o.latek@33bots.de,kontakt@33bots.de",
        "own_email": "o.latek@33bots.de", "inquiry_box": "kontakt@33bots.de",
        "phone_codes": ("49", "43", "41"), "mobile_local": ("0", 0),  # krajowy zapis: 0151 …
        "hours_unit": "Std.", "date_style": "dots",
        "auto_quote_default": "0",
        "auto_send": False,  # Niemcy: NIC nie wychodzi do klienta automatycznie — CRM generuje, człowiek wysyła
        "whatsapp_lang": "de",
        "form": {
            "title": "33Bots – Angaben zur Veranstaltung",
            "done_h1": "Vielen Dank! Wir haben Ihre Angaben erhalten.",
            "done_sub": "Wir erstellen Ihr Angebot und senden es Ihnen so schnell wie möglich per E-Mail.",
            "h1": "Angaben zur Veranstaltung",
            "sub": "Drei kurze Antworten – und wir erstellen Ihr Angebot für die Vermietung des humanoiden Roboters Unitree G1.",
            "date": "Datum der Veranstaltung", "multi": "Die Veranstaltung dauert mehrere Tage", "date_to": "Letzter Tag",
            "place": "Stadt", "place_ph": "z. B. Berlin", "from": "Von", "to": "Bis",
            "more": "Weitere Angaben (optional)", "kind": "Art der Veranstaltung",
            "kind_ph": "z. B. Messe, Konferenz, Firmenfeier", "guests": "Erwartete Gästezahl",
            "comment": "Wünsche", "send": "Senden",
        },
        "errors": {404: ("🔗", "Der Link ist nicht mehr gültig",
                         "Dieses Formular ist nicht mehr verfügbar. Schreiben Sie uns – wir helfen Ihnen gerne:"),
                   500: ("🛠️", "Es ist ein Fehler aufgetreten",
                         "Bitte versuchen Sie es in einigen Minuten erneut oder schreiben Sie uns:")},
        "ask_subject": "33Bots – Vermietung des humanoiden Roboters: ein paar Details für Ihr Angebot",
        "ask_hello": "Guten Tag,\n\nvielen Dank für Ihre Anfrage zur Vermietung des humanoiden Roboters Unitree G1!\n\n",
        "ask_form": "Damit wir Ihnen ein genaues Angebot erstellen können, füllen Sie bitte dieses kurze Formular aus "
                    "(dauert 30 Sekunden):\n{link}\n\nOder antworten Sie einfach auf diese E-Mail und nennen Sie uns:\n",
        "ask_noform": "Damit wir Ihnen ein genaues Angebot erstellen können, antworten Sie bitte auf diese E-Mail "
                      "und nennen Sie uns:\n",
        "ask_list": "– das Datum (bzw. die Daten) der Veranstaltung,\n– die Stadt,\n– die Uhrzeit (von–bis),\n"
                    "– die Art der Veranstaltung und die erwartete Gästezahl.\n\n"
                    "Wir senden Ihnen das Angebot so schnell wie möglich.\n\n",
        "ask_bye": "Mit freundlichen Grüßen",
        "quote_subject": "33Bots – Angebot: Vermietung des humanoiden Roboters",
        "quote_hello": "Guten Tag,",
        "quote_thanks": ("vielen Dank für Ihre Anfrage zur Vermietung des humanoiden Roboters Unitree G1! "
                         "Anbei erhalten Sie unser Angebot als PDF."),
        "quote_fixed": ("bitte entschuldigen Sie – in unserer vorherigen E-Mail war ein fehlerhafter Link. Wir senden "
                        "Ihnen das Angebot hiermit erneut, diesmal mit PDF im Anhang."),
        "quote_does": ("Was der Roboter auf Ihrer Veranstaltung macht:\n"
                       "– er begrüßt Ihre Gäste und lädt sie an Ihren Stand oder auf Ihre Bühne ein,\n"
                       "– er unterhält sich mit den Gästen und beantwortet ihre Fragen – auf Basis einer für Ihr "
                       "Unternehmen vorbereiteten Wissensdatenbank,\n"
                       "– er tanzt, winkt und posiert für gemeinsame Fotos,\n"
                       "– er ist ein echter Blickfang und liefert starke Inhalte für Social Media."),
        "quote_price": "Für Ihre Veranstaltung{when} ({time}, {place}) beträgt der Mietpreis {price} € netto.",
        "quote_included": "Im Preis enthalten:\n– Anreise und Aufbau des Roboters vor Ort,\n"
                          "– ein eigener Operator während der gesamten Veranstaltung,\n"
                          "– Anpassung der Persönlichkeit und des Wissens des Roboters an Ihre Veranstaltung,\n"
                          "– technischer Support vor Ort.",
        "quote_robot": "Der Roboter begrüßt Ihre Gäste, unterhält sich auf Basis einer vorbereiteten Wissensdatenbank, "
                       "tanzt und posiert für Fotos.",
        "quote_photos": "Fotos von unseren Veranstaltungen:",
        "quote_fix": "Falls die Details abweichen (Datum, Uhrzeit, Ort), können Sie sie hier korrigieren – "
                     "wir passen den Preis an:",
        "quote_reserve": ("Sollen wir den Termin für Sie reservieren? Antworten Sie einfach auf diese E-Mail – gerne "
                          "beantworte ich auch alle Ihre Fragen.\nDas Angebot ist 14 Tage gültig."),
        "offer_pdf": {
            "headline": "Humanoider Roboter",
            "subtitle": "Unitree G1 auf Ihrer Veranstaltung – begrüßt Gäste, unterhält sich, tanzt und posiert für Fotos.",
            "cover": ("TERMIN", "ORT", "ZEIT"), "soon": "Nach Absprache",
            "intro": ("Der Unitree G1 ist das Highlight Ihrer Veranstaltung: Er begrüßt Gäste, unterhält sich auf Basis "
                      "einer für Ihr Unternehmen vorbereiteten Wissensdatenbank, tanzt und posiert für Fotos. Unser "
                      "Operator betreut ihn die ganze Zeit."),
            "cards": [("ROLLE DES ROBOTERS", "Was der Roboter macht", [
                "Begrüßt Gäste am Stand oder auf der Bühne",
                "Beantwortet Fragen zu Ihrem Unternehmen",
                "Tanzt und posiert für gemeinsame Fotos"]),
                ("VORBEREITUNG", "So bereiten wir uns vor", [
                    "Persönlichkeit und Wissen angepasst",
                    "Rechtzeitige Anreise und Aufbau vor Ort",
                    "Operator und technischer Support vor Ort"])],
            "price_title": "Robotervermietung mit Operator",
        },
        "bye": "Mit freundlichen Grüßen",
        "msg_hello": "Guten Tag!",
        "msg_price": "Vielen Dank für Ihre Anfrage. Die Vermietung des humanoiden Roboters Unitree G1 für Ihre "
                     "Veranstaltung{when} ({time}, {place}) kostet {price} € netto.",
        "msg_included": "Im Preis enthalten: Anreise, ein eigener Operator, Anpassung des Roboters an Ihre Veranstaltung "
                        "und technischer Support vor Ort.",
        "msg_reserve": "Sollen wir den Termin für Sie reservieren? Ich beantworte gerne Ihre Fragen.",
        "soon": "nach Absprache",
        "campaign_body": "Guten Tag,\n\n\n\nMit freundlichen Grüßen\n",
        "campaign_footer": "\n\n—\nFalls Sie keine solchen Nachrichten mehr erhalten möchten, antworten Sie einfach mit „NEIN“.",
        "ai_offer": ("PISZESZ DO KLIENTÓW Z NIEMIEC (także Austria i Szwajcaria): mail i PDF w całości PO NIEMIECKU, "
                     "forma grzecznościowa 'Sie', ceny w EUR — nigdy po angielsku ani po polsku. Przykłady poniżej są po "
                     "polsku — użyj niemieckich odpowiedników: 'Guten Tag,' (albo 'Sehr geehrte Frau X,' / "
                     "'Sehr geehrter Herr X,' gdy znamy nazwisko), 'Mit freundlichen Grüßen', included: 'Im Preis enthalten: "
                     "Anreise, ein eigener Operator, Anpassung der Persönlichkeit und des Wissens des Roboters sowie "
                     "technischer Support vor Ort. Keine versteckten Kosten.', cover np. ERSTELLT FÜR / TERMIN / PREIS, "
                     "karty np. ROLLE DES ROBOTERS / VORBEREITUNG, temat: 'Angebot – humanoider Roboter für …'."),
        "ai_rule": "KLIENT JEST Z NIEMIEC: mail i oferta ZAWSZE PO NIEMIECKU (forma 'Sie'), ceny w EUR ('2 500 €').",
        "ai_campaign": ("PISZESZ PO NIEMIECKU (klienci z Niemiec, forma 'Sie'). Zacznij od 'Guten Tag,', "
                        "zakończ 'Mit freundlichen Grüßen' + podpis."),
        "ai_greeting": "'Guten Tag,' … 'Mit freundlichen Grüßen' (forma 'Sie')",
        "price_base": 2500, "price_km": 0.75, "price_lodging": 120,
        "departures": {"granica_ffo": ("Granica PL–DE (Frankfurt/Oder)", "od granicy (Frankfurt/Oder)", (52.342, 14.553)),
                       "granica_gr": ("Granica PL–DE (Görlitz)", "od granicy (Görlitz)", (51.153, 14.990))},
        "calc_from": "od granicy PL–DE (Frankfurt/Oder albo Görlitz — taniej)",
        "max_km_auto": 900,
    },
}


# nazwy miast dla klienta (w jego języku) — klucz jak w logistics.CITIES / CITY_ALIASES
CITY_NATIVE = {
    "lt": {"vilnius": "Vilnius", "wilno": "Vilnius", "kaunas": "Kaunas", "kowno": "Kaunas", "klaipeda": "Klaipėda",
           "siauliai": "Šiauliai", "panevezys": "Panevėžys", "alytus": "Alytus", "marijampole": "Marijampolė",
           "palanga": "Palanga", "druskininkai": "Druskininkai", "utena": "Utena", "jonava": "Jonava",
           "kedainiai": "Kėdainiai", "telsiai": "Telšiai", "taurage": "Tauragė", "mazeikiai": "Mažeikiai",
           "visaginas": "Visaginas", "trakai": "Trakai", "skuodas": "Skuodas", "plunge": "Plungė",
           "kretinga": "Kretinga", "birzai": "Biržai", "rokiskis": "Rokiškis", "elektrenai": "Elektrėnai",
           "ryga": "Ryga", "riga": "Ryga", "warszawa": "Varšuva", "berlin": "Berlynas"},
    "de": {"berlin": "Berlin", "drezno": "Dresden", "dresden": "Dresden", "lipsk": "Leipzig", "leipzig": "Leipzig",
           "monachium": "München", "munchen": "München", "kolonia": "Köln", "koln": "Köln", "hamburg": "Hamburg",
           "frankfurt": "Frankfurt", "wieden": "Wien", "wien": "Wien", "praga": "Prag", "praha": "Prag",
           "warszawa": "Warschau", "wroclaw": "Breslau", "wilno": "Vilnius", "vilnius": "Vilnius"},
}


def get(code):
    return MARKETS.get((code or "").lower()) or MARKETS[DEFAULT]


def codes():
    return list(MARKETS)


def env_num(code, name, default):
    """Liczba z Railway: najpierw <NAZWA>_<RYNEK> (np. CALC_BASE_8H_DE), potem <NAZWA> (tylko LT — zgodność)."""
    raw = os.environ.get(f"{name}_{code.upper()}", "").strip()
    if not raw and code == "lt":
        raw = os.environ.get(name, "").strip()
    try:
        return float(raw.replace(",", ".")) if raw else default
    except ValueError:
        return default


def detect(phone="", emails=(), country="", place=""):
    """Rynek po danych klienta (gdy wiadomo) albo None: +370 / .lt / LT → lt, +49/+43/+41 / .de/.at/.ch → de."""
    import re
    d = re.sub(r"\D", "", phone or "")
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("370") and len(d) >= 11:
        return "lt"
    if d[:2] in ("49", "43", "41") and len(d) >= 11:
        return "de"
    for e in emails or ():
        e = (e or "").lower()
        if e.endswith(".lt"):
            return "lt"
        if e.endswith((".de", ".at", ".ch")):
            return "de"
    c = (country or "").upper()
    if c == "LT":
        return "lt"
    if c in ("DE", "AT", "CH"):
        return "de"
    return None
