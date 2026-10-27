# -*- coding: utf-8 -*-
"""
SolutionTrack - Plataforma de Tracking, Atribuicao UTM & Meta Ads
Backend 100% Python stdlib (sem pip install).

Modulos:
  - Meta OAuth 2.0 (login 1-clique 60 dias) + Token Permanente manual
  - Graph API com paginacao completa (fetch_all_meta_pages)
  - Webhooks universais (Kiwify, Hotmart, Greenn, Cakto, Wiapy)
  - Script de pixel /solutiontrack.js
  - Conversions API (CAPI) server-side com SHA-256
  - Metricas financeiras e lucro real
  - Motor de regras / automacoes em background

Rodar:  python server.py 10000
"""

import http.server
import socketserver
import urllib.request
import urllib.parse
import urllib.error
import json
import sqlite3
import hashlib
import hmac
import uuid
import os
import sys
import time
import threading
import datetime
import ssl

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "solutiontrack.db")
GRAPH_VERSION = "v21.0"
GRAPH_BASE = "https://graph.facebook.com/" + GRAPH_VERSION

# Permite host sem verificacao SSL falhar em alguns ambientes Render
SSL_CTX = ssl.create_default_context()

DB_LOCK = threading.Lock()


def public_base_url():
    """URL publica usada para montar o redirect do OAuth."""
    env = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if env:
        return env
    render = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if render:
        return render
    port = os.environ.get("PORT", "10000")
    return "http://localhost:" + str(port)


def redirect_uri():
    return public_base_url() + "/api/integrations/meta/callback"


# ---------------------------------------------------------------------------
# BANCO DE DADOS + MIGRACOES
# ---------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def table_columns(conn, table):
    cur = conn.execute("PRAGMA table_info(" + table + ")")
    return [r[1] for r in cur.fetchall()]


def ensure_column(conn, table, column, decl):
    cols = table_columns(conn, table)
    if column not in cols:
        conn.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " " + decl)


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        external_id TEXT,
        platform TEXT,
        status TEXT,
        payment_method TEXT,
        product_name TEXT,
        customer_name TEXT,
        customer_email TEXT,
        customer_phone TEXT,
        amount REAL DEFAULT 0,
        currency TEXT DEFAULT 'BRL',
        utm_source TEXT, utm_medium TEXT, utm_campaign TEXT,
        utm_content TEXT, utm_term TEXT,
        src TEXT, sck TEXT, fbc TEXT, fbp TEXT,
        client_ip TEXT, user_agent TEXT,
        raw_payload TEXT,
        created_at TEXT, updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS tracking_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT,
        event_id TEXT,
        url TEXT,
        utm_source TEXT, utm_medium TEXT, utm_campaign TEXT,
        utm_content TEXT, utm_term TEXT,
        fbc TEXT, fbp TEXT,
        client_ip TEXT, user_agent TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS facebook_connections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fb_user_id TEXT,
        fb_user_name TEXT,
        access_token TEXT,
        token_type TEXT DEFAULT 'oauth',
        expires_at TEXT,
        is_permanent INTEGER DEFAULT 0,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS facebook_ad_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        connection_id INTEGER,
        account_id TEXT UNIQUE,
        name TEXT,
        currency TEXT,
        account_status INTEGER,
        is_selected INTEGER DEFAULT 1,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS meta_oauth_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        app_id TEXT,
        app_secret TEXT,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS oauth_states (
        state TEXT PRIMARY KEY,
        used INTEGER DEFAULT 0,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS meta_campaign_insights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT,
        campaign_id TEXT,
        date_start TEXT,
        date_stop TEXT,
        spend REAL DEFAULT 0,
        impressions INTEGER DEFAULT 0,
        clicks INTEGER DEFAULT 0,
        reach INTEGER DEFAULT 0,
        cpc REAL DEFAULT 0,
        cpm REAL DEFAULT 0,
        ctr REAL DEFAULT 0,
        synced_at TEXT
    );

    CREATE TABLE IF NOT EXISTS campaigns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT,
        campaign_id TEXT UNIQUE,
        name TEXT,
        status TEXT,
        objective TEXT,
        daily_budget REAL DEFAULT 0,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS ad_sets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT,
        campaign_id TEXT,
        adset_id TEXT UNIQUE,
        name TEXT,
        status TEXT,
        daily_budget REAL DEFAULT 0,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS ads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT,
        campaign_id TEXT,
        adset_id TEXT,
        ad_id TEXT UNIQUE,
        name TEXT,
        status TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS financial_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tax_rate REAL DEFAULT 6.0,
        gateway_percent REAL DEFAULT 8.9,
        gateway_fixed REAL DEFAULT 2.49,
        product_cost_percent REAL DEFAULT 0,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS meta_pixels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        pixel_id TEXT,
        access_token TEXT,
        test_event_code TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS automated_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        rule_type TEXT,
        target_level TEXT DEFAULT 'campaign',
        condition_metric TEXT,
        condition_operator TEXT,
        condition_value REAL,
        action_type TEXT,
        action_value REAL DEFAULT 0,
        schedule_on TEXT,
        schedule_off TEXT,
        is_active INTEGER DEFAULT 1,
        last_run TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS rule_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id INTEGER,
        message TEXT,
        level TEXT DEFAULT 'info',
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS custom_dashboards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT DEFAULT 'default',
        layout TEXT,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS user_preferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pref_key TEXT UNIQUE,
        pref_value TEXT,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS capi_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pixel_id TEXT,
        event_name TEXT,
        status TEXT,
        response TEXT,
        created_at TEXT
    );
    """)

    # Migracoes idempotentes (verificacao de colunas)
    ensure_column(conn, "orders", "fbclid", "TEXT")
    ensure_column(conn, "campaigns", "spend", "REAL DEFAULT 0")
    ensure_column(conn, "financial_settings", "product_cost_percent", "REAL DEFAULT 0")

    # Seed de settings financeiros
    row = c.execute("SELECT COUNT(*) FROM financial_settings").fetchone()
    if row[0] == 0:
        c.execute("INSERT INTO financial_settings (tax_rate, gateway_percent, gateway_fixed, product_cost_percent, updated_at) VALUES (?,?,?,?,?)",
                  (6.0, 8.9, 2.49, 0.0, now_iso()))

    # Seed config app via env (se existir)
    env_id = os.environ.get("META_APP_ID", "").strip()
    env_secret = os.environ.get("META_APP_SECRET", "").strip()
    cfg = c.execute("SELECT COUNT(*) FROM meta_oauth_config").fetchone()
    if cfg[0] == 0:
        c.execute("INSERT INTO meta_oauth_config (app_id, app_secret, updated_at) VALUES (?,?,?)",
                  (env_id, env_secret, now_iso()))

    conn.commit()
    conn.close()
    print("[DB] Inicializado em " + DB_PATH)


def now_iso():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def today_str():
    return datetime.date.today().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# GRAPH API (urllib) + PAGINACAO
# ---------------------------------------------------------------------------
def graph_get(path, params):
    if not path.startswith("http"):
        url = GRAPH_BASE + "/" + path.lstrip("/")
    else:
        url = path
    query = urllib.parse.urlencode(params)
    full = url + ("&" if "?" in url else "?") + query
    req = urllib.request.Request(full, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=40, context=SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        return None, body
    except Exception as e:
        return None, str(e)


def graph_delete(path, params):
    url = GRAPH_BASE + "/" + path.lstrip("/")
    query = urllib.parse.urlencode(params)
    full = url + "?" + query
    req = urllib.request.Request(full, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=40, context=SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        return None, e.read().decode("utf-8", "ignore")
    except Exception as e:
        return None, str(e)


def graph_post(path, data):
    url = GRAPH_BASE + "/" + path.lstrip("/")
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=40, context=SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        return None, e.read().decode("utf-8", "ignore")
    except Exception as e:
        return None, str(e)


def fetch_all_meta_pages(path, params):
    """Segue paging.next / paging.cursors.after ate esgotar os resultados."""
    results = []
    data, err = graph_get(path, params)
    if err:
        return results, err
    while data:
        results.extend(data.get("data", []))
        paging = data.get("paging", {})
        next_url = paging.get("next")
        if next_url:
            data, err = graph_get(next_url, {})
            if err:
                break
        else:
            break
    return results, None


# ---------------------------------------------------------------------------
# HELPERS DE DADOS
# ---------------------------------------------------------------------------
def get_meta_config(conn):
    r = conn.execute("SELECT app_id, app_secret FROM meta_oauth_config ORDER BY id DESC LIMIT 1").fetchone()
    if r:
        return r["app_id"], r["app_secret"]
    return "", ""


def active_token(conn):
    """Retorna o token de acesso mais recente valido."""
    r = conn.execute("SELECT access_token FROM facebook_connections ORDER BY id DESC LIMIT 1").fetchone()
    return r["access_token"] if r else None


def sha256(value):
    if value is None:
        return None
    v = str(value).strip().lower()
    if v == "":
        return None
    return hashlib.sha256(v.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# PERIODO -> intervalo de datas
# ---------------------------------------------------------------------------
def resolve_period(period, start, end):
    today = datetime.date.today()
    if period == "today":
        return today, today
    if period == "yesterday":
        y = today - datetime.timedelta(days=1)
        return y, y
    if period == "last7":
        return today - datetime.timedelta(days=6), today
    if period == "last_week":
        start_w = today - datetime.timedelta(days=today.weekday() + 7)
        return start_w, start_w + datetime.timedelta(days=6)
    if period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        first_this = today.replace(day=1)
        last_prev = first_this - datetime.timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    if period == "custom" and start and end:
        try:
            s = datetime.datetime.strptime(start, "%Y-%m-%d").date()
            e = datetime.datetime.strptime(end, "%Y-%m-%d").date()
            return s, e
        except Exception:
            pass
    # all / default
    return datetime.date(2000, 1, 1), today


# ---------------------------------------------------------------------------
# WEBHOOK PARSERS (normalizam payloads de cada plataforma)
# ---------------------------------------------------------------------------
def pick(d, *keys):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] not in (None, ""):
            return d[k]
    return None


def normalize_status(raw):
    if not raw:
        return "pending"
    r = str(raw).lower()
    if r in ("paid", "approved", "aprovado", "completed", "complete", "active"):
        return "approved"
    if r in ("waiting_payment", "pending", "pendente", "processing", "billet_printed", "waiting"):
        return "pending"
    if r in ("refunded", "chargeback", "canceled", "cancelled", "refused"):
        return "refused"
    return r


def parse_webhook(platform, payload):
    """Converte o payload bruto de cada gateway num dict normalizado de order."""
    p = payload if isinstance(payload, dict) else {}
    # Busca UTMs em varios formatos comuns
    tracking = p.get("tracking") or p.get("trackingParameters") or p.get("utm") or {}
    customer = p.get("customer") or p.get("buyer") or p.get("Customer") or {}

    def utm(name):
        return (pick(tracking, name, name.upper())
                or pick(p, name, name.upper()))

    amount = pick(p, "amount", "value", "price", "total", "commissionTotal")
    if isinstance(amount, dict):
        amount = pick(amount, "value", "total", "amount")
    try:
        amount = float(amount) if amount is not None else 0.0
    except Exception:
        amount = 0.0
    # Hotmart manda em centavos as vezes
    if platform == "hotmart" and amount and amount > 100000:
        amount = amount / 100.0

    status_raw = pick(p, "status", "order_status", "payment_status", "event")
    if isinstance(p.get("data"), dict):
        status_raw = status_raw or pick(p["data"], "status", "payment_status")

    return {
        "external_id": str(pick(p, "order_id", "orderId", "id", "transaction", "code", "hottok") or uuid.uuid4().hex[:12]),
        "platform": platform,
        "status": normalize_status(status_raw),
        "payment_method": pick(p, "payment_method", "paymentMethod", "payment_type", "method") or "unknown",
        "product_name": pick(p, "product_name", "productName", "product") or (pick(p.get("product", {}), "name") if isinstance(p.get("product"), dict) else None) or "Produto",
        "customer_name": pick(customer, "name", "full_name", "fullName"),
        "customer_email": pick(customer, "email", "Email"),
        "customer_phone": pick(customer, "phone", "mobile", "phone_number"),
        "amount": amount,
        "utm_source": utm("utm_source") or "direct",
        "utm_medium": utm("utm_medium"),
        "utm_campaign": utm("utm_campaign"),
        "utm_content": utm("utm_content"),
        "utm_term": utm("utm_term"),
        "src": utm("src"),
        "sck": utm("sck"),
        "fbc": utm("fbc"),
        "fbp": utm("fbp"),
    }


# ---------------------------------------------------------------------------
# CONVERSIONS API (CAPI)
# ---------------------------------------------------------------------------
def send_capi_event(conn, pixel_row, order, event_name="Purchase", test=False):
    user_data = {}
    em = sha256(order.get("customer_email"))
    ph = sha256(order.get("customer_phone"))
    if em:
        user_data["em"] = [em]
    if ph:
        user_data["ph"] = [ph]
    if order.get("fbc"):
        user_data["fbc"] = order["fbc"]
    if order.get("fbp"):
        user_data["fbp"] = order["fbp"]
    if order.get("client_ip"):
        user_data["client_ip_address"] = order["client_ip"]
    if order.get("user_agent"):
        user_data["client_user_agent"] = order["user_agent"]

    event = {
        "event_name": event_name,
        "event_time": int(time.time()),
        "action_source": "website",
        "event_id": str(order.get("external_id") or uuid.uuid4().hex),
        "user_data": user_data,
        "custom_data": {
            "currency": order.get("currency", "BRL"),
            "value": float(order.get("amount", 0) or 0),
        },
    }
    body = {"data": json.dumps([event]), "access_token": pixel_row["access_token"]}
    if test and pixel_row["test_event_code"]:
        body["test_event_code"] = pixel_row["test_event_code"]

    resp, err = graph_post(pixel_row["pixel_id"] + "/events", body)
    status = "ok" if (resp and not err) else "error"
    conn.execute("INSERT INTO capi_logs (pixel_id, event_name, status, response, created_at) VALUES (?,?,?,?,?)",
                 (pixel_row["pixel_id"], event_name, status, json.dumps(resp) if resp else str(err), now_iso()))
    conn.commit()
    return resp, err


# ---------------------------------------------------------------------------
# MOTOR DE REGRAS (background)
# ---------------------------------------------------------------------------
def rules_engine_loop():
    while True:
        try:
            run_rules_once()
        except Exception as e:
            print("[RULES] erro: " + str(e))
        time.sleep(300)  # a cada 5 minutos


def run_rules_once():
    conn = get_db()
    rules = conn.execute("SELECT * FROM automated_rules WHERE is_active=1").fetchall()
    token = active_token(conn)
    for rule in rules:
        # Avaliacao simplificada baseada em insights locais ja sincronizados
        try:
            evaluate_rule(conn, rule, token)
        except Exception as e:
            log_rule(conn, rule["id"], "Erro na avaliacao: " + str(e), "error")
    conn.close()


def evaluate_rule(conn, rule, token):
    metric = rule["condition_metric"]
    op = rule["condition_operator"]
    threshold = rule["condition_value"] or 0
    # Soma gasto e vendas por campanha das contas selecionadas
    rows = conn.execute("""
        SELECT campaign_id, SUM(spend) spend, SUM(clicks) clicks
        FROM meta_campaign_insights GROUP BY campaign_id
    """).fetchall()
    for r in rows:
        value = r[metric] if metric in r.keys() else 0
        hit = compare(value, op, threshold)
        if hit:
            log_rule(conn, rule["id"],
                     "Regra '" + rule["name"] + "' acionada para campanha " + str(r["campaign_id"]) +
                     " (" + metric + "=" + str(value) + ")", "info")
    conn.execute("UPDATE automated_rules SET last_run=? WHERE id=?", (now_iso(), rule["id"]))
    conn.commit()


def compare(value, op, threshold):
    try:
        value = float(value or 0)
        threshold = float(threshold or 0)
    except Exception:
        return False
    if op == ">":
        return value > threshold
    if op == "<":
        return value < threshold
    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    if op == "==":
        return value == threshold
    return False


def log_rule(conn, rule_id, message, level="info"):
    conn.execute("INSERT INTO rule_logs (rule_id, message, level, created_at) VALUES (?,?,?,?)",
                 (rule_id, message, level, now_iso()))
    conn.commit()


# ---------------------------------------------------------------------------
# HTTP HANDLER
# ---------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stdout.write("[HTTP] " + (fmt % args) + "\n")

    # ---- helpers de resposta ----
    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, content_type="text/plain; charset=utf-8", code=200):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type):
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.cors()
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_text("Not found: " + path, code=404)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            # tenta form-urlencoded
            try:
                parsed = urllib.parse.parse_qs(raw.decode("utf-8"))
                return {k: v[0] for k, v in parsed.items()}
            except Exception:
                return {"_raw": raw.decode("utf-8", "ignore")}

    def client_ip(self):
        fwd = self.headers.get("X-Forwarded-For")
        if fwd:
            return fwd.split(",")[0].strip()
        return self.client_address[0]

    # ---- metodos ----
    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            return self.send_file(os.path.join(BASE_DIR, "index.html"), "text/html; charset=utf-8")
        if path == "/solutiontrack.js":
            return self.serve_pixel_script()
        if path == "/health":
            return self.send_json({"status": "ok", "time": now_iso(), "base_url": public_base_url()})

        if path.startswith("/api/"):
            return self.route_get(path, qs)

        return self.send_text("404", code=404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self.read_body()
        if path == "/api/track":
            return self.handle_track(body)
        if path.startswith("/api/webhook/"):
            return self.handle_webhook(path, body)
        if path.startswith("/api/"):
            return self.route_post(path, body)
        return self.send_text("404", code=404)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        return self.route_delete(path, qs)

    # ---------------- ROTAS GET ----------------
    def route_get(self, path, qs):
        conn = get_db()
        try:
            if path == "/api/config":
                app_id, _secret = get_meta_config(conn)
                return self.send_json({
                    "app_id": app_id,
                    "has_secret": bool(_secret),
                    "redirect_uri": redirect_uri(),
                    "base_url": public_base_url(),
                })

            if path == "/api/integrations/meta/login":
                return self.meta_login(conn)

            if path == "/api/integrations/meta/callback":
                return self.meta_callback(conn, qs)

            if path == "/api/accounts":
                rows = conn.execute("""
                    SELECT a.*, c.fb_user_name FROM facebook_ad_accounts a
                    LEFT JOIN facebook_connections c ON c.id = a.connection_id
                    ORDER BY a.id DESC
                """).fetchall()
                return self.send_json([dict(r) for r in rows])

            if path == "/api/dashboard":
                return self.dashboard_metrics(conn, qs)

            if path == "/api/campaigns":
                return self.send_json(self.list_level(conn, "campaigns", qs))
            if path == "/api/adsets":
                return self.send_json(self.list_level(conn, "ad_sets", qs))
            if path == "/api/ads":
                return self.send_json(self.list_level(conn, "ads", qs))

            if path == "/api/orders":
                rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 200").fetchall()
                return self.send_json([dict(r) for r in rows])

            if path == "/api/financial/settings":
                r = conn.execute("SELECT * FROM financial_settings ORDER BY id DESC LIMIT 1").fetchone()
                return self.send_json(dict(r) if r else {})

            if path == "/api/pixels":
                rows = conn.execute("SELECT id,name,pixel_id,test_event_code,is_active,created_at FROM meta_pixels ORDER BY id DESC").fetchall()
                return self.send_json([dict(r) for r in rows])

            if path == "/api/rules":
                rows = conn.execute("SELECT * FROM automated_rules ORDER BY id DESC").fetchall()
                return self.send_json([dict(r) for r in rows])

            if path == "/api/rules/logs":
                rows = conn.execute("SELECT * FROM rule_logs ORDER BY id DESC LIMIT 100").fetchall()
                return self.send_json([dict(r) for r in rows])

            if path == "/api/capi/logs":
                rows = conn.execute("SELECT * FROM capi_logs ORDER BY id DESC LIMIT 50").fetchall()
                return self.send_json([dict(r) for r in rows])

            if path == "/api/dashboard/layout":
                r = conn.execute("SELECT layout FROM custom_dashboards WHERE name='default' ORDER BY id DESC LIMIT 1").fetchone()
                return self.send_json(json.loads(r["layout"]) if r and r["layout"] else {})

            if path == "/api/preferences":
                rows = conn.execute("SELECT pref_key, pref_value FROM user_preferences").fetchall()
                return self.send_json({r["pref_key"]: json.loads(r["pref_value"]) for r in rows})

            return self.send_json({"error": "rota nao encontrada"}, 404)
        finally:
            conn.close()

    # ---------------- ROTAS POST ----------------
    def route_post(self, path, body):
        conn = get_db()
        try:
            if path == "/api/config":
                conn.execute("INSERT INTO meta_oauth_config (app_id, app_secret, updated_at) VALUES (?,?,?)",
                             (body.get("app_id", ""), body.get("app_secret", ""), now_iso()))
                conn.commit()
                return self.send_json({"ok": True})

            if path == "/api/accounts/manual":
                return self.add_manual_account(conn, body)

            if path == "/api/accounts/select":
                conn.execute("UPDATE facebook_ad_accounts SET is_selected=? WHERE account_id=?",
                             (1 if body.get("selected") else 0, body.get("account_id")))
                conn.commit()
                return self.send_json({"ok": True})

            if path == "/api/accounts/select_all":
                conn.execute("UPDATE facebook_ad_accounts SET is_selected=?", (1 if body.get("selected") else 0,))
                conn.commit()
                return self.send_json({"ok": True})

            if path == "/api/campaigns/sync":
                return self.sync_campaigns(conn, body)

            if path == "/api/financial/settings":
                conn.execute("""INSERT INTO financial_settings
                    (tax_rate, gateway_percent, gateway_fixed, product_cost_percent, updated_at)
                    VALUES (?,?,?,?,?)""",
                    (float(body.get("tax_rate", 0)), float(body.get("gateway_percent", 0)),
                     float(body.get("gateway_fixed", 0)), float(body.get("product_cost_percent", 0)), now_iso()))
                conn.commit()
                return self.send_json({"ok": True})

            if path == "/api/pixels":
                conn.execute("""INSERT INTO meta_pixels (name, pixel_id, access_token, test_event_code, is_active, created_at)
                    VALUES (?,?,?,?,1,?)""",
                    (body.get("name"), body.get("pixel_id"), body.get("access_token"),
                     body.get("test_event_code"), now_iso()))
                conn.commit()
                return self.send_json({"ok": True})

            if path == "/api/capi/test":
                return self.capi_test(conn, body)

            if path == "/api/rules":
                conn.execute("""INSERT INTO automated_rules
                    (name, rule_type, target_level, condition_metric, condition_operator, condition_value,
                     action_type, action_value, schedule_on, schedule_off, is_active, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                    (body.get("name"), body.get("rule_type"), body.get("target_level", "campaign"),
                     body.get("condition_metric"), body.get("condition_operator"),
                     float(body.get("condition_value", 0) or 0), body.get("action_type"),
                     float(body.get("action_value", 0) or 0), body.get("schedule_on"),
                     body.get("schedule_off"), now_iso()))
                conn.commit()
                return self.send_json({"ok": True})

            if path == "/api/bulk/status":
                return self.bulk_status(conn, body)

            if path == "/api/dashboard/layout":
                conn.execute("DELETE FROM custom_dashboards WHERE name='default'")
                conn.execute("INSERT INTO custom_dashboards (name, layout, updated_at) VALUES ('default',?,?)",
                             (json.dumps(body), now_iso()))
                conn.commit()
                return self.send_json({"ok": True})

            if path == "/api/preferences":
                for k, v in body.items():
                    conn.execute("""INSERT INTO user_preferences (pref_key, pref_value, updated_at)
                        VALUES (?,?,?) ON CONFLICT(pref_key) DO UPDATE SET pref_value=excluded.pref_value, updated_at=excluded.updated_at""",
                        (k, json.dumps(v), now_iso()))
                conn.commit()
                return self.send_json({"ok": True})

            if path == "/api/orders/test":
                # cria uma venda de teste (dispara som ao vivo no front via polling)
                return self.create_test_order(conn, body)

            return self.send_json({"error": "rota nao encontrada"}, 404)
        finally:
            conn.close()

    # ---------------- ROTAS DELETE ----------------
    def route_delete(self, path, qs):
        conn = get_db()
        try:
            if path == "/api/integrations/meta/disconnect":
                return self.meta_disconnect(conn)
            if path == "/api/pixels":
                conn.execute("DELETE FROM meta_pixels WHERE id=?", (qs.get("id", [""])[0],))
                conn.commit()
                return self.send_json({"ok": True})
            if path == "/api/rules":
                conn.execute("DELETE FROM automated_rules WHERE id=?", (qs.get("id", [""])[0],))
                conn.commit()
                return self.send_json({"ok": True})
            if path == "/api/accounts":
                conn.execute("DELETE FROM facebook_ad_accounts WHERE account_id=?", (qs.get("account_id", [""])[0],))
                conn.commit()
                return self.send_json({"ok": True})
            return self.send_json({"error": "rota nao encontrada"}, 404)
        finally:
            conn.close()

    # ---------------- META OAUTH ----------------
    def meta_login(self, conn):
        app_id, _ = get_meta_config(conn)
        if not app_id:
            return self.send_json({"error": "App ID nao configurado"}, 400)
        state = uuid.uuid4().hex
        conn.execute("INSERT INTO oauth_states (state, used, created_at) VALUES (?,0,?)", (state, now_iso()))
        conn.commit()
        params = {
            "client_id": app_id,
            "redirect_uri": redirect_uri(),
            "state": state,
            "response_type": "code",
            "scope": "ads_read,public_profile",
        }
        auth_url = "https://www.facebook.com/" + GRAPH_VERSION + "/dialog/oauth?" + urllib.parse.urlencode(params)
        return self.send_json({"auth_url": auth_url, "state": state})

    def meta_callback(self, conn, qs):
        code = qs.get("code", [""])[0]
        state = qs.get("state", [""])[0]
        if not code:
            return self.popup_close("Erro: codigo nao recebido.")
        st = conn.execute("SELECT * FROM oauth_states WHERE state=? AND used=0", (state,)).fetchone()
        if not st:
            return self.popup_close("Erro: state invalido (anti-CSRF).")
        conn.execute("UPDATE oauth_states SET used=1 WHERE state=?", (state,))
        conn.commit()

        app_id, app_secret = get_meta_config(conn)
        # 1) code -> short-lived token
        data, err = graph_get("oauth/access_token", {
            "client_id": app_id,
            "redirect_uri": redirect_uri(),
            "client_secret": app_secret,
            "code": code,
        })
        if err or not data:
            return self.popup_close("Erro ao trocar code: " + str(err))
        short_token = data.get("access_token")
        # 2) short -> long-lived (60 dias)
        ll, err2 = graph_get("oauth/access_token", {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        })
        token = ll.get("access_token") if ll and not err2 else short_token
        expires_in = ll.get("expires_in") if ll else None
        expires_at = None
        if expires_in:
            expires_at = (datetime.datetime.utcnow() + datetime.timedelta(seconds=int(expires_in))).strftime("%Y-%m-%dT%H:%M:%SZ")

        me, _ = graph_get("me", {"access_token": token, "fields": "id,name"})
        fb_id = me.get("id") if me else None
        fb_name = me.get("name") if me else "Usuario Meta"

        cur = conn.execute("""INSERT INTO facebook_connections
            (fb_user_id, fb_user_name, access_token, token_type, expires_at, is_permanent, created_at)
            VALUES (?,?,?,?,?,0,?)""",
            (fb_id, fb_name, token, "oauth", expires_at, now_iso()))
        conn.commit()
        conn_id = cur.lastrowid
        self.import_ad_accounts(conn, conn_id, token)
        return self.popup_close("Conta Meta conectada com sucesso! Pode fechar esta janela.")

    def import_ad_accounts(self, conn, connection_id, token):
        accounts, err = fetch_all_meta_pages("me/adaccounts", {
            "access_token": token,
            "fields": "account_id,name,currency,account_status",
            "limit": 100,
        })
        for a in accounts:
            acc_id = a.get("account_id") or a.get("id", "").replace("act_", "")
            conn.execute("""INSERT INTO facebook_ad_accounts
                (connection_id, account_id, name, currency, account_status, is_selected, created_at)
                VALUES (?,?,?,?,?,1,?)
                ON CONFLICT(account_id) DO UPDATE SET name=excluded.name, currency=excluded.currency,
                account_status=excluded.account_status, connection_id=excluded.connection_id""",
                (connection_id, acc_id, a.get("name"), a.get("currency"), a.get("account_status"), now_iso()))
        conn.commit()
        return err

    def add_manual_account(self, conn, body):
        token = (body.get("token") or "").strip()
        if not token:
            return self.send_json({"error": "Token obrigatorio"}, 400)
        me, err = graph_get("me", {"access_token": token, "fields": "id,name"})
        if err or not me:
            return self.send_json({"error": "Token invalido: " + str(err)}, 400)
        cur = conn.execute("""INSERT INTO facebook_connections
            (fb_user_id, fb_user_name, access_token, token_type, expires_at, is_permanent, created_at)
            VALUES (?,?,?,?,?,1,?)""",
            (me.get("id"), me.get("name", "System User"), token, "permanent", None, now_iso()))
        conn.commit()
        err2 = self.import_ad_accounts(conn, cur.lastrowid, token)
        count = conn.execute("SELECT COUNT(*) FROM facebook_ad_accounts").fetchone()[0]
        return self.send_json({"ok": True, "user": me.get("name"), "accounts": count, "warn": err2})

    def meta_disconnect(self, conn):
        token = active_token(conn)
        revoked = None
        if token:
            _, revoked = graph_delete("me/permissions", {"access_token": token})
        conn.execute("DELETE FROM facebook_ad_accounts")
        conn.execute("DELETE FROM facebook_connections")
        conn.commit()
        return self.send_json({"ok": True, "revoke_note": revoked})

    def popup_close(self, msg):
        html = """<!doctype html><html><head><meta charset="utf-8">
<style>body{background:#0B0F19;color:#e5e7eb;font-family:system-ui;display:flex;
align-items:center;justify-content:center;height:100vh;text-align:center}</style></head>
<body><div><h2>SolutionTrack</h2><p>""" + msg + """</p></div>
<script>try{window.opener&&window.opener.postMessage('solutiontrack-meta-connected','*');}catch(e){}
setTimeout(function(){window.close();},2500);</script></body></html>"""
        return self.send_text(html, "text/html; charset=utf-8")

    # ---------------- SYNC CAMPANHAS ----------------
    def sync_campaigns(self, conn, body):
        token = active_token(conn)
        if not token:
            return self.send_json({"error": "Nenhuma conta conectada"}, 400)
        period = body.get("period", "last7")
        start, end = resolve_period(period, body.get("start"), body.get("end"))
        accounts = conn.execute("SELECT account_id FROM facebook_ad_accounts WHERE is_selected=1").fetchall()
        total = 0
        for acc in accounts:
            act = "act_" + acc["account_id"]
            # Campanhas
            camps, _ = fetch_all_meta_pages(act + "/campaigns", {
                "access_token": token,
                "fields": "id,name,status,objective,daily_budget",
                "limit": 200,
            })
            for cp in camps:
                conn.execute("""INSERT INTO campaigns (account_id, campaign_id, name, status, objective, daily_budget, created_at)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(campaign_id) DO UPDATE SET name=excluded.name, status=excluded.status,
                    objective=excluded.objective, daily_budget=excluded.daily_budget""",
                    (acc["account_id"], cp.get("id"), cp.get("name"), cp.get("status"),
                     cp.get("objective"), float(cp.get("daily_budget", 0) or 0) / 100.0, now_iso()))
            # Adsets
            adsets, _ = fetch_all_meta_pages(act + "/adsets", {
                "access_token": token,
                "fields": "id,name,status,campaign_id,daily_budget",
                "limit": 200,
            })
            for ad in adsets:
                conn.execute("""INSERT INTO ad_sets (account_id, campaign_id, adset_id, name, status, daily_budget, created_at)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(adset_id) DO UPDATE SET name=excluded.name, status=excluded.status,
                    daily_budget=excluded.daily_budget""",
                    (acc["account_id"], ad.get("campaign_id"), ad.get("id"), ad.get("name"),
                     ad.get("status"), float(ad.get("daily_budget", 0) or 0) / 100.0, now_iso()))
            # Ads
            adlist, _ = fetch_all_meta_pages(act + "/ads", {
                "access_token": token,
                "fields": "id,name,status,campaign_id,adset_id",
                "limit": 200,
            })
            for a in adlist:
                conn.execute("""INSERT INTO ads (account_id, campaign_id, adset_id, ad_id, name, status, created_at)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(ad_id) DO UPDATE SET name=excluded.name, status=excluded.status""",
                    (acc["account_id"], a.get("campaign_id"), a.get("adset_id"), a.get("id"),
                     a.get("name"), a.get("status"), now_iso()))
            # Insights por campanha no periodo
            time_range = json.dumps({"since": start.strftime("%Y-%m-%d"), "until": end.strftime("%Y-%m-%d")})
            insights, _ = fetch_all_meta_pages(act + "/insights", {
                "access_token": token,
                "level": "campaign",
                "fields": "campaign_id,spend,impressions,clicks,reach,cpc,cpm,ctr",
                "time_range": time_range,
                "limit": 500,
            })
            conn.execute("DELETE FROM meta_campaign_insights WHERE account_id=?", (acc["account_id"],))
            for ins in insights:
                conn.execute("""INSERT INTO meta_campaign_insights
                    (account_id, campaign_id, date_start, date_stop, spend, impressions, clicks, reach, cpc, cpm, ctr, synced_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (acc["account_id"], ins.get("campaign_id"), start.strftime("%Y-%m-%d"),
                     end.strftime("%Y-%m-%d"), float(ins.get("spend", 0) or 0),
                     int(ins.get("impressions", 0) or 0), int(ins.get("clicks", 0) or 0),
                     int(ins.get("reach", 0) or 0), float(ins.get("cpc", 0) or 0),
                     float(ins.get("cpm", 0) or 0), float(ins.get("ctr", 0) or 0), now_iso()))
                total += 1
        conn.commit()
        return self.send_json({"ok": True, "insights": total})

    def list_level(self, conn, table, qs):
        rows = conn.execute("SELECT * FROM " + table + " ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

    # ---------------- DASHBOARD METRICS ----------------
    def dashboard_metrics(self, conn, qs):
        period = qs.get("period", ["all"])[0]
        start, end = resolve_period(period, qs.get("start", [None])[0], qs.get("end", [None])[0])
        s = start.strftime("%Y-%m-%d")
        e = end.strftime("%Y-%m-%d") + "T23:59:59Z"

        # gasto das contas selecionadas
        spend_row = conn.execute("""
            SELECT COALESCE(SUM(i.spend),0) spend, COALESCE(SUM(i.clicks),0) clicks,
                   COALESCE(SUM(i.impressions),0) impressions
            FROM meta_campaign_insights i
            JOIN facebook_ad_accounts a ON a.account_id = i.account_id
            WHERE a.is_selected = 1
        """).fetchone()
        spend = spend_row["spend"] or 0
        clicks = spend_row["clicks"] or 0
        impressions = spend_row["impressions"] or 0

        orders = conn.execute("""
            SELECT status, payment_method, amount, utm_source
            FROM orders WHERE created_at >= ? AND created_at <= ?
        """, (s, e)).fetchall()

        gross = 0.0
        approved_count = 0
        pending_count = 0
        pending_value = 0.0
        by_source = {}
        for o in orders:
            src = (o["utm_source"] or "direct").lower()
            by_source.setdefault(src, 0.0)
            if o["status"] == "approved":
                gross += o["amount"] or 0
                approved_count += 1
                by_source[src] += o["amount"] or 0
            elif o["status"] == "pending":
                pending_count += 1
                pending_value += o["amount"] or 0

        fs = conn.execute("SELECT * FROM financial_settings ORDER BY id DESC LIMIT 1").fetchone()
        tax = (fs["tax_rate"] or 0) / 100.0
        gw_pct = (fs["gateway_percent"] or 0) / 100.0
        gw_fix = fs["gateway_fixed"] or 0
        prod_pct = (fs["product_cost_percent"] or 0) / 100.0

        taxes = gross * tax
        gateway_fees = gross * gw_pct + gw_fix * approved_count
        product_cost = gross * prod_pct
        net = gross - taxes - gateway_fees
        profit = net - spend - product_cost

        roas = (gross / spend) if spend > 0 else 0
        cpa = (spend / approved_count) if approved_count > 0 else 0
        ctr = (clicks / impressions * 100) if impressions > 0 else 0
        cpc = (spend / clicks) if clicks > 0 else 0
        ticket = (gross / approved_count) if approved_count > 0 else 0

        return self.send_json({
            "period": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")},
            "spend": round(spend, 2),
            "clicks": clicks,
            "impressions": impressions,
            "ctr": round(ctr, 2),
            "cpc": round(cpc, 2),
            "gross_revenue": round(gross, 2),
            "net_revenue": round(net, 2),
            "profit": round(profit, 2),
            "taxes": round(taxes, 2),
            "gateway_fees": round(gateway_fees, 2),
            "product_cost": round(product_cost, 2),
            "approved_count": approved_count,
            "pending_count": pending_count,
            "pending_value": round(pending_value, 2),
            "roas": round(roas, 2),
            "cpa": round(cpa, 2),
            "ticket": round(ticket, 2),
            "by_source": by_source,
            "funnel": self.build_funnel(conn, s, e, clicks, approved_count),
        })

    def build_funnel(self, conn, s, e, clicks, approved):
        pv = conn.execute("SELECT COUNT(*) c FROM tracking_events WHERE event_type='PageView' AND created_at>=? AND created_at<=?", (s, e)).fetchone()["c"]
        ic = conn.execute("SELECT COUNT(*) c FROM tracking_events WHERE event_type='InitiateCheckout' AND created_at>=? AND created_at<=?", (s, e)).fetchone()["c"]
        started = conn.execute("SELECT COUNT(*) c FROM orders WHERE created_at>=? AND created_at<=?", (s, e)).fetchone()["c"]
        return {
            "clicks": clicks,
            "page_views": pv,
            "initiate_checkout": ic,
            "sales_started": started,
            "sales_approved": approved,
        }

    # ---------------- BULK ACTIONS ----------------
    def bulk_status(self, conn, body):
        token = active_token(conn)
        ids = body.get("ids", [])
        level = body.get("level", "campaign")
        new_status = body.get("status", "PAUSED")
        table = {"campaign": "campaigns", "adset": "ad_sets", "ad": "ads"}.get(level, "campaigns")
        id_col = {"campaign": "campaign_id", "adset": "adset_id", "ad": "ad_id"}.get(level, "campaign_id")
        errors = []
        for item_id in ids:
            conn.execute("UPDATE " + table + " SET status=? WHERE " + id_col + "=?", (new_status, item_id))
            if token:
                _, err = graph_post(str(item_id), {"status": new_status, "access_token": token})
                if err:
                    errors.append({"id": item_id, "error": err})
        conn.commit()
        return self.send_json({"ok": True, "updated": len(ids), "errors": errors})

    # ---------------- CAPI TEST ----------------
    def capi_test(self, conn, body):
        pix = conn.execute("SELECT * FROM meta_pixels WHERE id=?", (body.get("pixel_db_id"),)).fetchone()
        if not pix:
            pix = conn.execute("SELECT * FROM meta_pixels WHERE is_active=1 ORDER BY id DESC LIMIT 1").fetchone()
        if not pix:
            return self.send_json({"error": "Nenhum pixel configurado"}, 400)
        order = {
            "external_id": "test_" + uuid.uuid4().hex[:8],
            "customer_email": body.get("email", "teste@solutiontrack.com"),
            "customer_phone": body.get("phone", ""),
            "amount": float(body.get("amount", 97.0) or 97.0),
            "currency": "BRL",
            "client_ip": self.client_ip(),
            "user_agent": self.headers.get("User-Agent", ""),
        }
        resp, err = send_capi_event(conn, pix, order, "Purchase", test=True)
        return self.send_json({"ok": not err, "response": resp, "error": err})

    def create_test_order(self, conn, body):
        conn.execute("""INSERT INTO orders
            (external_id, platform, status, payment_method, product_name, customer_name,
             customer_email, amount, utm_source, utm_campaign, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("test_" + uuid.uuid4().hex[:8], "test", "approved", "pix",
             body.get("product", "Produto Teste"), "Cliente Teste",
             "teste@solutiontrack.com", float(body.get("amount", 97.0) or 97.0),
             "facebook", "Campanha Teste", now_iso(), now_iso()))
        conn.commit()
        return self.send_json({"ok": True})

    # ---------------- TRACKING (pixel) ----------------
    def handle_track(self, body):
        conn = get_db()
        try:
            conn.execute("""INSERT INTO tracking_events
                (event_type, event_id, url, utm_source, utm_medium, utm_campaign,
                 utm_content, utm_term, fbc, fbp, client_ip, user_agent, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (body.get("event_type", "PageView"), uuid.uuid4().hex, body.get("url"),
                 body.get("utm_source"), body.get("utm_medium"), body.get("utm_campaign"),
                 body.get("utm_content"), body.get("utm_term"), body.get("fbc"),
                 body.get("fbp"), self.client_ip(), self.headers.get("User-Agent", ""), now_iso()))
            conn.commit()
            return self.send_json({"ok": True})
        finally:
            conn.close()

    # ---------------- WEBHOOKS ----------------
    def handle_webhook(self, path, body):
        platform = path.split("/api/webhook/")[-1].strip("/")
        if platform not in ("kiwify", "hotmart", "greenn", "cakto", "wiapy"):
            return self.send_json({"error": "plataforma nao suportada"}, 404)
        conn = get_db()
        try:
            order = parse_webhook(platform, body)
            order["client_ip"] = self.client_ip()
            order["user_agent"] = self.headers.get("User-Agent", "")
            order["currency"] = "BRL"

            existing = conn.execute("SELECT id FROM orders WHERE external_id=? AND platform=?",
                                    (order["external_id"], platform)).fetchone()
            if existing:
                conn.execute("""UPDATE orders SET status=?, amount=?, updated_at=? WHERE id=?""",
                             (order["status"], order["amount"], now_iso(), existing["id"]))
            else:
                conn.execute("""INSERT INTO orders
                    (external_id, platform, status, payment_method, product_name, customer_name,
                     customer_email, customer_phone, amount, currency, utm_source, utm_medium,
                     utm_campaign, utm_content, utm_term, src, sck, fbc, fbp, client_ip, user_agent,
                     raw_payload, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (order["external_id"], platform, order["status"], order["payment_method"],
                     order["product_name"], order["customer_name"], order["customer_email"],
                     order["customer_phone"], order["amount"], order["currency"],
                     order["utm_source"], order["utm_medium"], order["utm_campaign"],
                     order["utm_content"], order["utm_term"], order["src"], order["sck"],
                     order["fbc"], order["fbp"], order["client_ip"], order["user_agent"],
                     json.dumps(body)[:10000], now_iso(), now_iso()))
            conn.commit()

            # Dispara CAPI Purchase se aprovado e houver pixel ativo
            if order["status"] == "approved":
                for pix in conn.execute("SELECT * FROM meta_pixels WHERE is_active=1").fetchall():
                    try:
                        send_capi_event(conn, pix, order, "Purchase", test=False)
                    except Exception as ex:
                        print("[CAPI] " + str(ex))

            return self.send_json({"ok": True, "status": order["status"]})
        finally:
            conn.close()

    # ---------------- PIXEL SCRIPT ----------------
    def serve_pixel_script(self):
        path = os.path.join(BASE_DIR, "solutiontrack.js")
        base = public_base_url()
        try:
            with open(path, "r", encoding="utf-8") as f:
                js = f.read()
        except FileNotFoundError:
            js = "console.error('solutiontrack.js nao encontrado');"
        js = js.replace("__SOLUTIONTRACK_ENDPOINT__", base)
        self.send_text(js, "application/javascript; charset=utf-8")


# ---------------------------------------------------------------------------
# SERVIDOR THREADED + SHUTDOWN GRACIOSO
# ---------------------------------------------------------------------------
class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", "10000"))
    init_db()

    # Motor de regras em background
    t = threading.Thread(target=rules_engine_loop, daemon=True)
    t.start()

    server = ThreadedHTTPServer(("0.0.0.0", port), Handler)
    print("========================================================")
    print(" SolutionTrack rodando na porta " + str(port))
    print(" URL base: " + public_base_url())
    print(" Redirect OAuth: " + redirect_uri())
    print("========================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[SERVER] Encerrando graciosamente...")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
