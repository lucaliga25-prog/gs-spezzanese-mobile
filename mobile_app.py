import os
import base64
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor, execute_batch
from dotenv import load_dotenv
from datetime import date
from functools import wraps

from flask import Flask, request, redirect, url_for, session, flash, get_flashed_messages
from datetime import timedelta

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL non trovata. Controlla il file .env.")

DB_POOL = SimpleConnectionPool(1, int(os.getenv("DB_POOL_MAX", "16")), dsn=DATABASE_URL)


COACH_PASSWORD = "spezzanese2627"

app = Flask(__name__)
app.secret_key = "gestionale-gs-spezzanese-mobile-secret"


def _pg(query):
    """Converte i placeholder stile sqlite (?) in placeholder psycopg2 (%s)."""
    return query.replace("?", "%s")


def db_query(query, params=(), fetch=False):
    """Esegue una singola query usando il connection pool."""
    conn = DB_POOL.getconn()
    cur = None

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(_pg(query), params)
        rows = cur.fetchall() if fetch else None
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        DB_POOL.putconn(conn)


def db_transaction(statements=(), batches=()):
    """
    Esegue più comandi nello stesso round di connessione/commit.
    statements: [(query, params), ...]
    batches: [(query, [params, ...]), ...]
    """
    conn = DB_POOL.getconn()
    cur = None

    try:
        cur = conn.cursor()
        for query, params in statements:
            cur.execute(_pg(query), params)
        for query, rows in batches:
            if rows:
                execute_batch(cur, _pg(query), rows, page_size=200)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        DB_POOL.putconn(conn)


_DB_READY = False

def ensure_mobile_tables_once():
    global _DB_READY
    if not _DB_READY:
        ensure_mobile_tables()
        _DB_READY = True


@app.before_request
def _ensure_db_ready():
    ensure_mobile_tables_once()


def ensure_db():
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    id INTEGER PRIMARY KEY,
                    first_name TEXT NOT NULL DEFAULT '',
                    last_name TEXT NOT NULL DEFAULT '',
                    birth_date TEXT DEFAULT '',
                    role TEXT DEFAULT ''
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS matches (
                    id INTEGER PRIMARY KEY,
                    match_date TEXT NOT NULL,
                    opponent TEXT NOT NULL,
                    competition TEXT DEFAULT '',
                    home_away TEXT DEFAULT '',
                    result TEXT DEFAULT ''
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS appearances (
                    id INTEGER PRIMARY KEY,
                    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                    starter INTEGER DEFAULT 0,
                    minutes INTEGER DEFAULT 0,
                    goals INTEGER DEFAULT 0,
                    assists INTEGER DEFAULT 0,
                    yellow_cards INTEGER DEFAULT 0,
                    red_cards INTEGER DEFAULT 0,
                    captain INTEGER DEFAULT 0,
                    vice_captain INTEGER DEFAULT 0,
                    UNIQUE(match_id, player_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS substitutions (
                    id INTEGER PRIMARY KEY,
                    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                    slot INTEGER NOT NULL,
                    player_in_id INTEGER REFERENCES players(id) ON DELETE SET NULL,
                    player_out_id INTEGER REFERENCES players(id) ON DELETE SET NULL,
                    UNIQUE(match_id, slot)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS training_sessions (
                    id INTEGER PRIMARY KEY,
                    training_date TEXT NOT NULL,
                    title TEXT DEFAULT 'Allenamento',
                    notes TEXT DEFAULT ''
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS training_attendance (
                    id INTEGER PRIMARY KEY,
                    session_id INTEGER NOT NULL REFERENCES training_sessions(id) ON DELETE CASCADE,
                    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                    present INTEGER DEFAULT 1,
                    notes TEXT DEFAULT '',
                    UNIQUE(session_id, player_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS player_votes (
                    id INTEGER PRIMARY KEY,
                    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                    voter_player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                    voted_player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                    rating NUMERIC(4,2) NOT NULL,
                    UNIQUE(match_id, voter_player_id, voted_player_id)
                )
            """)

            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS photo_data TEXT DEFAULT ''")
            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS photo_mime TEXT DEFAULT ''")
            cur.execute("ALTER TABLE player_votes ALTER COLUMN rating TYPE NUMERIC(4,2) USING rating::numeric")

            cur.execute("CREATE INDEX IF NOT EXISTS idx_appearances_player_match ON appearances(player_id, match_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_appearances_match ON appearances(match_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(match_date)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_votes_voted_match ON player_votes(voted_player_id, match_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_votes_voter_match ON player_votes(voter_player_id, match_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_training_attendance_player_session ON training_attendance(player_id, session_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_training_sessions_date ON training_sessions(training_date)")

            for table in ["players", "matches", "appearances", "substitutions", "training_sessions", "training_attendance", "player_votes"]:
                seq = f"{table}_id_seq"
                cur.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq}")
                cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
                max_id = cur.fetchone()[0] or 0
                if max_id > 0:
                    cur.execute("SELECT setval(%s, %s, true)", (seq, max_id))
                else:
                    cur.execute("SELECT setval(%s, 1, false)", (seq,))
                cur.execute(f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT nextval('{seq}')")
        conn.commit()


def ensure_mobile_tables():
    ensure_db()


def ui_date(date_str):
    try:
        y, m, d = str(date_str).split("-")
        return f"{d}-{m}-{y[-2:]}"
    except Exception:
        return date_str or ""


def player_name(row):
    return f"{row['last_name']} {row['first_name']}".strip()


def get_players():
    return db_query("""
        SELECT id, first_name, last_name, role
        FROM players
        ORDER BY last_name, first_name
    """, fetch=True)


def last_match():
    # Per i voti deve prendere l'ultima partita con almeno un giocatore sopra i 10 minuti,
    # non semplicemente l'ultima partita creata.
    rows = db_query("""
        SELECT
            m.id,
            m.match_date,
            m.opponent,
            m.competition,
            m.home_away,
            m.result
        FROM matches m
        WHERE EXISTS (
            SELECT 1
            FROM appearances a
            WHERE a.match_id = m.id
              AND COALESCE(a.minutes, 0) > 10
        )
        ORDER BY m.match_date DESC, m.id DESC
        LIMIT 1
    """, fetch=True)

    if rows:
        return rows[0]

    # Fallback: se non esiste ancora nessuna formazione salvata,
    # mostra comunque l'ultima partita inserita.
    rows = db_query("""
        SELECT id, match_date, opponent, competition, home_away, result
        FROM matches
        ORDER BY match_date DESC, id DESC
        LIMIT 1
    """, fetch=True)

    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# AWARD HELPERS — una sola query al DB per ciascuna funzione
# ---------------------------------------------------------------------------

def get_best_player_last_match():
    """Giocatore con media voto più alta nell'ultima partita (CTE, 1 query)."""
    rows = db_query("""
        WITH last_m AS (
            SELECT m.id AS match_id, m.match_date, m.opponent
            FROM matches m
            WHERE EXISTS (
                SELECT 1 FROM appearances a
                WHERE a.match_id = m.id AND COALESCE(a.minutes,0) > 10
            )
            ORDER BY m.match_date DESC, m.id DESC
            LIMIT 1
        )
        SELECT
            p.id,
            p.first_name,
            p.last_name,
            COALESCE(p.role,'') AS role,
            COALESCE(p.photo_data,'') AS photo_data,
            COALESCE(p.photo_mime,'image/jpeg') AS photo_mime,
            lm.match_date,
            lm.opponent,
            ROUND(AVG(v.rating)::numeric, 2) AS media_voto,
            COUNT(v.id) AS num_voti
        FROM last_m lm
        JOIN player_votes v ON v.match_id = lm.match_id
        JOIN players p ON p.id = v.voted_player_id
        GROUP BY p.id, p.first_name, p.last_name, p.role,
                 p.photo_data, p.photo_mime, lm.match_date, lm.opponent
        ORDER BY media_voto DESC, num_voti DESC
        LIMIT 1
    """, fetch=True)
    return rows[0] if rows else None


def get_best_player_last_month():
    """Giocatore con media voto più alta nel mese precedente (1 query)."""
    today = date.today()
    first_this_month = today.replace(day=1)
    last_month_end = first_this_month - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    rows = db_query("""
        SELECT
            p.id,
            p.first_name,
            p.last_name,
            COALESCE(p.role,'') AS role,
            COALESCE(p.photo_data,'') AS photo_data,
            COALESCE(p.photo_mime,'image/jpeg') AS photo_mime,
            ROUND(AVG(v.rating)::numeric, 2) AS media_voto,
            COUNT(v.id) AS num_voti
        FROM player_votes v
        JOIN matches m ON m.id = v.match_id
        JOIN players p ON p.id = v.voted_player_id
        WHERE m.match_date BETWEEN ? AND ?
        GROUP BY p.id, p.first_name, p.last_name, p.role, p.photo_data, p.photo_mime
        ORDER BY media_voto DESC, num_voti DESC
        LIMIT 1
    """, (last_month_start.isoformat(), last_month_end.isoformat()), fetch=True)

    if rows:
        rows[0]["month_label"] = last_month_end.strftime("%B %Y")
    return rows[0] if rows else None


def _render_week_card(p):
    """Figurina nera con fulmini oro — Man of the Match."""
    full_name = f"{p['last_name']} {p['first_name']}".strip()
    photo_html = (
        f"<img class='card-photo-award' src='data:{p['photo_mime']};base64,{p['photo_data']}' alt='Foto'>"
        if p["photo_data"] else "<div class='card-placeholder-award'>👤</div>"
    )
    return f"""
    <div class="award-card-week">
        <span class="lightning left">⚡</span>
        <span class="lightning right">⚡</span>
        <div><span class="award-badge">⭐ Man of the Match</span></div>
        {photo_html}
        <div class="award-name">{full_name}</div>
        <div class="award-role">{p['role'] or '-'}</div>
        <div class="award-score">{p['media_voto']}</div>
        <div class="award-label">Media voto partita</div>
        <div class="award-meta">{ui_date(p['match_date'])} · {p['opponent']}</div>
    </div>
    """


def _render_month_card(p):
    """Figurina rossa e blu — Giocatore del Mese."""
    full_name = f"{p['last_name']} {p['first_name']}".strip()
    photo_html = (
        f"<img class='card-photo-award' src='data:{p['photo_mime']};base64,{p['photo_data']}' alt='Foto'>"
        if p["photo_data"] else "<div class='card-placeholder-award'>👤</div>"
    )
    return f"""
    <div class="award-card-month">
        <div><span class="award-badge-month">🏆 Giocatore del Mese</span></div>
        {photo_html}
        <div class="award-name">{full_name}</div>
        <div class="award-role">{p['role'] or '-'}</div>
        <div class="award-score">{p['media_voto']}</div>
        <div class="award-label">Media voto · {p.get('month_label','')}</div>
    </div>
    """


def login_required(kind=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if "role" not in session:
                return redirect(url_for("home"))
            if kind and session.get("role") != kind:
                flash("Accesso non autorizzato.")
                return redirect(url_for("home"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


BASE_STYLE = """
<style>
:root{--blue:#07152f;--red:#dc2626;--green:#16a34a;--bg:#f6f8fc;--card:#fff;--text:#111827;--muted:#64748b;--border:#dbe3ef}*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;background:var(--bg);color:var(--text)}.header{background:linear-gradient(135deg,var(--blue),#0b63f6);color:white;padding:22px 18px 28px;border-bottom-left-radius:24px;border-bottom-right-radius:24px}.header h1{margin:0;font-size:24px}.header p{margin:6px 0 0;opacity:.85;font-size:14px}.container{padding:16px;max-width:820px;margin:auto}.card{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:16px;margin-bottom:14px;box-shadow:0 8px 20px rgba(15,23,42,.06)}h2{font-size:19px;margin:0 0 12px}label{display:block;font-weight:700;color:var(--muted);font-size:13px;margin:10px 0 5px}input,select{width:100%;height:42px;border:1px solid var(--border);border-radius:12px;padding:0 12px;font-size:16px;background:white}button,.btn{display:block;width:100%;border:0;border-radius:13px;padding:12px 14px;background:var(--red);color:white;font-weight:800;font-size:15px;text-decoration:none;text-align:center;margin-top:12px}.btn-blue{background:#0b63f6}.btn-green{background:var(--green)}.btn-dark{background:var(--blue)}.row{display:grid;grid-template-columns:1fr 90px;gap:10px;align-items:center}.player-row{border:1px solid var(--border);border-radius:14px;padding:12px;margin:8px 0;background:#fff}.player-title{font-weight:800}.small{color:var(--muted);font-size:12px}.tabs{display:grid;grid-template-columns:1fr 1fr;gap:10px}.flash{background:#fee2e2;color:#991b1b;border:1px solid #fecaca;border-radius:12px;padding:10px;margin-bottom:12px;font-weight:700}.inline{display:grid;grid-template-columns:1fr 1fr;gap:8px}.checks{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}.checks label{margin:0;padding:10px;background:#f8fafc;border:1px solid var(--border);border-radius:12px;color:var(--text)}.checks input{width:auto;height:auto;margin-right:8px}.footer-space{height:30px}
.card-preview{
    text-align:center;
    background:linear-gradient(180deg,#ffffff,#f8fafc);
}
.card-photo{
    width:160px;
    height:160px;
    object-fit:cover;
    border-radius:28px;
    border:4px solid #e5e7eb;
    box-shadow:0 10px 24px rgba(15,23,42,.14);
    background:#e5e7eb;
}
.card-placeholder{
    width:160px;
    height:160px;
    border-radius:28px;
    display:flex;
    align-items:center;
    justify-content:center;
    margin:0 auto;
    background:#e5e7eb;
    font-size:62px;
    color:#64748b;
    border:4px solid #e5e7eb;
}
.card-name{
    font-size:24px;
    font-weight:900;
    margin-top:14px;
}
.card-role{
    color:#0b63f6;
    font-weight:900;
    margin-top:4px;
}
.stats-grid{
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:8px;
    margin-top:16px;
}
.stat-box{
    background:#f8fafc;
    border:1px solid #dbe3ef;
    border-radius:14px;
    padding:10px 6px;
}
.stat-value{
    font-size:20px;
    font-weight:900;
    color:#111827;
}
.stat-label{
    font-size:11px;
    color:#64748b;
    font-weight:800;
    margin-top:2px;
}
.stats-table{
    width:100%;
    border-collapse:collapse;
    font-size:13px;
}
.stats-table th{
    background:#07152f;
    color:white;
    padding:8px 6px;
    text-align:center;
    position:sticky;
    top:0;
}
.stats-table td{
    border-bottom:1px solid #dbe3ef;
    padding:8px 6px;
    text-align:center;
}
.stats-table td:first-child,
.stats-table th:first-child{
    text-align:left;
    min-width:145px;
}
.table-wrap{
    overflow-x:auto;
    border:1px solid #dbe3ef;
    border-radius:14px;
}
.performance-card{
    border:1px solid #dbe3ef;
    border-radius:16px;
    background:#ffffff;
    padding:14px;
    margin:10px 0;
    box-shadow:0 6px 16px rgba(15,23,42,.05);
}
.performance-title{
    font-weight:900;
    font-size:16px;
}
.performance-meta{
    color:#64748b;
    font-size:12px;
    margin-top:3px;
}
.performance-grid{
    display:grid;
    grid-template-columns:repeat(6,1fr);
    gap:6px;
    margin-top:12px;
}
.performance-stat{
    background:#f8fafc;
    border:1px solid #dbe3ef;
    border-radius:12px;
    padding:8px 4px;
    text-align:center;
}
.performance-value{
    font-size:17px;
    font-weight:900;
    color:#111827;
}
.performance-label{
    font-size:10px;
    font-weight:800;
    color:#64748b;
}
.small-btn{
    display:inline-block;
    width:auto;
    margin-top:6px;
    padding:8px 10px;
    font-size:12px;
    border-radius:10px;
}
/* ===== FIGURINA DELLA SETTIMANA (nera + fulmini oro) ===== */
.award-card-week{
    background:linear-gradient(160deg,#0a0a0a 0%,#1a1a1a 40%,#111 100%);
    border:2px solid #d4a017;border-radius:22px;padding:20px 16px 18px;
    margin-bottom:14px;box-shadow:0 0 30px rgba(212,160,23,.35),0 8px 28px rgba(0,0,0,.6);
    position:relative;overflow:hidden;text-align:center;
}
.award-card-week .lightning{position:absolute;top:10px;font-size:22px;opacity:.9;filter:drop-shadow(0 0 6px #f5c518);}
.award-card-week .lightning.left{left:12px;transform:rotate(-15deg)}
.award-card-week .lightning.right{right:12px;transform:rotate(15deg)}
.award-card-week .award-badge{
    display:inline-block;background:linear-gradient(135deg,#b8860b,#f5c518,#b8860b);
    color:#0a0a0a;font-weight:900;font-size:11px;padding:3px 12px;border-radius:20px;
    letter-spacing:.8px;text-transform:uppercase;margin-bottom:12px;
}
.award-card-week .card-photo-award{
    width:130px;height:130px;object-fit:cover;border-radius:50%;
    border:4px solid #d4a017;box-shadow:0 0 20px rgba(212,160,23,.5);background:#1a1a1a;
}
.award-card-week .card-placeholder-award{
    width:130px;height:130px;border-radius:50%;display:flex;align-items:center;
    justify-content:center;margin:0 auto;background:#1a1a1a;font-size:54px;
    border:4px solid #d4a017;box-shadow:0 0 20px rgba(212,160,23,.5);
}
.award-card-week .award-name{font-size:22px;font-weight:900;color:#f5c518;margin-top:12px;text-shadow:0 0 10px rgba(245,197,24,.4);}
.award-card-week .award-role{color:#d4a017;font-weight:700;font-size:13px;margin-top:3px;}
.award-card-week .award-score{font-size:36px;font-weight:900;color:#f5c518;text-shadow:0 0 16px rgba(245,197,24,.6);margin:10px 0 4px;}
.award-card-week .award-label{font-size:11px;font-weight:800;color:#b8860b;letter-spacing:.6px;text-transform:uppercase;}
.award-card-week .award-meta{font-size:12px;color:#888;margin-top:8px;}
/* ===== FIGURINA DEL MESE (rossa + blu) ===== */
.award-card-month{
    background:linear-gradient(160deg,#1a0010 0%,#0d001f 50%,#1a000a 100%);
    border:2px solid #dc2626;border-radius:22px;padding:20px 16px 18px;
    margin-bottom:14px;box-shadow:0 0 30px rgba(220,38,38,.3),0 0 30px rgba(37,99,235,.2),0 8px 28px rgba(0,0,0,.7);
    position:relative;overflow:hidden;text-align:center;
}
.award-card-month .award-badge-month{
    display:inline-block;background:linear-gradient(135deg,#dc2626,#1e40af);
    color:#fff;font-weight:900;font-size:11px;padding:3px 12px;border-radius:20px;
    letter-spacing:.8px;text-transform:uppercase;margin-bottom:12px;
}
.award-card-month .card-photo-award{
    width:130px;height:130px;object-fit:cover;border-radius:50%;
    border:4px solid #dc2626;box-shadow:0 0 18px rgba(220,38,38,.5),0 0 18px rgba(37,99,235,.3);background:#1a0010;
}
.award-card-month .card-placeholder-award{
    width:130px;height:130px;border-radius:50%;display:flex;align-items:center;
    justify-content:center;margin:0 auto;background:#1a0010;font-size:54px;
    border:4px solid #dc2626;box-shadow:0 0 18px rgba(220,38,38,.5);
}
.award-card-month .award-name{
    font-size:22px;font-weight:900;
    background:linear-gradient(135deg,#ef4444,#60a5fa);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-top:12px;
}
.award-card-month .award-role{color:#93c5fd;font-weight:700;font-size:13px;margin-top:3px;}
.award-card-month .award-score{
    font-size:36px;font-weight:900;
    background:linear-gradient(135deg,#ef4444,#60a5fa);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin:10px 0 4px;
}
.award-card-month .award-label{font-size:11px;font-weight:800;color:#93c5fd;letter-spacing:.6px;text-transform:uppercase;}
.award-card-month .award-meta{font-size:12px;color:#666;margin-top:8px;}
</style>
"""


def page(title, subtitle, content):
    flashes = "".join(f"<div class='flash'>{m}</div>" for m in get_flashed_messages())
    return f"""
    <!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>{BASE_STYLE}</head>
    <body><div class="header"><h1>{title}</h1><p>{subtitle}</p></div><div class="container">{flashes}{content}<div class="footer-space"></div></div></body></html>
    """


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        mode = request.form.get("mode")
        if mode == "player":
            first_name = request.form.get("first_name", "").strip()
            last_name = request.form.get("last_name", "").strip()
            player = db_query("""
                SELECT id, first_name, last_name FROM players
                WHERE lower(trim(first_name))=lower(trim(?)) AND lower(trim(last_name))=lower(trim(?))
                LIMIT 1
            """, (first_name, last_name), fetch=True)
            if not player:
                flash("Giocatore non trovato. Controlla nome e cognome.")
                return redirect(url_for("home"))
            session.clear()
            session["role"] = "player"
            session["player_id"] = player[0]["id"]
            session["player_name"] = f"{player[0]['last_name']} {player[0]['first_name']}"
            return redirect(url_for("player_home"))
        if mode == "coach":
            if request.form.get("password", "") != COACH_PASSWORD:
                flash("Password allenatore errata.")
                return redirect(url_for("home"))
            session.clear()
            session["role"] = "coach"
            return redirect(url_for("coach_panel"))
    content = """
    <div class="card"><h2>Accesso giocatore</h2><form method="post"><input type="hidden" name="mode" value="player"><label>Nome</label><input name="first_name" required><label>Cognome</label><input name="last_name" required><button>Entra e vota</button></form></div>
    <div class="card"><h2>Accesso allenatore</h2><form method="post"><input type="hidden" name="mode" value="coach"><label>Password allenatore</label><input name="password" type="password" required><button class="btn-dark">Entra come allenatore</button></form></div>
    """
    return page("GS Spezzanese Mobile", "Accesso giocatori e allenatore", content)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/awards")
@login_required()
def awards():
    """Figurine speciali: miglior giocatore dell'ultima partita e del mese scorso."""
    week_player = get_best_player_last_match()
    month_player = get_best_player_last_month()

    week_html = _render_week_card(week_player) if week_player else (
        "<div class='card' style='text-align:center;color:#888;padding:24px'>"
        "⚡ Nessun voto disponibile per l'ultima partita.</div>"
    )
    month_html = _render_month_card(month_player) if month_player else (
        "<div class='card' style='text-align:center;color:#888;padding:24px'>"
        "🏆 Nessun voto disponibile per il mese scorso.</div>"
    )

    back_url = url_for("coach_panel") if session.get("role") == "coach" else url_for("player_home")

    content = f"""
    <div style="font-size:13px;color:#64748b;font-weight:700;text-transform:uppercase;
                letter-spacing:.6px;margin-bottom:10px;">⚡ Man of the Match</div>
    {week_html}
    <div style="font-size:13px;color:#64748b;font-weight:700;text-transform:uppercase;
                letter-spacing:.6px;margin:16px 0 10px;">🏆 Giocatore del Mese</div>
    {month_html}
    <a class="btn btn-blue" href="{back_url}">Indietro</a>
    """
    return page("Premi", "Le figurine speciali della squadra", content)


@app.route("/player", methods=["GET", "POST"])
@login_required("player")
def player_home():
    voter_id = session["player_id"]

    if request.method == "POST":
        photo = request.files.get("photo")

        if not photo or not photo.filename:
            flash("Seleziona una foto prima di salvare.")
            return redirect(url_for("player_home"))

        data = photo.read()

        if len(data) > 2 * 1024 * 1024:
            flash("Foto troppo grande. Usa una foto sotto i 2 MB.")
            return redirect(url_for("player_home"))

        encoded = base64.b64encode(data).decode("utf-8")
        mime = photo.mimetype or "image/jpeg"

        db_query("""
            UPDATE players
            SET photo_data=?, photo_mime=?
            WHERE id=?
        """, (encoded, mime, voter_id))

        flash("Foto figurina aggiornata correttamente.")
        return redirect(url_for("player_home"))

    content = """
    <div class="card">
        <h2>Foto figurina</h2>
        <form method="post" enctype="multipart/form-data">
            <label>Carica foto profilo</label>
            <input type="file" name="photo" accept="image/*" required>
            <button class="btn-green">Salva foto</button>
        </form>
        <div class="small">La foto verrà mostrata nella pagina Figurine del gestionale PC.</div>
    </div>

    <div class="card">
        <h2>Voti partita</h2>
        <a class="btn btn-blue" href="/player/matches">Scegli partita da votare</a>
        <a class="btn btn-green" href="/player/card">Visualizza la mia figurina</a>
        <a class="btn btn-dark" href="/player/history">Storico prestazioni</a>
        <a class="btn" style="background:linear-gradient(135deg,#b8860b,#f5c518);color:#0a0a0a;font-weight:900;" href="/awards">⚡ Figurine Premi</a>
        <a class="btn" href="/logout">Esci</a>
    </div>
    """

    return page("Area giocatore", f"Ciao {session.get('player_name')}", content)



def _build_vote_choices():
    choices = []
    for base in range(4, 11):
        choices.append((f"{base}-", base - 0.25))
        choices.append((str(base), float(base)))
        choices.append((f"{base}+", base + 0.25))
        if base < 10:
            choices.append((f"{base}.5", base + 0.5))
    return tuple(choices)


VOTE_CHOICES = _build_vote_choices()


def vote_choices():
    return VOTE_CHOICES


def parse_vote(value):
    try:
        rating = float(value)
    except Exception:
        return None

    if rating < 1 or rating > 10.25:
        return None

    return rating



@app.route("/player/card")
@login_required("player")
def player_card():
    player_id = session["player_id"]

    rows = db_query("""
        SELECT
            p.id,
            p.first_name,
            p.last_name,
            COALESCE(p.role, '') AS role,
            COALESCE(p.photo_data, '') AS photo_data,
            COALESCE(p.photo_mime, 'image/jpeg') AS photo_mime,
            COUNT(a.id) AS presenze,
            COALESCE(SUM(a.goals), 0) AS gol,
            COALESCE(SUM(a.assists), 0) AS assist,
            COALESCE(ROUND(AVG(v.rating)::numeric, 2), 0) AS media_voto
        FROM players p
        LEFT JOIN appearances a ON a.player_id=p.id
        LEFT JOIN player_votes v ON v.voted_player_id=p.id
        WHERE p.id=?
        GROUP BY p.id, p.first_name, p.last_name, p.role, p.photo_data, p.photo_mime
    """, (player_id,), fetch=True)

    if not rows:
        flash("Giocatore non trovato.")
        return redirect(url_for("player_home"))

    p = rows[0]
    full_name = f"{p['last_name']} {p['first_name']}".strip()

    if p["photo_data"]:
        photo_html = f"<img class='card-photo' src='data:{p['photo_mime']};base64,{p['photo_data']}' alt='Foto giocatore'>"
    else:
        photo_html = "<div class='card-placeholder'>👤</div>"

    content = f"""
    <div class="card card-preview">
        {photo_html}
        <div class="card-name">{full_name}</div>
        <div class="card-role">{p['role'] or '-'}</div>

        <div class="stats-grid">
            <div class="stat-box">
                <div class="stat-value">{p['presenze']}</div>
                <div class="stat-label">Partite</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{p['gol']}</div>
                <div class="stat-label">Gol</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{p['assist']}</div>
                <div class="stat-label">Assist</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{p['media_voto']}</div>
                <div class="stat-label">Voto</div>
            </div>
        </div>
    </div>

    <a class="btn btn-blue" href="/player">Area giocatore</a>
    """

    return page("La mia figurina", f"Ciao {session.get('player_name')}", content)



@app.route("/player/history")
@login_required("player")
def player_history():
    player_id = session["player_id"]

    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    start_filter = start_date if start_date else "1900-01-01"
    end_filter = end_date if end_date else "2999-12-31"

    rows = db_query("""
        SELECT
            m.id AS match_id,
            m.match_date,
            m.opponent,
            m.competition,
            m.home_away,
            COALESCE(m.result, '') AS result,
            COALESCE(a.starter, 0) AS starter,
            COALESCE(a.minutes, 0) AS minutes,
            COALESCE(a.goals, 0) AS goals,
            COALESCE(a.assists, 0) AS assists,
            COALESCE(a.yellow_cards, 0) AS yellow_cards,
            COALESCE(a.red_cards, 0) AS red_cards,
            COALESCE(v.media_voto, 0) AS media_voto
        FROM appearances a
        JOIN matches m ON m.id=a.match_id
        LEFT JOIN (
            SELECT
                match_id,
                voted_player_id,
                ROUND(AVG(rating)::numeric, 2) AS media_voto
            FROM player_votes
            WHERE voted_player_id=?
            GROUP BY match_id, voted_player_id
        ) v ON v.match_id=m.id AND v.voted_player_id=a.player_id
        WHERE a.player_id=?
          AND m.match_date BETWEEN ? AND ?
        ORDER BY m.match_date DESC, m.id DESC
    """, (player_id, player_id, start_filter, end_filter), fetch=True)

    if not rows:
        cards = """
        <div class="card">
            Nessuna prestazione trovata per il periodo selezionato.
        </div>
        """
    else:
        cards = ""

        for r in rows:
            titolare = "Titolare" if int(r["starter"] or 0) == 1 else "Panchina/Subentrato"

            cards += f"""
            <div class="performance-card">
                <div class="performance-title">{ui_date(r['match_date'])} · {r['opponent']}</div>
                <div class="performance-meta">{r['competition']} · {r['home_away']} · Risultato: {r['result'] or '-'} · {titolare}</div>

                <div class="performance-grid">
                    <div class="performance-stat">
                        <div class="performance-value">{r['minutes']}</div>
                        <div class="performance-label">Min</div>
                    </div>
                    <div class="performance-stat">
                        <div class="performance-value">{r['goals']}</div>
                        <div class="performance-label">Gol</div>
                    </div>
                    <div class="performance-stat">
                        <div class="performance-value">{r['assists']}</div>
                        <div class="performance-label">Assist</div>
                    </div>
                    <div class="performance-stat">
                        <div class="performance-value">{r['yellow_cards']}</div>
                        <div class="performance-label">Gialli</div>
                    </div>
                    <div class="performance-stat">
                        <div class="performance-value">{r['red_cards']}</div>
                        <div class="performance-label">Rossi</div>
                    </div>
                    <div class="performance-stat">
                        <div class="performance-value">{r['media_voto']}</div>
                        <div class="performance-label">Voto</div>
                    </div>
                </div>
            </div>
            """

    today = date.today().isoformat()

    content = f"""
    <div class="card">
        <h2>Storico prestazioni</h2>
        <div class="small">Qui vedi solo le tue partite giocate.</div>

        <form method="get">
            <div class="inline">
                <div>
                    <label>Dal</label>
                    <input type="date" name="start_date" value="{start_date}">
                </div>
                <div>
                    <label>Al</label>
                    <input type="date" name="end_date" value="{end_date or today}">
                </div>
            </div>
            <button class="btn-blue">Filtra periodo</button>
            <a class="btn btn-dark" href="/player/history">Azzera filtro</a>
        </form>
    </div>

    {cards}

    <a class="btn btn-blue" href="/player">Area giocatore</a>
    """

    return page("Storico prestazioni", f"Ciao {session.get('player_name')}", content)


@app.route("/player/matches")
@login_required("player")
def player_matches():
    voter_id = session["player_id"]

    matches = db_query("""
        SELECT
            m.id,
            m.match_date,
            m.opponent,
            m.competition,
            m.home_away,
            COALESCE(m.result, '') AS result,
            COUNT(a.id) AS players_over_10,
            CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM player_votes pv
                    WHERE pv.match_id=m.id
                      AND pv.voter_player_id=?
                )
                THEN 1 ELSE 0
            END AS already_voted
        FROM matches m
        JOIN appearances a ON a.match_id=m.id
        WHERE COALESCE(a.minutes, 0) > 10
        GROUP BY m.id, m.match_date, m.opponent, m.competition, m.home_away, m.result
        ORDER BY m.match_date DESC, m.id DESC
    """, (voter_id,), fetch=True)

    if not matches:
        content = """
        <div class="card">
            <h2>Nessuna partita votabile</h2>
            <div>Non ci sono ancora partite con giocatori sopra i 10 minuti.</div>
        </div>
        <a class="btn btn-blue" href="/player">Area giocatore</a>
        """
        return page("Scegli partita", "Voti giocatore", content)

    items = ""

    for m in matches:
        voted = int(m["already_voted"] or 0) == 1

        if voted:
            action = "<button disabled style='background:#94a3b8'>Già votata</button>"
        else:
            action = f"<a class='btn btn-green' href='/player/votes/{m['id']}'>Vota questa partita</a>"

        items += f"""
        <div class="player-row">
            <div class="player-title">{ui_date(m['match_date'])} · {m['opponent']}</div>
            <div class="small">{m['competition']} · {m['home_away']} · Risultato: {m['result'] or '-'} · Giocatori votabili: {m['players_over_10']}</div>
            {action}
        </div>
        """

    content = f"""
    <div class="card">
        <h2>Scegli partita da votare</h2>
        {items}
    </div>
    <a class="btn btn-blue" href="/player">Area giocatore</a>
    """

    return page("Scegli partita", f"Ciao {session.get('player_name')}", content)


@app.route("/player/votes")
@login_required("player")
def player_votes_redirect():
    return redirect(url_for("player_matches"))


@app.route("/player/votes/<int:match_id>", methods=["GET", "POST"])
@login_required("player")
def player_votes(match_id):
    voter_id = session["player_id"]

    match_rows = db_query("""
        SELECT id, match_date, opponent, competition, home_away, COALESCE(result, '') AS result
        FROM matches
        WHERE id=?
    """, (match_id,), fetch=True)

    if not match_rows:
        flash("Partita non trovata.")
        return redirect(url_for("player_matches"))

    match = match_rows[0]

    already_voted_row = db_query("""
        SELECT COUNT(*) AS total
        FROM player_votes
        WHERE match_id=? AND voter_player_id=?
    """, (match_id, voter_id), fetch=True)[0]
    already_voted = already_voted_row["total"]

    if already_voted:
        content = f"""
        <div class="card">
            <h2>Partita già votata</h2>
            <div>Hai già inserito i voti per:</div>
            <div><b>{ui_date(match['match_date'])}</b> vs {match['opponent']}</div>
            <div class="small">{match['competition']} · {match['home_away']} · Risultato: {match['result'] or '-'}</div>
        </div>
        <a class="btn btn-blue" href="/player/matches">Torna alle partite</a>
        """
        return page("Voti già inseriti", f"Ciao {session.get('player_name')}", content)

    rows = db_query("""
        SELECT
            p.id,
            p.first_name,
            p.last_name,
            p.role,
            a.minutes
        FROM appearances a
        JOIN players p ON p.id=a.player_id
        WHERE a.match_id=?
          AND COALESCE(a.minutes, 0) > 10
        ORDER BY p.last_name, p.first_name
    """, (match_id,), fetch=True)

    if request.method == "POST":
        if not rows:
            flash("Nessun giocatore votabile per questa partita.")
            return redirect(url_for("player_matches"))

        vote_rows = []

        for row in rows:
            voted_id = row["id"]
            raw = request.form.get(f"rating_{voted_id}", "")

            if raw == "":
                continue

            rating = parse_vote(raw)
            if rating is not None:
                vote_rows.append((match_id, voter_id, voted_id, rating))

        saved = len(vote_rows)

        if saved == 0:
            flash("Inserisci almeno un voto prima di salvare.")
            return redirect(url_for("player_votes", match_id=match_id))

        db_transaction(batches=[("""
            INSERT INTO player_votes (match_id, voter_player_id, voted_player_id, rating)
            VALUES (?, ?, ?, ?)
        """, vote_rows)])

        flash(f"Voti salvati: {saved}. Non potrai più modificarli per questa partita.")
        return redirect(url_for("player_matches"))

    if not rows:
        items = """
        <div class="player-row">
            Nessun giocatore ha superato i 10 minuti in questa partita.
        </div>
        """
    else:
        items = ""

        for row in rows:
            options = "<option value=''>--</option>"
            for label, value in vote_choices():
                options += f"<option value='{value}'>{label}</option>"

            items += f"""
            <div class="player-row">
                <div class="row">
                    <div>
                        <div class="player-title">{player_name(row)}</div>
                        <div class="small">{row['role'] or '-'} · {row['minutes']} minuti</div>
                    </div>
                    <select name="rating_{row['id']}">
                        {options}
                    </select>
                </div>
            </div>
            """

    content = f"""
    <div class="card">
        <h2>Partita selezionata</h2>
        <div><b>{ui_date(match['match_date'])}</b> vs {match['opponent']}</div>
        <div class="small">{match['competition']} · {match['home_away']} · Risultato: {match['result'] or '-'}</div>
        <div class="small">Puoi votare solo i giocatori che hanno fatto più di 10 minuti.</div>
        <div class="small"><b>Attenzione:</b> dopo il salvataggio non potrai più modificare i voti di questa partita.</div>
    </div>

    <div class="card">
        <h2>Inserisci voti</h2>
        <form method="post">
            {items}
            <button>Salva voti definitivamente</button>
        </form>
        <a class="btn btn-blue" href="/player/matches">Torna alle partite</a>
    </div>
    """

    return page("Voti giocatore", f"Ciao {session.get('player_name')}", content)


@app.route("/coach")
@login_required("coach")
def coach_panel():
    content = """
    <div class="card"><h2>Pannello allenatore</h2><div class="tabs"><a class="btn btn-blue" href="/coach/matches">Partite</a><a class="btn btn-green" href="/coach/formation">Formazione</a><a class="btn btn-dark" href="/coach/training">Allenamenti</a><a class="btn btn-blue" href="/coach/player-stats">Statistiche giocatori</a><a class="btn" style="background:linear-gradient(135deg,#b8860b,#f5c518);color:#0a0a0a;font-weight:900;" href="/awards">⚡ Figurine Premi</a><a class="btn" href="/logout">Esci</a></div></div>
    """
    return page("Allenatore", "Gestione rapida da telefono", content)



@app.route("/coach/player-stats")
@login_required("coach")
def coach_player_stats():
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    today = date.today().isoformat()

    # Se una data non è impostata, uso un intervallo larghissimo.
    start_filter = start_date if start_date else "1900-01-01"
    end_filter = end_date if end_date else "2999-12-31"

    rows = db_query("""
        SELECT
            p.id,
            trim(p.last_name || ' ' || p.first_name) AS player_name,
            COALESCE(p.role, '') AS role,

            COALESCE(ms.presenze, 0) AS presenze,
            COALESCE(ms.titolare, 0) AS titolare,
            COALESCE(si.subentrato, 0) AS subentrato,
            COALESCE(so.sostituito, 0) AS sostituito,
            COALESCE(ms.minuti, 0) AS minuti,
            COALESCE(ms.gol, 0) AS gol,
            COALESCE(ms.assist, 0) AS assist,
            COALESCE(ms.ammonizioni, 0) AS ammonizioni,
            COALESCE(ms.espulsioni, 0) AS espulsioni,
            COALESCE(tr.all_presenti, 0) AS all_presenti,
            COALESCE(vt.media_voto, 0) AS media_voto

        FROM players p

        LEFT JOIN (
            SELECT
                a.player_id,
                COUNT(*) AS presenze,
                SUM(CASE WHEN a.starter=1 THEN 1 ELSE 0 END) AS titolare,
                SUM(a.minutes) AS minuti,
                SUM(a.goals) AS gol,
                SUM(a.assists) AS assist,
                SUM(a.yellow_cards) AS ammonizioni,
                SUM(a.red_cards) AS espulsioni
            FROM appearances a
            JOIN matches m ON m.id=a.match_id
            WHERE m.match_date BETWEEN ? AND ?
            GROUP BY a.player_id
        ) ms ON ms.player_id=p.id

        LEFT JOIN (
            SELECT
                s.player_in_id AS player_id,
                COUNT(*) AS subentrato
            FROM substitutions s
            JOIN matches m ON m.id=s.match_id
            WHERE m.match_date BETWEEN ? AND ?
            GROUP BY s.player_in_id
        ) si ON si.player_id=p.id

        LEFT JOIN (
            SELECT
                s.player_out_id AS player_id,
                COUNT(*) AS sostituito
            FROM substitutions s
            JOIN matches m ON m.id=s.match_id
            WHERE m.match_date BETWEEN ? AND ?
            GROUP BY s.player_out_id
        ) so ON so.player_id=p.id

        LEFT JOIN (
            SELECT
                ta.player_id,
                SUM(CASE WHEN ta.present=1 THEN 1 ELSE 0 END) AS all_presenti
            FROM training_attendance ta
            JOIN training_sessions ts ON ts.id=ta.session_id
            WHERE ts.training_date BETWEEN ? AND ?
            GROUP BY ta.player_id
        ) tr ON tr.player_id=p.id

        LEFT JOIN (
            SELECT
                v.voted_player_id AS player_id,
                ROUND(AVG(v.rating)::numeric, 2) AS media_voto
            FROM player_votes v
            JOIN matches m ON m.id=v.match_id
            WHERE m.match_date BETWEEN ? AND ?
            GROUP BY v.voted_player_id
        ) vt ON vt.player_id=p.id

        ORDER BY COALESCE(ms.minuti,0) DESC, p.last_name, p.first_name
    """, (
        start_filter, end_filter,
        start_filter, end_filter,
        start_filter, end_filter,
        start_filter, end_filter,
        start_filter, end_filter,
    ), fetch=True)

    table_rows = ""

    for r in rows:
        table_rows += f"""
        <tr>
            <td><b>{r['player_name']}</b><br><span class="small">{r['role'] or '-'}</span></td>
            <td>{r['presenze']}</td>
            <td>{r['titolare']}</td>
            <td>{r['subentrato']}</td>
            <td>{r['sostituito']}</td>
            <td>{r['minuti']}</td>
            <td>{r['gol']}</td>
            <td>{r['assist']}</td>
            <td>{r['ammonizioni']}</td>
            <td>{r['espulsioni']}</td>
            <td>{r['all_presenti']}</td>
            <td><b>{r['media_voto']}</b></td>
        </tr>
        """

    if not table_rows:
        table_rows = "<tr><td colspan='12'>Nessun giocatore presente.</td></tr>"

    content = f"""
    <div class="card">
        <h2>Statistiche giocatori</h2>
        <div class="small">Filtra le statistiche per periodo. Se nel periodo non ci sono dati, i giocatori vengono mostrati con tutti i valori a 0.</div>

        <form method="get">
            <div class="inline">
                <div>
                    <label>Dal</label>
                    <input type="date" name="start_date" value="{start_date}">
                </div>
                <div>
                    <label>Al</label>
                    <input type="date" name="end_date" value="{end_date or today}">
                </div>
            </div>
            <button class="btn-blue">Filtra periodo</button>
            <a class="btn btn-dark" href="/coach/player-stats">Azzera filtro</a>
        </form>
    </div>

    <div class="card">
        <div class="table-wrap">
            <table class="stats-table">
                <thead>
                    <tr>
                        <th>Giocatore</th>
                        <th>Pres</th>
                        <th>Tit</th>
                        <th>Sub</th>
                        <th>Sost</th>
                        <th>Min</th>
                        <th>Gol</th>
                        <th>Ast</th>
                        <th>Amm</th>
                        <th>Esp</th>
                        <th>Allen.</th>
                        <th>Voto</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
        </div>
    </div>

    <a class="btn btn-blue" href="/coach">Indietro</a>
    """

    return page("Statistiche giocatori", "Area allenatore", content)



@app.route("/coach/matches", methods=["GET", "POST"])
@login_required("coach")
def coach_matches():
    if request.method == "POST":
        match_date = request.form.get("match_date")
        opponent = request.form.get("opponent", "").strip()
        competition = request.form.get("competition", "Campionato")
        home_away = request.form.get("home_away", "Casa")
        if not opponent:
            flash("Inserisci avversario.")
            return redirect(url_for("coach_matches"))
        db_query("INSERT INTO matches (match_date,opponent,competition,home_away) VALUES (?,?,?,?)", (match_date, opponent, competition, home_away))
        flash("Partita inserita.")
        return redirect(url_for("coach_matches"))
    rows = db_query("SELECT id,match_date,opponent,competition,home_away,result FROM matches ORDER BY match_date DESC,id DESC LIMIT 10", fetch=True)
    match_list = "".join(f"<div class='player-row'><b>#{m['id']} · {ui_date(m['match_date'])}</b><br>{m['opponent']}<br><span class='small'>{m['competition']} · {m['home_away']} · {m['result'] or '-'}</span></div>" for m in rows)
    today = date.today().isoformat()
    content = f"""
    <div class="card"><h2>Nuova partita</h2><form method="post"><label>Data</label><input type="date" name="match_date" value="{today}" required><label>Avversario</label><input name="opponent" required><label>Competizione</label><select name="competition"><option>Campionato</option><option>Coppa</option></select><label>Casa/Fuori</label><select name="home_away"><option>Casa</option><option>Fuori</option></select><button>Salva partita</button></form></div>
    <div class="card"><h2>Ultime partite</h2>{match_list or 'Nessuna partita.'}</div><a class="btn btn-blue" href="/coach">Indietro</a>
    """
    return page("Partite", "Inserimento rapido partita", content)


@app.route("/coach/formation", methods=["GET", "POST"])
@login_required("coach")
def coach_formation():
    matches = db_query("SELECT id,match_date,opponent,competition,home_away,result FROM matches ORDER BY match_date DESC,id DESC LIMIT 30", fetch=True)
    players = get_players()
    selected_match_id = request.values.get("match_id") or (str(matches[0]["id"]) if matches else None)
    if request.method == "POST":
        match_id = int(request.form.get("match_id"))
        result = request.form.get("result", "").strip()
        appearance_rows = []
        for player in players:
            pid = player["id"]
            if not request.form.get(f"play_{pid}"):
                continue
            starter = 1 if request.form.get(f"starter_{pid}") else 0
            try:
                minutes = int(request.form.get(f"minutes_{pid}") or 0)
                goals = int(request.form.get(f"goals_{pid}") or 0)
                assists = int(request.form.get(f"assists_{pid}") or 0)
            except ValueError:
                minutes = goals = assists = 0
            yellow = 1 if request.form.get(f"yellow_{pid}") else 0
            red = 1 if request.form.get(f"red_{pid}") else 0
            appearance_rows.append((match_id, pid, starter, minutes, goals, assists, yellow, red))

        db_transaction(
            statements=[
                ("UPDATE matches SET result=? WHERE id=?", (result, match_id)),
                ("DELETE FROM appearances WHERE match_id=?", (match_id,)),
            ],
            batches=[("""
                INSERT INTO appearances (match_id,player_id,starter,minutes,goals,assists,yellow_cards,red_cards)
                VALUES (?,?,?,?,?,?,?,?)
            """, appearance_rows)],
        )
        flash("Formazione salvata.")
        return redirect(url_for("coach_formation", match_id=match_id))
    existing = {}
    selected_result = ""
    if selected_match_id:
        selected = db_query("SELECT result FROM matches WHERE id=?", (selected_match_id,), fetch=True)
        selected_result = selected[0]["result"] if selected else ""
        rows = db_query("SELECT * FROM appearances WHERE match_id=?", (selected_match_id,), fetch=True)
        existing = {r["player_id"]: r for r in rows}
    match_options = "".join(f"<option value='{m['id']}' {'selected' if str(m['id']) == str(selected_match_id) else ''}>#{m['id']} · {ui_date(m['match_date'])} vs {m['opponent']}</option>" for m in matches)
    player_rows = ""
    for p in players:
        ex = existing.get(p["id"])
        player_rows += f"""
        <div class="player-row"><div class="player-title">{player_name(p)}</div><div class="small">{p['role'] or '-'}</div>
        <div class="checks"><label><input type="checkbox" name="play_{p['id']}" {'checked' if ex else ''}> Convocato</label><label><input type="checkbox" name="starter_{p['id']}" {'checked' if ex and ex['starter'] else ''}> Titolare</label></div>
        <div class="inline"><div><label>Minuti</label><input type="number" min="0" max="130" name="minutes_{p['id']}" value="{ex['minutes'] if ex else 0}"></div><div><label>Gol</label><input type="number" min="0" name="goals_{p['id']}" value="{ex['goals'] if ex else 0}"></div></div>
        <div class="inline"><div><label>Assist</label><input type="number" min="0" name="assists_{p['id']}" value="{ex['assists'] if ex else 0}"></div><div><label>Cartellini</label><div class="checks"><label><input type="checkbox" name="yellow_{p['id']}" {'checked' if ex and ex['yellow_cards'] else ''}> Amm.</label><label><input type="checkbox" name="red_{p['id']}" {'checked' if ex and ex['red_cards'] else ''}> Esp.</label></div></div></div></div>
        """
    content = f"""
    <div class="card"><h2>Formazione partita</h2><form method="get"><label>Partita</label><select name="match_id" onchange="this.form.submit()">{match_options}</select></form></div>
    <form method="post"><input type="hidden" name="match_id" value="{selected_match_id or ''}"><div class="card"><label>Risultato</label><input name="result" placeholder="es. 2-1" value="{selected_result or ''}"></div><div class="card"><h2>Giocatori</h2>{player_rows or 'Nessun giocatore.'}<button>Salva formazione</button></div></form><a class="btn btn-blue" href="/coach">Indietro</a>
    """
    return page("Formazione", "Gestione formazione e dati partita", content)


@app.route("/coach/training", methods=["GET", "POST"])
@login_required("coach")
def coach_training():
    players = get_players()
    if request.method == "POST" and request.form.get("action") == "new_training":
        db_query("INSERT INTO training_sessions (training_date,title) VALUES (?,?)", (request.form.get("training_date"), request.form.get("title", "Allenamento")))
        flash("Allenamento creato.")
        return redirect(url_for("coach_training"))
    sessions = db_query("SELECT id,training_date,title FROM training_sessions ORDER BY training_date DESC,id DESC LIMIT 30", fetch=True)
    selected_session_id = request.values.get("session_id") or (str(sessions[0]["id"]) if sessions else None)
    if request.method == "POST" and request.form.get("action") == "save_attendance":
        session_id = int(request.form.get("session_id"))
        attendance_rows = []
        for p in players:
            pid = p["id"]
            try:
                status_int = int(request.form.get(f"status_{pid}", "0"))
            except ValueError:
                status_int = 0
            attendance_rows.append((session_id, pid, status_int))

        db_transaction(
            statements=[("DELETE FROM training_attendance WHERE session_id=?", (session_id,))],
            batches=[("INSERT INTO training_attendance (session_id,player_id,present) VALUES (?,?,?)", attendance_rows)],
        )
        flash("Presenze salvate.")
        return redirect(url_for("coach_training", session_id=session_id))
    existing = {}
    if selected_session_id:
        rows = db_query("SELECT player_id,present FROM training_attendance WHERE session_id=?", (selected_session_id,), fetch=True)
        existing = {r["player_id"]: r["present"] for r in rows}
    session_options = "".join(f"<option value='{s['id']}' {'selected' if str(s['id']) == str(selected_session_id) else ''}>#{s['id']} · {ui_date(s['training_date'])} · {s['title']}</option>" for s in sessions)
    player_rows = ""
    for p in players:
        status = existing.get(p["id"], 0)
        player_rows += f"""
        <div class="player-row"><div class="player-title">{player_name(p)}</div><div class="small">{p['role'] or '-'}</div>
        <div class="checks"><label><input type="radio" name="status_{p['id']}" value="1" {'checked' if status == 1 else ''}> Presente</label><label><input type="radio" name="status_{p['id']}" value="2" {'checked' if status == 2 else ''}> Infortunato</label></div>
        <label class="small"><input type="radio" name="status_{p['id']}" value="0" {'checked' if status == 0 else ''}> Assente</label></div>
        """
    today = date.today().isoformat()
    content = f"""
    <div class="card"><h2>Nuovo allenamento</h2><form method="post"><input type="hidden" name="action" value="new_training"><label>Data</label><input type="date" name="training_date" value="{today}" required><label>Titolo</label><input name="title" value="Allenamento"><button>Crea allenamento</button></form></div>
    <div class="card"><h2>Seleziona allenamento</h2><form method="get"><select name="session_id" onchange="this.form.submit()">{session_options}</select></form></div>
    <form method="post"><input type="hidden" name="action" value="save_attendance"><input type="hidden" name="session_id" value="{selected_session_id or ''}"><div class="card"><h2>Presenze</h2>{player_rows or 'Nessun giocatore.'}<button>Salva presenze</button></div></form><a class="btn btn-blue" href="/coach">Indietro</a>
    """
    return page("Allenamenti", "Presenze e infortunati", content)


if __name__ == "__main__":
    ensure_mobile_tables()
    app.run(host="0.0.0.0", port=5000, debug=False)
