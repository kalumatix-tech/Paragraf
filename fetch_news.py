#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Paragraf — generator kokpitu podatkowo-prawnego.
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

import feedparser
import requests

# ------------------------------------------------------------------ #
#  ZRODLA  —  dodawaj / usuwaj tutaj                                  #
#  (uzywamy AKTYWNEGO wzoru ".feed" — stare adresy rss.* byly martwe) #
# ------------------------------------------------------------------ #
FEEDS = [
    # ============================================================== #
    #  ZASADA: tylko DZIALY scisle podatkowo-prawne, NIE cale gazety. #
    #  Cale portale (rp.pl, money, wprost, BI) wpuszczaly kulture,    #
    #  sport i film ("Plus Minus") — dlatego ich tu NIE ma.           #
    # ============================================================== #

    # --- INFOR: dzialy tematyczne (potwierdzone, swieze, na temat) ---
    {"id": "infor-ks", "name": "INFOR Księgowość",    "cat": "Podatki", "color": "#8a2e2a",
     "url": "https://ksiegowosc.infor.pl/.feed"},
    {"id": "infor-pr", "name": "INFOR Prawo",          "cat": "Prawo",   "color": "#1b5e57",
     "url": "https://www.infor.pl/prawo/.feed"},
    {"id": "infor-ka", "name": "INFOR Kadry / ZUS",    "cat": "Kadry",   "color": "#3b5c8a",
     "url": "https://kadry.infor.pl/.feed"},

    # --- Interpretacje podatkowe KIS / Min. Finansow (czysto podatkowe) ---
    {"id": "kis",      "name": "Interpretacje (KIS)",  "cat": "Podatki", "color": "#6b2e8a",
     "url": "https://interpretacje-podatkowe.org/feed"},

    # --- Serwis specjalistyczny (na próbę — sprawdź licznik w logu) ---
    {"id": "podatkibiz", "name": "Podatki.biz",        "cat": "Podatki", "color": "#5c2e6b",
     "url": "https://www.podatki.biz/rss/rss.xml"},
    {"id": "money",  "name": "Money.pl",         "cat":"Finanse","color":"#2e7d6b","url":"https://www.money.pl/rss/"},
    {"id": "bi",     "name": "Business Insider",  "cat":"Biznes", "color":"#6b6b2a","url":"https://businessinsider.com.pl/.feed"},
    {"id": "wprost", "name": "Wprost",            "cat":"Biznes", "color":"#8a4a2e","url":"https://www.wprost.pl/rss.xml"},
    {"id": "rp",     "name": "Rzeczpospolita",    "cat":"Prawo",  "color":"#4a4a8a","url":"https://www.rp.pl/rss/1019"},
    {"id": "bankier","name": "Bankier.pl",        "cat":"Finanse","color":"#9a6b2e","url":"https://www.bankier.pl/rss/finanse.xml"},
    {"id": "infor-mf","name":"INFOR Moja firma",  "cat":"Biznes", "color":"#2e6e8c","url":"https://mojafirma.infor.pl/.feed"},

]

MAX_ITEMS = 120                 # ile pozycji trzymamy na stronie
PER_FEED = 60                   # ile najnowszych z jednego zrodla pobieramy do obrobki
UA = "Mozilla/5.0 (compatible; ParagrafBot/1.0; +https://github.com)"

# ILE najnowszych artykulow ma dostac streszczenie AI (2 zdania w karcie).
# Dziala TYLKO, gdy ustawiony jest sekret ANTHROPIC_API_KEY. 0 = wylacz.
SUMMARIZE_TOP = 18

# Filtr trafnosci AI — ODLOZONY (najpierw domykamy dzialy/daty bez AI).
# Gdy zechcesz: ustaw True (wymaga sekretu ANTHROPIC_API_KEY).
AI_FILTER = False

# ------------------------------------------------------------------ #
#  ZRODLA OFICJALNE (publiczne API Kancelarii Sejmu — bez klucza)     #
#  Dziennik Ustaw + Monitor Polski (publikowane akty) oraz projekty   #
#  ustaw (druki sejmowe). To autorytatywne, niezalezne od portali.    #
# ------------------------------------------------------------------ #
OFFICIAL_ENABLED = True
SEJM_TERM = 10                 # kadencja Sejmu (zmien po nowych wyborach)
OFFICIAL_MAX = 12             # ile PROJEKTOW (druki, z etapem) bierzemy z Sejmu
ELI_MAX = 40                  # ile OPUBLIKOWANYCH aktow (Dz.U./MP) do wyszukiwarki ustaw
RCL_MAX = 15                  # ile PROJEKTOW RZADOWYCH (RCL, przed Sejmem)
RCL_PAGES = 4                 # ile stron listy RCL przejrzec (kazda ~10 pozycji)
OFFICIAL_MAX_AGE_DAYS = 60    # okno swiezosci dla projektow (aktywne w procesie)

OFFICIAL_SRC = {
    "du":   {"name": "Dziennik Ustaw",  "cat": "Legislacja", "color": "#1d3a6b"},
    "mp":   {"name": "Monitor Polski",  "cat": "Legislacja", "color": "#0f5c4a"},
    "sejm": {"name": "Sejm — projekty",  "cat": "Projekty",   "color": "#7a2e5c"},
}

# Z urzedowego "firehose'a" (wszystkie akty/projekty) przepuszczamy tylko te,
# ktorych TYTUL pasuje SCISLE podatkowo/fiskalnie. Rdzenie slow (jak w BLOCK/FOCUS).
# (Swiadomie waskie — wczesniej "oplat"/"finans"/"budzet" wpuszczaly kulture i oswiate.)
OFFICIAL_TOPICS = [
    "podatk", "vat", "cit", "pit", "akcyz", "ryczałt", "ordynacj", "składk",
    "zus", "rachunkow", "cło", "celn", "faktur", "ksef", "jpk", "danin",
    "skarbow", "fiskus", "schemat podatk", "doradc podatkow",
]

# ------------------------------------------------------------------ #
#  ODSIEW  —  to tutaj decydujesz, co odpada                          #
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

# 3) BIALA LISTA (slownikowa) — dziala TYLKO gdy NIE masz klucza AI.
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
    """Małe litery, bez polskich ogonków, bez interpunkcji — do porównań."""
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
        print("  (bez AI — brak sekretu ANTHROPIC_API_KEY)")
        return None
    top = items[:25]
    lst = "\n".join(
        f"{i+1}. [{it['cat']}] {it['title']}" + (f" — {it['desc'][:150]}" if it["desc"] else "")
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
        "Streść poniższy artykuł w DOKŁADNIE dwóch krótkich zdaniach po polsku — rzeczowo "
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
            print("  (bez streszczeń artykułów — brak sekretu ANTHROPIC_API_KEY)")
        return
    targets = [it for it in items if not it.get("official")][:SUMMARIZE_TOP]
    print(f"  Streszczam {len(targets)} najnowszych artykułów…")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda it: _summarize_one(it, key), targets))
    done = sum(1 for it in targets if it.get("summary"))
    print(f"  Streszczono: {done}/{len(targets)} artykułów.")


# ------------------------------------------------------------------ #
#  ZRODLA OFICJALNE — pobieranie z API Sejm/ELI                       #
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
    """Pobiera surowy HTML (RCL nie ma API — parsujemy stronę)."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        if r.status_code != 200:
            print(f"  [RCL {r.status_code}] {url}")
            return None
        return r.text
    except Exception as ex:
        print(f"  [RCL błąd] {url}: {ex}")
        return None


def _rcl_projects():
    """ETAP RZĄDOWY: projekty ustaw z wykazu RCL (legislacja.rcl.gov.pl),
    zanim trafią do Sejmu. Brak API — parsujemy listę regexem (defensywnie)."""
    out, seen = [], set()
    base = "https://legislacja.rcl.gov.pl/lista?typeId=2"   # typeId=2 = projekty ustaw
    for page in range(1, RCL_PAGES + 1):
        url = base if page == 1 else f"{base}&page={page}"
        html_text = _http_get_text(url, timeout=20)
        if not html_text:
            continue
        # Każdy projekt: <a href="/projekt/12402157...">Tytuł</a>; numer (UD116) i data obok.
        for m in re.finditer(r'href="(/projekt/\d+[^"]*)"[^>]*>\s*([^<]{8,}?)\s*</a>', html_text):
            path = m.group(1)
            if path in seen:
                continue
            title = re.sub(r"\s+", " ", html.unescape(m.group(2))).strip()
            if not title or not _topic_ok(title):
                continue
            seen.add(path)
            tail = html_text[m.end():m.end() + 600]
            num = re.search(r"\b([A-Z]{2}\d{1,4})\b", tail)
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
                break
        if len(out) >= RCL_MAX:
            break
    print(f"  [Rząd (RCL)] dopasowano {len(out)} projektów rządowych.")
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
    """OPUBLIKOWANE akty do wyszukiwarki — szersze okno (cały rok), ale nadal
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
    """Dziennik Ustaw (DU) lub Monitor Polski (MP) — najnowsze akty na temat."""
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
    """Projekty ustaw i inne druki sejmowe — najnowsze na temat, z etapem procesu."""
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
                          ("Sejm — projekty", _sejm_prints)):
        try:
            items += getter()
        except Exception as e:
            print(f"  [oficjalne: {label} POMINIĘTE z powodu błędu] {e}")
    live = len({it["fid"] for it in items})
    return items, live


# ------------------------------------------------------------------ #
#  FILTR AI RELEVANCJI — zostawia tylko ŚCIŚLE podatkowe newsy.       #
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
            print("  [AI filtr] brak trafień — nie odsiewam (zostawiam wszystko)")
            return items
        approved = {id(cand[i]) for i in keep if 0 <= i < len(cand)}
        before = len(cand)
        result = [it for it in items if it.get("official") or id(it) in approved]
        print(f"  [AI filtr] ściśle podatkowe: {len(approved)}/{before} newsów")
        return result
    except Exception as ex:
        print(f"  [AI filtr błąd] {ex} — nie odsiewam")
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
      <button class="tab" data-tab="legis">Ustawy</button>
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
        <input class="search" id="searchL" type="text" placeholder="Szukaj w ustawach i projektach: VAT, akcyza, KSeF, nr druku…" autocomplete="off">
      </div>

      <section id="legis"></section>
    </section>

    <footer>
      <b>Paragraf</b> aktualizuje się automatycznie kilka razy dziennie — w jednym miejscu.<br>
      Zakładka <b>Ustawy</b> pokazuje ścieżkę legislacyjną (Projekt → Sejm → Prezydent → Dz.U.) wprost z oficjalnego API Sejmu i pozwala przeszukać akty. Zakładka <b>Wiadomości</b> — artykuły z portali (chipem włączasz/wyłączasz źródło; wybór zapamiętuje przeglądarka).
    </footer>
  </div>

<script>
const DATA = {DATA};
const BUILT = "{BUILT}";
const FEEDS = {FEEDS};
const state = { off:new Set(), q:"", qL:"", tab:"news" };
const $ = s => document.querySelector(s);
try{ const s=localStorage.getItem("paragraf-off"); if(s) state.off=new Set(JSON.parse(s)); }catch(e){}
const presentIds = new Set(DATA.map(d=>d.fid));

function esc(s){return (s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
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
function legisVisible(){
  const q=state.qL.trim().toLowerCase();
  return DATA
    .filter(it=>it.track)
    .filter(it=>!q||(it.title+" "+it.desc+" "+it.src+" "+it.cat+" "+(it.stage||"")).toLowerCase().includes(q))
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
function stepper(step){
  return `<div class="stepper">`+STEPS.map((s,i)=>{
    const on = (i < step) ? "on" : "";
    const cur = (i === step-1) ? "cur" : "";
    return `<div class="step ${on} ${cur}"><i></i><b>${s}</b></div>`;
  }).join("")+`</div>`;
}
function legisCard(it){
  const when = ago(it._d) || "—";
  const stage = it.stage ? `<div class="lstage"><span>Etap</span> ${esc(it.stage)}</div>` : "";
  return `<article class="lcard" style="--ccol:${it.color}">
    <div class="lhead"><span class="lsrc"><span class="dot"></span>${esc(it.src)}</span><span class="lwhen">${esc(when)}</span></div>
    <a class="ltitle" href="${esc(it.link)}" target="_blank" rel="noopener">${esc(it.title)}</a>
    ${stepper(it.step||1)}
    ${stage}
  </article>`;
}

const LEGIS_DEFAULT = 18;

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
      <span class="src"><span class="dot"></span>${esc(it.src)} <span class="cat">${esc(it.cat)}</span></span>
      <a class="title" href="${esc(it.link)}" target="_blank" rel="noopener">${esc(it.title)}</a>
      ${ (it.summary||it.desc) ? `<p class="desc${it.summary?' sum':''}">${it.summary?'<span class="aitag">✦ streszczenie</span> ':''}${esc(it.summary||it.desc)}</p>` : "" }
      <div class="meta">${esc(ago(it._d))||"—"}</div>
    </article>`;
  }
  feed.innerHTML=h; return vis.length;
}

function renderLegis(){
  const all=legisVisible(); const L=$("#legis");
  const searching = state.qL.trim().length>0;
  if(!all.length){
    L.innerHTML=`<div class="empty"><div class="ic">§</div><h3>Brak ustaw</h3><p>${searching?'Nic nie pasuje do tej frazy.':'Brak świeżych aktów i projektów.'}</p></div>`;
    return 0;
  }
  const show = searching ? all : all.slice(0, LEGIS_DEFAULT);
  let h=`<div class="lsec-head"><span class="lt">${searching?'Wyniki wyszukiwania':'Ścieżka legislacyjna'}</span>`
    +`<span class="lcount">${searching?(all.length+' znalezionych'):(all.length+' śledzonych')}</span></div>`
    +`<div class="lgrid">${show.map(legisCard).join("")}</div>`;
  if(!searching && all.length>LEGIS_DEFAULT){
    h+=`<button class="showall" id="showAll">Pokaż wszystkie (${all.length})</button>`;
  }
  L.innerHTML=h;
  const sa=$("#showAll");
  if(sa) sa.onclick=()=>{ L.querySelector(".lgrid").innerHTML=all.map(legisCard).join(""); sa.remove(); };
  return all.length;
}

function render(){
  const nc=renderNews(); const lc=renderLegis();
  $("#stCount").textContent = (state.tab==="legis") ? lc : nc;
}

function switchTab(t){
  state.tab=t;
  document.querySelectorAll(".tab").forEach(b=>b.classList.toggle("on", b.dataset.tab===t));
  $("#newsView").hidden = (t!=="news");
  $("#legisView").hidden = (t!=="legis");
  render();
}

(function init(){
  const b=BUILT?new Date(BUILT):null;
  if(b){ $("#stTime").textContent=b.toLocaleTimeString("pl-PL",{hour:"2-digit",minute:"2-digit"}); $("#stDate").textContent=PL.format(b); }
  let t1;$("#search").oninput=e=>{state.q=e.target.value;clearTimeout(t1);t1=setTimeout(render,160)};
  let t2;$("#searchL").oninput=e=>{state.qL=e.target.value;clearTimeout(t2);t2=setTimeout(render,160)};
  document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
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

    # Lista źródeł do kafelków — TYLKO portale RSS (źródła oficjalne mają teraz
    # własną sekcję "Ścieżka legislacyjna", więc nie dublujemy ich w chipach).
    chip_sources = [{"id": f["id"], "name": f["name"], "cat": f["cat"], "color": f["color"]} for f in FEEDS]

    total_sources = len(FEEDS) + (4 if OFFICIAL_ENABLED else 0)  # +RCL, Dz.U., MP, Sejm
    out = pathlib.Path("public")
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(
        render(items, chip_sources, summary, live + olive, total_sources), encoding="utf-8")
    print("Zapisano public/index.html — gotowe.")


if __name__ == "__main__":
    main()
