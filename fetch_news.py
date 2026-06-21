#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Paragraf - generator kokpitu podatkowo-prawnego.
Pobiera kanaly RSS i sklada statyczna strone public/index.html.
Uruchamiany automatycznie przez GitHub Actions (patrz .github/workflows/update.yml).

Aby DODAC lub USUNAC zrodlo: edytuj liste FEEDS ponizej.
Aby wlaczyc podsumowanie AI: dodaj sekret ANTHROPIC_API_KEY w ustawieniach repo.
"""

import os
import re
import html
import json
import datetime
import pathlib
import unicodedata
import urllib.parse

import feedparser
import requests

# ------------------------------------------------------------------ #
#  ZRODLA  -  dodawaj / usuwaj tutaj                                  #
#  (uzywamy AKTYWNEGO wzoru ".feed" - stare adresy rss.* byly martwe) #
# ------------------------------------------------------------------ #
FEEDS = [
    # ============================================================== #
    #  ZASADA: tylko DZIALY scisle podatkowo-prawne, NIE cale gazety. #
    #  Cale portale (rp.pl, money, wprost, BI) wpuszczaly kulture,    #
    #  sport i film ("Plus Minus") - dlatego ich tu NIE ma.           #
    # ============================================================== #

    # --- INFOR: dzialy tematyczne (potwierdzone, swieze, na temat) ---
    {"id": "infor-ks", "name": "INFOR Księgowość",    "cat": "Podatki", "color": "#8a2e2a",
     "url": "https://ksiegowosc.infor.pl/.feed"},
    {"id": "infor-pr", "name": "INFOR Prawo",          "cat": "Prawo",   "color": "#1b5e57",
     "url": "https://www.infor.pl/prawo/.feed"},
    {"id": "infor-ka", "name": "INFOR Kadry / ZUS",    "cat": "Kadry",   "color": "#3b5c8a",
     "url": "https://kadry.infor.pl/.feed"},

    # --- Interpretacje podatkowe KIS / Min. Finansow ---
    # UWAGA: interpretacje-podatkowe.org zostalo zawieszone ("account suspended"),
    # wiec kanal nie dziala - wylaczony. Interpretacje obsluguje teraz zakladka
    # "Interpretacje" (launcher do oficjalnej wyszukiwarki EUREKA).
    # {"id": "kis",      "name": "Interpretacje (KIS)",  "cat": "Podatki", "color": "#6b2e8a",
    #  "url": "https://interpretacje-podatkowe.org/feed"},

    # --- Serwis specjalistyczny (na próbę - sprawdź licznik w logu) ---
    {"id": "podatkibiz", "name": "Podatki.biz",        "cat": "Podatki", "color": "#5c2e6b",
     "url": "https://www.podatki.biz/rss/rss.xml"},

    # ============================================================== #
    #  CALE GAZETY - WYLACZONE, bo daja kulture/sport/film, a nie     #
    #  pozwalaja pobrac samego dzialu podatki/prawo przez RSS.        #
    #  Chcesz ktorys z nich? Wejdz na jego dzial Prawo/Podatki, znajdz#
    #  ikone RSS, przyslij mi adres - podepne TYLKO ten dzial.        #
    #  (INFOR i tak wydaje Dziennik Gazete Prawna, wiec masz pokrycie)#
    # ============================================================== #
    # {"id": "money",  "name": "Money.pl",         "cat":"Finanse","color":"#2e7d6b","url":"https://www.money.pl/rss/"},
    # {"id": "bi",     "name": "Business Insider",  "cat":"Biznes", "color":"#6b6b2a","url":"https://businessinsider.com.pl/.feed"},
    # {"id": "wprost", "name": "Wprost",            "cat":"Biznes", "color":"#8a4a2e","url":"https://www.wprost.pl/rss.xml"},
    # {"id": "rp",     "name": "Rzeczpospolita",    "cat":"Prawo",  "color":"#4a4a8a","url":"https://www.rp.pl/rss/1019"},
    # {"id": "bankier","name": "Bankier.pl",        "cat":"Finanse","color":"#9a6b2e","url":"https://www.bankier.pl/rss/finanse.xml"},
    # {"id": "infor-mf","name":"INFOR Moja firma",  "cat":"Biznes", "color":"#2e6e8c","url":"https://mojafirma.infor.pl/.feed"},
    # Martwe / bez RSS: Gazeta Prawna (kanal zamarl 02.2026), Prawo.pl (brak RSS).
]

MAX_ITEMS = 120                 # ile pozycji trzymamy na stronie
PER_FEED = 60                   # ile najnowszych z jednego zrodla pobieramy do obrobki
UA = "Mozilla/5.0 (compatible; ParagrafBot/1.0; +https://github.com)"

# ILE najnowszych artykulow ma dostac streszczenie AI (2 zdania w karcie).
# Dziala TYLKO, gdy ustawiony jest sekret ANTHROPIC_API_KEY. 0 = wylacz.
SUMMARIZE_TOP = 18

# Filtr trafnosci AI - ODLOZONY (najpierw domykamy dzialy/daty bez AI).
# Gdy zechcesz: ustaw True (wymaga sekretu ANTHROPIC_API_KEY).
AI_FILTER = False

# ------------------------------------------------------------------ #
#  ZRODLA OFICJALNE (publiczne API Kancelarii Sejmu - bez klucza)     #
#  Dziennik Ustaw + Monitor Polski (publikowane akty) oraz projekty   #
#  ustaw (druki sejmowe). To autorytatywne, niezalezne od portali.    #
# ------------------------------------------------------------------ #
OFFICIAL_ENABLED = True
SEJM_TERM = 10                 # kadencja Sejmu (zmien po nowych wyborach)
OFFICIAL_MAX = 12             # ile PROJEKTOW (druki, z etapem) bierzemy z Sejmu
ELI_MAX = 40                  # ile OPUBLIKOWANYCH aktow (Dz.U./MP) do wyszukiwarki ustaw
INFORCE_LOOKUP_MAX = 14       # dla ilu najnowszych aktow dociagac date wejscia w zycie (oszczednosc zapytan API)
RCL_MAX = 20                  # ile PROJEKTOW RZADOWYCH (RCL, przed Sejmem)
RCL_PAGES = 4                 # ile stron listy RCL przejrzec (kazda ~10 pozycji)
OFFICIAL_MAX_AGE_DAYS = 60    # okno swiezosci dla projektow (aktywne w procesie)

OFFICIAL_SRC = {
    "du":   {"name": "Dziennik Ustaw",  "cat": "Legislacja", "color": "#1d3a6b"},
    "mp":   {"name": "Monitor Polski",  "cat": "Legislacja", "color": "#0f5c4a"},
    "sejm": {"name": "Sejm - projekty",  "cat": "Projekty",   "color": "#7a2e5c"},
}

# Z urzedowego "firehose'a" (wszystkie akty/projekty) przepuszczamy tylko te,
# ktorych TYTUL pasuje SCISLE podatkowo/fiskalnie. Rdzenie slow (jak w BLOCK/FOCUS).
# (Swiadomie waskie - wczesniej "oplat"/"finans"/"budzet" wpuszczaly kulture i oswiate.)
OFFICIAL_TOPICS = [
    "podatk", "vat", "cit", "pit", "akcyz", "ryczałt", "ordynacj", "składk",
    "zus", "rachunkow", "cło", "celn", "faktur", "ksef", "jpk", "danin",
    "skarbow", "fiskus", "schemat podatk", "doradc podatkow",
]

# ------------------------------------------------------------------ #
#  ODSIEW  -  to tutaj decydujesz, co odpada                          #
# ------------------------------------------------------------------ #
#
#  JAK DZIALA DOPASOWANIE SLOW (wazne dla polskiego!):
#   - slowo 4+ liter dziala jak RDZEN i lapie odmiany:
#         "loter"  zlapie loteria, loterii, loterię
#         "mecz"   zlapie mecz, meczu, mecze
#         "podatk" zlapie podatek, podatki, podatkowy
#   - skrot 1-3 litery dziala jak CALE SLOWO (zeby nie psuc innych):
#         "vat" zlapie VAT, VAT-u, ale NIE "prywatny"
#         "pit" zlapie PIT, ale NIE "kapitał"
#  Wniosek: do list wpisuj raczej RDZENIE slow, nie pelne formy.

# 1) SWIEZOSC: pomijamy wpisy starsze niz tyle dni (0 = bez limitu).
#    Dzieki temu martwe/zamrozone zrodlo nie wstrzyknie starych newsow.
MAX_AGE_DAYS = 14

# 2) CZARNA LISTA: wpis wypada, jesli zawiera KTORYS z tych rdzeni.
BLOCK = [
    "horoskop", "loter", "lotto", "konkurs", "webinar", "szkoleni",
    "ranking", "notowani", "giełd", "odchudz", "celebryt", "plotk",
    "mecz", "piłk", "rozrywk", "quiz", "kupon", "promo", "black friday",
]

# 3) BIALA LISTA (slownikowa) - dziala TYLKO gdy NIE masz klucza AI.
#    Z kluczem AI relevancje ocenia model (ponizej), wiec ta lista jest pomijana.
#    Scisle podatkowa: bez rdzenia "ustaw" (lapal kazda ustawe!) i bez ogolnych
#    terminow prawnych/biznesowych. Chcesz widziec WSZYSTKO? Ustaw FOCUS = [].
FOCUS = [
    "vat", "cit", "pit", "ksef", "jpk", "podatk", "akcyz", "ryczałt", "ordynacj",
    "składk", "zus", "danin", "faktur", "fiskus", "skarbow", "schemat podatk",
    "mdr", "rachunkow", "interpretacj", "deklaracj", "doradc podatkow",
    "ministerstwo finansów", "estoński",
]


# ------------------------------------------------------------------ #
#  POBIERANIE I PARSOWANIE                                            #
# ------------------------------------------------------------------ #
def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def to_iso(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime.datetime(*t[:6], tzinfo=datetime.timezone.utc).isoformat()
            except Exception:
                pass
    return None


def _norm(text: str) -> str:
    """Małe litery, bez polskich ogonków, bez interpunkcji - do porównań."""
    text = (text or "").lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", text)


def _hit(haystack_padded: str, term: str) -> bool:
    """4+ liter = rdzeń (łapie odmiany); 1-3 litery = całe słowo; spacja = fraza."""
    term = _norm(term).strip()
    if not term:
        return False
    if " " in term:
        return term in haystack_padded
    if len(term) <= 3:
        return f" {term} " in haystack_padded
    return term in haystack_padded


def fetch_all():
    items, live = [], 0
    for f in FEEDS:
        try:
            parsed = feedparser.parse(f["url"], agent=UA)
            entries = parsed.entries or []
            if not entries:
                print(f"  [pusto] {f['name']}")
                continue
            live += 1
            for e in entries[:PER_FEED]:
                title = strip_html(e.get("title", ""))
                link = e.get("link", "") or ""
                if not title or not link:
                    continue
                desc = strip_html(e.get("summary", "") or e.get("description", ""))
                items.append({
                    "title": title,
                    "link": link,
                    "desc": desc[:300],
                    "summary": "",
                    "date": to_iso(e),
                    "src": f["name"],
                    "cat": f["cat"],
                    "color": f["color"],
                    "fid": f["id"],
                })
            print(f"  [ok]    {f['name']}: {len(entries)} wpisów")
        except Exception as ex:
            print(f"  [błąd]  {f['name']}: {ex}")

    items.sort(key=lambda it: it["date"] or "", reverse=True)
    return items, live


def apply_filters(items):
    """Świeżość + czarna lista + biała lista (gdy brak AI) + odsiew duplikatów."""
    now = datetime.datetime.now(datetime.timezone.utc)
    has_ai = bool(os.environ.get("ANTHROPIC_API_KEY"))   # z AI relevancję oceni model
    seen = set()
    out = []
    dropped_age = dropped_block = dropped_focus = dropped_dup = 0

    for it in items:
        # --- świeżość (akty prawne trzymamy dłużej niż newsy) ---
        limit_days = OFFICIAL_MAX_AGE_DAYS if it.get("official") else MAX_AGE_DAYS
        if limit_days and it["date"]:
            try:
                d = datetime.datetime.fromisoformat(it["date"])
                if (now - d).days > limit_days:
                    dropped_age += 1
                    continue
            except Exception:
                pass

        hay = " " + _norm(it["title"] + " " + it["desc"]) + " "

        # --- czarna lista ---
        if any(_hit(hay, w) for w in BLOCK):
            dropped_block += 1
            continue

        # --- biała lista słownikowa: tylko gdy NIE ma AI (i nie dla oficjalnych) ---
        if FOCUS and not has_ai and not it.get("official") and not any(_hit(hay, w) for w in FOCUS):
            dropped_focus += 1
            continue

        # --- duplikaty (po znormalizowanym tytule) ---
        key = _norm(it["title"])[:80]
        if key in seen:
            dropped_dup += 1
            continue
        seen.add(key)
        out.append(it)

    print(f"  Odsiano: {dropped_age} starych, {dropped_block} z czarnej listy, "
          f"{dropped_focus} poza tematem, {dropped_dup} duplikatów.")
    return out


# ------------------------------------------------------------------ #
#  OPCJONALNE PODSUMOWANIE AI (jesli ustawiony ANTHROPIC_API_KEY)     #
# ------------------------------------------------------------------ #
def ai_summary(items):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("  (bez AI - brak sekretu ANTHROPIC_API_KEY)")
        return None
    top = items[:25]
    lst = "\n".join(
        f"{i+1}. [{it['cat']}] {it['title']}" + (f" - {it['desc'][:150]}" if it["desc"] else "")
        for i, it in enumerate(top)
    )
    prompt = (
        "Jesteś asystentem podatkowo-prawnym dla profesjonalisty w Polsce. Poniżej najnowsze "
        "nagłówki ze źródeł podatkowych i prawnych. Wybierz 5-7 NAJWAŻNIEJSZYCH rzeczy, które warto "
        "dziś znać (zmiany przepisów, terminy, istotne interpretacje lub orzeczenia, KSeF, VAT, CIT, "
        "PIT, ZUS). Każdą zapisz jako jedno krótkie, konkretne zdanie po polsku, zaczynając wiersz od "
        "myślnika. Pomiń clickbait i powtórzenia. Bez wstępu i zakończenia.\n\nNagłówki:\n" + lst
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 700,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=45,
        )
        data = r.json()
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        out = "\n".join(parts).strip()
        print("  [AI] podsumowanie wygenerowane" if out else "  [AI] pusta odpowiedź")
        return out or None
    except Exception as ex:
        print(f"  [AI błąd] {ex}")
        return None


# ------------------------------------------------------------------ #
#  STRESZCZENIA POSZCZEGOLNYCH ARTYKULOW (2 zdania w karcie)          #
#  Dziala tylko z ANTHROPIC_API_KEY. Pobiera tresc artykulu i prosi   #
#  Claude (Haiku) o krotkie, rzeczowe streszczenie.                   #
# ------------------------------------------------------------------ #
def _article_text(url: str) -> str:
    """Pobiera artykuł i wyciąga główną treść (bez nawigacji i reklam)."""
    try:
        import trafilatura
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        text = trafilatura.extract(r.text, include_comments=False, include_tables=False)
        return (text or "").strip()
    except Exception:
        return ""


def _summarize_one(it: dict, key: str) -> None:
    src = _article_text(it["link"]) or it["desc"]
    if not src:
        return
    prompt = (
        "Streść poniższy artykuł w DOKŁADNIE dwóch krótkich zdaniach po polsku - rzeczowo "
        "i konkretnie, bez clickbaitu i bez ogólnego wstępu. Podaj najważniejszy fakt: "
        "co się zmienia albo co warto wiedzieć.\n\nArtykuł:\n" + src[:2200]
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 220,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=40,
        )
        data = r.json()
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        it["summary"] = "\n".join(parts).strip().replace("\n", " ")
    except Exception as ex:
        print(f"    [streszczenie błąd] {ex}")


def summarize_articles(items) -> None:
    """Uzupełnia pole 'summary' dla najnowszych SUMMARIZE_TOP artykułów."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or SUMMARIZE_TOP <= 0:
        if not key:
            print("  (bez streszczeń artykułów - brak sekretu ANTHROPIC_API_KEY)")
        return
    targets = [it for it in items if not it.get("official")][:SUMMARIZE_TOP]
    print(f"  Streszczam {len(targets)} najnowszych artykułów…")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda it: _summarize_one(it, key), targets))
    done = sum(1 for it in targets if it.get("summary"))
    print(f"  Streszczono: {done}/{len(targets)} artykułów.")


# ------------------------------------------------------------------ #
#  ZRODLA OFICJALNE - pobieranie z API Sejm/ELI                       #
# ------------------------------------------------------------------ #
def _date_iso(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = datetime.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return d.isoformat()
    except Exception:
        return None


def _api_get(url: str, timeout: int = 25):
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=timeout)
        if r.status_code != 200:
            print(f"  [API {r.status_code}] {url}")
            return None
        return r.json()
    except Exception as ex:
        print(f"  [API błąd] {url}: {ex}")
        return None


def _http_get_text(url: str, timeout: int = 25):
    """Pobiera surowy HTML (RCL nie ma API - parsujemy stronę)."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        if r.status_code != 200:
            print(f"  [RCL {r.status_code}] {url}")
            return None
        return r.text
    except Exception as ex:
        print(f"  [RCL błąd] {url}: {ex}")
        return None


def _rcl_parse_into(html_text, out, seen):
    """Wyłuskuje projekty z jednej strony HTML listy RCL do out (deduplikacja po seen).
    Odporne na zagnieżdżone znaczniki w treści linku."""
    if not html_text:
        return
    for m in re.finditer(r'href="(/projekt/\d+[^"]*)"[^>]*>(.*?)</a>', html_text, re.DOTALL):
        path = m.group(1)
        if path in seen:
            continue
        inner = re.sub(r"<[^>]*>", " ", m.group(2))          # zdejmij ewentualne tagi w środku
        title = re.sub(r"\s+", " ", html.unescape(inner)).strip()
        if len(title) < 6 or not _topic_ok(title):
            continue
        seen.add(path)
        tail = html_text[m.end():m.end() + 600]
        # Numer (UD116 itp.) bierzemy z WŁASNEGO tytułu lub z najbliższego otoczenia.
        num = re.search(r"\b([A-Z]{2}\d{1,4})\b", title) or re.search(r"\b([A-Z]{2}\d{1,4})\b", tail[:120])
        dm = re.search(r"\b(\d{2})-(\d{2})-(\d{4})\b", tail)
        date = None
        if dm:
            try:
                date = datetime.datetime(int(dm.group(3)), int(dm.group(2)), int(dm.group(1)),
                                         tzinfo=datetime.timezone.utc).isoformat()
            except Exception:
                date = None
        sygn = num.group(1) if num else ""
        out.append({
            "title": title,
            "link": "https://legislacja.rcl.gov.pl" + path,
            "desc": ("Projekt rządowy" + (" · " + sygn if sygn else "")).strip(),
            "summary": "", "date": date,
            "src": "Rząd (RCL)", "cat": "Projekty", "color": "#8a5a2e",
            "fid": "off-rcl", "official": True, "track": True,
            "step": 1, "stage": "Prace w rządzie" + (" (" + sygn + ")" if sygn else ""),
        })
        if len(out) >= RCL_MAX:
            return


_RCL_STAGE_KW = ("lobbing", "uzgodnie", "konsultacj", "opiniowan", "komitet", "komisj",
                 "rada ministr", "radzie ministr", "potwierdz", "skierowan", "notyfikacj",
                 "rozpatrz", "przyjęc", "przyjet")


def _rcl_stages(page_text):
    """Wyciąga wewnętrzne etapy rządowe ze STRONY projektu RCL
    (np. Uzgodnienia / Konsultacje / Opiniowanie / Komitet Stały / Komisja Prawnicza).
    Zwraca listę {n, name, date, state} gdzie state ∈ done|cur|pending."""
    if not page_text:
        return []
    t = re.sub(r"<[^>]*>", " ", page_text)
    t = re.sub(r"[·•|]", " ", t)
    t = re.sub(r"\s+", " ", t)
    items = []
    pat = re.compile(r"(\d{1,2})\.\s+(.{3,75}?)"
                     r"(?=\s+Data ostatniej modyfikacji:|\s+\d{1,2}\.\s|\s+Rządowe Centrum|"
                     r"\s+Mapa strony|\s+Pomoc\b|\s+Kontakt\b|$)"
                     r"(?:\s+Data ostatniej modyfikacji:\s*(\d{2}-\d{2}-\d{4}))?")
    seen = set()
    for m in pat.finditer(t):
        name = re.sub(r"\s*Data ostatniej modyfikacji.*$", "", m.group(2)).strip()
        low = name.lower()
        if not any(k in low for k in _RCL_STAGE_KW):
            continue
        key = (m.group(1), low[:20])
        if key in seen:
            continue
        seen.add(key)
        items.append({"n": int(m.group(1)), "name": name, "date": m.group(3)})
    if not items:
        return []
    # Strona RCL bywa uporzadkowana niechronologicznie (czasem od najnowszego).
    # Sortujemy etapy po dacie rosnaco (niedatowane = wczesne, na poczatek),
    # zeby daty szly w dol i "biezacy" byl naprawde najpozniejszy.
    def _dkey(it):
        d = it.get("date")
        if d:
            try:
                dd, mm, yy = d.split("-")
                return (1, int(yy), int(mm), int(dd))
            except Exception:
                pass
        return (0, 0, 0, 0)
    items.sort(key=_dkey)
    dated = [i for i, it in enumerate(items) if it["date"]]
    cur = dated[-1] if dated else 0
    for i, it in enumerate(items):
        it["state"] = "done" if i < cur else ("cur" if i == cur else "pending")
    return items


def _rcl_status(page_text):
    """Rozpoznaje status projektu z jego STRONY (nie z listy):
    'left'  - opuścił rząd (dalszy ciąg w Sejmie/Dz.U.),
    'closed'- zamknięty (wycofany/niezakończony w rządzie),
    'in_gov'- wciąż w rządzie,
    None    - nie udało się pobrać strony."""
    if not page_text:
        return None
    low = page_text.lower()
    if (re.search(r"sta[łl]a?\s*si[ęe]\s*ustaw", low) or "dołączono do projektu" in low
            or re.search(r"kontynuowan[ya]\s+(?:pod\s+nr|jako)", low)):
        return "became_law"      # zakonczony: stal sie ustawa / dolaczony / kontynuowany pod innym nr
    if "na stronach sejmu" in low or "dalszy ciąg procesu legislacyjnego" in low:
        return "left"
    if re.search(r"status projektu:\s*zamkn", low):
        return "closed"
    return "in_gov"


def _rcl_keep_in_gov(items, limit=12):
    """Tracker 'w rządzie' pokazuje TYLKO projekty realnie w rządzie.
    Sprawdzamy stronę każdego (do `limit`); te, które poszły dalej, odpadają.
    Przy okazji doczepiamy wewnętrzne etapy rządowe do karty."""
    kept = []
    for it in items[:limit]:
        page = _http_get_text(it["link"], timeout=12)
        st = _rcl_status(page)
        if st in (None, "in_gov"):     # None = brak pobrania -> zostaw (świeże z listy)
            it["stages"] = _rcl_stages(page)
            kept.append(it)
    return kept


def _rcl_projects():
    """ETAP RZĄDOWY: projekty ustaw z wykazu RCL (legislacja.rcl.gov.pl),
    zanim trafią do Sejmu. Brak API - parsujemy listę regexem (defensywnie).
    Łączymy dwa źródła: (1) wyszukiwarkę RCL po słowach podatkowych - łapie też
    STARSZE projekty (np. UD116), oraz (2) najnowsze strony listy."""
    out, seen = [], set()
    base = "https://legislacja.rcl.gov.pl/lista?typeId=2"   # typeId=2 = projekty ustaw
    # (1) Wyszukiwarka RCL po hasłach podatkowych (param `title`) - łapie też starsze.
    for kw in ("podatek", "podatku", "VAT", "akcyza", "KSeF", "PIT", "CIT", "Krajowy System e-Faktur"):
        if len(out) >= RCL_MAX:
            break
        url = f"{base}&title={urllib.parse.quote(kw)}"
        _rcl_parse_into(_http_get_text(url, timeout=20), out, seen)
    # (2) Najnowsze strony listy (świeży przegląd procesu); paginacja = `pNumber`.
    for page in range(1, RCL_PAGES + 1):
        if len(out) >= RCL_MAX:
            break
        url = base if page == 1 else f"{base}&pNumber={page}"
        _rcl_parse_into(_http_get_text(url, timeout=20), out, seen)
    out = _rcl_keep_in_gov(out, limit=12)
    print(f"  [Rząd (RCL)] dopasowano {len(out)} projektów rządowych (po weryfikacji statusu).")
    return out



def _official_date_ok(iso: str) -> bool:
    """PROJEKTY pokazujemy tylko z sensowną, świeżą datą (nie z przyszłości,
    nie starszą niż okno). Odcina błędne daty typu 'rok 2206'."""
    if not iso:
        return False
    try:
        d = datetime.datetime.fromisoformat(iso)
        days = (datetime.datetime.now(datetime.timezone.utc) - d).days
        return -1 <= days <= OFFICIAL_MAX_AGE_DAYS
    except Exception:
        return False


def _act_date_ok(iso: str) -> bool:
    """OPUBLIKOWANE akty do wyszukiwarki - szersze okno (cały rok), ale nadal
    odrzuca przyszłe/błędne roczniki (np. '2206')."""
    if not iso:
        return False
    try:
        d = datetime.datetime.fromisoformat(iso)
        now = datetime.datetime.now(datetime.timezone.utc)
        if (d - now).days > 1:          # nie z przyszłości
            return False
        return now.year - 1 <= d.year <= now.year + 1
    except Exception:
        return False


# Cztery etapy sciezki legislacyjnej (do "schodkow" w kokpicie).
LEGIS_STEPS = ["Rząd", "Sejm", "Prezydent", "Dz.U."]


def _process_stage(term, num):
    """Najnowszy etap procesu legislacyjnego dla druku (defensywnie)."""
    data = _api_get(f"https://api.sejm.gov.pl/sejm/term{term}/processes/{num}", timeout=12)
    if not isinstance(data, dict):
        return None
    stages = data.get("stages")
    if not isinstance(stages, list) or not stages:
        return None

    def _name(s):
        if isinstance(s, dict):
            return str(s.get("stageName") or s.get("name") or "")
        return str(s) if s else ""

    last = _name(stages[-1]).strip()
    allnames = " ".join(_name(s) for s in stages)
    return {"name": last, "all": allnames}


def _legis_step(allnames: str, published: bool) -> int:
    """Na ktorym z 4 etapow (Rzad→Sejm→Prezydent→Dz.U.) jest druk SEJMOWY.
    Druk jest juz w Sejmie, wiec minimum to etap 2."""
    if published:
        return 4
    a = _norm(allnames or "")
    if "prezydent" in a or "podpis" in a:
        return 3
    return 2


def _topic_ok(title: str) -> bool:
    hay = " " + _norm(title) + " "
    return any(_hit(hay, w) for w in OFFICIAL_TOPICS)


def _eli_items(pub: str):
    """Dziennik Ustaw (DU) lub Monitor Polski (MP) - najnowsze akty na temat."""
    year = datetime.datetime.now(datetime.timezone.utc).year
    data = _api_get(f"https://api.sejm.gov.pl/eli/acts/{pub}/{year}")
    if not data:
        return []
    raw = data.get("items", []) or []
    raw.sort(key=lambda a: a.get("announcementDate") or a.get("changeDate") or "", reverse=True)
    meta = OFFICIAL_SRC["du" if pub == "DU" else "mp"]
    out = []
    for a in raw:
        title = (a.get("title") or "").strip()
        if not title or not _topic_ok(title):
            continue
        date = _date_iso(a.get("announcementDate") or a.get("changeDate", "")[:10])
        if not _act_date_ok(date):
            continue
        eli = a.get("ELI", "")
        parts = eli.split("/")
        link = (f"https://api.sejm.gov.pl/eli/acts/{eli}/text.pdf" if len(parts) == 3
                else f"https://api.sejm.gov.pl/eli/acts/{pub}/{year}")
        typ = (a.get("type") or "").strip()
        sig = (a.get("displayAddress") or "").strip()
        desc = (typ + " · " + sig).strip(" ·")
        # date wejscia w zycie jest tylko w szczegolach aktu (nie w listingu rocznym) -
        # dociagamy ja tylko dla kilkunastu NAJNOWSZYCH aktow (tylko one moga miec date przyszla).
        inforce = None
        if len(out) < INFORCE_LOOKUP_MAX and len(parts) == 3:
            detail = _api_get(f"https://api.sejm.gov.pl/eli/acts/{eli}")
            if detail:
                inforce = _date_iso(detail.get("entryIntoForce") or "")
        out.append({
            "title": title, "link": link, "desc": desc, "summary": "",
            "date": date, "inforce": inforce,
            "src": meta["name"], "cat": meta["cat"], "color": meta["color"],
            "fid": "off-" + ("du" if pub == "DU" else "mp"), "official": True,
            "track": True, "step": 4, "stage": "Opublikowano",
        })
        if len(out) >= ELI_MAX:
            break
    print(f"  [{meta['name']}] dopasowano {len(out)} aktów.")
    return out


def _sejm_prints():
    """Projekty ustaw i inne druki sejmowe - najnowsze na temat, z etapem procesu."""
    data = _api_get(f"https://api.sejm.gov.pl/sejm/term{SEJM_TERM}/prints?sort_by=-documentDate&limit=80")
    if data is None:
        return []
    raw = data if isinstance(data, list) else data.get("items", [])
    raw = [p for p in raw if isinstance(p, dict)]
    raw.sort(key=lambda p: p.get("documentDate") or p.get("changeDate") or "", reverse=True)
    meta = OFFICIAL_SRC["sejm"]
    out = []
    for p in raw:
        title = (p.get("title") or "").strip()
        num = str(p.get("number", "")).strip()
        if not title or not num or not _topic_ok(title):
            continue
        date = _date_iso((p.get("documentDate") or p.get("changeDate") or "")[:10])
        if not _official_date_ok(date):
            continue
        out.append({
            "title": title,
            "link": f"https://api.sejm.gov.pl/sejm/term{SEJM_TERM}/prints/{num}/{num}.pdf",
            "desc": f"Druk sejmowy nr {num}", "summary": "",
            "date": date,
            "src": meta["name"], "cat": meta["cat"], "color": meta["color"],
            "fid": "off-sejm", "official": True,
            "track": True, "step": 2, "stage": "Wpłynęło do Sejmu", "_num": num,
        })
        if len(out) >= OFFICIAL_MAX:
            break

    # Wzbogac kazdy projekt o aktualny ETAP procesu legislacyjnego (rownolegle).
    from concurrent.futures import ThreadPoolExecutor

    def _enrich(it):
        st = _process_stage(SEJM_TERM, it["_num"])
        if st:
            it["step"] = _legis_step(st["all"], published=False)
            if st["name"]:
                it["stage"] = st["name"]
        return it
    try:
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(_enrich, out))
    except Exception as e:
        print(f"  [etapy] pominięto wzbogacanie: {e}")

    print(f"  [{meta['name']}] dopasowano {len(out)} projektów.")
    return out


def fetch_official():
    if not OFFICIAL_ENABLED:
        return [], 0
    print("Pobieram źródła oficjalne (API Sejm/ELI)…")
    items = []
    for label, getter in (("Rząd (RCL)", _rcl_projects),
                          ("Dz.U.", lambda: _eli_items("DU")),
                          ("Monitor Polski", lambda: _eli_items("MP")),
                          ("Sejm - projekty", _sejm_prints)):
        try:
            items += getter()
        except Exception as e:
            print(f"  [oficjalne: {label} POMINIĘTE z powodu błędu] {e}")
    live = len({it["fid"] for it in items})
    return items, live


# ------------------------------------------------------------------ #
#  FILTR AI RELEVANCJI - zostawia tylko ŚCIŚLE podatkowe newsy.       #
#  Jedno zbiorcze zapytanie do modelu. Bez klucza: pomijany (zostaje  #
#  filtr slownikowy FOCUS). Przy bledzie: nie odsiewa (bezpiecznie).  #
# ------------------------------------------------------------------ #
def ai_filter_relevance(items):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return items
    cand = [it for it in items if not it.get("official")]
    if not cand:
        return items
    listing = "\n".join(f"{i}. {it['title']}" for i, it in enumerate(cand))
    prompt = (
        "Poniżej ponumerowana lista nagłówków. Zwróć TYLKO numery tych, które dotyczą "
        "ŚCIŚLE polskich PODATKÓW lub rozliczeń podatkowych: VAT, CIT, PIT, akcyza, ryczałt, "
        "KSeF, JPK, schematy podatkowe / MDR, ordynacja podatkowa, interpretacje i orzeczenia "
        "podatkowe, kontrole skarbowe, ulgi i odliczenia, składki ZUS/zdrowotne w ujęciu rozliczeń, "
        "zmiany ustaw podatkowych i ich podpisanie przez prezydenta. "
        "NIE zaliczaj: ogólnej polityki, prawa pracy/kodeksu pracy samego w sobie, kultury, oświaty, "
        "ogólnej gospodarki, giełdy, rynków, biznesu bez wyraźnego wątku podatkowego. "
        "Odpowiedz wyłącznie numerami oddzielonymi przecinkami (np. 0,3,7). Bez żadnych słów.\n\n"
        + listing
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 500,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=45,
        )
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        keep = {int(x) for x in re.findall(r"\d+", text)}
        if not keep:
            print("  [AI filtr] brak trafień - nie odsiewam (zostawiam wszystko)")
            return items
        approved = {id(cand[i]) for i in keep if 0 <= i < len(cand)}
        before = len(cand)
        result = [it for it in items if it.get("official") or id(it) in approved]
        print(f"  [AI filtr] ściśle podatkowe: {len(approved)}/{before} newsów")
        return result
    except Exception as ex:
        print(f"  [AI filtr błąd] {ex} - nie odsiewam")
        return items


# ------------------------------------------------------------------ #
#  SZABLON STRONY                                                     #
# ------------------------------------------------------------------ #
TEMPLATE = r'''<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Paragraf — kokpit podatkowo-prawny</title>
<style>
  :root{
    --paper:#e9edf2;--surface:#fcfcfa;--ink:#16233b;--ink-soft:#586176;--ink-faint:#8a93a4;
    --line:#d7dde5;--accent:#8a2e2a;--radius:14px;
    --serif:"Iowan Old Style","Palatino Linotype","Book Antiqua","Hoefler Text",Georgia,"Times New Roman",serif;
    --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{background:var(--paper);color:var(--ink);font-family:var(--sans);font-size:16px;line-height:1.5;-webkit-font-smoothing:antialiased}
  a{color:inherit}
  .wrap{max-width:768px;margin:0 auto;padding:0 20px 96px}

  .masthead{position:sticky;top:0;z-index:20;background:rgba(233,237,242,.86);
    backdrop-filter:saturate(160%) blur(10px);-webkit-backdrop-filter:saturate(160%) blur(10px);
    border-bottom:1px solid var(--line)}
  .masthead-in{max-width:768px;margin:0 auto;padding:16px 20px 12px}
  .brandrow{display:flex;align-items:center;gap:14px}
  .seal{flex:none;width:46px;height:46px;border-radius:11px;background:var(--accent);color:#f3e9df;
    display:grid;place-items:center;font-family:var(--serif);font-size:28px;font-weight:600;line-height:1;
    box-shadow:inset 0 0 0 1px rgba(255,255,255,.14),0 2px 6px rgba(138,46,42,.28);user-select:none}
  .brandtext{flex:1;min-width:0}
  .wordmark{font-family:var(--serif);font-weight:600;font-size:27px;letter-spacing:-.01em;line-height:1;margin:0}
  .subtitle{margin-top:4px;font-size:11.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--ink-soft);font-weight:600}
  .actions{margin-left:auto}
  .iconbtn{border:1px solid var(--line);background:var(--surface);color:var(--ink);font-family:var(--sans);
    font-size:13px;font-weight:600;padding:9px 14px;border-radius:10px;cursor:pointer;white-space:nowrap;transition:.15s}
  .iconbtn:hover{border-color:var(--ink-faint)}
  .iconbtn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

  .stats{display:flex;gap:22px;margin-top:13px;padding-top:11px;border-top:1px solid var(--line);flex-wrap:wrap}
  .stat{display:flex;flex-direction:column;gap:1px}
  .stat .num{font-family:var(--serif);font-size:19px;font-weight:600;line-height:1}
  .stat .lab{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-faint);font-weight:600}
  .livedot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--accent);margin-right:5px;
    vertical-align:middle;animation:pulse 2.2s ease-in-out infinite}

  .controls{padding:18px 0 6px}
  .search{width:100%;border:1px solid var(--line);background:var(--surface);border-radius:11px;
    padding:12px 14px;font-family:var(--sans);font-size:15px;color:var(--ink)}
  .search::placeholder{color:var(--ink-faint)}
  .search:focus{outline:none;border-color:var(--accent)}
  .chips{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px}
  .chip{border:1px solid var(--line);background:var(--surface);padding:6px 11px 6px 9px;border-radius:999px;
    cursor:pointer;font-size:12.5px;font-weight:600;color:var(--ink-soft);display:inline-flex;align-items:center;gap:7px;transition:.15s;user-select:none}
  .chip:hover{border-color:var(--ink-faint)}
  .chip .dot{width:8px;height:8px;border-radius:50%;flex:none}
  .chip[data-on="0"]{opacity:.4}
  .chip[data-on="0"] .dot{filter:grayscale(1)}
  .chip[data-empty="1"]{border-style:dashed}
  .chip:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  /* chipy dziedzin podatkowych + tagi na kartach */
  .domrow{display:flex;align-items:center;gap:9px;margin-top:11px;flex-wrap:wrap}
  .domlab{font-size:10.5px;font-weight:700;color:var(--ink-faint);text-transform:uppercase;letter-spacing:.05em;flex:none}
  .dchips{display:flex;gap:6px;flex-wrap:wrap}
  .dchip{border:1px solid var(--dc);background:transparent;color:var(--dc);padding:3px 10px;border-radius:999px;
    font-size:11.5px;font-weight:600;cursor:pointer;font-family:inherit;transition:.15s;user-select:none}
  .dchip:hover{background:rgba(0,0,0,.04)}
  .dchip.on{background:var(--dc);color:#fff}
  .cardtags{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}
  .dtag{display:inline-block;font-size:9.5px;font-weight:700;letter-spacing:.02em;padding:1px 7px;
    border-radius:999px;color:#fff;line-height:1.5}
  /* data wejścia w życie (vacatio legis) */
  .inforce{font-size:12.5px;margin:7px 0 2px;padding:5px 10px;border-radius:8px;font-weight:600;line-height:1.4}
  .inforce-soon{background:rgba(176,124,42,.12);border:1px solid rgba(176,124,42,.32);color:#8a5a2e}
  .inforce-soon .ifd{font-weight:700;white-space:nowrap}
  .inforce-past{background:transparent;color:var(--ink-faint);font-weight:500;padding:2px 0}

  .summary-panel{margin-top:16px;border:1px solid var(--line);background:var(--surface);border-radius:var(--radius);
    padding:18px 20px;box-shadow:0 1px 2px rgba(22,35,59,.04)}
  .summary-head{display:flex;align-items:center;gap:8px;margin-bottom:10px}
  .summary-head .t{font-family:var(--serif);font-size:17px;font-weight:600}
  .summary-body{font-size:14.5px;color:var(--ink)}
  .summary-body ul{margin:0;padding:0;list-style:none}
  .summary-body li{position:relative;padding:7px 0 7px 22px;border-bottom:1px solid var(--line)}
  .summary-body li:last-child{border-bottom:none}
  .summary-body li:before{content:"§";position:absolute;left:0;top:7px;font-family:var(--serif);color:var(--accent);font-weight:600}
  .summary-note{margin-top:10px;font-size:12px;color:var(--ink-faint)}

  .daysep{display:flex;align-items:center;gap:12px;margin:30px 0 14px}
  .daysep .lab{font-family:var(--serif);font-size:14px;font-weight:600;color:var(--ink-soft);text-transform:capitalize;white-space:nowrap}
  .daysep .rule{flex:1;height:1px;background:var(--line)}

  .card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px;
    margin-bottom:11px;transition:.16s;position:relative;overflow:hidden}
  .card:before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--ccol,var(--line))}
  .card:hover{border-color:var(--ink-faint);transform:translateY(-1px);box-shadow:0 4px 14px rgba(22,35,59,.07)}
  .card .src{display:inline-flex;align-items:center;gap:7px;font-size:11.5px;font-weight:700;color:var(--ccol,var(--ink-soft));margin-bottom:7px}
  .card .src .dot{width:7px;height:7px;border-radius:50%;background:var(--ccol)}
  .card .src .cat{color:var(--ink-faint);font-weight:600;text-transform:uppercase;letter-spacing:.08em;font-size:10px}
  .card .title{font-family:var(--serif);font-size:18.5px;font-weight:600;line-height:1.32;letter-spacing:-.005em;text-decoration:none;display:block}
  .card a.title:hover{text-decoration:underline;text-decoration-color:var(--ccol);text-underline-offset:3px}
  .card .desc{margin-top:6px;color:var(--ink-soft);font-size:14px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .card .desc.sum{-webkit-line-clamp:4;color:var(--ink)}
  .aitag{font-size:9.5px;font-weight:700;color:var(--accent);text-transform:uppercase;letter-spacing:.07em;
    border:1px solid var(--accent);border-radius:5px;padding:1px 5px;margin-right:6px;vertical-align:1px}
  .card .meta{margin-top:10px;font-size:12px;color:var(--ink-faint);font-weight:500}

  .empty{text-align:center;padding:60px 20px;color:var(--ink-soft)}
  .empty .ic{font-family:var(--serif);font-size:44px;color:var(--line);margin-bottom:8px}
  .empty h3{font-family:var(--serif);font-weight:600;font-size:19px;margin:0 0 6px;color:var(--ink)}

  footer{margin-top:40px;padding-top:18px;border-top:1px solid var(--line);font-size:12px;color:var(--ink-faint);line-height:1.6}
  footer b{color:var(--ink-soft)}

  /* --- Zakładki --- */
  .tabs{display:flex;gap:4px;margin-bottom:16px;border-bottom:1px solid var(--line);flex-wrap:wrap}
  .tab{appearance:none;border:none;background:none;cursor:pointer;font-family:var(--sans);
    font-size:14.5px;font-weight:600;color:var(--ink-faint);padding:10px 16px;position:relative;
    border-bottom:2px solid transparent;margin-bottom:-1px;transition:color .15s}
  .tab:hover{color:var(--ink-soft)}
  .tab.on{color:var(--accent);border-bottom-color:var(--accent)}
  .tab:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
  /* drugi poziom: czlonkowie sekcji (Narzedzia / Prawo) */
  .memberbar{display:flex;gap:3px;margin:-4px 0 18px;flex-wrap:wrap}
  .memtab{appearance:none;border:none;cursor:pointer;font-family:var(--sans);font-size:13px;font-weight:600;
    color:var(--ink-faint);background:none;padding:6px 14px;border-radius:8px;transition:all .14s}
  .memtab:hover{color:var(--ink-soft);background:rgba(40,30,20,.04)}
  .memtab.on{color:var(--accent);background:rgba(138,46,42,.09)}
  .memtab:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
  [hidden]{display:none!important}
  .showall{display:block;width:100%;margin-top:16px;padding:11px;background:var(--surface);
    border:1px solid var(--line);border-radius:var(--radius);cursor:pointer;font-family:var(--sans);
    font-size:13px;font-weight:600;color:var(--ink-soft);transition:.15s}
  .showall:hover{border-color:var(--ink-faint);color:var(--ink)}

  /* --- Wyszukiwanie na żywo --- */
  .searchrow{display:flex;gap:8px;align-items:stretch}
  .searchrow .search{flex:1}
  .livebtn{flex:none;border:1px solid var(--accent);background:var(--accent);color:#f3e9df;border-radius:12px;
    padding:0 18px;font-family:var(--sans);font-size:13.5px;font-weight:600;cursor:pointer;white-space:nowrap;transition:.15s}
  .livebtn:hover{filter:brightness(1.08)}
  .livebtn:disabled{opacity:.55;cursor:wait}
  .livehint{margin:9px 2px 0;font-size:12px;color:var(--ink-faint);line-height:1.5}
  .livehint b{color:var(--ink-soft)}
  #liveResults{margin:20px 0}
  #liveResults:empty{display:none}
  .live-status{padding:16px;border:1px dashed var(--line);border-radius:var(--radius);color:var(--ink-soft);font-size:13.5px;text-align:center}
  .live-sec-head{font-family:var(--serif);font-size:17px;font-weight:600;color:var(--ink);margin:0 0 12px;padding-bottom:9px;border-bottom:2px solid var(--accent)}

  /* --- Ścieżka legislacyjna --- */
  #legis{margin-bottom:30px}
  .lsec-head{display:flex;align-items:baseline;gap:10px;margin:4px 0 14px;padding-bottom:10px;border-bottom:2px solid var(--accent)}
  .lsec-head .lt{font-family:var(--serif);font-size:20px;font-weight:600;color:var(--ink)}
  .lsec-head .lcount{font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:var(--ink-faint);font-weight:600}
  /* sekcja "Wchodza w zycie" - osobna zakladka; naglowek w kolorze bursztynu */
  #legisSoon .lsec-head{border-bottom-color:#b07c2a}
  #legisSoon .lsec-head .lt{color:#8a5a2e}
  .lgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
  .lcard{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--ccol,var(--accent));
    border-radius:var(--radius);padding:15px 17px 14px;display:flex;flex-direction:column}
  .lhead{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px}
  .lsrc{display:inline-flex;align-items:center;gap:7px;font-size:11px;font-weight:700;letter-spacing:.06em;
    text-transform:uppercase;color:var(--ink-soft)}
  .lsrc .dot{width:8px;height:8px;border-radius:50%;background:var(--ccol,var(--accent))}
  .lwhen{font-size:11.5px;color:var(--ink-faint);white-space:nowrap}
  .ltitle{font-family:var(--serif);font-size:16px;line-height:1.3;font-weight:600;color:var(--ink);
    text-decoration:none;display:block}
  .ltitle:hover{color:var(--accent);text-decoration:underline}
  .stepper{display:flex;margin:14px 0 4px}
  .step{flex:1;text-align:center;position:relative;font-size:10.5px;color:var(--ink-faint);font-weight:600}
  .step i{display:block;width:13px;height:13px;border-radius:50%;background:#cdd4de;margin:0 auto 6px;
    border:2px solid #cdd4de;position:relative;z-index:1}
  .step::before{content:"";position:absolute;top:6px;left:-50%;width:100%;height:2px;background:#cdd4de;z-index:0}
  .step:first-child::before{display:none}
  .step.on{color:var(--ink-soft)}
  .step.on i{background:var(--accent);border-color:var(--accent)}
  .step.on::before{background:var(--accent)}
  .step.cur{color:var(--accent)}
  .step.cur i{box-shadow:0 0 0 4px rgba(138,46,42,.16)}
  .lstage{margin-top:9px;font-size:12px;color:var(--ink-soft);line-height:1.35}
  .lstage span{display:inline-block;font-size:9.5px;letter-spacing:.07em;text-transform:uppercase;font-weight:700;
    color:var(--ink-faint);border:1px solid var(--line);border-radius:5px;padding:1px 5px;margin-right:6px}
  .lnote{margin-top:7px;font-size:11.5px;color:#1d3a6b;background:rgba(29,58,107,.06);
    border-radius:6px;padding:6px 9px;line-height:1.4}
  .lcard.is-closed{opacity:.62}
  .lcard.is-left{border-left-color:#1d3a6b}
  .lcard.is-done{border-left-color:#1b6e4f}
  .lnote-done{color:#0f5c3a;background:rgba(27,110,79,.08);font-weight:600}
  .lnote-warn{color:#8a5a00;background:rgba(180,120,0,.10);font-weight:600}
  /* terminarz podatkowy */
  .t-today{font-size:13px;color:var(--ink-soft);margin:2px 2px 14px}
  .tgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
  .tcard{border:1px solid var(--line);border-left:3px solid var(--ccol);background:var(--surface);
    border-radius:var(--radius);padding:13px 15px;box-shadow:0 1px 2px rgba(22,35,59,.04)}
  .tcard.t-soon{background:rgba(180,120,0,.05)}
  .tcard.t-now{background:rgba(138,46,42,.06);border-color:rgba(138,46,42,.3)}
  .thead{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:6px}
  .tdate{font-family:var(--serif);font-size:19px;font-weight:600;color:var(--ink)}
  .tdate i{font-style:normal;font-size:12px;color:var(--ink-faint);font-family:var(--sans);margin-left:3px}
  .twhen{font-size:11.5px;font-weight:700;color:var(--ink-faint);white-space:nowrap}
  .twhen.t-soon{color:#8a5a00}
  .twhen.t-now{color:var(--accent)}
  .ttitle{font-size:14px;font-weight:600;color:var(--ink);line-height:1.34}
  .tmeta{font-size:11.5px;color:var(--ink-soft);margin-top:6px}
  .tnote{font-size:11.5px;color:var(--ink-faint);margin-top:4px;line-height:1.35}
  /* eksport do kalendarza + kalkulator terminu */
  .tcal{display:flex;align-items:center;gap:8px;margin-top:9px;padding-top:8px;border-top:1px dashed var(--line);flex-wrap:wrap}
  .tcal-lab{font-size:10.5px;color:var(--ink-faint);font-weight:600;text-transform:uppercase;letter-spacing:.03em}
  .tcal-l{font-size:11.5px;font-weight:600;color:var(--accent);text-decoration:none;border:1px solid var(--line);
    border-radius:7px;padding:2px 9px}
  .tcal-l:hover{background:rgba(138,46,42,.06)}
  .tdl-all{display:inline-block;margin-bottom:14px;font-size:12.5px;font-weight:600;color:var(--accent);
    text-decoration:none;border:1px solid var(--accent);border-radius:9px;padding:6px 13px}
  .tdl-all:hover{background:rgba(138,46,42,.06)}
  .trc{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .trc-out{margin-top:12px}
  .trc-res{font-size:14px;color:var(--ink-soft);background:var(--surface);border:1px solid var(--line);
    border-radius:9px;padding:9px 13px}
  .trc-res b{font-family:var(--serif);color:var(--ink);font-size:15px}
  .trc-when{color:var(--ink-faint);font-weight:600}
  .trc-now{border-color:rgba(176,124,42,.4);background:rgba(176,124,42,.1)}
  .trc-now .trc-when{color:#8a5a2e}
  .trc-past{opacity:.7}
  .trc-desc{font-size:12px;color:var(--ink-faint);line-height:1.4;margin-top:7px}
  /* nowe od ostatniej wizyty */
  .newbar{background:rgba(138,46,42,.07);border:1px solid rgba(138,46,42,.18);color:var(--accent);
    border-radius:9px;padding:9px 13px;font-size:13px;font-weight:600;margin-bottom:14px}
  .newbar .nbwhen{color:var(--ink-faint);font-weight:500}
  .card.is-new{box-shadow:inset 3px 0 0 var(--accent), 0 1px 2px rgba(22,35,59,.04)}
  .new-pill{display:inline-block;margin-left:6px;background:var(--accent);color:#f3e9df;font-size:9.5px;
    font-weight:700;letter-spacing:.04em;padding:1px 6px;border-radius:999px;vertical-align:middle}
  /* wskaźniki: kursy NBP + ściągawka */
  .nbp-head{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;
    font-size:13px;font-weight:600;color:var(--ink-soft);margin-bottom:12px}
  .rategrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:9px}
  .rate{border:1px solid var(--line);background:var(--surface);border-radius:10px;padding:9px 11px;
    display:flex;flex-direction:column;gap:1px}
  .rate.rmain{border-color:rgba(29,58,107,.35);background:rgba(29,58,107,.04)}
  .rate .rc{font-weight:700;font-size:13px;color:var(--ink)}
  .rate .rm{font-family:var(--serif);font-size:18px;font-weight:600;color:var(--accent)}
  .rate .rn{font-size:10.5px;color:var(--ink-faint)}
  .sci-head{font-family:var(--serif);font-size:16px;font-weight:600;color:var(--ink);margin:24px 2px 12px}
  .scigrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}
  .sci{border:1px solid var(--line);border-left:3px solid var(--accent);background:var(--surface);
    border-radius:10px;padding:13px 15px;display:flex;flex-direction:column;gap:3px;
    box-shadow:0 1px 3px rgba(40,30,20,.06)}
  .sci .sl{font-size:11.5px;color:var(--ink-soft);font-weight:600;text-transform:uppercase;letter-spacing:.03em}
  .sci .sv{font-family:var(--serif);font-size:18px;font-weight:600;color:var(--ink)}
  .sci .sn{font-size:11.5px;color:var(--ink-faint);line-height:1.35}
  .zus-schemes{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
  .zus-s{border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:var(--surface);
    box-shadow:0 1px 3px rgba(40,30,20,.06);display:flex;flex-direction:column;gap:5px}
  .zus-s b{font-family:var(--serif);font-size:14.5px;color:var(--ink)}
  .zus-s span{font-size:12px;color:var(--ink-soft);line-height:1.45}
  .zus-s span b{font-family:var(--sans);font-size:12px;color:var(--accent)}
  .sciadd{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .sciadd .search{flex:1 1 180px;min-width:140px;margin:0}
  .sci-mine{position:relative;border-left-color:#2a6a7a}
  .sci-del{position:absolute;top:8px;right:9px;width:20px;height:20px;border-radius:50%;border:1px solid var(--line);
    background:var(--surface);color:var(--ink-faint);font-size:14px;line-height:1;cursor:pointer;padding:0}
  .sci-del:hover{border-color:var(--accent);color:var(--accent)}
  /* przelicznik walut */
  .conv-head{font-family:var(--serif);font-size:16px;font-weight:600;color:var(--ink);margin:24px 2px 12px}
  .conv{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
  .conv-amt{width:130px;padding:9px 11px;border:1px solid var(--line);border-radius:9px;font-size:15px;
    font-family:var(--serif);background:var(--surface);color:var(--ink)}
  .conv-cur{padding:9px 11px;border:1px solid var(--line);border-radius:9px;font-size:13px;
    background:var(--surface);color:var(--ink);max-width:260px}
  .conv-eq{font-size:18px;color:var(--ink-faint)}
  .conv-out{font-family:var(--serif);font-size:20px;font-weight:600;color:var(--accent);min-width:120px}
  .conv-swap{border:1px solid var(--line);background:var(--surface);color:var(--ink-soft);border-radius:9px;
    width:38px;height:38px;font-size:16px;cursor:pointer}
  .conv-swap:hover{background:rgba(0,0,0,.04)}
  /* VAT - weryfikator i panstwa (czyste, oddzielone karty) */
  .vat-tool{background:var(--surface);border:1px solid var(--line);border-radius:12px;
    padding:16px 16px 14px;margin:0 0 16px;box-shadow:0 1px 3px rgba(40,30,20,.05)}
  .vat-th{font-family:var(--serif);font-size:16px;font-weight:600;color:var(--accent);margin-bottom:3px}
  .vat-sub{font-size:12px;color:var(--ink-faint);line-height:1.5;margin:0 0 11px}
  .vat-input{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid var(--line);
    border-radius:9px;font-size:14px;font-family:var(--sans);background:#fff;color:var(--ink)}
  select.vat-input{cursor:pointer;-webkit-appearance:none;appearance:none;
    background-image:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M3 4.5L6 8l3-3.5' stroke='%23999' stroke-width='1.4' fill='none'/%3E%3C/svg%3E");
    background-repeat:no-repeat;background-position:right 12px center;padding-right:34px}
  .vat-input:focus{outline:none;border-color:var(--accent)}
  .vat-chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 2px}
  .vat-chip{font-family:var(--sans);font-size:11.5px;color:var(--ink-soft);background:var(--paper,#f5f0e8);
    border:1px solid var(--line);border-radius:20px;padding:4px 11px;cursor:pointer;transition:all .12s}
  .vat-chip:hover{border-color:var(--accent);color:var(--accent)}
  .vat-res{display:flex;align-items:flex-start;gap:13px;margin-top:13px;padding:13px 14px;
    background:var(--paper,#faf7f1);border:1px solid var(--line);border-radius:10px}
  .vat-badge{flex:none;min-width:54px;text-align:center;color:#fff;font-family:var(--serif);
    font-weight:700;font-size:19px;border-radius:9px;padding:9px 10px;line-height:1}
  .vat-resmain{flex:1;min-width:0}
  .vat-rescat{font-weight:700;font-size:13.5px;color:var(--ink);margin-bottom:3px;text-transform:capitalize}
  .vat-resnote{font-size:12.5px;color:var(--ink-soft);line-height:1.5}
  .vat-caveat{font-size:11.5px;color:var(--ink-faint);line-height:1.5;margin:12px 0 0;
    padding-top:11px;border-top:1px dashed var(--line)}
  .vat-country{margin-top:13px}
  .vat-cstd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;padding-bottom:12px;
    border-bottom:1px solid var(--line)}
  .vat-cstd-lbl{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--ink-faint);font-weight:700}
  .vat-cstd-val{font-family:var(--serif);font-weight:700;font-size:30px;line-height:1}
  .vat-cstd-tax{font-size:11px;color:var(--ink-faint);background:var(--paper,#f5f0e8);
    border:1px solid var(--line);border-radius:6px;padding:2px 7px}
  .vat-cred{padding-top:12px}
  .vat-cred-lbl{display:block;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;
    color:var(--ink-faint);font-weight:700;margin-bottom:7px}
  .vat-cred-row{display:flex;flex-wrap:wrap;gap:7px}
  .vat-rchip{font-family:var(--serif);font-weight:600;font-size:14px;color:#2a7a4a;
    background:rgba(42,122,74,.1);border:1px solid rgba(42,122,74,.25);border-radius:7px;padding:4px 11px}
  .vat-rnone{font-size:12.5px;color:var(--ink-faint);font-style:italic}
  .vat-cnote{font-size:12px;color:var(--ink-soft);line-height:1.5;margin-top:12px;
    padding:9px 11px;background:rgba(176,124,42,.08);border-left:3px solid var(--accent);border-radius:0 7px 7px 0}
  /* schemat transgraniczny VAT (sprzedaz z PL za granice) */
  .vat-xb-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:9px}
  .vat-xb-lbl{flex:none;min-width:90px;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--ink-faint);font-weight:700}
  .vat-xb-row .srcToggle{flex:1;margin-bottom:0;min-width:200px}
  .vat-xb-row .srcbtn{min-width:0;flex:1 1 auto;padding:7px 10px;font-size:12.5px}
  .vat-xb-res{margin-top:14px;padding:14px 15px;background:var(--paper,#faf7f1);border:1px solid var(--line);border-radius:10px}
  .vat-xb-head{display:flex;align-items:center;gap:11px;flex-wrap:wrap;margin-bottom:9px}
  .vat-xb-head .vat-badge{min-width:0;font-size:12px;font-family:var(--sans);font-weight:700;padding:5px 11px;border-radius:7px}
  .vat-xb-title{font-family:var(--serif);font-weight:600;font-size:15px;color:var(--ink)}
  .vat-xb-rate{font-size:14px;color:var(--ink-soft);margin-bottom:8px}
  .vat-xb-rate b{color:var(--accent)}
  .vat-xb-desc{font-size:12.5px;color:var(--ink-soft);line-height:1.55;margin-bottom:9px}
  .vat-xb-warunki{margin:0 0 9px;padding-left:18px;font-size:12px;color:var(--ink-soft);line-height:1.5}
  .vat-xb-warunki li{margin-bottom:4px}
  .vat-xb-podst{font-size:11px;color:var(--ink-faint);font-style:italic;padding-top:9px;border-top:1px dashed var(--line)}
  .vat-xb-kto{font-size:12.5px;color:var(--ink);line-height:1.55;margin-bottom:10px;
    padding:9px 11px;background:rgba(138,46,42,.06);border-left:3px solid var(--accent);border-radius:0 8px 8px 0}
  .vat-xb-thr{margin:0 0 11px;padding:11px 12px;background:var(--surface);border:1px solid var(--line);border-radius:9px}
  .vat-thr-lbl{display:block;font-size:11px;font-weight:600;color:var(--ink-soft);margin-bottom:6px}
  .vat-xb-thr .vat-input{margin-bottom:9px}
  .vat-thr-res{font-size:12.5px;line-height:1.55;padding:9px 11px;border-radius:8px}
  .vat-thr-over{background:rgba(176,124,42,.12);border:1px solid rgba(176,124,42,.35);color:#7a5320}
  .vat-thr-under{background:rgba(42,122,74,.1);border:1px solid rgba(42,122,74,.3);color:#1f5e39}
  .vat-thr-note{font-size:11.5px;color:var(--ink-soft);line-height:1.5;margin-bottom:9px;font-style:italic}
  .vat-xb-ex{font-size:12px;color:var(--ink-soft);line-height:1.55;margin-bottom:10px;
    padding:8px 11px;background:var(--surface);border:1px dashed var(--line);border-radius:8px}
  .vat-xb-ex b{color:var(--ink)}
  .vat-xb-links{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 11px}
  .vat-xb-link{font-size:12px;font-weight:600;color:var(--accent);text-decoration:none;
    padding:5px 10px;border:1px solid var(--line);border-radius:7px;background:var(--surface)}
  .vat-xb-link:hover{border-color:var(--accent)}
  /* kalkulator */
  .kalk-inputs{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px}
  .kin{display:flex;flex-direction:column;gap:4px;font-size:11.5px;color:var(--ink-soft);font-weight:600}
  .kin input,.kin select{padding:9px 11px;border:1px solid var(--line);border-radius:9px;font-size:14px;
    font-family:var(--serif);background:var(--surface);color:var(--ink);min-width:150px}
  .kc-dochod{font-size:13px;color:var(--ink-soft);margin-bottom:12px}
  .kc-dochod b{font-family:var(--serif);font-size:15px;color:var(--ink)}
  .kin-chk{flex-direction:row;align-items:center;gap:7px;align-self:flex-end;padding-bottom:9px}
  .kin-chk input{min-width:0;width:16px;height:16px;accent-color:var(--accent)}
  .kin-chk span{font-weight:600}
  .kc-zus{font-size:13px;color:var(--ink-soft);background:var(--surface);border:1px solid var(--line);
    border-left:3px solid var(--accent);border-radius:9px;padding:9px 13px;margin-bottom:10px}
  .kc-zus b{font-family:var(--serif);color:var(--ink)}
  .kc-zn{display:block;font-size:11.5px;color:var(--ink-faint);font-weight:400;margin-top:3px}
  .kc-note{font-size:12px;color:#8a5a2e;background:rgba(176,124,42,.1);border:1px solid rgba(176,124,42,.35);
    border-radius:8px;padding:7px 11px;margin-bottom:10px}
  .kgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
  .kc{position:relative;border:1px solid var(--line);border-radius:12px;padding:14px 15px;background:var(--surface)}
  .kc-best{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
  .kc-badge{position:absolute;top:-9px;left:14px;background:var(--accent);color:#f3e9df;font-size:9.5px;
    font-weight:700;letter-spacing:.04em;padding:2px 9px;border-radius:999px;text-transform:uppercase}
  .kc-h{font-family:var(--serif);font-size:16px;font-weight:600;color:var(--ink);margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid var(--line)}
  .kr{display:flex;justify-content:space-between;align-items:baseline;gap:10px;font-size:12.5px;color:var(--ink-soft);padding:3px 0}
  .kr b{font-family:var(--serif);font-weight:600;color:var(--ink);white-space:nowrap}
  .kr-sum{margin-top:6px;padding-top:7px;border-top:1px solid var(--line);font-weight:600;color:var(--ink)}
  .kr-net b{color:var(--accent);font-size:15px}
  .kc-off{opacity:.7}
  .kc-na{font-size:12px;color:var(--ink-faint);line-height:1.4}
  /* rozwijana karta etapów rządowych (RCL) */
  .rclproc{margin-top:8px;border:1px solid var(--line);border-radius:9px;overflow:hidden}
  .rclproc summary{list-style:none;cursor:pointer;display:flex;justify-content:space-between;align-items:center;
    gap:10px;padding:8px 11px;background:rgba(138,46,42,.05);font-size:12.5px}
  .rclproc summary::-webkit-details-marker{display:none}
  .rp-now{font-weight:600;color:var(--ink)}
  .rp-tog{font-size:10px;letter-spacing:.06em;text-transform:uppercase;font-weight:700;color:var(--ink-faint);white-space:nowrap}
  .rp-tog::after{content:" ▾"}
  .rclproc[open] .rp-tog::after{content:" ▴"}
  .rp-list{list-style:none;margin:0;padding:9px 12px 11px}
  .rp-list li{position:relative;padding:4px 0 4px 20px;font-size:12px;color:var(--ink-faint);line-height:1.35}
  .rp-list li::before{content:"";position:absolute;left:3px;top:8px;width:8px;height:8px;border-radius:50%;
    border:1.5px solid var(--line);background:var(--paper);box-sizing:border-box}
  .rp-list li.rp-done{color:var(--ink-soft)}
  .rp-list li.rp-done::before{background:var(--accent);border-color:var(--accent)}
  .rp-list li.rp-cur{color:var(--ink);font-weight:600}
  .rp-list li.rp-cur::before{background:var(--accent);border-color:var(--accent);box-shadow:0 0 0 3px rgba(138,46,42,.18)}
  .rp-list li i{font-style:normal;color:var(--ink-faint);font-size:10.5px;margin-left:7px}
  .rp-link{display:inline-block;margin-top:8px;font-size:12px;color:var(--accent);text-decoration:none}
  .rp-link:hover{text-decoration:underline}
  /* plusik „dodaj do Moje" + plakietka */
  .addbtn{flex:none;width:24px;height:24px;border-radius:50%;border:1px solid var(--line);background:var(--surface);
    color:var(--ink-faint);font-size:15px;line-height:1;cursor:pointer;display:inline-flex;align-items:center;
    justify-content:center;padding:0;transition:.15s}
  .addbtn:hover{border-color:var(--accent);color:var(--accent)}
  .addbtn.added{background:var(--accent);border-color:var(--accent);color:#f3e9df}
  .lright{display:flex;align-items:center;gap:9px}
  .chead{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:2px}
  .lacts{display:flex;align-items:center;gap:7px;flex:none}
  .notifybtn{flex:none;height:24px;padding:0 9px;border-radius:13px;border:1px solid var(--line);
    background:var(--surface);color:var(--ink-faint);font-size:11.5px;font-weight:600;letter-spacing:.01em;
    cursor:pointer;display:inline-flex;align-items:center;gap:3px;white-space:nowrap;transition:.15s;font-family:inherit}
  .notifybtn:hover{border-color:var(--accent);color:var(--accent)}
  .notifybtn.on{background:var(--accent);border-color:var(--accent);color:#f3e9df}
  .notifybox{border:1px solid var(--accent);background:#fbf3ec;border-radius:12px;padding:14px 16px;margin-bottom:18px}
  .nb-head{font-weight:700;color:var(--accent);font-size:14.5px;margin-bottom:5px}
  .nb-info{font-size:12.5px;color:var(--ink-soft);line-height:1.5;margin:0 0 9px}
  .nb-text{width:100%;box-sizing:border-box;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
    color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:8px 10px;
    resize:vertical;line-height:1.5}
  .nb-copy{margin-top:9px;height:30px;padding:0 14px;border-radius:8px;border:1px solid var(--accent);
    background:var(--accent);color:#f3e9df;font-size:12.5px;font-weight:600;cursor:pointer;font-family:inherit;transition:.15s}
  .nb-copy:hover{filter:brightness(1.08)}
  .wyrok-snip{margin:7px 0 0;font-size:12.5px;line-height:1.55;color:var(--ink-soft)}
  .wyrok-snip mark{background:rgba(176,124,42,.28);color:var(--ink);border-radius:2px;padding:0 1px}
  .wyr-filters{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-top:10px}
  .wf{display:flex;flex-direction:column;gap:3px;font-size:11px;color:var(--ink-faint);font-weight:600}
  .wf input,.wf select{padding:7px 9px;border:1px solid var(--line);border-radius:8px;font-size:13px;
    font-family:var(--sans);background:var(--surface);color:var(--ink)}
  .wf-chk{flex-direction:row;align-items:center;gap:6px;padding-bottom:8px}
  .wf-chk input{padding:0;width:15px;height:15px;accent-color:var(--accent)}
  .wyr-ct{display:inline-block;color:#f3e9df;font-size:10px;font-weight:700;border-radius:5px;
    padding:1px 6px;letter-spacing:.02em;vertical-align:middle}
  .wyr-pager{display:flex;align-items:center;justify-content:center;gap:14px;margin:18px 0 4px;
    font-size:12.5px;color:var(--ink-soft)}
  .wyr-pager .livebtn[disabled]{opacity:.4;cursor:default}
  .kis-launch{border:1px solid var(--line);background:var(--surface);border-radius:12px;padding:14px 16px;font-size:13.5px;line-height:1.55;color:var(--ink)}
  .kis-launch p{margin:0 0 8px}
  .kis-launch p:last-child{margin:0}
  .kis-alt{font-size:12.5px;color:var(--ink-soft)}
  .kis-alt a{color:var(--accent);text-decoration:none;font-weight:600}
  .kis-alt a:hover{text-decoration:underline}
  .srcToggle{display:flex;gap:7px;margin-bottom:10px;flex-wrap:wrap}
  .srcbtn{flex:1 1 auto;min-width:160px;padding:8px 12px;border-radius:9px;border:1px solid var(--line);
    background:var(--surface);color:var(--ink-soft);font-size:12px;font-weight:600;cursor:pointer;
    font-family:inherit;transition:.15s;text-align:center}
  .srcbtn:hover{border-color:var(--accent);color:var(--accent)}
  .srcbtn.on{background:var(--accent);border-color:var(--accent);color:#f3e9df}
  .subtabs{display:flex;gap:8px;margin:0 0 16px;flex-wrap:wrap}
  .subtab{padding:7px 16px;border-radius:999px;border:1px solid var(--line);background:var(--surface);
    color:var(--ink-soft);font-size:12.5px;font-weight:600;cursor:pointer;font-family:inherit;transition:.15s}
  .subtab:hover{border-color:var(--accent);color:var(--accent)}
  .subtab.on{background:var(--accent);border-color:var(--accent);color:#f3e9df}
  .tabbadge{display:none;min-width:17px;height:17px;padding:0 4px;border-radius:9px;background:var(--accent);
    color:#f3e9df;font-size:10px;font-weight:700;line-height:17px;text-align:center;margin-left:6px;vertical-align:middle}
  .tabbadge.show{display:inline-block}

  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
  @media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
  @media (max-width:560px){.wordmark{font-size:23px}.seal{width:42px;height:42px;font-size:25px}.stats{gap:16px}.card .title{font-size:17px}.lgrid{grid-template-columns:1fr}}
</style>
</head>
<body>
  <header class="masthead">
    <div class="masthead-in">
      <div class="brandrow">
        <div class="seal">§</div>
        <div class="brandtext">
          <h1 class="wordmark">Paragraf</h1>
          <div class="subtitle">kokpit podatkowo-prawny</div>
        </div>
        <div class="actions">
          <button class="iconbtn" onclick="location.reload()" title="Wczytaj najnowszą wersję">Odśwież</button>
        </div>
      </div>
      <div class="stats">
        <div class="stat"><span class="num" id="stCount">{TOTAL_ITEMS}</span><span class="lab">Pozycje</span></div>
        <div class="stat"><span class="num">{LIVE}/{TOTAL}</span><span class="lab">Źródła na żywo</span></div>
        <div class="stat"><span class="num"><span class="livedot"></span><span id="stTime">—</span></span><span class="lab" id="stDate">aktualizacja</span></div>
      </div>
    </div>
  </header>

  <div class="wrap">
    <nav class="tabs">
      <button class="tab on" data-section="news">Wiadomości<span class="tabbadge" id="newsBadge"></span></button>
      <button class="tab" data-section="terminy">Terminy</button>
      <button class="tab" data-section="narzedzia">Narzędzia</button>
      <button class="tab" data-section="prawo">Prawo</button>
      <button class="tab" data-section="moje">Moje<span class="tabbadge" id="mojeBadge"></span></button>
    </nav>
    <div class="memberbar" id="memberBar" hidden></div>

    <section id="newsView">
      <div class="controls">
        <input class="search" id="search" type="text" placeholder="Szukaj: VAT, KSeF, estoński CIT, ZUS, orzeczenie…" autocomplete="off">
        <div class="chips" id="chips"></div>
        <div class="domrow"><span class="domlab">Dziedzina</span><div class="dchips" id="domChips"></div></div>
      </div>

      {SUMMARY}

      <main id="feed"></main>
    </section>

    <div id="legisSubBar" class="subtabs" data-subbar="legis" hidden></div>

    <section id="legisSoonView" hidden>
      <section id="legisSoon"></section>
      <div id="legisSoonEmpty" class="live-status" hidden>Na razie nic nie czeka na wejście w życie. Pojawią się tu akty już opublikowane w Dz.U./M.P., które zaczną obowiązywać dopiero w przyszłości.</div>
    </section>

    <section id="legisView" hidden>
      <div class="controls">
        <div class="searchrow">
          <input class="search" id="searchL" type="text" placeholder="Szukaj ustawy w Dz.U. / nr druku Sejmu…  (Enter = na żywo)" autocomplete="off">
          <button class="livebtn" id="liveBtn" title="Pobierz na żywo z Dziennika Ustaw">Szukaj na żywo</button>
        </div>
        <p class="livehint">Pisanie filtruje na bieżąco to, co już pobrane. <b>„Szukaj na żywo"</b> (albo Enter) odpytuje Dziennik Ustaw i Monitor Polski w czasie rzeczywistym.</p>
      </div>
      <div id="liveResults"></div>
      <section id="legis"></section>
    </section>

    <section id="rclView" hidden>
      <div class="controls">
        <div class="searchrow">
          <input class="search" id="searchR" type="text" placeholder="Projekt rządowy: ryczałt, VAT, akcyza, UD116…  (Enter)" autocomplete="off">
          <button class="livebtn" id="rclBtn" title="Pobierz projekty na żywo z RCL">Szukaj</button>
        </div>
        <p class="livehint">Rządowy proces legislacyjny — projekty, <b>zanim trafią do Sejmu</b>. Szukam na żywo w RCL; przy szukaniu po słowie pokazuję tylko projekty <b>w toku</b> (zakończone i przekazane do Sejmu pomijam).</p>
      </div>
      <div id="rclResults"></div>
      <section id="rclList"></section>
    </section>

    <section id="wyrokiView" hidden>
      <div class="controls">
        <div class="searchrow">
          <input class="search" id="searchW" type="text" placeholder="Szukaj w treści orzeczeń: substancja ekonomiczna, ulga B+R, koszty…  (Enter)" autocomplete="off">
          <button class="livebtn" id="wyrokiBtn" title="Szukaj orzeczen">Szukaj</button>
        </div>
        <div class="wyr-filters" id="wyrFilters">
          <label class="wf"><span>Od</span><input id="wyrFrom" type="date"></label>
          <label class="wf"><span>Do</span><input id="wyrTo" type="date"></label>
          <label class="wf"><span>Typ</span><select id="wyrType"><option value="">wszystkie</option><option value="SENTENCE">wyrok</option><option value="DECISION">postanowienie</option><option value="RESOLUTION">uchwała</option></select></label>
          <label class="wf"><span>Sortuj</span><select id="wyrSort"><option value="DESC">od najnowszych</option><option value="ASC">od najstarszych</option></select></label>
          <label class="wf wf-chk"><input id="wyrPhrase" type="checkbox" checked><span>Dokładna fraza</span></label>
        </div>
        <p class="livehint">Wyniki z bazy <b>SAOS</b> (API): sądy powszechne, SN, TK, KIO <b>oraz administracyjne NSA/WSA</b> — orzeczenia podatkowe też się pojawiają. <b>Dokładna fraza</b> (domyślnie) szuka słów dokładnie obok siebie — „substancja ekonomiczna" nie pokaże już „substancji psychoaktywnej". Odznacz, by szukać każdego słowa osobno. Działa też składnia SAOS: cudzysłów = fraza, <i>OR</i> = lub, minus = bez słowa (np. <i>dobro -osobiste</i>).</p>
      </div>
      <div id="wyrokiResults"></div>
    </section>

    <section id="kisView" hidden>
      <div class="controls">
        <div class="searchrow">
          <input class="search" id="searchK" type="text" placeholder="Fraza do interpretacji: ulga B+R, najem prywatny, PIT-2…" autocomplete="off">
          <button class="livebtn" id="kisBtn" title="Skopiuj fraze i otworz oficjalna wyszukiwarke">Szukaj w EUREKA</button>
        </div>
        <p class="livehint">Interpretacje KIS nie mają publicznego API, więc nie pokażę ich tutaj w aplikacji. Po kliknięciu <b>kopiuję frazę do schowka</b> i otwieram oficjalną wyszukiwarkę MF (EUREKA) — wystarczy wkleić (Ctrl+V) i nacisnąć szukaj.</p>
      </div>
      <div id="kisResults"></div>
    </section>

    <section id="mojeView" hidden>
      <p class="livehint" style="margin:2px 2px 18px">Twoja kolekcja. Dodawaj plusikiem <b>+</b> z dowolnej zakładki (Wiadomości, Ustawy, RCL). Zapisuje się w tej przeglądarce.</p>
      <section id="mojeList"></section>
    </section>

    <section id="terminyView" hidden>
      <div class="subtabs" data-subbar="terminy"></div>
      <div id="terminyMain">
        <div class="controls">
          <div class="chips" id="termChips"></div>
          <p class="livehint" style="margin:10px 2px 0">Najbliższe terminy podatkowe i sprawozdawcze. Daty już <b>przesunięte</b>, gdy wypadają w sobotę/niedzielę/święto (art. 12 §5 Ordynacji). Liczone na dziś w Twojej przeglądarce. To <b>ogólny terminarz</b> dla typowych przypadków — które terminy faktycznie Cię dotyczą, zależy od formy klienta (VAT mies./kwart., skala/ryczałt, spółka/JDG). Filtruj chipami wyżej.</p>
        </div>
        <div id="terminyList"></div>
      </div>
      <div id="termCalcWrap"><div id="termCalc"></div></div>
    </section>

    <section id="kursyView" hidden>
      <div class="subtabs" data-subbar="kursy"></div>
      <div id="kursyRates"><div class="controls"><div id="nbpBox"></div></div></div>
      <div id="kursyConv"><div class="controls"><div id="kursyCalc"></div></div></div>
    </section>

    <section id="stawkiView" hidden>
      <div class="subtabs" data-subbar="stawki"></div>
      <div id="stawkiSciaga"><div class="controls"><div id="sciagawkaBox"></div><div id="sciagaCustom"></div></div></div>
      <div id="stawkiZus"><div class="controls"><div id="zusBox"></div></div></div>
    </section>

    <section id="vatView" hidden>
      <div class="controls"><div id="vatBox"></div></div>
      <!-- vatBox wypelniany przez renderVAT: weryfikator PL + stawki wg panstwa + schemat transgraniczny -->
    </section>

    <section id="kalkView" hidden>
      <div class="subtabs" data-subbar="kalk"></div>
      <div id="kalkJdg">
      <div class="controls">
        <div class="kalk-inputs">
          <label class="kin"><span>Przychód netto / rok</span><input id="kalkP" type="number" inputmode="decimal" value="300000" min="0" step="any"></label>
          <label class="kin"><span>Koszty / rok</span><input id="kalkK" type="number" inputmode="decimal" value="100000" min="0" step="any"></label>
          <label class="kin"><span>Schemat ZUS</span><select id="kalkZus">
            <option value="pelny" selected>Pełny ZUS (duży)</option>
            <option value="pref">Preferencyjny (mały, 24 mies.)</option>
            <option value="plus">Mały ZUS Plus</option>
            <option value="ulga">Ulga na start (6 mies.)</option>
          </select></label>
          <label class="kin kin-chk"><input id="kalkChor" type="checkbox"><span>Ubezpieczenie chorobowe (dobrowolne)</span></label>
          <label class="kin"><span>Stawka ryczałtu</span><select id="kalkR">
            <option value="0.17">17%</option>
            <option value="0.15">15%</option>
            <option value="0.14">14%</option>
            <option value="0.125">12,5%</option>
            <option value="0.12">12%</option>
            <option value="0.10">10%</option>
            <option value="0.085" selected>8,5%</option>
            <option value="0.055">5,5%</option>
            <option value="0.03">3%</option>
            <option value="0.02">2%</option>
          </select></label>
        </div>
        <div id="kalkResults"></div>
        <p class="livehint" style="margin-top:10px">Składki <b>społeczne</b> liczone automatycznie z wybranego schematu ZUS (stawki 2026), składka <b>zdrowotna</b> liczona sama wg formy (skala 9% / liniowy 4,9% / ryczałt wg progu przychodu). Chorobowe dobrowolne — domyślnie wyłączone. Orientacyjnie, dla jednoosobowej działalności — nie jest to porada podatkowa.</p>
      </div>
      </div>
      <div id="kalkSpzoo">
      <div class="controls">
        <div class="kalk-inputs">
          <label class="kin"><span>Przychód spółki / rok</span><input id="spP" type="number" inputmode="decimal" value="500000" min="0" step="any"></label>
          <label class="kin"><span>Koszty / rok</span><input id="spK" type="number" inputmode="decimal" value="100000" min="0" step="any"></label>
          <label class="kin"><span>Wynagrodzenie zarządu — powołanie / rok</span><input id="spPow" type="number" inputmode="decimal" value="0" min="0" step="any"></label>
          <label class="kin kin-chk"><input id="spMaly" type="checkbox" checked><span>Mały podatnik (CIT 9% / estoński 10%)</span></label>
          <label class="kin kin-chk"><input id="spDyw" type="checkbox" checked><span>Wypłata dywidendy do właściciela</span></label>
        </div>
        <div id="spzooResults"></div>
        <p class="livehint" style="margin-top:10px"><b>Wypłata dywidendy</b> włączona = pełna wypłata zysku do właściciela (podwójne opodatkowanie). Wyłączona = zysk zostaje w spółce: w klasycznym CIT podatek od zysku i tak jest należny co roku, a w <b>estońskim CIT podatku nie ma w ogóle do czasu wypłaty</b> — to jego główna przewaga. „Klasyczna" = CIT 9%/19% + 19% PIT od dywidendy. „Estoński CIT" przy wypłacie: CIT 10%/20% + obniżony PIT (łącznie ok. 20%/25%). Powołanie zmniejsza zysk i jest opodatkowane PIT 12% + 9% zdrowotnej (bez ZUS społecznego). Orientacyjnie — nie jest to porada podatkowa.</p>
      </div>
      </div>

      <div id="kalkEtat">
      <div class="controls">
        <div class="kalk-inputs">
          <label class="kin"><span>Wynagrodzenie brutto / miesiąc</span><input id="etP" type="number" inputmode="decimal" value="5000" min="0" step="any"></label>
          <label class="kin"><span>Koszty uzyskania (KUP) / rok</span><input id="etKup" type="number" inputmode="decimal" value="3000" min="0" step="any"></label>
        </div>
        <div id="etatResults"></div>
        <p class="livehint" style="margin-top:10px">Umowa o pracę, opodatkowanie skalą (12%/32%, kwota zmniejszająca 3 600 zł). ZUS pracownika: emerytalna 9,76% + rentowa 1,5% (do limitu 30-krotności 282 600 zł) + chorobowa 2,45%. KUP standardowo 3 000 zł/rok (250 zł/mc); przy pracy poza miejscem zamieszkania wpisz 3 600 zł. <b>Nie obejmuje składek po stronie pracodawcy.</b> Orientacyjnie — nie jest to porada podatkowa.</p>
      </div>
      </div>

      <div id="kalkJdgUop">
      <div class="controls">
        <div class="kalk-inputs">
          <label class="kin"><span>Przychód z DG / rok (netto)</span><input id="juP" type="number" inputmode="decimal" value="50000" min="0" step="any"></label>
          <label class="kin"><span>Koszty DG / rok</span><input id="juK" type="number" inputmode="decimal" value="10000" min="0" step="any"></label>
          <label class="kin"><span>ZUS z DG / rok</span><input id="juZus" type="number" inputmode="decimal" value="21459.48" min="0" step="any"></label>
          <label class="kin"><span>Wynagrodzenie z UoP brutto / miesiąc</span><input id="juMg" type="number" inputmode="decimal" value="4000" min="0" step="any"></label>
          <label class="kin"><span>KUP z UoP / rok</span><input id="juKup" type="number" inputmode="decimal" value="3000" min="0" step="any"></label>
        </div>
        <div id="jdgUopResults"></div>
        <p class="livehint" style="margin-top:10px">Działalność na <b>skali</b> łączona z etatem — dochody sumują się do jednej podstawy skali (próg 120 tys. liczony łącznie). Zdrowotna z DG 9% (min. 5 190,48 zł), zdrowotna i ZUS z UoP liczone osobno i doliczane. ZUS z DG wpisz wg swojego schematu (pełny 2026 = 21 459,48 zł). Orientacyjnie — nie jest to porada podatkowa.</p>
      </div>
      </div>

      <div id="kalkOsobowa">
      <div class="controls">
        <div class="kalk-inputs">
          <label class="kin"><span>Przychód spółki / rok (netto)</span><input id="osP" type="number" inputmode="decimal" value="600000" min="0" step="any"></label>
          <label class="kin"><span>Koszty spółki / rok</span><input id="osK" type="number" inputmode="decimal" value="200000" min="0" step="any"></label>
          <label class="kin"><span>Twój udział w zysku (%)</span><input id="osShare" type="number" inputmode="decimal" value="50" min="0" max="100" step="any"></label>
          <label class="kin"><span>Schemat ZUS</span><select id="osZus">
            <option value="pelny" selected>Pełny ZUS (duży)</option>
            <option value="pref">Preferencyjny (mały, 24 mies.)</option>
            <option value="plus">Mały ZUS Plus</option>
            <option value="ulga">Ulga na start (6 mies.)</option>
          </select></label>
          <label class="kin kin-chk"><input id="osChor" type="checkbox"><span>Ubezpieczenie chorobowe</span></label>
          <label class="kin"><span>Stawka ryczałtu</span><select id="osR">
            <option value="0.17">17%</option><option value="0.15">15%</option><option value="0.14">14%</option>
            <option value="0.125">12,5%</option><option value="0.12">12%</option><option value="0.10">10%</option>
            <option value="0.085" selected>8,5%</option><option value="0.055">5,5%</option><option value="0.03">3%</option><option value="0.02">2%</option>
          </select></label>
        </div>
        <div id="osResults"></div>
        <p class="livehint" style="margin-top:10px">Spółka „wieloosobowa" niebędąca podatnikiem CIT (jawna osób fizycznych, cywilna, partnerska) — <b>spółka nie płaci podatku</b>, każdy wspólnik rozlicza swój udział w zysku jak JDG. Podaj przychód i koszty całej spółki oraz swój udział — policzę Twoją część i porównam liniowy / ryczałt / skalę. Orientacyjnie — nie jest to porada podatkowa.</p>
      </div>
      </div>

      <div id="kalkKomandyt">
      <div class="controls">
        <div class="kalk-inputs">
          <label class="kin"><span>Przychód spółki / rok (netto)</span><input id="komP" type="number" inputmode="decimal" value="300000" min="0" step="any"></label>
          <label class="kin"><span>Koszty spółki / rok</span><input id="komK" type="number" inputmode="decimal" value="100000" min="0" step="any"></label>
          <label class="kin"><span>Liczba wspólników (osób fiz.)</span><input id="komN" type="number" inputmode="numeric" value="1" min="1" step="1"></label>
          <label class="kin kin-chk"><input id="komMaly" type="checkbox" checked><span>Mały podatnik (CIT 9%)</span></label>
        </div>
        <div id="komResults"></div>
        <p class="livehint" style="margin-top:10px">Spółka komandytowa płaci <b>CIT</b> od dochodu (9% mały podatnik / 19%), a wypłata zysku jest opodatkowana PIT 19% u wspólnika. Różnica: <b>komandytariusz</b> („tradycyjna") płaci pełne 19% (podwójne opodatkowanie), a <b>komplementariusz</b> („roszada") odlicza CIT spółki od swojego PIT — stąd niższe obciążenie. ZUS i zdrowotna liczone na każdego wspólnika. Zakłada pełną wypłatę zysku po CIT. Orientacyjnie — nie jest to porada podatkowa.</p>
      </div>
      </div>

      <div id="kalkLacznie">
      <div class="controls">
        <div id="lacMode" class="srcToggle" style="margin-bottom:12px">
          <button class="srcbtn on" data-mode="malzonkowie">Małżonkowie</button>
          <button class="srcbtn" data-mode="rodzic">Samotny rodzic</button>
        </div>
        <div class="kalk-inputs">
          <label class="kin"><span id="lacALbl">Dochód do opodatkowania — małżonek A / rok</span><input id="lacA" type="number" inputmode="decimal" value="178540" min="0" step="any"></label>
          <label class="kin" id="lacBWrap"><span>Dochód do opodatkowania — małżonek B / rok</span><input id="lacB" type="number" inputmode="decimal" value="0" min="0" step="any"></label>
        </div>
        <div id="lacResults"></div>
        <p class="livehint" style="margin-top:10px">Wspólne rozliczenie skalą: sumę dochodów dzieli się na pół, liczy podatek od połowy i mnoży ×2 — korzyść rośnie, gdy jedna osoba zarabia dużo mniej (niewykorzystany próg 12% i kwota wolna „przechodzą"). <b>Samotny rodzic</b> rozlicza tak swój dochód z dzieckiem. Wpisz <b>roczny dochód do opodatkowania</b> (po odliczeniu ZUS). ZUS i składka zdrowotna są takie same niezależnie od sposobu rozliczenia. Orientacyjnie — nie jest to porada podatkowa.</p>
      </div>
      </div>
    </section>

    <footer>
      <b>Paragraf</b> aktualizuje się automatycznie kilka razy dziennie — w jednym miejscu.<br>
      <b>Wiadomości</b> — artykuły z portali (chipem włączasz/wyłączasz źródło). <b>Ustawy</b> — projekty w Sejmie i ustawy ogłoszone w Dz.U. <b>RCL</b> — projekty na etapie rządowym, z rozwijanymi etapami. <b>Moje</b> — pozycje dodane plusikiem. Wybory zapamiętuje przeglądarka.
    </footer>
  </div>

<script>
const DATA = {DATA};
const BUILT = "{BUILT}";
const FEEDS = {FEEDS};
const state = { off:new Set(), q:"", qL:"", qR:"", tab:"news", moje:[], termOff:new Set(), nbpData:null, nbpLoading:false, dom:new Set(),
  sub:{ legis:"proc", terminy:"lista", kursy:"kursy", stawki:"sciaga", kalk:"jdg" }, sciaga:[] };
const $ = s => document.querySelector(s);
try{ const s=localStorage.getItem("paragraf-off"); if(s) state.off=new Set(JSON.parse(s)); }catch(e){}
try{ const s=localStorage.getItem("paragraf-moje"); if(s) state.moje=JSON.parse(s)||[]; }catch(e){}
try{ const s=localStorage.getItem("paragraf-termoff"); if(s) state.termOff=new Set(JSON.parse(s)); }catch(e){}
try{ const s=localStorage.getItem("paragraf-sciaga"); if(s) state.sciaga=JSON.parse(s)||[]; }catch(e){}
// "Nowe od ostatniej wizyty": zapamietujemy znacznik poprzedniej wizyty (do podswietlania),
// a nowy zapisujemy tylko gdy minelo >20 min - dzieki temu szybkie F5 nie kasuje podswietlen.
let LAST_VISIT=0;
try{ LAST_VISIT=+localStorage.getItem("paragraf-lastvisit")||0; }catch(_){}
try{ const _n=Date.now(); if(!LAST_VISIT || _n-LAST_VISIT>1200000) localStorage.setItem("paragraf-lastvisit", String(_n)); }catch(_){}
function isNew(it){ return LAST_VISIT>0 && it._d && it._d.getTime()>LAST_VISIT; }
try{ const s=localStorage.getItem("paragraf-dom"); if(s) state.dom=new Set(JSON.parse(s)); }catch(_){}
// Kategoryzacja po dziedzinie podatkowej (regexy: krotkie kody \b...\b, frazy - fragmentem).
const TAX_DOMS=[
  {key:"VAT",  label:"VAT",            color:"#1d3a6b", re:/\bvat\b|towarów i usług|ksef|faktur|jpk[_ ]?v7|split payment|biała lista|wewnątrzwspólnotow|odwrotne obciąż/i},
  {key:"PIT",  label:"PIT",            color:"#8a2e2a", re:/\bpit\b|pit-\d|osób fizycznych|rycza[łl]t|skal[ai] podatkow|podatek liniowy|kwota wolna|najem prywatn|nierejestrow/i},
  {key:"CIT",  label:"CIT",            color:"#6b4a8a", re:/\bcit\b|cit-\d|osób prawnych|estoński|podatek minimaln|minimalny podatek|podatek od spółek/i},
  {key:"ZUS",  label:"ZUS / składki",  color:"#0f5c4a", re:/\bzus\b|sk[łl]adk|ubezpiecze\w* spo[łl]|zdrowotn|emerytur|rentow|chorobow|wypadkow/i},
  {key:"AKC",  label:"Akcyza",         color:"#8a5a2e", re:/akcyz|banderol|susz tytoniow|alkohol etylow/i},
  {key:"LOK",  label:"Lokalne",        color:"#4a7a3a", re:/od nieruchomości|podatek rolny|podatek leśny|środków transportow|\bpcc\b|czynności cywilnoprawnych|spadków i darowizn|od spadków|darowizn/i},
  {key:"PROC", label:"Procedura",      color:"#7a6a2a", re:/ordynacj|postępowani\w* podatkow|kontrol\w* (podatkow|skarbow)|administracj\w* skarbow|czynny żal|przedawnieni|schemat\w* podatkow|\bmdr\b/i},
  {key:"RACH", label:"Rachunkowość",   color:"#5a5a6a", re:/rachunkow|sprawozdani\w* finansow|księgi rachunk|jpk[_ ]?(cit|pit)|biegł\w* rewiden|e-sprawozdani/i},
  {key:"MDZ",  label:"Międzynar.",     color:"#2a6a7a", re:/cen\w* transferow|u źródła|withholding|\bwht\b|raj podatkow|umowa o unikaniu|\boecd\b|transgraniczn/i}
];
function taxTags(text){ const t=(text||"").toLowerCase(); return TAX_DOMS.filter(d=>d.re.test(t)); }
function tagsHTML(tags, max){ if(!tags||!tags.length) return ""; return `<div class="cardtags">${tags.slice(0,max||2).map(d=>`<span class="dtag" style="background:${d.color}">${esc(d.label)}</span>`).join("")}</div>`; }
function renderDomChips(){
  const box=$("#domChips"); if(!box) return;
  box.innerHTML=TAX_DOMS.map(d=>`<button class="dchip${state.dom.has(d.key)?' on':''}" data-dom="${d.key}" style="--dc:${d.color}">${esc(d.label)}</button>`).join("");
  box.querySelectorAll("[data-dom]").forEach(b=>b.onclick=()=>{
    const k=b.dataset.dom; state.dom.has(k)?state.dom.delete(k):state.dom.add(k);
    try{localStorage.setItem("paragraf-dom",JSON.stringify([...state.dom]))}catch(_){}
    renderDomChips(); render();
  });
}
const presentIds = new Set(DATA.map(d=>d.fid));

function esc(s){return (s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
// --- „Moje": zapisywane w przeglądarce ---
function saveMoje(){ try{localStorage.setItem("paragraf-moje",JSON.stringify(state.moje))}catch(e){} }
function mojeHas(link){ return state.moje.some(x=>x.link===link); }
function toggleMoje(it){
  if(mojeHas(it.link)) state.moje=state.moje.filter(x=>x.link!==it.link);
  else state.moje=[{...it,_added:Date.now()}, ...state.moje];
  saveMoje(); render();
}
function addbtn(it){
  const on=mojeHas(it.link);
  return `<button class="addbtn${on?' added':''}" data-item="${esc(JSON.stringify(it))}" title="${on?'Usuń z „Moje”':'Dodaj do „Moje”'}">${on?'✓':'+'}</button>`;
}
// Po zmianie kolekcji odswiezamy wyglad WSZYSTKICH plusikow na stronie,
// takze tych w wynikach wyszukiwania na zywo (render() ich nie przerysowuje).
function syncAddBtns(){
  document.querySelectorAll(".addbtn[data-item]").forEach(btn=>{
    let link=""; try{ link=JSON.parse(btn.dataset.item).link; }catch(_){}
    const on=mojeHas(link);
    btn.classList.toggle("added", on);
    btn.textContent = on ? "✓" : "+";
    btn.title = on ? "Usuń z „Moje”" : "Dodaj do „Moje”";
  });
}
const PL = new Intl.DateTimeFormat("pl-PL",{day:"numeric",month:"long",year:"numeric"});
const NOW_Y = new Date().getFullYear();
// Twarda walidacja daty: odrzuca bledne/przyszle roczniki (np. "2206").
function pd(s){
  if(!s) return null;
  const d = new Date(s);
  if(isNaN(d.getTime())) return null;
  const y = d.getFullYear();
  if(y < 2015 || y > NOW_Y + 1) return null;
  return d;
}
function dayKey(d){return d?d.getFullYear()+"-"+d.getMonth()+"-"+d.getDate():"x"}
function dayLabel(d){
  if(!d) return "Bez daty";
  const t=new Date();t.setHours(0,0,0,0);const x=new Date(d);x.setHours(0,0,0,0);
  const diff=Math.round((t-x)/86400000);
  if(diff===0)return "Dziś";if(diff===1)return "Wczoraj";return PL.format(d);
}
function ago(d){
  if(!d)return "";const s=(Date.now()-d.getTime())/1000;
  if(s<0)return PL.format(d);
  if(s<60)return "przed chwilą";if(s<3600)return Math.floor(s/60)+" min temu";
  if(s<86400)return Math.floor(s/3600)+" godz. temu";const k=Math.floor(s/86400);
  if(k===1)return "wczoraj";if(k<8)return k+" dni temu";return PL.format(d);
}

function newsVisible(){
  const q=state.q.trim().toLowerCase();
  let arr=DATA
    .filter(it=>!it.track)
    .filter(it=>!state.off.has(it.fid))
    .filter(it=>!q||(it.title+" "+it.desc+" "+it.src+" "+it.cat).toLowerCase().includes(q))
    .map(it=>({...it,_d:pd(it.date),_tags:taxTags(it.title+" "+it.desc)}));
  if(state.dom.size) arr=arr.filter(it=>it._tags.some(d=>state.dom.has(d.key)));
  return arr;
}
function ustawyVisible(){
  const q=state.qL.trim().toLowerCase();
  return DATA
    .filter(it=>it.track && (it.src||"").indexOf("RCL")<0)
    .filter(it=>!q||(it.title+" "+it.desc+" "+it.src+" "+it.cat+" "+(it.stage||"")).toLowerCase().includes(q))
    .map(it=>({...it,_d:pd(it.date)}));
}
function rclVisible(){
  const q=state.qR.trim().toLowerCase();
  return DATA
    .filter(it=>it.track && (it.src||"").indexOf("RCL")>=0)
    .filter(it=>!q||(it.title+" "+it.desc+" "+(it.stage||"")).toLowerCase().includes(q))
    .map(it=>({...it,_d:pd(it.date)}));
}

function renderChips(){
  $("#chips").innerHTML = FEEDS.map(f=>{
    const on=state.off.has(f.id)?"0":"1";
    const empty=presentIds.has(f.id)?"0":"1";
    return `<button class="chip" data-id="${f.id}" data-on="${on}" data-empty="${empty}" title="${empty==="1"?"Brak świeżych wpisów z tego źródła":"Włącz / wyłącz"}"><span class="dot" style="background:${f.color}"></span>${esc(f.name)}</button>`;
  }).join("");
  document.querySelectorAll(".chip").forEach(c=>c.onclick=()=>{
    const id=c.dataset.id; state.off.has(id)?state.off.delete(id):state.off.add(id);
    try{localStorage.setItem("paragraf-off",JSON.stringify([...state.off]))}catch(e){}
    renderChips(); render();
  });
}

const STEPS = ["Rząd","Sejm","Prezydent","Dz.U."];
function stepper(step, done){
  return `<div class="stepper">`+STEPS.map((s,i)=>{
    const on = (i < step) ? "on" : "";
    const cur = (!done && i === step-1) ? "cur" : "";
    return `<div class="step ${on} ${cur}"><i></i><b>${s}</b></div>`;
  }).join("")+`</div>`;
}
function rclStatus(t){
  if(!t) return null;
  const low=t.toLowerCase();
  if(low.includes("na stronach sejmu")||low.includes("dalszy ciąg procesu legislacyjnego")) return "left";
  if(/status projektu:\s*zamkn/.test(low)) return "closed";
  return "in_gov";
}
// Wykrywa, ze projekt sie ZAKONCZYL: stal sie ustawa albo zostal dolaczony do
// innego projektu (ktory czesto stal sie ustawa). Wyciaga odwolanie do Dz.U.
function rclBecameLaw(t){
  if(!t) return null;
  const flat=t.replace(/<[^>]*>/g," ").replace(/\s+/g," ");
  const low=flat.toLowerCase();
  const isLaw=/sta[łl]a?\s*si[ęe]\s*ustaw/.test(low);
  const merged=/do[łl][aą]czono do projektu/.test(low);
  const contM=low.match(/kontynuowan[ya]\s+(?:pod\s+nr\.?\s*|jako\s*)([a-z]{1,3}\s?\d{1,4})/);
  const continued=!!contM;
  if(!isLaw && !merged && !continued) return null;
  let poz=null, year=null;
  const mp=low.match(/dz\.?\s*u\.?[\s\S]{0,60}?poz\.?\s*0*(\d{1,5})/);
  if(mp) poz=mp[1];
  let my=low.match(/ustaw[aąy][\s\S]{0,90}?((?:19|20)\d{2})\s*r/);
  if(!my) my=low.match(/((?:19|20)\d{2})[\s\S]{0,25}?poz/);
  if(my) year=my[1];
  const link=(poz && year) ? `http://dziennikustaw.gov.pl/DU/${year}/${poz}` : null;
  const ref=(year&&poz) ? `Dz.U. ${year} poz. ${poz}` : (poz ? `Dz.U. poz. ${poz}` : "");
  const contNum=contM?contM[1].toUpperCase().replace(/\s+/g,""):null;
  const contLink=contNum?`https://legislacja.rcl.gov.pl/lista?typeId=2&number=${encodeURIComponent(contNum)}`:null;
  return {isLaw, merged, continued, contNum, contLink, poz, year, link, ref};
}
// Najpozniejsza data etapu (DD-MM-YYYY) -> Date; sluzy do wykrycia "martwych" projektow.
function rclLatestDate(stages){
  if(!stages||!stages.length) return null;
  let best=null;
  for(const s of stages){
    if(!s.date) continue;
    const m=String(s.date).match(/(\d{2})-(\d{2})-(\d{4})/);
    if(!m) continue;
    const d=new Date(+m[3],+m[2]-1,+m[1]);
    if(!best||d>best) best=d;
  }
  return best;
}
const RCL_STAGE_KW=["lobbing","uzgodnie","konsultacj","opiniowan","komitet","komisj","rada ministr","radzie ministr","potwierdz","skierowan","notyfikacj","rozpatrz","przyjęc","przyjet"];
function rclStages(text){
  if(!text) return [];
  const t=text.replace(/<[^>]*>/g," ").replace(/[·•|]/g," ").replace(/\s+/g," ");
  const re=/(\d{1,2})\.\s+([\s\S]{3,75}?)(?=\s+Data ostatniej modyfikacji:|\s+\d{1,2}\.\s|\s+Rządowe Centrum|\s+Mapa strony|\s+Pomoc\b|\s+Kontakt\b|$)(?:\s+Data ostatniej modyfikacji:\s*(\d{2}-\d{2}-\d{4}))?/g;
  const items=[]; const seen=new Set(); let m;
  while((m=re.exec(t))){
    const name=m[2].replace(/\s*Data ostatniej modyfikacji.*$/,"").trim();
    const low=name.toLowerCase();
    if(!RCL_STAGE_KW.some(k=>low.includes(k))) continue;
    const key=m[1]+"|"+low.slice(0,20);
    if(seen.has(key)) continue; seen.add(key);
    items.push({n:+m[1], name, date:m[3]||null});
  }
  if(!items.length) return [];
  // sortuj chronologicznie (niedatowane na poczatek), zeby daty szly w dol
  const dkey=d=>{ const m=String(d||"").match(/(\d{2})-(\d{2})-(\d{4})/); return m?(+m[3])*10000+(+m[2])*100+(+m[1]):-1; };
  items.sort((a,b)=>{ const ka=dkey(a.date), kb=dkey(b.date);
    if(ka<0&&kb<0) return 0; if(ka<0) return -1; if(kb<0) return 1; return ka-kb; });
  let cur=0; items.forEach((it,i)=>{ if(it.date) cur=i; });
  items.forEach((it,i)=>{ it.state = i<cur?"done":(i===cur?"cur":"pending"); });
  return items;
}
function inforceHTML(it){
  if(!it.inforce) return "";
  const d=new Date(it.inforce); if(isNaN(d.getTime())) return "";
  const y=d.getFullYear(); if(y<2000||y>2100) return "";
  const today=new Date(); today.setHours(0,0,0,0);
  const x=new Date(d); x.setHours(0,0,0,0);
  const days=Math.round((x-today)/86400000);
  const ds=PL.format(d);
  if(days>0) return `<div class="inforce inforce-soon">⏱ Wchodzi w życie: <b>${esc(ds)}</b> <span class="ifd">za ${days} ${days===1?'dzień':'dni'}</span></div>`;
  if(days===0) return `<div class="inforce inforce-soon">⏱ Wchodzi w życie: <b>dziś (${esc(ds)})</b></div>`;
  return `<div class="inforce inforce-past">Obowiązuje od ${esc(ds)}</div>`;
}
function legisCard(it){
  const when = ago(it._d) || "—";
  const stageLine = it.stage ? `<div class="lstage"><span>Etap</span> ${esc(it.stage)}</div>` : "";
  const isRclGov = (it.src||"").indexOf("RCL")>=0 && !it.left && !it.closed;
  const type = it.type || ((it.src||"").indexOf("RCL")>=0 ? "rcl" : "ustawa");
  const mini = {type, title:it.title, link:it.link, src:it.src, stage:it.stage||"", step:it.step||1};
  let body;
  if(it.continued){
    const cl = it.contLink ? `<a class="rp-link" href="${esc(it.contLink)}" target="_blank" rel="noopener">Znajdź kontynuację${it.contNum?" ("+esc(it.contNum)+")":""} w RCL →</a>` : "";
    body = `<div class="lnote lnote-done">✓ ${esc(it.stage||"Zakończony — kontynuowany pod innym numerem")}</div>${cl}<a class="rp-link" href="${esc(it.link)}" target="_blank" rel="noopener">Szczegóły w RCL →</a>`;
  } else if(it.became){
    const dz = it.dzuLink
      ? `<a class="rp-link" href="${esc(it.dzuLink)}" target="_blank" rel="noopener">Zobacz ustawę w Dz.U.${it.dzuRef?" ("+esc(it.dzuRef)+")":""} →</a>`
      : "";
    const ref = (!it.dzuLink && it.dzuRef) ? `<div class="lstage"><span>Akt</span> ${esc(it.dzuRef)}</div>` : "";
    body = `<div class="lnote lnote-done">✓ ${esc(it.stage||"Zakończony — stał się ustawą")}</div>${ref}${dz}<a class="rp-link" href="${esc(it.link)}" target="_blank" rel="noopener">Szczegóły w RCL →</a>`;
  } else if(it.stages && it.stages.length){
    const cur = it.stages.find(s=>s.state==="cur") || it.stages[it.stages.length-1];
    const staleWarn = it.stale ? `<div class="lnote lnote-warn">⚠ Brak nowych etapów od ${esc(it.staleDate||"dawna")} — projekt może być nieaktywny lub już zakończony. Sprawdź w RCL.</div>` : "";
    body = `<details class="rclproc">
      <summary><span class="rp-now">W rządzie: ${esc(cur?cur.name:"—")}</span><span class="rp-tog">etapy</span></summary>
      <ol class="rp-list">${it.stages.map(s=>`<li class="rp-${s.state}">${esc(s.name)}${s.date?`<i>${esc(s.date)}</i>`:""}</li>`).join("")}</ol>
    </details>${staleWarn}`;
  } else if(it.left){
    body = stepper(1, true) + `<div class="lnote">→ Projekt opuścił etap rządowy — dalszy ciąg w Sejmie (lub już w Dz.U.).</div><a class="rp-link" href="${esc(it.link)}" target="_blank" rel="noopener">Szczegóły / dalszy ciąg w RCL →</a>`;
  } else if(it.closed){
    body = stageLine;
  } else if(isRclGov){
    body = stageLine + `<a class="rp-link" href="${esc(it.link)}" target="_blank" rel="noopener">Zobacz etapy procesu w RCL →</a>`;
  } else {
    body = stepper(it.step||1) + stageLine;
  }
  return `<article class="lcard ${it.left?'is-left':''} ${it.closed?'is-closed':''} ${(it.became||it.continued)?'is-done':''}" style="--ccol:${it.color}">
    <div class="lhead"><span class="lsrc"><span class="dot"></span>${esc(it.src)}</span><span class="lright"><span class="lwhen">${esc(when)}</span>${addbtn(mini)}</span></div>
    <a class="ltitle" href="${esc(it.link)}" target="_blank" rel="noopener">${esc(it.title)}</a>
    ${tagsHTML(taxTags(it.title),2)}
    ${inforceHTML(it)}
    ${body}
  </article>`;
}

const LEGIS_DEFAULT = 18;

// ---- WYSZUKIWANIE NA ŻYWO (Dz.U. + RCL) ----
// Statyczna strona nie ma serwera, więc próbujemy bezpośrednio, a gdy
// przeglądarka zablokuje (CORS) — przez darmowy przekaźnik. Dane publiczne.
// Wlasny Cloudflare Worker (niezawodny przekaznik). Pusty string = wylaczony.
const WORKER_BASE = "https://cbosworker.kalumatix.workers.dev";
const PXY = [
  u => u,
  ...(WORKER_BASE ? [u => WORKER_BASE + "/proxy?url=" + encodeURIComponent(u)] : []),
  u => "https://api.allorigins.win/raw?url=" + encodeURIComponent(u),
  u => "https://corsproxy.io/?url=" + encodeURIComponent(u),
  u => "https://api.codetabs.com/v1/proxy/?quest=" + encodeURIComponent(u),
];
const _cache = new Map();
// Ścigamy wszystkie przekaźniki naraz — bierzemy pierwszy, który odpowie poprawnie.
function _race(urls, asText){
  return new Promise(resolve=>{
    let left=urls.length, done=false;
    if(!left){ resolve(null); return; }
    urls.forEach(u=>{
      fetch(u).then(r=>{ if(!r.ok) throw 0; return asText?r.text():r.json(); })
        .then(v=>{ if(done) return; if(asText && (!v||v.length<50)) throw 0; done=true; resolve(v); })
        .catch(()=>{ if(--left===0 && !done){ done=true; resolve(null); } });
    });
  });
}
async function getJSON(u, fresh){
  if(!fresh && _cache.has("j"+u)) return _cache.get("j"+u);
  const v = await _race(PXY.map(p=>p(u)), false);
  if(v!=null) _cache.set("j"+u, v);   // nie zapamietuj nieudanej proby (null), zeby ponowienie dzialalo
  return v;
}
async function getText(u, fresh){
  if(!fresh && _cache.has("t"+u)) return _cache.get("t"+u);
  const v = await _race(PXY.map(p=>p(u)), true);
  if(v!=null) _cache.set("t"+u, v);
  return v;
}
// Dz.U./M.P. — wyszukiwanie w zakładce „Ustawy"
async function searchDU(){
  const q=(state.qL||$("#searchL").value||"").trim();
  const box=$("#liveResults"), btn=$("#liveBtn");
  if(q.length<2){ box.innerHTML=`<div class="live-status">Wpisz co najmniej 2 znaki.</div>`; return; }
  btn.disabled=true; box.innerHTML=`<div class="live-status">Szukam w Dzienniku Ustaw…</div>`;
  const out=[];
  try{
    const reqs=["DU","MP"].map(pub=>
      getJSON(`https://api.sejm.gov.pl/eli/acts/search?title=${encodeURIComponent(q)}&publisher=${pub}&limit=15`)
        .then(data=>({pub,data})));
    for(const {pub,data} of await Promise.all(reqs)){
      if(data && Array.isArray(data.items)){
        for(const a of data.items.slice(0,15)){
          if(!a||!a.title) continue;
          out.push({type:"ustawa", title:a.title, link:a.ELI?`https://api.sejm.gov.pl/eli/acts/${a.ELI}/text.pdf`:"#",
            src:pub==="DU"?"Dziennik Ustaw":"Monitor Polski", color:pub==="DU"?"#1d3a6b":"#0f5c4a",
            stage:"Opublikowano", step:4, inforce:a.entryIntoForce||null, _d:pd(a.announcementDate||null)});
        }
      }
    }
  }catch(e){}
  btn.disabled=false;
  if(!out.length){ box.innerHTML=`<div class="live-status">Nic nie znalazłem w Dz.U./M.P. dla „${esc(q)}".</div>`; return; }
  box.innerHTML=`<div class="live-sec-head">Dziennik Ustaw / Monitor Polski — „${esc(q)}" (${out.length})</div><div class="lgrid">${out.map(legisCard).join("")}</div>`;
}

// RCL — wyszukiwanie projektów rządowych w zakładce „RCL"
// Tokenizacja zapytania na znaczace RDZENIE slow - lapie odmiane (np. "artystow" -> "artyst" -> "artystyczny").
const RCL_STOP=new Set(["dla","o","i","oraz","albo","lub","na","do","od","po","za","we","ze","przy","ustawa","ustawy","ustawie","projekt","projektu","prawo","prawa","zmianie","zmiany","niektorych","osob","oraz"]);
function rclStems(q){
  const words=q.toLowerCase().split(/[^a-ząćęłńóśźż0-9]+/).filter(w=>w.length>=4 && !RCL_STOP.has(w));
  const stems=words.map(w=>w.length>6 ? w.slice(0,w.length-2) : w);
  return [...new Set(stems)].slice(0,4);
}
async function searchRCL(){
  const q=(state.qR||$("#searchR").value||"").trim();
  const box=$("#rclResults"), btn=$("#rclBtn");
  if(q.length<2){ box.innerHTML=`<div class="live-status">Wpisz co najmniej 2 znaki.</div>`; return; }
  const numLike=/^[A-Za-z]{2}\s?\d{1,4}$/.test(q);
  btn.disabled=true; box.innerHTML=`<div class="live-status">Szukam projektów w RCL…</div>`;
  const out=[];
  try{
    const queries = numLike
      ? ["number="+encodeURIComponent(q.replace(/\s+/g,"").toUpperCase())]
      : (rclStems(q).length ? rclStems(q) : [q]).map(s=>"title="+encodeURIComponent(s));
    const htmls = await Promise.all(queries.map(qq=>getText(`https://legislacja.rcl.gov.pl/lista?typeId=2&${qq}`)));
    const cand=[]; const seen=new Set();
    for(const htmlTxt of htmls){
      if(!htmlTxt) continue;
      const re=/href="(\/projekt\/\d+[^"]*)"[^>]*>([\s\S]*?)<\/a>/g; let m;
      while((m=re.exec(htmlTxt))){
        if(seen.has(m[1])) continue; seen.add(m[1]);
        const t=m[2].replace(/<[^>]*>/g," ").replace(/\s+/g," ").trim();
        if(t.length<6) continue;
        cand.push({title:t, link:"https://legislacja.rcl.gov.pl"+m[1]});
        if(cand.length>=16) break;
      }
      if(cand.length>=16) break;
    }
    // strony projektów pobieramy RÓWNOLEGLE (szybko)
    const pages=await Promise.all(cand.map(c=>getText(c.link)));
    const TWO_Y = Date.now() - 1000*60*60*24*365*2;
    cand.forEach((c,i)=>{
      const page=pages[i]; const st=rclStatus(page); const bl=rclBecameLaw(page);
      const base={type:"rcl", title:c.title, link:c.link, src:"Rząd (RCL)", color:"#8a5a2e", step:1, _d:null};
      if(bl && bl.isLaw)            out.push({...base, became:true, dzuLink:bl.link, dzuRef:bl.ref, stage:"Zakończony — stał się ustawą"});
      else if(bl && bl.continued)   out.push({...base, continued:true, contLink:bl.contLink, contNum:bl.contNum, stage:"Zakończony — kontynuowany pod innym numerem"});
      else if(bl && bl.merged)      out.push({...base, became:true, dzuLink:null, dzuRef:"", stage:"Zakończony — dołączony do innego projektu"});
      else if(st==="in_gov"){
        const stages=rclStages(page); const last=rclLatestDate(stages);
        const stale = last ? (last.getTime() < TWO_Y) : false;
        out.push({...base, stage:"Prace w rządzie", stages, stale, staleDate: (stale&&last)?last.toLocaleDateString("pl-PL"):""});
      }
      else if(st==="left")         out.push({...base, left:true,  stage:"Przekazany do Sejmu (etap rządowy zakończony)"});
      else if(st==="closed"&& numLike) out.push({...base, closed:true, stage:"Zamknięty (etap rządowy)"});
      else if(!st)                 out.push({...base, stage:"Etap rządowy — sprawdź w RCL"});
      // Po SŁOWIE pokazujemy: w toku, "stał się ustawą", "kontynuowany" ORAZ przekazane do Sejmu
      // (te ostatnie to czesto najwazniejsze - projekt idzie dalej). Pomijamy tylko "zamkniete".
    });
  }catch(e){}
  btn.disabled=false;
  if(!out.length){
    const hint=numLike
      ? ` Jeśli to numer (np. UD116) — sprawdź pisownię. Pusto się powtarza? Przekaźnik mógł nie odpowiedzieć, kliknij raz jeszcze.`
      : ` Szukałem po rdzeniach: ${esc(rclStems(q).join(", ")||q)}. RCL bywa, że nazywa rzecz inaczej (np. „emerytura dla artystów" = „zabezpieczenie socjalne osób wykonujących zawód artystyczny") — spróbuj prostszego, rdzennego słowa (np. „artyst", „zabezpieczenie").`;
    box.innerHTML=`<div class="live-status">Nic nie znalazłem w RCL dla „${esc(q)}".${hint}</div>`;
    return;
  }
  box.innerHTML=`<div class="live-sec-head">Projekty rządowe (RCL) — „${esc(q)}" (${out.length})</div><div class="lgrid">${out.map(legisCard).join("")}</div>`;
}

// Wyroki / orzeczenia — wyszukiwarka w bazie SAOS (czyste API JSON, z filtrami i stronicowaniem)
const WYR_CT={ADMINISTRATIVE:["NSA/WSA","#2a6a7a"], SUPREME:["SN","#6b4a8a"], COMMON:["sąd powszechny","#4a4a4a"], CONSTITUTIONAL_TRIBUNAL:["TK","#8a2e2a"], NATIONAL_APPEAL_CHAMBER:["KIO","#8a5a2e"]};
function wyrSnippet(raw){
  if(!raw) return "";
  let t=String(raw).replace(/\s+/g," ").replace(/<(?!\/?em\b)[^>]*>/gi,"").trim();
  if(t.length>340){ t=t.slice(0,340).replace(/<\/?e?m?$/i,"")+"…"; }
  const o=(t.match(/<em>/gi)||[]).length, c=(t.match(/<\/em>/gi)||[]).length;
  if(o>c) t+="</em>".repeat(o-c);
  return esc(t).replace(/&lt;em&gt;/gi,'<mark>').replace(/&lt;\/em&gt;/gi,'</mark>');
}
function wyrokCard(it){
  const court=(it.division && it.division.court && it.division.court.name) || "";
  const sig=(it.courtCases && it.courtCases[0] && it.courtCases[0].caseNumber) || "";
  const date=it.judgmentDate || "";
  const map={SENTENCE:"wyrok", DECISION:"postanowienie", RESOLUTION:"uchwała", REGULATION:"zarządzenie", REASONS:"uzasadnienie"};
  const kind=map[it.judgmentType] || "orzeczenie";
  const ct=WYR_CT[it.courtType]||["sąd","#777"];
  const snip=wyrSnippet(it.textContent||"");
  const snipPlain=(it.textContent||"").replace(/<[^>]*>/g," ");
  const link="https://www.saos.org.pl/judgments/"+it.id;
  const title=(sig ? sig+(court?" — ":"") : "") + court;
  const mini={type:"wyrok", title:title||kind, link:link, src:kind, stage:"", step:0};
  return `<article class="lcard" style="--ccol:${ct[1]}">
    <div class="lhead"><span class="lsrc"><span class="wyr-ct" style="background:${ct[1]}">${esc(ct[0])}</span> ${esc(kind)}${date?" · "+esc(date):""}</span><span class="lright">${addbtn(mini)}</span></div>
    <a class="ltitle" href="${esc(link)}" target="_blank" rel="noopener">${esc(title||kind)}</a>
    ${tagsHTML(taxTags(title+" "+snipPlain),2)}
    ${snip?`<p class="wyrok-snip">${snip}</p>`:""}
  </article>`;
}
let wyrCtx=null;
function wyrQuery(q, phrase){
  q=(q||"").trim();
  if(!q) return "";
  // jesli uzytkownik sam uzyl skladni SAOS (cudzyslow / OR / minus) - nie ruszamy
  if(q.indexOf('"')>=0 || /\bOR\b/.test(q) || /(^|\s)-\S/.test(q)) return q;
  // domyslnie: wieloslowne zapytanie -> dokladna fraza w cudzyslowie (zaweza wyniki)
  if(phrase && /\s/.test(q)) return '"'+q+'"';
  return q;
}
function wyrUrl(c){
  let u=`https://www.saos.org.pl/api/search/judgments?pageSize=20&pageNumber=${c.page}&sortingField=JUDGMENT_DATE&sortingDirection=${c.sort||"DESC"}`;
  if(c.q){ const qq=wyrQuery(c.q,c.phrase); if(qq) u+="&all="+encodeURIComponent(qq); }
  if(c.from) u+="&judgmentDateFrom="+c.from;
  if(c.to) u+="&judgmentDateTo="+c.to;
  if(c.jtype) u+="&judgmentTypes="+c.jtype;
  return u;
}
async function wyrFetch(){
  const box=$("#wyrokiResults"), btn=$("#wyrokiBtn"), c=wyrCtx; if(!c) return;
  if(btn) btn.disabled=true; box.innerHTML=`<div class="live-status">Szukam orzeczeń w SAOS…</div>`;
  let items=[], total=null;
  try{
    const data=await getJSON(wyrUrl(c));
    if(data){ if(Array.isArray(data.items)) items=data.items; if(data.info && typeof data.info.totalResults==="number") total=data.info.totalResults; }
  }catch(e){}
  if(btn) btn.disabled=false;
  if(!items.length){
    box.innerHTML=`<div class="live-status">${c.page>0?"Brak dalszych wyników — wróć na poprzednią stronę.":'Nic nie znalazłem w SAOS dla tych kryteriów. Spróbuj innych słów, odznacz „Dokładna fraza" (wtedy szuka każdego słowa osobno) albo poszerz zakres dat. Przekaźnik mógł też nie odpowiedzieć — kliknij „Szukaj" ponownie.'}</div>`;
    return;
  }
  const totalPages = total!=null ? Math.max(Math.ceil(total/20),1) : null;
  const desc = (c.q?'„'+esc(c.q)+'"':"filtry") + (total!=null?" · "+total+" wyników":"");
  const head=`<div class="live-sec-head">Orzeczenia (SAOS) — ${desc}</div>`;
  const prevOff = c.page<=0;
  const nextOff = totalPages!=null ? (c.page+1>=totalPages) : (items.length<20);
  const pager=`<div class="wyr-pager">
    <button class="livebtn" ${prevOff?"disabled":""} data-wpg="-1">‹ Poprzednia</button>
    <span>strona ${c.page+1}${totalPages?" / "+totalPages:""}</span>
    <button class="livebtn" ${nextOff?"disabled":""} data-wpg="1">Następna ›</button>
  </div>`;
  box.innerHTML=head+`<div class="lgrid">${items.map(wyrokCard).join("")}</div>`+pager;
  box.querySelectorAll("[data-wpg]").forEach(b=>b.onclick=()=>{ wyrCtx.page=Math.max(wyrCtx.page+(+b.dataset.wpg),0); wyrFetch(); });
  syncAddBtns();
}
async function searchWyroki(){
  const box=$("#wyrokiResults");
  const q=($("#searchW").value||"").trim();
  const from=($("#wyrFrom")||{}).value||"", to=($("#wyrTo")||{}).value||"", jtype=($("#wyrType")||{}).value||"", sort=($("#wyrSort")||{}).value||"DESC";
  const phrase=!!(($("#wyrPhrase")||{}).checked);
  if(q.length<2 && !from && !to && !jtype){ box.innerHTML=`<div class="live-status">Wpisz co najmniej 2 znaki albo ustaw filtr (data/typ).</div>`; return; }
  wyrCtx={q, phrase, from, to, jtype, sort, page:0};
  wyrFetch();
}

// Interpretacje KIS — brak publicznego API, wiec kopiujemy fraze i otwieramy oficjalna wyszukiwarke
function searchKIS(){
  const q=($("#searchK").value||"").trim();
  const box=$("#kisResults");
  if(q.length<2){ box.innerHTML=`<div class="live-status">Wpisz co najmniej 2 znaki.</div>`; return; }
  let copied=false;
  try{ navigator.clipboard.writeText(q); copied=true; }catch(_){}
  try{ window.open("https://eureka.mf.gov.pl/", "_blank", "noopener"); }catch(_){}
  box.innerHTML=`<div class="kis-launch">
    <p>${copied?'Skopiowałem frazę <b>„'+esc(q)+'"</b> do schowka.':'Fraza: <b>„'+esc(q)+'"</b>.'} Otworzyłem EUREKA w nowej karcie — wklej (Ctrl+V) w pole „Wpisz frazę wyszukiwania" i naciśnij szukaj.</p>
    <p class="kis-alt">Inne bazy z tą frazą: <a href="https://interpretacje.gofin.pl/" target="_blank" rel="noopener">Interpretacje GOFIN</a> · <a href="https://www.podatki.gov.pl/interpretacje-indywidualne/" target="_blank" rel="noopener">KIS / podatki.gov.pl</a></p>
  </div>`;
}

function renderNews(){
  const vis=newsVisible(); const feed=$("#feed");
  if(!vis.length){
    feed.innerHTML=`<div class="empty"><div class="ic">§</div><h3>Brak wiadomości</h3><p>Zmień frazę albo włącz więcej źródeł powyżej.</p></div>`;
    updateNewsBadge(0);
    return 0;
  }
  let newCount=0;
  let h="",last=null;
  for(const it of vis){
    const fresh=isNew(it); if(fresh) newCount++;
    const k=dayKey(it._d);
    if(k!==last){h+=`<div class="daysep"><span class="lab">${esc(dayLabel(it._d))}</span><span class="rule"></span></div>`;last=k;}
    h+=`<article class="card${fresh?' is-new':''}" style="--ccol:${it.color}">
      <div class="chead"><span class="src"><span class="dot"></span>${esc(it.src)} <span class="cat">${esc(it.cat)}</span>${fresh?'<span class="new-pill">NOWE</span>':''}</span>${addbtn({type:"news", title:it.title, link:it.link, src:it.src, stage:"", step:0})}</div>
      <a class="title" href="${esc(it.link)}" target="_blank" rel="noopener">${esc(it.title)}</a>
      ${tagsHTML(it._tags,2)}
      ${ (it.summary||it.desc) ? `<p class="desc${it.summary?' sum':''}">${it.summary?'<span class="aitag">✦ streszczenie</span> ':''}${esc(it.summary||it.desc)}</p>` : "" }
      <div class="meta">${esc(ago(it._d))||"—"}</div>
    </article>`;
  }
  let bar="";
  if(newCount>0){
    const when=new Date(LAST_VISIT).toLocaleString("pl-PL",{day:"numeric",month:"long",hour:"2-digit",minute:"2-digit"});
    bar=`<div class="newbar">✦ Nowe od ostatniej wizyty: <b>${newCount}</b> <span class="nbwhen">(byłeś tu: ${esc(when)})</span></div>`;
  }
  feed.innerHTML=bar+h; updateNewsBadge(newCount); return vis.length;
}
function updateNewsBadge(n){
  const b=$("#newsBadge"); if(!b) return;
  b.textContent=n>0?n:""; b.classList.toggle("show", n>0);
}

function renderTracker(items, L, searching, headOn, countWord){
  if(!L) return 0;
  if(!items.length){
    L.innerHTML=`<div class="empty"><div class="ic">§</div><h3>Brak pozycji</h3><p>${searching?'Nic nie pasuje do tej frazy.':'Brak świeżych pozycji.'}</p></div>`;
    return 0;
  }
  const show = searching ? items : items.slice(0, LEGIS_DEFAULT);
  let h=`<div class="lsec-head"><span class="lt">${searching?'Wyniki wyszukiwania':headOn}</span>`
    +`<span class="lcount">${searching?(items.length+' znalezionych'):(items.length+' '+countWord)}</span></div>`
    +`<div class="lgrid">${show.map(legisCard).join("")}</div>`;
  if(!searching && items.length>LEGIS_DEFAULT){
    h+=`<button class="showall">Pokaż wszystkie (${items.length})</button>`;
  }
  L.innerHTML=h;
  const sa=L.querySelector(".showall");
  if(sa) sa.onclick=()=>{ L.querySelector(".lgrid").innerHTML=items.map(legisCard).join(""); sa.remove(); };
  return items.length;
}
function renderUstawy(){
  const all=ustawyVisible();
  const searching=state.qL.trim().length>0;
  const soonBox=$("#legisSoon"), soonEmpty=$("#legisSoonEmpty");
  const now=Date.now();
  const fdate=it=>{ if(!it.inforce) return null; const d=new Date(it.inforce); return isNaN(d.getTime())?null:d; };
  const isFuture=it=>{ const d=fdate(it); return d && d.getTime()>now; };
  const soon=all.filter(isFuture).sort((a,b)=>fdate(a)-fdate(b));
  if(soonBox){
    soonBox.innerHTML = soon.length
      ? `<div class="lsec-head"><span class="lt">⏱ Wchodzą w życie wkrótce</span><span class="lcount">${soon.length}</span></div>`
        +`<p class="livehint" style="margin:-4px 0 14px">Akty opublikowane, które dopiero zaczną obowiązywać. Śledzę datę wejścia w życie najnowszych aktów z Dz.U./M.P.</p>`
        +`<div class="lgrid">${soon.map(legisCard).join("")}</div>`
      : "";
  }
  if(soonEmpty) soonEmpty.hidden = soon.length>0;
  if(searching) return renderTracker(all, $("#legis"), true, "Sejm i Dziennik Ustaw", "śledzonych");
  const rest=all.filter(it=>!isFuture(it));
  return renderTracker(rest, $("#legis"), false, "Sejm i Dziennik Ustaw", "śledzonych") + soon.length;
}
function renderRcl(){ return renderTracker(rclVisible(), $("#rclList"), state.qR.trim().length>0, "Projekty na etapie rządowym", "projektów"); }

function mojeCard(it){
  const label = it.type==="news" ? "Wiadomość" : it.type==="rcl" ? "RCL" : it.type==="wyrok" ? "Wyrok" : "Ustawa";
  const col   = it.type==="news" ? "#7a5a2e" : it.type==="rcl" ? "#8a2e2a" : it.type==="wyrok" ? "#3a5c8a" : "#1d3a6b";
  const st = it.stage ? `<div class="lstage"><span>Etap</span> ${esc(it.stage)}</div>` : "";
  const bell = (it.type==="rcl" || it.type==="ustawa")
    ? `<button class="notifybtn${it.notify?' on':''}" data-notify="${esc(it.link)}" title="${it.notify?'Pilnowane mailowo - kliknij, aby wyłączyć':'Dodaj do powiadomień mailowych'}">${it.notify?'🔔 pilnowane':'🔔 powiadom'}</button>`
    : "";
  return `<article class="lcard" style="--ccol:${col}">
    <div class="lhead"><span class="lsrc"><span class="dot"></span><b>${esc(label)}</b>${it.src?" · "+esc(it.src):""}</span>
      <span class="lacts">${bell}<button class="addbtn added" data-rm="${esc(it.link)}" title="Usuń z „Moje”">✓</button></span></div>
    <a class="ltitle" href="${esc(it.link)}" target="_blank" rel="noopener">${esc(it.title)}</a>
    ${st}
  </article>`;
}
function renderMoje(){
  const L=$("#mojeList"); if(!L) return 0;
  if(!state.moje.length){
    L.innerHTML=`<div class="empty"><div class="ic">§</div><h3>Pusto w „Moje"</h3><p>Dodawaj pozycje plusikiem + z innych zakładek.</p></div>`;
    return 0;
  }
  const watch = state.moje.filter(x=>(x.type==="rcl"||x.type==="ustawa") && x.notify);
  let panel = "";
  if(watch.length){
    const lines = watch.map(x=>x.link).join("\n");
    const word = watch.length===1 ? "projekt" : "projekty";
    panel = `<div class="notifybox">
      <div class="nb-head">🔔 Do powiadomień mailowych - ${watch.length} ${word}</div>
      <p class="nb-info">Te projekty masz oznaczone dzwonkiem. Żeby robot pisał Ci maila przy zmianie etapu, wklej poniższe linie do pliku <b>obserwowane.txt</b> w repozytorium (dopisz na końcu) i zatwierdź zmianę.</p>
      <textarea class="nb-text" readonly rows="${Math.min(watch.length,6)}" onclick="this.select()">${esc(lines)}</textarea>
      <button class="nb-copy" id="nbCopy">Kopiuj linie</button>
    </div>`;
  }
  L.innerHTML = panel
    + `<div class="lsec-head"><span class="lt">Twoja kolekcja</span><span class="lcount">${state.moje.length} zapisanych</span></div>`
    + `<div class="lgrid">${state.moje.map(mojeCard).join("")}</div>`;
  const cp=$("#nbCopy");
  if(cp) cp.onclick=()=>{
    const ta=L.querySelector(".nb-text"); if(!ta) return;
    ta.select();
    let ok=false;
    try{ navigator.clipboard.writeText(ta.value); ok=true; }catch(_){}
    if(!ok){ try{ document.execCommand("copy"); }catch(_){} }
    cp.textContent="Skopiowano ✓";
    setTimeout(()=>{ cp.textContent="Kopiuj linie"; },1600);
  };
  return state.moje.length;
}

function updateMojeBadge(n){
  const b=$("#mojeBadge"); if(!b) return;
  b.textContent=n||""; b.classList.toggle("show",(n||0)>0);
}
// ===== TERMINARZ PODATKOWY (czysta logika dat - bez API, liczone w przegladarce) =====
const T_MIES=["styczeń","luty","marzec","kwiecień","maj","czerwiec","lipiec","sierpień","wrzesień","październik","listopad","grudzień"];
const T_DNI=["niedz.","pon.","wt.","śr.","czw.","pt.","sob."];
const T_CAT={VAT:"#1d3a6b", PIT:"#8a2e2a", CIT:"#6b4a8a", ZUS:"#0f5c4a", Kadry:"#8a5a2e", Roczne:"#4a4a4a", "Ceny transf.":"#2a6a7a"};
// Reguly terminow. day=dzien ustawowy; months=miesiace wystepowania; okres=opis za jaki okres.
const T_RULES=[
  {day:15, months:[1,2,3,4,5,6,7,8,9,10,11,12], cat:"ZUS", label:"Składki ZUS — płatnicy będący osobami prawnymi", okres:"prev-month", note:"np. spółki z o.o., S.A."},
  {day:20, months:[1,2,3,4,5,6,7,8,9,10,11,12], cat:"ZUS", label:"Składki ZUS — pozostali płatnicy", okres:"prev-month", note:"m.in. JDG, osoby fizyczne i podmioty bez osobowości prawnej"},
  {day:20, months:[1,2,3,4,5,6,7,8,9,10,11,12], cat:"PIT", label:"Zaliczka PIT — skala / liniowy (miesięcznie)", okres:"prev-month", note:"przedsiębiorcy rozliczający się miesięcznie"},
  {day:20, months:[1,2,3,4,5,6,7,8,9,10,11,12], cat:"PIT", label:"Ryczałt od przychodów ewidencjonowanych (miesięcznie)", okres:"prev-month", note:""},
  {day:20, months:[1,2,3,4,5,6,7,8,9,10,11,12], cat:"Kadry", label:"Zaliczki na PIT od wynagrodzeń (płatnik, PIT-4)", okres:"prev-month", note:"zaliczki pobrane od pracowników / zleceniobiorców"},
  {day:20, months:[1,2,3,4,5,6,7,8,9,10,11,12], cat:"CIT", label:"Zaliczka CIT (miesięcznie)", okres:"prev-month", note:"spółki rozliczające się miesięcznie"},
  {day:25, months:[1,2,3,4,5,6,7,8,9,10,11,12], cat:"VAT", label:"JPK_V7M — plik JPK + zapłata VAT", okres:"prev-month", note:"rozliczenie miesięczne VAT"},
  {day:25, months:[1,2,3,4,5,6,7,8,9,10,11,12], cat:"VAT", label:"Informacja podsumowująca VAT-UE", okres:"prev-month", note:"transakcje wewnątrzwspólnotowe"},
  {day:25, months:[1,4,7,10], cat:"VAT", label:"JPK_V7K — deklaracja kwartalna VAT", okres:"prev-quarter", note:"rozliczenie kwartalne VAT"},
  {day:20, months:[1,4,7,10], cat:"PIT", label:"Zaliczka kwartalna PIT / CIT", okres:"prev-quarter", note:"dla rozliczających się kwartalnie"},
  {day:20, months:[1,4,7,10], cat:"PIT", label:"Ryczałt — rozliczenie kwartalne", okres:"prev-quarter", note:""},
  {day:31, months:[1], cat:"Kadry", label:"PIT-4R i PIT-8AR — roczne deklaracje płatnika", okres:"prev-year", note:"do urzędu skarbowego"},
  {day:31, months:[1], cat:"Kadry", label:"PIT-11 — przekazanie do urzędu skarbowego", okres:"prev-year", note:"informacje o dochodach"},
  {day:28, months:[2], cat:"Kadry", label:"PIT-11 — przekazanie podatnikowi (pracownikowi)", okres:"prev-year", note:"termin: koniec lutego", lastFeb:true},
  {day:31, months:[3], cat:"CIT", label:"CIT-8 — zeznanie roczne CIT", okres:"prev-year", note:"gdy rok podatkowy = kalendarzowy"},
  {day:31, months:[3], cat:"Roczne", label:"Sporządzenie sprawozdania finansowego", okres:"prev-year", note:"jednostki prowadzące księgi rachunkowe"},
  {day:30, months:[4], cat:"PIT", label:"PIT roczny (PIT-36/37/36L/28/38/39)", okres:"prev-year", note:"zeznania roczne osób fizycznych"},
  {day:20, months:[5], cat:"ZUS", label:"Roczne rozliczenie składki zdrowotnej", okres:"prev-year", note:"przedsiębiorcy"},
  {day:30, months:[6], cat:"Roczne", label:"Zatwierdzenie sprawozdania finansowego", okres:"prev-year", note:"do 6 mies. po zakończeniu roku"},
  {day:31, months:[1], cat:"ZUS", label:"ZUS IWA — informacja do ustalenia składki wypadkowej", okres:"prev-year", note:"płatnicy spełniający warunki (m.in. ≥10 ubezpieczonych)"},
  {day:31, months:[3], cat:"CIT", label:"IFT-2R — informacja o wypłatach do nierezydentów", okres:"prev-year", note:"koniec 3. mies. po roku podatkowym (rok kalendarzowy)"},
  {day:31, months:[3], cat:"Ceny transf.", label:"Powiadomienie CbC-P", okres:"prev-year", note:"3 mies. po końcu roku grupy (rok kalendarzowy)"},
  {day:31, months:[10], cat:"Ceny transf.", label:"Dokumentacja cen transferowych (local file)", okres:"prev-year", note:"koniec 10. mies. po roku podatkowym (rok kalendarzowy)"},
  {day:30, months:[11], cat:"Ceny transf.", label:"Informacja o cenach transferowych TPR-C / TPR-P", okres:"prev-year", note:"koniec 11. mies. po roku podatkowym"},
  {day:31, months:[12], cat:"Ceny transf.", label:"Raport CbC-R", okres:"prev-year", note:"12 mies. po końcu roku sprawozdawczego grupy"}
];
const _holCache={};
function tEaster(y){
  const a=y%19,b=Math.floor(y/100),c=y%100,d=Math.floor(b/4),e=b%4,f=Math.floor((b+8)/25),
    g=Math.floor((b-f+1)/3),h=(19*a+b-d-g+15)%30,i=Math.floor(c/4),k=c%4,
    l=(32+2*e+2*i-h-k)%7,m=Math.floor((a+11*h+22*l)/451),
    mo=Math.floor((h+l-7*m+114)/31),da=((h+l-7*m+114)%31)+1;
  return new Date(y,mo-1,da);
}
function tKey(d){ return d.getFullYear()+"-"+(d.getMonth()+1)+"-"+d.getDate(); }
function tHolidays(y){
  if(_holCache[y]) return _holCache[y];
  const s=new Set(["1-1","1-6","5-1","5-3","8-15","11-1","11-11","12-25","12-26"].map(x=>y+"-"+x));
  const e=tEaster(y);
  const mon=new Date(e); mon.setDate(e.getDate()+1);   // Poniedziałek Wielkanocny
  const cor=new Date(e); cor.setDate(e.getDate()+60);  // Boże Ciało
  s.add(tKey(mon)); s.add(tKey(cor));
  _holCache[y]=s; return s;
}
function tWorking(d){
  const g=d.getDay();
  if(g===0||g===6) return false;
  return !tHolidays(d.getFullYear()).has(tKey(d));
}
function tRoll(d){
  const x=new Date(d.getFullYear(),d.getMonth(),d.getDate());
  while(!tWorking(x)) x.setDate(x.getDate()+1);
  return x;
}
function tOkres(kind,mo,yr){
  if(kind==="prev-month"){ let m=mo-1,y=yr; if(m<1){m=12;y--;} return "za "+T_MIES[m-1]+" "+y; }
  if(kind==="prev-quarter"){ const map={4:["I kwartał",0],7:["II kwartał",0],10:["III kwartał",0],1:["IV kwartał",-1]}; const q=map[mo]; return q?("za "+q[0]+" "+(yr+q[1])):""; }
  if(kind==="prev-year") return "za "+(yr-1)+" r.";
  return "";
}
function tUpcoming(days){
  const now=new Date(); const today0=new Date(now.getFullYear(),now.getMonth(),now.getDate());
  const horizon=new Date(today0); horizon.setDate(today0.getDate()+days);
  const out=[];
  for(const r of T_RULES){
    // sprawdzamy biezacy i nastepny rok, kazdy pasujacy miesiac
    for(let yr=today0.getFullYear(); yr<=today0.getFullYear()+1; yr++){
      for(const mo of r.months){
        let dayNum=r.day;
        if(r.lastFeb) dayNum=new Date(yr,2,0).getDate();   // ostatni dzień lutego
        const stat=new Date(yr,mo-1,dayNum);
        const eff=tRoll(stat);
        if(eff<today0 || eff>horizon) continue;
        const diff=Math.round((eff-today0)/86400000);
        const rolled=eff.getTime()!==stat.getTime();
        out.push({eff, stat, rolled, diff, cat:r.cat, label:r.label, note:r.note,
                  okres:tOkres(r.okres,mo,yr)});
      }
    }
  }
  out.sort((a,b)=> a.eff-b.eff || (a.cat<b.cat?-1:1));
  return out;
}
function tSaveOff(){ try{ localStorage.setItem("paragraf-termoff", JSON.stringify([...state.termOff])); }catch(_){} }
function tLoadOff(){ try{ const r=localStorage.getItem("paragraf-termoff"); if(r) state.termOff=new Set(JSON.parse(r)); }catch(_){} }
// --- eksport do kalendarza (.ics / Google) ---
function icsDate(d){ return d.getFullYear()+String(d.getMonth()+1).padStart(2,"0")+String(d.getDate()).padStart(2,"0"); }
function icsEsc(s){ return (s||"").replace(/\\/g,"\\\\").replace(/;/g,"\\;").replace(/,/g,"\\,").replace(/\n/g,"\\n"); }
function icsEvent(summary,d,desc){
  const start=icsDate(d), end=icsDate(new Date(d.getFullYear(),d.getMonth(),d.getDate()+1));
  const uid="paragraf-"+start+"-"+Math.random().toString(36).slice(2,8)+"@paragraf";
  const stamp=new Date().toISOString().replace(/[-:]/g,"").split(".")[0]+"Z";
  return ["BEGIN:VEVENT","UID:"+uid,"DTSTAMP:"+stamp,"DTSTART;VALUE=DATE:"+start,"DTEND;VALUE=DATE:"+end,
    "SUMMARY:"+icsEsc(summary), desc?("DESCRIPTION:"+icsEsc(desc)):"",
    "BEGIN:VALARM","ACTION:DISPLAY","DESCRIPTION:"+icsEsc(summary),"TRIGGER:-P2D","END:VALARM",
    "END:VEVENT"].filter(Boolean).join("\r\n");
}
function icsWrap(events){ return ["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Paragraf//PL","CALSCALE:GREGORIAN",...events,"END:VCALENDAR"].join("\r\n"); }
function icsHref(ics){ return "data:text/calendar;charset=utf-8,"+encodeURIComponent(ics); }
function gcalHref(summary,d,desc){
  const start=icsDate(d), end=icsDate(new Date(d.getFullYear(),d.getMonth(),d.getDate()+1));
  const p=new URLSearchParams({action:"TEMPLATE", text:summary, dates:start+"/"+end, details:desc||""});
  return "https://calendar.google.com/calendar/render?"+p.toString();
}
function calLinks(summary,d,desc){
  const ics=icsWrap([icsEvent(summary,d,desc)]);
  return `<div class="tcal"><span class="tcal-lab">Do kalendarza:</span>`
    +`<a class="tcal-l" href="${esc(gcalHref(summary,d,desc))}" target="_blank" rel="noopener">Google</a>`
    +`<a class="tcal-l" href="${esc(icsHref(ics))}" download="termin.ics">iCal (.ics)</a></div>`;
}

// --- kalkulator terminu liczonego OD ZDARZENIA (np. MDR 30 dni) ---
const TERM_REL=[
  {key:"mdr",  n:30, unit:"d",  label:"MDR — schemat podatkowy",                 desc:"30 dni, licząc od następnego dnia po zdarzeniu (np. wdrożenie / udostępnienie schematu)."},
  {key:"crbr", n:14, unit:"dr", label:"CRBR — beneficjent rzeczywisty",          desc:"14 dni roboczych od wpisu/zmiany w KRS (soboty i święta nie liczą się do biegu)."},
  {key:"pcc",  n:14, unit:"d",  label:"PCC-3 — np. pożyczka, umowa",             desc:"14 dni od powstania obowiązku podatkowego."},
  {key:"vatr", n:7,  unit:"d",  label:"VAT-R — aktualizacja danych",             desc:"7 dni od zmiany danych objętych zgłoszeniem."},
  {key:"zua",  n:7,  unit:"d",  label:"ZUS ZUA/ZZA — zgłoszenie do ubezpieczeń",  desc:"7 dni od powstania obowiązku ubezpieczeń."},
  {key:"rud",  n:7,  unit:"d",  label:"RUD — umowa o dzieło",                     desc:"7 dni od zawarcia umowy."},
  {key:"sdz2", n:6,  unit:"m",  label:"SD-Z2 — spadek/darowizna (zwolnienie)",    desc:"6 miesięcy od powstania obowiązku (nabycia)."},
];
function unitLab(n,u){ if(u==="m") return n+" "+(n===1?"miesiąc":"miesięcy"); if(u==="dr") return n+" dni roboczych"; return n+" dni"; }
function relDeadline(rule, ev){
  if(rule.unit==="m"){
    const tm=ev.getMonth()+rule.n, ty=ev.getFullYear();
    const last=new Date(ty,tm+1,0).getDate();
    return tRoll(new Date(ty,tm,Math.min(ev.getDate(),last)));
  }
  if(rule.unit==="dr"){
    const d=new Date(ev.getFullYear(),ev.getMonth(),ev.getDate()); let c=0;
    while(c<rule.n){ d.setDate(d.getDate()+1); if(tWorking(d)) c++; }
    return d;
  }
  return tRoll(new Date(ev.getFullYear(),ev.getMonth(),ev.getDate()+rule.n));
}
function renderTermCalc(){
  const box=$("#termCalc"); if(!box) return;
  if(!box.dataset.init){
    box.dataset.init="1";
    const opts=TERM_REL.map((r,i)=>`<option value="${i}">${esc(r.label)} (${unitLab(r.n,r.unit)})</option>`).join("");
    const today=new Date(); const td=today.getFullYear()+"-"+String(today.getMonth()+1).padStart(2,"0")+"-"+String(today.getDate()).padStart(2,"0");
    box.innerHTML=`<div class="sci-head" style="margin-top:26px">Kalkulator terminu (od zdarzenia)</div>
      <p class="livehint" style="margin:-4px 0 12px">Wybierz obowiązek i datę zdarzenia — policzę termin (z przesunięciem na dzień roboczy). Lista pełni też rolę ściągi.</p>
      <div class="trc">
        <select id="trcSel" class="conv-cur">${opts}</select>
        <input id="trcDate" class="conv-amt" type="date" value="${td}" style="width:170px">
      </div>
      <div id="trcOut" class="trc-out"></div>`;
    $("#trcSel").addEventListener("change",calcRel);
    $("#trcDate").addEventListener("input",calcRel);
  }
  calcRel();
}
function calcRel(){
  const sel=$("#trcSel"), di=$("#trcDate"), out=$("#trcOut"); if(!sel||!di||!out) return;
  const rule=TERM_REL[+sel.value]; const parts=(di.value||"").split("-");
  if(parts.length!==3){ out.innerHTML=""; return; }
  const ev=new Date(+parts[0], +parts[1]-1, +parts[2]); if(isNaN(ev.getTime())){ out.innerHTML=""; return; }
  const dl=relDeadline(rule, ev);
  const today0=new Date(); today0.setHours(0,0,0,0);
  const diff=Math.round((dl-today0)/86400000);
  const when = diff<0?("minął "+(-diff)+" dni temu"):diff===0?"to dziś!":diff===1?"to jutro":("za "+diff+" dni");
  const dstr=dl.toLocaleDateString("pl-PL",{weekday:"long",day:"numeric",month:"long",year:"numeric"});
  const sumcal="Termin: "+rule.label.split(" — ")[0];
  out.innerHTML=`<div class="trc-res ${diff<0?'trc-past':diff<=3?'trc-now':''}">Termin: <b>${esc(dstr)}</b> <span class="trc-when">(${esc(when)})</span></div>
    <div class="trc-desc">${esc(rule.desc)}</div>
    ${calLinks(sumcal, dl, rule.desc)}`;
}
function tCard(it){
  const dd=String(it.eff.getDate()).padStart(2,"0")+"."+String(it.eff.getMonth()+1).padStart(2,"0");
  const wd=T_DNI[it.eff.getDay()];
  const when = it.diff===0?"dziś!":it.diff===1?"jutro":("za "+it.diff+" dni");
  const urg = it.diff<=2?"t-now":it.diff<=7?"t-soon":"";
  const od=String(it.stat.getDate()).padStart(2,"0")+"."+String(it.stat.getMonth()+1).padStart(2,"0");
  const meta=[it.cat, it.okres].filter(Boolean).join(" · ") + (it.rolled?` · przesunięty z ${od} (dzień wolny)`:"");
  return `<article class="tcard ${urg}" style="--ccol:${T_CAT[it.cat]||"#777"}">
    <div class="thead"><span class="tdate">${dd} <i>${wd}</i></span><span class="twhen ${urg}">${esc(when)}</span></div>
    <div class="ttitle">${esc(it.label)}</div>
    <div class="tmeta">${esc(meta)}</div>
    ${it.note?`<div class="tnote">${esc(it.note)}</div>`:""}
    ${calLinks("Termin: "+it.label, it.eff, [it.okres,it.note].filter(Boolean).join(" · "))}
  </article>`;
}
function renderTermChips(){
  const box=$("#termChips"); if(!box) return;
  const cats=Object.keys(T_CAT);
  box.innerHTML=cats.map(c=>`<button class="chip" data-tcat="${c}" data-on="${state.termOff.has(c)?0:1}"><span class="dot" style="background:${T_CAT[c]}"></span>${c}</button>`).join("");
  box.querySelectorAll("[data-tcat]").forEach(b=>b.onclick=()=>{
    const c=b.dataset.tcat;
    if(state.termOff.has(c)) state.termOff.delete(c); else state.termOff.add(c);
    tSaveOff(); render();
  });
}
function renderTerminy(){
  renderTermChips();
  renderTermCalc();
  const box=$("#terminyList"); if(!box) return 0;
  const all=tUpcoming(45).filter(it=>!state.termOff.has(it.cat));
  const now=new Date();
  const head=`<div class="t-today">Dziś: <b>${now.toLocaleDateString("pl-PL",{weekday:"long",day:"numeric",month:"long",year:"numeric"})}</b> · najbliższe 45 dni</div>`;
  if(!all.length){ box.innerHTML=head+`<div class="live-status">Brak terminów w tym oknie dla wybranych kategorii (sprawdź chipy wyżej).</div>`; return 0; }
  const allIcs=icsWrap(all.map(it=>icsEvent("Termin: "+it.label, it.eff, [it.okres,it.note].filter(Boolean).join(" · "))));
  const dlAll=`<a class="tdl-all" href="${esc(icsHref(allIcs))}" download="terminy-paragraf.ics">⤓ Dodaj wszystkie (${all.length}) do kalendarza (.ics)</a>`;
  box.innerHTML=head+dlAll+`<div class="tgrid">${all.map(tCard).join("")}</div>`;
  return all.length;
}

// ===== WSKAŹNIKI: kursy NBP (na żywo) + ściągawka stawek =====
const NBP_MAIN=["EUR","USD","GBP","CHF"];
function fmtMid(m){ const s=(typeof m==="number"?m.toFixed(4):String(m)); return s.replace(".",","); }
function renderNBP(tab){
  const box=$("#nbpBox"); if(!box) return;
  const rates=(tab.rates||[]).slice();
  rates.sort((a,b)=>{ const ia=NBP_MAIN.indexOf(a.code), ib=NBP_MAIN.indexOf(b.code);
    if(ia>=0||ib>=0){ if(ia<0)return 1; if(ib<0)return -1; return ia-ib; } return a.code<b.code?-1:1; });
  const cards=rates.map(r=>`<div class="rate${NBP_MAIN.includes(r.code)?' rmain':''}"><span class="rc">${esc(r.code)}</span><span class="rm">${fmtMid(r.mid)} zł</span><span class="rn">${esc(r.currency)}</span></div>`).join("");
  box.innerHTML=`<div class="nbp-head"><span>Kursy średnie NBP — tabela ${esc(tab.no||"")} z dnia ${esc(tab.effectiveDate||"")}</span><button class="livebtn" id="nbpRefresh">Odśwież</button></div>
    <div class="rategrid">${cards}</div>
    <p class="livehint" style="margin-top:8px">1 jednostka waluty = ile zł (kurs średni, tabela A). Do przeliczeń podatkowych zwykle bierze się kurs średni z <b>ostatniego dnia roboczego poprzedzającego</b> dzień uzyskania przychodu / poniesienia kosztu.</p>`;
  const rb=$("#nbpRefresh"); if(rb) rb.onclick=()=>{ state.nbpData=null; state.nbpLoading=false; loadNBP(true); };
}
async function loadNBP(fresh){
  const box=$("#nbpBox"); if(!box||state.nbpLoading) return;
  state.nbpLoading=true; box.innerHTML=`<div class="live-status">Pobieram kursy z NBP…</div>`;
  const fail=(msg)=>{ state.nbpLoading=false; box.innerHTML=`<div class="live-status">${msg} <button class="livebtn" id="nbpRefresh" style="margin-left:6px">Spróbuj ponownie</button></div>`; const rb=$("#nbpRefresh"); if(rb) rb.onclick=()=>{ state.nbpLoading=false; loadNBP(true); }; };
  try{
    const data=await getJSON("https://api.nbp.pl/api/exchangerates/tables/A/?format=json", fresh);
    const tab=Array.isArray(data)?data[0]:((data&&data.rates)?data:null);
    state.nbpLoading=false;
    if(!tab||!tab.rates){ fail("Nie udało się pobrać kursów NBP (serwis nie odpowiedział)."); return; }
    state.nbpData=tab; renderNBP(tab); renderConverter(tab);
  }catch(e){ fail("Błąd pobierania kursów NBP."); }
}
// ---- KURSY: przelicznik walut (po kursie srednim NBP) ----
function renderConverter(tab){
  const box=$("#kursyCalc"); if(!box) return;
  const rates=(tab&&tab.rates)?tab.rates.slice():[];
  if(!rates.length){ box.innerHTML=""; return; }
  rates.sort((a,b)=>{ const ia=NBP_MAIN.indexOf(a.code), ib=NBP_MAIN.indexOf(b.code);
    if(ia>=0||ib>=0){ if(ia<0)return 1; if(ib<0)return -1; return ia-ib; } return a.code<b.code?-1:1; });
  const opts=rates.map(r=>`<option value="${esc(r.code)}" data-mid="${r.mid}">${esc(r.code)} — ${esc(r.currency)}</option>`).join("");
  box.innerHTML=`<div class="conv-head">Przelicznik walut</div>
    <div class="conv">
      <input id="convAmt" class="conv-amt" type="number" inputmode="decimal" value="100" min="0" step="any">
      <select id="convCur" class="conv-cur">${opts}</select>
      <span class="conv-eq">=</span>
      <span id="convOut" class="conv-out">—</span>
      <button class="conv-swap" id="convSwap" title="Odwróć kierunek">⇄</button>
    </div>
    <p class="livehint" id="convNote" style="margin-top:8px">Po kursie średnim NBP (tabela A z dnia ${esc(tab.effectiveDate||"")}). Kierunek: waluta → złoty.</p>`;
  let toPLN=true; // true: waluta->PLN, false: PLN->waluta
  const amt=$("#convAmt"), cur=$("#convCur"), out=$("#convOut"), note=$("#convNote"), swap=$("#convSwap");
  function calc(){
    const opt=cur.options[cur.selectedIndex]; const mid=opt?parseFloat(opt.dataset.mid):NaN;
    const a=parseFloat((amt.value||"").replace(",", ".")); 
    if(!isFinite(mid)||!isFinite(a)){ out.textContent="—"; return; }
    if(toPLN){ const v=a*mid; out.textContent=v.toLocaleString("pl-PL",{minimumFractionDigits:2,maximumFractionDigits:2})+" zł"; }
    else { const v=a/mid; out.textContent=v.toLocaleString("pl-PL",{minimumFractionDigits:2,maximumFractionDigits:2})+" "+cur.value; }
  }
  amt.addEventListener("input",calc); cur.addEventListener("change",calc);
  swap.onclick=()=>{ toPLN=!toPLN; note.textContent="Po kursie średnim NBP (tabela A z dnia "+(tab.effectiveDate||"")+"). Kierunek: "+(toPLN?"waluta → złoty":"złoty → waluta")+"."; calc(); };
  calc();
}
function renderKursy(){
  if(state.nbpData){ renderNBP(state.nbpData); renderConverter(state.nbpData); }
  else loadNBP();
  return state.nbpData?state.nbpData.rates.length:0;
}

// ---- STAWKI: sciagawka + tabela VAT (sprzedaz transgraniczna) ----
// === VAT/GST: stawki standardowe i obnizone wg panstwa (caly swiat, ~135 panstw) ===
// Stan: styczen 2026 (zrodla: eClear, VATupdate/PwC). std=null => brak VAT (inny podatek).
const VAT_COUNTRIES=[
  // --- Europa ---
  {name:"Albania", flag:"🇦🇱", region:"Europa", std:20, red:[10,6]},
  {name:"Andora", flag:"🇦🇩", region:"Europa", std:4.5, red:[2.5,1], note:"Najniższa stawka standardowa w Europie."},
  {name:"Austria", flag:"🇦🇹", region:"Europa", tax:"USt", std:20, red:[13,10]},
  {name:"Belgia", flag:"🇧🇪", region:"Europa", tax:"TVA/BTW", std:21, red:[12,6]},
  {name:"Białoruś", flag:"🇧🇾", region:"Europa", std:20, red:[10]},
  {name:"Bośnia i Hercegowina", flag:"🇧🇦", region:"Europa", std:17, red:[]},
  {name:"Bułgaria", flag:"🇧🇬", region:"Europa", tax:"DDS", std:20, red:[9]},
  {name:"Chorwacja", flag:"🇭🇷", region:"Europa", tax:"PDV", std:25, red:[13,5]},
  {name:"Cypr", flag:"🇨🇾", region:"Europa", tax:"FPA", std:19, red:[9,5,3]},
  {name:"Czarnogóra", flag:"🇲🇪", region:"Europa", std:21, red:[15,7]},
  {name:"Czechy", flag:"🇨🇿", region:"Europa", tax:"DPH", std:21, red:[12]},
  {name:"Dania", flag:"🇩🇰", region:"Europa", tax:"MOMS", std:25, red:[], note:"Jedna stawka — bez ogólnych stawek obniżonych."},
  {name:"Estonia", flag:"🇪🇪", region:"Europa", tax:"KMR", std:24, red:[13,9], note:"Standardowa podniesiona do 24% (lipiec 2025)."},
  {name:"Finlandia", flag:"🇫🇮", region:"Europa", tax:"ALV", std:25.5, red:[13.5,10], note:"Stawka obniżona obcięta do 13,5% (2026)."},
  {name:"Francja", flag:"🇫🇷", region:"Europa", tax:"TVA", std:20, red:[10,5.5,2.1]},
  {name:"Grecja", flag:"🇬🇷", region:"Europa", tax:"FPA", std:24, red:[13,6]},
  {name:"Hiszpania", flag:"🇪🇸", region:"Europa", tax:"IVA", std:21, red:[10,4]},
  {name:"Holandia", flag:"🇳🇱", region:"Europa", tax:"BTW", std:21, red:[9], note:"Noclegi przeniesione na 21% (2026)."},
  {name:"Irlandia", flag:"🇮🇪", region:"Europa", tax:"VAT", std:23, red:[13.5,9,4.8]},
  {name:"Islandia", flag:"🇮🇸", region:"Europa", tax:"VSK", std:24, red:[11]},
  {name:"Liechtenstein", flag:"🇱🇮", region:"Europa", tax:"MWST", std:8.1, red:[3.8,2.6]},
  {name:"Litwa", flag:"🇱🇹", region:"Europa", tax:"PVM", std:21, red:[12,5]},
  {name:"Luksemburg", flag:"🇱🇺", region:"Europa", tax:"TVA", std:17, red:[14,8,3], note:"Najniższa stawka standardowa w UE."},
  {name:"Łotwa", flag:"🇱🇻", region:"Europa", tax:"PVN", std:21, red:[12,5]},
  {name:"Macedonia Północna", flag:"🇲🇰", region:"Europa", std:18, red:[10,5]},
  {name:"Malta", flag:"🇲🇹", region:"Europa", tax:"VAT", std:18, red:[12,7,5]},
  {name:"Mołdawia", flag:"🇲🇩", region:"Europa", std:20, red:[12,8]},
  {name:"Monako", flag:"🇲🇨", region:"Europa", tax:"TVA", std:20, red:[10,5.5,2.1], note:"Stosuje stawki francuskie."},
  {name:"Niemcy", flag:"🇩🇪", region:"Europa", tax:"USt", std:19, red:[7], note:"Posiłki w gastronomii na 7% od 2026."},
  {name:"Norwegia", flag:"🇳🇴", region:"Europa", tax:"MVA", std:25, red:[15,12]},
  {name:"Polska", flag:"🇵🇱", region:"Europa", tax:"VAT", std:23, red:[8,5]},
  {name:"Portugalia", flag:"🇵🇹", region:"Europa", tax:"IVA", std:23, red:[13,6], note:"Inne stawki na Azorach i Maderze."},
  {name:"Rumunia", flag:"🇷🇴", region:"Europa", tax:"TVA", std:21, red:[11], note:"Standardowa 21% (sierpień 2025), jedna obniżona 11%."},
  {name:"Serbia", flag:"🇷🇸", region:"Europa", tax:"PDV", std:20, red:[10]},
  {name:"Słowacja", flag:"🇸🇰", region:"Europa", tax:"DPH", std:23, red:[19,5]},
  {name:"Słowenia", flag:"🇸🇮", region:"Europa", tax:"DDV", std:22, red:[9.5,5]},
  {name:"Szwajcaria", flag:"🇨🇭", region:"Europa", tax:"MWST", std:8.1, red:[3.8,2.6], note:"Jedyne państwo w Europie poniżej minimum UE (15%)."},
  {name:"Szwecja", flag:"🇸🇪", region:"Europa", tax:"MOMS", std:25, red:[12,6]},
  {name:"Ukraina", flag:"🇺🇦", region:"Europa", tax:"PDV", std:20, red:[14,7]},
  {name:"Watykan", flag:"🇻🇦", region:"Europa", std:null, note:"Brak VAT."},
  {name:"Węgry", flag:"🇭🇺", region:"Europa", tax:"AFA", std:27, red:[18,5], note:"Najwyższa stawka standardowa w UE i na świecie."},
  {name:"Wielka Brytania", flag:"🇬🇧", region:"Europa", tax:"VAT", std:20, red:[5]},
  {name:"Włochy", flag:"🇮🇹", region:"Europa", tax:"IVA", std:22, red:[10,5,4]},
  // --- Bliski Wschód ---
  {name:"Arabia Saudyjska", flag:"🇸🇦", region:"Bliski Wschód", std:15, red:[], note:"Podniesiona z 5% do 15% (2020)."},
  {name:"Bahrajn", flag:"🇧🇭", region:"Bliski Wschód", std:10, red:[], note:"Podwojona z 5% do 10% (2022)."},
  {name:"Emiraty Arabskie (ZEA)", flag:"🇦🇪", region:"Bliski Wschód", std:5, red:[]},
  {name:"Iran", flag:"🇮🇷", region:"Bliski Wschód", std:9, red:[]},
  {name:"Izrael", flag:"🇮🇱", region:"Bliski Wschód", std:18, red:[]},
  {name:"Jemen", flag:"🇾🇪", region:"Bliski Wschód", std:5, red:[]},
  {name:"Jordania", flag:"🇯🇴", region:"Bliski Wschód", std:16, red:[]},
  {name:"Katar", flag:"🇶🇦", region:"Bliski Wschód", std:null, note:"Brak VAT (planowany)."},
  {name:"Kuwejt", flag:"🇰🇼", region:"Bliski Wschód", std:null, note:"Brak VAT (planowany)."},
  {name:"Liban", flag:"🇱🇧", region:"Bliski Wschód", std:11, red:[]},
  {name:"Oman", flag:"🇴🇲", region:"Bliski Wschód", std:5, red:[]},
  {name:"Syria", flag:"🇸🇾", region:"Bliski Wschód", std:null, note:"Brak VAT."},
  {name:"Turcja", flag:"🇹🇷", region:"Bliski Wschód", tax:"KDV", std:20, red:[10,1]},
  // --- Azja i Oceania ---
  {name:"Armenia", flag:"🇦🇲", region:"Azja i Oceania", std:20, red:[]},
  {name:"Australia", flag:"🇦🇺", region:"Azja i Oceania", tax:"GST", std:10, red:[], note:"GST, bez stawek obniżonych (podstawowa żywność 0%)."},
  {name:"Azerbejdżan", flag:"🇦🇿", region:"Azja i Oceania", std:18, red:[]},
  {name:"Bangladesz", flag:"🇧🇩", region:"Azja i Oceania", std:15, red:[10,7.5,5]},
  {name:"Bhutan", flag:"🇧🇹", region:"Azja i Oceania", std:7, red:[], note:"GST wprowadzony w 2025/2026."},
  {name:"Chiny", flag:"🇨🇳", region:"Azja i Oceania", tax:"VAT", std:13, red:[9,6], note:"Mali podatnicy 3%."},
  {name:"Fidżi", flag:"🇫🇯", region:"Azja i Oceania", std:9, red:[]},
  {name:"Filipiny", flag:"🇵🇭", region:"Azja i Oceania", std:12, red:[]},
  {name:"Gruzja", flag:"🇬🇪", region:"Azja i Oceania", std:18, red:[]},
  {name:"Hongkong", flag:"🇭🇰", region:"Azja i Oceania", std:null, note:"Brak VAT/GST."},
  {name:"Indie", flag:"🇮🇳", region:"Azja i Oceania", tax:"GST", std:18, red:[12,5,3], note:"GST, główne progi 5/12/18/28%."},
  {name:"Indonezja", flag:"🇮🇩", region:"Azja i Oceania", std:11, red:[]},
  {name:"Japonia", flag:"🇯🇵", region:"Azja i Oceania", tax:"JCT", std:10, red:[8], note:"8% m.in. na żywność i prasę."},
  {name:"Kambodża", flag:"🇰🇭", region:"Azja i Oceania", std:10, red:[]},
  {name:"Kazachstan", flag:"🇰🇿", region:"Azja i Oceania", std:16, red:[], note:"Podniesiona do 16% (2026)."},
  {name:"Korea Południowa", flag:"🇰🇷", region:"Azja i Oceania", std:10, red:[]},
  {name:"Laos", flag:"🇱🇦", region:"Azja i Oceania", std:10, red:[]},
  {name:"Malezja", flag:"🇲🇾", region:"Azja i Oceania", std:null, note:"Brak VAT — podatek SST (sprzedaż/usługi)."},
  {name:"Mongolia", flag:"🇲🇳", region:"Azja i Oceania", std:10, red:[]},
  {name:"Nepal", flag:"🇳🇵", region:"Azja i Oceania", std:13, red:[]},
  {name:"Nowa Zelandia", flag:"🇳🇿", region:"Azja i Oceania", tax:"GST", std:15, red:[]},
  {name:"Pakistan", flag:"🇵🇰", region:"Azja i Oceania", std:null, note:"Brak jednolitego VAT — sales tax 18% na towary."},
  {name:"Papua-Nowa Gwinea", flag:"🇵🇬", region:"Azja i Oceania", std:10, red:[]},
  {name:"Rosja", flag:"🇷🇺", region:"Azja i Oceania", std:22, red:[10], note:"Podniesiona do 22% (2026)."},
  {name:"Singapur", flag:"🇸🇬", region:"Azja i Oceania", tax:"GST", std:9, red:[], note:"Podniesiona z 7% do 9%."},
  {name:"Sri Lanka", flag:"🇱🇰", region:"Azja i Oceania", std:18, red:[]},
  {name:"Tadżykistan", flag:"🇹🇯", region:"Azja i Oceania", std:14, red:[10,7,5]},
  {name:"Tajlandia", flag:"🇹🇭", region:"Azja i Oceania", std:7, red:[], note:"Ustawowo 10%, czasowo 7%."},
  {name:"Tajwan", flag:"🇹🇼", region:"Azja i Oceania", std:5, red:[]},
  {name:"Uzbekistan", flag:"🇺🇿", region:"Azja i Oceania", std:12, red:[]},
  {name:"Vanuatu", flag:"🇻🇺", region:"Azja i Oceania", std:15, red:[]},
  {name:"Wietnam", flag:"🇻🇳", region:"Azja i Oceania", std:10, red:[5], note:"Czasowo obniżona do 8% (przedłużona na 2026)."},
  // --- Afryka ---
  {name:"Algieria", flag:"🇩🇿", region:"Afryka", std:19, red:[9]},
  {name:"Angola", flag:"🇦🇴", region:"Afryka", std:14, red:[]},
  {name:"Botswana", flag:"🇧🇼", region:"Afryka", std:14, red:[]},
  {name:"Egipt", flag:"🇪🇬", region:"Afryka", std:14, red:[5]},
  {name:"Etiopia", flag:"🇪🇹", region:"Afryka", std:15, red:[]},
  {name:"Gabon", flag:"🇬🇦", region:"Afryka", std:18, red:[]},
  {name:"Ghana", flag:"🇬🇭", region:"Afryka", std:15, red:[], note:"Plus dodatkowe opłaty, efektywnie ~20%."},
  {name:"Kamerun", flag:"🇨🇲", region:"Afryka", std:19.25, red:[]},
  {name:"Kenia", flag:"🇰🇪", region:"Afryka", std:16, red:[]},
  {name:"Maroko", flag:"🇲🇦", region:"Afryka", std:20, red:[14,10,7]},
  {name:"Mauritius", flag:"🇲🇺", region:"Afryka", std:15, red:[]},
  {name:"Mozambik", flag:"🇲🇿", region:"Afryka", std:16, red:[]},
  {name:"Namibia", flag:"🇳🇦", region:"Afryka", std:15, red:[]},
  {name:"Nigeria", flag:"🇳🇬", region:"Afryka", std:7.5, red:[]},
  {name:"RPA", flag:"🇿🇦", region:"Afryka", std:15, red:[]},
  {name:"Rwanda", flag:"🇷🇼", region:"Afryka", std:18, red:[]},
  {name:"Senegal", flag:"🇸🇳", region:"Afryka", std:18, red:[]},
  {name:"Tanzania", flag:"🇹🇿", region:"Afryka", std:18, red:[]},
  {name:"Tunezja", flag:"🇹🇳", region:"Afryka", std:19, red:[13,7]},
  {name:"Uganda", flag:"🇺🇬", region:"Afryka", std:18, red:[]},
  {name:"Wybrzeże Kości Słoniowej", flag:"🇨🇮", region:"Afryka", std:18, red:[]},
  {name:"Zambia", flag:"🇿🇲", region:"Afryka", std:16, red:[]},
  {name:"Zimbabwe", flag:"🇿🇼", region:"Afryka", std:15, red:[], note:"Podniesiona do 15% (2026)."},
  // --- Ameryki ---
  {name:"Argentyna", flag:"🇦🇷", region:"Ameryki", std:21, red:[10.5]},
  {name:"Barbados", flag:"🇧🇧", region:"Ameryki", std:17.5, red:[10]},
  {name:"Belize", flag:"🇧🇿", region:"Ameryki", std:12.5, red:[]},
  {name:"Boliwia", flag:"🇧🇴", region:"Ameryki", std:13, red:[]},
  {name:"Brazylia", flag:"🇧🇷", region:"Ameryki", std:18, red:[], note:"ICMS stanowy, zwykle 17-20%; złożony system wielopodatkowy."},
  {name:"Chile", flag:"🇨🇱", region:"Ameryki", std:19, red:[]},
  {name:"Dominikana", flag:"🇩🇴", region:"Ameryki", std:18, red:[]},
  {name:"Ekwador", flag:"🇪🇨", region:"Ameryki", std:15, red:[]},
  {name:"Gwatemala", flag:"🇬🇹", region:"Ameryki", std:12, red:[]},
  {name:"Honduras", flag:"🇭🇳", region:"Ameryki", std:15, red:[]},
  {name:"Jamajka", flag:"🇯🇲", region:"Ameryki", std:15, red:[]},
  {name:"Kanada", flag:"🇨🇦", region:"Ameryki", tax:"GST", std:5, red:[], note:"GST 5% + podatki prowincji (HST/PST), łącznie zwykle 12-15%."},
  {name:"Kolumbia", flag:"🇨🇴", region:"Ameryki", std:19, red:[5]},
  {name:"Kostaryka", flag:"🇨🇷", region:"Ameryki", std:13, red:[4,2,1]},
  {name:"Meksyk", flag:"🇲🇽", region:"Ameryki", std:16, red:[], note:"8% w strefach przygranicznych."},
  {name:"Nikaragua", flag:"🇳🇮", region:"Ameryki", std:15, red:[]},
  {name:"Panama", flag:"🇵🇦", region:"Ameryki", std:7, red:[], note:"Najniższa stawka w obu Amerykach."},
  {name:"Paragwaj", flag:"🇵🇾", region:"Ameryki", std:10, red:[5]},
  {name:"Peru", flag:"🇵🇪", region:"Ameryki", std:18, red:[]},
  {name:"Salwador", flag:"🇸🇻", region:"Ameryki", std:13, red:[]},
  {name:"Stany Zjednoczone (USA)", flag:"🇺🇸", region:"Ameryki", tax:"sales tax", std:null, note:"Brak VAT — podatek od sprzedaży (sales tax) różny stanowo, średnio ~7,5%."},
  {name:"Trynidad i Tobago", flag:"🇹🇹", region:"Ameryki", std:12.5, red:[]},
  {name:"Urugwaj", flag:"🇺🇾", region:"Ameryki", std:22, red:[10]},
  {name:"Wenezuela", flag:"🇻🇪", region:"Ameryki", std:16, red:[8]},
];
const VAT_REGIONS=["Europa","Bliski Wschód","Azja i Oceania","Afryka","Ameryki"];
const VAT_COL={"23%":"#8a2e2a","8%":"#b07c2a","5%":"#2a7a4a","0%":"#2a6a7a","zw":"#6b4a8a"};

// === Weryfikator stawki VAT (Polska) — heurystyka slowo -> stawka ===
const VAT_RULES=[
  // 23% wyjatki bijace nizsze stawki
  {rate:"23%", cat:"napoje", kw:["kawa","herbata","napoj","napoje","sok","soki","energetyk","cola","lemoniad","slodzon"], note:"Napoje (także w gastronomii) są zwykle 23%, mimo że samo jedzenie w lokalu bywa 8%."},
  {rate:"23%", cat:"alkohol", kw:["alkohol","piwo","wino","wodka","whisky","drink","szampan","cydr","nalewk","trunek"], note:"Napoje alkoholowe — zawsze 23%, także podawane w lokalu."},
  {rate:"23%", cat:"wyroby tytoniowe", kw:["papieros","tyton","tytoniow","cygar","nikotyn","podgrzewacz"], note:"Wyroby tytoniowe i nikotynowe — 23%."},
  {rate:"23%", cat:"produkt luksusowy", kw:["lod","kawior","homar","ostryg","skorupiak"], note:"Lody oraz wybrane produkty luksusowe — 23% (mimo że to żywność)."},
  // 0%
  {rate:"0%", cat:"eksport / WDT", kw:["eksport","wdt","wewnatrzwspolnotow","transport miedzynarodow"], note:"0% dotyczy eksportu poza UE, WDT (z ważnym VAT-UE) i transportu międzynarodowego — pod warunkami."},
  // zwolnienie
  {rate:"zw", cat:"usługi medyczne", kw:["lekarz","medyczn","lecz","zdrowotn","przychodni","stomatolog","dentyst","fizjoterap","psycholog","psychiatr"], note:"Zwolnione, gdy to profilaktyka/diagnoza/leczenie przez uprawniony podmiot. Medycyna estetyczna i usługi „techniczne” bywają 23%."},
  {rate:"zw", cat:"edukacja", kw:["edukac","nauczani","szkolen","korepetycj","ksztalcen","przedszkol","zlobek"], note:"Usługi edukacyjne/nauczania zwykle zwolnione. Część szkoleń komercyjnych bywa 23%."},
  {rate:"zw", cat:"finanse i ubezpieczenia", kw:["ubezpiecz","kredyt","pozyczk","finansow","bankow"], note:"Usługi finansowe i ubezpieczeniowe — zwolnione z VAT."},
  {rate:"zw", cat:"najem mieszkaniowy", kw:["najem mieszkan","wynajem mieszkan","najem lokalu mieszkaln"], note:"Najem nieruchomości mieszkalnej na cele mieszkaniowe — zwolniony. Najem użytkowy = 23%, krótkoterminowy/turystyczny = 8%."},
  // 5%
  {rate:"5%", cat:"żywność podstawowa", kw:["chleb","pieczyw","bulk","mleko","nabial","jogurt","twarog","serek","maslo","mieso","drob","kurczak","wolowin","wieprzow","ryb","jaja","jajk","warzyw","owoc","maka","kasz","platki","zboz","tluszcz","miod"], note:"Podstawowe, nieprzetworzone produkty spożywcze — 5%. Przetworzone bywają 8%, a luksusowe/alkohol 23%."},
  {rate:"5%", cat:"książki i prasa lokalna", kw:["ksiazk","ebook","audiobook","czasopismo regionaln","prasa lokaln","gazeta lokaln"], note:"Książki (papierowe i elektroniczne), audiobooki oraz prasa regionalna/lokalna i e-prasa — 5%."},
  {rate:"5%", cat:"artykuły dziecięce i higieniczne", kw:["pieluch","pielusz","smoczek","fotelik","niemowl","podpask","tampon","higienicz","menstruac"], note:"Pieluchy, smoczki, foteliki dziecięce, artykuły higieniczne, żywność dla niemowląt — 5%."},
  // 8%
  {rate:"8%", cat:"gastronomia", kw:["restauracj","gastronom","catering","posilek","obiad","kawiarni","pizzeri","jadlodajni"], note:"Usługi gastronomiczne — 8%. Ale napoje (kawa, soki) i alkohol w rachunku — 23%."},
  {rate:"8%", cat:"hotele i noclegi", kw:["hotel","nocleg","pensjonat","hostel","zakwaterowan","apartament"], note:"Usługi hotelarskie i noclegowe — 8%."},
  {rate:"8%", cat:"transport pasażerski", kw:["transport osob","przewoz osob","transport pasazer","bilet","taksowk","komunikacj"], note:"Przewóz osób — 8%. Taksówki na ryczałcie mają szczególną stawkę 4%."},
  {rate:"8%", cat:"usługi fryzjerskie i kosmetyczne", kw:["fryzjer","manicure","pedicure","paznokc","kosmetyczn","depilac"], note:"Usługi fryzjerskie i kosmetyczne (PKWiU 96.02) — 8% od kwietnia 2024. Same kosmetyki jako towar — 23%."},
  {rate:"8%", cat:"leki i wyroby medyczne", kw:["leki","lekarstw","lecznicz","farmaceut","wyrob medyczn","wyroby medyczn","sprzet medyczn","odkaza"], note:"Produkty lecznicze i wyroby medyczne z wykazu — 8%."},
  {rate:"8%", cat:"budownictwo mieszkaniowe", kw:["budownictwo mieszkan","remont mieszkan","budowa domu","mieszkani","termomoderniz","modernizacja budynk"], note:"Budowa/remont w społecznym programie mieszkaniowym (mieszkania do 150 m², domy do 300 m²) — 8%. Poza nim 23%."},
  {rate:"8%", cat:"kultura, sport, rekreacja", kw:["kino","teatr","muzeum","koncert","basen","silown","sport","rekreac","wystaw"], note:"Wstęp na wydarzenia kultury, sportu i rekreacji — 8%."},
  {rate:"8%", cat:"woda, ścieki, odpady", kw:["woda","wodociag","uzdatnian","sciek","kanalizac","smieci","odpad"], note:"Dostawa wody, odprowadzanie ścieków, wywóz odpadów — 8%."},
  {rate:"8%", cat:"rolnictwo i ogród", kw:["nawoz","rolnicz","ogrodnicz","sadzonk","kwiat","roslin"], note:"Towary do produkcji rolnej, nawozy, rośliny i kwiaty cięte — 8%."},
  {rate:"8%", cat:"prasa", kw:["prasa","gazeta","czasopismo","dziennik","tygodnik","miesiecznik"], note:"Wydawnictwa prasowe — zwykle 8% (prasa regionalna/lokalna i e-prasa — 5%)."},
  // 23% jawne
  {rate:"23%", cat:"usługi profesjonalne", kw:["ksiegow","rachunkow","prawn","adwokat","radca","doradz","konsulting","consulting","audyt"], note:"Usługi księgowe, prawne i doradcze — 23% bez wyjątków."},
  {rate:"23%", cat:"IT i marketing", kw:["informatyc","programowani","oprogramowani","software","aplikacj","marketing","reklam"], note:"Usługi IT, programistyczne i marketingowe — 23%."},
  {rate:"23%", cat:"tłumaczenia", kw:["tlumacz","translacj"], note:"Tłumaczenia — zwykle 23%. Wyjątek: nauczanie języków lub zwolnienie podmiotowe (limit 240 000 zł)."},
  {rate:"23%", cat:"magazynowanie", kw:["magazynowani","skladowani"], note:"Usługi magazynowania i składowania — 23%."},
  {rate:"23%", cat:"odzież i obuwie", kw:["odziez","ubran","obuwie","buty","kurtk","spodni","sukienk"], note:"Odzież i obuwie — 23%."},
  {rate:"23%", cat:"elektronika", kw:["elektronik","komputer","laptop","telefon","smartfon","monitor","konsola"], note:"Elektronika i sprzęt RTV/AGD — 23%."},
  {rate:"23%", cat:"motoryzacja", kw:["samochod","auto","pojazd","paliwo","benzyn","opony"], note:"Samochody, paliwo i części — 23%."},
  {rate:"23%", cat:"meble i wyposażenie", kw:["meble","mebel","sofa","lozko","szaf"], note:"Meble i wyposażenie wnętrz — 23%."},
  {rate:"23%", cat:"kosmetyki (towar)", kw:["kosmetyk","perfum","szampon","dezodorant","makijaz"], note:"Kosmetyki jako produkty — 23% (ale usługi kosmetyczne i fryzjerskie — 8%)."},
];
function vatStrip(s){ return String(s).toLowerCase()
  .replace(/[ąàáâ]/g,"a").replace(/[ćč]/g,"c").replace(/[ęèéê]/g,"e").replace(/ł/g,"l")
  .replace(/[ńň]/g,"n").replace(/[óòôö]/g,"o").replace(/[śš]/g,"s").replace(/[źżž]/g,"z").replace(/[üû]/g,"u"); }
function vatVerify(q){
  const full=vatStrip(q);
  const toks=full.split(/[^a-z0-9]+/).filter(Boolean);
  for(const r of VAT_RULES){
    for(let kw of r.kw){
      kw=vatStrip(kw);
      const hit = kw.indexOf(" ")>=0 ? full.indexOf(kw)>=0 : toks.some(t=>t.indexOf(kw)===0);
      if(hit) return r;
    }
  }
  return {rate:"23%", cat:"stawka podstawowa", note:"Większość towarów i usług. Jeśli to żywność, książki albo usługa społeczna/medyczna — sprawdź dokładniej; 23% to wartość domyślna."};
}
// --- Schemat VAT transgraniczny: sprzedaz z PL (out) i zakup do PL (in); kazdy: towar/usluga x firma/konsument x UE/poza ---
const VIES_URL="https://ec.europa.eu/taxation_customs/vies/#/vat-validation";
const OSS_URL="https://www.podatki.gov.pl/vat/wyjasnienia/rejestracja-do-procedury-unijnej-oraz-nieunijnej-oss-i-procedury-importu-ioss-180424/";
const VAT_XB = {
  out: {
    towar: {
      b2b: {
        ue: {tag:"WDT · 0%", col:"#2a6a7a", title:"Wewnątrzwspólnotowa dostawa towarów (WDT)", rate:"0% VAT",
          kto:"Ty wystawiasz 0%. VAT rozlicza nabywca w swoim kraju (mechanizm WDT u Ciebie, WNT u niego) — podatek nie znika, przenosi się na nabywcę.",
          przyklad:"Faktura 10 000 zł → VAT 0 zł. Niemiecki nabywca rozlicza WNT u siebie.",
          warunki:["Nabywca ma ważny numer VAT-UE — sprawdź w bazie VIES","Masz dokumenty potwierdzające wywóz towaru do innego kraju UE","Wykazujesz w informacji podsumowującej VAT-UE"],
          links:[{l:"Sprawdź numer VAT-UE w VIES",u:VIES_URL}],
          podstawa:"art. 13 i 42 ustawy o VAT"},
        poza: {tag:"Eksport · 0%", col:"#2a6a7a", title:"Eksport towarów (poza UE)", rate:"0% VAT",
          kto:"Ty wystawiasz 0%, ale to NIE jest sprzedaż bez podatku: VAT (importowy) i cło płaci nabywca przy odprawie w kraju przeznaczenia. Podatek pobiera kraj docelowy — tyle że od importera, nie od Ciebie.",
          przyklad:"Faktura 10 000 zł → VAT 0 zł. Cło i VAT importowy płaci nabywca w kraju docelowym.",
          warunki:["Masz dokument potwierdzający wywóz poza UE — komunikat IE-599 lub dokument celny","Bez potwierdzenia w terminie stosujesz stawkę krajową, z korektą po otrzymaniu dokumentu"],
          podstawa:"art. 2 pkt 8 i art. 41 ust. 4-11 ustawy o VAT"},
      },
      b2c: {
        ue: {tag:"WSTO · OSS", col:"#b07c2a", title:"Sprzedaż towaru konsumentowi w UE (WSTO)", rate:"zależy od progu 10 000 EUR", threshold:true,
          kto:"Do limitu 10 000 EUR rocznie — polski VAT. Powyżej — VAT kraju konsumenta, rozliczany przez VAT-OSS.",
          przyklad:"Sprzedaż 12 000 zł do Niemiec (powyżej progu) → VAT niemiecki (19%) przez OSS, nie polski.",
          warunki:["Limit 10 000 EUR liczony łącznie dla całej sprzedaży WSTO i usług elektronicznych do konsumentów w UE","Powyżej limitu rejestrujesz się do VAT-OSS i składasz jedną deklarację w Polsce, płacąc stawki krajów nabywców"],
          links:[{l:"Rejestracja OSS (podatki.gov.pl)",u:OSS_URL}],
          podstawa:"art. 22a ustawy o VAT, procedura OSS"},
        poza: {tag:"Eksport · 0%", col:"#2a6a7a", title:"Eksport towaru do konsumenta poza UE", rate:"0% VAT",
          kto:"Ty wystawiasz 0%. Konsument płaci VAT importowy i ewentualnie cło w swoim kraju przy odprawie.",
          przyklad:"Paczka 800 zł do USA → VAT 0 zł. Klient może zapłacić lokalny podatek i cło przy odbiorze.",
          warunki:["Dokument potwierdzający wywóz poza UE","Przy wysyłce do konsumenta sprawdź lokalne progi i obowiązki (np. brytyjski VAT od przesyłek o niskiej wartości)"],
          podstawa:"art. 2 pkt 8 i art. 41 ust. 4-11 ustawy o VAT"},
      },
    },
    usluga: {
      b2b: {
        ue: {tag:"Reverse charge", col:"#6b4a8a", title:"Usługa dla firmy z UE — odwrotne obciążenie", rate:"bez VAT (rozlicza nabywca)",
          kto:"Ty wystawiasz fakturę bez VAT. VAT rozlicza nabywca w swoim kraju (reverse charge).",
          przyklad:"Faktura 5 000 zł → VAT 0 zł, adnotacja reverse charge. Nabywca rozlicza VAT u siebie.",
          warunki:["Miejsce opodatkowania to kraj nabywcy (zasada B2B)","Faktura bez kwoty VAT, z adnotacją: odwrotne obciążenie / reverse charge","Wykazujesz w informacji podsumowującej VAT-UE"],
          links:[{l:"Sprawdź numer VAT-UE w VIES",u:VIES_URL}],
          podstawa:"art. 28b ustawy o VAT"},
        poza: {tag:"Poza PL VAT", col:"#6b4a8a", title:"Usługa dla firmy spoza UE", rate:"bez VAT (poza zakresem PL)",
          kto:"Ty wystawiasz fakturę bez VAT. Podatek rozlicza nabywca wg przepisów swojego kraju.",
          przyklad:"Faktura 5 000 zł → VAT 0 zł. Podatek rozlicza nabywca w swoim kraju.",
          warunki:["Miejsce opodatkowania to kraj nabywcy (państwo trzecie)","Nie wykazujesz w VAT-UE — to nie jest transakcja unijna"],
          podstawa:"art. 28b ustawy o VAT"},
      },
      b2c: {
        ue: {tag:"VAT PL (zasada)", col:"#8a2e2a", title:"Usługa dla konsumenta z UE", rate:"co do zasady VAT polski", threshold:true,
          thrNote:"Próg dotyczy usług elektronicznych, telekomunikacyjnych i nadawczych (TBE). Pozostałe usługi B2C — zawsze polski VAT, bez progu.",
          kto:"Co do zasady Ty naliczasz polski VAT. Wyjątek: usługi elektroniczne (TBE) — patrz próg poniżej.",
          przyklad:"Korepetycje online 300 zł dla Niemca → polski VAT 23%. Ale e-book lub aplikacja (TBE) → reguła progu.",
          warunki:["Zasada: miejsce opodatkowania to siedziba usługodawcy (Polska)","Usługi TBE — VAT kraju konsumenta powyżej progu 10 000 EUR, przez OSS","Wyjątek: nieruchomości — VAT kraju nieruchomości; transport, wstęp na imprezy, gastronomia — odrębne zasady"],
          links:[{l:"Rejestracja OSS (podatki.gov.pl)",u:OSS_URL}],
          podstawa:"art. 28c i 28k ustawy o VAT (wyjątki: art. 28e-28n)"},
        poza: {tag:"Zależy od usługi", col:"#8a2e2a", title:"Usługa dla konsumenta spoza UE", rate:"zasada: VAT polski; częste wyjątki",
          kto:"Zasada to polski VAT, ale wiele usług niematerialnych jest opodatkowanych w kraju konsumenta (wtedy bez polskiego VAT).",
          przyklad:"Doradztwo 2 000 zł dla konsumenta z USA → zwykle bez polskiego VAT (art. 28l).",
          warunki:["Wyjątek: usługi niematerialne (doradcze, prawne, reklamowe, licencje, IT) dla konsumenta spoza UE — opodatkowane w kraju konsumenta","Wyjątek: usługi elektroniczne dla konsumenta spoza UE — poza polskim VAT","Nieruchomości — VAT kraju nieruchomości"],
          podstawa:"art. 28c oraz art. 28l ustawy o VAT"},
      },
    },
  },
  in: {
    towar: {
      b2b: {
        ue: {tag:"WNT", col:"#2a7a4a", title:"Wewnątrzwspólnotowe nabycie towarów (WNT)", rate:"VAT rozliczasz w PL",
          kto:"To Ty (polski nabywca) rozliczasz VAT w Polsce — wykazujesz VAT należny i jednocześnie naliczony. Przy pełnym prawie do odliczenia jest to neutralne.",
          przyklad:"Zakup 8 000 zł od firmy z Niemiec → VAT należny 1 840 zł i naliczony 1 840 zł, neutralnie.",
          warunki:["Dotyczy czynnego podatnika VAT kupującego od firmy z UE","Podajesz sprzedawcy swój numer VAT-UE — wtedy on stosuje 0% (WDT u niego)","VAT należny i naliczony w tej samej deklaracji"],
          links:[{l:"Sprawdź numer VAT-UE w VIES",u:VIES_URL}],
          podstawa:"art. 9 i 86 ustawy o VAT"},
        poza: {tag:"Import towarów", col:"#8a5a2e", title:"Import towarów (spoza UE)", rate:"VAT importowy + cło w PL",
          kto:"To Ty (polski importer) płacisz VAT importowy i cło — przy odprawie celnej albo, w procedurze uproszczonej, w deklaracji VAT.",
          przyklad:"Towar 5 000 zł + cło 200 zł → VAT 23% od 5 200 zł = 1 196 zł (do odliczenia).",
          warunki:["VAT importowy można rozliczyć w deklaracji (art. 33a) zamiast płacić na granicy — szybsza neutralność","Podstawa opodatkowania obejmuje wartość celną powiększoną o cło","Zachowaj dokumenty celne (PZC/SAD)"],
          podstawa:"art. 2 pkt 7 oraz art. 33-33a ustawy o VAT"},
      },
      b2c: {
        ue: {tag:"VAT w cenie", col:"#2a7a4a", title:"Zakup towaru z UE jako osoba prywatna", rate:"VAT już w cenie",
          kto:"Nie masz obowiązków VAT. Sprzedawca dolicza VAT — swojego kraju albo polski przez OSS, jeśli przekroczył próg sprzedaży do Polski.",
          przyklad:"Zakup 500 zł ze sklepu w Niemczech → VAT już w cenie, nic nie dopłacasz.",
          warunki:["Sprzedawca rozlicza WSTO/OSS — Ty płacisz tylko cenę z VAT","Nie składasz żadnej deklaracji"],
          podstawa:"po stronie sprzedawcy: WSTO / OSS"},
        poza: {tag:"Import VAT", col:"#8a5a2e", title:"Import towaru spoza UE jako osoba prywatna", rate:"VAT importowy (+ cło)",
          kto:"Płacisz VAT importowy przy odprawie. Dla przesyłek do 150 EUR sprzedawca lub platforma może pobrać VAT od razu w cenie (procedura IOSS).",
          przyklad:"Paczka z Chin za 200 zł → VAT 23% (46 zł) przy odprawie albo w cenie (IOSS dla przesyłek do 150 EUR).",
          warunki:["Każda przesyłka spoza UE podlega VAT importowemu (zniesiono zwolnienie dla najmniejszych paczek)","Powyżej 150 EUR dochodzi cło","Przy IOSS VAT płacisz w cenie i unikasz formalności na granicy"],
          podstawa:"art. 2 pkt 7 ustawy o VAT; IOSS dla przesyłek do 150 EUR"},
      },
    },
    usluga: {
      b2b: {
        ue: {tag:"Import usług", col:"#6b4a8a", title:"Import usług z UE (odwrotne obciążenie)", rate:"VAT rozliczasz w PL",
          kto:"Zagraniczny usługodawca wystawia fakturę bez VAT, a Ty (polski nabywca) rozliczasz VAT w Polsce — należny i naliczony (zwykle neutralnie).",
          przyklad:"Faktura 4 000 zł od firmy z Irlandii → VAT należny 920 zł i naliczony 920 zł, neutralnie.",
          warunki:["Miejsce opodatkowania to Polska (siedziba nabywcy, zasada B2B)","Wykazujesz VAT należny i naliczony w deklaracji","Transakcję ujmujesz w ewidencji jako import usług"],
          podstawa:"art. 28b oraz art. 17 ust. 1 pkt 4 ustawy o VAT"},
        poza: {tag:"Import usług", col:"#6b4a8a", title:"Import usług spoza UE (odwrotne obciążenie)", rate:"VAT rozliczasz w PL",
          kto:"Usługodawca z państwa trzeciego wystawia fakturę bez VAT, a Ty (polski nabywca) rozliczasz VAT w Polsce — należny i naliczony.",
          przyklad:"Faktura 4 000 zł od firmy z USA → VAT należny 920 zł i naliczony 920 zł.",
          warunki:["Miejsce opodatkowania to Polska (siedziba nabywcy)","Rozliczasz jak import usług (odwrotne obciążenie)","Nie wykazujesz w informacji podsumowującej VAT-UE"],
          podstawa:"art. 28b oraz art. 17 ust. 1 pkt 4 ustawy o VAT"},
      },
      b2c: {
        ue: {tag:"VAT w cenie", col:"#8a2e2a", title:"Usługa z UE jako osoba prywatna", rate:"VAT już w cenie",
          kto:"Nie masz obowiązków VAT. Przy usługach elektronicznych zagraniczny dostawca dolicza polski VAT (przez OSS) — płacisz go w cenie.",
          przyklad:"Subskrypcja streamingu z Irlandii 40 zł/mc → polski VAT 23% już w cenie.",
          warunki:["Usługi elektroniczne (streaming, aplikacje) — dostawca rozlicza polski VAT przez OSS","Inne usługi — VAT zależy od rodzaju, zwykle kraju usługodawcy"],
          podstawa:"art. 28k ustawy o VAT (usługi TBE B2C)"},
        poza: {tag:"VAT w cenie", col:"#8a2e2a", title:"Usługa spoza UE jako osoba prywatna", rate:"VAT w cenie (usługi elektroniczne)",
          kto:"Przy usługach elektronicznych dostawca spoza UE powinien zarejestrować się i doliczyć polski VAT. Płacisz go w cenie, bez własnych formalności.",
          przyklad:"Aplikacja od firmy z USA 50 zł → polski VAT w cenie, jeśli dostawca rozlicza OSS.",
          warunki:["Usługi elektroniczne dla konsumenta z UE — opodatkowane w kraju konsumenta (Polska)","Dostawca spoza UE rozlicza je przez procedurę nieunijną OSS"],
          podstawa:"art. 28k ustawy o VAT"},
      },
    },
  },
};
let xbDir="out", xbT="towar", xbC="b2b", xbW="ue", xbRev=15000;
function vatXbRender(){
  const out=$("#vatXbResult"); if(!out) return;
  const inbound=(xbDir==="in");
  const cl=$("#xbCLbl"); if(cl) cl.textContent=inbound?"Kto kupuje":"Komu";
  const tl=$("#xbTLbl"); if(tl) tl.textContent=inbound?"Co kupujesz":"Co sprzedajesz";
  const wl=$("#xbWLbl"); if(wl) wl.textContent=inbound?"Skąd":"Dokąd";
  const d = inbound ? VAT_XB.in[xbT][xbC][xbW] : VAT_XB.out[xbT][xbC][xbW];
  if(!d) return;
  const thrNote = d.thrNote ? `<div class="vat-thr-note">${esc(d.thrNote)}</div>` : "";
  const extra = d.threshold ? `<div class="vat-xb-thr">${thrNote}
      <label class="vat-thr-lbl" for="xbRev">Twoja roczna sprzedaż do konsumentów w UE (EUR)</label>
      <input id="xbRev" class="vat-input" type="number" inputmode="decimal" min="0" step="any" value="${xbRev}">
      <div id="xbThrOut"></div></div>` : "";
  const links = d.links ? `<div class="vat-xb-links">${d.links.map(x=>`<a class="vat-xb-link" href="${esc(x.u)}" target="_blank" rel="noopener">${esc(x.l)} ↗</a>`).join("")}</div>` : "";
  out.innerHTML=`<div class="vat-xb-res">
    <div class="vat-xb-head"><span class="vat-badge" style="background:${d.col}">${esc(d.tag)}</span><span class="vat-xb-title">${esc(d.title)}</span></div>
    <div class="vat-xb-rate">Stawka / zasada: <b>${esc(d.rate)}</b></div>
    <div class="vat-xb-kto"><b>Kto płaci VAT:</b> ${esc(d.kto)}</div>
    <div class="vat-xb-ex"><b>Przykład:</b> ${esc(d.przyklad)}</div>
    ${extra}
    <ul class="vat-xb-warunki">${d.warunki.map(w=>`<li>${esc(w)}</li>`).join("")}</ul>
    ${links}
    <div class="vat-xb-podst">Podstawa: ${esc(d.podstawa)}</div>
  </div>`;
  if(d.threshold){
    const ri=$("#xbRev");
    if(ri) ri.addEventListener("input",()=>{ xbRev=parseFloat((ri.value||"").replace(",","."))||0; xbThrUpdate(); });
    xbThrUpdate();
  }
}
function xbThrUpdate(){
  const o=$("#xbThrOut"); if(!o) return;
  o.innerHTML = (xbRev>10000)
    ? `<div class="vat-thr-res vat-thr-over"><b>Powyżej 10 000 EUR</b> — naliczasz <b>VAT kraju konsumenta</b> i rozliczasz go przez <b>VAT-OSS</b> (jedna deklaracja w Polsce). Polski VAT już nie wystarcza.</div>`
    : `<div class="vat-thr-res vat-thr-under"><b>Do 10 000 EUR</b> — możesz stosować <b>polski VAT</b> (rejestracja do OSS nie jest konieczna).</div>`;
}
function renderVAT(){
  const box=$("#vatBox"); if(!box || box.dataset.done) return;
  box.dataset.done="1";
  box.innerHTML=`
    <div class="vat-tool">
      <div class="vat-th">Transakcje zagraniczne — jaki VAT?</div>
      <p class="vat-sub">Z perspektywy polskiej strony (firmy lub osoby prywatnej), w obie strony. Rozróżnia transakcje wewnątrz UE i z państwami trzecimi (np. UK, USA).</p>
      <div class="vat-xb-row"><span class="vat-xb-lbl">Kierunek</span><div id="xbDir" class="srcToggle"><button class="srcbtn on" data-xbdir="out">Sprzedaję z Polski</button><button class="srcbtn" data-xbdir="in">Kupuję do Polski</button></div></div>
      <div class="vat-xb-row"><span class="vat-xb-lbl" id="xbTLbl">Co sprzedajesz</span><div id="xbT" class="srcToggle"><button class="srcbtn on" data-xbt="towar">Towar</button><button class="srcbtn" data-xbt="usluga">Usługa</button></div></div>
      <div class="vat-xb-row" id="xbCRow"><span class="vat-xb-lbl" id="xbCLbl">Komu</span><div id="xbC" class="srcToggle"><button class="srcbtn on" data-xbc="b2b">Firma (podatnik)</button><button class="srcbtn" data-xbc="b2c">Konsument</button></div></div>
      <div class="vat-xb-row"><span class="vat-xb-lbl" id="xbWLbl">Dokąd</span><div id="xbW" class="srcToggle"><button class="srcbtn on" data-xbw="ue">Kraj UE</button><button class="srcbtn" data-xbw="poza">Poza UE</button></div></div>
      <div id="vatXbResult"></div>
      <p class="vat-caveat">Schemat liczy VAT <b>po stronie polskiej</b> — co rozliczasz w Polsce. Jeśli masz firmę za granicą i pytasz o jej tamtejszy VAT, to już przepisy tego kraju (tu ich nie wyliczam). Przy wyborze „Firma (podatnik)" zakładam czynnego podatnika VAT. To uproszczone zasady ogólne — są wyjątki (nieruchomości, usługi elektroniczne, transport, montaż na miejscu). Pewność daje WIS lub doradca.</p>
    </div>
    <div class="vat-tool">
      <div class="vat-th">Weryfikator stawki VAT (Polska)</div>
      <p class="vat-sub">Wpisz towar lub usługę, a podpowiem najbardziej prawdopodobną polską stawkę. To wskazówka, nie wiążąca interpretacja.</p>
      <input id="vatQ" class="vat-input" type="text" placeholder="np. książka, nocleg, usługi księgowe, chleb, kawa…" autocomplete="off">
      <div class="vat-chips" id="vatExamples"></div>
      <div id="vatResult"></div>
      <p class="vat-caveat">Pewność daje <b>WIS</b> (Wiążąca Informacja Stawkowa) — wniosek przez e-Urząd Skarbowy, chroni przy kontroli. Klasyfikację ustala się wg CN/PKWiU.</p>
    </div>
    <div class="vat-tool">
      <div class="vat-th">Stawki VAT według państwa</div>
      <p class="vat-sub">Cały świat — ponad 130 państw pogrupowanych regionami. Stawka standardowa i obniżone. Stan: styczeń 2026 (orientacyjnie).</p>
      <select id="vatCountry" class="vat-input"></select>
      <div id="vatCountryResult"></div>
    </div>`;
  const ex=["książka","nocleg w hotelu","usługi księgowe","chleb","kawa","fryzjer","leki","eksport"];
  $("#vatExamples").innerHTML=ex.map(e=>`<button class="vat-chip" data-vex="${esc(e)}">${esc(e)}</button>`).join("");
  $("#vatExamples").querySelectorAll("[data-vex]").forEach(b=>b.onclick=()=>{ $("#vatQ").value=b.dataset.vex; vatShowResult(); });
  $("#vatQ").addEventListener("input", vatShowResult);
  const sel=$("#vatCountry");
  sel.innerHTML=VAT_REGIONS.map(rg=>{
    const opts=VAT_COUNTRIES.map((c,i)=>c.region===rg?`<option value="${i}">${c.flag} ${esc(c.name)}</option>`:"").join("");
    return `<optgroup label="${esc(rg)}">${opts}</optgroup>`;
  }).join("");
  const pl=VAT_COUNTRIES.findIndex(c=>c.name==="Polska");
  sel.value=pl>=0?pl:0;
  sel.addEventListener("change", vatShowCountry);
  vatShowCountry();
  const xbWire=(id,attr,set)=>{ document.querySelectorAll(`#${id} .srcbtn`).forEach(b=>b.onclick=()=>{
    set(b.dataset[attr]); document.querySelectorAll(`#${id} .srcbtn`).forEach(x=>x.classList.toggle("on",x===b)); vatXbRender(); }); };
  xbWire("xbDir","xbdir",v=>xbDir=v);
  xbWire("xbT","xbt",v=>xbT=v);
  xbWire("xbC","xbc",v=>xbC=v);
  xbWire("xbW","xbw",v=>xbW=v);
  vatXbRender();
}
function vatShowResult(){
  const out=$("#vatResult"); if(!out) return;
  const q=($("#vatQ").value||"").trim();
  if(q.length<2){ out.innerHTML=""; return; }
  const r=vatVerify(q), col=VAT_COL[r.rate]||"#555";
  out.innerHTML=`<div class="vat-res">
    <div class="vat-badge" style="background:${col}">${esc(r.rate)}</div>
    <div class="vat-resmain"><div class="vat-rescat">${esc(r.cat)}</div><div class="vat-resnote">${esc(r.note)}</div></div>
  </div>`;
}
function vatShowCountry(){
  const sel=$("#vatCountry"), out=$("#vatCountryResult"); if(!sel||!out) return;
  const c=VAT_COUNTRIES[+sel.value]; if(!c) return;
  const fmt=x=>String(x).replace(".",",")+"%";
  const stdDisp=(c.std==null)?"brak VAT":fmt(c.std);
  const reduced=(c.std==null)?`<span class="vat-rnone">podatek od sprzedaży zamiast VAT</span>`
    :((c.red&&c.red.length)?c.red.map(x=>`<span class="vat-rchip">${fmt(x)}</span>`).join(""):`<span class="vat-rnone">brak stawek obniżonych</span>`);
  out.innerHTML=`<div class="vat-country">
    <div class="vat-cstd"><span class="vat-cstd-lbl">Stawka standardowa</span><span class="vat-cstd-val" style="color:${(c.std==null)?'#888':'#8a2e2a'}">${esc(stdDisp)}</span>${c.tax?`<span class="vat-cstd-tax">${esc(c.tax)}</span>`:""}</div>
    <div class="vat-cred"><span class="vat-cred-lbl">Stawki obniżone</span><div class="vat-cred-row">${reduced}</div></div>
    ${c.note?`<div class="vat-cnote">${esc(c.note)}</div>`:""}
  </div>`;
}
function renderStawki(){
  const sb=$("#sciagawkaBox");
  if(sb && !sb.dataset.done){
    sb.dataset.done="1";
    sb.innerHTML=`<div class="sci-head" style="margin-top:6px">Ściągawka — stan na 2026</div>
    <div class="scigrid">
      <div class="sci"><span class="sl">Zwolnienie podmiotowe VAT</span><span class="sv">240 000 zł / rok</span><span class="sn">od 1.01.2026 (wcześniej 200 000 zł)</span></div>
      <div class="sci"><span class="sl">Limit ryczałtu</span><span class="sv">2 000 000 EUR</span><span class="sn">przychód za rok poprzedni · kwartalnie do 200 000 EUR</span></div>
      <div class="sci"><span class="sl">Dieta krajowa</span><span class="sv">45 zł / dobę</span><span class="sn">8–12 h = 50% · powyżej 12 h = 100%</span></div>
      <div class="sci"><span class="sl">Kilometrówka</span><span class="sv">0,89 / 1,15 zł/km</span><span class="sn">≤900 cm³ / powyżej 900 cm³ · motocykl 0,69 · motorower 0,42</span></div>
      <div class="sci"><span class="sl">Skala podatkowa</span><span class="sv">12% / 32%</span><span class="sn">próg 120 000 zł · kwota wolna 30 000 zł</span></div>
      <div class="sci"><span class="sl">Podatek liniowy</span><span class="sv">19%</span><span class="sn">+ danina solidarnościowa 4% powyżej 1 mln zł dochodu</span></div>
    </div>
    <p class="livehint" style="margin-top:8px">⚠ Wartości zmieniają się co roku (zwłaszcza kwoty ZUS i progi) — przed użyciem sprawdź aktualność u źródła. To skrót poglądowy, nie porada podatkowa.</p>`;
  }
  renderSciagaCustom();
  renderZus();
  return 0;
}
function renderZus(){
  const box=$("#zusBox"); if(!box || box.dataset.done) return;
  box.dataset.done="1";
  box.innerHTML=`<div class="sci-head" style="margin-top:6px">Składki ZUS — stawki i schematy</div>
    <div class="scigrid">
      <div class="sci"><span class="sl">Emerytalna</span><span class="sv">19,52%</span><span class="sn">podstawy wymiaru</span></div>
      <div class="sci"><span class="sl">Rentowa</span><span class="sv">8,00%</span><span class="sn">podstawy wymiaru</span></div>
      <div class="sci"><span class="sl">Chorobowa</span><span class="sv">2,45%</span><span class="sn">dobrowolna dla przedsiębiorcy</span></div>
      <div class="sci"><span class="sl">Wypadkowa</span><span class="sv">~1,67%</span><span class="sn">typowa; zależy od kodu PKD/zgłoszonych</span></div>
      <div class="sci"><span class="sl">Fundusz Pracy</span><span class="sv">2,45%</span><span class="sn">gdy podstawa ≥ minimalnego wynagrodzenia</span></div>
      <div class="sci"><span class="sl">Zdrowotna — skala</span><span class="sv">9%</span><span class="sn">od dochodu (min. roczna 5 190,48 zł)</span></div>
      <div class="sci"><span class="sl">Zdrowotna — liniowy</span><span class="sv">4,9%</span><span class="sn">od dochodu (min. roczna 5 190,48 zł)</span></div>
      <div class="sci"><span class="sl">Zdrowotna — ryczałt</span><span class="sv">3 progi</span><span class="sn">wg przychodu: do 60 tys. / do 300 tys. / powyżej</span></div>
    </div>
    <div class="sci-head" style="margin-top:22px">Schematy składek społecznych (JDG)</div>
    <div class="zus-schemes">
      <div class="zus-s"><b>Ulga na start</b><span>Pierwsze <b>6 miesięcy</b> bez składek społecznych (płacisz tylko zdrowotną). Dobrowolna.</span></div>
      <div class="zus-s"><b>Preferencyjny ZUS (mały ZUS)</b><span>Kolejne <b>24 miesiące</b> — podstawa = 30% minimalnego wynagrodzenia (niższe składki społeczne).</span></div>
      <div class="zus-s"><b>Mały ZUS Plus</b><span>Składki <b>zależne od dochodu</b> z poprzedniego roku. Limit przychodu i max <b>36 miesięcy</b> w okresie 60 miesięcy. Nie łączy się z ulgą na start/preferencyjnym w tym samym czasie.</span></div>
      <div class="zus-s"><b>Pełny ZUS</b><span>Podstawa standardowa (60% prognozowanego przeciętnego wynagrodzenia) — gdy nie przysługują/wyczerpią się ulgi.</span></div>
    </div>
    <p class="livehint" style="margin-top:10px">⚠ Konkretne <b>kwoty</b> składek zmieniają się co roku — tu pokazuję stawki procentowe i zasady. Wybór schematu i kwoty potwierdź w ZUS / u księgowego.</p>`;
}
function sciagaSave(){ try{ localStorage.setItem("paragraf-sciaga", JSON.stringify(state.sciaga)); }catch(_){} }
function renderSciagaCustom(){
  const box=$("#sciagaCustom"); if(!box) return;
  const items=(state.sciaga||[]).map((it,i)=>`<div class="sci sci-mine"><button class="sci-del" data-sdel="${i}" title="Usuń">×</button><span class="sl">${esc(it.n)}</span><span class="sv">${esc(it.v||"")}</span>${it.note?`<span class="sn">${esc(it.note)}</span>`:""}</div>`).join("");
  box.innerHTML=`<div class="sci-head" style="margin-top:22px">Twoje pozycje</div>
    <div class="sciadd">
      <input id="sciN" class="search" type="text" placeholder="Nazwa (np. Mój limit faktury)" autocomplete="off">
      <input id="sciV" class="search" type="text" placeholder="Wartość (np. 5 000 zł)" autocomplete="off">
      <button class="livebtn" id="sciAdd">Dodaj</button>
    </div>
    ${items?`<div class="scigrid" style="margin-top:11px">${items}</div>`:`<p class="livehint" style="margin-top:6px">Dodaj własne stawki/limity/notatki — zapisują się w tej przeglądarce.</p>`}`;
  const add=()=>{ const n=($("#sciN").value||"").trim(), v=($("#sciV").value||"").trim();
    if(!n){ $("#sciN").focus(); return; } state.sciaga.push({n,v}); sciagaSave(); renderSciagaCustom(); };
  const ab=$("#sciAdd"); if(ab) ab.onclick=add;
  const nI=$("#sciN"); if(nI) nI.addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); add(); } });
  const vI=$("#sciV"); if(vI) vI.addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); add(); } });
  box.querySelectorAll("[data-sdel]").forEach(b=>b.onclick=()=>{ state.sciaga.splice(+b.dataset.sdel,1); sciagaSave(); renderSciagaCustom(); });
}

// ===== KALKULATOR obciazen JDG (liniowy / ryczalt / skala) =====
// Stale 2026 i formuly odwzorowane 1:1 ze sprawdzonego arkusza.
const HEALTH_MIN_2026 = 5190.48;   // minimalna roczna skladka zdrowotna (liniowy/skala)
function plPLN(v){ return (isFinite(v)?v:0).toLocaleString("pl-PL",{minimumFractionDigits:2,maximumFractionDigits:2})+" zł"; }
function plPct(v){ return (v*100).toLocaleString("pl-PL",{minimumFractionDigits:1,maximumFractionDigits:1})+"%"; }
function kalkNum(id){ const el=$(id); if(!el) return 0; const v=parseFloat((el.value||"").replace(",", ".")); return isFinite(v)?v:0; }
function calcForms(P,K,ZUS,r){
  const D=P-K, POD=D-ZUS;
  // LINIOWY 19%
  const zdrowLin=Math.max(0.049*(D-ZUS), HEALTH_MIN_2026);
  const dedLin=Math.min(zdrowLin,14100);
  const pitLin=Math.max(Math.round((P-K-ZUS-dedLin)*0.19),0);
  const danLin=(POD-dedLin)>1000000?((POD-dedLin-1000000)*0.04):0;
  const sumaLin=pitLin+ZUS+zdrowLin+danLin;
  // RYCZALT (jedna stawka na calosc przychodu)
  const ryczOK = P<=8517200;
  const zdrowR=(P-ZUS+1661.64>300000?1495.04:(P-ZUS+1661.64>60000?830.58:498.35))*12;
  const zusDed=Math.max(ZUS-1661.64,0);
  const pitR=Math.max((P-zusDed-0.5*zdrowR)*r,0);
  const sumaR=pitR+zdrowR+ZUS;
  // SKALA
  const zdrowS=Math.max(0.09*(D-ZUS), HEALTH_MIN_2026);
  const pitS=Math.max((POD>0?(Math.min(POD,120000)*0.12+Math.max(POD-120000,0)*0.32-3600):0),0);
  const danS=POD>1000000?((POD-1000000)*0.04):0;
  const sumaS=pitS+ZUS+zdrowS+danS;
  const F=(pit,zdrow,danina,suma)=>({pit,zdrow,zus:ZUS,danina,suma,netto:D-suma,stopa:D?suma/D:null});
  return { D, lin:F(pitLin,zdrowLin,danLin,sumaLin), rycz:ryczOK?F(pitR,zdrowR,0,sumaR):null, sk:F(pitS,zdrowS,danS,sumaS) };
}
// --- schematy ZUS 2026 (skladki spoleczne liczone automatycznie) ---
const ZUS_BASE_FULL=5652, ZUS_BASE_PREF=1441.80, MIN_WAGE_26=4806, MZP_LIMIT=120000;
function zusFromBase(base, chor){
  const r2=x=>Math.round(x*100)/100;
  let m = r2(base*0.1952) + r2(base*0.08) + r2(base*0.0167);  // emerytalna + rentowa + wypadkowa
  if(base>=MIN_WAGE_26-0.005) m += r2(base*0.0245);            // Fundusz Pracy gdy podstawa >= min. wynagrodzenia
  if(chor) m += r2(base*0.0245);                               // chorobowe (dobrowolne)
  return r2(m)*12;
}
function zusForScheme(scheme, P, K, chor){
  if(scheme==="ulga") return 0;
  if(scheme==="pref") return zusFromBase(ZUS_BASE_PREF, chor);
  if(scheme==="plus"){
    const D=Math.max(P-K,0);
    const base=Math.min(Math.max((D/365)*30*0.5, ZUS_BASE_PREF), ZUS_BASE_FULL);
    return zusFromBase(base, chor);
  }
  return zusFromBase(ZUS_BASE_FULL, chor);      // pelny
}
const ZUS_LABEL={pelny:"Pełny ZUS", pref:"Preferencyjny", plus:"Mały ZUS Plus", ulga:"Ulga na start"};
const ZUS_NOTE={
  pelny:"Podstawa 5 652 zł (60% przeciętnego), z Funduszem Pracy.",
  pref:"Podstawa 1 441,80 zł (30% min. wynagrodzenia), przez pierwsze 24 mies., bez Funduszu Pracy.",
  plus:"Szacunkowo — podstawa zależy od dochodu z poprzedniego roku, w przedziale 1 441,80–5 652 zł.",
  ulga:"Pierwsze 6 mies. bez składek społecznych — tu liczone jako brak składek dla całego roku.",
};
function renderKalk(){
  const box=$("#kalkResults"); if(!box) return 0;
  if(!box.dataset.wired){
    box.dataset.wired="1";
    ["#kalkP","#kalkK"].forEach(id=>{ const el=$(id); if(el) el.addEventListener("input",renderKalk); });
    ["#kalkR","#kalkZus","#kalkChor"].forEach(id=>{ const el=$(id); if(el) el.addEventListener("change",renderKalk); });
  }
  const P=kalkNum("#kalkP"), K=kalkNum("#kalkK");
  const r=parseFloat(($("#kalkR")||{}).value||"0.085");
  const chor=!!(($("#kalkChor")||{}).checked);
  // blokada Malego ZUS Plus po przekroczeniu limitu przychodu
  const plusLocked = P>MZP_LIMIT;
  const sel=$("#kalkZus");
  if(sel){
    const opt=sel.querySelector('option[value="plus"]');
    if(opt){ opt.disabled=plusLocked; opt.textContent = plusLocked?"Mały ZUS Plus (limit przekroczony)":"Mały ZUS Plus"; }
    if(plusLocked && sel.value==="plus") sel.value="pelny";
  }
  const scheme=(sel&&sel.value)||"pelny";
  const ZUS=zusForScheme(scheme,P,K,chor);
  const res=calcForms(P,K,ZUS,r);
  const forms=[["Liniowy 19%",res.lin],["Ryczałt "+plPct(r),res.rycz],["Skala podatkowa",res.sk]];
  let best=null; forms.forEach(([_,f])=>{ if(f && (best===null||f.suma<best)) best=f.suma; });
  const card=(name,f)=>{
    if(!f) return `<div class="kc kc-off"><div class="kc-h">${esc(name)}</div><div class="kc-na">Niedostępny — przychód powyżej limitu 2 mln EUR.</div></div>`;
    const isBest=best!==null && Math.abs(f.suma-best)<0.005;
    const rows=[["PIT",f.pit],["Składka zdrowotna",f.zdrow],["ZUS społeczny",f.zus]];
    if(f.danina>0) rows.push(["Danina solidarn.",f.danina]);
    const rh=rows.map(x=>`<div class="kr"><span>${x[0]}</span><b>${plPLN(x[1])}</b></div>`).join("");
    return `<div class="kc${isBest?' kc-best':''}">${isBest?'<div class="kc-badge">najkorzystniej</div>':''}
      <div class="kc-h">${esc(name)}</div>${rh}
      <div class="kr kr-sum"><span>Suma obciążeń</span><b>${plPLN(f.suma)}</b></div>
      <div class="kr"><span>Efektywna stopa</span><b>${f.stopa==null?"n/d":plPct(f.stopa)}</b></div>
      <div class="kr kr-net"><span>Zostaje (rok)</span><b>${plPLN(f.netto)}</b></div>
      <div class="kr"><span>Zostaje (mies.)</span><b>${plPLN(f.netto/12)}</b></div>
    </div>`;
  };
  const lockNote = plusLocked ? `<div class="kc-note">⚠ Mały ZUS Plus niedostępny — przychód przekracza limit ${plPLN(MZP_LIMIT)} z poprzedniego roku.</div>` : "";
  box.innerHTML =
      `<div class="kc-zus">ZUS społeczny — <b>${esc(ZUS_LABEL[scheme])}</b>${chor?" + chorobowe":""}: <b>${plPLN(ZUS)}</b> / rok · ${plPLN(ZUS/12)} / mies. <span class="kc-zn">${esc(ZUS_NOTE[scheme])}</span></div>`
    + lockNote
    + `<div class="kc-dochod">Dochód (przychód − koszty): <b>${plPLN(res.D)}</b></div>`
    + `<div class="kgrid">${forms.map(f=>card(f[0],f[1])).join("")}</div>`;
  return 0;
}

// --- kalkulator sp. z o.o.: klasyczna (CIT) vs estonski CIT (wzory z arkusza) ---
function calcSpzoo(P,K,powolanie,maly,dyw){
  const zysk=Math.max(P-K,0);
  const zyskPoPow=Math.max(zysk-powolanie,0);
  // powolanie: PIT 12% (po odliczeniu 3000+30000) + 9% zdrowotnej od calosci
  const pitPow = Math.max(0,(powolanie-3000-30000)*0.12) + 0.09*powolanie;
  // KLASYCZNA: CIT od zysku nalezny CO ROKU; 19% PIT od dywidendy tylko przy wyplacie
  const citK = zyskPoPow*(maly?0.09:0.19);
  const pitDywK = dyw ? (zyskPoPow-citK)*0.19 : 0;
  const sumaK = citK+pitDywK+pitPow;
  // ESTONSKI: brak podatku do czasu wyplaty; przy wyplacie CIT 10/20% + PIT pomniejszony
  const citE = dyw ? zyskPoPow*(maly?0.10:0.20) : 0;
  const pitDywE = dyw ? Math.max(zyskPoPow*0.19 - (maly?citE*0.9:citE*0.7), 0) : 0;
  const sumaE = citE+pitDywE+pitPow;
  const mk=(cit,pitDyw,suma)=>({cit,pitDyw,pitPow,suma,netto:zysk-suma,stopa:zysk?suma/zysk:null});
  return { zysk, dyw, klas:mk(citK,pitDywK,sumaK), est:mk(citE,pitDywE,sumaE) };
}
function renderSpzoo(){
  const box=$("#spzooResults"); if(!box) return;
  if(!box.dataset.wired){
    box.dataset.wired="1";
    ["#spP","#spK","#spPow"].forEach(id=>{ const el=$(id); if(el) el.addEventListener("input",renderSpzoo); });
    ["#spMaly","#spDyw"].forEach(id=>{ const el=$(id); if(el) el.addEventListener("change",renderSpzoo); });
  }
  const P=kalkNum("#spP"),K=kalkNum("#spK"),pow=kalkNum("#spPow"),maly=!!(($("#spMaly")||{}).checked),dyw=!!(($("#spDyw")||{}).checked);
  const res=calcSpzoo(P,K,pow,maly,dyw);
  const forms=[["Klasyczna (CIT + 19% dywidenda)",res.klas],["Estoński CIT",res.est]];
  let best=null; forms.forEach(([_,f])=>{ if(best===null||f.suma<best) best=f.suma; });
  const card=(name,f)=>{
    const isBest=best!==null && Math.abs(f.suma-best)<0.005;
    const rows=[["CIT",f.cit]];
    if(res.dyw) rows.push(["PIT od dywidendy",f.pitDyw]);
    if(f.pitPow>0) rows.push(["PIT + zdrow. (powołanie)",f.pitPow]);
    const rh=rows.map(x=>`<div class="kr"><span>${x[0]}</span><b>${plPLN(x[1])}</b></div>`).join("");
    return `<div class="kc${isBest?' kc-best':''}">${isBest?'<div class="kc-badge">korzystniej</div>':''}
      <div class="kc-h">${esc(name)}</div>${rh}
      <div class="kr kr-sum"><span>Suma obciążeń</span><b>${plPLN(f.suma)}</b></div>
      <div class="kr"><span>Efektywna stopa</span><b>${f.stopa==null?"n/d":plPct(f.stopa)}</b></div>
      <div class="kr kr-net"><span>Zostaje (rok)</span><b>${plPLN(f.netto)}</b></div>
    </div>`;
  };
  const scen = res.dyw ? "przy pełnej wypłacie zysku" : "zysk zatrzymany w spółce — bez wypłaty dywidendy";
  box.innerHTML=`<div class="kc-dochod">Zysk spółki (przychód − koszty): <b>${plPLN(res.zysk)}</b> · ${esc(scen)}</div>`
    +`<div class="kgrid">${forms.map(f=>card(f[0],f[1])).join("")}</div>`;
}

// ===== Kalkulatory dodatkowe: etat, JDG+etat, spolka osobowa, komandytowa, laczne =====
function kalkRows(rows){ return rows.map(x=>`<div class="kr"><span>${x[0]}</span><b>${plPLN(x[1])}</b></div>`).join(""); }
function jdgCard(name,f,best){
  if(!f) return `<div class="kc kc-off"><div class="kc-h">${esc(name)}</div><div class="kc-na">Niedostępny — przychód powyżej limitu 2 mln EUR.</div></div>`;
  const isBest=best!==null && Math.abs(f.suma-best)<0.005;
  const rows=[["PIT",f.pit],["Składka zdrowotna",f.zdrow],["ZUS społeczny",f.zus]];
  if(f.danina>0) rows.push(["Danina solidarn.",f.danina]);
  return `<div class="kc${isBest?' kc-best':''}">${isBest?'<div class="kc-badge">najkorzystniej</div>':''}
    <div class="kc-h">${esc(name)}</div>${kalkRows(rows)}
    <div class="kr kr-sum"><span>Suma obciążeń</span><b>${plPLN(f.suma)}</b></div>
    <div class="kr"><span>Efektywna stopa</span><b>${f.stopa==null?"n/d":plPct(f.stopa)}</b></div>
    <div class="kr kr-net"><span>Zostaje (rok)</span><b>${plPLN(f.netto)}</b></div>
    <div class="kr"><span>Zostaje (mies.)</span><b>${plPLN(f.netto/12)}</b></div>
  </div>`;
}
const ZUS_CAP_2026 = 282600;  // 30-krotnosc przecietnego wynagrodzenia (limit emerytalnej+rentowej)
function uopZus(P){ const base=Math.min(P,ZUS_CAP_2026); return base*0.0976 + base*0.015 + P*0.0245; }
function skalaPIT(pod){ return Math.max(pod>0?(Math.min(pod,120000)*0.12+Math.max(pod-120000,0)*0.32-3600):0,0); }

// --- Etat (umowa o prace) ---
function calcEtat(mg,kup){
  const P=mg*12;
  const zus=uopZus(P);
  const doch=P-kup, pod=doch-zus;
  const pit=skalaPIT(pod);
  const zdrow=0.09*(P-zus);
  const danina=pod>1000000?(pod-1000000)*0.04:0;
  const suma=pit+zus+zdrow+danina;
  return {P,zus,zdrow,pit,danina,suma,nettoY:P-suma,nettoM:(P-suma)/12,stopa:P?suma/P:null};
}
function renderEtat(){
  const box=$("#etatResults"); if(!box) return;
  if(!box.dataset.wired){ box.dataset.wired="1"; ["#etP","#etKup"].forEach(id=>{const el=$(id);if(el)el.addEventListener("input",renderEtat);}); }
  const f=calcEtat(kalkNum("#etP"),kalkNum("#etKup"));
  const rows=[["PIT (skala)",f.pit],["Składka zdrowotna",f.zdrow],["ZUS (Twoja część)",f.zus]];
  if(f.danina>0) rows.push(["Danina solidarn.",f.danina]);
  box.innerHTML=`<div class="kc-dochod">Przychód roczny brutto: <b>${plPLN(f.P)}</b></div>
    <div class="kgrid"><div class="kc"><div class="kc-h">Umowa o pracę (etat)</div>${kalkRows(rows)}
      <div class="kr kr-sum"><span>Suma obciążeń</span><b>${plPLN(f.suma)}</b></div>
      <div class="kr"><span>Efektywna stopa</span><b>${f.stopa==null?"n/d":plPct(f.stopa)}</b></div>
      <div class="kr kr-net"><span>Na rękę (rok)</span><b>${plPLN(f.nettoY)}</b></div>
      <div class="kr"><span>Na rękę (mies.)</span><b>${plPLN(f.nettoM)}</b></div>
    </div></div>`;
}

// --- JDG (skala) + etat ---
function calcJdgUop(dgP,dgK,dgZus,mg,kup){
  const dgD=dgP-dgK;
  const dgZdrow=Math.max(0.09*(dgD-dgZus),HEALTH_MIN_2026);
  const dgPod=dgD-dgZus;
  const uP=mg*12, uZus=uopZus(uP), uDoch=uP-kup, uZdrow=0.09*(uP-uZus);
  const pod=dgPod+uDoch-uZus;
  const pit=skalaPIT(pod);
  const danina=pod>1000000?(pod-1000000)*0.04:0;
  const suma=pit+danina+dgZus+dgZdrow+uZdrow+uZus;
  const nettoY=dgD+uP-suma;
  return {dgD,uP,dgZus,dgZdrow,uZus,uZdrow,pit,danina,suma,nettoY,nettoM:nettoY/12,stopa:(dgD+uDoch)?suma/(dgD+uDoch):null};
}
function renderJdgUop(){
  const box=$("#jdgUopResults"); if(!box) return;
  if(!box.dataset.wired){ box.dataset.wired="1"; ["#juP","#juK","#juZus","#juMg","#juKup"].forEach(id=>{const el=$(id);if(el)el.addEventListener("input",renderJdgUop);}); }
  const f=calcJdgUop(kalkNum("#juP"),kalkNum("#juK"),kalkNum("#juZus"),kalkNum("#juMg"),kalkNum("#juKup"));
  const rows=[["PIT (łącznie, skala)",f.pit],["Zdrowotna z DG",f.dgZdrow],["Zdrowotna z UoP",f.uZdrow],["ZUS z DG",f.dgZus],["ZUS z UoP",f.uZus]];
  if(f.danina>0) rows.push(["Danina solidarn.",f.danina]);
  box.innerHTML=`<div class="kc-dochod">Dochód z DG: <b>${plPLN(f.dgD)}</b> + przychód z UoP: <b>${plPLN(f.uP)}</b></div>
    <div class="kgrid"><div class="kc"><div class="kc-h">JDG (skala) + umowa o pracę</div>${kalkRows(rows)}
      <div class="kr kr-sum"><span>Suma obciążeń</span><b>${plPLN(f.suma)}</b></div>
      <div class="kr"><span>Efektywna stopa</span><b>${f.stopa==null?"n/d":plPct(f.stopa)}</b></div>
      <div class="kr kr-net"><span>Zostaje (rok)</span><b>${plPLN(f.nettoY)}</b></div>
      <div class="kr"><span>Zostaje (mies.)</span><b>${plPLN(f.nettoM)}</b></div>
    </div></div>`;
}

// --- Spolka osobowa nie-CIT: udzial wspolnika rozliczany jak JDG ---
function renderOsobowa(){
  const box=$("#osResults"); if(!box) return;
  if(!box.dataset.wired){ box.dataset.wired="1";
    ["#osP","#osK","#osShare"].forEach(id=>{const el=$(id);if(el)el.addEventListener("input",renderOsobowa);});
    ["#osZus","#osR","#osChor"].forEach(id=>{const el=$(id);if(el)el.addEventListener("change",renderOsobowa);});
  }
  const totP=kalkNum("#osP"), totK=kalkNum("#osK");
  let share=kalkNum("#osShare")/100; if(!(share>0)) share=0; if(share>1) share=1;
  const pP=totP*share, pK=totK*share;
  const r=parseFloat(($("#osR")||{}).value||"0.085");
  const chor=!!(($("#osChor")||{}).checked);
  const scheme=(($("#osZus")||{}).value)||"pelny";
  const ZUS=zusForScheme(scheme,pP,pK,chor);
  const res=calcForms(pP,pK,ZUS,r);
  const forms=[["Liniowy 19%",res.lin],["Ryczałt "+plPct(r),res.rycz],["Skala podatkowa",res.sk]];
  let best=null; forms.forEach(([_,f])=>{ if(f&&(best===null||f.suma<best)) best=f.suma; });
  box.innerHTML=`<div class="kc-dochod">Twój udział: <b>${plPct(share)}</b> → Twój przychód: <b>${plPLN(pP)}</b> · Twój dochód: <b>${plPLN(res.D)}</b> · ZUS <b>${esc(ZUS_LABEL[scheme])}</b>${chor?" + chorobowe":""}: ${plPLN(ZUS)}</div>
    <div class="kgrid">${forms.map(f=>jdgCard(f[0],f[1],best)).join("")}</div>`;
}

// --- Spolka komandytowa (komandytariusz vs komplementariusz) ---
function calcKomandyt(P,K,n,maly){
  const doch=Math.max(P-K,0);
  const cit=Math.max(doch*(maly?0.09:0.19),0);
  const zus=21459.48*n, zdrow=830.58*12*n;
  const wyplata=Math.max(doch-cit,0);
  const pitTrad=wyplata*0.19;
  const pitRosz=Math.max(wyplata*0.19-cit,0);
  const mk=(pit)=>{ const suma=cit+zus+zdrow+pit; return {cit,zus,zdrow,pit,suma,stopa:doch?suma/doch:null,netto:doch-suma}; };
  return {doch,wyplata, trad:mk(pitTrad), rosz:mk(pitRosz)};
}
function renderKomandyt(){
  const box=$("#komResults"); if(!box) return;
  if(!box.dataset.wired){ box.dataset.wired="1";
    ["#komP","#komK","#komN"].forEach(id=>{const el=$(id);if(el)el.addEventListener("input",renderKomandyt);});
    const m=$("#komMaly"); if(m) m.addEventListener("change",renderKomandyt);
  }
  const P=kalkNum("#komP"),K=kalkNum("#komK"); let n=Math.round(kalkNum("#komN"))||1; if(n<1)n=1;
  const maly=!!(($("#komMaly")||{}).checked);
  const res=calcKomandyt(P,K,n,maly);
  let best=Math.min(res.trad.suma,res.rosz.suma);
  const card=(name,sub,f)=>{
    const isBest=Math.abs(f.suma-best)<0.005;
    const rows=[["CIT spółki",f.cit],["PIT od wypłaty",f.pit],["ZUS wspólników",f.zus],["Zdrowotna wspólników",f.zdrow]];
    return `<div class="kc${isBest?' kc-best':''}">${isBest?'<div class="kc-badge">korzystniej</div>':''}
      <div class="kc-h">${esc(name)}</div><div style="font-size:11px;color:var(--ink-faint);margin:-2px 0 8px;line-height:1.3">${esc(sub)}</div>${kalkRows(rows)}
      <div class="kr kr-sum"><span>Suma obciążeń</span><b>${plPLN(f.suma)}</b></div>
      <div class="kr"><span>Efektywna stopa</span><b>${f.stopa==null?"n/d":plPct(f.stopa)}</b></div>
      <div class="kr kr-net"><span>Zostaje (rok)</span><b>${plPLN(f.netto)}</b></div>
    </div>`;
  };
  box.innerHTML=`<div class="kc-dochod">Dochód spółki: <b>${plPLN(res.doch)}</b> · po CIT do wypłaty: <b>${plPLN(res.wyplata)}</b> · ${n} wspóln.</div>
    <div class="kgrid">${card("Komandytariusze (tradycyjna)","wspólnik = komandytariusz, bez odliczenia CIT",res.trad)}${card("Komplementariusze (roszada)","wspólnik = komplementariusz, odlicza CIT od PIT",res.rosz)}</div>`;
}

// --- Laczne rozliczenie skala (malzonkowie / samotny rodzic) ---
let lacSingle=false;
function calcLacznie(podA,podB,single){
  const half = single ? podA/2 : (podA+podB)/2;
  const joint = 2*skalaPIT(half);
  const sep = single ? skalaPIT(podA) : (skalaPIT(podA)+skalaPIT(podB));
  return {joint, sep, save:Math.max(sep-joint,0)};
}
function renderLacznie(){
  const box=$("#lacResults"); if(!box) return;
  if(!box.dataset.wired){ box.dataset.wired="1";
    ["#lacA","#lacB"].forEach(id=>{const el=$(id);if(el)el.addEventListener("input",renderLacznie);});
    document.querySelectorAll("#lacMode .srcbtn").forEach(b=>b.addEventListener("click",()=>{
      lacSingle=(b.dataset.mode==="rodzic");
      document.querySelectorAll("#lacMode .srcbtn").forEach(x=>x.classList.toggle("on",x===b));
      const bw=$("#lacBWrap"); if(bw) bw.style.display=lacSingle?"none":"";
      const lbl=$("#lacALbl"); if(lbl) lbl.textContent=lacSingle?"Dochód do opodatkowania — rodzic / rok":"Dochód do opodatkowania — małżonek A / rok";
      renderLacznie();
    }));
  }
  const podA=kalkNum("#lacA"), podB=kalkNum("#lacB");
  const res=calcLacznie(podA,podB,lacSingle);
  const hasGain=res.save>0.005;
  box.innerHTML=`<div class="kgrid">
    <div class="kc"><div class="kc-h">Rozliczenie osobne</div>
      <div class="kr"><span>PIT (suma osobnych)</span><b>${plPLN(res.sep)}</b></div></div>
    <div class="kc${hasGain?' kc-best':''}">${hasGain?'<div class="kc-badge">korzystniej</div>':''}<div class="kc-h">${lacSingle?"Rodzic z dzieckiem":"Wspólnie (małżonkowie)"}</div>
      <div class="kr"><span>PIT przy wspólnym rozliczeniu</span><b>${plPLN(res.joint)}</b></div>
      <div class="kr kr-net"><span>Oszczędność na PIT</span><b>${plPLN(res.save)}</b></div></div>
  </div>${hasGain?"":`<div class="kc-note">Przy zbliżonych dochodach wspólne rozliczenie nie daje korzyści (oba progi i kwoty wolne i tak są wykorzystane).</div>`}`;
}

function render(){
  const n=renderNews(), u=renderUstawy(), r=renderRcl(), mj=renderMoje();
  updateMojeBadge(mj);
  let c;
  if(state.tab==="legis") c=(state.sub.legis==="proc"?r:u);
  else if(state.tab==="moje") c=mj;
  else if(state.tab==="terminy") c=renderTerminy();
  else if(state.tab==="kursy") c=renderKursy();
  else if(state.tab==="stawki") c=renderStawki();
  else if(state.tab==="vat"){ renderVAT(); c=0; }
  else if(state.tab==="kalk"){ renderKalk(); renderEtat(); renderJdgUop(); renderOsobowa(); renderKomandyt(); renderSpzoo(); renderLacznie(); c=0; }
  else if(state.tab==="wyroki") c=($("#wyrokiResults")?$("#wyrokiResults").querySelectorAll(".lcard").length:0);
  else if(state.tab==="kis") c=0;
  else c=n;
  $("#stCount").textContent = c;
  syncAddBtns();
}

// --- generyczny mechanizm podzakladek (jedna logika dla Ustaw, Terminow, Kursow, Stawek) ---
const SUBTABS = {
  legis:   [["proc","W trakcie procedowania","#rclView"], ["wchodza","Wchodzą w życie","#legisSoonView"], ["dziennik","Sejm i Dziennik Ustaw","#legisView"]],
  terminy: [["lista","Terminy","#terminyMain"], ["kalk","Kalkulator terminu","#termCalcWrap"]],
  kursy:   [["kursy","Kursy walut","#kursyRates"], ["kalk","Przelicznik walut","#kursyConv"]],
  stawki:  [["sciaga","Ściągawka","#stawkiSciaga"], ["zus","Stawki ZUS","#stawkiZus"]],
  kalk:    [["jdg","JDG","#kalkJdg"], ["etat","Etat","#kalkEtat"], ["jdguop","JDG + etat","#kalkJdgUop"], ["osobowa","Spółka osobowa","#kalkOsobowa"], ["komandyt","Komandytowa","#kalkKomandyt"], ["spzoo","Spółka z o.o.","#kalkSpzoo"], ["lacznie","Łączne (skala)","#kalkLacznie"]],
};
function fillSubBars(){
  Object.keys(SUBTABS).forEach(tab=>{
    const bar=document.querySelector(`[data-subbar="${tab}"]`); if(!bar) return;
    bar.innerHTML=SUBTABS[tab].map(([k,label])=>`<button class="subtab" data-sub="${tab}:${k}">${esc(label)}</button>`).join("");
  });
  document.querySelectorAll("[data-sub]").forEach(b=>b.onclick=()=>{
    const i=b.dataset.sub.indexOf(":"); const tab=b.dataset.sub.slice(0,i), k=b.dataset.sub.slice(i+1);
    state.sub[tab]=k; applySub(tab); render();
  });
}
function applySub(tab){
  const cfg=SUBTABS[tab]; if(!cfg) return;
  const cur=state.sub[tab];
  cfg.forEach(([k,,view])=>{ const el=$(view); if(el) el.hidden=(k!==cur); });
  document.querySelectorAll(`[data-sub^="${tab}:"]`).forEach(b=>b.classList.toggle("on", b.dataset.sub===tab+":"+cur));
}
// --- sekcje: grupowanie zakladek-czlonkow (Narzedzia, Prawo) ---
const SECTIONS = [
  ["news",      "Wiadomości", ["news"]],
  ["terminy",   "Terminy",    ["terminy"]],
  ["narzedzia", "Narzędzia",  ["kursy","stawki","vat","kalk"]],
  ["prawo",     "Prawo",      ["legis","wyroki","kis"]],
  ["moje",      "Moje",       ["moje"]],
];
const MEMBER_LABEL = {news:"Wiadomości", terminy:"Terminy", kursy:"Kursy", stawki:"Stawki", vat:"VAT", kalk:"Kalkulator", legis:"Ustawy", wyroki:"Wyroki", kis:"Interpretacje", moje:"Moje"};
let lastMem = {narzedzia:"kursy", prawo:"legis"};
function sectionOf(tab){ const s=SECTIONS.find(x=>x[2].includes(tab)); return s?s[0]:"news"; }
function fillMemberBar(sec, activeTab){
  const bar=$("#memberBar"); if(!bar) return;
  const s=SECTIONS.find(x=>x[0]===sec);
  if(!s || s[2].length<2){ bar.hidden=true; bar.innerHTML=""; return; }
  bar.hidden=false;
  bar.innerHTML=s[2].map(m=>`<button class="memtab${m===activeTab?' on':''}" data-mem="${m}">${esc(MEMBER_LABEL[m])}</button>`).join("");
  bar.querySelectorAll("[data-mem]").forEach(btn=>btn.onclick=()=>switchTab(btn.dataset.mem));
}
function switchSection(sec){
  const s=SECTIONS.find(x=>x[0]===sec); if(!s) return;
  let target=s[2][0];
  if(s[2].length>1 && lastMem[sec] && s[2].includes(lastMem[sec])) target=lastMem[sec];
  switchTab(target);
}
function switchTab(t){
  state.tab=t;
  const sec=sectionOf(t);
  const s=SECTIONS.find(x=>x[0]===sec);
  if(s && s[2].length>1) lastMem[sec]=t;
  document.querySelectorAll("[data-section]").forEach(b=>b.classList.toggle("on", b.dataset.section===sec));
  fillMemberBar(sec, t);
  $("#newsView").hidden  = t!=="news";
  $("#wyrokiView").hidden= t!=="wyroki";
  $("#kisView").hidden   = t!=="kis";
  $("#mojeView").hidden  = t!=="moje";
  $("#terminyView").hidden = t!=="terminy";
  $("#kursyView").hidden = t!=="kursy";
  $("#stawkiView").hidden = t!=="stawki";
  $("#vatView").hidden = t!=="vat";
  $("#kalkView").hidden = t!=="kalk";
  // Ustawy: pasek podzakladek + trzy widoki na poziomie glownym
  const inLegis=(t==="legis");
  $("#legisSubBar").hidden = !inLegis;
  if(inLegis){ applySub("legis"); } else { $("#legisView").hidden=true; $("#legisSoonView").hidden=true; $("#rclView").hidden=true; }
  // zakladki z podzakladkami wewnatrz sekcji
  if(t==="terminy") applySub("terminy");
  if(t==="kursy") applySub("kursy");
  if(t==="stawki") applySub("stawki");
  if(t==="kalk") applySub("kalk");
  render();
}

(function init(){
  const b=BUILT?new Date(BUILT):null;
  if(b){ $("#stTime").textContent=b.toLocaleTimeString("pl-PL",{hour:"2-digit",minute:"2-digit"}); $("#stDate").textContent=PL.format(b); }
  let t1;$("#search").oninput=e=>{state.q=e.target.value;clearTimeout(t1);t1=setTimeout(render,160)};
  let t2;$("#searchL").oninput=e=>{state.qL=e.target.value;clearTimeout(t2);t2=setTimeout(render,160)};
  let t3;$("#searchR").oninput=e=>{state.qR=e.target.value;clearTimeout(t3);t3=setTimeout(render,160)};
  $("#liveBtn").onclick=searchDU;
  $("#searchL").addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); searchDU(); } });
  $("#rclBtn").onclick=searchRCL;
  $("#searchR").addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); searchRCL(); } });
  $("#wyrokiBtn").onclick=searchWyroki;
  $("#searchW").addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); searchWyroki(); } });
  ["#wyrFrom","#wyrTo","#wyrType","#wyrSort","#wyrPhrase"].forEach(id=>{ const el=$(id); if(el) el.addEventListener("change",()=>{ if(wyrCtx) searchWyroki(); }); });
  $("#kisBtn").onclick=searchKIS;
  $("#searchK").addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); searchKIS(); } });
  document.querySelectorAll("[data-section]").forEach(b=>b.onclick=()=>switchSection(b.dataset.section));
  fillSubBars();
  // delegacja: „+" dodaj / „✓" usuń (na kartach) oraz usuń w „Moje"
  document.body.addEventListener("click", e=>{
    const add=e.target.closest(".addbtn[data-item]");
    if(add){ try{ toggleMoje(JSON.parse(add.dataset.item)); }catch(_){} return; }
    const bell=e.target.closest("[data-notify]");
    if(bell){ const it=state.moje.find(x=>x.link===bell.dataset.notify); if(it){ it.notify=!it.notify; saveMoje(); render(); } return; }
    const rm=e.target.closest("[data-rm]");
    if(rm){ state.moje=state.moje.filter(x=>x.link!==rm.dataset.rm); saveMoje(); render(); }
  });
  renderChips(); renderDomChips(); render();
})();
</script>
</body>
</html>
'''


# ------------------------------------------------------------------ #
#  RENDEROWANIE                                                       #
# ------------------------------------------------------------------ #
def render(items, feeds, summary, live, total):
    built = datetime.datetime.now(datetime.timezone.utc).isoformat()
    summary_html = ""
    if summary:
        lines = [ln.strip().lstrip("-•*").strip() for ln in summary.splitlines() if ln.strip()]
        lis = "".join(f"<li>{html.escape(ln)}</li>" for ln in lines)
        summary_html = (
            '<div class="summary-panel"><div class="summary-head">'
            '<span class="t">Najważniejsze dziś · AI</span></div>'
            f'<div class="summary-body"><ul>{lis}</ul>'
            '<p class="summary-note">Wygenerowane automatycznie przy ostatniej aktualizacji. '
            'Zawsze sprawdź źródło przed decyzją.</p></div></div>'
        )

    def safe(obj):
        return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")

    return (TEMPLATE
            .replace("{DATA}", safe(items))
            .replace("{FEEDS}", safe(feeds))
            .replace("{BUILT}", built)
            .replace("{LIVE}", str(live))
            .replace("{TOTAL}", str(total))
            .replace("{TOTAL_ITEMS}", str(len(items)))
            .replace("{SUMMARY}", summary_html))


def main():
    print("Pobieram kanały RSS…")
    items, live = fetch_all()
    oitems, olive = fetch_official()
    items += oitems
    items.sort(key=lambda it: it["date"] or "", reverse=True)
    print(f"Pobrano {len(items)} pozycji z {live + olive} źródeł (w tym {olive} oficjalnych). Odsiewam…")
    filtered = apply_filters(items)
    official = [it for it in filtered if it.get("official")]
    news = [it for it in filtered if not it.get("official")][:MAX_ITEMS]
    items = official + news
    items.sort(key=lambda it: it["date"] or "", reverse=True)
    print(f"Po odsiewie: {len(news)} wiadomości + {len(official)} aktów/projektów.")
    if AI_FILTER:
        try:
            items = ai_filter_relevance(items)  # (odlozone) z kluczem: tylko scisle podatkowe newsy
        except Exception as e:
            print(f"  [AI filtr POMINIĘTY] {e}")
    try:
        summarize_articles(items)      # streszczenia poszczególnych artykułów (jeśli jest klucz)
    except Exception as e:
        print(f"  [streszczenia POMINIĘTE] {e}")
    try:
        summary = ai_summary(items)    # zbiorcze "Najważniejsze dziś" (jeśli jest klucz)
    except Exception as e:
        print(f"  [podsumowanie POMINIĘTE] {e}")
        summary = ""

    # Lista źródeł do kafelków - TYLKO portale RSS (źródła oficjalne mają teraz
    # własną sekcję "Ścieżka legislacyjna", więc nie dublujemy ich w chipach).
    chip_sources = [{"id": f["id"], "name": f["name"], "cat": f["cat"], "color": f["color"]} for f in FEEDS]

    total_sources = len(FEEDS) + (4 if OFFICIAL_ENABLED else 0)  # +RCL, Dz.U., MP, Sejm
    out = pathlib.Path("public")
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(
        render(items, chip_sources, summary, live + olive, total_sources), encoding="utf-8")
    print("Zapisano public/index.html - gotowe.")


if __name__ == "__main__":
    main()
