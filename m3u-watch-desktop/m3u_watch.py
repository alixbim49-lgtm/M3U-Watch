#!/usr/bin/env python3
"""
m3u_watch.py - Surveille une ou plusieurs playlists M3U et renvoie les changements
(ajouts, suppressions, modifications, avec jaquette/logo) sous forme de JSON.

Le script ne planifie rien et n'envoie rien : c'est Home Assistant qui le declenche
(automation) et qui utilise le resultat comme il veut (Telegram, notification...).

Modes (sans dependance externe, bibliotheque standard uniquement) :
  --serve    petit serveur HTTP : GET /check[?playlist=Nom][&preview=1]   (add-on)
  --check    une verification, JSON sur la sortie standard                (autonome)
             option --preview : compare sans enregistrer le nouvel etat
  --test     teste un lien M3U (diagnostic de connexion), sans rien configurer
             python m3u_watch.py --test            (le lien est demande)
             python m3u_watch.py --test "http://..."
             option --detail : diagnostic reseau complet (DNS, connexion, en-tetes, proxy)

Si le lien est de type Xtream (get.php?username=...&password=...) et que le fichier M3U est
vide ou indisponible, le programme lit automatiquement le catalogue par l'API du fournisseur
(player_api.php : chaines, films, series, avec jaquettes).

Configuration : /data/options.json (add-on Home Assistant) ou config.json
(a cote du script, cree au premier lancement ; un autre chemin peut etre donne).
"""
import gzip
import hashlib
import json
import platform
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ADDON_OPTIONS = Path("/data/options.json")
ADDON_STATE_DIR = Path("/data/state")
BASE = Path(__file__).resolve().parent
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
LOCK = threading.Lock()

DEFAULT_CONFIG = {
    "max_items": 20,
    "port": 8099,
    "user_agent": "VLC/3.0.20 LibVLC/3.0.20",
    "timeout": 120,
    "max_series_checks": 30,
    "playlists": [
        {"name": "Ma playlist", "url": "https://exemple.com/playlist.m3u"},
    ],
}


SETTINGS = {"user_agent": DEFAULT_CONFIG["user_agent"], "timeout": DEFAULT_CONFIG["timeout"],
            "max_series_checks": DEFAULT_CONFIG["max_series_checks"]}


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), file=sys.stderr, flush=True)


# ----------------------------------------------------------------- config --
def load_config(args):
    """Retourne (config, dossier_etat)."""
    if ADDON_OPTIONS.exists():
        cfg = json.loads(ADDON_OPTIONS.read_text(encoding="utf-8"))
        state_dir = ADDON_STATE_DIR
    else:
        paths = [a for a in args if not a.startswith("--")]
        path = Path(paths[0]) if paths else BASE / "config.json"
        if not path.exists():
            path.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
            print("Fichier de config cree : %s\nRemplis-le puis relance le script." % path, file=sys.stderr)
            sys.exit(1)
        cfg = json.loads(path.read_text(encoding="utf-8"))
        state_dir = path.resolve().parent / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return {**DEFAULT_CONFIG, **cfg}, state_dir


def validate(cfg):
    problems = []
    playlists = cfg.get("playlists") or []
    if not playlists:
        problems.append("aucune playlist configuree")
    for i, pl in enumerate(playlists, 1):
        url = str(pl.get("url", ""))
        if not url.startswith(("http://", "https://")) or "exemple.com" in url:
            problems.append("playlist %d : URL manquante ou a remplacer" % i)
    return problems


# ------------------------------------------------------------------- m3u ---
def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": SETTINGS["user_agent"]})
    with urllib.request.urlopen(req, timeout=SETTINGS["timeout"]) as r:
        data = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            data = gzip.decompress(data)
        return data.decode("utf-8", errors="replace")


def parse_m3u(text):
    """Retourne un dict {cle: {id, name, group, logo, url}}."""
    entries, current = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            attrs = dict(ATTR_RE.findall(line))
            name = line.rsplit(",", 1)[-1].strip() if "," in line else ""
            logo = attrs.get("tvg-logo") or attrs.get("logo") or ""
            if not logo.startswith(("http://", "https://")):
                logo = ""
            current = {
                "id": attrs.get("tvg-id", ""),
                "name": name or attrs.get("tvg-name", ""),
                "group": attrs.get("group-title", ""),
                "logo": logo,
            }
        elif line and not line.startswith("#") and current is not None:
            current["url"] = line
            key = current["id"] or current["name"] or line
            base, n = key, 2
            while key in entries:  # doublons
                key = "%s#%d" % (base, n)
                n += 1
            entries[key] = current
            current = None
    return entries


def change_reasons(o, n):
    reasons = []
    if o.get("url") != n.get("url"):
        reasons.append("URL modifiée")
    if o.get("group", "") != n.get("group", ""):
        reasons.append("groupe : %s → %s" % (o.get("group") or "—", n.get("group") or "—"))
    return reasons


def diff(old, new):
    added = [new[k] for k in new if k not in old]
    removed = [old[k] for k in old if k not in new]
    changed = []
    for k in new:
        if k in old:
            reasons = change_reasons(old[k], new[k])
            if reasons:
                changed.append((old[k], new[k], reasons))
    return added, removed, changed


# ------------------------------------------------------------- API Xtream --
XTREAM_KINDS = (
    ("live", "get_live_categories", "get_live_streams", "stream_id", "stream_icon"),
    ("vod", "get_vod_categories", "get_vod_streams", "stream_id", "stream_icon"),
    ("series", "get_series_categories", "get_series", "series_id", "cover"),
)
KIND_NAMES = {"live": "chaînes", "vod": "films", "series": "séries"}
KIND_LABELS = {"live": "chaîne", "vod": "film", "series": "série"}


def xtream_creds(url):
    """(base, utilisateur, mot de passe) si le lien est un get.php Xtream, sinon None."""
    p = urllib.parse.urlsplit(url)
    d = dict(urllib.parse.parse_qsl(p.query))
    if p.path.endswith("get.php") and d.get("username") and d.get("password"):
        base = urllib.parse.urlunsplit(p._replace(path=p.path[:-len("get.php")], query="", fragment=""))
        return base, d["username"], d["password"]
    return None


def xtream_call(base, user, pwd, action=None, params=None):
    q = {"username": user, "password": pwd}
    if action:
        q["action"] = action
    q.update(params or {})
    data = lire(base + "player_api.php?" + urllib.parse.urlencode(q), SETTINGS["user_agent"])
    if not data.strip():
        raise ValueError("réponse vide")
    return json.loads(data.decode("utf-8", errors="replace"))


def entries_from_xtream(base, user, pwd):
    """Meme format que parse_m3u, construit depuis l'API (chaines, films, series)."""
    entries = {}
    for kind, a_cat, a_str, id_key, logo_key in XTREAM_KINDS:
        cats = xtream_call(base, user, pwd, a_cat)
        items = xtream_call(base, user, pwd, a_str)
        if not isinstance(items, list):  # categorie non proposee par le fournisseur
            continue
        names = {}
        if isinstance(cats, list):
            names = {str(c.get("category_id")): c.get("category_name", "") for c in cats if isinstance(c, dict)}
        for it in items:
            if not isinstance(it, dict) or it.get(id_key) is None:
                continue
            iid = it[id_key]
            logo = str(it.get(logo_key) or "")
            if not logo.startswith(("http://", "https://")):
                logo = ""
            ext = it.get("container_extension") or ""
            entries["%s:%s" % (kind, iid)] = {
                "id": str(iid),
                "kind": kind,
                "name": str(it.get("name") or ""),
                "group": names.get(str(it.get("category_id")), ""),
                "logo": logo,
                "url": "%s/%s%s" % (kind, iid, ("." + ext) if ext else ""),
            }
            if kind == "series":
                entries["series:%s" % iid]["modified"] = str(it.get("last_modified") or "")
    return entries


def fetch_entries(url):
    """Retourne (entrees, source) ; source vaut 'm3u' ou 'api'."""
    err = None
    try:
        entries = parse_m3u(download(url))
        if entries:
            return entries, "m3u"
    except Exception as e:
        err = e
    cred = xtream_creds(url)
    if cred:
        log("M3U indisponible ou vide : lecture par l'API du fournisseur")
        return entries_from_xtream(*cred), "api"
    if err:
        raise err
    return {}, "m3u"


def parse_episodes(info):
    """{saison: {episode: date_ajout_ou_None}} a partir de get_series_info."""
    raw = info.get("episodes") if isinstance(info, dict) else None
    eps = {}

    def add(season, ep):
        try:
            sn = int(ep.get("season") or season)
            n = int(ep.get("episode_num"))
        except (TypeError, ValueError):
            return
        try:
            added = int(ep.get("added"))
        except (TypeError, ValueError):
            added = None
        eps.setdefault(sn, {})[n] = added

    if isinstance(raw, dict):
        for season, lst in raw.items():
            for ep in (lst or []):
                if isinstance(ep, dict):
                    add(season, ep)
    elif isinstance(raw, list):
        for lst in raw:
            for ep in (lst if isinstance(lst, list) else [lst]):
                if isinstance(ep, dict):
                    add(0, ep)
    return eps


def fmt_runs(nums):
    nums = sorted(nums)
    runs, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        runs.append((start, prev))
        start = prev = n
    runs.append((start, prev))
    return ", ".join(("E%02d" % a) if a == b else ("E%02d-E%02d" % (a, b)) for a, b in runs)


def label_eps(fresh, new_seasons):
    parts = []
    for sn in sorted(fresh):
        txt = "S%02d %s" % (sn, fmt_runs(fresh[sn]))
        if sn in new_seasons:
            txt += " (nouvelle saison)"
        parts.append(txt)
    return " ; ".join(parts)


def detect_series_updates(cred, old, new, name, limit_checks, max_items):
    """Nouveaux episodes / saisons des series dont la date de modification a change.

    Retourne (elements, total). Complete `new` avec la liste d'episodes connue
    (reference pour la comparaison suivante)."""
    base, user, pwd = cred
    for k, e in new.items():  # reprise des references d'episodes deja connues
        if e.get("kind") == "series" and k in old and "episodes" in old[k]:
            e["episodes"] = old[k]["episodes"]
    candidates = [k for k, e in new.items()
                  if e.get("kind") == "series" and k in old and e.get("modified")
                  and old[k].get("modified") and e["modified"] != old[k]["modified"]]
    items, total = [], 0
    for i, k in enumerate(candidates):
        e, o = new[k], old[k]
        if i >= limit_checks:
            e["modified"] = o["modified"]  # on reessaiera au prochain passage
            continue
        try:
            eps = parse_episodes(xtream_call(base, user, pwd, "get_series_info", {"series_id": e["id"]}))
        except Exception as ex:
            log("« %s » : détail de la série %s impossible (%s)" % (name, e["name"], ex))
            e["modified"] = o["modified"]
            continue
        known = o.get("episodes")
        fresh = {}
        if known is not None:  # comparaison exacte avec la liste d'episodes connue
            for sn, v in eps.items():
                seen = set(known.get(str(sn), []))
                nouveaux = sorted(n for n in v if n not in seen)
                if nouveaux:
                    fresh[sn] = nouveaux
            new_seasons = [sn for sn in fresh if not known.get(str(sn))]
        else:  # pas de reference : on se base sur la date d'ajout des episodes, si elle existe
            try:
                since = int(o["modified"])
            except ValueError:
                since = None
            if since is not None:
                for sn, v in eps.items():
                    recents = sorted(n for n, ts in v.items() if ts is not None and ts > since)
                    if recents:
                        fresh[sn] = recents
            new_seasons = [sn for sn in fresh if len(fresh[sn]) == len(eps[sn])]
        e["episodes"] = {str(sn): sorted(v) for sn, v in eps.items()}
        if fresh:
            total += 1
            if len(items) < max_items:
                items.append({
                    "playlist": name, "name": e["name"], "logo": e.get("logo", ""), "kind": "series",
                    "label": label_eps(fresh, new_seasons),
                    "episodes_count": sum(len(v) for v in fresh.values()),
                    "seasons": sorted(fresh), "new_seasons": sorted(new_seasons),
                })
    return items, total


def save_state(sp, entries, src):
    sp.write_text(json.dumps(dict(entries, __source__=src), ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------ verification --
def state_path(state_dir, pl):
    # Lie a l'URL : renommer la playlist garde son historique.
    key = str(pl.get("state_key") or pl["url"])
    return state_dir / ("playlist-%s.json" % hashlib.sha1(key.encode()).hexdigest()[:12])


KIND_WORDS_SERIES = ("serie", "série", "saison", "season", "episode", "épisode")
KIND_WORDS_VOD = ("vod", "film", "cinema", "cinéma", "movie")


def kind_of(e):
    """'live', 'vod' ou 'series' (depuis l'API, sinon deduit de l'URL ou du groupe)."""
    k = e.get("kind")
    if k in ("live", "vod", "series"):
        return k
    path = urllib.parse.urlsplit(e.get("url", "") or "").path.lower()
    if "/movie/" in path:
        return "vod"
    if "/series/" in path:
        return "series"
    g = (e.get("group") or "").lower()
    if any(w in g for w in KIND_WORDS_SERIES):
        return "series"
    if any(w in g for w in KIND_WORDS_VOD):
        return "vod"
    return "live"


def count_kinds(entries):
    out = {"live": 0, "vod": 0, "series": 0}
    for e in entries:
        out[kind_of(e)] += 1
    return out


def public(e, playlist):
    """Ce qui est expose a Home Assistant : jamais l'URL du flux."""
    return {"playlist": playlist, "name": e.get("name", ""), "group": e.get("group", ""),
            "logo": e.get("logo", ""), "kind": kind_of(e)}


def new_result(name):
    return {"name": name, "status": "ok", "error": None, "total": 0, "previous_total": None,
            "has_changes": False, "added_count": 0, "removed_count": 0, "changed_count": 0,
            "added": [], "removed": [], "changed": [], "series_updates": [], "series_updates_count": 0,
            "totals_by_kind": {}, "added_by_kind": {}, "removed_by_kind": {},
            "summary": "", "message": ""}


def fmt_basic(e):
    kind = (" (%s)" % KIND_LABELS[e["kind"]]) if e.get("kind") in KIND_LABELS else ""
    return "• %s%s%s" % (e["name"], (" [%s]" % e["group"]) if e.get("group") else "", kind)


def fmt_series(e):
    return "• %s : %s" % (e["name"], e["label"])


def fmt_changed(e):
    return "• %s (%s)" % (e["name"], ", ".join(e["reasons"]))


def section(title, items, count, fmt):
    if not count:
        return ""
    out = "\n%s (%d)\n" % (title, count)
    for it in items:
        out += fmt(it) + "\n"
    if count > len(items):
        out += "… et %d autres\n" % (count - len(items))
    return out


def _commit(sp, new, src, defer):
    """Enregistre l'etat, ou le met de cote (defer) pour l'enregistrer apres l'envoi."""
    if defer is not None:
        defer.append((sp, new, src))
    else:
        save_state(sp, new, src)


def check_playlist(pl, name, state_dir, max_items, preview, defer=None):
    res = new_result(name)
    sp = state_path(state_dir, pl)

    try:
        new, src = fetch_entries(pl["url"])
    except Exception as e:
        res.update(status="error", error="téléchargement impossible (%s)" % e)
        res["message"] = res["summary"] = "⚠️ %s : impossible de récupérer la playlist (%s)" % (name, e)
        return res
    if not new:  # page d'erreur, fichier vide... on garde l'ancien etat
        res.update(status="error", error="playlist vide ou invalide")
        res["message"] = res["summary"] = "⚠️ %s : playlist vide ou invalide, aucune comparaison faite." % name
        return res
    res["total"] = len(new)
    res["totals_by_kind"] = count_kinds(new.values())

    if not sp.exists():
        if not preview:
            _commit(sp, new, src, defer)
        res["status"] = "initialized"
        res["message"] = res["summary"] = "✅ %s : surveillance initialisée, %d entrées enregistrées." % (name, len(new))
        return res

    old = json.loads(sp.read_text(encoding="utf-8"))
    old_src = old.pop("__source__", "m3u")
    if old_src != src:  # la source a change (M3U <-> API) : on repart d'une base propre
        if not preview:
            _commit(sp, new, src, defer)
        res["status"] = "initialized"
        res["message"] = res["summary"] = "ℹ️ %s : source modifiée (%s → %s), %d entrées enregistrées." % (
            name, old_src, src, len(new))
        return res
    added, removed, changed = diff(old, new)
    res["previous_total"] = len(old)
    res["added_by_kind"], res["removed_by_kind"] = count_kinds(added), count_kinds(removed)
    res["added_count"], res["removed_count"], res["changed_count"] = len(added), len(removed), len(changed)
    res["added"] = [public(e, name) for e in added[:max_items]]
    res["removed"] = [public(e, name) for e in removed[:max_items]]
    res["changed"] = [dict(public(n, name), reasons=r) for (_o, n, r) in changed[:max_items]]
    if src == "api" and xtream_creds(pl["url"]):
        res["series_updates"], res["series_updates_count"] = detect_series_updates(
            xtream_creds(pl["url"]), old, new, name, SETTINGS["max_series_checks"], max_items)
    res["has_changes"] = bool(added or removed or changed or res["series_updates_count"])

    if res["has_changes"]:
        msg = "📺 %s – changements du %s\n" % (name, datetime.now().strftime("%d/%m/%Y %H:%M"))
        msg += section("➕ Ajoutés", res["added"], len(added), fmt_basic)
        msg += section("➖ Supprimés", res["removed"], len(removed), fmt_basic)
        msg += section("✏️ Modifiés", res["changed"], len(changed), fmt_changed)
        msg += section("🆕 Nouveaux épisodes", res["series_updates"], res["series_updates_count"], fmt_series)
        msg += "\nTotal : %d → %d" % (len(old), len(new))
        res["message"] = msg
        res["summary"] = "📺 %s : ➕ %d  ➖ %d  ✏️ %d" % (name, len(added), len(removed), len(changed))
        if res["series_updates_count"]:
            res["summary"] += "  🆕 %d" % res["series_updates_count"]
    if not preview:
        _commit(sp, new, src, defer)
    return res


def run_check(cfg, state_dir, only=None, preview=False):
    max_items = int(cfg["max_items"])
    results = []
    for i, pl in enumerate(cfg["playlists"], 1):
        name = (pl.get("name") or "").strip() or "Playlist %d" % i
        if only and name.lower() != only.lower():
            continue
        try:
            res = check_playlist(pl, name, state_dir, max_items, preview)
        except Exception as e:  # une playlist en echec ne bloque pas les autres
            res = new_result(name)
            res.update(status="error", error=str(e))
            res["message"] = res["summary"] = "⚠️ %s : erreur (%s)" % (name, e)
        log("« %s » : %s" % (name, res["status"] if not res["has_changes"] else "changements détectés"))
        results.append(res)

    out = {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "error": ("playlist « %s » inconnue" % only) if (only and not results) else None,
        "has_changes": any(r["has_changes"] for r in results),
        "has_errors": any(r["status"] == "error" for r in results),
        "added_count": sum(r["added_count"] for r in results),
        "removed_count": sum(r["removed_count"] for r in results),
        "changed_count": sum(r["changed_count"] for r in results),
        "added": [x for r in results for x in r["added"]],
        "removed": [x for r in results for x in r["removed"]],
        "changed": [x for r in results for x in r["changed"]],
        "series_updates": [x for r in results for x in r["series_updates"]],
        "series_updates_count": sum(r["series_updates_count"] for r in results),
        "summary": "\n".join(r["summary"] for r in results if r["summary"]),
        "message": "\n\n".join(r["message"] for r in results if r["message"]),
    }
    light = ("added", "removed", "changed", "series_updates")
    out["playlists"] = [{k: v for k, v in r.items() if k not in light} for r in results]
    return out


# --------------------------------------------------------------------- test --
TEST_USER_AGENTS = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "navigateur"),
    ("VLC/3.0.20 LibVLC/3.0.20", "VLC"),
    ("okhttp/4.12.0", "application Android"),
    ("Kodi/21.0 (Windows NT 10.0; Win64; x64) App_Bitness/64 Version/21.0", "Kodi"),
]


def masque(url):
    """Affiche le lien sans identifiants ni parametres (ils sont souvent secrets)."""
    p = urllib.parse.urlsplit(url)
    host = p.hostname or "?"
    if p.port:
        host += ":%d" % p.port
    return "%s://%s%s%s" % (p.scheme, host, p.path, "?…" if p.query else "")


def explique_erreur(e):
    if isinstance(e, urllib.error.HTTPError):
        c = e.code
        if c in (401, 403):
            return ("HTTP %d : accès refusé (abonnement expiré, identifiants incorrects, trop de connexions "
                    "simultanées, ou adresse IP / identifiant bloqué par le fournisseur)" % c)
        if c == 404:
            return "HTTP 404 : lien introuvable (lien incomplet ou modifié ?)"
        if c == 407:
            return "HTTP 407 : un proxy d'entreprise demande une authentification"
        if c == 429:
            return "HTTP 429 : trop de requêtes, le fournisseur limite les appels"
        if c >= 500:
            return "HTTP %d : problème côté serveur du fournisseur" % c
        return "HTTP %d" % c
    reason = getattr(e, "reason", e)
    if isinstance(reason, socket.gaierror):
        return "nom du serveur introuvable (DNS) : vérifie le lien, la connexion Internet, ou un VPN/filtrage DNS"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "délai dépassé : serveur injoignable ou trop lent (pare-feu, réseau d'entreprise/école ?)"
    if isinstance(reason, ConnectionRefusedError):
        return "connexion refusée (port fermé ou bloqué par un pare-feu)"
    if isinstance(reason, ConnectionResetError):
        return "connexion coupée par le serveur (souvent un blocage du fournisseur ou d'un pare-feu)"
    if isinstance(reason, ssl.SSLError) or "CERTIFICATE" in str(reason).upper():
        return "problème de certificat HTTPS : essaie le même lien en http:// au lieu de https://"
    return "erreur : %s" % reason


SAFE_HEADERS = ("Server", "Content-Type", "Content-Length", "Location", "WWW-Authenticate",
                "Retry-After", "Via", "X-Powered-By")


SECRET_KEYS = ("user", "pass", "pwd", "token", "key", "secret", "auth", "login", "mac")


def extrait(texte, url, n=300):
    """Debut de la reponse, avec les identifiants du lien remplaces par ***."""
    out = " ".join(texte.split())
    p = urllib.parse.urlsplit(url)
    secrets = [p.username, p.password]
    for k, vals in urllib.parse.parse_qs(p.query).items():
        if any(w in k.lower() for w in SECRET_KEYS):
            secrets += vals
    for v in secrets:
        if v and len(v) >= 3:
            out = out.replace(v, "***")
    return out[:n] + ("…" if len(out) > n else "")


def detail_http(e):
    """En-tetes utiles de la reponse du serveur (jamais le contenu de la page)."""
    print("      code : %s %s" % (e.code, e.reason))
    for h in SAFE_HEADERS:
        v = e.headers.get(h) if e.headers else None
        if v:
            if h == "Location" and v.startswith("http"):
                v = masque(v)
            print("      %s : %s" % (h, v[:150]))


def diagnostic_reseau(url):
    p = urllib.parse.urlsplit(url)
    host = p.hostname
    port = p.port or (443 if p.scheme == "https" else 80)
    print("\nDiagnostic réseau détaillé")
    print("  Python %s | %s | %s" % (sys.version.split()[0], platform.platform(), ssl.OPENSSL_VERSION))
    proxies = urllib.request.getproxies()
    txt = ", ".join("%s → %s" % (k, masque(v) if "://" in v else v) for k, v in proxies.items())
    print("  Proxy système : %s" % (txt or "aucun"))
    try:
        ips = sorted({i[4][0] for i in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
        print("  DNS : %s → %s" % (host, ", ".join(ips)))
    except Exception as e:
        print("  DNS : échec (%s)" % e)
        return
    try:
        t = time.time()
        with socket.create_connection((host, port), timeout=15):
            pass
        print("  Connexion TCP au port %d : OK (%.0f ms)" % (port, (time.time() - t) * 1000))
    except Exception as e:
        print("  Connexion TCP au port %d : échec (%s)" % (port, e))
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=8):
            print("  Accès Internet général (1.1.1.1:443) : OK")
    except Exception as e:
        print("  Accès Internet général : échec (%s)" % e)


def variantes_xtream(url):
    """Autres formats du meme lien get.php (type / output), sans l'original."""
    p = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    d = dict(q)
    if not p.path.endswith("get.php") or "username" not in d or "password" not in d:
        return []
    base = [(k, v) for k, v in q if k not in ("type", "output")]
    out = []
    for typ, outp in (("m3u_plus", "ts"), ("m3u_plus", "m3u8"), ("m3u_plus", None), ("m3u", "ts"), ("m3u", None)):
        if typ == d.get("type") and outp == d.get("output"):
            continue
        params = base + [("type", typ)] + ([("output", outp)] if outp else [])
        label = "type=%s%s" % (typ, ("&output=" + outp) if outp else " (sans output)")
        out.append((label, urllib.parse.urlunsplit(p._replace(query=urllib.parse.urlencode(params)))))
    return out


def lire(url, ua):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=SETTINGS["timeout"]) as r:
        data = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            data = gzip.decompress(data)
    return data


def chercher_variante(url):
    variantes = variantes_xtream(url)
    if not variantes:
        return None, None
    print("\nEssai d'autres formats du même lien (identifiant VLC) :")
    for label, u in variantes:
        print("→ %s ... " % label, end="", flush=True)
        try:
            data = lire(u, TEST_USER_AGENTS[1][0])
        except Exception as e:
            print("échec : " + explique_erreur(e))
            continue
        texte = data.decode("utf-8", errors="replace")
        if "#EXTM3U" in texte[:2000] or "#EXTINF" in texte[:5000]:
            print("OK")
            return texte, label
        print("pas une playlist (%s)" % ("réponse vide" if not data else "%d octets" % len(data)))
    return None, None


def diagnostic_xtream(url):
    """Interroge l'API du compte (player_api.php) : statut, expiration, connexions."""
    p = urllib.parse.urlsplit(url)
    d = dict(urllib.parse.parse_qsl(p.query))
    if not p.path.endswith("get.php") or "username" not in d or "password" not in d:
        return
    api = urllib.parse.urlunsplit(p._replace(
        path=p.path[:-len("get.php")] + "player_api.php",
        query=urllib.parse.urlencode({"username": d["username"], "password": d["password"]})))
    print("\nVérification du compte auprès du fournisseur (player_api.php) ...")
    try:
        data = lire(api, TEST_USER_AGENTS[1][0])
    except Exception as e:
        print("  échec : " + explique_erreur(e))
        return
    if not data.strip():
        print("  réponse vide : le serveur ne donne aucune information sur le compte.")
        return
    try:
        ui = json.loads(data.decode("utf-8", errors="replace")).get("user_info") or {}
    except Exception:
        print("  réponse illisible (%d octets, ce n'est pas du JSON attendu)." % len(data))
        return
    if not ui:
        print("  la réponse ne contient pas d'informations de compte.")
        return
    print("  auth (identifiants valides)  : %s" % ui.get("auth"))
    print("  statut                       : %s" % ui.get("status"))
    exp = ui.get("exp_date")
    try:
        dt = datetime.fromtimestamp(int(exp))
        print("  expiration                   : %s%s" % (dt.strftime("%d/%m/%Y"), "  ← EXPIRÉ" if dt < datetime.now() else ""))
    except (TypeError, ValueError):
        print("  expiration                   : %s" % ("illimitée" if not exp else exp))
    print("  connexions (actives / max)   : %s / %s" % (ui.get("active_cons"), ui.get("max_connections")))
    if ui.get("allowed_output_formats"):
        print("  formats de sortie autorisés  : %s" % ", ".join(map(str, ui["allowed_output_formats"])))


def tester_api(url):
    cred = xtream_creds(url)
    if not cred:
        return False
    print("\nEssai de lecture par l'API du fournisseur (sans passer par get.php) :")
    total, exemples, series_items = 0, [], []
    for kind, a_cat, a_str, id_key, logo_key in XTREAM_KINDS:
        print("→ %s ... " % KIND_NAMES[kind], end="", flush=True)
        try:
            items = xtream_call(*cred, a_str)
        except Exception as e:
            print("échec : " + explique_erreur(e))
            continue
        if not isinstance(items, list):
            print("non proposé par le fournisseur")
            continue
        logos = sum(1 for i in items if isinstance(i, dict) and str(i.get(logo_key) or "").startswith("http"))
        print("%d éléments, %d avec jaquette" % (len(items), logos))
        total += len(items)
        exemples += [str(i.get("name", "")) for i in items[:2] if isinstance(i, dict)]
        if kind == "series":
            series_items = [i for i in items if isinstance(i, dict)]
    if series_items:
        datees = sum(1 for i in series_items if str(i.get("last_modified") or "") not in ("", "0"))
        print("   Séries avec une date de dernière modification : %d / %d" % (datees, len(series_items)))
        exemple = next((i for i in series_items if i.get("series_id") is not None), None)
        if exemple:
            try:
                eps = parse_episodes(xtream_call(*cred, "get_series_info", {"series_id": exemple["series_id"]}))
                nb = sum(len(v) for v in eps.values())
                dates = sum(1 for v in eps.values() for ts in v.values() if ts is not None)
                print("   Détail d'une série : %d saison(s), %d épisode(s), %d avec date d'ajout" % (len(eps), nb, dates))
            except Exception as e:
                print("   Détail d'une série : échec (%s)" % explique_erreur(e))
    if total:
        print("   Exemples : " + " | ".join(exemples[:4]))
        print("\n✅ Le fournisseur répond via son API. Le programme utilisera automatiquement ce mode")
        print("   quand le fichier M3U (get.php) est vide : tu peux continuer avec ce même lien.")
    return total > 0


def tester_lien(url, detail=False):
    if not url.startswith(("http://", "https://")):
        print("Le lien doit commencer par http:// ou https://")
        return
    print("Test de : %s\n" % masque(url))
    texte, ua_ok = None, None
    for ua, label in TEST_USER_AGENTS:
        print("→ essai avec l'identifiant « %s » ... " % label, end="", flush=True)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type", "?")
                status = r.status
                hdrs = dict(r.headers.items())
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    data = gzip.decompress(data)
        except urllib.error.HTTPError as e:
            print("échec : " + explique_erreur(e))
            print("      détail technique : %r" % e)
            if detail:
                detail_http(e)
            continue
        except Exception as e:
            print("échec : " + explique_erreur(e))
            print("      détail technique : %r" % e)
            print("\nLe serveur n'est pas joignable depuis ce réseau : changer d'identifiant ne servirait à rien.")
            print("Teste le même lien depuis le réseau où le programme tournera (ex. chez toi, derrière ta Freebox).")
            if detail:
                diagnostic_reseau(url)
            else:
                print("Relance avec --detail pour un diagnostic réseau complet.")
            return
        texte = data.decode("utf-8", errors="replace")
        if "#EXTM3U" in texte[:2000] or "#EXTINF" in texte[:5000]:
            print("OK (%.1f Mo)" % (len(data) / 1e6))
            ua_ok = (ua, label)
            break
        debut = texte.lstrip()[:1]
        if not data:
            genre = "réponse VIDE, 0 octet"
        else:
            genre = "une page web (HTML)" if debut == "<" else "du JSON" if debut in ("{", "[") else "autre chose"
        print("réponse reçue (HTTP %s), mais ce n'est pas une playlist M3U (%s, %s)" % (status, ctype, genre))
        if detail:
            low = {k.lower(): v for k, v in hdrs.items()}
            for h in SAFE_HEADERS + ("CF-RAY", "Content-Encoding", "Cache-Control"):
                v = low.get(h.lower())
                if v:
                    if h == "Location" and v.startswith("http"):
                        v = masque(v)
                    print("      %s : %s" % (h, v[:150]))
            if data:
                print("      début de la réponse : " + extrait(texte, url))
                print("      (identifiants masqués par ***, relis avant de partager)")

    if not ua_ok:
        texte2, desc = chercher_variante(url)
        if texte2:
            texte, ua_ok = texte2, (TEST_USER_AGENTS[1][0], "VLC")
            print("\n⚠️ Le lien d'origine ne marche pas, mais ce format fonctionne : %s" % desc)
            print("   Dans ton lien, remplace les valeurs de « type » et « output » par celles-ci.")

    if not ua_ok:
        print("\n❌ Aucun essai n'a permis de récupérer la playlist.")
        print("Causes fréquentes : abonnement expiré ou identifiants incorrects, adresse IP non autorisée par le")
        print("fournisseur, limite de connexions simultanées atteinte, lien différent à utiliser (format M3U / M3U Plus).")
        print("Si le lien ne s'ouvre pas non plus dans un navigateur ou VLC, le problème vient du lien ou du fournisseur.")
        diagnostic_xtream(url)
        tester_api(url)
        if detail:
            diagnostic_reseau(url)
        return

    entries = parse_m3u(texte)
    logos = sum(1 for e in entries.values() if e["logo"])
    groupes = {}
    for e in entries.values():
        g = e["group"] or "(sans groupe)"
        groupes[g] = groupes.get(g, 0) + 1
    print("\n✅ Playlist lisible : %d entrées, %d avec jaquette/logo, %d groupes." % (len(entries), logos, len(groupes)))
    for g, n in sorted(groupes.items(), key=lambda x: -x[1])[:5]:
        print("   %-40s %d" % (g[:38], n))
    exemples = [e["name"] for e in list(entries.values())[:3]]
    print("   Exemples : " + " | ".join(exemples))
    if ua_ok[0] != DEFAULT_CONFIG["user_agent"]:
        print('\nConseil : ajoute cette ligne dans ta configuration pour que le programme utilise cet identifiant :')
        print('   "user_agent": "%s"' % ua_ok[0])
    print("\nTu peux maintenant utiliser le programme : python m3u_watch.py --check")


def test_mode(args):
    paths = [a for a in args if not a.startswith("--")]
    interactif = not paths
    url = paths[0] if paths else input("Colle ton lien M3U (http:// ou https://) : ").strip().strip('"')
    try:
        tester_lien(url, detail=("--detail" in args) or interactif)
    finally:
        if interactif:
            input("\nAppuie sur Entrée pour fermer...")


# ------------------------------------------------------------------ serveur --
class Handler(BaseHTTPRequestHandler):
    cfg = None
    state_dir = None

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path == "/health":
            return self._send(200, {"ok": True})
        if url.path == "/check":
            only = (q.get("playlist") or [""])[0].strip() or None
            preview = (q.get("preview") or ["0"])[0].lower() in ("1", "true", "yes", "oui")
            try:
                with LOCK:  # une seule verification a la fois
                    result = run_check(self.cfg, self.state_dir, only, preview)
            except Exception as e:
                return self._send(500, {"error": str(e)})
            return self._send(200, result)
        self._send(404, {"error": "route inconnue (utiliser /check)"})

    def log_message(self, fmt, *args):
        log("HTTP " + (fmt % args))


def main():
    args = sys.argv[1:]
    if "--test" in args:
        return test_mode(args)
    if "--serve" not in args and "--check" not in args:
        print(__doc__, file=sys.stderr)
        sys.exit(2)

    cfg, state_dir = load_config(args)
    SETTINGS["user_agent"] = str(cfg.get("user_agent") or DEFAULT_CONFIG["user_agent"])
    SETTINGS["timeout"] = int(cfg.get("timeout") or DEFAULT_CONFIG["timeout"])
    SETTINGS["max_series_checks"] = int(cfg.get("max_series_checks") or DEFAULT_CONFIG["max_series_checks"])
    problems = validate(cfg)
    if problems:
        for p in problems:
            log("Configuration : " + p)
        if "--check" in args:
            print(json.dumps({"error": "; ".join(problems)}, ensure_ascii=False))
        sys.exit(1)

    if "--check" in args:
        print(json.dumps(run_check(cfg, state_dir, preview="--preview" in args), ensure_ascii=False))
        return

    Handler.cfg, Handler.state_dir = cfg, state_dir
    port = int(cfg.get("port", 8099))
    log("Serveur prêt sur le port %d : %d playlist(s), GET /check" % (port, len(cfg["playlists"])))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
