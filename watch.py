#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Obserwator projektów (RCL) dla kokpitu „Paragraf".

Czyta listę z `obserwowane.txt`, sprawdza aktualny etap każdego projektu w RCL,
porównuje ze stanem z poprzedniego uruchomienia (`stan_obserwowanych.json`)
i — gdy coś się zmieniło — przygotowuje TREŚĆ maila (pliki mail_subject.txt /
mail_body.txt). Samego maila NIE wysyła; robi to workflow GitHub Actions,
jeśli są zmiany i ustawiono sekrety poczty.

Skrypt jest samodzielny — potrzebuje tylko biblioteki `requests`.
"""
import os
import re
import json
import html
import datetime
import urllib.parse

import requests

WATCH_FILE = "obserwowane.txt"
STATE_FILE = "stan_obserwowanych.json"
SUBJECT_FILE = "mail_subject.txt"
BODY_FILE = "mail_body.txt"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ------------------------------------------------------------------ pobieranie
def _get(url, timeout=20):
    try:
        r = requests.get(url, headers={"User-Agent": UA,
                                       "Accept-Language": "pl,en;q=0.8"}, timeout=timeout)
        if r.status_code == 200 and r.text:
            return r.text
    except Exception:
        pass
    return None


def _get_json(url, timeout=20):
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def topic_matches(keyword):
    """Akty (Dz.U./M.P.) z fraza w tytule, opublikowane w ostatnich ~45 dniach.
    Zwraca slownik {ELI: {title, date, inforce, ref, link}}."""
    since = (datetime.date.today() - datetime.timedelta(days=45)).isoformat()
    out = {}
    for pub in ("DU", "MP"):
        url = ("https://api.sejm.gov.pl/eli/acts/search?title="
               + urllib.parse.quote(keyword)
               + f"&publisher={pub}&dateFrom={since}&limit=50")
        data = _get_json(url)
        items = (data or {}).get("items") or []
        for a in items:
            eli = a.get("ELI") or a.get("address")
            if not eli:
                continue
            out[eli] = {
                "title": (a.get("title") or "").strip(),
                "date": a.get("announcementDate") or "",
                "inforce": a.get("entryIntoForce") or "",
                "ref": (a.get("displayAddress") or "").strip(),
                "link": f"https://api.sejm.gov.pl/eli/acts/{eli}/text.pdf",
            }
    return out


# ------------------------------------------------------- analiza strony RCL
# (te same reguły, co w kokpicie)
_RCL_STAGE_KW = ("lobbing", "uzgodnie", "konsultacj", "opiniowan", "komitet", "komisj",
                 "rada ministr", "radzie ministr", "potwierdz", "skierowan", "notyfikacj",
                 "rozpatrz", "przyjęc", "przyjet")


def rcl_status(page_text):
    if not page_text:
        return None
    low = page_text.lower()
    if (re.search(r"sta[łl]a?\s*si[ęe]\s*ustaw", low) or "dołączono do projektu" in low
            or re.search(r"kontynuowan[ya]\s+(?:pod\s+nr|jako)", low)):
        return "became_law"
    if "na stronach sejmu" in low or "dalszy ciąg procesu legislacyjnego" in low:
        return "left"
    if re.search(r"status projektu:\s*zamkn", low):
        return "closed"
    return "in_gov"


def rcl_became_law(page_text):
    """Czy projekt sie zakonczyl (stal sie ustawa / dolaczony do innego) + odwolanie do Dz.U."""
    if not page_text:
        return None
    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", page_text))
    low = flat.lower()
    is_law = re.search(r"sta[łl]a?\s*si[ęe]\s*ustaw", low) is not None
    merged = "dołączono do projektu" in low
    continued = re.search(r"kontynuowan[ya]\s+(?:pod\s+nr|jako)", low) is not None
    if not (is_law or merged or continued):
        return None
    poz = year = None
    mp = re.search(r"dz\.?\s*u\.?.{0,60}?poz\.?\s*0*(\d{1,5})", low)
    if mp:
        poz = mp.group(1)
    my = re.search(r"ustaw[aąy].{0,90}?((?:19|20)\d{2})\s*r", low)
    if not my:
        my = re.search(r"((?:19|20)\d{2}).{0,25}?poz", low)
    if my:
        year = my.group(1)
    return {"is_law": is_law, "merged": merged, "poz": poz, "year": year}


def rcl_stages(page_text):
    if not page_text:
        return []
    t = re.sub(r"<[^>]*>", " ", page_text)
    t = re.sub(r"[·•|]", " ", t)
    t = re.sub(r"\s+", " ", t)
    pat = re.compile(r"(\d{1,2})\.\s+(.{3,75}?)"
                     r"(?=\s+Data ostatniej modyfikacji:|\s+\d{1,2}\.\s|\s+Rządowe Centrum|"
                     r"\s+Mapa strony|\s+Pomoc\b|\s+Kontakt\b|$)"
                     r"(?:\s+Data ostatniej modyfikacji:\s*(\d{2}-\d{2}-\d{4}))?")
    items, seen = [], set()
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


def project_title(page_text):
    if not page_text:
        return ""
    m = re.search(r"<title>(.*?)</title>", page_text, re.S | re.I)
    if not m:
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", m.group(1)))).strip()


# ------------------------------------------------------------------- logika
def read_watchlist():
    items = []
    if not os.path.exists(WATCH_FILE):
        return items
    for line in open(WATCH_FILE, encoding="utf-8"):
        s = line.strip()
        if s and not s.startswith("#"):
            items.append(s)
    return items


def resolve_url(entry):
    """entry = pełny link do projektu RCL ALBO numer z wykazu (UD116/UC55…)."""
    if entry.lower().startswith("http"):
        return entry
    url = ("https://legislacja.rcl.gov.pl/lista?typeId=2&number="
           + urllib.parse.quote(entry.upper()))
    txt = _get(url)
    if txt:
        m = re.search(r'href="(/projekt/\d+[^"]*)"', txt)
        if m:
            return "https://legislacja.rcl.gov.pl" + m.group(1)
    return None


def signature(url):
    """Zwraca (podpis_stanu, opis_dla_człowieka, tytuł) lub (None, None, None)."""
    low_url = url.lower()
    # Akt juz opublikowany (Dz.U. / ELI / PDF) - etap zakonczony, nic do pilnowania.
    if (low_url.endswith(".pdf") or "/text.pdf" in low_url
            or "dziennikustaw.gov.pl" in low_url or "/eli/acts/" in low_url):
        return "opublikowana", "ustawa opublikowana (Dz.U.) - etap zakonczony", url
    page = _get(url)
    if not page:
        return None, None, None
    status = rcl_status(page)
    if status == "became_law":
        bl = rcl_became_law(page) or {}
        if bl.get("year") and bl.get("poz"):
            ref = f"Dz.U. {bl['year']} poz. {bl['poz']}"
        elif bl.get("poz"):
            ref = f"Dz.U. poz. {bl['poz']}"
        else:
            ref = ""
        desc = "zakonczony - stal sie ustawa" + (f" ({ref})" if ref else "")
        return f"became_law|{ref}", desc, project_title(page)
    stages = rcl_stages(page)
    cur = next((s for s in stages if s.get("state") == "cur"), None) or (stages[-1] if stages else None)
    cur_name = cur["name"] if cur else ""
    if status == "left":
        desc = "opuścił rząd → dalej w Sejmie / Dz.U."
    elif status == "closed":
        desc = "zamknięty (etap rządowy)"
    elif status == "in_gov":
        desc = "w rządzie: " + (cur_name or "—")
    else:
        desc = "stan nieustalony (strona nie odpowiedziała jak zwykle)"
    return f"{status}|{cur_name}", desc, project_title(page)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def main():
    entries = read_watchlist()
    prev = load_state()
    first_run = not prev
    new_state = {}
    proj_started, proj_changes, topic_started, topic_news = [], [], [], []

    projects = [e for e in entries if not e.lower().startswith("temat:")]
    topics = [e for e in entries if e.lower().startswith("temat:")]

    # --- projekty RCL / akty (jak dotychczas) ---
    for entry in projects:
        url = resolve_url(entry)
        if not url:
            if entry in prev:               # nie znaleziono teraz — zachowaj poprzedni stan
                new_state[entry] = prev[entry]
            continue
        sig, desc, title = signature(url)
        if sig is None:                     # pobranie nie wyszło — NIE zgłaszaj zmiany
            if entry in prev:
                new_state[entry] = prev[entry]
            continue
        title = title or entry
        new_state[entry] = {"url": url, "sig": sig, "desc": desc, "title": title}
        if first_run:
            proj_started.append((title, desc, url))
        else:
            old = prev.get(entry)
            if old is None:
                proj_changes.append((title, "(nowy na liście)", desc, url))
            elif old.get("sig") != sig:
                proj_changes.append((title, old.get("desc", "?"), desc, url))

    # --- tematy / dziedziny (nowe akty w Dz.U./M.P. z fraza w tytule) ---
    for entry in topics:
        kw = entry.split(":", 1)[1].strip()
        if not kw:
            continue
        key = "temat:" + kw.lower()
        matches = topic_matches(kw)
        if not matches and key in prev:      # API nie odpowiedziało — zachowaj stan, nie alarmuj
            new_state[key] = prev[key]
            continue
        cur_ids = sorted(matches.keys())
        prevrec = prev.get(key)
        if prevrec is None:                  # nowy temat — wycisz istniejace, pilnuj kolejnych
            new_state[key] = {"topic": kw, "seen": cur_ids}
            topic_started.append((kw, len(cur_ids)))
        else:
            seen = set(prevrec.get("seen") or [])
            new_state[key] = {"topic": kw, "seen": sorted(seen | set(cur_ids))}
            for i in cur_ids:
                if i not in seen:
                    topic_news.append((kw, matches[i]))

    json.dump(new_state, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # --- budowa maila: sklejamy tylko te sekcje, ktore maja tresc ---
    sections, subj_bits = [], []
    if first_run and proj_started:
        sections.append(("Od teraz pilnuję tych projektów (napiszę, gdy zmieni się etap):",
                         [f"• {t}\n  stan: {d}\n  {u}\n" for t, d, u in proj_started]))
        subj_bits.append(f"{len(proj_started)} projekt(ów)")
    if topic_started:
        sections.append(("Od teraz pilnuję nowych aktów w tematach:",
                         [f"• temat „{kw}” — teraz pasuje {n} akt(ów) w Dz.U./M.P.\n" for kw, n in topic_started]))
        subj_bits.append(f"{len(topic_started)} temat(ów)")
    if proj_changes:
        sections.append(("Zmienił się etap obserwowanych projektów:",
                         [f"• {t}\n  było: {o}\n  jest: {nw}\n  {u}\n" for t, o, nw, u in proj_changes]))
        subj_bits.append(f"{len(proj_changes)} zmiana(y)")
    if topic_news:
        rows = []
        for kw, m in topic_news:
            ref = f" — {m['ref']}" if m.get("ref") else ""
            inf = f"\n  wchodzi w życie: {m['inforce']}" if m.get("inforce") else ""
            rows.append(f"• {m['title'] or 'akt'}{ref}\n  (temat „{kw}”){inf}\n  {m['link']}\n")
        sections.append(("Nowe akty w obserwowanych tematach:", rows))
        subj_bits.append(f"{len(topic_news)} nowy akt(ów) w tematach")

    has_mail = bool(sections)
    subject = ("Paragraf: " + " · ".join(subj_bits)) if has_mail else ""
    lines = []
    for head, rows in sections:
        lines.append(head + "\n")
        lines.extend(rows)
    body = ("\n".join(lines).strip() + "\n\n— Kokpit Paragraf") if has_mail else ""

    open(SUBJECT_FILE, "w", encoding="utf-8").write(subject)
    open(BODY_FILE, "w", encoding="utf-8").write(body)

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"changes={'true' if has_mail else 'false'}\n")
            f.write(f"subject={subject}\n")

    print(f"Projekty: {len(projects)} | tematy: {len(topics)} | "
          f"zmiany: {len(proj_changes)} | nowe akty: {len(topic_news)} | "
          f"pierwsze uruchomienie: {first_run} | mail: {has_mail}")


if __name__ == "__main__":
    main()
