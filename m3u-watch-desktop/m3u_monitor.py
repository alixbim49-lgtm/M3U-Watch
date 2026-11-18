#!/usr/bin/env python3
"""
m3u_monitor.py - M3U Watch : configuration, planification, envois Telegram, statistiques.

Utilise le moteur m3u_watch.py. L'interface graphique est dans m3u_gui.py ; config.json
peut aussi etre modifie a la main (il est relu automatiquement).

    python3 m3u_monitor.py --daemon          service d'arriere-plan (planification + envois)
    python3 m3u_monitor.py --run-now [NOM]   verifie tout de suite et envoie sur Telegram
    python3 m3u_monitor.py --status          resume des serveurs
    python3 m3u_monitor.py --check-config    valide config.json
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import m3u_watch as mw

HOME = Path(os.environ.get("M3U_WATCH_HOME") or Path(__file__).resolve().parent)
CONFIG_PATH = HOME / "config.json"
STATE_DIR = HOME / "state"
STATS_PATH = STATE_DIR / "stats.json"
HEARTBEAT_PATH = STATE_DIR / "daemon.json"
LOCK_PATH = STATE_DIR / "run.lock"
LOG_PATH = STATE_DIR / "m3u_watch.log"

FALLBACK_START = "2000-01-01T08:00:00"
KIND_ORDER = ("live", "vod", "series")
KIND_PLURAL = {"live": "chaînes", "vod": "films", "series": "séries"}
KIND_SINGULAR = {"live": "chaîne", "vod": "film", "series": "série"}


# ------------------------------------------------------------------- utils --
def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 1_000_000:
            LOG_PATH.write_text("", encoding="utf-8")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def write_json(path, data, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if private:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, path)


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def iso(dt=None):
    return (dt or datetime.now()).isoformat(timespec="seconds")


def parse_dt(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ config --
def default_schedule():
    now = datetime.now()
    start = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if start <= now:
        start += timedelta(days=1)
    return {"start": iso(start), "every": 1, "unit": "days"}


def default_settings():
    return {
        "schedule": default_schedule(),
        "max_items": 10,
        "send_photos": True,
        "photo_delay": 3,
        "user_agent": mw.DEFAULT_CONFIG["user_agent"],
        "timeout": mw.DEFAULT_CONFIG["timeout"],
        "max_series_checks": mw.DEFAULT_CONFIG["max_series_checks"],
    }


def normalize(raw):
    base = default_settings()
    base["schedule"] = {"start": FALLBACK_START, "every": 1, "unit": "days"}  # config ecrite a la main
    settings = {**base, **(raw.get("settings") or {})}
    settings["schedule"] = {**base["schedule"], **(settings.get("schedule") or {})}
    servers = []
    for s in raw.get("servers") or []:
        if isinstance(s, dict):
            servers.append({"enabled": True, "telegram_token": "", "telegram_chat_id": "", **s})
    out = dict(raw)
    out["settings"], out["servers"] = settings, servers
    return out


def load_config():
    if not CONFIG_PATH.exists():
        cfg = {"settings": default_settings(), "servers": []}
        save_config(cfg)
        return normalize(cfg)
    return normalize(read_json(CONFIG_PATH, {}))


def save_config(cfg):
    write_json(CONFIG_PATH, cfg, private=True)  # contient les tokens : lisible par toi seul


def server_id(srv):
    if srv.get("id"):
        return str(srv["id"])
    return "auto-" + hashlib.sha1(("%s|%s" % (srv.get("name", ""), srv.get("url", ""))).encode()).hexdigest()[:8]


def new_server_id():
    return uuid.uuid4().hex[:8]


def url_hash(url):
    return hashlib.sha1(url.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- calendrier --
def interval(sched):
    n = max(1, int(sched.get("every") or 1))
    return timedelta(days=n * (7 if sched.get("unit") == "weeks" else 1))


def latest_slot(sched, now=None):
    """Dernier envoi prevu <= maintenant (None si la date de debut n'est pas atteinte)."""
    now = now or datetime.now()
    start = parse_dt(sched.get("start")) or parse_dt(FALLBACK_START)
    if start > now:
        return None
    return start + ((now - start) // interval(sched)) * interval(sched)


def next_slot(sched, now=None):
    now = now or datetime.now()
    start = parse_dt(sched.get("start")) or parse_dt(FALLBACK_START)
    if start > now:
        return start
    return start + ((now - start) // interval(sched) + 1) * interval(sched)


def describe_interval(sched):
    n = max(1, int(sched.get("every") or 1))
    if sched.get("unit") == "weeks":
        return "toutes les semaines" if n == 1 else "toutes les %d semaines" % n
    return "tous les jours" if n == 1 else "tous les %d jours" % n


# --------------------------------------------------------------- statistiques --
def load_stats():
    st = read_json(STATS_PATH, {})
    st.setdefault("servers", {})
    st.setdefault("meta", {})
    return st


def save_stats(st):
    write_json(STATS_PATH, st)


def write_heartbeat():
    try:
        write_json(HEARTBEAT_PATH, {"at": iso(), "pid": os.getpid()})
    except OSError:
        pass


def heartbeat_age():
    d = parse_dt(read_json(HEARTBEAT_PATH, {}).get("at"))
    return (datetime.now() - d).total_seconds() if d else None


def forget_server(sid, url=None):
    """Supprime l'historique d'un serveur (apres suppression ou changement de lien)."""
    stats = load_stats()
    stats["servers"].pop(sid, None)
    save_stats(stats)
    if url:
        try:
            mw.state_path(STATE_DIR, {"url": url, "state_key": sid}).unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------- verrou --
class RunLocked(Exception):
    pass


class RunLock:
    """Une seule verification a la fois (service ou bouton « Verifier maintenant »)."""

    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    age = time.time() - LOCK_PATH.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > 2 * 3600:  # verrou perime (arret brutal)
                    LOCK_PATH.unlink(missing_ok=True)
                    continue
                raise RunLocked()
        raise RunLocked()

    def __exit__(self, *exc):
        LOCK_PATH.unlink(missing_ok=True)


# -------------------------------------------------------------------- Telegram --
class TelegramError(Exception):
    def __init__(self, code, desc, retry_after=None):
        super().__init__(desc)
        self.code, self.desc, self.retry_after = code, desc, retry_after


def tg_call(token, method, fields, timeout=40):
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    data = urllib.parse.urlencode(fields).encode()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            try:
                j = json.loads(e.read().decode("utf-8", errors="replace"))
            except ValueError:
                j = {}
            err = TelegramError(e.code, j.get("description", ""), (j.get("parameters") or {}).get("retry_after"))
            if e.code == 429 and err.retry_after and attempt < 2:
                time.sleep(min(int(err.retry_after) + 1, 60))
                continue
            raise err
    raise TelegramError(429, "trop de requêtes")


def tg_explain(e):
    if isinstance(e, TelegramError):
        d = (e.desc or "").lower()
        if e.code == 401:
            return "token du bot invalide"
        if e.code == 404:
            return "token du bot introuvable ou mal recopié"
        if "chat not found" in d:
            return "chaîne introuvable : vérifie l'identifiant et que le bot en est administrateur"
        if e.code == 403:
            return "le bot n'a pas le droit d'écrire dans cette chaîne (ajoute-le comme administrateur)"
        if e.code == 429:
            return "trop de messages envoyés, Telegram demande de patienter"
        return "erreur Telegram %s : %s" % (e.code, e.desc)
    return mw.explique_erreur(e)


def tg_send_text(token, chat, text):
    chunks, buf = [], ""
    for line in text.splitlines(keepends=True):  # limite Telegram : 4096 caracteres
        if len(buf) + len(line) > 3800:
            chunks.append(buf)
            buf = ""
        buf += line
    if buf.strip():
        chunks.append(buf)
    for i, chunk in enumerate(chunks):
        tg_call(token, "sendMessage", {"chat_id": chat, "text": chunk, "disable_web_page_preview": "true"})
        if i < len(chunks) - 1:
            time.sleep(1)


def tg_send_photo(token, chat, url, caption):
    tg_call(token, "sendPhoto", {"chat_id": chat, "photo": url, "caption": caption[:1000]})


def send_plan(token, chat, plan, delay):
    sent_photo = False
    for item in plan:
        if item[0] == "text":
            tg_send_text(token, chat, item[1])
            time.sleep(1)
            continue
        if sent_photo:
            time.sleep(max(0, delay))
        try:
            tg_send_photo(token, chat, item[1], item[2])
        except TelegramError as e:
            if e.code == 400:  # jaquette inaccessible pour Telegram : on envoie la legende en texte
                tg_send_text(token, chat, item[2])
            else:
                raise
        sent_photo = True


def tg_test(token, chat):
    """(ok, message) : verifie le token puis envoie un message de test."""
    token, chat = (token or "").strip(), (chat or "").strip()
    if not token or not chat:
        return False, "Renseigne le token du bot et l'identifiant de la chaîne."
    try:
        me = tg_call(token, "getMe", {})["result"]
    except Exception as e:
        return False, "Bot injoignable : " + tg_explain(e)
    try:
        tg_call(token, "sendMessage", {"chat_id": chat, "text": "✅ M3U Watch : message de test réussi."})
    except Exception as e:
        return False, "Bot @%s reconnu, mais envoi impossible : %s" % (me.get("username", "?"), tg_explain(e))
    return True, "Bot @%s : message de test envoyé." % me.get("username", "?")


# -------------------------------------------------------------- messages --
def kinds_txt(d):
    parts = ["%d %s" % (d.get(k, 0), (KIND_SINGULAR if d.get(k, 0) == 1 else KIND_PLURAL)[k])
             for k in KIND_ORDER if d.get(k, 0)]
    return " · ".join(parts)


def build_plan(res, settings):
    """Liste d'envois : ('text', texte) ou ('photo', url, legende)."""
    if res["status"] in ("error", "initialized"):
        return [("text", res["message"])] if res["message"] else []
    if not res["has_changes"]:
        return []
    if not settings.get("send_photos", True):
        return [("text", res["message"])]

    name = res["name"]
    lines = ["📺 %s – %s" % (name, datetime.now().strftime("%d/%m/%Y %H:%M"))]
    if sum(res["added_by_kind"].values()):
        lines.append("➕ Ajoutés : " + kinds_txt(res["added_by_kind"]))
        extra = res["added_count"] - len(res["added"])
        if extra > 0:
            lines.append("   (dont %d non détaillés)" % extra)
    if sum(res["removed_by_kind"].values()):
        lines.append("➖ Supprimés : " + kinds_txt(res["removed_by_kind"]))
    if res["changed_count"]:
        lines.append("✏️ Modifiés : %d" % res["changed_count"])
    if res["series_updates_count"]:
        lines.append("🆕 Séries avec nouveaux épisodes : %d" % res["series_updates_count"])
    text = "\n".join(lines) + "\n"

    sans_logo = [a for a in res["added"] if not a["logo"]]
    text += mw.section("➕ Sans jaquette", sans_logo, len(sans_logo), mw.fmt_basic)
    text += mw.section("➖ Supprimés", res["removed"], res["removed_count"], mw.fmt_basic)
    text += mw.section("✏️ Modifiés", res["changed"], res["changed_count"], mw.fmt_changed)
    ep_sans_logo = [u for u in res["series_updates"] if not u["logo"]]
    text += mw.section("🆕 Nouveaux épisodes", ep_sans_logo, len(ep_sans_logo), mw.fmt_series)
    plan = [("text", text)]

    for a in res["added"]:
        if a["logo"]:
            cap = "➕ %s" % a["name"]
            if a.get("group"):
                cap += " [%s]" % a["group"]
            cap += " (%s)" % mw.KIND_LABELS.get(a["kind"], "")
            plan.append(("photo", a["logo"], cap))
    for u in res["series_updates"]:
        if u["logo"]:
            plan.append(("photo", u["logo"], "🆕 %s — %s" % (u["name"], u["label"])))
    return plan


# ------------------------------------------------------------ execution --
def apply_settings(settings):
    mw.SETTINGS["user_agent"] = str(settings.get("user_agent") or mw.DEFAULT_CONFIG["user_agent"])
    mw.SETTINGS["timeout"] = int(settings.get("timeout") or mw.DEFAULT_CONFIG["timeout"])
    mw.SETTINGS["max_series_checks"] = int(settings.get("max_series_checks") or mw.DEFAULT_CONFIG["max_series_checks"])


def run_server(srv, settings, stats):
    sid, name = server_id(srv), (srv.get("name") or "Serveur").strip()
    st = stats["servers"].setdefault(sid, {})
    url = (srv.get("url") or "").strip()
    pl = {"name": name, "url": url, "state_key": sid}

    h = url_hash(url)
    if st.get("url_hash") and st["url_hash"] != h:  # lien modifie : on repart d'une base propre
        try:
            mw.state_path(STATE_DIR, pl).unlink()
        except OSError:
            pass
        for k in ("totals", "last_send"):
            st.pop(k, None)
    st["url_hash"], st["name"] = h, name

    defer = []
    try:
        res = mw.check_playlist(pl, name, STATE_DIR, int(settings["max_items"]), False, defer)
    except Exception as e:
        res = mw.new_result(name)
        res.update(status="error", error=str(e))
        res["message"] = "⚠️ %s : erreur (%s)" % (name, e)

    st["last_check"] = iso()
    st["status"], st["error"] = res["status"], res["error"]
    if res["status"] != "error":
        st["totals"] = res["totals_by_kind"]

    plan = build_plan(res, settings)
    token, chat = (srv.get("telegram_token") or "").strip(), str(srv.get("telegram_chat_id") or "").strip()
    send_error, sent = None, False
    if plan:
        if not token or not chat:
            send_error = "Telegram non configuré (token ou identifiant de chaîne manquant)"
        else:
            try:
                send_plan(token, chat, plan, float(settings.get("photo_delay", 3)))
                sent = True
            except Exception as e:
                send_error = "Telegram : " + tg_explain(e)

    if res["status"] != "error":
        if send_error:  # etat non valide : les changements seront renvoyes au prochain passage
            st["status"], st["error"] = "telegram_error", send_error
        else:
            for sp, new, src in defer:
                mw.save_state(sp, new, src)
            if sent:
                st["last_send"] = {
                    "at": iso(),
                    "added": res["added_by_kind"] if res["has_changes"] else {"live": 0, "vod": 0, "series": 0},
                    "series_updates": res["series_updates_count"],
                }
    elif send_error:
        st["error"] = "%s | %s" % (res["error"], send_error)
    elif sent:
        st["last_alert"] = iso()

    log("« %s » : %s%s" % (name, st["status"], (" – " + st["error"]) if st.get("error") else ""))
    return {"id": sid, "name": name, "status": st["status"], "error": st.get("error"), "sent": sent,
            "totals": st.get("totals"), "added": res["added_by_kind"], "has_changes": res["has_changes"]}


def run_servers(cfg, ids=None, mark_all=False):
    """Verifie les serveurs actifs (tous, ou ceux de `ids`) et envoie sur Telegram."""
    settings = cfg["settings"]
    servers = [s for s in cfg["servers"] if s.get("enabled", True) and (s.get("url") or "").strip()]
    if ids is not None:
        servers = [s for s in servers if server_id(s) in ids]
    results = []
    with RunLock():
        apply_settings(settings)
        stats = load_stats()
        for srv in servers:
            results.append(run_server(srv, settings, stats))
            save_stats(stats)
        if mark_all:
            stats["meta"]["last_run_all"] = iso()
            save_stats(stats)
    return results


def test_server(url, settings):
    """Test du lien : {'ok', 'message', 'totals'} sans rien enregistrer."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "message": "Le lien doit commencer par http:// ou https://"}
    apply_settings(settings)
    try:
        entries, src = mw.fetch_entries(url)
    except ValueError:
        return {"ok": False, "message": "Réponse du serveur vide ou illisible."}
    except Exception as e:
        return {"ok": False, "message": mw.explique_erreur(e)}
    if not entries:
        return {"ok": False, "message": "Le serveur répond mais la playlist est vide (compte expiré ou lien incorrect ?)."}
    totals = mw.count_kinds(entries.values())
    txt = "%s  (source : %s)" % (" - ".join(str(totals[k]) for k in KIND_ORDER) + "  chaînes - films - séries",
                                   "API du fournisseur" if src == "api" else "fichier M3U")
    return {"ok": True, "message": txt + account_info(url), "totals": totals}


def account_info(url):
    cred = mw.xtream_creds(url)
    if not cred:
        return ""
    try:
        ui = (mw.xtream_call(*cred) or {}).get("user_info") or {}
        parts = []
        if ui.get("status"):
            parts.append("compte " + str(ui["status"]).lower())
        try:
            parts.append("expire le " + datetime.fromtimestamp(int(ui["exp_date"])).strftime("%d/%m/%Y"))
        except (KeyError, TypeError, ValueError):
            pass
        return ("\n" + ", ".join(parts)) if parts else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------- service --
def daemon():
    log("Service M3U Watch démarré (dossier : %s)" % HOME)
    while True:
        try:
            cfg = load_config()
            write_heartbeat()
            stats = load_stats()
            now = datetime.now()
            actifs = [s for s in cfg["servers"] if s.get("enabled", True) and (s.get("url") or "").strip()]
            nouveaux = {server_id(s) for s in actifs} - set(stats["servers"])  # jamais verifies
            slot = latest_slot(cfg["settings"]["schedule"], now)
            last_all = parse_dt(stats["meta"].get("last_run_all"))
            if slot is not None and (last_all is None or last_all < slot):
                log("Envoi planifié (%s)" % slot.strftime("%d/%m/%Y %H:%M"))
                run_servers(cfg, None, mark_all=True)
            elif nouveaux:
                log("Premier passage de %d nouveau(x) serveur(s)" % len(nouveaux))
                run_servers(cfg, nouveaux)
        except RunLocked:
            pass
        except Exception as e:
            log("Erreur du service : %s" % e)
        time.sleep(30)


# -------------------------------------------------------------------------- CLI --
def cmd_status():
    cfg, stats = load_config(), load_stats()
    sched = cfg["settings"]["schedule"]
    print("Prochain envoi : %s (%s)" % (next_slot(sched).strftime("%d/%m/%Y %H:%M"), describe_interval(sched)))
    age = heartbeat_age()
    print("Service : %s" % ("actif" if age is not None and age < 120 else "arrêté"))
    for s in cfg["servers"]:
        st = stats["servers"].get(server_id(s), {})
        tot = " - ".join(str((st.get("totals") or {}).get(k, 0)) for k in KIND_ORDER) if st.get("totals") else "—"
        d = parse_dt((st.get("last_send") or {}).get("at"))
        ls = d.strftime("%d/%m/%Y %H:%M") if d else "jamais"
        print("%-24s total %-22s dernier envoi %s  [%s]" % (s.get("name", "?")[:24], tot, ls, st.get("status", "pas encore vérifié")))


def main():
    args = sys.argv[1:]
    if "--daemon" in args:
        daemon()
    elif "--run-now" in args:
        cfg = load_config()
        names = [a for a in args if not a.startswith("--")]
        ids = {server_id(s) for s in cfg["servers"] if s.get("name") in names} if names else None
        try:
            run_servers(cfg, ids)
        except RunLocked:
            print("Une vérification est déjà en cours.")
            sys.exit(1)
    elif "--status" in args:
        cmd_status()
    elif "--check-config" in args:
        cfg = load_config()
        print("config.json lu : %d serveur(s)." % len(cfg["servers"]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
