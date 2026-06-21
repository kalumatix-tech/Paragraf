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
    dated = [i for i, it in enumerate(items) if it["date"]]
    cur = max(dated) if dated else 0
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
        out.append({
            "title": title, "link": link, "desc": desc, "summary": "",
            "date": date,
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
  .tabs{display:flex;gap:4px;margin-bottom:22px;border-bottom:1px solid var(--line)}
  .tab{appearance:none;border:none;background:none;cursor:pointer;font-family:var(--sans);
    font-size:14.5px;font-weight:600;color:var(--ink-faint);padding:10px 16px;position:relative;
    border-bottom:2px solid transparent;margin-bottom:-1px;transition:color .15s}
  .tab:hover{color:var(--ink-soft)}
  .tab.on{color:var(--accent);border-bottom-color:var(--accent)}
  .tab:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
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
      <button class="tab on" data-tab="news">Wiadomości</button>
      <button class="tab" data-tab="terminy">Terminy</button>
      <button class="tab" data-tab="legis">Ustawy</button>
      <button class="tab" data-tab="rcl">RCL</button>
      <button class="tab" data-tab="wyroki">Wyroki</button>
      <button class="tab" data-tab="kis">Interpretacje</button>
      <button class="tab" data-tab="moje">Moje<span class="tabbadge" id="mojeBadge"></span></button>
    </nav>

    <section id="newsView">
      <div class="controls">
        <input class="search" id="search" type="text" placeholder="Szukaj: VAT, KSeF, estoński CIT, ZUS, orzeczenie…" autocomplete="off">
        <div class="chips" id="chips"></div>
      </div>

      {SUMMARY}

      <main id="feed"></main>
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
        <div class="srcToggle" id="wyrokiSrc">
          <button class="srcbtn on" data-src="saos">SAOS — sądy powszechne, SN, TK, KIO</button>
          <button class="srcbtn" data-src="cbosa">CBOSA — NSA/WSA (podatkowe)</button>
        </div>
        <div class="searchrow">
          <input class="search" id="searchW" type="text" placeholder="Szukaj w treści orzeczeń: VAT, ulga, koszty, zwolnienie…  (Enter)" autocomplete="off">
          <button class="livebtn" id="wyrokiBtn" title="Szukaj orzeczen">Szukaj</button>
        </div>
        <p class="livehint"><b>SAOS</b> ma API, więc wyniki pokazuję tu w aplikacji — ale to sądy powszechne/SN/TK/KIO, <b>bez podatkowych</b>. Sprawy podatkowe (NSA/WSA) są w <b>CBOSA</b>, która blokuje automatyczny dostęp i nie ma API — dlatego dla niej kopiuję frazę i otwieram jej wyszukiwarkę.</p>
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
      <div class="controls">
        <div class="chips" id="termChips"></div>
        <p class="livehint" style="margin:10px 2px 0">Najbliższe terminy podatkowe i sprawozdawcze. Daty już <b>przesunięte</b>, gdy wypadają w sobotę/niedzielę/święto (art. 12 §5 Ordynacji). Liczone na dziś w Twojej przeglądarce. To <b>ogólny terminarz</b> dla typowych przypadków — które terminy faktycznie Cię dotyczą, zależy od formy klienta (VAT mies./kwart., skala/ryczałt, spółka/JDG). Filtruj chipami wyżej.</p>
      </div>
      <div id="terminyList"></div>
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
const state = { off:new Set(), q:"", qL:"", qR:"", tab:"news", moje:[], wyrokiSrc:"saos", termOff:new Set() };
const $ = s => document.querySelector(s);
try{ const s=localStorage.getItem("paragraf-off"); if(s) state.off=new Set(JSON.parse(s)); }catch(e){}
try{ const s=localStorage.getItem("paragraf-moje"); if(s) state.moje=JSON.parse(s)||[]; }catch(e){}
try{ const s=localStorage.getItem("paragraf-termoff"); if(s) state.termOff=new Set(JSON.parse(s)); }catch(e){}
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
  return DATA
    .filter(it=>!it.track)
    .filter(it=>!state.off.has(it.fid))
    .filter(it=>!q||(it.title+" "+it.desc+" "+it.src+" "+it.cat).toLowerCase().includes(q))
    .map(it=>({...it,_d:pd(it.date)}));
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
  let cur=0; items.forEach((it,i)=>{ if(it.date) cur=i; });
  items.forEach((it,i)=>{ it.state = i<cur?"done":(i===cur?"cur":"pending"); });
  return items;
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
    ${body}
  </article>`;
}

const LEGIS_DEFAULT = 18;

// ---- WYSZUKIWANIE NA ŻYWO (Dz.U. + RCL) ----
// Statyczna strona nie ma serwera, więc próbujemy bezpośrednio, a gdy
// przeglądarka zablokuje (CORS) — przez darmowy przekaźnik. Dane publiczne.
const PXY = [
  u => u,
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
async function getJSON(u){
  if(_cache.has("j"+u)) return _cache.get("j"+u);
  const v = await _race(PXY.map(p=>p(u)), false);
  _cache.set("j"+u, v); return v;
}
async function getText(u){
  if(_cache.has("t"+u)) return _cache.get("t"+u);
  const v = await _race([PXY[1],PXY[2],PXY[3],PXY[0]].map(p=>p(u)), true);
  _cache.set("t"+u, v); return v;
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
            stage:"Opublikowano", step:4, _d:pd(a.announcementDate||null)});
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

// Wyroki / orzeczenia — wyszukiwanie po hasle w bazie SAOS (czyste API JSON)
function wyrokCard(it){
  const court = (it.division && it.division.court && it.division.court.name) || it.courtType || "Sąd";
  const sig = (it.courtCases && it.courtCases[0] && it.courtCases[0].caseNumber) || "";
  const date = it.judgmentDate || "";
  const map = {SENTENCE:"wyrok", DECISION:"postanowienie", RESOLUTION:"uchwała", REGULATION:"zarządzenie", REASONS:"uzasadnienie"};
  const kind = map[it.judgmentType] || "orzeczenie";
  let snip = (it.textContent||"").replace(/<[^>]*>/g," ").replace(/\s+/g," ").trim();
  if(snip.length>240) snip = snip.slice(0,240)+"…";
  const link = "https://www.saos.org.pl/judgments/"+it.id;
  const title = (sig ? sig+" — " : "") + court;
  const mini = {type:"wyrok", title:title, link:link, src:kind, stage:"", step:0};
  return `<article class="lcard" style="--ccol:#3a5c8a">
    <div class="lhead"><span class="lsrc"><span class="dot"></span>${esc(kind)}${date?" · "+esc(date):""}</span><span class="lright">${addbtn(mini)}</span></div>
    <a class="ltitle" href="${esc(link)}" target="_blank" rel="noopener">${esc(title)}</a>
    ${snip?`<p class="wyrok-snip">${esc(snip)}</p>`:""}
  </article>`;
}
async function searchWyroki(){
  const q=($("#searchW").value||"").trim();
  const box=$("#wyrokiResults"), btn=$("#wyrokiBtn");
  if(q.length<2){ box.innerHTML=`<div class="live-status">Wpisz co najmniej 2 znaki.</div>`; return; }
  // CBOSA: brak API + blokuje boty -> kopiujemy fraze i otwieramy jej wyszukiwarke.
  if(state.wyrokiSrc==="cbosa"){
    let copied=false;
    try{ navigator.clipboard.writeText(q); copied=true; }catch(_){}
    try{ window.open("https://orzeczenia.nsa.gov.pl/cbo/query", "_blank", "noopener"); }catch(_){}
    box.innerHTML=`<div class="kis-launch">
      <p>${copied?'Skopiowałem frazę <b>„'+esc(q)+'"</b> do schowka.':'Fraza: <b>„'+esc(q)+'"</b>.'} Otworzyłem wyszukiwarkę <b>CBOSA</b> (NSA/WSA) w nowej karcie — wklej (Ctrl+V) w pole „Szukana fraza" i naciśnij „Szukaj".</p>
      <p class="kis-alt">CBOSA blokuje automatyczne pobieranie i nie ma API, dlatego nie da się jej wyników wciągnąć tutaj. To jedyne pełne źródło orzeczeń podatkowych (sądy administracyjne).</p>
    </div>`;
    return;
  }
  btn.disabled=true; box.innerHTML=`<div class="live-status">Szukam orzeczeń w SAOS…</div>`;
  let items=[];
  try{
    const url=`https://www.saos.org.pl/api/search/judgments?pageSize=20&pageNumber=0&all=${encodeURIComponent(q)}&sortingField=JUDGMENT_DATE&sortingDirection=DESC`;
    const data=await getJSON(url);
    if(data && Array.isArray(data.items)) items=data.items;
  }catch(e){}
  btn.disabled=false;
  if(!items.length){
    box.innerHTML=`<div class="live-status">Nic nie znalazłem w SAOS dla „${esc(q)}". Uwaga: SAOS nie ma spraw podatkowych — dla nich przełącz na <b>CBOSA</b> wyżej. Albo kliknij ponownie (przekaźnik mógł nie odpowiedzieć).</div>`;
    return;
  }
  box.innerHTML=`<div class="live-sec-head">Orzeczenia (SAOS) — „${esc(q)}" (${items.length})</div><div class="lgrid">${items.map(wyrokCard).join("")}</div>`;
  syncAddBtns();
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
    return 0;
  }
  let h="",last=null;
  for(const it of vis){
    const k=dayKey(it._d);
    if(k!==last){h+=`<div class="daysep"><span class="lab">${esc(dayLabel(it._d))}</span><span class="rule"></span></div>`;last=k;}
    h+=`<article class="card" style="--ccol:${it.color}">
      <div class="chead"><span class="src"><span class="dot"></span>${esc(it.src)} <span class="cat">${esc(it.cat)}</span></span>${addbtn({type:"news", title:it.title, link:it.link, src:it.src, stage:"", step:0})}</div>
      <a class="title" href="${esc(it.link)}" target="_blank" rel="noopener">${esc(it.title)}</a>
      ${ (it.summary||it.desc) ? `<p class="desc${it.summary?' sum':''}">${it.summary?'<span class="aitag">✦ streszczenie</span> ':''}${esc(it.summary||it.desc)}</p>` : "" }
      <div class="meta">${esc(ago(it._d))||"—"}</div>
    </article>`;
  }
  feed.innerHTML=h; return vis.length;
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
function renderUstawy(){ return renderTracker(ustawyVisible(), $("#legis"), state.qL.trim().length>0, "Sejm i Dziennik Ustaw", "śledzonych"); }
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
const T_CAT={VAT:"#1d3a6b", PIT:"#8a2e2a", CIT:"#6b4a8a", ZUS:"#0f5c4a", Kadry:"#8a5a2e", Roczne:"#4a4a4a"};
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
  {day:30, months:[6], cat:"Roczne", label:"Zatwierdzenie sprawozdania finansowego", okres:"prev-year", note:"do 6 mies. po zakończeniu roku"}
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
  const box=$("#terminyList"); if(!box) return 0;
  const all=tUpcoming(45).filter(it=>!state.termOff.has(it.cat));
  const now=new Date();
  const head=`<div class="t-today">Dziś: <b>${now.toLocaleDateString("pl-PL",{weekday:"long",day:"numeric",month:"long",year:"numeric"})}</b> · najbliższe 45 dni</div>`;
  if(!all.length){ box.innerHTML=head+`<div class="live-status">Brak terminów w tym oknie dla wybranych kategorii (sprawdź chipy wyżej).</div>`; return 0; }
  box.innerHTML=head+`<div class="tgrid">${all.map(tCard).join("")}</div>`;
  return all.length;
}

function render(){
  const n=renderNews(), u=renderUstawy(), r=renderRcl(), mj=renderMoje();
  updateMojeBadge(mj);
  let c;
  if(state.tab==="legis") c=u;
  else if(state.tab==="rcl") c=r;
  else if(state.tab==="moje") c=mj;
  else if(state.tab==="terminy") c=renderTerminy();
  else if(state.tab==="wyroki") c=($("#wyrokiResults")?$("#wyrokiResults").querySelectorAll(".lcard").length:0);
  else if(state.tab==="kis") c=0;
  else c=n;
  $("#stCount").textContent = c;
  syncAddBtns();
}

function switchTab(t){
  state.tab=t;
  document.querySelectorAll(".tab").forEach(b=>b.classList.toggle("on", b.dataset.tab===t));
  $("#newsView").hidden  = t!=="news";
  $("#legisView").hidden = t!=="legis";
  $("#rclView").hidden   = t!=="rcl";
  $("#wyrokiView").hidden= t!=="wyroki";
  $("#kisView").hidden   = t!=="kis";
  $("#mojeView").hidden  = t!=="moje";
  $("#terminyView").hidden = t!=="terminy";
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
  document.querySelectorAll("#wyrokiSrc .srcbtn").forEach(b=>b.onclick=()=>{
    state.wyrokiSrc=b.dataset.src;
    document.querySelectorAll("#wyrokiSrc .srcbtn").forEach(x=>x.classList.toggle("on",x===b));
    const inp=$("#searchW"); if(inp&&inp.value.trim().length>=2) searchWyroki();
  });
  $("#kisBtn").onclick=searchKIS;
  $("#searchK").addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); searchKIS(); } });
  document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
  // delegacja: „+" dodaj / „✓" usuń (na kartach) oraz usuń w „Moje"
  document.body.addEventListener("click", e=>{
    const add=e.target.closest(".addbtn[data-item]");
    if(add){ try{ toggleMoje(JSON.parse(add.dataset.item)); }catch(_){} return; }
    const bell=e.target.closest("[data-notify]");
    if(bell){ const it=state.moje.find(x=>x.link===bell.dataset.notify); if(it){ it.notify=!it.notify; saveMoje(); render(); } return; }
    const rm=e.target.closest("[data-rm]");
    if(rm){ state.moje=state.moje.filter(x=>x.link!==rm.dataset.rm); saveMoje(); render(); }
  });
  renderChips(); render();
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
