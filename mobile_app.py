import os
import base64
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor, execute_batch
from dotenv import load_dotenv
from datetime import date
from functools import wraps

from flask import Flask, request, redirect, url_for, session, flash, get_flashed_messages, Response
from datetime import timedelta

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL non trovata. Controlla il file .env.")

DB_POOL = SimpleConnectionPool(1, int(os.getenv("DB_POOL_MAX", "16")), dsn=DATABASE_URL)


# ── Configurazione squadra ─────────────────────────────────────────────────
# Modifica queste costanti per adattare l'app a una squadra diversa.
TEAM_NAME    = "GS Spezzanese"          # nome visualizzato nell'interfaccia
TEAM_SEASON  = "26/27"                  # stagione corrente
APP_PASSWORD = "spezzanese2627"         # password allenatore (web)
# ───────────────────────────────────────────────────────────────────────────

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


# Pre-calcolo set normalizzati: evita di ricreare il set ad ogni login.
_AUTHORIZED_COACH_NORMALIZED = frozenset(
    (_norm_name(f), _norm_name(l)) for f, l in AUTHORIZED_COACH_PLAYER_ACCESS
)
_AUTHORIZED_PRES_NORMALIZED = frozenset(
    (_norm_name(f), _norm_name(l)) for f, l in AUTHORIZED_PRES_ACCESS
)


def is_authorized_coach_name(first_name, last_name):
    return (_norm_name(first_name), _norm_name(last_name)) in _AUTHORIZED_COACH_NORMALIZED


def is_authorized_pres_name(first_name, last_name):
    return (_norm_name(first_name), _norm_name(last_name)) in _AUTHORIZED_PRES_NORMALIZED


def _get_or_create_special_player(first_name, last_name, role):
    """Recupera o crea un record tecnico (MISTER/PRES) nella tabella players."""
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
        VALUES (?, ?, '', ?)
    """, (first_name.strip().title(), last_name.strip().title(), role))

    rows = db_query("""
        SELECT id, first_name, last_name
        FROM players
        WHERE lower(trim(first_name))=lower(trim(?))
          AND lower(trim(last_name))=lower(trim(?))
        ORDER BY id DESC
        LIMIT 1
    """, (first_name, last_name), fetch=True)
    return rows[0] if rows else None


def get_or_create_coach_player(first_name, last_name):
    return _get_or_create_special_player(first_name, last_name, 'MISTER')


def get_or_create_pres_player(first_name, last_name):
    """Crea (se non esiste) un record tecnico con ruolo PRES per il presidente."""
    return _get_or_create_special_player(first_name, last_name, 'PRES')

app = Flask(__name__)
app.secret_key = "gestionale-gs-spezzanese-mobile-secret"

# Logo squadra codificato in base64 — servito inline e come PWA icon.
LOGO_B64 = "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"
LOGO_MIME = "image/jpeg"


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

# Cache semplice per le query award: si invalida ogni ora
# evita di rieseguire le query ad ogni page load sulla pagina /awards
_award_cache = {}
_AWARD_CACHE_TTL = 3600  # secondi

def _award_cached(key, fn):
    """Esegue fn() e memorizza il risultato per TTL secondi."""
    import time
    entry = _award_cache.get(key)
    if entry and time.time() - entry['ts'] < _AWARD_CACHE_TTL:
        return entry['val']
    val = fn()
    _award_cache[key] = {'val': val, 'ts': time.time()}
    return val


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
            COUNT(v.id) AS num_voti,
            COALESCE(SUM(a.goals),0)   AS stat_gol,
            COALESCE(SUM(a.assists),0) AS stat_assist,
            COALESCE(SUM(a.minutes),0) AS stat_minuti,
            COUNT(a.id)                AS stat_presenze
        FROM last_m lm
        JOIN player_votes v ON v.match_id = lm.match_id
        JOIN players p ON p.id = v.voted_player_id
        LEFT JOIN appearances a ON a.player_id = p.id
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
            COUNT(v.id) AS num_voti,
            COALESCE(SUM(CASE WHEN m.match_date BETWEEN ? AND ? THEN a.goals   ELSE 0 END),0) AS stat_gol,
            COALESCE(SUM(CASE WHEN m.match_date BETWEEN ? AND ? THEN a.assists ELSE 0 END),0) AS stat_assist,
            COALESCE(SUM(CASE WHEN m.match_date BETWEEN ? AND ? THEN a.minutes ELSE 0 END),0) AS stat_minuti,
            COUNT(DISTINCT CASE WHEN m.match_date BETWEEN ? AND ? THEN a.match_id END)        AS stat_presenze
        FROM player_votes v
        JOIN matches m ON m.id = v.match_id
        JOIN players p ON p.id = v.voted_player_id
        LEFT JOIN appearances a ON a.player_id = p.id
        WHERE m.match_date BETWEEN ? AND ?
        GROUP BY p.id, p.first_name, p.last_name, p.role, p.photo_data, p.photo_mime
        ORDER BY media_voto DESC, num_voti DESC
        LIMIT 1
    """, (last_month_start.isoformat(), last_month_end.isoformat(),
         last_month_start.isoformat(), last_month_end.isoformat(),
         last_month_start.isoformat(), last_month_end.isoformat(),
         last_month_start.isoformat(), last_month_end.isoformat(),
         last_month_start.isoformat(), last_month_end.isoformat()), fetch=True)

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
    logo_b64_local = LOGO_B64
    stat_presenze = p.get('stat_presenze', 0)
    stat_minuti   = p.get('stat_minuti', 0)
    stat_gol      = p.get('stat_gol', 0)
    stat_assist   = p.get('stat_assist', 0)
    # Foto giocatore centrale — pattern per cover centrato
    if p.get("photo_data"):
        _pd = p['photo_data']; _pm = p['photo_mime']
        player_img_svg = (
            f'<defs><pattern id="pp-motw" patternUnits="userSpaceOnUse"'
            f' x="98" y="163" width="104" height="104">'
            f'<image href="data:{_pm};base64,{_pd}"'
            f' x="0" y="0" width="104" height="104"'
            f' preserveAspectRatio="xMidYMid slice"/></pattern></defs>'
            f'<circle cx="150" cy="215" r="52" fill="url(#pp-motw)"/>'
        )
        player_ring_svg = '<circle cx="150" cy="215" r="53" fill="none" stroke="url(#brdg)" stroke-width="2.5"/>'
    else:
        player_img_svg = '<circle cx="150" cy="215" r="52" fill="#0a0906"/><text x="150" y="215" font-size="52" text-anchor="middle" dominant-baseline="middle">&#x1F464;</text>'
        player_ring_svg = ''

    try:
        stars = min(5, max(1, round((float(score) - 4) / 1.2)))
    except Exception:
        stars = 3
    stars_html = "★" * stars + "☆" * (5 - stars)

    return f"""    <div class="award-card-wrap">
    <svg viewBox="0 0 300 400" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <clipPath id="motw-clip"><path d="M20,32 Q20,14 38,14 L130,14 L150,2 L170,14 L262,14 Q280,14 280,32 L280,356 Q280,374 264,374 L198,374 L150,392 L102,374 L36,374 Q20,374 20,356 Z"/></clipPath>
      <clipPath id="logo-circle-motw"><circle cx="150" cy="49" r="33"/></clipPath>
      <linearGradient id="bg-motw" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#0a0906"/><stop offset="100%" stop-color="#080705"/></linearGradient>
      <linearGradient id="gm1" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#3a2a00" stop-opacity="0"/><stop offset="20%" stop-color="#a07818"/><stop offset="42%" stop-color="#e8c840"/><stop offset="55%" stop-color="#f5e060"/><stop offset="70%" stop-color="#d4a820"/><stop offset="85%" stop-color="#8a6010"/><stop offset="100%" stop-color="#3a2800" stop-opacity="0"/></linearGradient>
      <linearGradient id="gm2" x1="5%" y1="0%" x2="95%" y2="100%"><stop offset="0%" stop-color="#2a1e00" stop-opacity="0"/><stop offset="25%" stop-color="#b89020"/><stop offset="50%" stop-color="#ead848"/><stop offset="75%" stop-color="#a07010"/><stop offset="100%" stop-color="#2a1e00" stop-opacity="0"/></linearGradient>
      <linearGradient id="gm3" x1="0%" y1="10%" x2="100%" y2="90%"><stop offset="0%" stop-color="#1a1000" stop-opacity="0"/><stop offset="30%" stop-color="#c09828"/><stop offset="55%" stop-color="#f0d850"/><stop offset="80%" stop-color="#906808"/><stop offset="100%" stop-color="#1a1000" stop-opacity="0"/></linearGradient>
      <linearGradient id="brdg" x1="0%" y1="0%" x2="0%" y2="100%"><stop offset="0%" stop-color="#f0d860"/><stop offset="25%" stop-color="#c9a030"/><stop offset="50%" stop-color="#906800"/><stop offset="75%" stop-color="#c09020"/><stop offset="100%" stop-color="#e8c840"/></linearGradient>
      <radialGradient id="gspot-m" cx="52%" cy="42%" r="40%"><stop offset="0%" stop-color="#d4a800" stop-opacity="0.18"/><stop offset="100%" stop-color="#000" stop-opacity="0"/></radialGradient>
      <linearGradient id="divg" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#c9a030" stop-opacity="0"/><stop offset="20%" stop-color="#c9a030" stop-opacity="0.9"/><stop offset="80%" stop-color="#c9a030" stop-opacity="0.9"/><stop offset="100%" stop-color="#c9a030" stop-opacity="0"/></linearGradient>
      <linearGradient id="lrm" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#f0d860"/><stop offset="50%" stop-color="#c9a030"/><stop offset="100%" stop-color="#e8c840"/></linearGradient>
    </defs>
    <g clip-path="url(#motw-clip)">
      <rect width="300" height="400" fill="url(#bg-motw)"/>
      <g fill="none" opacity="0.35">
        <path d="M-10,290 Q60,260 130,275 Q200,290 270,258" stroke="#1c1800" stroke-width="10"/>
        <path d="M-10,310 Q70,278 145,295 Q215,310 285,278" stroke="#181400" stroke-width="8"/>
        <path d="M-10,330 Q55,298 125,312 Q195,326 265,295" stroke="#201c00" stroke-width="7"/>
        <path d="M-10,268 Q65,238 140,254 Q208,268 278,238" stroke="#161200" stroke-width="9"/>
        <path d="M20,248 Q80,220 155,235 Q220,248 280,220" stroke="#141000" stroke-width="6"/>
        <path d="M-10,350 Q60,318 130,333 Q200,348 268,316" stroke="#1a1600" stroke-width="6"/>
        <path d="M10,370 Q75,338 148,353 Q214,366 278,336" stroke="#181400" stroke-width="5"/>
        <path d="M-10,80 Q60,55 130,68 Q200,80 268,55" stroke="#141000" stroke-width="5" opacity="0.5"/>
        <path d="M-10,100 Q55,76 125,88 Q195,100 265,76" stroke="#121000" stroke-width="4" opacity="0.4"/>
      </g>
      <rect width="300" height="400" fill="url(#gspot-m)"/>
      <path d="M-30,172 C30,138 95,152 160,118 C218,88 256,58 316,32 L316,68 C262,92 226,122 168,154 C104,188 42,175 -30,210 Z" fill="url(#gm1)" opacity="0.94"/>
      <path d="M-30,210 C42,175 104,188 168,154 C226,122 262,92 316,68 L316,76 C260,100 224,132 166,164 C100,198 38,185 -30,220 Z" fill="#060504" opacity="0.96"/>
      <path d="M-30,220 C38,185 100,198 166,164 C224,132 260,100 316,76 L316,112 C266,134 232,166 172,200 C110,232 48,220 -30,256 Z" fill="url(#gm2)" opacity="0.88"/>
      <path d="M-30,256 C48,220 110,232 172,200 C232,166 266,134 316,112 L316,120 C264,142 230,175 170,208 C108,241 46,229 -30,265 Z" fill="#060504" opacity="0.94"/>
      <path d="M20,148 C80,118 138,130 194,100 C242,74 272,48 320,28 L320,42 C274,62 244,88 196,116 C140,146 82,134 20,164 Z" fill="url(#gm3)" opacity="0.72"/>
      <rect x="20" y="266" width="260" height="1.5" fill="url(#divg)"/>
    </g>
    <path d="M20,32 Q20,14 38,14 L130,14 L150,2 L170,14 L262,14 Q280,14 280,32 L280,356 Q280,374 264,374 L198,374 L150,392 L102,374 L36,374 Q20,374 20,356 Z" fill="none" stroke="url(#brdg)" stroke-width="3"/>
    <path d="M24,33 Q24,18 39,18 L131,18 L150,7 L169,18 L261,18 Q276,18 276,33 L276,355 Q276,370 262,370 L197,370 L150,388 L103,370 L38,370 Q24,370 24,355 Z" fill="none" stroke="#c9a030" stroke-width="0.8" opacity="0.3"/>
    <circle cx="150" cy="49" r="37" fill="url(#lrm)"/>
    <circle cx="150" cy="49" r="33" fill="#0a0906"/>
    <image href="data:image/jpeg;base64,{logo_b64_local}" x="117" y="16" width="66" height="66" clip-path="url(#logo-circle-motw)" preserveAspectRatio="xMidYMid slice"/>
    <circle cx="150" cy="49" r="37" fill="none" stroke="url(#lrm)" stroke-width="1.5"/>
    {player_img_svg}
    {player_ring_svg}
    <g clip-path="url(#motw-clip)">
      <text x="36" y="148" font-family="Arial Black,sans-serif" font-weight="900" font-size="46" fill="white">{score}</text>
      <text x="48" y="170" font-family="Arial,sans-serif" font-weight="700" font-size="14" fill="white" opacity="0.72" letter-spacing="1">{role_display}</text>
      <text x="150" y="300" font-family="Arial Black,sans-serif" font-weight="900" font-size="15" fill="white" text-anchor="middle" letter-spacing="2">{last} {first}</text>
      <text x="150" y="316" font-family="Arial,sans-serif" font-weight="700" font-size="10" fill="#c9a030" text-anchor="middle" letter-spacing="3">MAN OF THE MATCH</text>
      <line x1="30" y1="325" x2="270" y2="325" stroke="#c9a030" stroke-width="0.6" opacity="0.4"/>
      <text x="60"  y="338" font-size="9" fill="#c9a030" font-family="Arial" font-weight="600" text-anchor="middle" opacity="0.8">PRES</text>
      <text x="120" y="338" font-size="9" fill="#c9a030" font-family="Arial" font-weight="600" text-anchor="middle" opacity="0.8">MIN</text>
      <text x="180" y="338" font-size="9" fill="#c9a030" font-family="Arial" font-weight="600" text-anchor="middle" opacity="0.8">GOL</text>
      <text x="240" y="338" font-size="9" fill="#c9a030" font-family="Arial" font-weight="600" text-anchor="middle" opacity="0.8">ASS</text>
      <text x="60"  y="356" font-size="16" fill="white" font-family="Arial Black,sans-serif" font-weight="900" text-anchor="middle">{stat_presenze}</text>
      <text x="120" y="356" font-size="16" fill="white" font-family="Arial Black,sans-serif" font-weight="900" text-anchor="middle">{stat_minuti}</text>
      <text x="180" y="356" font-size="16" fill="white" font-family="Arial Black,sans-serif" font-weight="900" text-anchor="middle">{stat_gol}</text>
      <text x="240" y="356" font-size="16" fill="white" font-family="Arial Black,sans-serif" font-weight="900" text-anchor="middle">{stat_assist}</text>
    </g>
    </svg>
    </div>"""


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
    logo_b64_local = LOGO_B64
    # Foto giocatore centrale — pattern per cover centrato
    if p.get("photo_data"):
        _pd = p['photo_data']; _pm = p['photo_mime']
        player_img_svg = (
            f'<defs><pattern id="pp-potm" patternUnits="userSpaceOnUse"'
            f' x="98" y="156" width="104" height="104">'
            f'<image href="data:{_pm};base64,{_pd}"'
            f' x="0" y="0" width="104" height="104"'
            f' preserveAspectRatio="xMidYMid slice"/></pattern></defs>'
            f'<circle cx="150" cy="208" r="52" fill="url(#pp-potm)"/>'
        )
        player_ring_svg = '<circle cx="150" cy="208" r="53" fill="none" stroke="url(#brd-bl)" stroke-width="2.5"/>'
    else:
        player_img_svg = '<circle cx="150" cy="208" r="52" fill="#040c28"/><text x="150" y="208" font-size="52" text-anchor="middle" dominant-baseline="middle">&#x1F464;</text>'
        player_ring_svg = ''

    stat_presenze = p.get('stat_presenze', 0)
    stat_minuti   = p.get('stat_minuti', 0)
    stat_gol      = p.get('stat_gol', 0)
    stat_assist   = p.get('stat_assist', 0)
    try:
        stars = min(5, max(1, round((float(score) - 4) / 1.2)))
    except Exception:
        stars = 3
    stars_html = "★" * stars + "☆" * (5 - stars)

    return f"""    <div class="award-card-wrap">
    <svg viewBox="0 0 300 400" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <clipPath id="potm-clip"><path d="M22,28 Q22,12 38,12 L138,12 L150,0 L162,12 L262,12 Q278,12 278,28 L278,318 Q278,360 250,378 L150,400 L50,378 Q22,360 22,318 Z"/></clipPath>
      <clipPath id="logo-circle-potm"><circle cx="150" cy="47" r="33"/></clipPath>
      <linearGradient id="potm-bg" x1="10%" y1="0%" x2="90%" y2="100%"><stop offset="0%" stop-color="#1535d8"/><stop offset="40%" stop-color="#1a42e8"/><stop offset="70%" stop-color="#2252f5"/><stop offset="100%" stop-color="#1030c8"/></linearGradient>
      <linearGradient id="dark-ar" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#040c28"/><stop offset="100%" stop-color="#081638"/></linearGradient>
      <linearGradient id="brd-bl" x1="0%" y1="0%" x2="0%" y2="100%"><stop offset="0%" stop-color="#70b0ff"/><stop offset="30%" stop-color="#3878e8"/><stop offset="65%" stop-color="#1a50c0"/><stop offset="100%" stop-color="#50a0f8"/></linearGradient>
      <linearGradient id="div-b" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#00d8ff" stop-opacity="0.05"/><stop offset="20%" stop-color="#00d8ff" stop-opacity="0.85"/><stop offset="80%" stop-color="#00d8ff" stop-opacity="0.85"/><stop offset="100%" stop-color="#00d8ff" stop-opacity="0.05"/></linearGradient>
      <radialGradient id="pglow" cx="72%" cy="30%" r="50%"><stop offset="0%" stop-color="#5080ff" stop-opacity="0.5"/><stop offset="60%" stop-color="#2040c0" stop-opacity="0.12"/><stop offset="100%" stop-color="#1020a0" stop-opacity="0"/></radialGradient>
      <radialGradient id="rglow" cx="28%" cy="78%" r="52%"><stop offset="0%" stop-color="#cc0028" stop-opacity="0.38"/><stop offset="100%" stop-color="#800018" stop-opacity="0"/></radialGradient>
      <linearGradient id="racc" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#ee0030" stop-opacity="0.92"/><stop offset="100%" stop-color="#880018" stop-opacity="0.5"/></linearGradient>
      <linearGradient id="lrp" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#ee0030"/><stop offset="40%" stop-color="#1a42e8"/><stop offset="100%" stop-color="#00d8ff"/></linearGradient>
    </defs>
    <g clip-path="url(#potm-clip)">
      <rect width="300" height="400" fill="url(#potm-bg)"/>
      <rect width="300" height="400" fill="url(#pglow)"/>
      <rect width="300" height="400" fill="url(#rglow)"/>
      <path d="M22,12 L22,200 L185,200 L185,12 Z" fill="url(#dark-ar)" opacity="0.8"/>
      <path d="M108,12 L278,172 L278,214 L68,12 Z"   fill="#1840dc" opacity="0.92"/>
      <path d="M278,172 L278,214 L90,400 L52,400 Z"   fill="#1535cc" opacity="0.88"/>
      <line x1="108" y1="12"  x2="278" y2="172" stroke="#00e8cc" stroke-width="2.5" opacity="0.95"/>
      <line x1="68"  y1="12"  x2="278" y2="214" stroke="#00e8cc" stroke-width="2.5" opacity="0.95"/>
      <line x1="278" y1="172" x2="90"  y2="400" stroke="#00d8be" stroke-width="2"   opacity="0.75"/>
      <line x1="278" y1="214" x2="52"  y2="400" stroke="#00d8be" stroke-width="2"   opacity="0.75"/>
      <path d="M168,12 L278,118 L278,152 L138,12 Z"   fill="#1e4cf0" opacity="0.7"/>
      <path d="M278,118 L278,152 L162,330 L134,330 Z"  fill="#1840e4" opacity="0.65"/>
      <line x1="168" y1="12"  x2="278" y2="118" stroke="#00d4be" stroke-width="2"   opacity="0.85"/>
      <line x1="138" y1="12"  x2="278" y2="152" stroke="#00d4be" stroke-width="2"   opacity="0.85"/>
      <line x1="278" y1="118" x2="162" y2="330" stroke="#00c4b0" stroke-width="1.5" opacity="0.6"/>
      <line x1="278" y1="152" x2="134" y2="330" stroke="#00c4b0" stroke-width="1.5" opacity="0.6"/>
      <path d="M222,12 L278,66 L278,87 L200,12 Z"     fill="#2258f8" opacity="0.5"/>
      <path d="M278,66 L278,87 L204,278 L183,278 Z"    fill="#1e52f0" opacity="0.45"/>
      <line x1="222" y1="12" x2="278" y2="66"  stroke="#00c8b8" stroke-width="1.5" opacity="0.7"/>
      <line x1="200" y1="12" x2="278" y2="87"  stroke="#00c8b8" stroke-width="1.5" opacity="0.7"/>
      <path d="M255,12 L278,34 L278,12 Z"             fill="url(#racc)"/>
      <path d="M22,308 L22,382 L72,400 Z"              fill="url(#racc)" opacity="0.6"/>
      <path d="M278,292 L210,400 L248,400 Z"           fill="url(#racc)" opacity="0.65"/>
      <path d="M22,258 L82,400 L60,400 L22,280 Z"      fill="#cc0028" opacity="0.28"/>
      <line x1="255" y1="12" x2="278" y2="34" stroke="#ff1840" stroke-width="2" opacity="0.8"/>
      <rect x="22" y="268" width="256" height="1.5" fill="url(#div-b)" rx="1"/>
    </g>
    <path d="M22,28 Q22,12 38,12 L138,12 L150,0 L162,12 L262,12 Q278,12 278,28 L278,318 Q278,360 250,378 L150,400 L50,378 Q22,360 22,318 Z" fill="none" stroke="url(#brd-bl)" stroke-width="3"/>
    <path d="M26,29 Q26,16 39,16 L139,16 L150,5 L161,16 L261,16 Q274,16 274,29 L274,317 Q274,357 247,374 L150,396 L53,374 Q26,357 26,317 Z" fill="none" stroke="#3070d8" stroke-width="1" opacity="0.45"/>
    <circle cx="150" cy="47" r="36" fill="url(#lrp)"/>
    <circle cx="150" cy="47" r="32" fill="#040c28"/>
    <image href="data:image/jpeg;base64,{logo_b64_local}" x="117" y="14" width="66" height="66" clip-path="url(#logo-circle-potm)" preserveAspectRatio="xMidYMid slice"/>
    <circle cx="150" cy="47" r="36" fill="none" stroke="url(#lrp)" stroke-width="1.5"/>
    {player_img_svg}
    {player_ring_svg}
    <g clip-path="url(#potm-clip)">
      <text x="36" y="152" font-family="Arial Black,sans-serif" font-weight="900" font-size="46" fill="white">{score}</text>
      <text x="48" y="174" font-family="Arial,sans-serif" font-weight="700" font-size="14" fill="white" opacity="0.75" letter-spacing="1">{role_display}</text>
      <text x="150" y="298" font-family="Arial Black,sans-serif" font-weight="900" font-size="15" fill="white" text-anchor="middle" letter-spacing="2">{last} {first}</text>
      <text x="150" y="316" font-family="Arial,sans-serif" font-weight="700" font-size="10" fill="#00d8ff" text-anchor="middle" letter-spacing="3">POTM · {month_label}</text>
      <line x1="30" y1="328" x2="270" y2="328" stroke="#00d8ff" stroke-width="0.6" opacity="0.4"/>
      <text x="60"  y="340" font-size="9" fill="#00d8ff" font-family="Arial" font-weight="600" text-anchor="middle" opacity="0.85">PRES</text>
      <text x="120" y="340" font-size="9" fill="#00d8ff" font-family="Arial" font-weight="600" text-anchor="middle" opacity="0.85">MIN</text>
      <text x="180" y="340" font-size="9" fill="#00d8ff" font-family="Arial" font-weight="600" text-anchor="middle" opacity="0.85">GOL</text>
      <text x="240" y="340" font-size="9" fill="#00d8ff" font-family="Arial" font-weight="600" text-anchor="middle" opacity="0.85">ASS</text>
      <text x="60"  y="360" font-size="17" fill="white" font-family="Arial Black,sans-serif" font-weight="900" text-anchor="middle">{stat_presenze}</text>
      <text x="120" y="360" font-size="17" fill="white" font-family="Arial Black,sans-serif" font-weight="900" text-anchor="middle">{stat_minuti}</text>
      <text x="180" y="360" font-size="17" fill="white" font-family="Arial Black,sans-serif" font-weight="900" text-anchor="middle">{stat_gol}</text>
      <text x="240" y="360" font-size="17" fill="white" font-family="Arial Black,sans-serif" font-weight="900" text-anchor="middle">{stat_assist}</text>
    </g>
    </svg>
    </div>"""


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
.header-inner{
  display:flex;align-items:center;gap:14px;position:relative;
}
.header-logo{
  width:60px;height:60px;object-fit:cover;
  border-radius:50%;
  border:2px solid var(--gold-dark);
  box-shadow:0 0 16px rgba(201,168,76,.35),0 4px 12px rgba(0,0,0,.5);
  flex-shrink:0;
}
.header-text{flex:1;min-width:0;}
.header-text h1{margin:0;font-size:22px;font-weight:900;color:var(--gold-light);
  text-shadow:0 0 20px rgba(201,168,76,.4);}
.header-text p{margin:4px 0 0;font-size:13px;color:var(--white-muted);}

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


.award-card-wrap{display:flex;flex-direction:column;align-items:center;margin:0 auto 18px;max-width:300px;}
.award-card-wrap svg{width:100%;max-width:280px;height:auto;}
</style>
"""


def page(title, subtitle, content):
    flashes = "".join(f"<div class='flash'>{m}</div>" for m in get_flashed_messages())
    return f"""
    <!doctype html><html><head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="theme-color" content="#c9a84c">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="{TEAM_NAME}">
    <link rel="apple-touch-icon" href="/logo.jpg">
    <link rel="manifest" href="/manifest.json">
    <link rel="icon" href="/favicon.ico" type="image/jpeg">
    <title>{title} · {TEAM_NAME}</title>
    {BASE_STYLE}</head>
    <body>
    <div class="header">
      <div class="header-inner">
        <img src="data:{LOGO_MIME};base64,{LOGO_B64}" class="header-logo" alt="GS Spezzanese">
        <div class="header-text">
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </div>
      </div>
    </div>
    <div class="container">{flashes}{content}<div class="footer-space"></div></div>
    </body></html>
    """



@app.route("/logo.jpg")
def serve_logo():
    """Serve il logo come file JPEG (usato da manifest PWA e tag <img>)."""
    import base64 as _b64
    return Response(_b64.b64decode(LOGO_B64), mimetype=LOGO_MIME)


@app.route("/favicon.ico")
def serve_favicon():
    """Favicon per browser desktop."""
    import base64 as _b64
    return Response(_b64.b64decode(LOGO_B64), mimetype=LOGO_MIME)


@app.route("/manifest.json")
def serve_manifest():
    """Web App Manifest per PWA (icona schermata Home su iOS/Android)."""
    import json
    manifest = {
        "name": TEAM_NAME,
        "short_name": TEAM_NAME,
        "description": f"Gestionale {TEAM_NAME} - voti e statistiche squadra",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0a1f0e",
        "theme_color": "#c9a84c",
        "orientation": "portrait",
        "icons": [
            {"src": "/logo.jpg", "sizes": "192x192", "type": "image/jpeg", "purpose": "any maskable"},
            {"src": "/logo.jpg", "sizes": "512x512", "type": "image/jpeg", "purpose": "any maskable"}
        ]
    }
    return Response(
        json.dumps(manifest),
        mimetype="application/manifest+json",
        headers={"Cache-Control": "public, max-age=86400"}
    )


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
            if request.form.get("password", "") != APP_PASSWORD:
                flash("Password allenatore errata.")
                return redirect(url_for("home"))
            session.clear()
            session["role"] = "coach"
            return redirect(url_for("coach_panel"))
    content = """
    <div class="card"><h2>Accesso giocatore / mister</h2><form method="post"><input type="hidden" name="mode" value="player"><label>Nome</label><input name="first_name" required><label>Cognome</label><input name="last_name" required><button>Entra e vota</button><div class="small">Accesso consentito ai calciatori presenti nel database e ai mister autorizzati.</div></form></div>
    <div class="card"><h2>Accesso allenatore</h2><form method="post"><input type="hidden" name="mode" value="coach"><label>Password allenatore</label><input name="password" type="password" required><button class="btn-dark">Entra come allenatore</button></form></div>
    """
    return page(f"{TEAM_NAME} Mobile", "Accesso giocatori, mister e allenatore", content)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/awards")
@login_required()
def awards():
    """Figurine speciali: miglior giocatore dell'ultima partita e del mese scorso."""
    week_player  = _award_cached("week",  get_best_player_last_match)
    month_player = _award_cached("month", get_best_player_last_month)

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
            for label, value in VOTE_CHOICES:
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

        WHERE LOWER(TRIM(COALESCE(p.role, ''))) NOT IN ('mister', 'pres')
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
