"""
Shop Server v3 — SQLite (local) or PostgreSQL+Cloudinary (cloud)
"""
import http.server, json, sqlite3, hashlib, secrets, os, sys, re
import threading, webbrowser, urllib.request, base64, smtplib
from email.mime.text import MIMEText
from urllib.parse import unquote
from datetime import datetime, timedelta

DATABASE_URL   = os.environ.get("DATABASE_URL", "")
CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL", "")
USE_PG         = bool(DATABASE_URL)
USE_CDN        = bool(CLOUDINARY_URL)
IS_CLOUD       = bool(os.environ.get("RENDER") or os.environ.get("RAILWAY_ENVIRONMENT"))
SITE_URL       = os.environ.get("SITE_URL", "http://localhost:8767")
PORT           = int(os.environ.get("PORT", 8767))
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DATA_DIR       = os.environ.get("DATA_DIR", BASE_DIR)
DB_PATH        = os.path.join(DATA_DIR, "shop.db")
HTML_FILE      = os.path.join(BASE_DIR, "shop.html")
UPLOADS_DIR    = os.path.join(DATA_DIR, "uploads")
if not USE_PG: os.makedirs(UPLOADS_DIR, exist_ok=True)

if USE_PG:
    try: import psycopg2, psycopg2.pool, psycopg2.extras
    except ImportError: print("pip install psycopg2-binary"); sys.exit(1)
if USE_CDN:
    try:
        import cloudinary, cloudinary.uploader
        cloudinary.config(cloudinary_url=CLOUDINARY_URL)
    except ImportError: print("pip install cloudinary"); sys.exit(1)

# ── DB Wrapper ──────────────────────────────────────────────────────────────
_pg_pool = None
def _build_pg_pool():
    global _pg_pool
    _pg_pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL)

class _NoLock:
    def __enter__(self): return self
    def __exit__(self, *_): pass

_db_lock = _NoLock() if USE_PG else threading.Lock()

class _PGRow(dict):
    def keys(self): return list(super().keys())

class _CP:  # CursorProxy
    def __init__(self, c): self._c = c
    def fetchone(self):
        r = self._c.fetchone(); return _PGRow(r) if r else None
    def fetchall(self): return [_PGRow(r) for r in self._c.fetchall()]
    @property
    def lastrowid(self):
        r = self._c.fetchone(); return r['id'] if r else None
    def __iter__(self): return iter(self.fetchall())

def _pgsql(s):
    s = s.replace("?", "%s")
    s = re.sub(r"datetime\('now','localtime'\)", "NOW()", s)
    s = re.sub(r"datetime\('now'\)", "NOW()", s)
    return s

class _DB:
    def __init__(self):
        if USE_PG:
            self._conn = _pg_pool.getconn()
            self._cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")

    def execute(self, sql, params=()):
        if USE_PG:
            self._cur.execute(_pgsql(sql), params or None)
            return _CP(self._cur)
        return self._conn.execute(sql, params)

    def executemany(self, sql, lst):
        if USE_PG:
            sql = _pgsql(sql)
            for p in lst: self._cur.execute(sql, p)
        else: self._conn.executemany(sql, lst)

    def execute_insert(self, sql, params=()):
        if USE_PG:
            sql = _pgsql(sql)
            if "RETURNING" not in sql.upper(): sql = sql.rstrip("; ") + " RETURNING id"
            self._cur.execute(sql, params or None)
            r = self._cur.fetchone(); return r['id'] if r else None
        cur = self._conn.execute(sql, params); return cur.lastrowid

    def upsert_setting(self, key, val):
        if USE_PG:
            self._cur.execute(
                "INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
                (key, val))
        else: self._conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, val))

    def commit(self): self._conn.commit()
    def rollback(self): self._conn.rollback()
    def __enter__(self): return self
    def __exit__(self, exc, *_):
        if USE_PG:
            (self._conn.rollback if exc else self._conn.commit)()
            _pg_pool.putconn(self._conn)
        else:
            if not exc: self._conn.commit()
            self._conn.close()

def get_db(): return _DB()

# ── Init DB ─────────────────────────────────────────────────────────────────
def init_db():
    if USE_PG: _init_pg()
    else: _init_sqlite()

def _init_sqlite():
    with _db_lock, get_db() as db:
        db._conn.executescript("""
            CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE NOT NULL,pw_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'editor',email TEXT DEFAULT '',created_at TEXT DEFAULT(datetime('now','localtime')),last_login TEXT);
            CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,cat TEXT NOT NULL DEFAULT 'ของชำ',price REAL NOT NULL,unit TEXT DEFAULT 'ชิ้น',desc_text TEXT DEFAULT '',emoji TEXT DEFAULT '🛒',img_url TEXT DEFAULT '',img_pos TEXT DEFAULT '50% 50%',created_at TEXT DEFAULT(datetime('now','localtime')),updated_at TEXT DEFAULT(datetime('now','localtime')),in_stock INTEGER DEFAULT 1,price_bulk REAL DEFAULT 0,bulk_qty INTEGER DEFAULT 0,visible INTEGER DEFAULT 1,featured INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL,expires_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,username TEXT,action TEXT,detail TEXT,ip TEXT,ts TEXT DEFAULT(datetime('now','localtime')));
            CREATE TABLE IF NOT EXISTS login_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,ip TEXT,username TEXT,success INTEGER DEFAULT 0,ts TEXT DEFAULT(datetime('now','localtime')));
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS reset_tokens(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL,expires_at TEXT NOT NULL);
        """)
        # migration: add cost_price if not exists
        try: db._conn.execute("ALTER TABLE products ADD COLUMN cost_price REAL DEFAULT 0")
        except: pass
        try: db._conn.execute("ALTER TABLE products ADD COLUMN extra_images TEXT DEFAULT '[]'")
        except: pass
        db.commit()
        _seed_if_empty(db)

def _init_pg():
    with get_db() as db:
        for s in [
            "CREATE TABLE IF NOT EXISTS users(id SERIAL PRIMARY KEY,username TEXT UNIQUE NOT NULL,pw_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'editor',email TEXT DEFAULT '',created_at TEXT DEFAULT to_char(NOW(),'YYYY-MM-DD HH24:MI:SS'),last_login TEXT)",
            "CREATE TABLE IF NOT EXISTS products(id SERIAL PRIMARY KEY,name TEXT NOT NULL,cat TEXT NOT NULL DEFAULT 'ของชำ',price REAL NOT NULL,unit TEXT DEFAULT 'ชิ้น',desc_text TEXT DEFAULT '',emoji TEXT DEFAULT '🛒',img_url TEXT DEFAULT '',img_pos TEXT DEFAULT '50% 50%',created_at TEXT DEFAULT to_char(NOW(),'YYYY-MM-DD HH24:MI:SS'),updated_at TEXT DEFAULT to_char(NOW(),'YYYY-MM-DD HH24:MI:SS'),in_stock INTEGER DEFAULT 1,price_bulk REAL DEFAULT 0,bulk_qty INTEGER DEFAULT 0,visible INTEGER DEFAULT 1,featured INTEGER DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL,expires_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS audit_log(id SERIAL PRIMARY KEY,user_id INTEGER,username TEXT,action TEXT,detail TEXT,ip TEXT,ts TEXT DEFAULT to_char(NOW(),'YYYY-MM-DD HH24:MI:SS'))",
            "CREATE TABLE IF NOT EXISTS login_attempts(id SERIAL PRIMARY KEY,ip TEXT,username TEXT,success INTEGER DEFAULT 0,ts TEXT DEFAULT to_char(NOW(),'YYYY-MM-DD HH24:MI:SS'))",
            "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)",
            "CREATE TABLE IF NOT EXISTS reset_tokens(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL,expires_at TEXT NOT NULL)",
        ]: db.execute(s)
        # migration: add columns if not exists (each in its own transaction to avoid aborted-txn state)
        for col_sql in [
            "ALTER TABLE products ADD COLUMN cost_price REAL DEFAULT 0",
            "ALTER TABLE products ADD COLUMN extra_images TEXT DEFAULT '[]'",
        ]:
            try:
                db.execute(col_sql)
                db.commit()
            except Exception:
                try: db.rollback()
                except Exception: pass
        db.commit()
        _seed_if_empty(db)

def _seed_if_empty(db):
    r = db.execute("SELECT COUNT(*) as n FROM users WHERE role='superadmin'").fetchone()
    if not (r['n'] if isinstance(r, dict) else r[0]):
        db.execute("INSERT INTO users(username,pw_hash,role) VALUES(?,?,?)",
                   ('admin', _hash_pw(os.environ.get("ADMIN_PASSWORD","KhaoSarn@2024")), 'superadmin'))
    r2 = db.execute("SELECT COUNT(*) as n FROM products").fetchone()
    if not (r2['n'] if isinstance(r2, dict) else r2[0]):
        db.executemany("INSERT INTO products(name,cat,price,unit,desc_text,emoji,img_url) VALUES(?,?,?,?,?,?,?)", [
            ('ข้าวเหนียว','ข้าวสาร',180,'ถุง','5 กิโลกรัม','🌾',''),
            ('ข้าวเหนียว','ข้าวสาร',340,'ถุง','10 กิโลกรัม','🌾',''),
            ('ข้าวเหนียว','ข้าวสาร',650,'ถุง','20 กิโลกรัม','🌾',''),
            ('ข้าวหอมมะลิ','ข้าวสาร',220,'ถุง','5 กิโลกรัม','🌾',''),
            ('ข้าวหอมมะลิ','ข้าวสาร',420,'ถุง','10 กิโลกรัม','🌾',''),
            ('ข้าวธรรมดา','ข้าวสาร',160,'ถุง','5 กิโลกรัม','🌾',''),
            ('ถุงพลาสติกหูหิ้ว','บรรจุภัณฑ์',50,'แพ็ค','','🛍️',''),
            ('ถุงพลาสติกใส','บรรจุภัณฑ์',45,'แพ็ค','','🛍️',''),
            ('อาหารหมา','อาหารสัตว์',180,'ถุง','','🐕',''),
            ('อาหารแมว','อาหารสัตว์',120,'ถุง','','🐱',''),
        ])
    db.commit()

# ── Security ────────────────────────────────────────────────────────────────
def _hash_pw(pw, salt=None):
    if not salt: salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 260_000)
    return f"{salt}${h.hex()}"

def _verify_pw(pw, stored):
    try:
        salt, _ = stored.split('$', 1)
        return _hash_pw(pw, salt) == stored
    except: return False

def _now(): return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def _create_session(uid, remember=False):
    tok = secrets.token_urlsafe(40)
    exp = (datetime.now() + timedelta(days=30 if remember else 7)).strftime('%Y-%m-%d %H:%M:%S')
    with _db_lock, get_db() as db:
        db.execute("DELETE FROM sessions WHERE user_id=? OR expires_at < ?", (uid, _now()))
        db.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES(?,?,?)", (tok, uid, exp))
        db.commit()
    return tok

def _get_session_user(tok):
    if not tok: return None
    with get_db() as db:
        r = db.execute(
            "SELECT u.id,u.username,u.role FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.token=? AND s.expires_at>?",
            (tok, _now())).fetchone()
    return dict(r) if r else None

def _delete_session(tok):
    with _db_lock, get_db() as db:
        db.execute("DELETE FROM sessions WHERE token=?", (tok,)); db.commit()

def _rate_ok(ip):
    cut = (datetime.now()-timedelta(minutes=15)).strftime('%Y-%m-%d %H:%M:%S')
    with get_db() as db:
        r = db.execute("SELECT COUNT(*) as n FROM login_attempts WHERE ip=? AND success=0 AND ts>?", (ip, cut)).fetchone()
    return (r['n'] if isinstance(r, dict) else r[0]) < 5

def _log_attempt(ip, u, ok):
    with _db_lock, get_db() as db:
        db.execute("INSERT INTO login_attempts(ip,username,success,ts) VALUES(?,?,?,?)", (ip, u, 1 if ok else 0, _now()))
        db.commit()

def _audit(uid, uname, action, detail, ip):
    with _db_lock, get_db() as db:
        db.execute("INSERT INTO audit_log(user_id,username,action,detail,ip,ts) VALUES(?,?,?,?,?,?)",
                   (uid, uname, action, detail, ip, _now()))
        # auto-clear: keep only latest 1000 entries
        cnt = db.execute("SELECT COUNT(*) as n FROM audit_log").fetchone()
        n = cnt['n'] if isinstance(cnt, dict) else cnt[0]
        if n > 1000:
            db.execute("DELETE FROM audit_log WHERE id IN (SELECT id FROM audit_log ORDER BY id ASC LIMIT ?)", (n - 1000,))
        db.commit()

# ── Email ───────────────────────────────────────────────────────────────────
def _send_reset_email(to, uname, link):
    host, port = os.environ.get("SMTP_HOST","smtp.gmail.com"), int(os.environ.get("SMTP_PORT",587))
    user, pw   = os.environ.get("SMTP_USER",""), os.environ.get("SMTP_PASS","")
    if not user: print(f"[EMAIL] Reset link: {link}"); return
    msg = MIMEText(f"คุณ {uname}\n\nลิงค์รีเซ็ต (1 ชม.):\n{link}", 'plain', 'utf-8')
    msg['Subject'] = '🔑 รีเซ็ตรหัสผ่าน'; msg['From'] = user; msg['To'] = to
    try:
        with smtplib.SMTP(host, port) as s: s.starttls(); s.login(user, pw); s.send_message(msg)
    except Exception as e: print(f"[EMAIL] Error: {e}\n[EMAIL] link: {link}")

# ── Handler ──────────────────────────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self._cors()
        self.end_headers()

    def do_GET(self):
        if IS_CLOUD and self.headers.get('X-Forwarded-Proto','https') == 'http':
            self.send_response(301)
            self.send_header('Location', f"https://{self.headers.get('Host')}{self.path}")
            self.end_headers(); return
        p = self.path.split('?')[0]
        {'/': self._html, '/index.html': self._html,
         '/api/products': self._get_products, '/api/me': self._get_me,
         '/api/resolve-tiktok': self._resolve_tiktok,
         '/api/audit': self._get_audit, '/api/users': self._get_users,
         '/api/settings': self._get_settings}.get(p, lambda: (
            self._serve_upload(p) if p.startswith('/uploads/') else
            self._proxy() if self.path.startswith('/proxy?url=') else
            self.send_error(404)
        ))()

    def do_POST(self):
        p = self.path.split('?')[0]
        {'/api/login': self._login, '/api/logout': self._logout,
         '/api/products': self._add_product, '/api/products/bulk': self._bulk_add_products,
         '/api/users': self._add_user,
         '/api/upload': self._upload_image, '/api/settings': self._put_settings,
         '/api/forgot-password': self._forgot_pw, '/api/reset-password': self._reset_pw
         }.get(p, lambda: self.send_error(404))()

    def do_PUT(self):
        p = self.path.split('?')[0]
        if p == '/api/products/reset-stock':   return self._reset_all_stock()
        if p == '/api/products/reset-visible': return self._reset_all_visible()
        for pat, fn in [
            (r'^/api/products/(\d+)/featured$', lambda m: self._toggle_featured(int(m.group(1)))),
            (r'^/api/products/(\d+)/stock$',    lambda m: self._toggle_stock(int(m.group(1)))),
            (r'^/api/products/(\d+)/visible$',  lambda m: self._toggle_visible(int(m.group(1)))),
            (r'^/api/products/(\d+)$',          lambda m: self._update_product(int(m.group(1)))),
            (r'^/api/users/(\d+)/password$',    lambda m: self._change_pw(int(m.group(1)))),
            (r'^/api/users/(\d+)/reset-password$', lambda m: self._admin_reset_pw(int(m.group(1)))),
            (r'^/api/users/(\d+)/email$',       lambda m: self._update_email(int(m.group(1)))),
        ]:
            m = re.match(pat, p)
            if m: return fn(m)
        self.send_error(404)

    def do_DELETE(self):
        p = self.path.split('?')[0]
        m = re.match(r'^/api/products/(\d+)$', p)
        if m: return self._del_product(int(m.group(1)))
        m = re.match(r'^/api/users/(\d+)$', p)
        if m: return self._del_user(int(m.group(1)))
        self.send_error(404)

    # ── helpers ──
    def _cors(self):
        o = self.headers.get('Origin','*')
        self.send_header('Access-Control-Allow-Origin', o)
        self.send_header('Access-Control-Allow-Methods','GET,POST,PUT,DELETE,OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type,Authorization')
        self.send_header('Vary','Origin')

    def _sec(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','SAMEORIGIN')
        self.send_header('Referrer-Policy','strict-origin-when-cross-origin')

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self._cors(); self._sec(); self.end_headers(); self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length',0)); return json.loads(self.rfile.read(n))
    def _token(self):
        a = self.headers.get('Authorization',''); return a[7:] if a.startswith('Bearer ') else None
    def _user(self): return _get_session_user(self._token())
    def _ip(self): return self.headers.get('X-Forwarded-For', self.client_address[0]).split(',')[0].strip()

    # ── routes ──
    def _html(self):
        try:
            with open(HTML_FILE,'rb') as f: data=f.read()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length', len(data))
            self._sec(); self.end_headers(); self.wfile.write(data)
        except FileNotFoundError: self.send_error(404)

    def _get_products(self):
        u = self._user()
        is_admin = bool(u)
        with get_db() as db:
            rows = db.execute(
                "SELECT id,name,cat,price,unit,desc_text,emoji,img_url,img_pos,"
                "COALESCE(in_stock,1) as in_stock,COALESCE(price_bulk,0) as price_bulk,"
                "COALESCE(bulk_qty,0) as bulk_qty,COALESCE(visible,1) as visible,"
                "COALESCE(featured,0) as featured,COALESCE(cost_price,0) as cost_price,"
                "COALESCE(extra_images,'[]') as extra_images FROM products ORDER BY featured DESC,cat,name,price"
            ).fetchall()
        result = []
        for r in rows:
            row = dict(r)
            if not is_admin:
                row.pop('cost_price', None)
            result.append(row)
        self._json(result)

    def _get_me(self):
        u = self._user(); self._json(u if u else {'error':'unauthorized'}, 200 if u else 401)

    def _login(self):
        ip = self._ip()
        try:
            d = self._body(); uname = d.get('username','').strip(); pw = d.get('password','')
        except: self._json({'error':'invalid'},400); return
        if not _rate_ok(ip): self._json({'error':'ลองผิดหลายครั้ง รอ 15 นาที'},429); return
        with get_db() as db:
            r = db.execute("SELECT id,username,pw_hash,role FROM users WHERE username=?", (uname,)).fetchone()
        if r and _verify_pw(pw, r['pw_hash']):
            _log_attempt(ip, uname, True)
            tok = _create_session(r['id'], bool(d.get('remember')))
            with _db_lock, get_db() as db:
                db.execute("UPDATE users SET last_login=? WHERE id=?", (_now(), r['id'])); db.commit()
            _audit(r['id'], uname, 'LOGIN', 'เข้าสู่ระบบ', ip)
            self._json({'token': tok, 'role': r['role'], 'username': uname})
        else:
            _log_attempt(ip, uname, False)
            self._json({'error':'username หรือ password ไม่ถูกต้อง'},401)

    def _logout(self):
        u = self._user()
        if u: _audit(u['id'],u['username'],'LOGOUT','ออกจากระบบ',self._ip()); _delete_session(self._token())
        self._json({'ok':True})

    def _add_product(self):
        u = self._user()
        if not u: self._json({'error':'กรุณาเข้าสู่ระบบ'},401); return
        try:
            d = self._body(); name = d.get('name','').strip(); price = float(d.get('price',0))
            if not name or price <= 0: raise ValueError
        except: self._json({'error':'ข้อมูลไม่ครบ'},400); return
        with _db_lock, get_db() as db:
            nid = db.execute_insert(
                "INSERT INTO products(name,cat,price,unit,desc_text,emoji,img_url,img_pos,in_stock,price_bulk,bulk_qty,cost_price,extra_images)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name,d.get('cat','ของชำ'),price,d.get('unit','ชิ้น'),d.get('desc_text',''),
                 d.get('emoji','🛒'),d.get('img_url',''),d.get('img_pos','50% 50%'),1,
                 float(d.get('price_bulk',0) or 0),int(d.get('bulk_qty',0) or 0),
                 float(d.get('cost_price',0) or 0),d.get('extra_images','[]')))
            db.commit()
        _audit(u['id'],u['username'],'ADD_PRODUCT',f"เพิ่ม:{name} ฿{price}",self._ip())
        self._json({'id':nid,'ok':True})

    def _bulk_add_products(self):
        u = self._user()
        if not u: self._json({'error':'กรุณาเข้าสู่ระบบ'},401); return
        try:
            d = self._body()
            items = d.get('products', [])
            if not isinstance(items, list) or len(items) == 0: raise ValueError
        except: self._json({'error':'ข้อมูลไม่ถูกต้อง'},400); return
        added = updated = 0
        now = _now()
        with _db_lock, get_db() as db:
            for row in items:
                name = str(row.get('name','')).strip()
                cat  = str(row.get('cat','ของชำ')).strip() or 'ของชำ'
                try: price = float(row.get('price',0))
                except: price = 0
                if not name or price <= 0: continue
                unit      = str(row.get('unit','ชิ้น')).strip() or 'ชิ้น'
                desc_text = str(row.get('desc_text','')).strip()
                price_bulk= float(row.get('price_bulk',0) or 0)
                bulk_qty  = int(float(row.get('bulk_qty',0) or 0))
                cost_price= float(row.get('cost_price',0) or 0)
                # upsert: match on name (case-insensitive)
                ex = db.execute("SELECT id FROM products WHERE LOWER(name)=LOWER(?)", (name,)).fetchone()
                if ex:
                    pid = ex['id'] if isinstance(ex, dict) else ex[0]
                    db.execute(
                        "UPDATE products SET cat=?,price=?,unit=?,desc_text=?,price_bulk=?,bulk_qty=?,cost_price=?,updated_at=? WHERE id=?",
                        (cat, price, unit, desc_text, price_bulk, bulk_qty, cost_price, now, pid))
                    updated += 1
                else:
                    emoji = str(row.get('emoji','🛒')).strip() or '🛒'
                    db.execute_insert(
                        "INSERT INTO products(name,cat,price,unit,desc_text,emoji,in_stock,price_bulk,bulk_qty,cost_price)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (name, cat, price, unit, desc_text, emoji, 1, price_bulk, bulk_qty, cost_price))
                    added += 1
            db.commit()
        _audit(u['id'],u['username'],'BULK_IMPORT',f"เพิ่ม {added} อัพเดต {updated} รายการ",self._ip())
        self._json({'ok':True,'added':added,'updated':updated})

    def _update_product(self, pid):
        u = self._user()
        if not u: self._json({'error':'กรุณาเข้าสู่ระบบ'},401); return
        try: d = self._body()
        except: self._json({'error':'invalid json'},400); return
        with _db_lock, get_db() as db:
            ex = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            if not ex: self._json({'error':'ไม่พบสินค้า'},404); return
            now = _now()
            ex = dict(ex)
            db.execute(
                "UPDATE products SET name=?,cat=?,price=?,unit=?,desc_text=?,emoji=?,img_url=?,img_pos=?,price_bulk=?,bulk_qty=?,cost_price=?,extra_images=?,updated_at=? WHERE id=?",
                (d.get('name',ex['name']),d.get('cat',ex['cat']),float(d.get('price',ex['price'])),
                 d.get('unit',ex['unit']),d.get('desc_text',ex['desc_text']),d.get('emoji',ex['emoji']),
                 d.get('img_url',ex['img_url']),d.get('img_pos',ex.get('img_pos','50% 50%')),
                 float(d.get('price_bulk',0) or 0),int(d.get('bulk_qty',0) or 0),
                 float(d.get('cost_price',0) or 0),d.get('extra_images',ex.get('extra_images','[]')),now,pid))
            db.commit()
        _audit(u['id'],u['username'],'UPDATE_PRODUCT',f"#{pid}",self._ip()); self._json({'ok':True})

    def _del_product(self, pid):
        u = self._user()
        if not u: self._json({'error':'กรุณาเข้าสู่ระบบ'},401); return
        if u['role'] != 'superadmin': self._json({'error':'ไม่มีสิทธิ์'},403); return
        with _db_lock, get_db() as db:
            r = db.execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone()
            if not r: self._json({'error':'ไม่พบสินค้า'},404); return
            db.execute("DELETE FROM products WHERE id=?", (pid,)); db.commit()
        _audit(u['id'],u['username'],'DELETE_PRODUCT',f"ลบ:{r['name']}",self._ip()); self._json({'ok':True})

    def _get_audit(self):
        u = self._user()
        if not u or u['role'] != 'superadmin': self._json({'error':'ไม่มีสิทธิ์'},403); return
        with get_db() as db:
            rows = db.execute("SELECT id,username,action,detail,ip,ts FROM audit_log ORDER BY id DESC LIMIT 300").fetchall()
        self._json([dict(r) for r in rows])

    def _get_users(self):
        u = self._user()
        if not u or u['role'] != 'superadmin': self._json({'error':'ไม่มีสิทธิ์'},403); return
        with get_db() as db:
            rows = db.execute("SELECT id,username,role,created_at,last_login FROM users ORDER BY role,username").fetchall()
        self._json([dict(r) for r in rows])

    def _add_user(self):
        u = self._user()
        if not u or u['role'] != 'superadmin': self._json({'error':'ไม่มีสิทธิ์'},403); return
        try:
            d = self._body(); uname = d.get('username','').strip(); pw = d.get('password','')
            role = d.get('role','editor')
            if not uname or not pw or role not in ('superadmin','editor'): raise ValueError
        except: self._json({'error':'ข้อมูลไม่ถูกต้อง'},400); return
        try:
            with _db_lock, get_db() as db:
                db.execute("INSERT INTO users(username,pw_hash,role) VALUES(?,?,?)", (uname,_hash_pw(pw),role))
                db.commit()
        except Exception as e:
            if any(x in str(e).lower() for x in ['unique','duplicate','23505']):
                self._json({'error':'username นี้มีอยู่แล้ว'},409); return
            raise
        _audit(u['id'],u['username'],'ADD_USER',f"เพิ่ม:{uname}",self._ip()); self._json({'ok':True})

    def _del_user(self, uid):
        u = self._user()
        if not u or u['role'] != 'superadmin': self._json({'error':'ไม่มีสิทธิ์'},403); return
        if uid == u['id']: self._json({'error':'ลบตัวเองไม่ได้'},400); return
        with _db_lock, get_db() as db:
            r = db.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
            if not r: self._json({'error':'ไม่พบ user'},404); return
            db.execute("DELETE FROM users WHERE id=?", (uid,))
            db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            db.commit()
        _audit(u['id'],u['username'],'DELETE_USER',f"ลบ:{r['username']}",self._ip()); self._json({'ok':True})

    def _change_pw(self, uid):
        u = self._user()
        if not u: self._json({'error':'กรุณาเข้าสู่ระบบ'},401); return
        if u['id'] != uid and u['role'] != 'superadmin': self._json({'error':'ไม่มีสิทธิ์'},403); return
        try:
            d = self._body(); np = d.get('new_password','')
            if not np or len(np) < 6: raise ValueError('ต้องอย่างน้อย 6 ตัวอักษร')
        except ValueError as e: self._json({'error':str(e)},400); return
        if u['id'] == uid:
            with get_db() as db: r = db.execute("SELECT pw_hash FROM users WHERE id=?", (uid,)).fetchone()
            if not _verify_pw(d.get('old_password',''), r['pw_hash']):
                self._json({'error':'รหัสผ่านเดิมไม่ถูกต้อง'},401); return
        with _db_lock, get_db() as db:
            db.execute("UPDATE users SET pw_hash=? WHERE id=?", (_hash_pw(np),uid))
            db.execute("DELETE FROM sessions WHERE user_id=?", (uid,)); db.commit()
        _audit(u['id'],u['username'],'CHANGE_PW',f"uid:{uid}",self._ip()); self._json({'ok':True})

    def _admin_reset_pw(self, tuid):
        u = self._user()
        if not u or u['role'] != 'superadmin': self._json({'error':'ไม่มีสิทธิ์'},403); return
        if u['id'] == tuid: self._json({'error':'ใช้หน้าเปลี่ยนรหัสตัวเองแทน'},400); return
        try:
            d = self._body(); np = d.get('new_password','')
            if not np or len(np) < 6: raise ValueError('ต้องอย่างน้อย 6 ตัวอักษร')
        except ValueError as e: self._json({'error':str(e)},400); return
        with get_db() as db: r = db.execute("SELECT username FROM users WHERE id=?", (tuid,)).fetchone()
        if not r: self._json({'error':'ไม่พบ user'},404); return
        with _db_lock, get_db() as db:
            db.execute("UPDATE users SET pw_hash=? WHERE id=?", (_hash_pw(np),tuid))
            db.execute("DELETE FROM sessions WHERE user_id=?", (tuid,)); db.commit()
        _audit(u['id'],u['username'],'ADMIN_RESET_PW',f"รีเซ็ต:{r['username']}",self._ip()); self._json({'ok':True})

    def _get_settings(self):
        with get_db() as db: rows = db.execute("SELECT key,value FROM settings").fetchall()
        self._json({r['key']:r['value'] for r in rows})

    def _put_settings(self):
        u = self._user()
        if not u or u['role'] != 'superadmin': self._json({'error':'ไม่มีสิทธิ์'},403); return
        try: d = self._body()
        except: self._json({'error':'invalid json'},400); return
        with _db_lock, get_db() as db:
            for k,v in d.items(): db.upsert_setting(k, v)
            db.commit()
        _audit(u['id'],u['username'],'SETTINGS',f"{list(d.keys())}",self._ip()); self._json({'ok':True})

    def _serve_upload(self, path):
        if USE_CDN: self.send_error(404); return
        fp = os.path.join(UPLOADS_DIR, os.path.basename(path))
        if not os.path.exists(fp): self.send_error(404); return
        ext = fp.rsplit('.',1)[-1].lower()
        mime = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','gif':'image/gif','webp':'image/webp'}.get(ext,'application/octet-stream')
        with open(fp,'rb') as f: data=f.read()
        self.send_response(200)
        self.send_header('Content-Type',mime); self.send_header('Content-Length',len(data))
        self.send_header('Cache-Control','max-age=86400'); self.end_headers(); self.wfile.write(data)

    def _upload_image(self):
        u = self._user()
        if not u: self._json({'error':'กรุณาเข้าสู่ระบบ'},401); return
        if u['role'] != 'superadmin': self._json({'error':'ไม่มีสิทธิ์'},403); return
        try:
            d = self._body(); durl = d.get('data','')
            if not durl.startswith('data:image/'): self._json({'error':'รูปแบบไม่ถูกต้อง'},400); return
            hdr, b64 = durl.split(',',1)
            ext = hdr.split('/')[1].split(';')[0].lower()
            if ext == 'jpeg': ext = 'jpg'
            if ext not in ('jpg','png','gif','webp'): self._json({'error':'รองรับ jpg,png,gif,webp'},400); return
            if len(base64.b64decode(b64)) > 8*1024*1024: self._json({'error':'ไฟล์ใหญ่เกิน 8MB'},400); return
            if USE_CDN:
                res = cloudinary.uploader.upload(durl, folder="shop", resource_type="image")
                url = res['secure_url']
            else:
                fn = f"{secrets.token_hex(8)}.{ext}"
                with open(os.path.join(UPLOADS_DIR,fn),'wb') as f: f.write(base64.b64decode(b64))
                url = f"/uploads/{fn}"
            _audit(u['id'],u['username'],'UPLOAD','อัพโหลดรูป',self._ip()); self._json({'url':url})
        except Exception as e: self._json({'error':str(e)},500)

    def _reset_all_stock(self):
        u = self._user()
        if not u: self._json({'error':'กรุณาเข้าสู่ระบบ'},401); return
        with _db_lock, get_db() as db: db.execute("UPDATE products SET in_stock=1"); db.commit()
        _audit(u['id'],u['username'],'RESET_STOCK','รีเซ็ตเปิดขายทั้งหมด',self._ip()); self._json({'ok':True})

    def _reset_all_visible(self):
        u = self._user()
        if not u: self._json({'error':'กรุณาเข้าสู่ระบบ'},401); return
        with _db_lock, get_db() as db: db.execute("UPDATE products SET visible=1"); db.commit()
        _audit(u['id'],u['username'],'RESET_VISIBLE','แสดงทั้งหมด',self._ip()); self._json({'ok':True})

    def _toggle_stock(self, pid):
        u = self._user()
        if not u: self._json({'error':'กรุณาเข้าสู่ระบบ'},401); return
        with _db_lock, get_db() as db:
            r = db.execute("SELECT in_stock,name FROM products WHERE id=?", (pid,)).fetchone()
            if not r: self._json({'error':'ไม่พบสินค้า'},404); return
            ns = 0 if r['in_stock'] else 1
            db.execute("UPDATE products SET in_stock=? WHERE id=?", (ns,pid)); db.commit()
        _audit(u['id'],u['username'],'STOCK',f"{'มีสินค้า' if ns else 'หมด'}:{r['name']}",self._ip())
        self._json({'ok':True,'in_stock':ns})

    def _toggle_visible(self, pid):
        u = self._user()
        if not u: self._json({'error':'กรุณาเข้าสู่ระบบ'},401); return
        with _db_lock, get_db() as db:
            r = db.execute("SELECT COALESCE(visible,1) as visible,name FROM products WHERE id=?", (pid,)).fetchone()
            if not r: self._json({'error':'ไม่พบสินค้า'},404); return
            nv = 0 if r['visible'] else 1
            db.execute("UPDATE products SET visible=? WHERE id=?", (nv,pid)); db.commit()
        _audit(u['id'],u['username'],'VISIBLE',f"{'แสดง' if nv else 'ซ่อน'}:{r['name']}",self._ip())
        self._json({'ok':True,'visible':nv})

    def _toggle_featured(self, pid):
        u = self._user()
        if not u: self._json({'error':'กรุณาเข้าสู่ระบบ'},401); return
        with _db_lock, get_db() as db:
            r = db.execute("SELECT COALESCE(featured,0) as featured,name FROM products WHERE id=?", (pid,)).fetchone()
            if not r: self._json({'error':'ไม่พบสินค้า'},404); return
            nf = 0 if r['featured'] else 1
            db.execute("UPDATE products SET featured=? WHERE id=?", (nf,pid)); db.commit()
        _audit(u['id'],u['username'],'FEATURED',f"{'แนะนำ' if nf else 'ยกเลิก'}:{r['name']}",self._ip())
        self._json({'ok':True,'featured':nf})

    def _update_email(self, uid):
        u = self._user()
        if not u: self._json({'error':'กรุณาเข้าสู่ระบบ'},401); return
        if u['id'] != uid and u['role'] != 'superadmin': self._json({'error':'ไม่มีสิทธิ์'},403); return
        try: d = self._body(); email = d.get('email','').strip()
        except: self._json({'error':'invalid'},400); return
        with _db_lock, get_db() as db: db.execute("UPDATE users SET email=? WHERE id=?", (email,uid)); db.commit()
        _audit(u['id'],u['username'],'UPDATE_EMAIL',f"uid:{uid}",self._ip()); self._json({'ok':True})

    def _forgot_pw(self):
        try: d = self._body(); uname = d.get('username','').strip()
        except: self._json({'error':'invalid'},400); return
        with get_db() as db: r = db.execute("SELECT id,email FROM users WHERE username=?", (uname,)).fetchone()
        msg = {'ok':True,'msg':'ถ้า username ถูกต้องและมี email จะได้รับลิงค์'}
        if not r or not r['email']: self._json(msg); return
        tok = secrets.token_urlsafe(32)
        exp = (datetime.now()+timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
        with _db_lock, get_db() as db:
            db.execute("DELETE FROM reset_tokens WHERE user_id=?", (r['id'],))
            db.execute("INSERT INTO reset_tokens(token,user_id,expires_at) VALUES(?,?,?)", (tok,r['id'],exp))
            db.commit()
        _send_reset_email(r['email'], uname, f"{SITE_URL}/?reset={tok}")
        self._json(msg)

    def _reset_pw(self):
        try:
            d = self._body(); tok = d.get('token',''); np = d.get('new_password','')
            if not tok or not np or len(np) < 6: raise ValueError
        except: self._json({'error':'ข้อมูลไม่ครบ'},400); return
        with get_db() as db: r = db.execute("SELECT user_id FROM reset_tokens WHERE token=? AND expires_at>?", (tok,_now())).fetchone()
        if not r: self._json({'error':'ลิงค์หมดอายุหรือไม่ถูกต้อง'},400); return
        uid = r['user_id']
        with _db_lock, get_db() as db:
            db.execute("UPDATE users SET pw_hash=? WHERE id=?", (_hash_pw(np),uid))
            db.execute("DELETE FROM reset_tokens WHERE token=?", (tok,))
            db.execute("DELETE FROM sessions WHERE user_id=?", (uid,)); db.commit()
        _audit(uid,'(reset)','RESET_PW','รีเซ็ตรหัสผ่านผ่านอีเมล',self._ip()); self._json({'ok':True})

    def _resolve_tiktok(self):
        raw = self.path.split('?url=')[-1] if '?url=' in self.path else ''
        if not raw:
            try: raw = self._body().get('url','')
            except: pass
        if not raw: self._json({'error':'no url'},400); return
        try:
            req = urllib.request.Request(unquote(raw), headers={"User-Agent":"Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=10)
            final = resp.url
            m = re.search(r'/video/(\d+)', final)
            if m: self._json({'video_id': m.group(1), 'url': final})
            else: self._json({'error':'ไม่พบ video ID ใน URL นี้'}, 400)
        except Exception as e: self._json({'error': str(e)}, 500)

    def _proxy(self):
        raw = unquote(self.path[len('/proxy?url='):])
        def to_csv(u):
            if '/edit' in u:
                base = u.split('/edit')[0]; m = re.search(r'gid=(\d+)',u)
                return f"{base}/export?format=csv&gid={m.group(1)}" if m else f"{base}/export?format=csv"
            return u
        try:
            req = urllib.request.Request(to_csv(raw), headers={"User-Agent":"Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=15); data = resp.read()
            self.send_response(200); self.send_header('Content-Type','text/csv; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin','*'); self.send_header('Content-Length',len(data))
            self.end_headers(); self.wfile.write(data)
        except Exception as e:
            err = json.dumps({'error':str(e)}).encode()
            self.send_response(500); self.send_header('Content-Type','application/json')
            self.send_header('Access-Control-Allow-Origin','*'); self.end_headers(); self.wfile.write(err)

# ── Main ─────────────────────────────────────────────────────────────────────
def open_browser():
    import time; time.sleep(0.9); webbrowser.open(f"http://localhost:{PORT}")

if __name__ == '__main__':
    if not os.path.exists(HTML_FILE): print("❌ ไม่พบ shop.html"); input(); sys.exit(1)
    if USE_PG: print("🐘 PostgreSQL..."); _build_pg_pool()
    init_db()
    mode = "PostgreSQL+Cloudinary" if USE_PG else "SQLite (local)"
    print(f"{'='*55}\n  Shop Server v3  [{mode}]\n{'='*55}")
    if not IS_CLOUD:
        threading.Thread(target=open_browser, daemon=True).start()
        print(f"  🌐 http://localhost:{PORT}  |  ⛔ Ctrl+C เพื่อหยุด")
    else: print(f"  🌐 port {PORT}")
    print('='*55)
    try:
        httpd = http.server.HTTPServer(("0.0.0.0", PORT), Handler); httpd.serve_forever()
    except KeyboardInterrupt: print("\nหยุดแล้ว")
