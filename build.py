"""Genera index.html de "Disponibilidad del Equipo" (Iglesia Northwood) desde Planning Center Services.

Lee los blockout dates de las personas listadas en equipo.json (equipo pastoral y ministerial),
desde 4 semanas atrás hasta ~6 meses adelante. Los blockouts que se repiten se expanden a fechas.
NO se guarda el motivo ni la descripción del blockout: solo las fechas (y la hora si es parte del día).

Credenciales: variables de entorno PCO_APP_ID y PCO_SECRET (Personal Access Token).
Salida: index.html SIN cifrar. El workflow lo cifra antes de publicarlo; nunca se commitea.
"""
import base64, calendar, json, os, re, sys, time, unicodedata, urllib.error, urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

API = "https://api.planningcenteronline.com/services/v2"
DAYS_BACK = 28
DAYS_AHEAD = 183
TZ = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("DISP_OUT", os.path.join(HERE, "index.html"))
CONFIG = os.environ.get("EQUIPO_PATH", os.path.join(HERE, "equipo.json"))
# Eventos del proyecto "Calendario de Eventos" (repo público). La página los vuelve a leer en vivo al abrirse;
# esta copia es el respaldo por si GitHub no responde.
EVENTOS_URL = os.environ.get("EVENTOS_URL", "https://raw.githubusercontent.com/danielsc1012/calendario-northwood/main/eventos.json")
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
_calls = 0
TOKEN = None


def get(url, allow_404=False):
    global _calls
    if not url.startswith("http"):
        url = API + url
    for attempt in range(8):
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {TOKEN}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                _calls += 1
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After", "20")) + 1)
                continue
            if e.code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            if e.code in (403, 404) and allow_404:
                return None
            raise
        except urllib.error.URLError:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"No se pudo leer {url}")


def paginate(path, allow_404=False):
    url = path
    while url:
        j = get(url, allow_404)
        if j is None:
            return
        yield j
        url = j.get("links", {}).get("next")


# ---------- nombres ----------
SUFIJOS = {"jr", "sr", "ii", "iii", "iv"}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z\s]", " ", s)
    return " ".join(t for t in s.split() if t not in SUFIJOS)


def load_pco_people():
    out = []
    for page in paginate("/people?per_page=100"):
        for p in page["data"]:
            a = p["attributes"]
            fn, ln = a.get("first_name") or "", a.get("last_name") or ""
            variants = [a.get("full_name"), a.get("name"), f"{fn} {ln}",
                        f"{a.get('goes_by_name') or ''} {ln}", f"{a.get('nickname') or ''} {ln}"]
            names = {norm(v) for v in variants if v and len(norm(v).split()) >= 2}
            archived = bool(a.get("archived_at")) or (a.get("status") or "").lower() == "inactive"
            out.append({"id": p["id"], "names": names, "archived": archived})
    return out


def match(persona, pco):
    if persona.get("pco_id"):
        return str(persona["pco_id"]), ""
    targets = [norm(persona["nombre"])] + [norm(a) for a in persona.get("alias", [])]
    tset = set(targets)
    cands = [p for p in pco if p["names"] & tset]
    if not cands:  # flexible: todos los nombres buscados están dentro del nombre de PCO ("Kathya Garro Gonzalez")
        cands = [p for p in pco if any(set(t.split()) <= set(n.split()) for t in targets for n in p["names"])]
    if not cands:
        return None, "no encontrado"
    activos = [p for p in cands if not p["archived"]] or cands
    nota = f"{len(activos)} coincidencias, se usó la primera (fija 'pco_id' en equipo.json)" if len(activos) > 1 else ""
    return activos[0]["id"], nota


# ---------- fechas ----------
def parse_dt(s, tz):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(tz)


def hora(dt):
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


def segment(s, e, tz):
    """[fecha_inicio, fecha_fin (inclusive), hora_salida o '', hora_regreso o ''] en hora local."""
    s, e = parse_dt(s, tz), parse_dt(e, tz)
    full_start = s.hour == 0 and s.minute == 0
    if e > s and e.hour == 0 and e.minute == 0:
        e_eff, full_end = e - timedelta(seconds=1), True
    else:
        e_eff, full_end = e, (e.hour == 23 and e.minute >= 59)
    if e_eff < s:
        e_eff = s
    return [s.date().isoformat(), e_eff.date().isoformat(), "" if full_start else hora(s), "" if full_end else hora(e)]


def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    if d.day > calendar.monthrange(y, m)[1]:
        return None
    return d.replace(year=y, month=m)


def expand(a, tz, limit):
    """Respaldo por si Planning Center no da las fechas de un blockout que se repite."""
    s, e = parse_dt(a["starts_at"], tz).replace(tzinfo=None), parse_dt(a["ends_at"], tz).replace(tzinfo=None)
    m = re.search(r"(\d+)", a.get("repeat_frequency") or "")
    n = int(m.group(1)) if m else 1
    period = (a.get("repeat_period") or "week").lower().rstrip("s")
    until = a.get("repeat_until")
    stop = min(limit, date.fromisoformat(until[:10])) if until else limit
    dur, out, k = e - s, [], 0
    while k < 1000:
        if period.startswith("day"):
            cur = s + timedelta(days=n * k)
        elif period.startswith("month"):
            cur = add_months(s, n * k)
        elif period.startswith("year"):
            cur = add_months(s, 12 * n * k)
        else:
            cur = s + timedelta(weeks=n * k)
        k += 1
        if cur is None:
            continue
        if cur.date() > stop:
            break
        out.append(((cur).replace(tzinfo=tz).isoformat(), (cur + dur).replace(tzinfo=tz).isoformat()))
    return out


def blockouts(pid, start, end):
    segs, avisos = [], []
    for page in paginate(f"/people/{pid}/blockouts?per_page=100", allow_404=True):
        for b in page["data"]:
            a = b["attributes"]
            if not a.get("starts_at") or not a.get("ends_at"):
                continue
            try:
                tz = ZoneInfo(a.get("time_zone") or "America/New_York")
            except Exception:
                tz = TZ
            freq = (a.get("repeat_frequency") or "no_repeat").lower()
            if freq in ("no_repeat", "none", ""):
                occ = [(a["starts_at"], a["ends_at"])]
            else:
                occ = []
                for dp in paginate(f"/people/{pid}/blockouts/{b['id']}/blockout_dates?per_page=100", allow_404=True):
                    for x in dp["data"]:
                        xa = x["attributes"]
                        if xa.get("starts_at") and xa.get("ends_at"):
                            occ.append((xa["starts_at"], xa["ends_at"]))
                if not occ:
                    occ = expand(a, tz, end)
                    avisos.append(f"blockout repetido {b['id']} ({freq}/{a.get('repeat_period')}) expandido localmente")
            for s, e in occ:
                seg = segment(s, e, tz)
                if seg[1] >= start.isoformat() and seg[0] <= end.isoformat():
                    segs.append(seg)
    segs = sorted({tuple(x) for x in segs})
    return [list(x) for x in segs], avisos


def load_events():
    try:
        with urllib.request.urlopen(urllib.request.Request(EVENTOS_URL, headers={"Accept": "application/json"}), timeout=30) as r:
            j = json.load(r)
        keep = ("nombre", "inicio", "fin", "publico", "grupo", "tentativo")
        evs = [{k: e[k] for k in keep if k in e} for e in j.get("eventos", []) if e.get("inicio")]
        print(f"Eventos del Calendario de Eventos: {len(evs)}")
        return evs, j.get("actualizado", "")
    except Exception as e:
        print(f"aviso · no se pudieron leer los eventos ({EVENTOS_URL}): {e}")
        return [], ""


def main():
    global TOKEN
    TOKEN = base64.b64encode(f"{os.environ['PCO_APP_ID']}:{os.environ['PCO_SECRET']}".encode()).decode()
    today = datetime.now(TZ).date()
    events, ev_upd = load_events()
    start, end = today - timedelta(days=DAYS_BACK), today + timedelta(days=DAYS_AHEAD)
    last_ev = max((date.fromisoformat(e.get("fin") or e["inicio"]) for e in events), default=end)
    end = min(max(end, last_ev), today + timedelta(days=400))   # cubrir todo el año de eventos

    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    pco = load_pco_people()
    print(f"Personas en Planning Center Services: {len(pco)}")

    groups, people, found = [], [], 0
    for gi, g in enumerate(cfg["grupos"]):
        groups.append(g["nombre"])
        for persona in g.get("personas", []):
            pid, nota = match(persona, pco)
            entry = {"n": persona["nombre"], "g": gi, "ok": bool(pid), "b": []}
            if pid:
                found += 1
                entry["b"], avisos = blockouts(pid, start, end)
                for av in avisos:
                    print(f"  aviso · {persona['nombre']}: {av}")
            print(f"  {g['nombre']} · {persona['nombre']}: "
                  + (f"{len(entry['b'])} blockouts" if pid else "NO ENCONTRADO") + (f" ({nota})" if nota and pid else ""))
            people.append(entry)

    data = {"gen": today.isoformat(), "start": start.isoformat(), "end": end.isoformat(), "groups": groups, "p": people,
            "ev": events, "evUpd": ev_upd, "evUrl": EVENTOS_URL}
    updated = f"{today.day} de {MESES[today.month - 1]} de {today.year}"
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        tpl = f.read()
    html = tpl.replace("__UPDATED__", updated).replace(
        "__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"index.html generado: {found}/{len(people)} personas encontradas, {_calls} consultas.")
    if not found:
        sys.exit("No se encontró a nadie: revisa las credenciales, los permisos del token o los nombres en equipo.json.")


if __name__ == "__main__":
    main()
