/*
  Paragraf - Cloudflare Worker (warstwa danych poza GitHub Pages)
  =================================================================
  Po co: GitHub Pages to statyczne pliki, wiec przegladarka nie moze
  pobrac CBOSA (CORS + blokada botow). Ten Worker robi to PO STRONIE
  SERWERA - bez CORS, z naglowkami jak przegladarka - i oddaje JSON.

  Dwa endpointy:
    /proxy?url=<ENCODED>   - uniwersalny proxy (mozesz nim zastapic
                             flaky publiczne proxy dla SAOS/NBP/Sejm/RCL!)
    /cbosa?q=...&od=...&do=...&typ=...   - wyszukiwarka CBOSA -> JSON

  Wdrozenie (bez wiersza polecen):
    1. Zaloz darmowe konto na https://dash.cloudflare.com
    2. Workers & Pages -> Create -> Worker -> nadaj nazwe -> Deploy
    3. Edit code -> skasuj domyslny kod -> wklej CALY ten plik -> Deploy
    4. Skopiuj adres (np. https://paragraf.twojnick.workers.dev)
    5. Sprawdz w przegladarce:
         .../cbosa?q=VAT&raw=1   <- zobaczysz surowy HTML z CBOSA
         .../cbosa?q=VAT         <- zobaczysz JSON (lista linkow)
    6. Przyslij mi ten adres + wynik z ?raw=1 - dostroje parser i podepne
       go do aplikacji (przycisk CBOSA zacznie pokazywac wyniki na stronie).

  Limity darmowego planu Cloudflare: 100 000 zapytan/dzien - z zapasem.
  Uwaga prawna: CBOSA to baza "do celow informacyjnych i edukacyjnych".
  Trzymaj rozsadny rate-limit, nie zarzucaj serwera ruchem.
*/

const ALLOW_ORIGIN = "*"; // mozesz zawezic, np. "https://kalumatix-tech.github.io"

const CORS = {
  "Access-Control-Allow-Origin": ALLOW_ORIGIN,
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export default {
  async fetch(request) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    const url = new URL(request.url);

    // --- uniwersalny proxy: /proxy?url=ENCODED ---
    if (url.pathname === "/proxy") {
      const target = url.searchParams.get("url");
      if (!target) return json({ error: "brak parametru url" }, 400);
      if (!/^https?:\/\//.test(target)) return json({ error: "url musi byc http(s)" }, 400);
      try {
        const r = await fetch(target, { headers: browserHeaders() });
        const body = await r.text();
        return new Response(body, {
          headers: { ...CORS, "Content-Type": r.headers.get("content-type") || "text/plain; charset=utf-8" },
        });
      } catch (e) {
        return json({ error: String(e) }, 502);
      }
    }

    // --- CBOSA: /cbosa?q=...&od=YYYY-MM-DD&do=YYYY-MM-DD&typ=Wyrok&raw=1 ---
    if (url.pathname === "/cbosa") {
      const q = url.searchParams.get("q") || "";
      const od = url.searchParams.get("od") || "";
      const doo = url.searchParams.get("do") || "";
      const typ = url.searchParams.get("typ") || "";
      const raw = url.searchParams.get("raw") === "1";
      try {
        // CBOSA wyszukuje formularzem POST na /cbo/query.
        // UWAGA: dokladne nazwy pol potwierdzimy na zywo (devtools -> Network
        // przy realnym wyszukiwaniu). Ponizej najczestszy uklad SoftProdukt.
        const form = new URLSearchParams();
        form.set("p_search", q);
        if (typ) form.set("p_judgmentType", typ);
        if (od) form.set("p_dateFrom", od);
        if (doo) form.set("p_dateTo", doo);

        const r = await fetch("https://orzeczenia.nsa.gov.pl/cbo/query", {
          method: "POST",
          headers: { ...browserHeaders(), "Content-Type": "application/x-www-form-urlencoded" },
          body: form.toString(),
        });
        const html = await r.text();

        if (raw) {
          return new Response(html, { headers: { ...CORS, "Content-Type": "text/html; charset=utf-8" } });
        }
        return json({
          ok: true,
          status: r.status,
          count: undefined,
          items: parseCbosa(html),
          note: "Parser pierwszego podejscia - jesli items puste, sprawdz ?raw=1 i przyslij HTML, dostroje selektory.",
        });
      } catch (e) {
        return json({ error: String(e) }, 502);
      }
    }

    return json({ ok: true, routes: ["/cbosa?q=...&od=...&do=...&typ=...", "/proxy?url=..."] });
  },
};

function browserHeaders() {
  return {
    "User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.6",
  };
}

/*
  Pierwsze podejscie do parsowania listy wynikow CBOSA.
  Wyniki CBOSA linkuja do dokumentow pod /doc/<HASH>. Wyciagamy te linki
  (sygnatura jest tekstem linku). Po wdrozeniu dostroimy to do realnego HTML.
*/
function parseCbosa(html) {
  const items = [];
  const seen = new Set();
  const re = /<a[^>]+href="(\/doc\/[A-Za-z0-9]+)"[^>]*>([\s\S]*?)<\/a>/gi;
  let m;
  while ((m = re.exec(html)) && items.length < 25) {
    const href = m[1];
    if (seen.has(href)) continue;
    seen.add(href);
    const label = decodeEntities(m[2].replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim());
    items.push({
      link: "https://orzeczenia.nsa.gov.pl" + href,
      caseNumber: label,
    });
  }
  return items;
}

function decodeEntities(s) {
  return String(s)
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&nbsp;/g, " ")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n));
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj, null, 2), {
    status,
    headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" },
  });
}
