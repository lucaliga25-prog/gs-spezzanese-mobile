from pathlib import Path
import os
import base64
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from datetime import date
from functools import wraps

from flask import Flask, request, redirect, url_for, session, render_template_string, flash, get_flashed_messages

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL non trovata. Controlla il file .env.")

COACH_PASSWORD = "spezzanese2627"

app = Flask(__name__)
app.secret_key = "gestionale-gs-spezzanese-mobile-secret"


def db_query(query, params=(), fetch=False):
    """Esegue query PostgreSQL usando la stessa sintassi con ? del vecchio SQLite."""
    pg_query = query.replace("?", "%s")

    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(pg_query, params)
        rows = cur.fetchall() if fetch else None
        conn.commit()
        return rows
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()


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
                    rating INTEGER NOT NULL,
                    UNIQUE(match_id, voter_player_id, voted_player_id)
                )
            """)

            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS photo_data TEXT DEFAULT ''")
            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS photo_mime TEXT DEFAULT ''")

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
    ensure_mobile_tables()
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

        flash("Foto figurina salvata correttamente.")
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
        <a class="btn" href="/logout">Esci</a>
    </div>
    """

    return page("Area giocatore", f"Ciao {session.get('player_name')}", content)


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

        saved = 0

        for row in rows:
            voted_id = row["id"]
            raw = request.form.get(f"rating_{voted_id}", "")

            if raw == "":
                continue

            try:
                rating = int(raw)
            except ValueError:
                continue

            if rating < 1 or rating > 10:
                continue

            db_query("""
                INSERT INTO player_votes (match_id, voter_player_id, voted_player_id, rating)
                VALUES (?, ?, ?, ?)
            """, (match_id, voter_id, voted_id, rating))

            saved += 1

        if saved == 0:
            flash("Inserisci almeno un voto prima di salvare.")
            return redirect(url_for("player_votes", match_id=match_id))

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
            for i in range(1, 11):
                options += f"<option value='{i}'>{i}</option>"

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
    <div class="card"><h2>Pannello allenatore</h2><div class="tabs"><a class="btn btn-blue" href="/coach/matches">Partite</a><a class="btn btn-green" href="/coach/formation">Formazione</a><a class="btn btn-dark" href="/coach/training">Allenamenti</a><a class="btn" href="/logout">Esci</a></div></div>
    """
    return page("Allenatore", "Gestione rapida da telefono", content)


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
        db_query("UPDATE matches SET result=? WHERE id=?", (result, match_id))
        db_query("DELETE FROM appearances WHERE match_id=?", (match_id,))
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
            db_query("""
                INSERT INTO appearances (match_id,player_id,starter,minutes,goals,assists,yellow_cards,red_cards)
                VALUES (?,?,?,?,?,?,?,?)
            """, (match_id, pid, starter, minutes, goals, assists, yellow, red))
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
        db_query("DELETE FROM training_attendance WHERE session_id=?", (session_id,))
        for p in players:
            pid = p["id"]
            try:
                status_int = int(request.form.get(f"status_{pid}", "0"))
            except ValueError:
                status_int = 0
            db_query("INSERT INTO training_attendance (session_id,player_id,present) VALUES (?,?,?)", (session_id, pid, status_int))
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
    app.run(host="0.0.0.0", port=5000, debug=True)
