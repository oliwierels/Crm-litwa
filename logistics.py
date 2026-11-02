"""Planowanie logistyki realizacji: gdzie jest dane miejsce, ile km jedzie operator,
co w planie jest nieoptymalne. Czyste funkcje — bez Flaska i bazy, żeby dało się je
łatwo testować; crm.py podaje im gotowe dane."""
import math
import re
import unicodedata
from datetime import timedelta

ROAD_FACTOR = 1.25     # droga jest średnio ~25% dłuższa niż linia prosta
AVG_SPEED_KMH = 70     # średnia prędkość przejazdu (trasy mieszane, z postojami)
STAY_KM = 150          # dalej niż tyle od bazy + wydarzenie następnego dnia = nocleg na miejscu
STAY_SAVING_KM = 100   # jazda prosto z wczorajszego miejsca krótsza o tyle niż przez bazę = też nocleg
LONG_DRIVE_KM = 350    # dojazd dłuższy niż tyle w jedną stronę = ostrzeżenie o noclegu przed eventem
SAME_PLACE_KM = 30     # bliżej niż tyle = to samo miejsce (dwa eventy w jednym mieście)
MIN_SAVING_KM = 80     # poniżej tej oszczędności nie zawracamy głowy sugestią
HANDOVER_KM = 150      # robot przekazywany między operatorami z dnia na dzień dalej niż tyle
CARPOOL_KM = 60        # ekipa podwójna: bazy operatorów bliżej niż tyle = jadą razem jednym autem

# współrzędne (lat, lon) — klucze po normalizacji (małe litery, bez polskich znaków)
CITIES = {
    "warszawa": (52.2297, 21.0122), "krakow": (50.0647, 19.9450), "lodz": (51.7592, 19.4560),
    "wroclaw": (51.1079, 17.0385), "poznan": (52.4064, 16.9252), "gdansk": (54.3520, 18.6466),
    "gdynia": (54.5189, 18.5305), "sopot": (54.4416, 18.5601), "szczecin": (53.4285, 14.5528),
    "bydgoszcz": (53.1235, 18.0084), "torun": (53.0138, 18.5984), "lublin": (51.2465, 22.5684),
    "bialystok": (53.1325, 23.1688), "katowice": (50.2649, 19.0238), "gliwice": (50.2945, 18.6714),
    "zabrze": (50.3249, 18.7857), "bytom": (50.3483, 18.9157), "sosnowiec": (50.2863, 19.1041),
    "chorzow": (50.2974, 18.9545), "tychy": (50.1231, 18.9870), "rybnik": (50.1022, 18.5463),
    "bielsko biala": (49.8224, 19.0584), "czestochowa": (50.8118, 19.1203), "opole": (50.6751, 17.9213),
    "kielce": (50.8661, 20.6286), "rzeszow": (50.0412, 21.9991), "olsztyn": (53.7784, 20.4801),
    "radom": (51.4027, 21.1471), "plock": (52.5463, 19.7065), "elblag": (54.1561, 19.4045),
    "gorzow wielkopolski": (52.7368, 15.2288), "zielona gora": (51.9356, 15.5062),
    "kalisz": (51.7611, 18.0910), "koszalin": (54.1943, 16.1715), "slupsk": (54.4641, 17.0285),
    "legnica": (51.2070, 16.1619), "walbrzych": (50.7714, 16.2843), "jelenia gora": (50.9044, 15.7194),
    "nowy sacz": (49.6218, 20.6972), "tarnow": (50.0121, 20.9858), "przemysl": (49.7838, 22.7678),
    "zamosc": (50.7231, 23.2520), "chelm": (51.1431, 23.4716), "biala podlaska": (52.0325, 23.1149),
    "siedlce": (52.1676, 22.2902), "ostroleka": (53.0842, 21.5745), "lomza": (53.1781, 22.0590),
    "suwalki": (54.1115, 22.9308), "augustow": (53.8434, 22.9797), "elk": (53.8281, 22.3647),
    "gizycko": (54.0381, 21.7661), "mragowo": (53.8644, 21.3050), "mikolajki": (53.8030, 21.5710),
    "wloclawek": (52.6483, 19.0677), "grudziadz": (53.4837, 18.7536), "inowroclaw": (52.7982, 18.2610),
    "gniezno": (52.5348, 17.5826), "konin": (52.2230, 18.2512), "leszno": (51.8406, 16.5749),
    "pila": (53.1515, 16.7378), "ostrow wielkopolski": (51.6550, 17.8069),
    "piotrkow trybunalski": (51.4053, 19.7031), "skierniewice": (51.9549, 20.1583),
    "tomaszow mazowiecki": (51.5310, 20.0085), "pabianice": (51.6645, 19.3547), "zgierz": (51.8562, 19.4063),
    "sieradz": (51.5956, 18.7302), "wielun": (51.2210, 18.5700), "kutno": (52.2308, 19.3642),
    "lowicz": (52.1066, 19.9450), "ciechanow": (52.8812, 20.6202), "mlawa": (53.1128, 20.3842),
    "pruszkow": (52.1706, 20.8119), "legionowo": (52.4015, 20.9265), "otwock": (52.1054, 21.2618),
    "piaseczno": (52.0814, 21.0240), "zabki": (52.2926, 21.1060), "marki": (52.3207, 21.1057),
    "wolomin": (52.3496, 21.2420), "nadarzyn": (52.0955, 20.8065), "grodzisk mazowiecki": (52.1094, 20.6326),
    "minsk mazowiecki": (52.1795, 21.5715), "sochaczew": (52.2297, 20.2384), "zyrardow": (52.0488, 20.4459),
    "pulawy": (51.4167, 21.9690), "krasnik": (50.9243, 22.2201), "stalowa wola": (50.5826, 22.0533),
    "mielec": (50.2874, 21.4239), "debica": (50.0516, 21.4114), "krosno": (49.6886, 21.7706),
    "sanok": (49.5557, 22.2056), "jaslo": (49.7449, 21.4718), "zakopane": (49.2992, 19.9496),
    "nowy targ": (49.4775, 20.0328), "oswiecim": (50.0344, 19.2098), "wadowice": (49.8833, 19.4933),
    "myslenice": (49.8335, 19.9380), "limanowa": (49.7059, 20.4224), "bochnia": (49.9690, 20.4302),
    "wieliczka": (49.9870, 20.0647), "chrzanow": (50.1355, 19.4022), "olkusz": (50.2813, 19.5654),
    "jaworzno": (50.2050, 19.2745), "dabrowa gornicza": (50.3217, 19.1949), "bedzin": (50.3265, 19.1289),
    "jastrzebie zdroj": (49.9549, 18.5947), "zory": (50.0449, 18.7003), "raciborz": (50.0919, 18.2193),
    "cieszyn": (49.7496, 18.6325), "ustron": (49.7210, 18.8110), "wisla": (49.6557, 18.8594),
    "szczyrk": (49.7188, 19.0270), "klobuck": (50.9046, 18.9355), "lubliniec": (50.6687, 18.6840),
    "myszkow": (50.5747, 19.3219), "zawiercie": (50.4878, 19.4169), "kedzierzyn kozle": (50.3497, 18.2262),
    "nysa": (50.4747, 17.3339), "brzeg": (50.8604, 17.4676), "namyslow": (51.0766, 17.7167),
    "kluczbork": (50.9728, 18.2185), "olesnica": (51.2095, 17.3799), "olawa": (50.9457, 17.2927),
    "swidnica": (50.8436, 16.4879), "klodzko": (50.4346, 16.6614), "boleslawiec": (51.2638, 15.5697),
    "glogow": (51.6634, 16.0845), "lubin": (51.4010, 16.2015), "polkowice": (51.5042, 16.0735),
    "karpacz": (50.7771, 15.7480), "szklarska poreba": (50.8263, 15.5226), "zgorzelec": (51.1492, 15.0081),
    "zary": (51.6424, 15.1370), "zagan": (51.6177, 15.3150), "nowa sol": (51.8030, 15.7147),
    "swiebodzin": (52.2474, 15.5335), "kargowa": (52.0706, 15.8660), "miedzyrzecz": (52.4453, 15.5781),
    "sulechow": (52.0843, 15.6266), "kostrzyn nad odra": (52.5885, 14.6475), "slubice": (52.3504, 14.5605),
    "swinoujscie": (53.9105, 14.2476), "miedzyzdroje": (53.9280, 14.4497), "kolobrzeg": (54.1757, 15.5832),
    "darlowo": (54.4213, 16.4096), "ustka": (54.5806, 16.8618), "leba": (54.7600, 17.5563),
    "wladyslawowo": (54.7907, 18.4020), "jastarnia": (54.6961, 18.6786), "puck": (54.7178, 18.4081),
    "wejherowo": (54.6059, 18.2353), "rumia": (54.5709, 18.3882), "pruszcz gdanski": (54.2622, 18.6356),
    "tczew": (54.0924, 18.7779), "malbork": (54.0359, 19.0266), "kwidzyn": (53.7298, 18.9311),
    "starogard gdanski": (53.9658, 18.5307), "chojnice": (53.6955, 17.5570), "koscierzyna": (54.1223, 17.9811),
    "kartuzy": (54.3341, 18.1974), "lebork": (54.5392, 17.7501), "bytow": (54.1708, 17.4924),
    "szczecinek": (53.7079, 16.6988), "walcz": (53.2716, 16.4730), "stargard": (53.3367, 15.0499),
    "gryfino": (53.2524, 14.4882), "pobierowo": (54.0592, 14.9324), "mielno": (54.2614, 16.0616),
    "ostroda": (53.6966, 19.9647), "ilawa": (53.5961, 19.5686), "dzialdowo": (53.2375, 20.1832),
    "bartoszyce": (54.2542, 20.8084), "ketrzyn": (54.0762, 21.3753), "pisz": (53.6275, 21.8120),
    "olecko": (54.0340, 22.5010), "hajnowka": (52.7427, 23.5813), "bielsk podlaski": (52.7658, 23.1866),
    "zambrow": (52.9855, 22.2436), "grajewo": (53.6474, 22.4555), "sokolka": (53.4064, 23.5028),
    "nowy dwor mazowiecki": (52.4298, 20.7152), "pultusk": (52.7026, 21.0826), "wyszkow": (52.5925, 21.4584),
    "ostrow mazowiecka": (52.8024, 21.8947), "wegrow": (52.3997, 22.0158), "garwolin": (51.8973, 21.6147),
    "kozienice": (51.5835, 21.5480), "grojec": (51.8651, 20.8672), "gora kalwaria": (51.9760, 21.2150),
    "lukow": (51.9296, 22.3820), "radzyn podlaski": (51.7837, 22.6230), "swidnik": (51.2201, 22.6966),
    "kazimierz dolny": (51.3218, 21.9480), "naleczow": (51.2854, 22.2167),
    "tomaszow lubelski": (50.4477, 23.4164), "hrubieszow": (50.8072, 23.8918), "bilgoraj": (50.5413, 22.7222),
    "tarnobrzeg": (50.5727, 21.6793), "sandomierz": (50.6828, 21.7488),
    "ostrowiec swietokrzyski": (50.9294, 21.3854), "starachowice": (51.0373, 21.0712),
    "skarzysko kamienna": (51.1134, 20.8673), "konskie": (51.1917, 20.4057), "busko zdroj": (50.4702, 20.7189),
    "jedrzejow": (50.6393, 20.3036), "belchatow": (51.3688, 19.3564), "radomsko": (51.0673, 19.4448),
    "opoczno": (51.3754, 20.2786), "rawa mazowiecka": (51.7640, 20.2540), "zdunska wola": (51.5993, 18.9397),
    "turek": (52.0153, 18.5006), "wrzesnia": (52.3251, 17.5659), "srem": (52.0888, 17.0147),
    "koscian": (52.0856, 16.6461), "swarzedz": (52.4122, 17.0784), "lubon": (52.3469, 16.8744),
    "oborniki": (52.6480, 16.8144), "szamotuly": (52.6116, 16.5770), "wronki": (52.7093, 16.3798),
    "chodziez": (52.9954, 16.9197), "wagrowiec": (52.8084, 17.1995), "swiecie": (53.4095, 18.4474),
    "chelmno": (53.3490, 18.4250), "brodnica": (53.2596, 19.3968), "ciechocinek": (52.8793, 18.7947),
    "aleksandrow kujawski": (52.8768, 18.6936), "lipno": (52.8430, 19.1770), "rypin": (53.0660, 19.4120),
    "znin": (52.8491, 17.7191), "sokolow podlaski": (52.4068, 22.2528), "drawsko pomorskie": (53.5308, 15.8094),
    "ilowa": (51.4983, 15.1971),
    # zagranica
    "brno": (49.1951, 16.6068), "praga": (50.0755, 14.4378), "praha": (50.0755, 14.4378),
    "ostrawa": (49.8209, 18.2625), "ostrava": (49.8209, 18.2625), "berlin": (52.5200, 13.4050),
    "drezno": (51.0504, 13.7373), "dresden": (51.0504, 13.7373), "lipsk": (51.3397, 12.3731),
    "leipzig": (51.3397, 12.3731), "wilno": (54.6872, 25.2797), "vilnius": (54.6872, 25.2797),
    "kowno": (54.8985, 23.9036), "kaunas": (54.8985, 23.9036), "bratyslawa": (48.1486, 17.1077),
    "bratislava": (48.1486, 17.1077), "wieden": (48.2082, 16.3738), "wien": (48.2082, 16.3738),
    "bukareszt": (44.4268, 26.1025), "bucharest": (44.4268, 26.1025), "bucuresti": (44.4268, 26.1025),
    "budapeszt": (47.4979, 19.0402), "budapest": (47.4979, 19.0402), "ryga": (56.9496, 24.1052),
    "riga": (56.9496, 24.1052), "kolonia": (50.9375, 6.9603), "koln": (50.9375, 6.9603),
    "monachium": (48.1351, 11.5820), "munchen": (48.1351, 11.5820), "hamburg": (53.5511, 9.9937),
    "frankfurt": (50.1109, 8.6821), "lwow": (49.8397, 24.0297), "lviv": (49.8397, 24.0297),
}

# nazwy do wyświetlenia (klucz w CITIES = ta nazwa po normalize())
CITY_NAMES = {
    "warszawa": "Warszawa", "krakow": "Kraków", "lodz": "Łódź", "wroclaw": "Wrocław", "poznan": "Poznań",
    "gdansk": "Gdańsk", "gdynia": "Gdynia", "sopot": "Sopot", "szczecin": "Szczecin", "bydgoszcz": "Bydgoszcz",
    "torun": "Toruń", "lublin": "Lublin", "bialystok": "Białystok", "katowice": "Katowice", "gliwice": "Gliwice",
    "zabrze": "Zabrze", "bytom": "Bytom", "sosnowiec": "Sosnowiec", "chorzow": "Chorzów", "tychy": "Tychy",
    "rybnik": "Rybnik", "bielsko biala": "Bielsko-Biała", "czestochowa": "Częstochowa", "opole": "Opole", "kielce": "Kielce",
    "rzeszow": "Rzeszów", "olsztyn": "Olsztyn", "radom": "Radom", "plock": "Płock", "elblag": "Elbląg",
    "gorzow wielkopolski": "Gorzów Wielkopolski", "zielona gora": "Zielona Góra", "kalisz": "Kalisz", "koszalin": "Koszalin", "slupsk": "Słupsk",
    "legnica": "Legnica", "walbrzych": "Wałbrzych", "jelenia gora": "Jelenia Góra", "nowy sacz": "Nowy Sącz", "tarnow": "Tarnów",
    "przemysl": "Przemyśl", "zamosc": "Zamość", "chelm": "Chełm", "biala podlaska": "Biała Podlaska", "siedlce": "Siedlce",
    "ostroleka": "Ostrołęka", "lomza": "Łomża", "suwalki": "Suwałki", "augustow": "Augustów", "elk": "Ełk",
    "gizycko": "Giżycko", "mragowo": "Mrągowo", "mikolajki": "Mikołajki", "wloclawek": "Włocławek", "grudziadz": "Grudziądz",
    "inowroclaw": "Inowrocław", "gniezno": "Gniezno", "konin": "Konin", "leszno": "Leszno", "pila": "Piła",
    "ostrow wielkopolski": "Ostrów Wielkopolski", "piotrkow trybunalski": "Piotrków Trybunalski", "skierniewice": "Skierniewice", "tomaszow mazowiecki": "Tomaszów Mazowiecki", "pabianice": "Pabianice",
    "zgierz": "Zgierz", "sieradz": "Sieradz", "wielun": "Wieluń", "kutno": "Kutno", "lowicz": "Łowicz",
    "ciechanow": "Ciechanów", "mlawa": "Mława", "pruszkow": "Pruszków", "legionowo": "Legionowo", "otwock": "Otwock",
    "piaseczno": "Piaseczno", "zabki": "Ząbki", "marki": "Marki", "wolomin": "Wołomin", "nadarzyn": "Nadarzyn",
    "grodzisk mazowiecki": "Grodzisk Mazowiecki", "minsk mazowiecki": "Mińsk Mazowiecki", "sochaczew": "Sochaczew", "zyrardow": "Żyrardów", "pulawy": "Puławy",
    "krasnik": "Kraśnik", "stalowa wola": "Stalowa Wola", "mielec": "Mielec", "debica": "Dębica", "krosno": "Krosno",
    "sanok": "Sanok", "jaslo": "Jasło", "zakopane": "Zakopane", "nowy targ": "Nowy Targ", "oswiecim": "Oświęcim",
    "wadowice": "Wadowice", "myslenice": "Myślenice", "limanowa": "Limanowa", "bochnia": "Bochnia", "wieliczka": "Wieliczka",
    "chrzanow": "Chrzanów", "olkusz": "Olkusz", "jaworzno": "Jaworzno", "dabrowa gornicza": "Dąbrowa Górnicza", "bedzin": "Będzin",
    "jastrzebie zdroj": "Jastrzębie-Zdrój", "zory": "Żory", "raciborz": "Racibórz", "cieszyn": "Cieszyn", "ustron": "Ustroń",
    "wisla": "Wisła", "szczyrk": "Szczyrk", "klobuck": "Kłobuck", "lubliniec": "Lubliniec", "myszkow": "Myszków",
    "zawiercie": "Zawiercie", "kedzierzyn kozle": "Kędzierzyn-Koźle", "nysa": "Nysa", "brzeg": "Brzeg", "namyslow": "Namysłów",
    "kluczbork": "Kluczbork", "olesnica": "Oleśnica", "olawa": "Oława", "swidnica": "Świdnica", "klodzko": "Kłodzko",
    "boleslawiec": "Bolesławiec", "glogow": "Głogów", "lubin": "Lubin", "polkowice": "Polkowice", "karpacz": "Karpacz",
    "szklarska poreba": "Szklarska Poręba", "zgorzelec": "Zgorzelec", "zary": "Żary", "zagan": "Żagań", "nowa sol": "Nowa Sól",
    "swiebodzin": "Świebodzin", "kargowa": "Kargowa", "miedzyrzecz": "Międzyrzecz", "sulechow": "Sulechów", "kostrzyn nad odra": "Kostrzyn nad Odrą",
    "slubice": "Słubice", "swinoujscie": "Świnoujście", "miedzyzdroje": "Międzyzdroje", "kolobrzeg": "Kołobrzeg", "darlowo": "Darłowo",
    "ustka": "Ustka", "leba": "Łeba", "wladyslawowo": "Władysławowo", "jastarnia": "Jastarnia", "puck": "Puck",
    "wejherowo": "Wejherowo", "rumia": "Rumia", "pruszcz gdanski": "Pruszcz Gdański", "tczew": "Tczew", "malbork": "Malbork",
    "kwidzyn": "Kwidzyn", "starogard gdanski": "Starogard Gdański", "chojnice": "Chojnice", "koscierzyna": "Kościerzyna", "kartuzy": "Kartuzy",
    "lebork": "Lębork", "bytow": "Bytów", "szczecinek": "Szczecinek", "walcz": "Wałcz", "stargard": "Stargard",
    "gryfino": "Gryfino", "pobierowo": "Pobierowo", "mielno": "Mielno", "ostroda": "Ostróda", "ilawa": "Iława",
    "dzialdowo": "Działdowo", "bartoszyce": "Bartoszyce", "ketrzyn": "Kętrzyn", "pisz": "Pisz", "olecko": "Olecko",
    "hajnowka": "Hajnówka", "bielsk podlaski": "Bielsk Podlaski", "zambrow": "Zambrów", "grajewo": "Grajewo", "sokolka": "Sokółka",
    "nowy dwor mazowiecki": "Nowy Dwór Mazowiecki", "pultusk": "Pułtusk", "wyszkow": "Wyszków", "ostrow mazowiecka": "Ostrów Mazowiecka", "wegrow": "Węgrów",
    "garwolin": "Garwolin", "kozienice": "Kozienice", "grojec": "Grójec", "gora kalwaria": "Góra Kalwaria", "lukow": "Łuków",
    "radzyn podlaski": "Radzyń Podlaski", "swidnik": "Świdnik", "kazimierz dolny": "Kazimierz Dolny", "naleczow": "Nałęczów", "tomaszow lubelski": "Tomaszów Lubelski",
    "hrubieszow": "Hrubieszów", "bilgoraj": "Biłgoraj", "tarnobrzeg": "Tarnobrzeg", "sandomierz": "Sandomierz", "ostrowiec swietokrzyski": "Ostrowiec Świętokrzyski",
    "starachowice": "Starachowice", "skarzysko kamienna": "Skarżysko-Kamienna", "konskie": "Końskie", "busko zdroj": "Busko-Zdrój", "jedrzejow": "Jędrzejów",
    "belchatow": "Bełchatów", "radomsko": "Radomsko", "opoczno": "Opoczno", "rawa mazowiecka": "Rawa Mazowiecka", "zdunska wola": "Zduńska Wola",
    "turek": "Turek", "wrzesnia": "Września", "srem": "Śrem", "koscian": "Kościan", "swarzedz": "Swarzędz",
    "lubon": "Luboń", "oborniki": "Oborniki", "szamotuly": "Szamotuły", "wronki": "Wronki", "chodziez": "Chodzież",
    "wagrowiec": "Wągrowiec", "swiecie": "Świecie", "chelmno": "Chełmno", "brodnica": "Brodnica", "ciechocinek": "Ciechocinek",
    "aleksandrow kujawski": "Aleksandrów Kujawski", "lipno": "Lipno", "rypin": "Rypin", "znin": "Żnin", "sokolow podlaski": "Sokołów Podlaski",
    "drawsko pomorskie": "Drawsko Pomorskie", "ilowa": "Iłowa", "brno": "Brno", "praga": "Praga", "praha": "Praga",
    "ostrawa": "Ostrawa", "ostrava": "Ostrawa", "berlin": "Berlin", "drezno": "Drezno", "dresden": "Drezno",
    "lipsk": "Lipsk", "leipzig": "Lipsk", "wilno": "Wilno", "vilnius": "Wilno", "kowno": "Kowno",
    "kaunas": "Kowno", "bratyslawa": "Bratysława", "bratislava": "Bratysława", "wieden": "Wiedeń", "wien": "Wiedeń",
    "bukareszt": "Bukareszt", "bucharest": "Bukareszt", "bucuresti": "Bukareszt", "budapeszt": "Budapeszt", "budapest": "Budapeszt",
    "ryga": "Ryga", "riga": "Ryga", "kolonia": "Kolonia", "koln": "Kolonia", "monachium": "Monachium",
    "munchen": "Monachium", "hamburg": "Hamburg", "frankfurt": "Frankfurt", "lwow": "Lwów", "lviv": "Lwów",
}

# skróty i formy spotykane w CRM
CITY_ALIASES = {
    "wwa": "warszawa", "waw": "warszawa", "warsaw": "warszawa", "lodzi": "lodz",
    "trojmiasto": "gdansk", "3miasto": "gdansk", "krk": "krakow", "cracow": "krakow",
    "wroc": "wroclaw", "poz": "poznan", "bb": "bielsko biala",
}

# polskie litery, których NFKD nie rozkłada (ł)
_PL_EXTRA = str.maketrans({"ł": "l", "Ł": "l"})


def normalize(text):
    """'Zielona Góra, ul. Łódzka' -> 'zielona gora ul lodzka' (bez polskich znaków, samo a-z/0-9)."""
    t = (text or "").translate(_PL_EXTRA)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def city_label(key):
    """'bielsko biala' -> 'Bielsko-Biała'."""
    return CITY_NAMES.get(key) or " ".join(w.capitalize() for w in key.split())


# przymiotniki od nazw miast ('ul. Warszawska', 'Krakowskie Przedmieście') to nie miasto
_ADJ_SUFFIXES = ("ska", "ski", "skie", "skiej", "skim", "cka", "cki", "ckie", "ckiej", "ckim")


def match_city(text):
    """Szuka znanego miasta w dowolnym opisie lokalizacji. Zwraca (klucz, (lat, lon)) albo None.
    Dokładna nazwa wygrywa z odmianą ('w Krakowie'); gdy miast jest kilka
    ('Katowice + Zakopane'), bierze pierwsze od lewej."""
    norm = normalize(text)
    if not norm:
        return None
    padded = f" {norm} "
    best = None  # (czy_odmiana, pozycja, -długość, klucz)

    def consider(cand):
        nonlocal best
        if best is None or cand < best:
            best = cand

    for key in CITIES:
        if " " in key:
            idx = padded.find(f" {key} ")
            if idx >= 0:
                consider((0, idx, -len(key), key))
    for tok in norm.split():
        idx = padded.find(f" {tok} ")
        if tok in CITIES:
            consider((0, idx, -len(tok), tok))
        elif tok in CITY_ALIASES:
            consider((0, idx, -len(tok), CITY_ALIASES[tok]))
        elif not tok.endswith(_ADJ_SUFFIXES):
            # odmiana: 'krakowie', 'poznaniu', 'warszawie', 'katowicach'
            for k in CITIES:
                if " " not in k and len(k) >= 6 and tok.startswith(k[:-1]) and len(tok) <= len(k) + 3:
                    consider((1, idx, -len(k), k))
                    break
    if best is None:
        return None
    return best[3], CITIES[best[3]]


def geocode_query(text):
    """Tekst do zapytania w zewnętrznym geokoderze: bez kodu pocztowego i dopisków w nawiasach."""
    t = re.sub(r"\b\d{2}-\d{3}\b", " ", text or "")
    t = re.sub(r"\(.*?\)", " ", t)
    t = re.split(r"[–—;|]", t)[0]
    return " ".join(t.split()).strip(" ,.-")


def haversine_km(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def road_km(a, b):
    """Szacowana długość drogi między punktami (linia prosta × współczynnik drogowy)."""
    if a is None or b is None:
        return 0.0
    return haversine_km(a, b) * ROAD_FACTOR


def drive_hours(km):
    return km / AVG_SPEED_KMH


# ---------------------------------------------------------------------------
# Trasy operatorów
#
# Stop = dict z kluczami: key (id ekipy), date (datetime.date), point ((lat, lon) albo None),
# place (nazwa miejsca do wyświetlenia), opcjonalnie title (firma + miejsce, do opisów sugestii)
# i with (imię 2. operatora, który jedzie tym samym autem — takiego stopu nie przerzucamy).
# Stop z ctx=True to dzień tuż poza oglądanym zakresem dat — liczy się do trasy (skąd operator
# jedzie), ale nie dostaje sugestii.
# Model: dni jeden po drugim (albo ten sam dzień) tworzą jeden wyjazd. Następnego dnia operator
# jedzie prosto z wczorajszego miejsca (nocleg), gdy jest ono daleko od bazy albo gdy tak jest
# wyraźnie krócej niż przez bazę (np. wczoraj Poznań, dziś coś obok). Inaczej wraca do bazy.
# ---------------------------------------------------------------------------

def _chains(stops):
    chains = []
    for s in sorted(stops, key=lambda s: (s["date"], str(s["key"]))):
        if chains and (s["date"] - chains[-1][-1]["date"]).days <= 1:
            chains[-1].append(s)
        else:
            chains.append([s])
    return chains


def route_plan(base_point, base_name, stops):
    """Wylicza wyjazdy operatora. Zwraca dict: km, hours, nights, trips (lista wyjazdów z odcinkami)."""
    known = [s for s in stops if s.get("point")]
    trips = []
    total_km = 0.0
    nights = 0
    for chain in _chains(known):
        legs = []
        pos, pos_name = base_point, base_name
        trip_km = 0.0
        trip_nights = 0
        for i, s in enumerate(chain):
            d = road_km(pos, s["point"])
            if d >= 1:
                legs.append({"from": pos_name, "to": s["place"], "km": d, "date": s["date"]})
            trip_km += d
            pos, pos_name = s["point"], s["place"]
            nxt = chain[i + 1] if i + 1 < len(chain) else None
            if nxt and nxt["date"] != s["date"]:
                to_base = road_km(s["point"], base_point)
                via_base = to_base + road_km(base_point, nxt["point"])
                if to_base > STAY_KM or via_base - road_km(s["point"], nxt["point"]) >= STAY_SAVING_KM:
                    trip_nights += 1
                    legs.append({"stay": True, "place": s["place"], "date": s["date"]})
                else:
                    back = road_km(s["point"], base_point)
                    if back >= 1:
                        legs.append({"from": pos_name, "to": base_name, "km": back, "date": s["date"]})
                    trip_km += back
                    pos, pos_name = base_point, base_name
        back = road_km(pos, base_point)
        if back >= 1:
            legs.append({"from": pos_name, "to": base_name, "km": back, "date": chain[-1]["date"]})
        trip_km += back
        if not any("km" in leg for leg in legs):
            legs = [{"local": True, "place": chain[0]["place"], "date": chain[0]["date"]}]
        total_km += trip_km
        nights += trip_nights
        trips.append({
            "start": chain[0]["date"], "end": chain[-1]["date"], "km": trip_km,
            "hours": drive_hours(trip_km), "nights": trip_nights, "legs": legs,
        })
    return {"km": total_km, "hours": drive_hours(total_km), "nights": nights, "trips": trips}


def route_km(base_point, stops):
    return route_plan(base_point, "", stops)["km"]


def find_suggestions(operators, stops_by_op, unassigned, today, extra_busy=None):
    """Szuka zmian w planie, które skracają łączną trasę.

    operators: {op_id: {"name", "point"}} (tylko aktywni, ze znaną bazą)
    stops_by_op: {op_id: [stop, ...]} — obecny plan
    unassigned: [stop, ...] — dni realizacji bez operatora
    extra_busy: {op_id: {date, ...}} — dni, w które operator jest zajęty bez własnej trasy
                (jedzie jako drugi operator w cudzym aucie)
    Zwraca listę dict: kind (reassign/swap/assign), saving_km/extra_km, text, i dane do "Zastosuj".
    """
    out = []
    extra_busy = extra_busy or {}
    cost = {op: route_km(operators[op]["point"], stops_by_op.get(op, [])) for op in operators}

    def busy(op, day):
        return day in extra_busy.get(op, ()) or any(s["date"] == day for s in stops_by_op.get(op, []))

    # 1) przerzucenie ekipy do wolnego tego dnia operatora
    for op_from, stops in stops_by_op.items():
        if op_from not in operators:
            continue
        for s in stops:
            # s["with"]: kierowca wiezie 2. operatora — zmiana kierowcy zmienia też jego dojazd
            if s["date"] < today or not s.get("point") or s.get("ctx") or s.get("with"):
                continue
            rest = [x for x in stops if x is not s]
            base_from = route_km(operators[op_from]["point"], rest)
            best = None
            for op_to in operators:
                if op_to == op_from or busy(op_to, s["date"]):
                    continue
                new_to = route_km(operators[op_to]["point"], stops_by_op.get(op_to, []) + [s])
                saving = (cost[op_from] + cost[op_to]) - (base_from + new_to)
                if saving >= MIN_SAVING_KM and (best is None or saving > best[0]):
                    best = (saving, op_to)
            if best:
                out.append({
                    "kind": "reassign", "saving_km": best[0], "crew_key": s["key"],
                    "op_from": op_from, "op_to": best[1], "date": s["date"], "place": s["place"],
                    "text": (f"{s['date'].strftime('%d.%m')} {s.get('title') or s['place']}: zmień operatora "
                             f"{operators[op_from]['name']} → {operators[best[1]]['name']}"),
                })

    # 2) zamiana operatorów między dwiema ekipami tego samego dnia
    ops = [op for op in stops_by_op if op in operators]
    for i, a_op in enumerate(ops):
        for b_op in ops[i + 1:]:
            for a in stops_by_op[a_op]:
                if a["date"] < today or not a.get("point") or a.get("ctx") or a.get("with"):
                    continue
                for b in stops_by_op[b_op]:
                    if b["date"] != a["date"] or not b.get("point") or b.get("with"):
                        continue
                    new_a = route_km(operators[a_op]["point"], [x for x in stops_by_op[a_op] if x is not a] + [b])
                    new_b = route_km(operators[b_op]["point"], [x for x in stops_by_op[b_op] if x is not b] + [a])
                    saving = (cost[a_op] + cost[b_op]) - (new_a + new_b)
                    if saving >= MIN_SAVING_KM:
                        out.append({
                            "kind": "swap", "saving_km": saving, "date": a["date"],
                            "crew_key": a["key"], "crew_key_2": b["key"], "op_from": a_op, "op_to": b_op,
                            "text": (f"{a['date'].strftime('%d.%m')}: zamiana — "
                                     f"{a.get('title') or a['place']}: {operators[b_op]['name']}, "
                                     f"{b.get('title') or b['place']}: {operators[a_op]['name']}"),
                        })

    # 3) dni bez operatora — kto dołoży najmniej kilometrów
    for s in unassigned:
        if s["date"] < today or not s.get("point"):
            continue
        best = None
        for op in operators:
            if busy(op, s["date"]):
                continue
            extra = route_km(operators[op]["point"], stops_by_op.get(op, []) + [s]) - cost[op]
            if best is None or extra < best[0]:
                best = (extra, op)
        if best:
            name = operators[best[1]]["name"]
            if best[0] < 1:
                # dołożenie łączy się z jego wyjazdem (nocleg zamiast powrotu do bazy) — trasa się skraca
                how = f"najlepiej pasuje {name} — po drodze, trasa skróci się o ≈{fmt_km(-best[0])} km"
            else:
                how = f"najbliżej ma {name} (+≈{fmt_km(best[0])} km)"
            out.append({
                "kind": "assign", "extra_km": best[0], "saving_km": 0, "crew_key": s["key"],
                "op_to": best[1], "date": s["date"], "place": s["place"],
                "text": f"{s['date'].strftime('%d.%m')} {s.get('title') or s['place']}: brak operatora — {how}",
            })

    # lustrzane propozycje (A bierze wyjazd B albo B bierze wyjazd A — ta sama oszczędność)
    # pokazujemy raz: po zastosowaniu jednej druga i tak znika
    seen, unique = set(), []
    for s in sorted(out, key=lambda x: (x["kind"] == "assign", -x["saving_km"])):
        if s["kind"] != "assign":
            sig = (s["kind"], frozenset((s["op_from"], s["op_to"])), round(s["saving_km"]))
            if sig in seen:
                continue
            seen.add(sig)
        unique.append(s)
    # najpierw brakujący operatorzy (chronologicznie), potem oszczędności od największej
    unique.sort(key=lambda x: (0, x["date"]) if x["kind"] == "assign" else (1, -x["saving_km"]))
    return unique


def gap_stay_hints(base_point, stops, op_name):
    """Operator wraca do bazy między dwoma odległymi wyjazdami w to samo miejsce, a przerwa
    to tylko 1-2 dni — zwykle taniej przenocować na miejscu."""
    hints = []
    chains = _chains([s for s in stops if s.get("point")])
    for prev, nxt in zip(chains, chains[1:]):
        last, first = prev[-1], nxt[0]
        gap = (first["date"] - last["date"]).days - 1
        if gap < 1 or gap > 2:
            continue
        via_base = road_km(last["point"], base_point) + road_km(base_point, first["point"])
        direct = road_km(last["point"], first["point"])
        if via_base - direct >= 400:
            hints.append({
                "date": last["date"],
                "text": (f"{op_name}: {last['date'].strftime('%d.%m')} {last['place']} → "
                         f"{first['date'].strftime('%d.%m')} {first['place']}, a w międzyczasie powrót do bazy "
                         f"(≈{via_base:.0f} km zamiast ≈{direct:.0f} km). Rozważ {gap} "
                         f"{'nocleg' if gap == 1 else 'noclegi'} na miejscu."),
            })
    return hints


def fmt_km(km):
    return f"{int(round(km)):,}".replace(",", " ")


def fmt_hours(h):
    total_min = int(round(h * 60))
    hh, mm = divmod(total_min, 60)
    return f"{hh} h {mm:02d} min" if hh else f"{mm} min"


def daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)
