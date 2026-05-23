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

# Nomi autorizzati ad accedere anche se non sono presenti nella rosa calciatori.
# Vengono gestiti come “votanti mister” creando, se manca, un record tecnico
# nella tabella players con ruolo MISTER, così i vincoli del database sui voti restano validi.
AUTHORIZED_COACH_PLAYER_ACCESS = {
    ("Stefano", "Lanzellotto"),
    ("Luigi", "Andreoli"),
    ("Lampos", "Lampos"),
}

# Presidente: accesso speciale con ruolo PRES.
# Non compare mai nelle statistiche desktop né nella lista giocatori web.
AUTHORIZED_PRES_ACCESS = {
    ("Luca", "Milani"),
}


def _norm_name(value):
    return " ".join((value or "").strip().lower().split())


def is_authorized_coach_name(first_name, last_name):
    return (_norm_name(first_name), _norm_name(last_name)) in {
        (_norm_name(f), _norm_name(l)) for f, l in AUTHORIZED_COACH_PLAYER_ACCESS
    }


def is_authorized_pres_name(first_name, last_name):
    return (_norm_name(first_name), _norm_name(last_name)) in {
        (_norm_name(f), _norm_name(l)) for f, l in AUTHORIZED_PRES_ACCESS
    }


def get_or_create_coach_player(first_name, last_name):
    rows = db_query("""
        SELECT id, first_name, last_name
        FROM players
        WHERE lower(trim(first_name))=lower(trim(?))
          AND lower(trim(last_name))=lower(trim(?))
        ORDER BY id
        LIMIT 1
    """, (first_name, last_name), fetch=True)

    if rows:
        return rows[0]

    db_query("""
        INSERT INTO players (first_name, last_name, birth_date, role)
        VALUES (?, ?, '', 'MISTER')
    """, (first_name.strip().title(), last_name.strip().title()))

    rows = db_query("""
        SELECT id, first_name, last_name
        FROM players
        WHERE lower(trim(first_name))=lower(trim(?))
          AND lower(trim(last_name))=lower(trim(?))
        ORDER BY id DESC
        LIMIT 1
    """, (first_name, last_name), fetch=True)
    return rows[0] if rows else None


def get_or_create_pres_player(first_name, last_name):
    """Crea (se non esiste) un record tecnico con ruolo PRES per il presidente."""
    rows = db_query("""
        SELECT id, first_name, last_name
        FROM players
        WHERE lower(trim(first_name))=lower(trim(?))
          AND lower(trim(last_name))=lower(trim(?))
        ORDER BY id
        LIMIT 1
    """, (first_name, last_name), fetch=True)

    if rows:
        return rows[0]

    db_query("""
        INSERT INTO players (first_name, last_name, birth_date, role)
        VALUES (?, ?, '', 'PRES')
    """, (first_name.strip().title(), last_name.strip().title()))

    rows = db_query("""
        SELECT id, first_name, last_name
        FROM players
        WHERE lower(trim(first_name))=lower(trim(?))
          AND lower(trim(last_name))=lower(trim(?))
        ORDER BY id DESC
        LIMIT 1
    """, (first_name, last_name), fetch=True)
    return rows[0] if rows else None

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
                    subentrato INTEGER DEFAULT 0,
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
            cur.execute("ALTER TABLE appearances ADD COLUMN IF NOT EXISTS starter INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE appearances ADD COLUMN IF NOT EXISTS subentrato INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE appearances ADD COLUMN IF NOT EXISTS minutes INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE appearances ADD COLUMN IF NOT EXISTS goals INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE appearances ADD COLUMN IF NOT EXISTS assists INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE appearances ADD COLUMN IF NOT EXISTS yellow_cards INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE appearances ADD COLUMN IF NOT EXISTS red_cards INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE appearances ADD COLUMN IF NOT EXISTS captain INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE appearances ADD COLUMN IF NOT EXISTS vice_captain INTEGER DEFAULT 0")
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


def parse_team_goals_from_result(result, home_away):
    """Restituisce i gol della nostra squadra partendo dal risultato tipo 2-1."""
    clean = (result or "").replace(" ", "")
    if not clean:
        return None, "Inserisci il risultato nel formato 2-1."
    if "-" not in clean:
        return None, "Il risultato deve essere nel formato 2-1."

    parts = clean.split("-")
    if len(parts) != 2:
        return None, "Il risultato deve essere nel formato 2-1."

    try:
        left = int(parts[0])
        right = int(parts[1])
    except ValueError:
        return None, "Il risultato deve contenere solo numeri, esempio 2-1."

    if left < 0 or right < 0:
        return None, "Il risultato non può avere numeri negativi."

    return (left if home_away == "Casa" else right), None


def get_players():
    return db_query("""
        SELECT id, first_name, last_name, role
        FROM players
        WHERE LOWER(TRIM(COALESCE(role, ''))) NOT IN ('mister', 'pres')
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
    """Figurina FUT — MOTM: nera + oro."""
    last  = (p.get('last_name')  or '').upper()
    first = (p.get('first_name') or '').upper()
    role  = p.get('role') or ''
    # Doppio ruolo: "CDC/ATT" → mostra entrambi
    role_display = " · ".join(r.strip() for r in role.split("/") if r.strip()) or "—"

    if p.get("photo_data"):
        photo_html = f"<img class='card-photo-award' src='data:{p['photo_mime']};base64,{p['photo_data']}' alt=''>"
    else:
        photo_html = "<div class='card-placeholder-award'>👤</div>"

    match_info = f"{ui_date(p['match_date'])} · {p['opponent']}"
    score = p['media_voto']
    # Stelle: 1 per ogni punto sopra il 5, max 5
    try:
        stars = min(5, max(1, round((float(score) - 4) / 1.2)))
    except Exception:
        stars = 3
    stars_html = "★" * stars + "☆" * (5 - stars)

    return f"""
    <div class="award-card-week">
      <div class="fut-header">
        <div class="fut-type">⚡ Man of the Match</div>
        <div class="fut-badge">⭐ MOTM · Partita</div>
      </div>
      <span class="lightning left">⚡</span>
      <span class="lightning right">⚡</span>
      <div class="fut-body">
        <div class="fut-photo-wrap">
          <div class="fut-photo-ring">{photo_html}</div>
        </div>
        <div class="fut-stars">{stars_html}</div>
        <div class="award-score">{score}</div>
        <div class="fut-score-label">Media voto</div>
        <div class="fut-divider"></div>
        <div class="award-name">{last} {first}</div>
        <div class="fut-role-pill">{role_display}</div>
        <div class="award-meta">{match_info}</div>
      </div>
    </div>
    """


def _render_month_card(p):
    """Figurina FUT — MOTW: rossa + blu."""
    last  = (p.get('last_name')  or '').upper()
    first = (p.get('first_name') or '').upper()
    role  = p.get('role') or ''
    role_display = " · ".join(r.strip() for r in role.split("/") if r.strip()) or "—"

    if p.get("photo_data"):
        photo_html = f"<img class='card-photo-award' src='data:{p['photo_mime']};base64,{p['photo_data']}' alt=''>"
    else:
        photo_html = "<div class='card-placeholder-award'>👤</div>"

    month_label = p.get("month_label", "")
    score = p['media_voto']
    try:
        stars = min(5, max(1, round((float(score) - 4) / 1.2)))
    except Exception:
        stars = 3
    stars_html = "★" * stars + "☆" * (5 - stars)

    return f"""
    <div class="award-card-month">
      <div class="fut-header">
        <div class="fut-type">🏆 Giocatore del Mese</div>
        <div class="fut-badge">🥇 POTM · {month_label}</div>
      </div>
      <div class="fut-body">
        <div class="fut-photo-wrap">
          <div class="fut-photo-ring">{photo_html}</div>
        </div>
        <div class="fut-stars">{stars_html}</div>
        <div class="award-score">{score}</div>
        <div class="fut-score-label">Media voto</div>
        <div class="fut-divider"></div>
        <div class="award-name">{last} {first}</div>
        <div class="fut-role-pill">{role_display}</div>
      </div>
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
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap');
:root{
  --gold:#c9a84c;--gold-light:#f0c040;--gold-dark:#a07828;
  --green-deep:#0a1f0e;--green-mid:#112a14;--green-card:#162b19;--green-surface:#1c3520;
  --green-accent:#1a7a2e;--green-bright:#22c55e;
  --white:#f0f4f0;--white-muted:#b0bfb4;
  --border:#2a4a2e;--border-light:#3a5e3e;
  --text:#e8f0e8;--muted:#7a9a7e;
  --red:#e03535;--blue:#1a6fd4;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{
  margin:0;
  font-family:'Inter',-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
  background:var(--green-deep);
  color:var(--text);
  min-height:100vh;
}

/* ── HEADER ── */
.header{
  background:linear-gradient(160deg,#071510 0%,#0d2410 50%,#0a1f0e 100%);
  padding:22px 18px 26px;
  border-bottom:2px solid var(--gold-dark);
  position:relative;
  overflow:hidden;
}
.header::before{
  content:'';position:absolute;inset:0;
  background:repeating-linear-gradient(
    45deg,transparent,transparent 28px,
    rgba(201,168,76,.04) 28px,rgba(201,168,76,.04) 30px
  );
  pointer-events:none;
}
.header h1{
  margin:0;font-size:24px;font-weight:900;
  color:var(--gold-light);
  text-shadow:0 0 20px rgba(201,168,76,.4);
  position:relative;
}
.header p{
  margin:5px 0 0;font-size:13px;color:var(--white-muted);
  position:relative;
}

/* ── CONTAINER ── */
.container{padding:16px;max-width:820px;margin:auto}

/* ── CARD ── */
.card{
  background:var(--green-card);
  border:1px solid var(--border);
  border-radius:20px;
  padding:18px;
  margin-bottom:14px;
  box-shadow:0 8px 28px rgba(0,0,0,.4);
  position:relative;
  overflow:hidden;
}
.card::before{
  content:'';position:absolute;top:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,transparent,var(--gold-dark),var(--gold-light),var(--gold-dark),transparent);
  opacity:.6;
}
h2{font-size:19px;font-weight:800;margin:0 0 14px;color:var(--gold-light)}

/* ── INPUTS ── */
label{display:block;font-weight:700;color:var(--muted);font-size:12px;margin:10px 0 5px;text-transform:uppercase;letter-spacing:.5px}
input,select{
  width:100%;height:44px;
  border:1px solid var(--border-light);
  border-radius:12px;padding:0 14px;font-size:15px;
  background:var(--green-surface);
  color:var(--text);
  outline:none;
  transition:border-color .2s,box-shadow .2s;
}
input:focus,select:focus{border-color:var(--gold);box-shadow:0 0 0 3px rgba(201,168,76,.15)}
select option{background:var(--green-mid)}
input:-webkit-autofill,
input:-webkit-autofill:hover,
input:-webkit-autofill:focus{
  -webkit-text-fill-color:var(--text) !important;
  caret-color:var(--text);
  transition:background-color 9999s ease-in-out 0s;
}


/* ── BUTTONS ── */
button,.btn{
  display:block;width:100%;border:0;
  border-radius:14px;padding:13px 16px;
  background:linear-gradient(135deg,var(--gold-dark),var(--gold-light),var(--gold-dark));
  background-size:200% 100%;background-position:100%;
  color:#0a1f0e;font-weight:900;font-size:15px;
  text-decoration:none;text-align:center;margin-top:12px;
  cursor:pointer;
  transition:background-position .3s,box-shadow .2s,transform .1s;
  box-shadow:0 4px 14px rgba(201,168,76,.3);
  letter-spacing:.3px;
}
button:hover,.btn:hover{background-position:0%;box-shadow:0 6px 20px rgba(201,168,76,.45);transform:translateY(-1px)}
button:active,.btn:active{transform:translateY(0)}
.btn-blue{
  background:linear-gradient(135deg,#0e4a9e,#1a6fd4,#0e4a9e);background-size:200% 100%;background-position:100%;
  color:white;box-shadow:0 4px 14px rgba(26,111,212,.3);
}
.btn-blue:hover{background-position:0%;box-shadow:0 6px 20px rgba(26,111,212,.45)}
.btn-green{
  background:linear-gradient(135deg,#0f5c1e,#1a7a2e,#0f5c1e);background-size:200% 100%;background-position:100%;
  color:white;box-shadow:0 4px 14px rgba(26,122,46,.3);
}
.btn-green:hover{background-position:0%;box-shadow:0 6px 20px rgba(26,122,46,.45)}
.btn-dark{
  background:linear-gradient(135deg,#0a1f0e,#162b19,#0a1f0e);background-size:200% 100%;background-position:100%;
  color:var(--white-muted);border:1px solid var(--border-light);
  box-shadow:0 4px 14px rgba(0,0,0,.3);
}
.btn-dark:hover{background-position:0%;color:var(--white)}

/* ── LAYOUT HELPERS ── */
.row{display:grid;grid-template-columns:1fr 90px;gap:10px;align-items:center}
.inline{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.tabs{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.small-btn{display:inline-block;width:auto;margin-top:6px;padding:8px 12px;font-size:12px;border-radius:10px}

/* ── PLAYER ROW ── */
.player-row{
  border:1px solid var(--border);border-radius:16px;
  padding:14px;margin:8px 0;
  background:var(--green-surface);
  transition:border-color .2s,background .2s;
}
.player-row:hover{border-color:var(--gold-dark);background:var(--green-card)}
.player-title{font-weight:800;color:var(--text)}
.small{color:var(--muted);font-size:12px}

/* ── FLASH ── */
.flash{
  background:rgba(224,53,53,.15);color:#fca5a5;
  border:1px solid rgba(224,53,53,.35);
  border-radius:12px;padding:10px;margin-bottom:12px;font-weight:700;
}

/* ── CHECKS ── */
.checks{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}
.checks label{
  margin:0;padding:12px;
  background:var(--green-surface);border:1px solid var(--border);
  border-radius:12px;color:var(--text);font-size:13px;
  cursor:pointer;transition:border-color .2s;
}
.checks label:hover{border-color:var(--gold-dark)}
.checks input{width:auto;height:auto;margin-right:8px;accent-color:var(--gold)}

/* ── PLAYER CARD FIGURINA ── */
.card-preview{
  text-align:center;
  background:linear-gradient(180deg,var(--green-surface),var(--green-card));
}
.card-photo{
  width:160px;height:160px;object-fit:cover;
  border-radius:50%;
  border:4px solid var(--gold);
  box-shadow:0 0 24px rgba(201,168,76,.4),0 10px 30px rgba(0,0,0,.5);
  background:var(--green-surface);
}
.card-placeholder{
  width:160px;height:160px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  margin:0 auto;background:var(--green-surface);font-size:62px;
  border:4px solid var(--gold-dark);
  box-shadow:0 0 24px rgba(201,168,76,.2);
}
.card-name{font-size:24px;font-weight:900;margin-top:14px;color:var(--gold-light)}
.card-role{
  display:inline-block;margin-top:6px;
  background:linear-gradient(135deg,var(--gold-dark),var(--gold-light));
  color:#0a1f0e;font-weight:900;font-size:12px;
  padding:3px 14px;border-radius:20px;letter-spacing:.5px;text-transform:uppercase;
}

/* ── STATS GRID ── */
.stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:16px}
.stat-box{
  background:var(--green-surface);border:1px solid var(--border);
  border-radius:14px;padding:12px 6px;text-align:center;
  transition:border-color .2s;
}
.stat-box:hover{border-color:var(--gold-dark)}
.stat-value{font-size:22px;font-weight:900;color:var(--gold-light)}
.stat-label{font-size:11px;color:var(--muted);font-weight:700;margin-top:3px;text-transform:uppercase;letter-spacing:.4px}

/* ── STATS TABLE ── */
.stats-table{width:100%;border-collapse:collapse;font-size:13px}
.stats-table th{
  background:linear-gradient(135deg,#071510,#0d2410);
  color:var(--gold-light);padding:10px 8px;text-align:center;
  position:sticky;top:0;font-weight:800;letter-spacing:.4px;text-transform:uppercase;font-size:11px;
}
.stats-table td{
  border-bottom:1px solid var(--border);padding:9px 8px;
  text-align:center;color:var(--text);
  transition:background .15s;
}
.stats-table tr:hover td{background:var(--green-surface)}
.stats-table td:first-child,.stats-table th:first-child{text-align:left;min-width:145px}
.table-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:16px}

/* ── PERFORMANCE CARD ── */
.performance-card{
  border:1px solid var(--border);border-radius:18px;
  background:var(--green-surface);padding:16px;margin:10px 0;
  box-shadow:0 4px 16px rgba(0,0,0,.3);
  transition:border-color .2s;
}
.performance-card:hover{border-color:var(--gold-dark)}
.performance-title{font-weight:900;font-size:16px;color:var(--text)}
.performance-meta{color:var(--muted);font-size:12px;margin-top:3px}
.performance-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:6px;margin-top:12px}
.performance-stat{
  background:var(--green-card);border:1px solid var(--border);
  border-radius:12px;padding:8px 4px;text-align:center;
}
.performance-value{font-size:17px;font-weight:900;color:var(--gold-light)}
.performance-label{font-size:10px;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:.3px}
.footer-space{height:30px}

/* ── VOTE BUTTONS ── */
.vote-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:8px}
.vote-btn{
  padding:10px 4px;border-radius:12px;font-size:14px;font-weight:800;
  background:var(--green-surface);border:1px solid var(--border);
  color:var(--text);cursor:pointer;text-align:center;
  transition:all .15s;
}
.vote-btn:hover,.vote-btn.selected{
  background:linear-gradient(135deg,var(--gold-dark),var(--gold-light));
  color:#0a1f0e;border-color:var(--gold);
  box-shadow:0 4px 12px rgba(201,168,76,.35);
  transform:translateY(-1px);
}


/* ══════════════════════════════════════════════
   FIGURINA SETTIMANA — FUT Style: NERA + ORO
══════════════════════════════════════════════ */
.award-card-week{
  background:linear-gradient(175deg,#0b0b0b 0%,#1a1400 35%,#0d0d0d 65%,#110e00 100%);
  border:2px solid #8a6a00;
  border-radius:18px;
  padding:0;
  margin-bottom:14px;
  box-shadow:
    0 0 0 1px #2a2000,
    0 0 30px rgba(212,168,0,.25),
    0 16px 40px rgba(0,0,0,.85);
  position:relative;overflow:hidden;text-align:center;
  max-width:320px;margin-left:auto;margin-right:auto;
}
/* Cornice FUT: doppio bordo oro */
.award-card-week::before{
  content:'';position:absolute;inset:3px;
  border:1px solid rgba(212,168,0,.25);
  border-radius:15px;pointer-events:none;z-index:1;
}
/* Bagliore dorato in cima */
.award-card-week::after{
  content:'';position:absolute;
  top:-40px;left:50%;transform:translateX(-50%);
  width:180px;height:180px;
  background:radial-gradient(circle,rgba(212,168,0,.18) 0%,transparent 70%);
  pointer-events:none;z-index:0;
}
.award-card-week .fut-header{
  background:linear-gradient(180deg,#1a1400 0%,#0f0f0f 100%);
  padding:14px 18px 10px;
  position:relative;z-index:2;
  border-bottom:1px solid rgba(212,168,0,.2);
}
.award-card-week .fut-type{
  font-size:9px;font-weight:900;letter-spacing:2.5px;text-transform:uppercase;
  color:#8a6a00;margin-bottom:4px;
}
.award-card-week .fut-badge{
  display:inline-flex;align-items:center;gap:6px;
  background:linear-gradient(135deg,#8a6a00,#d4a800,#f0c820,#d4a800,#8a6a00);
  color:#0b0b0b;font-weight:900;font-size:12px;
  padding:5px 16px;border-radius:30px;letter-spacing:.6px;
  box-shadow:0 2px 12px rgba(212,168,0,.4);
}
.award-card-week .fut-body{
  padding:20px 18px 16px;position:relative;z-index:2;
}
.award-card-week .lightning{
  position:absolute;top:50px;font-size:20px;opacity:.6;
  filter:drop-shadow(0 0 6px #d4a800);z-index:3;
}
.award-card-week .lightning.left{left:10px;transform:rotate(-20deg)}
.award-card-week .lightning.right{right:10px;transform:rotate(20deg)}
/* Foto con cornice esagonale via clip-path */
.award-card-week .fut-photo-wrap{
  position:relative;display:inline-block;margin-bottom:12px;
}
.award-card-week .fut-photo-ring{
  width:124px;height:124px;border-radius:50%;
  background:linear-gradient(135deg,#8a6a00,#f0c820,#8a6a00);
  padding:3px;display:inline-block;
  box-shadow:0 0 20px rgba(212,168,0,.5);
}
.award-card-week .card-photo-award{
  width:118px;height:118px;object-fit:cover;border-radius:50%;
  display:block;background:#1a1400;
}
.award-card-week .card-placeholder-award{
  width:118px;height:118px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  font-size:52px;background:#1a1400;
}
/* Stelle rating */
.award-card-week .fut-stars{
  color:#d4a800;font-size:13px;letter-spacing:2px;margin-bottom:6px;
}
.award-card-week .award-score{
  font-size:52px;font-weight:900;line-height:1;
  color:#f0c820;
  text-shadow:0 0 20px rgba(240,192,32,.6),0 2px 0 rgba(0,0,0,.8);
  letter-spacing:-1px;
}
.award-card-week .fut-score-label{
  font-size:10px;font-weight:800;letter-spacing:1.5px;text-transform:uppercase;
  color:#8a6a00;margin-top:4px;
}
/* Divider oro */
.award-card-week .fut-divider{
  height:1px;background:linear-gradient(90deg,transparent,#8a6a00,#d4a800,#8a6a00,transparent);
  margin:12px 0;
}
.award-card-week .award-name{
  font-size:18px;font-weight:900;color:#f0c820;
  text-transform:uppercase;letter-spacing:1.5px;
  text-shadow:0 0 10px rgba(240,192,32,.3);
}
.award-card-week .fut-role-pill{
  display:inline-block;margin-top:6px;
  background:linear-gradient(135deg,#8a6a00,#d4a800);
  color:#0b0b0b;font-weight:900;font-size:11px;
  padding:3px 12px;border-radius:20px;letter-spacing:.8px;text-transform:uppercase;
}
.award-card-week .award-meta{
  font-size:11px;color:#5a5030;margin-top:8px;letter-spacing:.3px;
}
/* Mini stats FUT */
.award-card-week .fut-stats{
  display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px;
}
.award-card-week .fut-stat{
  background:rgba(212,168,0,.06);border:1px solid rgba(212,168,0,.12);
  border-radius:8px;padding:6px 4px;
}
.award-card-week .fut-stat-val{font-size:15px;font-weight:900;color:#d4a800;}
.award-card-week .fut-stat-lbl{font-size:9px;font-weight:800;color:#5a5030;text-transform:uppercase;letter-spacing:.5px;}

/* ══════════════════════════════════════════════
   FIGURINA MESE — FUT Style: ROSSO + BLU
══════════════════════════════════════════════ */
.award-card-month{
  background:linear-gradient(175deg,#0d0008 0%,#1a0016 35%,#00061a 65%,#0a0014 100%);
  border:2px solid #6a1a6a;
  border-radius:18px;
  padding:0;
  margin-bottom:14px;
  box-shadow:
    0 0 0 1px #1a0030,
    0 0 30px rgba(180,0,80,.2),
    0 0 30px rgba(0,60,200,.15),
    0 16px 40px rgba(0,0,0,.85);
  position:relative;overflow:hidden;text-align:center;
  max-width:320px;margin-left:auto;margin-right:auto;
}
.award-card-month::before{
  content:'';position:absolute;inset:3px;
  border:1px solid rgba(200,50,200,.15);
  border-radius:15px;pointer-events:none;z-index:1;
}
.award-card-month::after{
  content:'';position:absolute;
  top:-40px;left:50%;transform:translateX(-50%);
  width:200px;height:200px;
  background:radial-gradient(circle,rgba(180,0,100,.15) 0%,rgba(0,50,180,.1) 50%,transparent 70%);
  pointer-events:none;z-index:0;
}
.award-card-month .fut-header{
  background:linear-gradient(180deg,#1a0016 0%,#0a000f 100%);
  padding:14px 18px 10px;
  position:relative;z-index:2;
  border-bottom:1px solid rgba(180,0,100,.2);
}
.award-card-month .fut-type{
  font-size:9px;font-weight:900;letter-spacing:2.5px;text-transform:uppercase;
  color:#8a2080;margin-bottom:4px;
}
.award-card-month .fut-badge{
  display:inline-flex;align-items:center;gap:6px;
  background:linear-gradient(135deg,#8b0030,#cc0050,#ff2060,#1a50cc,#0030aa);
  color:#fff;font-weight:900;font-size:12px;
  padding:5px 16px;border-radius:30px;letter-spacing:.6px;
  box-shadow:0 2px 12px rgba(180,0,80,.4);
}
.award-card-month .fut-body{
  padding:20px 18px 16px;position:relative;z-index:2;
}
.award-card-month .fut-photo-ring{
  width:124px;height:124px;border-radius:50%;
  background:linear-gradient(135deg,#8b0030,#cc0050,#1a50cc,#0030aa);
  padding:3px;display:inline-block;
  box-shadow:0 0 20px rgba(180,0,80,.4),0 0 20px rgba(0,50,180,.3);
}
.award-card-month .card-photo-award{
  width:118px;height:118px;object-fit:cover;border-radius:50%;
  display:block;background:#1a0016;
}
.award-card-month .card-placeholder-award{
  width:118px;height:118px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  font-size:52px;background:#1a0016;
}
.award-card-month .fut-stars{
  font-size:13px;letter-spacing:2px;margin-bottom:6px;
  background:linear-gradient(90deg,#ff2060,#6060ff);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
}
.award-card-month .award-score{
  font-size:52px;font-weight:900;line-height:1;letter-spacing:-1px;
  background:linear-gradient(135deg,#ff4080,#6080ff);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
  filter:drop-shadow(0 0 10px rgba(180,0,80,.4));
}
.award-card-month .fut-score-label{
  font-size:10px;font-weight:800;letter-spacing:1.5px;text-transform:uppercase;
  color:#6a1a6a;margin-top:4px;
}
.award-card-month .fut-divider{
  height:1px;background:linear-gradient(90deg,transparent,#8b0030,#cc0050,#1a50cc,transparent);
  margin:12px 0;
}
.award-card-month .award-name{
  font-size:18px;font-weight:900;text-transform:uppercase;letter-spacing:1.5px;
  background:linear-gradient(135deg,#ff6090,#8090ff);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
}
.award-card-month .fut-role-pill{
  display:inline-block;margin-top:6px;
  background:linear-gradient(135deg,#8b0030,#1a50cc);
  color:#fff;font-weight:900;font-size:11px;
  padding:3px 12px;border-radius:20px;letter-spacing:.8px;text-transform:uppercase;
}
.award-card-month .award-meta{
  font-size:11px;color:#4a1a4a;margin-top:8px;letter-spacing:.3px;
}
.award-card-month .fut-stats{
  display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px;
}
.award-card-month .fut-stat{
  background:rgba(180,0,80,.06);border:1px solid rgba(180,0,80,.12);
  border-radius:8px;padding:6px 4px;
}
.award-card-month .fut-stat-val{
  font-size:15px;font-weight:900;
  background:linear-gradient(135deg,#ff4080,#6080ff);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
}
.award-card-month .fut-stat-lbl{font-size:9px;font-weight:800;color:#4a1a4a;text-transform:uppercase;letter-spacing:.5px;}
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
            is_coach_player_access = False
            is_pres_player_access = False

            if not player and is_authorized_pres_name(first_name, last_name):
                pres_player = get_or_create_pres_player(first_name, last_name)
                if pres_player:
                    player = [pres_player]
                    is_pres_player_access = True

            if not player and is_authorized_coach_name(first_name, last_name):
                coach_player = get_or_create_coach_player(first_name, last_name)
                if coach_player:
                    player = [coach_player]
                    is_coach_player_access = True

            if not player:
                flash("Nome non autorizzato. Inserisci nome e cognome di un calciatore presente nel database oppure di uno dei mister autorizzati.")
                return redirect(url_for("home"))

            session.clear()
            session["role"] = "player"
            session["player_id"] = player[0]["id"]
            session["player_name"] = f"{player[0]['last_name']} {player[0]['first_name']}"
            session["is_coach_player_access"] = 1 if is_coach_player_access else 0
            session["is_pres_player_access"] = 1 if is_pres_player_access else 0
            return redirect(url_for("player_home"))
        if mode == "coach":
            if request.form.get("password", "") != COACH_PASSWORD:
                flash("Password allenatore errata.")
                return redirect(url_for("home"))
            session.clear()
            session["role"] = "coach"
            return redirect(url_for("coach_panel"))
    content = """
    <div class="card"><h2>Accesso giocatore / mister</h2><form method="post"><input type="hidden" name="mode" value="player"><label>Nome</label><input name="first_name" required><label>Cognome</label><input name="last_name" required><button>Entra e vota</button><div class="small">Accesso consentito ai calciatori presenti nel database e ai mister autorizzati.</div></form></div>
    <div class="card"><h2>Accesso allenatore</h2><form method="post"><input type="hidden" name="mode" value="coach"><label>Password allenatore</label><input name="password" type="password" required><button class="btn-dark">Entra come allenatore</button></form></div>
    """
    return page("GS Spezzanese Mobile", "Accesso giocatori, mister e allenatore", content)


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

    coach_access_note = ""
    if session.get("is_coach_player_access"):
        coach_access_note = """
        <div class="card">
            <h2>Accesso mister</h2>
            <div class="small">Sei entrato nella zona calciatori come mister autorizzato. Puoi usare la sezione voti come votante mister.</div>
        </div>
        """

    pres_access_note = ""
    if session.get("is_pres_player_access"):
        pres_access_note = """
        <div class="card">
            <h2>Accesso presidente</h2>
            <div class="small">Benvenuto presidente. Puoi consultare la sezione voti e le figurine speciali.</div>
        </div>
        """

    # Il tasto "Storico prestazioni" è visibile solo ai calciatori normali,
    # non ai mister né al presidente (non hanno presenze in distinta).
    show_history = not session.get("is_coach_player_access") and not session.get("is_pres_player_access")
    history_btn = '<a class="btn btn-dark" href="/player/history">Storico prestazioni</a>' if show_history else ""

    content = pres_access_note + coach_access_note + f"""
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
        {history_btn}
        <a class="btn" style="background:linear-gradient(135deg,#b8860b,#f5c518);color:#0a0a0a;font-weight:900;" href="/awards">⚡ Figurine Premi</a>
        <a class="btn" href="/logout">Esci</a>
    </div>
    """

    return page("Area giocatore", f"Ciao {session.get('player_name')}", content)



def _build_vote_choices():
    choices = []
    for base in range(3, 11):
        if base > 3:
            choices.append((f"{base}-", base - 0.25))
        choices.append((str(base), float(base)))
        if base < 10:
            choices.append((f"{base}+", base + 0.25))
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

    if rating < 3 or rating > 10:
        return None

    return rating



@app.route("/player/card")
@login_required("player")
def player_card():
    player_id = session["player_id"]

    rows = db_query("""
        WITH app_stats AS (
            SELECT
                a.player_id,
                COUNT(*) AS presenze,
                COALESCE(SUM(a.goals), 0) AS gol,
                COALESCE(SUM(a.assists), 0) AS assist
            FROM appearances a
            JOIN matches m ON m.id = a.match_id
            WHERE a.player_id=?
            GROUP BY a.player_id
        ),
        vote_stats AS (
            SELECT
                v.voted_player_id AS player_id,
                COALESCE(ROUND(AVG(v.rating)::numeric, 2), 0) AS media_voto
            FROM player_votes v
            JOIN matches m ON m.id = v.match_id
            WHERE v.voted_player_id=?
            GROUP BY v.voted_player_id
        )
        SELECT
            p.id,
            p.first_name,
            p.last_name,
            COALESCE(p.role, '') AS role,
            COALESCE(p.photo_data, '') AS photo_data,
            COALESCE(p.photo_mime, 'image/jpeg') AS photo_mime,
            COALESCE(app.presenze, 0) AS presenze,
            COALESCE(app.gol, 0) AS gol,
            COALESCE(app.assist, 0) AS assist,
            COALESCE(vote.media_voto, 0) AS media_voto
        FROM players p
        LEFT JOIN app_stats app ON app.player_id = p.id
        LEFT JOIN vote_stats vote ON vote.player_id = p.id
        WHERE p.id=?
    """, (player_id, player_id, player_id), fetch=True)

    if not rows:
        flash("Giocatore non trovato.")
        return redirect(url_for("player_home"))

    p = rows[0]
    full_name = f"{p['last_name']} {p['first_name']}".strip()
    # Doppio ruolo: "CDC/ATT" → due pill separate
    roles = [r.strip() for r in (p['role'] or '').split('/') if r.strip()]
    roles_html = "".join(f"<span class='card-role'>{r}</span>" for r in roles) if roles else "<span class='card-role'>—</span>"

    if p["photo_data"]:
        photo_html = f"<img class='card-photo' src='data:{p['photo_mime']};base64,{p['photo_data']}' alt='Foto giocatore'>"
    else:
        photo_html = "<div class='card-placeholder'>👤</div>"

    content = f"""
    <div class="card card-preview">
        {photo_html}
        <div class="card-name">{full_name}</div>
        <div style="display:flex;gap:6px;justify-content:center;flex-wrap:wrap;margin-top:6px">{roles_html}</div>

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

    # Tutto in una CTE: partita + controllo voto già inserito + lista giocatori votabili
    ctx = db_query("""
        WITH match_data AS (
            SELECT id, match_date, opponent, competition, home_away,
                   COALESCE(result, '') AS result
            FROM matches WHERE id=?
        ),
        voted_check AS (
            SELECT COUNT(*) AS total
            FROM player_votes
            WHERE match_id=? AND voter_player_id=?
        ),
        votable AS (
            SELECT p.id, p.first_name, p.last_name, p.role,
                   COALESCE(a.minutes,0) AS minutes, 1 AS votable
            FROM appearances a
            JOIN players p ON p.id=a.player_id
            WHERE a.match_id=? AND COALESCE(a.minutes,0) > 10
            ORDER BY p.last_name, p.first_name
        ),
        fallback AS (
            SELECT p.id, p.first_name, p.last_name, p.role,
                   COALESCE(a.minutes,0) AS minutes,
                   CASE WHEN COALESCE(a.minutes,0) > 10 THEN 1 ELSE 0 END AS votable
            FROM appearances a
            JOIN players p ON p.id=a.player_id
            WHERE a.match_id=?
            ORDER BY COALESCE(a.starter,0) DESC, p.last_name, p.first_name
        )
        SELECT
            (SELECT id          FROM match_data) AS match_id,
            (SELECT match_date  FROM match_data) AS match_date,
            (SELECT opponent    FROM match_data) AS opponent,
            (SELECT competition FROM match_data) AS competition,
            (SELECT home_away   FROM match_data) AS home_away,
            (SELECT result      FROM match_data) AS result,
            (SELECT total       FROM voted_check) AS already_voted,
            (SELECT COUNT(*)    FROM votable) AS votable_count
    """, (match_id, match_id, voter_id, match_id, match_id), fetch=True)

    if not ctx or ctx[0]["match_id"] is None:
        flash("Partita non trovata.")
        return redirect(url_for("player_matches"))

    c = ctx[0]
    match = {k: c[k] for k in ("match_id","match_date","opponent","competition","home_away","result")}
    already_voted = c["already_voted"]

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

    # Singola query: prima tenta >10 min, se vuoto prende tutta la distinta
    if c["votable_count"] > 0:
        rows = db_query("""
            SELECT p.id, p.first_name, p.last_name, p.role,
                   COALESCE(a.minutes,0) AS minutes, 1 AS votable
            FROM appearances a
            JOIN players p ON p.id=a.player_id
            WHERE a.match_id=? AND COALESCE(a.minutes,0) > 10
            ORDER BY p.last_name, p.first_name
        """, (match_id,), fetch=True)
        showing_full_lineup_fallback = False
    else:
        rows = db_query("""
            SELECT p.id, p.first_name, p.last_name, p.role,
                   COALESCE(a.minutes,0) AS minutes,
                   CASE WHEN COALESCE(a.minutes,0) > 10 THEN 1 ELSE 0 END AS votable
            FROM appearances a
            JOIN players p ON p.id=a.player_id
            WHERE a.match_id=?
            ORDER BY COALESCE(a.starter,0) DESC, p.last_name, p.first_name
        """, (match_id,), fetch=True)
        showing_full_lineup_fallback = bool(rows)

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
                    <select name="rating_{row['id']}" {'disabled' if int(row.get('votable') or 0) != 1 else ''}>
                        {options}
                    </select>
                </div>
                {"<div class='small'>Non votabile: meno di 11 minuti.</div>" if int(row.get('votable') or 0) != 1 else ""}
            </div>
            """

    content = f"""
    <div class="card">
        <h2>Partita selezionata</h2>
        <div><b>{ui_date(match['match_date'])}</b> vs {match['opponent']}</div>
        <div class="small">{match['competition']} · {match['home_away']} · Risultato: {match['result'] or '-'}</div>
        <div class="small">Puoi votare solo i giocatori che hanno fatto più di 10 minuti.</div>
        {"<div class='flash'>La distinta è presente, ma nessun giocatore risulta sopra i 10 minuti: controllo minuti consigliato dal gestionale desktop.</div>" if showing_full_lineup_fallback else ""}
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
    <div class="card"><h2>Pannello allenatore</h2><div class="tabs"><a class="btn btn-blue" href="/coach/matches">Partite</a><a class="btn btn-green" href="/coach/formation">Formazione</a><a class="btn btn-dark" href="/coach/training">Allenamenti</a><a class="btn btn-blue" href="/coach/player-stats">Statistiche giocatori</a><a class="btn" href="/logout">Esci</a></div></div>
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
                a.player_id,
                SUM(CASE WHEN COALESCE(a.subentrato,0)=1 THEN 1 ELSE 0 END) AS subentrato
            FROM appearances a
            JOIN matches m ON m.id=a.match_id
            WHERE m.match_date BETWEEN ? AND ?
            GROUP BY a.player_id
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
            starter = 1 if request.form.get(f"starter_{pid}") else 0
            substitute = 1 if request.form.get(f"sub_{pid}") else 0

            # Sicurezza lato server: anche se il browser inviasse entrambe le spunte,
            # Titolare e Subentrato non possono essere veri insieme.
            if starter and substitute:
                substitute = 0
            captain = 1 if request.form.get(f"captain_{pid}") else 0
            vice_captain = 1 if request.form.get(f"vice_captain_{pid}") else 0

            try:
                minutes = int(request.form.get(f"minutes_{pid}") or 0)
                goals = int(request.form.get(f"goals_{pid}") or 0)
                assists = int(request.form.get(f"assists_{pid}") or 0)
            except ValueError:
                flash("Controlla minuti, gol e assist: devono essere numeri interi.")
                return redirect(url_for("coach_formation", match_id=match_id))

            yellow = 1 if request.form.get(f"yellow_{pid}") else 0
            red = 1 if request.form.get(f"red_{pid}") else 0

            if min(minutes, goals, assists) < 0:
                flash("Minuti, gol e assist non possono essere negativi.")
                return redirect(url_for("coach_formation", match_id=match_id))

            # Prima venivano salvati solo i giocatori con la spunta "Convocato".
            # Da mobile può capitare di compilare minuti/gol/assist/cartellini senza
            # attivare la spunta: in quel caso i dati venivano scartati e non
            # comparivano nelle statistiche giocatore. Ora il giocatore viene
            # incluso automaticamente se ha qualsiasi dato partita valorizzato.
            has_match_data = any([
                request.form.get(f"play_{pid}"),
                starter,
                substitute,
                minutes > 0,
                goals > 0,
                assists > 0,
                yellow,
                red,
                captain,
                vice_captain,
            ])
            if not has_match_data:
                continue

            appearance_rows.append((match_id, pid, starter, substitute, minutes, goals, assists, yellow, red, captain, vice_captain))

        captains      = [row[1] for row in appearance_rows if row[9]]
        vice_captains = [row[1] for row in appearance_rows if row[10]]

        # ── Vincoli formazione ────────────────────────────────────────────
        n_convocati  = len(appearance_rows)
        n_titolari   = sum(1 for row in appearance_rows if row[2] == 1)   # starter
        n_subentrati = sum(1 for row in appearance_rows if row[3] == 1)   # subentrato

        if n_convocati > 20:
            flash(f"I convocati sono {n_convocati}: il massimo consentito è 20.")
            return redirect(url_for("coach_formation", match_id=match_id))

        if n_titolari > 11:
            flash(f"I titolari sono {n_titolari}: il massimo consentito è 11.")
            return redirect(url_for("coach_formation", match_id=match_id))

        # Ogni titolare deve avere almeno 1 minuto
        titolari_senza_minuti = [
            row for row in appearance_rows if row[2] == 1 and row[4] < 1
        ]
        if titolari_senza_minuti:
            flash(f"{len(titolari_senza_minuti)} titolar{'e' if len(titolari_senza_minuti)==1 else 'i'} "
                  f"{'ha' if len(titolari_senza_minuti)==1 else 'hanno'} 0 minuti: "
                  f"ogni titolare deve avere almeno 1 minuto.")
            return redirect(url_for("coach_formation", match_id=match_id))

        if n_subentrati > 5:
            flash(f"I subentrati sono {n_subentrati}: il massimo consentito è 5.")
            return redirect(url_for("coach_formation", match_id=match_id))
        # ─────────────────────────────────────────────────────────────────
        if len(captains) > 1:
            flash("Puoi selezionare un solo capitano C.")
            return redirect(url_for("coach_formation", match_id=match_id))
        if len(vice_captains) > 1:
            flash("Puoi selezionare un solo vice capitano VC.")
            return redirect(url_for("coach_formation", match_id=match_id))
        if captains and vice_captains and captains[0] == vice_captains[0]:
            flash("Capitano C e vice VC devono essere due giocatori diversi.")
            return redirect(url_for("coach_formation", match_id=match_id))

        match_rows = db_query("SELECT home_away FROM matches WHERE id=?", (match_id,), fetch=True)
        home_away = match_rows[0]["home_away"] if match_rows else "Casa"
        expected_goals, result_error = parse_team_goals_from_result(result, home_away)
        if result_error:
            flash(result_error)
            return redirect(url_for("coach_formation", match_id=match_id))

        total_goals = sum(row[5] for row in appearance_rows)
        total_assists = sum(row[6] for row in appearance_rows)

        if total_goals != expected_goals:
            flash(f"La somma dei gol dei giocatori è {total_goals}, ma dal risultato i gol squadra sono {expected_goals}.")
            return redirect(url_for("coach_formation", match_id=match_id))

        # Gli assist non possono essere più dei gol segnati dalla squadra.
        # Non richiediamo assist == gol perché alcuni gol possono non avere assist.
        if total_assists > expected_goals:
            flash(f"La somma degli assist è {total_assists}, ma i gol squadra sono {expected_goals}. Gli assist non possono essere più dei gol segnati.")
            return redirect(url_for("coach_formation", match_id=match_id))

        db_transaction(
            statements=[
                ("UPDATE matches SET result=? WHERE id=?", (result, match_id)),
                ("DELETE FROM appearances WHERE match_id=?", (match_id,)),
            ],
            batches=[("""
                INSERT INTO appearances (match_id,player_id,starter,subentrato,minutes,goals,assists,yellow_cards,red_cards,captain,vice_captain)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, appearance_rows)],
        )
        flash("Formazione salvata.")
        return redirect(url_for("coach_formation", match_id=match_id))
    existing = {}
    selected_result = ""
    if selected_match_id:
        rows = db_query("""
            SELECT m.result,
                   a.player_id, a.starter, a.subentrato, a.minutes,
                   a.goals, a.assists, a.yellow_cards, a.red_cards,
                   a.captain, a.vice_captain
            FROM matches m
            LEFT JOIN appearances a ON a.match_id = m.id
            WHERE m.id=?
        """, (selected_match_id,), fetch=True)
        if rows:
            selected_result = rows[0]["result"] or ""
            existing = {r["player_id"]: r for r in rows if r["player_id"] is not None}
    match_options = "".join(f"<option value='{m['id']}' {'selected' if str(m['id']) == str(selected_match_id) else ''}>#{m['id']} · {ui_date(m['match_date'])} vs {m['opponent']}</option>" for m in matches)
    player_rows = ""
    for p in players:
        ex = existing.get(p["id"])
        player_rows += f"""
        <div class="player-row"><div class="player-title">{player_name(p)}</div><div class="small">{' / '.join(r.strip() for r in (p['role'] or '').split('/') if r.strip()) or '-'}</div>
        <div class="checks"><label><input type="checkbox" name="play_{p['id']}" data-player="{p['id']}" data-role="play" {'checked' if ex else ''}> Convocato</label><label><input class="exclusive-presence" type="checkbox" name="starter_{p['id']}" data-player="{p['id']}" data-role="starter" {'checked' if ex and ex['starter'] else ''}> Titolare</label><label><input class="exclusive-presence" type="checkbox" name="sub_{p['id']}" data-player="{p['id']}" data-role="sub" {'checked' if ex and int(ex.get('subentrato') or 0) else ''}> Subentrato</label><label><input type="checkbox" name="captain_{p['id']}" {'checked' if ex and ex.get('captain') else ''}> C</label><label><input type="checkbox" name="vice_captain_{p['id']}" {'checked' if ex and ex.get('vice_captain') else ''}> VC</label></div>
        <div class="inline"><div><label>Minuti</label><input type="number" min="0" max="130" name="minutes_{p['id']}" value="{ex['minutes'] if ex else 0}"></div><div><label>Gol</label><input type="number" min="0" name="goals_{p['id']}" value="{ex['goals'] if ex else 0}"></div></div>
        <div class="inline"><div><label>Assist</label><input type="number" min="0" name="assists_{p['id']}" value="{ex['assists'] if ex else 0}"></div><div><label>Cartellini</label><div class="checks"><label><input type="checkbox" name="yellow_{p['id']}" {'checked' if ex and ex['yellow_cards'] else ''}> Amm.</label><label><input type="checkbox" name="red_{p['id']}" {'checked' if ex and ex['red_cards'] else ''}> Esp.</label></div></div></div></div>
        """
    content = f"""
    <div class="card"><h2>Formazione partita</h2><form method="get"><label>Partita</label><select name="match_id" onchange="this.form.submit()">{match_options}</select></form></div>

    <!-- Contatori in tempo reale -->
    <div class="card" id="counters-card" style="padding:14px 18px;">
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;text-align:center;">
        <div>
          <div id="cnt-conv" style="font-size:26px;font-weight:900;color:var(--gold-light)">0</div>
          <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px">Convocati <span style="color:var(--muted)">/20</span></div>
        </div>
        <div>
          <div id="cnt-tit" style="font-size:26px;font-weight:900;color:var(--green-bright)">0</div>
          <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px">Titolari <span style="color:var(--muted)">/11</span></div>
        </div>
        <div>
          <div id="cnt-sub" style="font-size:26px;font-weight:900;color:var(--blue)">0</div>
          <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px">Subentrati <span style="color:var(--muted)">/5</span></div>
        </div>
      </div>
      <div id="limit-warn" style="display:none;margin-top:10px;padding:8px 12px;border-radius:10px;background:rgba(224,53,53,.15);color:#fca5a5;font-weight:700;font-size:13px;text-align:center;"></div>
    </div>

    <form method="post" id="formation-form"><input type="hidden" name="match_id" value="{selected_match_id or ''}"><div class="card"><label>Risultato</label><input name="result" placeholder="es. 2-1" value="{selected_result or ''}"></div><div class="card"><h2>Giocatori</h2>{player_rows or 'Nessun giocatore.'}<button type="submit" id="submit-btn">Salva formazione</button></div></form><a class="btn btn-blue" href="/coach">Indietro</a>
    <script>
    (function() {{
      var LIMITS = {{ conv: 20, tit: 11, sub: 5 }};

      function updateCounters() {{
        var conv = 0, tit = 0, sub = 0, titSenzaMinuti = 0;
        var warnings = [];

        document.querySelectorAll('input[name^="play_"]').forEach(function(cb) {{
          if (cb.checked) conv++;
        }});
        document.querySelectorAll('input[name^="starter_"]').forEach(function(cb) {{
          if (cb.checked) {{
            tit++;
            // controlla che il campo minuti di questo titolare sia >= 1
            var pid = cb.name.replace('starter_','');
            var mInput = document.querySelector('input[name="minutes_' + pid + '"]');
            if (mInput && (parseInt(mInput.value) || 0) < 1) titSenzaMinuti++;
          }}
        }});
        document.querySelectorAll('input[name^="sub_"]').forEach(function(cb) {{
          if (cb.checked) sub++;
        }});

        var cConv = document.getElementById('cnt-conv');
        var cTit  = document.getElementById('cnt-tit');
        var cSub  = document.getElementById('cnt-sub');
        cConv.textContent = conv;
        cTit.textContent  = tit;
        cSub.textContent  = sub;

        cConv.style.color = conv > LIMITS.conv ? '#e03535' : 'var(--gold-light)';
        cTit.style.color  = tit  > LIMITS.tit  ? '#e03535' : 'var(--green-bright)';
        cSub.style.color  = sub  > LIMITS.sub  ? '#e03535' : 'var(--blue)';

        if (conv > LIMITS.conv) warnings.push('Convocati: ' + conv + '/20 (max 20)');
        if (tit  > LIMITS.tit)  warnings.push('Titolari: '  + tit  + '/11 (max 11)');
        if (sub  > LIMITS.sub)  warnings.push('Subentrati: ' + sub + '/5 (max 5)');
        if (titSenzaMinuti > 0) warnings.push(titSenzaMinuti + ' titolar' + (titSenzaMinuti===1?'e':'i') + ' con 0 minuti (min. 1)');

        var warn = document.getElementById('limit-warn');
        var btn  = document.getElementById('submit-btn');
        if (warnings.length) {{
          warn.textContent = '⚠ ' + warnings.join('  ·  ');
          warn.style.display = 'block';
          btn.style.opacity = '.45';
          btn.style.cursor  = 'not-allowed';
          btn.setAttribute('data-blocked', '1');
        }} else {{
          warn.style.display = 'none';
          btn.style.opacity = '1';
          btn.style.cursor  = '';
          btn.removeAttribute('data-blocked');
        }}
      }}

      // Intercetta submit se ci sono errori
      document.getElementById('formation-form').addEventListener('submit', function(e) {{
        var btn = document.getElementById('submit-btn');
        if (btn.getAttribute('data-blocked')) {{
          e.preventDefault();
          document.getElementById('limit-warn').scrollIntoView({{behavior:'smooth',block:'center'}});
        }}
      }});

      // Ascolta tutti i checkbox e i campi numerici del form
      document.getElementById('formation-form').addEventListener('change', function(e) {{
        if (e.target.type === 'checkbox') updateCounters();
      }});
      document.getElementById('formation-form').addEventListener('input', function(e) {{
        if (e.target.type === 'number' && e.target.name && e.target.name.startsWith('minutes_')) updateCounters();
      }});

      updateCounters();
    }})();
    </script>
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
        <div class="player-row"><div class="player-title">{player_name(p)}</div><div class="small">{' / '.join(r.strip() for r in (p['role'] or '').split('/') if r.strip()) or '-'}</div>
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
