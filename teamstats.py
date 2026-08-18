import csv
import base64
import os
import sys
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import execute_batch
from dotenv import load_dotenv
from datetime import date, datetime
from pathlib import Path
from io import BytesIO
import calendar
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import customtkinter as ctk

import matplotlib
import matplotlib.pyplot as plt
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.backends.backend_pdf import PdfPages

try:
    from PIL import Image, ImageGrab, ImageOps
except Exception:
    Image = None
    ImageGrab = None
    ImageOps = None


# ── Configurazione squadra ─────────────────────────────────────────────────
# Modifica queste costanti per adattare l'app a una squadra diversa.
TEAM_NAME    = "GS Spezzanese"          # nome visualizzato nell'interfaccia
TEAM_SEASON  = "26/27"                  # stagione corrente
APP_PASSWORD = "spezzanese2627"         # password di avvio applicazione
# ───────────────────────────────────────────────────────────────────────────

APP_NAME = f"Gestionale {TEAM_NAME} {TEAM_SEASON} - Gestionale Squadra"
APP_BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = Path.cwd()


def resource_path(name):
    """Trova file inclusi accanto al .py oppure dentro/accanto all'eseguibile PyInstaller."""
    candidates = []

    # Cartella del file python
    candidates.append(APP_BASE_DIR / name)

    # Cartella corrente
    candidates.append(PROJECT_DIR / name)

    # Cartella dove sta l'eseguibile .exe
    try:
        candidates.append(Path(sys.executable).resolve().parent / name)
    except Exception:
        pass

    # Cartella temporanea PyInstaller _MEIPASS
    try:
        candidates.append(Path(sys._MEIPASS) / name)
    except Exception:
        pass

    # Vecchia cartella usata durante lo sviluppo
    candidates.append(Path.home() / "TeamStatsGestionale" / name)

    for p in candidates:
        if p.exists():
            return p

    return None
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL non trovata. Controlla il file .env nella cartella FootballStats.")

DB_POOL = SimpleConnectionPool(1, 8, dsn=DATABASE_URL)


COLORS = {
    "bg":           "#0a1f0e",   # verde campo scuro — sfondo principale
    "sidebar":      "#071510",   # quasi nero verdastro — sidebar
    "card":         "#162b19",   # verde carta — pannelli
    "surface":      "#1c3520",   # verde superficie — widget interni
    "border":       "#2a4a2e",   # bordo scuro
    "border_light": "#3a5e3e",   # bordo hover
    "text":         "#e8f0e8",   # bianco verdino — testo principale
    "muted":        "#7a9a7e",   # verde grigio — testo secondario
    "gold":         "#c9a84c",   # oro principale
    "gold_light":   "#f0c040",   # oro brillante
    "gold_dark":    "#a07828",   # oro scuro
    "green":        "#1a7a2e",   # verde accento azioni positive
    "green_bright": "#22c55e",   # verde brillante
    "red":          "#e03535",   # rosso errori/cancellazioni
    "red_dark":     "#a01818",   # rosso scuro hover
    "blue":         "#1a6fd4",   # blu azioni info
    # alias per compatibilità con codice esistente
    "blue_dark":    "#0e4a9e",
}

ROLES = ["POR", "DC", "TD", "TS", "CC", "COC", "CDC", "AS", "AD", "ATT"]


def db_query(query, params=(), fetch=False):
    """Query PostgreSQL veloce con connection pool. Mantiene compatibilità con i placeholder ?."""
    pg_query = query.replace("?", "%s")
    conn = DB_POOL.getconn()
    cur = None

    try:
        cur = conn.cursor()
        cur.execute(pg_query, params)
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


def db_batch(query, rows, page_size=200):
    """Esegue un INSERT/UPDATE batch con execute_batch in una sola transazione."""
    if not rows:
        return
    pg_query = query.replace("?", "%s")
    conn = DB_POOL.getconn()
    cur = None
    try:
        cur = conn.cursor()
        execute_batch(cur, pg_query, rows, page_size=page_size)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        DB_POOL.putconn(conn)


def ensure_db():
    """Crea/aggiorna lo schema PostgreSQL online — usa il pool di connessioni."""
    conn = DB_POOL.getconn()
    try:
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

            # Aggiunge colonne eventualmente mancanti
            cur.execute("ALTER TABLE appearances ADD COLUMN IF NOT EXISTS captain INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE appearances ADD COLUMN IF NOT EXISTS vice_captain INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS photo_data TEXT DEFAULT ''")
            cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS photo_mime TEXT DEFAULT ''")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_training_sessions_date ON training_sessions(training_date)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_training_attendance_session ON training_attendance(session_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_training_attendance_player ON training_attendance(player_id)")
            # Indici critici per le query più frequenti (formazione, statistiche, voti)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_appearances_match_id ON appearances(match_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_appearances_player_id ON appearances(player_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(match_date)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_player_votes_voted ON player_votes(voted_player_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_substitutions_match_id ON substitutions(match_id)")
            cur.execute("ALTER TABLE player_votes ALTER COLUMN rating TYPE NUMERIC(4,2) USING rating::numeric")

            # Crea sequence/default per permettere INSERT senza id anche se le tabelle sono state migrate con id INTEGER.
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
    except Exception:
        conn.rollback()
        raise
    finally:
        DB_POOL.putconn(conn)


def compact_table_ids(table, related_updates=None):
    """Ricompone gli ID di una tabella PostgreSQL e aggiorna le tabelle collegate.
    Usa UPDATE...FROM batch invece di un loop Python riga per riga."""
    if related_updates is None:
        related_updates = []

    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {table} ORDER BY id")
            ids = [r[0] for r in cur.fetchall()]

            if not ids:
                return

            mapping = [(new_id, old_id) for new_id, old_id in enumerate(ids, start=1)]

            cur.execute("CREATE TEMP TABLE id_map (new_id INTEGER NOT NULL, old_id INTEGER PRIMARY KEY) ON COMMIT DROP")
            execute_batch(cur, "INSERT INTO id_map (new_id, old_id) VALUES (%s, %s)", mapping)

            # Sposta temporaneamente gli ID per evitare conflitti.
            cur.execute(f"UPDATE {table} SET id = -id")

            # Un solo UPDATE batch invece del loop Python
            cur.execute(f"""
                UPDATE {table} SET id = id_map.new_id
                FROM id_map WHERE {table}.id = -id_map.old_id
            """)

            for related_table, related_col in related_updates:
                cur.execute(f"""
                    UPDATE {related_table}
                    SET {related_col} = id_map.new_id
                    FROM id_map
                    WHERE {related_table}.{related_col} = id_map.old_id
                """)

            seq = f"{table}_id_seq"
            cur.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq}")
            if len(ids) > 0:
                cur.execute("SELECT setval(%s, %s, true)", (seq, len(ids)))
            else:
                cur.execute("SELECT setval(%s, 1, false)", (seq,))
            cur.execute(f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT nextval('{seq}')")
        conn.commit()


def compact_training_ids():
    compact_table_ids("training_sessions", [("training_attendance", "session_id")])


def compact_match_ids():
    """Ricompone gli ID delle partite dopo eliminazione e aggiorna appearances, substitutions e player_votes.
    Usa UPDATE...FROM con una tabella temporanea invece di un loop Python riga per riga."""
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM matches ORDER BY id")
            ids = [row[0] for row in cur.fetchall()]

            if not ids:
                cur.execute("SELECT setval('matches_id_seq', 1, false)")
                conn.commit()
                return

            # Costruisce la mappa old→new in una tabella temporanea e usa UPDATE...FROM batch
            mapping = [(new_id, old_id) for new_id, old_id in enumerate(ids, start=1)]

            # Disabilita temporaneamente i vincoli FK durante la ricompattazione.
            cur.execute("ALTER TABLE appearances DROP CONSTRAINT IF EXISTS appearances_match_id_fkey")
            cur.execute("ALTER TABLE substitutions DROP CONSTRAINT IF EXISTS substitutions_match_id_fkey")
            cur.execute("ALTER TABLE player_votes DROP CONSTRAINT IF EXISTS player_votes_match_id_fkey")

            # Tabella temporanea con la mappa di rimappatura
            cur.execute("CREATE TEMP TABLE match_id_map (new_id INTEGER NOT NULL, old_id INTEGER PRIMARY KEY) ON COMMIT DROP")
            execute_batch(cur, "INSERT INTO match_id_map (new_id, old_id) VALUES (%s, %s)", mapping)

            # Porta tutto in area negativa per evitare conflitti con chiavi primarie esistenti.
            cur.execute("UPDATE matches SET id = -id")
            cur.execute("UPDATE appearances SET match_id = -match_id")
            cur.execute("UPDATE substitutions SET match_id = -match_id")
            cur.execute("UPDATE player_votes SET match_id = -match_id")

            # Rimappatura in un solo UPDATE per tabella invece di N UPDATE in loop
            cur.execute("""
                UPDATE matches SET id = m.new_id
                FROM match_id_map m WHERE matches.id = -m.old_id
            """)
            cur.execute("""
                UPDATE appearances SET match_id = m.new_id
                FROM match_id_map m WHERE appearances.match_id = -m.old_id
            """)
            cur.execute("""
                UPDATE substitutions SET match_id = m.new_id
                FROM match_id_map m WHERE substitutions.match_id = -m.old_id
            """)
            cur.execute("""
                UPDATE player_votes SET match_id = m.new_id
                FROM match_id_map m WHERE player_votes.match_id = -m.old_id
            """)

            cur.execute("""
                ALTER TABLE appearances
                ADD CONSTRAINT appearances_match_id_fkey
                FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE
            """)
            cur.execute("""
                ALTER TABLE substitutions
                ADD CONSTRAINT substitutions_match_id_fkey
                FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE
            """)
            cur.execute("""
                ALTER TABLE player_votes
                ADD CONSTRAINT player_votes_match_id_fkey
                FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE
            """)

            cur.execute("CREATE SEQUENCE IF NOT EXISTS matches_id_seq")
            cur.execute("SELECT setval('matches_id_seq', %s, true)", (len(ids),))
            cur.execute("ALTER TABLE matches ALTER COLUMN id SET DEFAULT nextval('matches_id_seq')")

        conn.commit()


def compact_player_ids():
    compact_table_ids(
        "players",
        [
            ("appearances", "player_id"),
            ("training_attendance", "player_id"),
            ("substitutions", "player_in_id"),
            ("substitutions", "player_out_id"),
            ("player_votes", "voter_player_id"),
            ("player_votes", "voted_player_id"),
        ],
    )


def ui_to_db_date(date_str):
    try:
        return datetime.strptime(date_str, "%d-%m-%y").strftime("%Y-%m-%d")
    except Exception:
        return date_str

def db_to_ui_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%m-%y")
    except Exception:
        return date_str

def default_dates():
    today = date.today()
    return date(today.year, 1, 1), today



class LoginWindow(ctk.CTkToplevel):
    """Finestra modale di login mostrata all'avvio prima dell'app principale."""

    def __init__(self, on_success):
        super().__init__()
        self.on_success = on_success
        self.title(f"{TEAM_NAME} — Accesso")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])

        # Centra la finestra (440×320) sullo schermo
        self.after(1, self._center)

        # Blocca il focus su questa finestra finché non viene chiusa
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── Contenuto ────────────────────────────────────────────────────────
        wrapper = ctk.CTkFrame(self, fg_color=COLORS["card"],
                               corner_radius=18, border_width=1,
                               border_color=COLORS["gold_dark"])
        wrapper.pack(padx=30, pady=30, fill="both", expand=True)

        # Logo / titolo
        ctk.CTkLabel(
            wrapper, text=f"⚽  {TEAM_NAME}",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=COLORS["gold_light"]
        ).pack(pady=(22, 4))

        ctk.CTkLabel(
            wrapper, text="Inserisci la password per accedere",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["muted"]
        ).pack(pady=(0, 20))

        # Campo password
        self.pwd_var = tk.StringVar()
        self.pwd_entry = ctk.CTkEntry(
            wrapper, textvariable=self.pwd_var,
            show="●", width=280, height=44,
            font=ctk.CTkFont(size=15),
            fg_color=COLORS["surface"],
            border_color=COLORS["border_light"],
            text_color=COLORS["text"],
            placeholder_text="Password"
        )
        self.pwd_entry.pack(pady=(0, 6))
        self.pwd_entry.bind("<Return>", lambda _e: self._check())
        self.pwd_entry.focus()

        # Messaggio errore (nascosto di default)
        self.error_label = ctk.CTkLabel(
            wrapper, text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["red"]
        )
        self.error_label.pack(pady=(0, 8))

        # Pulsante accesso
        ctk.CTkButton(
            wrapper, text="Accedi", width=280, height=44,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=COLORS["gold_dark"],
            hover_color=COLORS["gold"],
            text_color="#0a1f0e",
            command=self._check
        ).pack(pady=(0, 22))

    def _center(self):
        self.update_idletasks()
        w, h = 440, 320
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _check(self):
        if self.pwd_var.get() == APP_PASSWORD:
            self.grab_release()
            self.destroy()
            self.on_success()
        else:
            self.error_label.configure(text="Password errata. Riprova.")
            self.pwd_var.set("")
            self.pwd_entry.focus()
            # Breve shake visivo: sposta la finestra di ±6px orizzontali
            x = self.winfo_x()
            y = self.winfo_y()
            for dx in (6, -12, 12, -12, 6, 0):
                self.after(30, lambda _dx=dx: self.geometry(f"+{x+_dx}+{y}"))
                import time; time.sleep(0.03)

    def _on_close(self):
        """Chiude anche l'app principale se l'utente chiude il login."""
        self.grab_release()
        self.master.destroy()


class teamstats(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.title(APP_NAME)
        self.geometry("1550x920")
        self.minsize(1260, 760)
        self.configure(fg_color=COLORS["bg"])

        self.current_page = "Partite"
        self.nav_buttons = {}
        self.selected_match_id = None
        self.selected_player_id = None
        self.selected_appearance_id = None
        self.selected_training_id = None
        self.formation_player_vars = []
        self.formation_minutes_vars = []
        self.formation_player_menus = []
        self.sub_in_vars = []
        self.sub_out_vars = []
        self.sub_in_menus = []
        self.sub_out_menus = []
        self.formation_goals_vars = []
        self.formation_assists_vars = []
        self.formation_yellow_vars = []
        self.formation_red_vars = []
        self.formation_player_menus = []
        self.captain_var = tk.StringVar(value="")
        self.vice_captain_var = tk.StringVar(value="")
        self.module_var = tk.StringVar(value="4-3-1-2")
        # Cache lista giocatori — invalidata ad ogni add/update/delete
        self._player_options_cache = None

        # Inizializza lo stile TTK una sola volta (evita riconfigurazioni ad ogni create_tree)
        self._init_tree_style()

        self.build_shell()
        self.show_dashboard()

    def _init_tree_style(self):
        """Configura lo stile TTK Treeview una sola volta per tutta l'applicazione."""
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background=COLORS["surface"],
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
            rowheight=36,
            borderwidth=0,
            font=("Segoe UI", 10)
        )
        style.configure(
            "Treeview.Heading",
            background=COLORS["sidebar"],
            foreground=COLORS["gold_light"],
            font=("Segoe UI", 10, "bold"),
            relief="flat"
        )
        style.map(
            "Treeview",
            background=[("selected", COLORS["gold_dark"])],
            foreground=[("selected", "#0a1f0e")]
        )
        # Stile dedicato per la tabella marcatori — righe più alte e font leggermente più grande
        style.configure(
            "Scorer.Treeview",
            background=COLORS["surface"],
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
            rowheight=44,
            borderwidth=0,
            font=("Segoe UI", 11)
        )
        style.configure(
            "Scorer.Treeview.Heading",
            background=COLORS["sidebar"],
            foreground=COLORS["gold_light"],
            font=("Segoe UI", 11, "bold"),
            relief="flat"
        )
        style.map(
            "Scorer.Treeview",
            background=[("selected", COLORS["gold_dark"])],
            foreground=[("selected", "#0a1f0e")]
        )

    def build_shell(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=255, fg_color=COLORS["sidebar"], corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)

        self.main = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(0, weight=1)

        self.build_sidebar()

    def build_sidebar(self):
        # Linea decorativa oro in cima alla sidebar
        ctk.CTkFrame(
            self.sidebar, height=3, fg_color=COLORS["gold_dark"], corner_radius=0
        ).pack(fill="x")

        ctk.CTkLabel(
            self.sidebar, text=f"⚽  {TEAM_NAME}", text_color=COLORS["gold_light"],
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", padx=22, pady=(20, 2))

        ctk.CTkLabel(
            self.sidebar, text="Gestionale tecnico 26/27", text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11)
        ).pack(anchor="w", padx=22, pady=(0, 16))

        # Separatore
        ctk.CTkFrame(self.sidebar, height=1, fg_color=COLORS["border"]).pack(fill="x", padx=18, pady=(0, 12))

        logo_path = None
        for name in ["logo.png", "logo.jpg", "logo.jpeg", "icon.png", "icon.jpg", "icon.ico"]:
            logo_path = resource_path(name)
            if logo_path:
                break

        if logo_path and Image:
            try:
                logo_img = Image.open(logo_path)
                if logo_path.suffix.lower() == ".ico":
                    try:
                        logo_img.seek(0)
                    except Exception:
                        pass
                logo_img = logo_img.convert("RGBA")
                self.sidebar_logo = ctk.CTkImage(light_image=logo_img, dark_image=logo_img, size=(94, 94))
                self.sidebar_logo_label = ctk.CTkLabel(self.sidebar, text="", image=self.sidebar_logo)
                self.sidebar_logo_label.pack(pady=(0, 12))
            except Exception:
                self.sidebar_logo_label = ctk.CTkLabel(
                    self.sidebar, text="⚽", font=ctk.CTkFont(size=50), text_color=COLORS["gold_light"]
                )
                self.sidebar_logo_label.pack(pady=(0, 8))
        else:
            self.sidebar_logo_label = ctk.CTkLabel(
                self.sidebar, text="⚽", font=ctk.CTkFont(size=50), text_color=COLORS["gold_light"]
            )
            self.sidebar_logo_label.pack(pady=(0, 8))

        ctk.CTkButton(
            self.sidebar, text="＋   Nuovo giocatore", anchor="w", height=46,
            corner_radius=12, fg_color=COLORS["gold_dark"], hover_color=COLORS["gold"],
            text_color="#0a1f0e", font=ctk.CTkFont(size=14, weight="bold"),
            command=self.open_player_panel
        ).pack(fill="x", padx=18, pady=(4, 14))

        ctk.CTkFrame(self.sidebar, height=1, fg_color=COLORS["border"]).pack(fill="x", padx=18, pady=(0, 8))

        self.nav_button("🏠", "Dashboard", self.show_dashboard)
        self.nav_button("📋", "Partite", self.show_matches)
        self.nav_button("🟩", "Formazione", self.show_formation)
        self.nav_button("👥", "Giocatori", self.show_players)
        self.nav_button("🃏", "Figurine", self.show_player_cards)
        self.nav_button("⚽", "Bonus", self.show_goals_assists)
        self.nav_button("🟨", "Cartellini", self.show_cards)
        self.nav_button("⏱", "Minuti", self.show_minutes)
        self.nav_button("🏃", "Allenamenti", self.show_training)
        self.nav_button("📊", "Statistiche +", self.show_advanced_stats)
        self.nav_button("📤", "Export CSV", self.export_current_csv)
        self.nav_button("📄", "PDF stagione", self.export_season_pdf)

        ctk.CTkFrame(self.sidebar, height=1, fg_color=COLORS["border"]).pack(side="bottom", fill="x", padx=18, pady=(0, 8))
        ctk.CTkLabel(
            self.sidebar, text="🟢  Database online\nSupabase PostgreSQL", text_color=COLORS["muted"],
            font=ctk.CTkFont(size=10), wraplength=205, justify="center"
        ).pack(side="bottom", padx=18, pady=(0, 12))

    def nav_button(self, icon, text, command):
        btn = ctk.CTkButton(
            self.sidebar, text=f"{icon}   {text}", anchor="w", height=42,
            corner_radius=10, fg_color=COLORS["sidebar"], hover_color=COLORS["surface"],
            text_color=COLORS["text"], font=ctk.CTkFont(size=13, weight="bold"),
            command=command
        )
        btn.pack(fill="x", padx=18, pady=3)
        self.nav_buttons[text] = btn

    def set_active_nav(self, page):
        self.current_page = page
        for name, btn in self.nav_buttons.items():
            if name == page:
                btn.configure(fg_color=COLORS["gold_dark"], text_color="#0a1f0e")
            else:
                btn.configure(fg_color=COLORS["sidebar"], text_color=COLORS["text"])

    def clear_main(self):
        for child in self.main.winfo_children():
            child.destroy()

    def page_container(self):
        frame = ctk.CTkScrollableFrame(self.main, fg_color=COLORS["bg"], corner_radius=0)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)
        return frame

    def header(self, parent, title, subtitle):
        head = ctk.CTkFrame(parent, fg_color=COLORS["bg"], corner_radius=0)
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 18))
        ctk.CTkLabel(
            head, text=title, text_color=COLORS["gold_light"],
            font=ctk.CTkFont(size=28, weight="bold")
        ).pack(anchor="w")
        ctk.CTkLabel(
            head, text=subtitle, text_color=COLORS["muted"],
            font=ctk.CTkFont(size=13)
        ).pack(anchor="w", pady=(3, 0))
        ctk.CTkFrame(head, height=2, fg_color=COLORS["gold_dark"], corner_radius=1).pack(
            anchor="w", fill="x", pady=(10, 0)
        )

    def card(self, parent, **grid_options):
        frame = ctk.CTkFrame(
            parent, fg_color=COLORS["card"], corner_radius=18,
            border_width=1, border_color=COLORS["border"]
        )
        frame.grid(**grid_options)
        return frame

    def form_field(self, parent, label, widget):
        ctk.CTkLabel(
            parent, text=label.upper(), text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11, weight="bold")
        ).pack(anchor="w", padx=18, pady=(8, 3))
        widget.pack(fill="x", padx=18, pady=(0, 4))

    def date_picker_row(self, parent, variable, date_format="%d-%m-%y"):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkEntry(frame, textvariable=variable, height=38, width=130,
                     fg_color=COLORS["surface"], border_color=COLORS["border_light"],
                     text_color=COLORS["text"]).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            frame, text="📅", width=44, height=38,
            fg_color=COLORS["surface"], hover_color=COLORS["gold_dark"],
            text_color=COLORS["gold_light"],
            command=lambda: self.open_calendar(variable, date_format)
        ).pack(side="left", padx=(6, 0))
        return frame

    def open_calendar(self, variable, date_format="%d-%m-%y"):
        try:
            selected = datetime.strptime(variable.get().strip(), date_format).date()
        except ValueError:
            selected = date.today()

        popup = ctk.CTkToplevel(self)
        popup.title("Scegli data")
        popup.geometry("320x320")
        popup.resizable(False, False)
        popup.configure(fg_color="#f8fafc")
        popup.grab_set()

        year = tk.IntVar(value=selected.year)
        month = tk.IntVar(value=selected.month)

        head = ctk.CTkFrame(popup, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=12)
        body = ctk.CTkFrame(popup, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        def prev_month():
            if month.get() == 1:
                month.set(12)
                year.set(year.get() - 1)
            else:
                month.set(month.get() - 1)
            render()

        def next_month():
            if month.get() == 12:
                month.set(1)
                year.set(year.get() + 1)
            else:
                month.set(month.get() + 1)
            render()

        def pick(day_num):
            variable.set(date(year.get(), month.get(), day_num).strftime(date_format))
            popup.destroy()

        def render():
            for w in head.winfo_children():
                w.destroy()
            for w in body.winfo_children():
                w.destroy()

            ctk.CTkButton(
                head, text="‹", width=42, fg_color=COLORS["red"], hover_color=COLORS["red_dark"],
                text_color="#ffffff", command=prev_month
            ).pack(side="left")
            ctk.CTkLabel(
                head, text=f"{calendar.month_name[month.get()].capitalize()} {year.get()}",
                text_color="#111827",
                font=ctk.CTkFont(size=15, weight="bold")
            ).pack(side="left", expand=True)
            ctk.CTkButton(
                head, text="›", width=42, fg_color=COLORS["gold_dark"], hover_color=COLORS["gold"],
                text_color="#ffffff", command=next_month
            ).pack(side="right")

            for col, name in enumerate(["L", "M", "M", "G", "V", "S", "D"]):
                ctk.CTkLabel(
                    body, text=name, text_color="#374151",
                    font=ctk.CTkFont(size=12, weight="bold")
                ).grid(row=0, column=col, padx=3, pady=3)

            cal = calendar.Calendar(firstweekday=0)
            for row, week in enumerate(cal.monthdayscalendar(year.get(), month.get()), start=1):
                for col, day_num in enumerate(week):
                    if day_num == 0:
                        ctk.CTkLabel(body, text="", width=36).grid(row=row, column=col, padx=3, pady=3)
                    else:
                        active = day_num == selected.day and month.get() == selected.month and year.get() == selected.year
                        ctk.CTkButton(
                            body, text=str(day_num), width=36, height=30,
                            fg_color=COLORS["red"] if active else "#e2e8f0",
                            text_color="#ffffff" if active else "#111827",
                            hover_color=COLORS["border_light"], command=lambda d=day_num: pick(d)
                        ).grid(row=row, column=col, padx=3, pady=3)

        render()

    def create_searchable_combo(self, parent, variable, values, width=210, command=None, bg=COLORS["surface"], fg="#111827"):
        entry = tk.Entry(
            parent,
            textvariable=variable,
            width=max(10, int(width / 9)),
            font=("Segoe UI", 9),
            bg=bg,
            fg=fg,
            insertbackground=fg,
            relief="solid",
            bd=1,
        )

        entry._original_values = list(values)
        entry._suggestion_popup = None
        entry._suggestion_listbox = None
        entry._command = command

        def close_popup():
            popup = getattr(entry, "_suggestion_popup", None)
            if popup is not None:
                try:
                    popup.destroy()
                except Exception:
                    pass
            entry._suggestion_popup = None
            entry._suggestion_listbox = None

        def get_filtered():
            typed = variable.get().lower().strip()
            all_values = getattr(entry, "_original_values", [])

            if not typed:
                return all_values[:12]

            return [
                value for value in all_values
                if typed in value.lower()
            ][:12]

        def choose_value(value):
            if not value or value == "Nessun risultato":
                return

            variable.set(value)
            close_popup()
            entry.focus_set()
            entry.icursor("end")

            cmd = getattr(entry, "_command", None)
            if cmd:
                cmd(value)

        def show_popup(filtered):
            close_popup()

            if not filtered:
                filtered = ["Nessun risultato"]

            try:
                x = entry.winfo_rootx()
                y = entry.winfo_rooty() + entry.winfo_height()
                w = max(entry.winfo_width(), width)
            except Exception:
                return

            popup = tk.Toplevel(entry)
            popup.overrideredirect(True)
            popup.attributes("-topmost", True)
            popup.geometry(f"{w}x{min(180, 24 * len(filtered) + 4)}+{x}+{y}")

            listbox = tk.Listbox(
                popup,
                height=min(8, len(filtered)),
                font=("Segoe UI", 9),
                activestyle="none",
                exportselection=False,
            )
            listbox.pack(fill="both", expand=True)

            for item in filtered:
                listbox.insert("end", item)

            if filtered and filtered[0] != "Nessun risultato":
                listbox.selection_set(0)
                listbox.activate(0)

            def on_click(_event=None):
                sel = listbox.curselection()
                if sel:
                    choose_value(listbox.get(sel[0]))

            listbox.bind("<ButtonRelease-1>", on_click)

            entry._suggestion_popup = popup
            entry._suggestion_listbox = listbox

        def refresh_suggestions(event=None):
            if event and event.keysym in ["Return", "Escape", "Tab", "Up", "Down"]:
                return

            filtered = get_filtered()

            # Mostra suggerimenti senza spostare il focus dall'entry
            show_popup(filtered)
            entry.focus_set()
            entry.icursor("end")

        def confirm_selection(event=None):
            listbox = getattr(entry, "_suggestion_listbox", None)

            if listbox is not None:
                sel = listbox.curselection()
                if sel:
                    choose_value(listbox.get(sel[0]))
                    return "break"

                filtered = get_filtered()
                if filtered:
                    choose_value(filtered[0])
                    return "break"

            current = variable.get()
            if current:
                cmd = getattr(entry, "_command", None)
                if cmd:
                    cmd(current)

            close_popup()
            return "break"

        def escape_popup(event=None):
            close_popup()
            return "break"

        def move_selection(delta):
            listbox = getattr(entry, "_suggestion_listbox", None)
            if listbox is None:
                filtered = get_filtered()
                show_popup(filtered)
                return "break"

            size = listbox.size()
            if size == 0:
                return "break"

            sel = listbox.curselection()
            current = sel[0] if sel else 0
            new_index = max(0, min(size - 1, current + delta))
            listbox.selection_clear(0, "end")
            listbox.selection_set(new_index)
            listbox.activate(new_index)
            listbox.see(new_index)
            entry.focus_set()
            return "break"

        entry.bind("<KeyRelease>", refresh_suggestions)
        entry.bind("<Return>", confirm_selection)
        entry.bind("<Escape>", escape_popup)
        entry.bind("<Down>", lambda event: move_selection(1))
        entry.bind("<Up>", lambda event: move_selection(-1))
        entry.bind("<FocusOut>", lambda event: entry.after(150, close_popup))

        return entry

    def set_combo_values(self, combo, values):
        try:
            combo._original_values = list(values)
        except Exception:
            pass

        try:
            combo.configure(values=values)
        except Exception:
            pass

    def safe_tree_insert(self, tree, values):
        """Inserisce una riga adattandola automaticamente al numero di colonne della tabella."""
        try:
            cols = list(tree["columns"])
            expected = len(cols)
            vals = list(values)

            if len(vals) > expected:
                vals = vals[:expected]
            elif len(vals) < expected:
                vals.extend([""] * (expected - len(vals)))

            tree.insert("", "end", values=vals)
        except Exception:
            tree.insert("", "end", values=values)

    def create_tree(self, parent, columns, headers, widths, style_name="Treeview"):
        # Lo stile è già inizializzato in _init_tree_style() — nessuna ri-configurazione necessaria

        tree_frame = tk.Frame(parent, bg=COLORS["surface"])
        tree_frame.pack(fill="both", expand=True, padx=14, pady=14)

        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", style=style_name)

        y_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        x_scroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)

        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")

        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        for col in columns:
            tree.heading(col, text=headers[col])
            tree.column(col, width=widths.get(col, 120), anchor="center", stretch=True)

        # Tag per righe alternate
        tree.tag_configure("odd",  background="#1c3520", foreground=COLORS["text"])
        tree.tag_configure("even", background="#162b19", foreground=COLORS["text"])

        return tree


    # ---------------- DASHBOARD ----------------

    def show_dashboard(self):
        self.set_active_nav("Dashboard")
        self.clear_main()
        page = self.page_container()
        self.header(page, "Dashboard", "Panoramica moderna stile match center con squadra, forma e statistiche principali.")

        metrics = ctk.CTkFrame(page, fg_color=COLORS["bg"])
        metrics.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 14))
        for i in range(7):
            metrics.grid_columnconfigure(i, weight=1)

        # Una sola query per tutte le metriche — inclusi gol/partita per il calcolo Python
        dash = db_query("""
            SELECT
                (SELECT COUNT(*) FROM matches)           AS total_matches,
                (SELECT COUNT(*) FROM players WHERE LOWER(TRIM(COALESCE(role, ''))) NOT IN ('mister', 'pres')) AS total_players,
                (SELECT COUNT(*) FROM training_sessions) AS trainings
        """, fetch=True)[0]

        total_matches, total_players, trainings = dash

        rows_results = db_query("""
            SELECT home_away, COALESCE(result, ''), COALESCE(competition, '')
            FROM matches
            WHERE result IS NOT NULL AND result != ''
        """, fetch=True)

        goals_for = 0
        goals_against = 0
        clean_sheets = 0
        championship_points = 0

        for home_away, result, competition in rows_results:
            left, right = self.safe_int_result(result)
            if left is None:
                continue
            team_goals = left if home_away == "Casa" else right
            opp_goals = right if home_away == "Casa" else left
            goals_for += team_goals
            goals_against += opp_goals
            if opp_goals == 0:
                clean_sheets += 1

            if str(competition or "").strip().lower() == "campionato":
                if team_goals > opp_goals:
                    championship_points += 3
                elif team_goals == opp_goals:
                    championship_points += 1

        self.dashboard_card(metrics, 0, "Partite",      total_matches,        "📋", COLORS["gold_light"])
        self.dashboard_card(metrics, 1, "Punti",        championship_points, "🏆", COLORS["gold"])
        self.dashboard_card(metrics, 2, "Allenamenti",  trainings,            "🏃", COLORS["green_bright"])
        self.dashboard_card(metrics, 3, "Giocatori",    total_players,        "👥", COLORS["gold"])
        self.dashboard_card(metrics, 4, "Gol fatti",    goals_for,            "⚽", COLORS["green_bright"])
        self.dashboard_card(metrics, 5, "Gol subiti",   goals_against,        "🥅", COLORS["red"])
        self.dashboard_card(metrics, 6, "Clean sheet",  clean_sheets,         "🧤", COLORS["gold_light"])

        body = ctk.CTkFrame(page, fg_color=COLORS["bg"])
        body.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 24))
        body.grid_columnconfigure(0, weight=2)
        body.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(body, fg_color=COLORS["bg"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.grid_columnconfigure(0, weight=1)

        right = ctk.CTkFrame(body, fg_color=COLORS["bg"])
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right.grid_columnconfigure(0, weight=1)

        # Fetch una sola volta — condiviso tra grafico gol e form ultime 5
        recent_results = self._fetch_recent_results(8)

        # ── Colonna sinistra: grafico andamento (in cima) + ultime partite (sotto) ──
        chart_card = self.card(left, row=0, column=0, sticky="nsew", padx=0, pady=(0, 14))
        self.draw_dashboard_goals_chart(chart_card, preloaded_rows=recent_results[:6],
                                        goals_for=goals_for, goals_against=goals_against)

        recent_card = self.card(left, row=1, column=0, sticky="nsew", padx=0, pady=(0, 14))
        ctk.CTkLabel(
            recent_card,
            text="Ultime partite",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", padx=18, pady=(16, 8))

        recent_cols = ("date", "opponent", "competition", "home_away", "result")
        recent_headers = {"date": "Data", "opponent": "Avversario", "competition": "Competizione", "home_away": "Casa/Fuori", "result": "Risultato"}
        recent_widths = {"date": 100, "opponent": 230, "competition": 140, "home_away": 100, "result": 90}
        recent_tree = self.create_tree(recent_card, recent_cols, recent_headers, recent_widths)
        recent_rows = db_query("""
            SELECT match_date, opponent, competition, home_away, COALESCE(result, '')
            FROM matches
            ORDER BY match_date DESC, id DESC
            LIMIT 8
        """, fetch=True)
        for row in recent_rows:
            row = list(row)
            row[0] = db_to_ui_date(row[0])
            recent_tree.insert("", "end", values=row)

        # ── Colonna destra: form ultime 5 + top voti + report ──
        form_card = self.card(right, row=0, column=0, sticky="nsew", padx=0, pady=(0, 14))
        self.draw_last5_form(form_card, preloaded_rows=recent_results[:5])

        top5_rating_card = self.card(right, row=1, column=0, sticky="ew", padx=0, pady=(0, 14))
        self.draw_top5_ratings_card(top5_rating_card)

        report_card = self.card(right, row=2, column=0, sticky="ew", padx=0, pady=0)
        ctk.CTkLabel(
            report_card,
            text="Report",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            report_card,
            text="Crea un PDF con statistiche giocatore per giocatore filtrate per periodo.",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12),
            wraplength=330
        ).pack(anchor="w", padx=18, pady=(0, 12))

        start_default, end_default = default_dates()
        self.pdf_start_var = tk.StringVar(value=start_default.strftime("%d-%m-%y"))
        self.pdf_end_var = tk.StringVar(value=end_default.strftime("%d-%m-%y"))

        pdf_dates = ctk.CTkFrame(report_card, fg_color=COLORS["card"])
        pdf_dates.pack(fill="x", padx=18, pady=(0, 12))
        pdf_dates.grid_columnconfigure(0, weight=1)
        pdf_dates.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            pdf_dates,
            text="Dal",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=(0, 6), pady=(0, 3))

        ctk.CTkLabel(
            pdf_dates,
            text="Al",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11, weight="bold")
        ).grid(row=0, column=1, sticky="w", padx=(6, 0), pady=(0, 3))

        self.date_picker_row(pdf_dates, self.pdf_start_var).grid(row=1, column=0, sticky="ew", padx=(0, 6))
        self.date_picker_row(pdf_dates, self.pdf_end_var).grid(row=1, column=1, sticky="ew", padx=(6, 0))

        ctk.CTkButton(
            report_card,
            text="Genera PDF statistiche periodo",
            fg_color=COLORS["red"],
            hover_color=COLORS["gold"],
            command=self.export_players_pdf
        ).pack(fill="x", padx=18, pady=(0, 14))

        ctk.CTkLabel(
            report_card,
            text="PDF singolo giocatore",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", padx=18, pady=(0, 6))

        self.pdf_player_var = tk.StringVar(value="")
        self.pdf_player_combo = self.create_searchable_combo(
            report_card,
            self.pdf_player_var,
            self.player_options(),
            width=310,
            bg=COLORS["surface"],
            fg=COLORS["text"]
        )
        self.pdf_player_combo.pack(fill="x", padx=18, pady=(0, 10))

        player_pdf_buttons = ctk.CTkFrame(report_card, fg_color=COLORS["card"])
        player_pdf_buttons.pack(fill="x", padx=18, pady=(0, 18))

        ctk.CTkButton(
            player_pdf_buttons,
            text="PDF giocatore periodo",
            fg_color=COLORS["gold_dark"],
            command=lambda: self.export_single_player_pdf(period=True)
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            player_pdf_buttons,
            text="PDF giocatore stagione",
            fg_color=COLORS["red"],
            hover_color=COLORS["gold"],
            command=lambda: self.export_single_player_pdf(period=False)
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

    def draw_top5_ratings_card(self, parent):
        for child in parent.winfo_children():
            child.destroy()

        ctk.CTkLabel(
            parent,
            text="Top 5 media voto",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", padx=18, pady=(16, 8))

        rows = db_query("""
            SELECT
                trim(p.last_name || ' ' || p.first_name) AS player_name,
                COALESCE(ROUND(AVG(v.rating)::numeric, 2), 0) AS media_voto,
                COUNT(v.id) AS voti
            FROM players p
            JOIN player_votes v ON v.voted_player_id=p.id
            WHERE LOWER(TRIM(COALESCE(p.role, ''))) NOT IN ('mister', 'pres')
            GROUP BY p.id, p.last_name, p.first_name
            HAVING COUNT(v.id) > 0
            ORDER BY AVG(v.rating) DESC, COUNT(v.id) DESC, p.last_name, p.first_name
            LIMIT 5
        """, fetch=True)

        if not rows:
            ctk.CTkLabel(
                parent,
                text="Nessun voto inserito.",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=13, weight="bold")
            ).pack(anchor="w", padx=18, pady=(0, 18))
            return

        container = ctk.CTkFrame(parent, fg_color=COLORS["card"])
        container.pack(fill="x", padx=14, pady=(0, 14))

        for idx, (player_name, media_voto, voti) in enumerate(rows, start=1):
            row = ctk.CTkFrame(
                container,
                fg_color="#f8fafc" if idx % 2 else "#eef2ff",
                corner_radius=12
            )
            row.pack(fill="x", pady=4)

            ctk.CTkLabel(
                row,
                text=f"{idx}",
                text_color="#ffffff",
                fg_color=COLORS["red"] if idx == 1 else COLORS["blue"],
                corner_radius=10,
                width=30,
                height=30,
                font=ctk.CTkFont(size=13, weight="bold")
            ).pack(side="left", padx=(10, 8), pady=8)

            ctk.CTkLabel(
                row,
                text=player_name,
                text_color="#111827",
                font=ctk.CTkFont(size=13, weight="bold"),
                anchor="w"
            ).pack(side="left", fill="x", expand=True, padx=(0, 8))

            ctk.CTkLabel(
                row,
                text=f"{media_voto}",
                text_color=COLORS["red"],
                font=ctk.CTkFont(size=17, weight="bold")
            ).pack(side="right", padx=(6, 10))

            ctk.CTkLabel(
                row,
                text=f"{voti} voti",
                text_color="#374151",
                font=ctk.CTkFont(size=10, weight="bold")
            ).pack(side="right", padx=(0, 4))

    def dashboard_card(self, parent, col, title, value, icon, color):
        card = ctk.CTkFrame(
            parent, fg_color=COLORS["card"], corner_radius=18,
            border_width=1, border_color=COLORS["border"]
        )
        card.grid(row=0, column=col, sticky="ew", padx=6, pady=0)
        # Barra colorata in cima alla card
        ctk.CTkFrame(card, height=4, fg_color=color, corner_radius=2).pack(fill="x", pady=(0, 0))
        ctk.CTkLabel(card, text=icon, text_color=color, font=ctk.CTkFont(size=26)).pack(anchor="w", padx=16, pady=(12, 0))
        ctk.CTkLabel(card, text=str(value), text_color=color,
                     font=ctk.CTkFont(size=30, weight="bold")).pack(anchor="w", padx=16, pady=(2, 0))
        ctk.CTkLabel(card, text=title.upper(), text_color=COLORS["muted"],
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=16, pady=(2, 14))

    def _fetch_recent_results(self, n=8):
        """Ritorna le ultime n partite con risultato — risultato cachato per tutta la sessione di disegno."""
        return db_query("""
            SELECT match_date, opponent, home_away, COALESCE(result, '')
            FROM matches
            WHERE result IS NOT NULL AND result != ''
            ORDER BY match_date DESC, id DESC
            LIMIT ?
        """, (n,), fetch=True)

    def draw_dashboard_goals_chart(self, parent, preloaded_rows=None, goals_for=None, goals_against=None):
        for child in parent.winfo_children():
            child.destroy()

        # ── Header con titolo a sinistra e differenza reti a destra ──
        header_row = ctk.CTkFrame(parent, fg_color="transparent")
        header_row.pack(fill="x", padx=18, pady=(16, 4))

        ctk.CTkLabel(
            header_row,
            text="Gol per partita",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w"
        ).pack(side="left")

        if goals_for is not None and goals_against is not None:
            diff = goals_for - goals_against
            diff_str = f"+{diff}" if diff > 0 else str(diff)
            diff_color = COLORS["green_bright"] if diff > 0 else (COLORS["red"] if diff < 0 else COLORS["muted"])
            badge = ctk.CTkFrame(header_row, fg_color=COLORS["surface"], corner_radius=10)
            badge.pack(side="right")
            ctk.CTkLabel(
                badge,
                text="DR",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=10, weight="bold")
            ).pack(side="left", padx=(8, 2), pady=4)
            ctk.CTkLabel(
                badge,
                text=diff_str,
                text_color=diff_color,
                font=ctk.CTkFont(size=14, weight="bold")
            ).pack(side="left", padx=(0, 8), pady=4)

        rows = preloaded_rows if preloaded_rows is not None else self._fetch_recent_results(6)
        parsed_rows = []
        for match_date, opponent, home_away, result in rows:
            left, right = self.safe_int_result(result)
            if left is None:
                continue
            team_goals = left if home_away == "Casa" else right
            opp_goals  = right if home_away == "Casa" else left
            parsed_rows.append((match_date, opponent, team_goals, opp_goals))
        rows = list(reversed(parsed_rows))

        BG     = "#162b19"
        AX_BG  = "#1c3520"
        GOLD   = "#c9a84c"
        GOLD_L = "#f0c040"
        RED    = "#e03535"
        RED_L  = "#ff6b6b"
        TICK   = "#b0bfb4"
        GRID   = "#2a4a2e"

        fig = Figure(figsize=(4.4, 3.2), dpi=100, facecolor=BG)
        ax = fig.add_subplot(111)
        ax.set_facecolor(AX_BG)
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID)
        ax.tick_params(colors=TICK, labelsize=8)
        ax.yaxis.label.set_color(TICK)

        if rows:
            x      = range(len(rows))
            labels = [f"{r[0][5:]} {r[1][:8]}" for r in rows]
            gf     = [int(r[2] or 0) for r in rows]
            ga     = [int(r[3] or 0) for r in rows]

            # Gol fatti — oro
            ax.plot(x, gf, linewidth=2.5, color=GOLD,
                    marker="o", markersize=6, markerfacecolor=GOLD_L,
                    label="Gol fatti", zorder=3)
            ax.fill_between(x, gf, alpha=0.13, color=GOLD)

            # Gol subiti — rosso
            ax.plot(x, ga, linewidth=2.0, color=RED,
                    marker="s", markersize=5, markerfacecolor=RED_L,
                    label="Gol subiti", linestyle="--", zorder=2)
            ax.fill_between(x, ga, alpha=0.08, color=RED)

            ax.set_xticks(list(x))
            ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=7, color=TICK)
            ax.grid(axis="y", alpha=0.2, color=GRID)
            ax.set_ylabel("Gol", color=TICK, fontsize=9)
            ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))

            legend = ax.legend(
                fontsize=8, facecolor="#1c3520", edgecolor=GRID,
                labelcolor=TICK, loc="upper left", framealpha=0.8
            )
        else:
            ax.text(0.5, 0.5, "Nessun dato", ha="center", va="center", color="#7a9a7e")
            ax.set_axis_off()

        fig.tight_layout(pad=2)
        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.get_tk_widget().configure(bg=BG, highlightthickness=0)
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=(0, 12))
        canvas.draw()


    def draw_last5_form(self, parent, preloaded_rows=None):
        for child in parent.winfo_children():
            child.destroy()

        ctk.CTkLabel(
            parent,
            text="Rendimento ultime 5 partite",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", padx=18, pady=(16, 10))

        rows = (preloaded_rows[:5] if preloaded_rows is not None else self._fetch_recent_results(5))

        rows = list(reversed(rows))

        container = ctk.CTkFrame(parent, fg_color="transparent")
        container.pack(fill="x", padx=14, pady=(0, 14))

        for idx, (match_date, opponent, home_away, result) in enumerate(rows):
            outcome = "N"

            try:
                left = int(result.split("-")[0].strip())
                right = int(result.split("-")[1].strip())

                team_goals = left if home_away == "Casa" else right
                opp_goals = right if home_away == "Casa" else left

                if team_goals > opp_goals:
                    outcome = "V"
                    color = "#16a34a"
                elif team_goals < opp_goals:
                    outcome = "S"
                    color = "#dc2626"
                else:
                    outcome = "N"
                    color = "#ca8a04"
            except Exception:
                color = COLORS["muted"]

            box = ctk.CTkFrame(container, fg_color=color, corner_radius=12, width=76, height=110)
            box.pack(side="left", padx=5, pady=2)
            box.pack_propagate(False)

            # V / N / S
            ctk.CTkLabel(
                box,
                text=outcome,
                text_color="white",
                font=ctk.CTkFont(size=22, weight="bold")
            ).pack(pady=(8, 0))

            # Risultato
            ctk.CTkLabel(
                box,
                text=result,
                text_color="white",
                font=ctk.CTkFont(size=12, weight="bold")
            ).pack(pady=(0, 0))

            # Nome avversario — a capo automatico, nessun taglio
            ctk.CTkLabel(
                box,
                text=opponent,
                text_color="white",
                font=ctk.CTkFont(size=9, weight="bold"),
                wraplength=68,
                justify="center"
            ).pack(pady=(2, 0), padx=3)

            # Data partita
            try:
                date_fmt = datetime.strptime(match_date, "%Y-%m-%d").strftime("%d/%m")
            except Exception:
                date_fmt = match_date[-5:] if match_date else ""
            ctk.CTkLabel(
                box,
                text=date_fmt,
                text_color="rgba(255,255,255,0.7)" if False else "white",
                font=ctk.CTkFont(size=9),
                fg_color="transparent",
                text_color_disabled="white"
            ).pack(pady=(2, 4))

    def export_single_player_pdf(self, period=True):
        choice = self.pdf_player_var.get().strip() if hasattr(self, "pdf_player_var") else ""

        if " - " not in choice:
            messagebox.showerror("Errore", "Seleziona un giocatore valido.")
            return

        try:
            player_id = int(choice.split(" - ")[0])
        except ValueError:
            messagebox.showerror("Errore", "Giocatore non valido.")
            return

        if period:
            try:
                start = datetime.strptime(self.pdf_start_var.get(), "%d-%m-%y").date()
                end = datetime.strptime(self.pdf_end_var.get(), "%d-%m-%y").date()
            except ValueError:
                messagebox.showerror("Errore", "Date non valide. Usa il formato GG-MM-AA.")
                return

            if end < start:
                start, end = end, start

            start_db = ui_to_db_date(start.strftime("%d-%m-%y"))
            end_db = ui_to_db_date(end.strftime("%d-%m-%y"))
            period_label = f"{start.strftime('%d-%m-%y')} / {end.strftime('%d-%m-%y')}"
            default_name = f"statistiche_giocatore_{player_id}_{start.strftime('%d-%m-%y')}_{end.strftime('%d-%m-%y')}.pdf"
        else:
            start_db = "00-01-01"
            end_db = "99-12-31"
            period_label = "Stagione completa"
            default_name = f"statistiche_giocatore_{player_id}_stagione.pdf"

        file_path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile=default_name
        )
        if not file_path:
            return

        player = db_query("""
            SELECT id, trim(last_name || ' ' || first_name), birth_date, role
            FROM players
            WHERE id=?
              AND LOWER(TRIM(COALESCE(role, ''))) NOT IN ('mister', 'pres')
        """, (player_id,), fetch=True)

        if not player:
            messagebox.showerror("Errore", "Giocatore non trovato.")
            return

        _, name, birth, role = player[0]

        stats = db_query("""
            SELECT
                COUNT(a.id),
                COALESCE(SUM(a.starter),0),
                COALESCE(SUM(a.minutes),0),
                COALESCE(SUM(a.goals),0),
                COALESCE(SUM(a.assists),0),
                COALESCE(SUM(a.yellow_cards),0),
                COALESCE(SUM(a.red_cards),0),
                COALESCE(si.cnt,0),
                COALESCE(so.cnt,0)
            FROM appearances a
            JOIN matches m ON m.id=a.match_id
            LEFT JOIN (
                SELECT COUNT(*) AS cnt
                FROM substitutions s
                JOIN matches ms ON ms.id=s.match_id
                WHERE s.player_in_id=? AND ms.match_date BETWEEN ? AND ?
            ) si ON TRUE
            LEFT JOIN (
                SELECT COUNT(*) AS cnt
                FROM substitutions s
                JOIN matches ms ON ms.id=s.match_id
                WHERE s.player_out_id=? AND ms.match_date BETWEEN ? AND ?
            ) so ON TRUE
            WHERE a.player_id=?
              AND m.match_date BETWEEN ? AND ?
        """, (player_id, start_db, end_db, player_id, start_db, end_db, player_id, start_db, end_db), fetch=True)[0]

        apps, starts, minutes, goals, assists, yellow, red, sub_in, sub_out = stats

        match_rows = db_query("""
            SELECT
                m.match_date,
                m.opponent,
                m.competition,
                m.home_away,
                COALESCE(m.result, ''),
                a.starter,
                a.minutes,
                a.goals,
                a.assists,
                a.yellow_cards,
                a.red_cards,
                COALESCE(a.captain,0),
                COALESCE(a.vice_captain,0)
            FROM appearances a
            JOIN matches m ON m.id=a.match_id
            WHERE a.player_id=?
              AND m.match_date BETWEEN ? AND ?
            ORDER BY m.match_date
        """, (player_id, start_db, end_db), fetch=True)

        training = db_query("""
            SELECT
                COALESCE(SUM(CASE WHEN ta.present=1 THEN 1 ELSE 0 END),0),
                COALESCE(SUM(CASE WHEN ta.present IN (0,2) THEN 1 ELSE 0 END),0),
                COALESCE(SUM(CASE WHEN ta.present=2 THEN 1 ELSE 0 END),0),
                COUNT(ta.id)
            FROM training_attendance ta
            JOIN training_sessions ts ON ts.id=ta.session_id
            WHERE ta.player_id=?
              AND ts.training_date BETWEEN ? AND ?
        """, (player_id, start_db, end_db), fetch=True)[0]

        tr_present, tr_absent, tr_injured, tr_total = training

        ga90 = round(((goals + assists) * 90 / minutes), 2) if minutes else 0
        pct_starts = round((starts / apps) * 100, 1) if apps else 0
        pct_sub_in = round((sub_in / apps) * 100, 1) if apps else 0
        training_pct = round((tr_present / tr_total) * 100, 1) if tr_total else 0

        try:
            with PdfPages(file_path) as pdf:
                # Pagina riepilogo
                fig = Figure(figsize=(8.27, 11.69), dpi=100, facecolor="white")
                ax = fig.add_subplot(111)
                ax.axis("off")

                # Intestazione con logo squadra, se il file è disponibile accanto al programma.
                logo_path = None
                for logo_name in ["logo.png", "logo.jpg", "logo.jpeg", "icon.png", "icon.jpg"]:
                    logo_path = resource_path(logo_name)
                    if logo_path:
                        break

                if logo_path and Image:
                    try:
                        logo_img = Image.open(logo_path).convert("RGBA")
                        logo_ax = fig.add_axes([0.075, 0.865, 0.12, 0.09])
                        logo_ax.imshow(logo_img)
                        logo_ax.axis("off")
                        title_x = 0.22
                    except Exception:
                        title_x = 0.08
                else:
                    title_x = 0.08

                ax.text(title_x, 0.945, f"Gestionale {TEAM_NAME} {TEAM_SEASON}", fontsize=20, fontweight="bold", transform=ax.transAxes)
                ax.text(title_x, 0.905, "Report singolo giocatore", fontsize=15, fontweight="bold", transform=ax.transAxes)
                ax.text(title_x, 0.875, f"Periodo: {period_label}", fontsize=11, transform=ax.transAxes)

                # Anagrafica e statistiche totali in tabelle ordinate.
                def draw_info_table(ax, x, y_top, w, row_h, rows, title):
                    ax.add_patch(plt.Rectangle((x, y_top - row_h), w, row_h, transform=ax.transAxes,
                                               facecolor="#d1d5db", edgecolor="#9ca3af", linewidth=0.8))
                    ax.text(x + 0.01, y_top - row_h / 2, title, fontsize=9.5, fontweight="bold",
                            va="center", ha="left", transform=ax.transAxes)
                    for idx, (label, value) in enumerate(rows, start=1):
                        y = y_top - row_h * (idx + 1)
                        bg = "#ffffff" if idx % 2 else "#f9fafb"
                        ax.add_patch(plt.Rectangle((x, y), w * 0.54, row_h, transform=ax.transAxes,
                                                   facecolor=bg, edgecolor="#d1d5db", linewidth=0.6))
                        ax.add_patch(plt.Rectangle((x + w * 0.54, y), w * 0.46, row_h, transform=ax.transAxes,
                                                   facecolor=bg, edgecolor="#d1d5db", linewidth=0.6))
                        ax.text(x + 0.01, y + row_h / 2, str(label), fontsize=8.5, fontweight="bold",
                                va="center", ha="left", transform=ax.transAxes)
                        ax.text(x + w * 0.56, y + row_h / 2, str(value), fontsize=8.5,
                                va="center", ha="left", transform=ax.transAxes)

                anagrafica_rows = [
                    ("Giocatore", name),
                    ("Data nascita", birth or "-"),
                    ("Ruolo", role or "-"),
                ]
                partita_rows = [
                    ("Presenze", apps),
                    ("Titolare", f"{starts} ({pct_starts}%)"),
                    ("Subentrato", f"{sub_in} ({pct_sub_in}%)"),
                    ("Sostituito", sub_out),
                    ("Minuti", minutes),
                    ("Gol", goals),
                    ("Assist", assists),
                    ("G+A/90", ga90),
                    ("Ammonizioni", yellow),
                    ("Espulsioni", red),
                ]
                allenamenti_rows = [
                    ("Presenti", tr_present),
                    ("Assenti", tr_absent),
                    ("Infortunato", tr_injured),
                    ("Presenza allenamenti", f"{training_pct}%"),
                ]

                draw_info_table(ax, 0.08, 0.81, 0.38, 0.034, anagrafica_rows, "Dati giocatore")
                draw_info_table(ax, 0.08, 0.62, 0.38, 0.034, partita_rows, "Statistiche partita")
                draw_info_table(ax, 0.08, 0.21, 0.38, 0.034, allenamenti_rows, "Allenamenti")

                chart_ax = fig.add_axes([0.54, 0.56, 0.34, 0.26])
                if apps > 0:
                    other = max(0, 100 - pct_starts - pct_sub_in)
                    chart_ax.pie(
                        [pct_starts, pct_sub_in, other],
                        labels=["Titolare", "Subentrato", "Altro"],
                        autopct="%1.1f%%",
                        startangle=90,
                        textprops={"fontsize": 8}
                    )
                    chart_ax.set_title("Utilizzo partita", fontsize=11, fontweight="bold")
                else:
                    chart_ax.text(0.5, 0.5, "Nessuna presenza", ha="center", va="center", fontsize=9)
                    chart_ax.axis("off")

                pdf.savefig(fig)

                # Pagina dettaglio partite — tabella vera con griglia e colonne allineate
                detail_columns = [
                    "Data", "Avversario", "Competizione", "C/F", "Ris.",
                    "Tit.", "Min", "Gol", "Ass", "Amm", "Esp", "C/VC"
                ]
                detail_rows = []
                for row in match_rows:
                    match_date, opponent, competition, home_away, result, starter, mins, g, a, amm, esp, cap, vice = row
                    cap_txt = "C" if cap else "VC" if vice else ""
                    detail_rows.append([
                        db_to_ui_date(match_date),
                        opponent or "-",
                        competition or "-",
                        home_away or "-",
                        result or "-",
                        "Sì" if starter else "No",
                        mins or 0,
                        g or 0,
                        a or 0,
                        amm or 0,
                        esp or 0,
                        cap_txt,
                    ])

                self.pdf_add_table_page(
                    pdf,
                    f"Dettaglio partite - {name}",
                    detail_columns,
                    detail_rows,
                    col_widths=[0.09, 0.22, 0.16, 0.07, 0.07, 0.06, 0.06, 0.055, 0.055, 0.055, 0.055, 0.07],
                    max_rows=26,
                )

            messagebox.showinfo("PDF creato", f"PDF giocatore creato correttamente:\\n{file_path}")

        except Exception as exc:
            messagebox.showerror("Errore PDF", f"Non sono riuscito a creare il PDF giocatore:\\n{exc}")

    def export_players_pdf(self):
        try:
            if hasattr(self, "pdf_start_var") and hasattr(self, "pdf_end_var"):
                start = datetime.strptime(self.pdf_start_var.get(), "%d-%m-%y").date()
                end = datetime.strptime(self.pdf_end_var.get(), "%d-%m-%y").date()
            else:
                start, end = default_dates()
        except ValueError:
            messagebox.showerror("Errore", "Date non valide. Usa il formato GG-MM-AA.")
            return

        if end < start:
            start, end = end, start

        start_ui = start.strftime("%d-%m-%y")
        end_ui = end.strftime("%d-%m-%y")
        start_db = ui_to_db_date(start_ui)
        end_db = ui_to_db_date(end_ui)

        file_path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile=f"statistiche_squadra_{start_ui}_{end_ui}.pdf"
        )
        if not file_path:
            return

        rows = db_query("""
            SELECT
                p.id,
                trim(p.last_name || ' ' || p.first_name),
                p.role,
                COUNT(DISTINCT a.id),
                COALESCE(SUM(a.starter),0),
                COALESCE(SUM(a.minutes),0),
                COALESCE(SUM(a.goals),0),
                COALESCE(SUM(a.assists),0),
                COALESCE(SUM(a.yellow_cards),0),
                COALESCE(SUM(a.red_cards),0),
                COALESCE(si.subentrato, 0),
                COALESCE(so.sostituito, 0)
            FROM players p
            LEFT JOIN appearances a ON a.player_id=p.id
            LEFT JOIN matches m ON m.id=a.match_id AND m.match_date BETWEEN ? AND ?
            LEFT JOIN (
                SELECT s.player_in_id AS pid, COUNT(*) AS subentrato
                FROM substitutions s
                JOIN matches ms ON ms.id=s.match_id
                WHERE ms.match_date BETWEEN ? AND ?
                GROUP BY s.player_in_id
            ) si ON si.pid=p.id
            LEFT JOIN (
                SELECT s.player_out_id AS pid, COUNT(*) AS sostituito
                FROM substitutions s
                JOIN matches ms ON ms.id=s.match_id
                WHERE ms.match_date BETWEEN ? AND ?
                GROUP BY s.player_out_id
            ) so ON so.pid=p.id
            WHERE ((m.match_date BETWEEN ? AND ?) OR m.match_date IS NULL)
              AND LOWER(TRIM(COALESCE(p.role, ''))) NOT IN ('mister', 'pres')
            GROUP BY p.id, si.subentrato, so.sostituito
            ORDER BY COALESCE(SUM(a.minutes),0) DESC, p.last_name, p.first_name
        """, (start_db, end_db, start_db, end_db, start_db, end_db, start_db, end_db), fetch=True)

        player_rows = []
        for player_id, name, role, apps, starts, minutes, goals, assists, yellow, red, sub_in, sub_out in rows:
            pct_starts = round((starts / apps) * 100, 1) if apps else 0
            pct_sub = round((sub_in / apps) * 100, 1) if apps else 0
            ga90 = round(((goals + assists) * 90 / minutes), 2) if minutes else 0

            player_rows.append([
                name,
                role or "-",
                apps,
                starts,
                f"{pct_starts}%",
                sub_in,
                f"{pct_sub}%",
                sub_out,
                minutes,
                goals,
                assists,
                ga90,
                yellow,
                red
            ])

        try:
            with PdfPages(file_path) as pdf:
                fig = Figure(figsize=(11.69, 8.27), dpi=100, facecolor="white")
                ax = fig.add_subplot(111)
                ax.axis("off")
                ax.text(0.04, 0.90, f"Gestionale {TEAM_NAME} {TEAM_SEASON}", fontsize=22, fontweight="bold", transform=ax.transAxes)
                ax.text(0.04, 0.82, f"Statistiche squadra - periodo {start_ui} / {end_ui}", fontsize=14, transform=ax.transAxes)
                ax.text(0.04, 0.72, f"Giocatori in tabella: {len(player_rows)}", fontsize=11, transform=ax.transAxes)
                pdf.savefig(fig)

                self.pdf_add_table_page(
                    pdf,
                    f"Statistiche giocatori {start_ui} / {end_ui}",
                    ["Giocatore", "Ruolo", "Pres", "Tit", "Tit%", "Sub", "Sub%", "Sost", "Min", "Gol", "Ast", "G+A/90", "Amm", "Esp"],
                    player_rows,
                    col_widths=[0.22, 0.07, 0.055, 0.055, 0.065, 0.055, 0.065, 0.06, 0.065, 0.055, 0.055, 0.075, 0.055, 0.055],
                    max_rows=24
                )

            messagebox.showinfo("PDF creato", f"Report periodo salvato correttamente:\n{file_path}")
        except Exception as exc:
            messagebox.showerror("Errore PDF", f"Non sono riuscito a creare il PDF:\n{exc}")


    # ---------------- FIGURINE ----------------

    def player_photo_image(self, photo_data, size=(116, 116)):
        if not photo_data or Image is None:
            return None

        try:
            raw = base64.b64decode(photo_data)
            img = Image.open(BytesIO(raw))
            if ImageOps is not None:
                img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail(size)

            canvas = Image.new("RGB", size, "white")
            x = (size[0] - img.width) // 2
            y = (size[1] - img.height) // 2
            canvas.paste(img, (x, y))

            return ctk.CTkImage(canvas, size=size)
        except Exception:
            return None

    def get_motw_motm_players(self):
        """Ritorna (motw_player_id, motm_player_id). In caso di pari voto sceglie casualmente."""
        motw_id = None
        motm_id = None

        try:
            # MOTW: migliore media voto nell'ultima partita registrata con voti.
            # Se più giocatori hanno la stessa media migliore, ne sceglie uno random.
            rows = db_query("""
                WITH last_match AS (
                    SELECT m.id
                    FROM matches m
                    JOIN player_votes pv ON pv.match_id=m.id
                    GROUP BY m.id, m.match_date
                    ORDER BY m.match_date DESC, m.id DESC
                    LIMIT 1
                ),
                player_avgs AS (
                    SELECT
                        v.voted_player_id,
                        AVG(v.rating) AS avg_rating,
                        COUNT(v.id) AS votes_count
                    FROM player_votes v
                    JOIN last_match lm ON lm.id=v.match_id
                    GROUP BY v.voted_player_id
                    HAVING COUNT(v.id) > 0
                ),
                best_avg AS (
                    SELECT MAX(avg_rating) AS max_rating
                    FROM player_avgs
                )
                SELECT pa.voted_player_id
                FROM player_avgs pa
                JOIN best_avg b ON pa.avg_rating=b.max_rating
                ORDER BY RANDOM()
                LIMIT 1
            """, fetch=True)

            if rows:
                motw_id = rows[0][0]
        except Exception:
            motw_id = None

        try:
            # MOTM: migliore media voto del mese precedente a quello attuale.
            # In caso di pari media migliore, sceglie casualmente.
            rows = db_query("""
                WITH player_avgs AS (
                    SELECT
                        v.voted_player_id,
                        AVG(v.rating) AS avg_rating,
                        COUNT(v.id) AS votes_count
                    FROM player_votes v
                    JOIN matches m ON m.id=v.match_id
                    WHERE m.match_date::date >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month'
                      AND m.match_date::date < date_trunc('month', CURRENT_DATE)
                    GROUP BY v.voted_player_id
                    HAVING COUNT(v.id) > 0
                ),
                best_avg AS (
                    SELECT MAX(avg_rating) AS max_rating
                    FROM player_avgs
                )
                SELECT pa.voted_player_id
                FROM player_avgs pa
                JOIN best_avg b ON pa.avg_rating=b.max_rating
                ORDER BY RANDOM()
                LIMIT 1
            """, fetch=True)

            if rows:
                motm_id = rows[0][0]
        except Exception:
            motm_id = None

        return motw_id, motm_id


    def show_player_cards(self):
        self.set_active_nav("Figurine")
        self.clear_main()

        page = self.page_container()
        self.header(
            page,
            "Figurine giocatori",
            "Foto caricate dai giocatori tramite app mobile con statistiche principali e media voto."
        )

        grid = ctk.CTkFrame(page, fg_color=COLORS["bg"])
        grid.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 24))

        for col in range(4):
            grid.grid_columnconfigure(col, weight=1)

        motw_id, motm_id = self.get_motw_motm_players()

        rows = db_query("""
            WITH app_stats AS (
                SELECT
                    a.player_id,
                    COUNT(*) AS presenze,
                    COALESCE(SUM(a.goals), 0) AS gol,
                    COALESCE(SUM(a.assists), 0) AS assist
                FROM appearances a
                JOIN matches m ON m.id = a.match_id
                GROUP BY a.player_id
            ),
            vote_stats AS (
                SELECT
                    v.voted_player_id AS player_id,
                    COALESCE(ROUND(AVG(v.rating)::numeric, 2), 0) AS media_voto
                FROM player_votes v
                JOIN matches m ON m.id = v.match_id
                GROUP BY v.voted_player_id
            )
            SELECT
                p.id,
                trim(p.last_name || ' ' || p.first_name) AS player_name,
                COALESCE(p.role, ''),
                COALESCE(p.photo_data, ''),
                COALESCE(app.presenze, 0) AS presenze,
                COALESCE(app.gol, 0) AS gol,
                COALESCE(app.assist, 0) AS assist,
                COALESCE(vote.media_voto, 0) AS media_voto
            FROM players p
            LEFT JOIN app_stats app ON app.player_id = p.id
            LEFT JOIN vote_stats vote ON vote.player_id = p.id
            WHERE LOWER(TRIM(COALESCE(p.role, ''))) NOT IN ('mister', 'pres')
            ORDER BY p.last_name, p.first_name
        """, fetch=True)

        if not rows:
            empty = self.card(grid, row=0, column=0, sticky="ew", padx=0, pady=0)
            ctk.CTkLabel(
                empty,
                text="Nessun giocatore presente.",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=15, weight="bold")
            ).pack(padx=18, pady=18)
            return

        for idx, row in enumerate(rows):
            player_id, name, role, photo_data, presenze, gol, assist, media_voto = row
            r = idx // 4
            c = idx % 4

            if player_id == motw_id:
                # MOTW - Nero con saette oro
                outer = ctk.CTkFrame(
                    grid,
                    fg_color="#facc15",
                    corner_radius=22
                )
                outer.grid(row=r, column=c, sticky="nsew", padx=8, pady=8)

                card = ctk.CTkFrame(
                    outer,
                    fg_color="#0a0a0a",
                    corner_radius=20,
                    border_width=3,
                    border_color="#facc15"
                )
                card.pack(fill="both", expand=True, padx=3, pady=3)

            elif player_id == motm_id:
                # MOTM - Blu/Rosso premium
                outer = ctk.CTkFrame(
                    grid,
                    fg_color="#2563eb",
                    corner_radius=22
                )
                outer.grid(row=r, column=c, sticky="nsew", padx=8, pady=8)

                card = ctk.CTkFrame(
                    outer,
                    fg_color="#991b1b",
                    corner_radius=20,
                    border_width=3,
                    border_color="#60a5fa"
                )
                card.pack(fill="both", expand=True, padx=3, pady=3)

            else:
                card = self.card(grid, row=r, column=c, sticky="nsew", padx=8, pady=8)

            card.grid_columnconfigure(0, weight=1)

            badge_text = ""
            badge_color = COLORS["text"]

            if player_id == motw_id:
                badge_text = "⚡ MOTW ⚡"
                badge_color = "#facc15"
            elif player_id == motm_id:
                badge_text = "🔥 MOTM 🔥"
                badge_color = "#dbeafe"

            if badge_text:
                ctk.CTkLabel(
                    card,
                    text=badge_text,
                    text_color=badge_color,
                    font=ctk.CTkFont(size=18, weight="bold")
                ).pack(pady=(12, 0))

            photo = self.player_photo_image(photo_data)

            if photo:
                ctk.CTkLabel(card, text="", image=photo).pack(pady=(16, 8))
            else:
                placeholder = ctk.CTkFrame(card, fg_color="#e5e7eb", width=116, height=116, corner_radius=58)
                placeholder.pack(pady=(16, 8))
                placeholder.pack_propagate(False)
                ctk.CTkLabel(
                    placeholder,
                    text="👤",
                    font=ctk.CTkFont(size=42),
                    text_color=COLORS["muted"]
                ).pack(expand=True)

            ctk.CTkLabel(
                card,
                text=name,
                text_color="#eff6ff" if player_id == motm_id else COLORS["text"],
                font=ctk.CTkFont(size=15, weight="bold"),
                wraplength=190
            ).pack(padx=12, pady=(0, 2))

            ctk.CTkLabel(
                card,
                text=role or "-",
                text_color="#fde68a" if player_id == motm_id else COLORS["blue"],
                font=ctk.CTkFont(size=12, weight="bold")
            ).pack(padx=12, pady=(0, 10))

            stats = ctk.CTkFrame(
                card,
                fg_color="#1e3a8a" if player_id == motm_id else ("#1a1a1a" if player_id == motw_id else "#f8fafc"),
                corner_radius=12
            )
            stats.pack(fill="x", padx=14, pady=(0, 14))
            for i in range(4):
                stats.grid_columnconfigure(i, weight=1)

            values = [
                ("Partite", presenze),
                ("Gol", gol),
                ("Assist", assist),
                ("Voto", media_voto),
            ]

            for col, (label, value) in enumerate(values):
                ctk.CTkLabel(
                    stats,
                    text=str(value),
                    text_color=(
                        "#facc15" if player_id == motw_id
                        else ("#dbeafe" if player_id == motm_id else (COLORS["red"] if label == "Voto" else "#111827"))
                    ),
                    font=ctk.CTkFont(size=16, weight="bold")
                ).grid(row=0, column=col, pady=(8, 0))

                ctk.CTkLabel(
                    stats,
                    text=label,
                    text_color=(
                        "#fde68a" if player_id == motw_id
                        else ("#bfdbfe" if player_id == motm_id else "#374151")
                    ),
                    font=ctk.CTkFont(size=10, weight="bold")
                ).grid(row=1, column=col, pady=(0, 8))

    # ---------------- GIOCATORI ----------------

    def build_player_form_fields(self, form, popup=False):
        self.player_first_name_var = tk.StringVar()
        self.player_last_name_var  = tk.StringVar()
        self.player_birth_var      = tk.StringVar(value=date.today().strftime("%d-%m-%Y"))
        self.player_role_var       = tk.StringVar(value="")
        self.player_role2_var      = tk.StringVar(value="")   # ruolo secondario (opzionale)

        labels = ["Nome", "Cognome", "Data di nascita", "Ruolo 1", "Ruolo 2"]
        for col, label in enumerate(labels):
            ctk.CTkLabel(
                form, text=label, text_color=COLORS["muted"],
                font=ctk.CTkFont(size=12, weight="bold")
            ).grid(row=0, column=col, padx=(16, 8) if col == 0 else 8, pady=(12, 0), sticky="w")

        ctk.CTkEntry(form, textvariable=self.player_first_name_var, placeholder_text="Nome",   width=150, height=40).grid(row=1, column=0, padx=(16, 8), pady=(4, 18))
        ctk.CTkEntry(form, textvariable=self.player_last_name_var,  placeholder_text="Cognome", width=155, height=40).grid(row=1, column=1, padx=8,       pady=(4, 18))
        self.date_picker_row(form, self.player_birth_var, date_format="%d-%m-%Y").grid(row=1, column=2, padx=8, pady=(4, 18))
        ctk.CTkOptionMenu(form, values=[""] + ROLES, variable=self.player_role_var,
                          width=115, height=40, fg_color=COLORS["gold_dark"],
                          text_color="#0a1f0e", button_color=COLORS["gold"],
                          button_hover_color=COLORS["gold_light"]
                          ).grid(row=1, column=3, padx=8, pady=(4, 18))
        ctk.CTkOptionMenu(form, values=[""] + ROLES, variable=self.player_role2_var,
                          width=115, height=40, fg_color=COLORS["surface"],
                          text_color=COLORS["text"], button_color=COLORS["border_light"],
                          button_hover_color=COLORS["gold_dark"]
                          ).grid(row=1, column=4, padx=8, pady=(4, 18))
        ctk.CTkButton(form, text="Salva" if popup else "Aggiungi",
                      fg_color=COLORS["red"], hover_color=COLORS["gold"],
                      width=100, height=40, command=self.add_player
                      ).grid(row=1, column=5, padx=8, pady=(4, 18))
        ctk.CTkButton(form, text="Modifica",
                      fg_color=COLORS["gold_dark"], width=100, height=40,
                      command=self.update_player
                      ).grid(row=1, column=6, padx=8, pady=(4, 18))
        ctk.CTkButton(form, text="Elimina",
                      fg_color=COLORS["red_dark"], width=100, height=40,
                      command=self.delete_player
                      ).grid(row=1, column=7, padx=(8, 16), pady=(4, 18))

    def build_players_tree(self, parent):
        columns = ("id", "first_name", "last_name", "birth_date", "role")
        headers = {
            "id": "ID", "first_name": "Nome", "last_name": "Cognome",
            "birth_date": "Data nascita", "role": "Ruolo"
        }
        widths = {"id": 60, "first_name": 220, "last_name": 240, "birth_date": 130, "role": 180}
        self.players_tree = self.create_tree(parent, columns, headers, widths)
        self.players_tree.bind("<<TreeviewSelect>>", self.on_player_select)
        self.refresh_players()

    def open_player_panel(self):
        popup = ctk.CTkToplevel(self)
        popup.title("Nuovo giocatore")
        popup.geometry("980x620")
        popup.minsize(900, 560)
        popup.configure(fg_color=COLORS["bg"])
        popup.grab_set()

        ctk.CTkLabel(
            popup, text="＋ Nuovo giocatore", text_color=COLORS["text"],
            font=ctk.CTkFont(size=26, weight="bold")
        ).pack(anchor="w", padx=24, pady=(24, 6))

        ctk.CTkLabel(
            popup, text="Inserisci nome, cognome, data di nascita e ruolo.",
            text_color=COLORS["muted"], font=ctk.CTkFont(size=13)
        ).pack(anchor="w", padx=24, pady=(0, 16))

        form = ctk.CTkFrame(
            popup, fg_color=COLORS["card"], corner_radius=16,
            border_width=1, border_color=COLORS["border"]
        )
        form.pack(fill="x", padx=24, pady=(0, 16))
        self.build_player_form_fields(form, popup=True)

        table = ctk.CTkFrame(
            popup, fg_color=COLORS["card"], corner_radius=16,
            border_width=1, border_color=COLORS["border"]
        )
        table.pack(fill="both", expand=True, padx=24, pady=(0, 24))
        self.build_players_tree(table)

    def show_players(self):
        self.set_active_nav("Giocatori")
        self.clear_main()
        page = self.page_container()
        self.header(page, "Giocatori", "Anagrafica giocatori: nome, cognome, data di nascita e ruolo.")

        form = self.card(page, row=1, column=0, sticky="ew", padx=24, pady=(0, 14))
        self.build_player_form_fields(form, popup=False)

        table = self.card(page, row=2, column=0, sticky="nsew", padx=24, pady=(0, 24))
        self.build_players_tree(table)

    def refresh_players(self):
        if not hasattr(self, "players_tree"):
            return
        for item in self.players_tree.get_children():
            self.players_tree.delete(item)
        rows = db_query("""
            SELECT id, first_name, last_name, birth_date, role
            FROM players
            WHERE LOWER(TRIM(COALESCE(role, ''))) NOT IN ('mister', 'pres')
            ORDER BY last_name, first_name
        """, fetch=True)
        for row in rows:
            self.players_tree.insert("", "end", values=row)

    def refresh_player_menus(self):
        values = self.player_options()
        for menu_name in ["player_menu", "training_player_menu", "captain_menu", "vice_captain_menu"]:
            menu = getattr(self, menu_name, None)
            if menu:
                try:
                    self.set_combo_values(menu, values)
                except Exception:
                    pass

    # ── helper ruolo doppio ────────────────────────────────────────────────
    def _get_combined_role(self):
        r1 = self.player_role_var.get().strip()
        r2 = getattr(self, "player_role2_var", tk.StringVar()).get().strip()
        if r1 and r2:
            return f"{r1}/{r2}"
        return r1 or r2

    def _set_role_vars(self, role_str):
        """Scompone 'CDC/ATT' in Ruolo 1 e Ruolo 2."""
        parts = (role_str or "").split("/", 1)
        self.player_role_var.set(parts[0].strip() if parts else "")
        if hasattr(self, "player_role2_var"):
            self.player_role2_var.set(parts[1].strip() if len(parts) > 1 else "")
    # ──────────────────────────────────────────────────────────────────────

    def add_player(self):
        first_name = self.player_first_name_var.get().strip()
        last_name  = self.player_last_name_var.get().strip()
        birth_date = self.player_birth_var.get().strip()
        role       = self._get_combined_role()

        if not first_name or not last_name:
            messagebox.showerror("Errore", "Inserisci nome e cognome.")
            return

        try:
            if birth_date:
                datetime.strptime(birth_date, "%d-%m-%Y")
        except ValueError:
            messagebox.showerror("Errore", "La data di nascita deve essere nel formato GG-MM-AAAA (es. 15-03-2005).")
            return

        existing = db_query("""
            SELECT COUNT(*)
            FROM players
            WHERE lower(trim(first_name)) = lower(trim(?))
              AND lower(trim(last_name)) = lower(trim(?))
              AND COALESCE(birth_date, '') = COALESCE(?, '')
        """, (first_name, last_name, birth_date), fetch=True)[0][0]

        if existing:
            messagebox.showerror("Errore", "Questo giocatore è già presente con la stessa data di nascita.")
            return

        db_query(
            "INSERT INTO players (first_name, last_name, birth_date, role) VALUES (?, ?, ?, ?)",
            (first_name, last_name, birth_date, role)
        )

        self.player_first_name_var.set("")
        self.player_last_name_var.set("")
        self.player_birth_var.set(date.today().strftime("%d-%m-%Y"))
        self._set_role_vars("")
        self.selected_player_id = None
        self._invalidate_player_cache()
        self.refresh_players()
        self.refresh_player_menus()

    def update_player(self):
        if not self.selected_player_id:
            messagebox.showinfo("Seleziona", "Seleziona un giocatore.")
            return

        first_name = self.player_first_name_var.get().strip()
        last_name  = self.player_last_name_var.get().strip()
        birth_date = self.player_birth_var.get().strip()
        role       = self._get_combined_role()

        if not first_name or not last_name:
            messagebox.showerror("Errore", "Inserisci nome e cognome.")
            return

        try:
            if birth_date:
                datetime.strptime(birth_date, "%d-%m-%Y")
        except ValueError:
            messagebox.showerror("Errore", "La data di nascita deve essere nel formato GG-MM-AAAA (es. 15-03-2005).")
            return

        db_query(
            "UPDATE players SET first_name=?, last_name=?, birth_date=?, role=? WHERE id=?",
            (first_name, last_name, birth_date, role, self.selected_player_id)
        )
        self._invalidate_player_cache()
        self.refresh_players()
        self.refresh_player_menus()

    def delete_player(self):
        if not self.selected_player_id:
            messagebox.showinfo("Seleziona", "Seleziona un giocatore.")
            return

        if messagebox.askyesno("Conferma", "Eliminare il giocatore selezionato?"):
            db_query("DELETE FROM players WHERE id=?", (self.selected_player_id,))
            compact_player_ids()
            self._invalidate_player_cache()

            self.selected_player_id = None
            self.player_first_name_var.set("")
            self.player_last_name_var.set("")
            self.player_birth_var.set(date.today().strftime("%d-%m-%Y"))
            self._set_role_vars("")

            self.refresh_players()
            self.refresh_player_menus()

    def on_player_select(self, _event):
        sel = self.players_tree.selection()
        if not sel:
            return
        values = self.players_tree.item(sel[0], "values")
        self.selected_player_id = values[0]
        self.player_first_name_var.set(values[1])
        self.player_last_name_var.set(values[2])
        self.player_birth_var.set(values[3])
        self._set_role_vars(values[4])

    def _invalidate_player_cache(self):
        """Invalida la cache lista giocatori dopo ogni modifica al DB."""
        self._player_options_cache = None

    def player_options(self):
        if self._player_options_cache is not None:
            return self._player_options_cache
        rows = db_query("""
            SELECT id, first_name, last_name
            FROM players
            WHERE LOWER(TRIM(COALESCE(role, ''))) NOT IN ('mister', 'pres')
            ORDER BY last_name, first_name
        """, fetch=True)
        result = [f"{r[0]} - {r[2]} {r[1]}".strip() for r in rows] or ["Nessun giocatore"]
        self._player_options_cache = result
        return result

    # ---------------- PARTITE ----------------

    def show_matches(self):
        self.set_active_nav("Partite")
        self.clear_main()
        page = self.page_container()
        self.header(page, "Partite", "Inserisci e gestisci le partite. La formazione si compila nella schermata Formazione.")

        form_wrap = ctk.CTkFrame(page, fg_color=COLORS["bg"])
        form_wrap.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 14))
        form_wrap.grid_columnconfigure(0, weight=1)

        self.build_match_form(form_wrap)

        matches_grid = ctk.CTkFrame(page, fg_color=COLORS["bg"])
        matches_grid.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 24))
        matches_grid.grid_columnconfigure(0, weight=1)
        matches_grid.grid_columnconfigure(1, weight=1)

        campionato_card = self.card(matches_grid, row=0, column=0, sticky="nsew", padx=(0, 8), pady=0)
        coppa_card = self.card(matches_grid, row=0, column=1, sticky="nsew", padx=(8, 0), pady=0)

        ctk.CTkLabel(
            campionato_card,
            text="Partite Campionato",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=18, pady=(16, 8))

        ctk.CTkLabel(
            coppa_card,
            text="Partite Coppa",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=18, pady=(16, 8))

        columns = ("id", "date", "opponent", "home_away")
        headers = {
            "id": "ID",
            "date": "Data",
            "opponent": "Avversario",
            "home_away": "Casa/Fuori"
        }
        widths = {"id": 50, "date": 110, "opponent": 260, "home_away": 110}

        self.matches_tree_campionato = self.create_tree(campionato_card, columns, headers, widths)
        self.matches_tree_campionato.bind("<<TreeviewSelect>>", self.on_match_select)

        self.matches_tree_coppa = self.create_tree(coppa_card, columns, headers, widths)
        self.matches_tree_coppa.bind("<<TreeviewSelect>>", self.on_match_select)

        self.refresh_matches()

    def build_match_form(self, parent):
        form = self.card(parent, row=0, column=0, sticky="ew", padx=0, pady=0)
        ctk.CTkLabel(
            form, text="Nuova partita", text_color=COLORS["text"],
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=18, pady=(16, 10))

        self.match_date_var = tk.StringVar(value=date.today().strftime("%d-%m-%y"))
        self.opponent_var = tk.StringVar()
        self.competition_var = tk.StringVar(value="Campionato")
        self.home_away_var = tk.StringVar(value="Casa")

        self.form_field(form, "Data", self.date_picker_row(form, self.match_date_var))
        self.form_field(form, "Avversario", ctk.CTkEntry(form, textvariable=self.opponent_var, height=36))
        self.form_field(form, "Competizione", ctk.CTkOptionMenu(form, values=["Campionato", "Coppa"], variable=self.competition_var, height=36, fg_color=COLORS["gold_dark"]))
        self.form_field(form, "Casa/Fuori", ctk.CTkOptionMenu(form, values=["Casa", "Fuori"], variable=self.home_away_var, height=36, fg_color=COLORS["gold_dark"]))


        buttons = ctk.CTkFrame(form, fg_color=COLORS["card"])
        buttons.pack(fill="x", padx=18, pady=(10, 18))
        ctk.CTkButton(buttons, text="Salva partita", fg_color=COLORS["red"], command=self.save_match).pack(side="left", fill="x", expand=True, padx=(0, 5))
        ctk.CTkButton(buttons, text="Elimina", fg_color=COLORS["red_dark"], command=self.delete_match).pack(side="left", fill="x", expand=True, padx=(5, 0))

    def show_formation(self):
        self.set_active_nav("Formazione")
        self.clear_main()
        page = self.page_container()
        self.header(page, "Formazione partita", "Seleziona una partita e compila campo, panchina, capitano, minuti, gol, assist e cartellini.")

        try:
            selector = self.card(page, row=1, column=0, sticky="ew", padx=24, pady=(0, 14))
            selector.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(
                selector,
                text="Partita",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=12, weight="bold")
            ).grid(row=0, column=0, padx=(16, 8), pady=14, sticky="w")

            self.formation_match_var = tk.StringVar(value="")
            options = self.match_options()
            self.formation_match_menu = ctk.CTkOptionMenu(
                selector,
                values=options,
                variable=self.formation_match_var,
                height=36,
                fg_color=COLORS["gold_dark"]
            )
            self.formation_match_menu.grid(row=0, column=1, sticky="ew", padx=8, pady=14)

            if options and options[0] != "Nessuna partita":
                self.formation_match_var.set(options[0])

            ctk.CTkButton(
                selector,
                text="Carica partita",
                fg_color=COLORS["red"],
                hover_color=COLORS["gold"],
                command=self.select_formation_match
            ).grid(row=0, column=2, padx=(8, 16), pady=14)

            self.result_var = tk.StringVar()
            ctk.CTkLabel(
                selector,
                text="Risultato",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=12, weight="bold")
            ).grid(row=1, column=0, padx=(16, 8), pady=(0, 14), sticky="w")

            ctk.CTkEntry(
                selector,
                textvariable=self.result_var,
                height=36,
                width=140,
                placeholder_text="es. 2-1"
            ).grid(row=1, column=1, sticky="w", padx=8, pady=(0, 14))

            ctk.CTkLabel(
                selector,
                text="Il totale gol giocatori deve combaciare col risultato.",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=11)
            ).grid(row=1, column=2, sticky="w", padx=(8, 16), pady=(0, 14))

            formation_wrap = ctk.CTkFrame(page, fg_color=COLORS["bg"])
            formation_wrap.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 24))
            formation_wrap.grid_columnconfigure(0, weight=1)

            self.build_appearance_form(formation_wrap)

        except Exception as exc:
            error_card = self.card(page, row=2, column=0, sticky="ew", padx=24, pady=(0, 24))
            ctk.CTkLabel(
                error_card,
                text="Errore caricamento Formazione",
                text_color=COLORS["red"],
                font=ctk.CTkFont(size=18, weight="bold")
            ).pack(anchor="w", padx=18, pady=(18, 6))
            ctk.CTkLabel(
                error_card,
                text=str(exc),
                text_color=COLORS["text"],
                font=ctk.CTkFont(size=12),
                wraplength=900
            ).pack(anchor="w", padx=18, pady=(0, 18))

    def match_options(self):
        rows = db_query("""
            SELECT id, match_date, opponent, competition, home_away, result
            FROM matches
            ORDER BY match_date DESC, id DESC
        """, fetch=True)
        options = []
        for match_id, match_date, opponent, competition, home_away, result in rows:
            result_txt = f" - {result}" if result else ""
            options.append(f"{match_id} - {db_to_ui_date(match_date)} vs {opponent} ({competition}, {home_away}){result_txt}")
        return options or ["Nessuna partita"]

    def select_formation_match(self):
        choice = self.formation_match_var.get().strip()
        if not choice or choice == "Nessuna partita" or " - " not in choice:
            messagebox.showinfo("Seleziona", "Seleziona una partita valida.")
            return

        try:
            self.selected_match_id = int(choice.split(" - ")[0])
        except ValueError:
            messagebox.showerror("Errore", "Partita non valida.")
            return

        match_data = db_query("SELECT result FROM matches WHERE id=?", (self.selected_match_id,), fetch=True)
        if match_data and hasattr(self, "result_var"):
            self.result_var.set(match_data[0][0] or "")

        if hasattr(self, "selected_match_label"):
            self.selected_match_label.configure(text=f"Partita selezionata: {choice}")

        self.refresh_appearances()
        self.load_formation_slots()
        self.load_substitution_slots()

    def build_appearance_form(self, parent):
        form = self.card(parent, row=0, column=0, sticky="nsew", padx=0, pady=0)

        ctk.CTkLabel(
            form,
            text="Campo gara - 11 titolari + 9 panchinari",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(16, 4))

        self.selected_match_label = ctk.CTkLabel(
            form,
            text="Nessuna partita selezionata",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.selected_match_label.pack(anchor="w", padx=18, pady=(0, 8))

        self.formation_player_vars = []
        self.formation_minutes_vars = []
        self.formation_player_menus = []
        self.sub_in_vars = []
        self.sub_out_vars = []
        self.sub_in_menus = []
        self.sub_out_menus = []
        self.formation_goals_vars = []
        self.formation_assists_vars = []
        self.formation_yellow_vars = []
        self.formation_red_vars = []
        self.formation_player_menus = []
        self.captain_var = tk.StringVar(value="")
        self.vice_captain_var = tk.StringVar(value="")
        self.module_var = tk.StringVar(value="4-3-1-2")

        field = ctk.CTkFrame(form, fg_color="#15803d", corner_radius=18)
        field.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        for col in range(4):
            field.grid_columnconfigure(col, weight=1)

        top_field = ctk.CTkFrame(field, fg_color="#15803d")
        top_field.grid(row=0, column=0, columnspan=4, sticky="ew", padx=10, pady=(8, 2))
        top_field.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            top_field,
            text="Distinta gara",
            text_color="#dcfce7",
            font=ctk.CTkFont(size=13, weight="bold")
        ).grid(row=0, column=0, sticky="w")

        right_box = ctk.CTkFrame(top_field, fg_color="#15803d")
        right_box.grid(row=0, column=1, sticky="e")

        ctk.CTkLabel(
            right_box,
            text="Capitano",
            text_color="#dcfce7",
            font=ctk.CTkFont(size=11, weight="bold")
        ).grid(row=0, column=0, padx=(0, 4))
        self.captain_menu = self.create_searchable_combo(
            right_box,
            self.captain_var,
            self.player_options(),
            width=155,
            bg="#166534",
            fg="white"
        )
        self.captain_menu.grid(row=0, column=1, padx=(0, 10))

        ctk.CTkLabel(
            right_box,
            text="Vice",
            text_color="#dcfce7",
            font=ctk.CTkFont(size=11, weight="bold")
        ).grid(row=0, column=2, padx=(0, 4))
        self.vice_captain_menu = self.create_searchable_combo(
            right_box,
            self.vice_captain_var,
            self.player_options(),
            width=155,
            bg="#166534",
            fg="white"
        )
        self.vice_captain_menu.grid(row=0, column=3)

        ctk.CTkLabel(
            right_box,
            text="Modulo",
            text_color="#dcfce7",
            font=ctk.CTkFont(size=11, weight="bold")
        ).grid(row=1, column=0, padx=(0, 4), pady=(6, 0))

        self.module_menu = ctk.CTkOptionMenu(
            right_box,
            values=["4-3-1-2", "4-3-3", "4-4-2", "3-5-2", "4-2-3-1", "3-4-3", "5-3-2"],
            variable=self.module_var,
            width=155,
            height=28,
            fg_color=COLORS["gold_dark"],
            font=ctk.CTkFont(size=11)
        )
        self.module_menu.grid(row=1, column=1, padx=(0, 10), pady=(6, 0))

        ctk.CTkButton(
            right_box,
            text="Aggiorna modulo",
            width=155,
            height=28,
            fg_color=COLORS["red"],
            hover_color=COLORS["gold"],
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.redraw_formation_field
        ).grid(row=1, column=2, columnspan=2, sticky="ew", pady=(6, 0))

        self.field_slots_frame = ctk.CTkFrame(field, fg_color="#15803d")
        self.field_slots_frame.grid(row=1, column=0, columnspan=4, sticky="nsew")
        for col in range(9):
            self.field_slots_frame.grid_columnconfigure(col, weight=1)

        self.redraw_formation_field()

        bench = ctk.CTkFrame(form, fg_color=COLORS["card"])
        bench.pack(fill="x", padx=18, pady=(0, 12))
        ctk.CTkLabel(
            bench,
            text="Panchina",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=4, pady=(0, 6))

        bench_grid = ctk.CTkFrame(bench, fg_color=COLORS["card"])
        bench_grid.pack(fill="x")

        for idx in range(11, 20):
            r = (idx - 11) // 3
            c = (idx - 11) % 3
            self.build_player_slot(
                bench_grid,
                idx,
                title=f"Panchina {idx - 10}",
                row=r,
                column=c,
                starter=False
            )

        subs = ctk.CTkFrame(form, fg_color=COLORS["card"])
        subs.pack(fill="x", padx=18, pady=(0, 12))

        ctk.CTkLabel(
            subs,
            text="Sostituzioni",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, columnspan=5, sticky="w", padx=4, pady=(0, 6))

        for i in range(5):
            self.sub_out_vars.append(tk.StringVar(value=""))
            self.sub_in_vars.append(tk.StringVar(value=""))

            ctk.CTkLabel(
                subs,
                text=f"{i+1}",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=11, weight="bold")
            ).grid(row=i+1, column=0, padx=(4, 8), pady=3)

            ctk.CTkLabel(
                subs,
                text="Esce",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=10, weight="bold")
            ).grid(row=i+1, column=1, padx=(0, 4), pady=3)

            out_menu = self.create_searchable_combo(
                subs,
                self.sub_out_vars[i],
                self.available_substitution_player_options(i, "out"),
                width=210,
                bg="#dc2626",
                fg="white",
                command=lambda _choice, idx=i: self.refresh_substitution_menus(except_pair=idx),
            )
            out_menu.grid(row=i+1, column=2, padx=(0, 12), pady=3)
            self.sub_out_menus.append(out_menu)

            ctk.CTkLabel(
                subs,
                text="Entra",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=10, weight="bold")
            ).grid(row=i+1, column=3, padx=(0, 4), pady=3)

            in_menu = self.create_searchable_combo(
                subs,
                self.sub_in_vars[i],
                self.available_substitution_player_options(i, "in"),
                width=210,
                bg="#16a34a",
                fg="white",
                command=lambda _choice, idx=i: self.refresh_substitution_menus(except_pair=idx),
            )
            in_menu.grid(row=i+1, column=4, padx=(0, 4), pady=3)
            self.sub_in_menus.append(in_menu)

        actions = ctk.CTkFrame(form, fg_color=COLORS["card"])
        actions.pack(fill="x", padx=18, pady=(0, 12))

        ctk.CTkButton(
            actions,
            text="Salva distinta partita",
            fg_color=COLORS["red"],
            hover_color=COLORS["gold"],
            command=self.save_formation_slots
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            actions,
            text="Svuota distinta",
            fg_color=COLORS["red_dark"],
            command=self.clear_formation_slots
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        columns = ("id", "player", "starter", "minutes", "goals", "assists", "yellow", "red", "cap")
        headers = {"id": "ID", "player": "Giocatore", "starter": "Tit.", "minutes": "Min", "goals": "Gol", "assists": "Ast", "yellow": "Amm", "red": "Esp", "cap": "Cap"}
        widths = {"id": 40, "player": 190, "starter": 50, "minutes": 60, "goals": 50, "assists": 50, "yellow": 50, "red": 50, "cap": 55}
        self.appearances_tree = self.create_tree(form, columns, headers, widths)
        self.appearances_tree.bind("<<TreeviewSelect>>", self.on_appearance_select)

    def get_formation_lines(self):
        module = self.module_var.get() if hasattr(self, "module_var") else "4-3-3"
        schemes = {
            "4-3-3": [("ATT", 3), ("CEN", 3), ("DIF", 4), ("POR", 1)],
            "4-4-2": [("ATT", 2), ("CEN", 4), ("DIF", 4), ("POR", 1)],
            "3-5-2": [("ATT", 2), ("CEN", 5), ("DIF", 3), ("POR", 1)],
            "4-2-3-1": [("ATT", 1), ("TRE", 3), ("MED", 2), ("DIF", 4), ("POR", 1)],
            "4-3-1-2": [("ATT", 2), ("COC", 1), ("CEN", 3), ("DIF", 4), ("POR", 1)],
            "3-4-3": [("ATT", 3), ("CEN", 4), ("DIF", 3), ("POR", 1)],
            "5-3-2": [("ATT", 2), ("CEN", 3), ("DIF", 5), ("POR", 1)],
        }
        return schemes.get(module, schemes["4-3-3"])

    def centered_columns_for_count(self, count):
        positions = {
            1: [4],
            2: [3, 5],
            3: [2, 4, 6],
            4: [1, 3, 5, 7],
            5: [0, 2, 4, 6, 8],
        }
        return positions.get(count, list(range(count)))

    def redraw_formation_field(self):
        if not hasattr(self, "field_slots_frame"):
            return

        for child in self.field_slots_frame.winfo_children():
            child.destroy()

        slot_index = 0
        row_index = 0

        for label, count in self.get_formation_lines():
            ctk.CTkLabel(
                self.field_slots_frame,
                text=label,
                text_color="#dcfce7",
                font=ctk.CTkFont(size=11, weight="bold")
            ).grid(row=row_index, column=0, columnspan=9, pady=(8, 2))
            row_index += 1

            columns = self.centered_columns_for_count(count)
            for i in range(count):
                self.build_player_slot(
                    self.field_slots_frame,
                    slot_index,
                    title=f"Titolare {slot_index + 1}",
                    row=row_index,
                    column=columns[i],
                    starter=True
                )
                slot_index += 1
            row_index += 1

    def selected_formation_player_ids(self, except_index=None):
        selected = set()
        for idx, var in enumerate(getattr(self, "formation_player_vars", [])):
            if except_index is not None and idx == except_index:
                continue
            choice = var.get().strip()
            if " - " not in choice:
                continue
            try:
                selected.add(int(choice.split(" - ")[0]))
            except ValueError:
                pass
        return selected

    def available_formation_player_options(self, slot_index):
        selected = self.selected_formation_player_ids(except_index=slot_index)
        current = ""
        if slot_index < len(getattr(self, "formation_player_vars", [])):
            current = self.formation_player_vars[slot_index].get().strip()

        # Usa la cache invece di una query al DB
        all_options = self.player_options()
        values = [""]
        for item in all_options:
            if item == "Nessun giocatore":
                continue
            try:
                pid = int(item.split(" - ")[0])
            except ValueError:
                continue
            if pid not in selected or item == current:
                values.append(item)

        return values or [""]

    def refresh_formation_player_menus(self, except_index=None):
        for idx, menu in enumerate(getattr(self, "formation_player_menus", [])):
            if not menu:
                continue
            if except_index is not None and idx == except_index:
                continue  # Salta il menu che ha appena triggerato l'aggiornamento
            try:
                self.set_combo_values(menu, self.available_formation_player_options(idx))
            except Exception:
                pass


    def selected_substitution_player_ids(self, except_pair=None):
        selected = set()

        for idx, var in enumerate(getattr(self, "sub_out_vars", [])):
            if except_pair is not None and idx == except_pair:
                continue
            choice = var.get().strip()
            if " - " in choice:
                try:
                    selected.add(int(choice.split(" - ")[0]))
                except ValueError:
                    pass

        for idx, var in enumerate(getattr(self, "sub_in_vars", [])):
            if except_pair is not None and idx == except_pair:
                continue
            choice = var.get().strip()
            if " - " in choice:
                try:
                    selected.add(int(choice.split(" - ")[0]))
                except ValueError:
                    pass

        return selected

    def selected_substitution_player_ids_for_menu(self, pair_index, mode):
        selected = set()

        # Se sto costruendo una combo VERDE "Esce":
        # tolgo tutti quelli scelti nelle combo rosse "Entra"
        # e tutti quelli scelti nelle altre combo verdi.
        if mode == "out":
            for idx, var in enumerate(getattr(self, "sub_in_vars", [])):
                choice = var.get().strip()
                if " - " in choice:
                    try:
                        selected.add(int(choice.split(" - ")[0]))
                    except ValueError:
                        pass

            for idx, var in enumerate(getattr(self, "sub_out_vars", [])):
                if idx == pair_index:
                    continue
                choice = var.get().strip()
                if " - " in choice:
                    try:
                        selected.add(int(choice.split(" - ")[0]))
                    except ValueError:
                        pass

        # Se sto costruendo una combo ROSSA "Entra":
        # tolgo tutti quelli scelti nelle combo verdi "Esce"
        # e tutti quelli scelti nelle altre combo rosse.
        if mode == "in":
            for idx, var in enumerate(getattr(self, "sub_out_vars", [])):
                choice = var.get().strip()
                if " - " in choice:
                    try:
                        selected.add(int(choice.split(" - ")[0]))
                    except ValueError:
                        pass

            for idx, var in enumerate(getattr(self, "sub_in_vars", [])):
                if idx == pair_index:
                    continue
                choice = var.get().strip()
                if " - " in choice:
                    try:
                        selected.add(int(choice.split(" - ")[0]))
                    except ValueError:
                        pass

        return selected

    def available_substitution_player_options(self, pair_index, mode):
        selected = self.selected_substitution_player_ids_for_menu(pair_index, mode)

        current = ""
        if mode == "out" and pair_index < len(getattr(self, "sub_out_vars", [])):
            current = self.sub_out_vars[pair_index].get().strip()

        if mode == "in" and pair_index < len(getattr(self, "sub_in_vars", [])):
            current = self.sub_in_vars[pair_index].get().strip()

        # Usa la cache invece di una query al DB
        all_options = self.player_options()
        values = [""]
        for item in all_options:
            if item == "Nessun giocatore":
                continue
            try:
                pid = int(item.split(" - ")[0])
            except ValueError:
                continue
            if pid not in selected or item == current:
                values.append(item)

        return values

    def refresh_substitution_menus(self, except_pair=None):
        for idx, menu in enumerate(getattr(self, "sub_out_menus", [])):
            try:
                self.set_combo_values(menu, self.available_substitution_player_options(idx, "out"))
            except Exception:
                pass

        for idx, menu in enumerate(getattr(self, "sub_in_menus", [])):
            try:
                self.set_combo_values(menu, self.available_substitution_player_options(idx, "in"))
            except Exception:
                pass

    def build_player_slot(self, parent, slot_index, title, row, column, starter):
        while len(self.formation_player_vars) <= slot_index:
            self.formation_player_vars.append(tk.StringVar(value=""))
            self.formation_minutes_vars.append(tk.StringVar(value="0" if len(self.formation_minutes_vars) >= 11 else "90"))
            self.formation_goals_vars.append(tk.StringVar(value="0"))
            self.formation_assists_vars.append(tk.StringVar(value="0"))
            self.formation_yellow_vars.append(tk.StringVar(value="0"))
            self.formation_red_vars.append(tk.StringVar(value="0"))
            self.formation_player_menus.append(None)

        slot = ctk.CTkFrame(
            parent,
            fg_color="#ffffff" if starter else "#f8fafc",
            corner_radius=12,
            border_width=1,
            border_color=COLORS["border"],
        )
        slot.grid(row=row, column=column, padx=5, pady=5, sticky="nsew")

        ctk.CTkLabel(
            slot,
            text=title,
            text_color="#111827" if starter else "#111827",
            font=ctk.CTkFont(size=11, weight="bold")
        ).pack(anchor="w", padx=8, pady=(8, 3))

        menu = self.create_searchable_combo(
            slot,
            self.formation_player_vars[slot_index],
            self.available_formation_player_options(slot_index),
            width=190,
            bg="#166534" if starter else "#ffffff",
            fg="white" if starter else "#111827",
            command=lambda _choice, idx=slot_index: self.refresh_formation_player_menus(except_index=idx),
        )
        menu.pack(fill="x", padx=8, pady=(0, 5))

        while len(self.formation_player_menus) <= slot_index:
            self.formation_player_menus.append(None)
        self.formation_player_menus[slot_index] = menu

        stats = ctk.CTkFrame(slot, fg_color="transparent")
        stats.pack(fill="x", padx=8, pady=(0, 4))

        fields = [
            ("Min", self.formation_minutes_vars[slot_index], 43),
            ("G", self.formation_goals_vars[slot_index], 34),
            ("A", self.formation_assists_vars[slot_index], 34),
        ]

        for col, (label, var, width) in enumerate(fields):
            ctk.CTkLabel(
                stats,
                text=label,
                text_color="#111827",
                font=ctk.CTkFont(size=9, weight="bold")
            ).grid(row=0, column=col, padx=2, sticky="w")

            ctk.CTkEntry(
                stats,
                textvariable=var,
                width=width,
                height=26,
                fg_color="#ffffff",
                text_color="#111827",
                border_color=COLORS["border_light"],
                font=ctk.CTkFont(size=10)
            ).grid(row=1, column=col, padx=2, pady=(1, 0))

        cards_row = ctk.CTkFrame(slot, fg_color="transparent")
        cards_row.pack(fill="x", padx=8, pady=(0, 8))

        yellow_box = ctk.CTkCheckBox(
            cards_row,
            text="Amm",
            variable=self.formation_yellow_vars[slot_index],
            onvalue="1",
            offvalue="0",
            checkbox_width=16,
            checkbox_height=16,
            text_color="#111827",
            font=ctk.CTkFont(size=10, weight="bold")
        )
        yellow_box.pack(side="left", padx=(0, 8))

        red_box = ctk.CTkCheckBox(
            cards_row,
            text="Esp",
            variable=self.formation_red_vars[slot_index],
            onvalue="1",
            offvalue="0",
            checkbox_width=16,
            checkbox_height=16,
            text_color="#111827",
            font=ctk.CTkFont(size=10, weight="bold")
        )
        red_box.pack(side="left")

    def save_match(self):
        if not self.opponent_var.get().strip():
            messagebox.showerror("Errore", "Inserisci l'avversario.")
            return

        try:
            datetime.strptime(self.match_date_var.get(), "%d-%m-%y")
        except ValueError:
            messagebox.showerror("Errore", "Controlla la data della partita.")
            return

        db_query("""
            INSERT INTO matches (match_date, opponent, competition, home_away)
            VALUES (?, ?, ?, ?)
        """, (
            ui_to_db_date(self.match_date_var.get()), self.opponent_var.get().strip(),
            self.competition_var.get().strip(), self.home_away_var.get()
        ))
        self.show_dashboard()

    def refresh_matches(self):
        for tree_name in ["matches_tree_campionato", "matches_tree_coppa"]:
            tree = getattr(self, tree_name, None)
            if tree:
                for item in tree.get_children():
                    tree.delete(item)

        rows = db_query("""
            SELECT id, match_date, opponent, competition, home_away
            FROM matches
            ORDER BY match_date DESC, id DESC
        """, fetch=True)

        for match_id, match_date, opponent, competition, home_away in rows:
            values = (match_id, db_to_ui_date(match_date), opponent, home_away)

            if competition == "Coppa":
                tree = getattr(self, "matches_tree_coppa", None)
            else:
                tree = getattr(self, "matches_tree_campionato", None)

            if tree:
                tree.insert("", "end", values=values)

    def on_match_select(self, event):
        tree = event.widget if event is not None else None
        if tree is None:
            return

        sel = tree.selection()
        if not sel:
            return

        values = tree.item(sel[0], "values")
        self.selected_match_id = int(values[0])

    def delete_match(self):
        if not self.selected_match_id:
            messagebox.showinfo("Seleziona", "Seleziona una partita.")
            return

        if messagebox.askyesno("Conferma", "Eliminare la partita e i relativi convocati/voti?"):
            deleted_match_id = self.selected_match_id

            try:
                db_query("DELETE FROM matches WHERE id=?", (deleted_match_id,))
                compact_match_ids()
            except Exception as exc:
                messagebox.showerror("Errore eliminazione", f"Non sono riuscito a eliminare/ricompattare la partita:\n{exc}")
                return

            self.selected_match_id = None
            self.selected_appearance_id = None

            if hasattr(self, "formation_match_var"):
                self.formation_match_var.set("")
            if hasattr(self, "result_var"):
                self.result_var.set("")
            if hasattr(self, "selected_match_label"):
                self.selected_match_label.configure(text="Nessuna partita selezionata")

            try:
                self.refresh_matches()
            except Exception:
                pass

            try:
                self.refresh_appearances()
            except Exception:
                pass

            try:
                if hasattr(self, "formation_match_menu"):
                    self.formation_match_menu.configure(values=self.match_options())
            except Exception:
                pass

            try:
                self.clear_formation_slots()
            except Exception:
                pass

            try:
                self.show_matches()
            except Exception:
                self.show_dashboard()

    def clear_formation_slots(self):
        for var in getattr(self, "formation_player_vars", []):
            var.set("")
        for i, var in enumerate(getattr(self, "formation_minutes_vars", [])):
            var.set("90" if i < 11 else "0")
        for var in getattr(self, "formation_goals_vars", []):
            var.set("0")
        for var in getattr(self, "formation_assists_vars", []):
            var.set("0")
        for var in getattr(self, "formation_yellow_vars", []):
            var.set("0")
        for var in getattr(self, "formation_red_vars", []):
            var.set("0")
        if hasattr(self, "captain_var"):
            self.captain_var.set("")
        if hasattr(self, "vice_captain_var"):
            self.vice_captain_var.set("")
        self.refresh_formation_player_menus()
        self.refresh_substitution_menus()

    def load_formation_slots(self):
        self.clear_formation_slots()
        if not self.selected_match_id:
            return

        rows = db_query("""
            SELECT
                a.player_id,
                trim(p.first_name || ' ' || p.last_name),
                a.starter,
                a.minutes,
                a.goals,
                a.assists,
                a.yellow_cards,
                a.red_cards,
                COALESCE(a.captain, 0),
                COALESCE(a.vice_captain, 0)
            FROM appearances a
            JOIN players p ON p.id=a.player_id
            WHERE a.match_id=?
            ORDER BY a.starter DESC, p.last_name, p.first_name
        """, (self.selected_match_id,), fetch=True)

        starter_i = 0
        bench_i = 11

        for player_id, player_name, starter, minutes, goals, assists, yellow, red, captain, vice in rows:
            value = f"{player_id} - {player_name}"
            if starter and starter_i < 11:
                idx = starter_i
                starter_i += 1
            elif not starter and bench_i < 20:
                idx = bench_i
                bench_i += 1
            else:
                continue

            self.formation_player_vars[idx].set(value)
            self.formation_minutes_vars[idx].set(str(minutes or 90))
            self.formation_goals_vars[idx].set(str(goals or 0))
            self.formation_assists_vars[idx].set(str(assists or 0))
            self.formation_yellow_vars[idx].set(str(yellow or 0))
            self.formation_red_vars[idx].set(str(red or 0))

            if captain:
                self.captain_var.set(value)
            if vice:
                self.vice_captain_var.set(value)

        self.refresh_formation_player_menus()
        self.refresh_substitution_menus()

    def load_substitution_slots(self):
        if not hasattr(self, "sub_in_vars") or not self.selected_match_id:
            return

        for var in self.sub_in_vars + self.sub_out_vars:
            var.set("")

        rows = db_query("""
            SELECT
                s.slot,
                s.player_in_id,
                trim(pin.first_name || ' ' || pin.last_name),
                s.player_out_id,
                trim(pout.first_name || ' ' || pout.last_name)
            FROM substitutions s
            LEFT JOIN players pin ON pin.id=s.player_in_id
            LEFT JOIN players pout ON pout.id=s.player_out_id
            WHERE s.match_id=?
            ORDER BY s.slot
        """, (self.selected_match_id,), fetch=True)

        for slot, in_id, in_name, out_id, out_name in rows:
            idx = int(slot) - 1
            if 0 <= idx < 5:
                if out_id:
                    self.sub_out_vars[idx].set(f"{out_id} - {out_name}")
                if in_id:
                    self.sub_in_vars[idx].set(f"{in_id} - {in_name}")

        self.refresh_substitution_menus()

    def parse_result_goals(self):
        result = self.result_var.get().strip() if hasattr(self, "result_var") else ""
        if not result:
            messagebox.showerror("Errore", "Inserisci il risultato nella schermata Formazione.")
            return None

        clean = result.replace(" ", "")
        if "-" not in clean:
            messagebox.showerror("Errore", "Il risultato deve essere nel formato 2-1.")
            return None

        parts = clean.split("-")
        if len(parts) != 2:
            messagebox.showerror("Errore", "Il risultato deve essere nel formato 2-1.")
            return None

        try:
            left = int(parts[0])
            right = int(parts[1])
        except ValueError:
            messagebox.showerror("Errore", "Il risultato deve contenere solo numeri, esempio 2-1.")
            return None

        if left < 0 or right < 0:
            messagebox.showerror("Errore", "Il risultato non può avere numeri negativi.")
            return None

        match_info = db_query("SELECT home_away FROM matches WHERE id=?", (self.selected_match_id,), fetch=True)
        home_away = match_info[0][0] if match_info else "Casa"

        return left if home_away == "Casa" else right

    def save_formation_slots(self):
        if not self.selected_match_id:
            # prova a leggere la partita dalla combo se presente
            choice = self.formation_match_var.get().strip() if hasattr(self, "formation_match_var") else ""
            if choice and " - " in choice:
                try:
                    self.selected_match_id = int(choice.split(" - ")[0])
                except Exception:
                    self.selected_match_id = None

        if not self.selected_match_id:
            messagebox.showinfo("Seleziona", "Seleziona prima una partita dalla tabella Partite inserite.")
            return

        selected_players = []
        rows_to_save = []

        captain_choice = self.captain_var.get().strip() if hasattr(self, "captain_var") else ""
        vice_choice = self.vice_captain_var.get().strip() if hasattr(self, "vice_captain_var") else ""

        captain_id = None
        vice_id = None

        if captain_choice and " - " in captain_choice:
            try:
                captain_id = int(captain_choice.split(" - ")[0])
            except ValueError:
                captain_id = None

        if vice_choice and " - " in vice_choice:
            try:
                vice_id = int(vice_choice.split(" - ")[0])
            except ValueError:
                vice_id = None

        if captain_id and vice_id and captain_id == vice_id:
            messagebox.showerror("Errore", "Capitano e vice capitano devono essere due giocatori diversi.")
            return

        for idx, player_var in enumerate(self.formation_player_vars):
            choice = player_var.get().strip()

            if not choice or choice == "Nessun giocatore" or " - " not in choice:
                continue

            try:
                player_id = int(choice.split(" - ")[0])
                minutes = int(self.formation_minutes_vars[idx].get() or 0)
                goals = int(self.formation_goals_vars[idx].get() or 0)
                assists = int(self.formation_assists_vars[idx].get() or 0)
                yellow = int(self.formation_yellow_vars[idx].get() or 0)
                red = int(self.formation_red_vars[idx].get() or 0)
            except ValueError:
                messagebox.showerror("Errore", "Controlla minuti, gol, assist, ammonizioni ed espulsioni.")
                return

            if player_id in selected_players:
                messagebox.showerror("Errore", "Lo stesso giocatore è stato selezionato più volte.")
                return

            starter = 1 if idx < 11 else 0

            # Titolari: almeno 1 minuto. Panchinari: possono avere anche 0 minuti.
            if starter and (minutes < 1 or minutes > 130):
                messagebox.showerror("Errore", "I minuti dei titolari devono essere compresi tra 1 e 130.")
                return

            if not starter and (minutes < 0 or minutes > 130):
                messagebox.showerror("Errore", "I minuti dei panchinari devono essere compresi tra 0 e 130.")
                return

            if min(goals, assists, yellow, red) < 0:
                messagebox.showerror("Errore", "Gol, assist e cartellini non possono essere negativi.")
                return

            selected_players.append(player_id)
            is_captain = 1 if captain_id == player_id else 0
            is_vice = 1 if vice_id == player_id else 0

            rows_to_save.append((
                self.selected_match_id,
                player_id,
                starter,
                minutes,
                goals,
                assists,
                yellow,
                red,
                is_captain,
                is_vice
            ))

        if not rows_to_save:
            messagebox.showerror("Errore", "Non hai selezionato nessun giocatore nella distinta.")
            return

        if len(rows_to_save) > 20:
            messagebox.showerror("Errore", "Puoi inserire massimo 20 giocatori.")
            return

        expected_team_goals = self.parse_result_goals()
        if expected_team_goals is None:
            return

        total_player_goals = sum(row[4] for row in rows_to_save)
        if total_player_goals != expected_team_goals:
            messagebox.showerror(
                "Errore risultato",
                f"La somma dei gol dei giocatori è {total_player_goals}, ma dal risultato i gol squadra sono {expected_team_goals}."
            )
            return

        substitutions_to_save = []

        for idx in range(5):
            out_choice = self.sub_out_vars[idx].get().strip() if idx < len(self.sub_out_vars) else ""
            in_choice = self.sub_in_vars[idx].get().strip() if idx < len(self.sub_in_vars) else ""

            if not out_choice and not in_choice:
                continue

            if " - " not in out_choice or " - " not in in_choice:
                messagebox.showerror("Errore sostituzioni", f"Completa entrato e uscito nella sostituzione {idx + 1}.")
                return

            try:
                out_id = int(out_choice.split(" - ")[0])
                in_id = int(in_choice.split(" - ")[0])
            except ValueError:
                messagebox.showerror("Errore sostituzioni", f"Sostituzione {idx + 1} non valida.")
                return

            if out_id == in_id:
                messagebox.showerror("Errore sostituzioni", f"Nella sostituzione {idx + 1} entrato e uscito devono essere diversi.")
                return

            already_in_players = [s[2] for s in substitutions_to_save]
            already_out_players = [s[3] for s in substitutions_to_save]

            if in_id in already_out_players:
                messagebox.showerror(
                    "Errore sostituzioni",
                    "Un giocatore entrato non può essere selezionato successivamente come giocatore uscito."
                )
                return

            if out_id in already_in_players:
                messagebox.showerror(
                    "Errore sostituzioni",
                    "Un giocatore entrato non può essere selezionato come giocatore da sostituire."
                )
                return

            if out_id not in selected_players or in_id not in selected_players:
                messagebox.showerror("Errore sostituzioni", f"I giocatori della sostituzione {idx + 1} devono essere presenti in distinta.")
                return

            substitutions_to_save.append((self.selected_match_id, idx + 1, in_id, out_id))

        if captain_id and captain_id not in selected_players:
            messagebox.showerror("Errore", "Il capitano deve essere tra i giocatori inseriti in distinta.")
            return

        if vice_id and vice_id not in selected_players:
            messagebox.showerror("Errore", "Il vice capitano deve essere tra i giocatori inseriti in distinta.")
            return

        # Salvataggio PostgreSQL in transazione unica — usa il pool invece di una connessione diretta.
        conn = DB_POOL.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE matches SET result=%s WHERE id=%s",
                    (self.result_var.get().strip(), self.selected_match_id)
                )

                cur.execute(
                    "DELETE FROM substitutions WHERE match_id=%s",
                    (self.selected_match_id,)
                )

                cur.execute(
                    "DELETE FROM appearances WHERE match_id=%s",
                    (self.selected_match_id,)
                )

                if substitutions_to_save:
                    execute_batch(cur, """
                        INSERT INTO substitutions (match_id, slot, player_in_id, player_out_id)
                        VALUES (%s, %s, %s, %s)
                    """, substitutions_to_save)

                execute_batch(cur, """
                    INSERT INTO appearances
                    (match_id, player_id, starter, minutes, goals, assists, yellow_cards, red_cards, captain, vice_captain)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, rows_to_save)

                cur.execute(
                    "SELECT COUNT(*), COALESCE(SUM(CASE WHEN minutes > 10 THEN 1 ELSE 0 END), 0) FROM appearances WHERE match_id=%s",
                    (self.selected_match_id,)
                )
                saved_count, over_10_count = cur.fetchone()

            conn.commit()

        except Exception as exc:
            conn.rollback()
            messagebox.showerror("Errore salvataggio", f"Non sono riuscito a salvare la distinta su PostgreSQL:\n{exc}")
            return
        finally:
            DB_POOL.putconn(conn)

        self.refresh_appearances()
        self.load_formation_slots()

        messagebox.showinfo(
            "Salvato",
            f"Distinta salvata correttamente.\n\nGiocatori salvati: {saved_count}\nGiocatori con più di 10 minuti: {over_10_count}"
        )

    def save_appearance(self):
        self.save_formation_slots()

    def refresh_appearances(self):
        for item in self.appearances_tree.get_children():
            self.appearances_tree.delete(item)
        if not self.selected_match_id:
            return
        rows = db_query("""
            SELECT
                a.id,
                trim(p.first_name || ' ' || p.last_name),
                CASE WHEN a.starter=1 THEN 'Sì' ELSE 'No' END,
                a.minutes,
                a.goals,
                a.assists,
                a.yellow_cards,
                a.red_cards,
                CASE
                    WHEN COALESCE(a.captain, 0)=1 THEN 'C'
                    WHEN COALESCE(a.vice_captain, 0)=1 THEN 'VC'
                    ELSE ''
                END
            FROM appearances a
            JOIN players p ON p.id=a.player_id
            WHERE a.match_id=?
            ORDER BY a.starter DESC, p.last_name, p.first_name
        """, (self.selected_match_id,), fetch=True)
        for row in rows:
            self.appearances_tree.insert("", "end", values=row)

    def on_appearance_select(self, _event):
        sel = self.appearances_tree.selection()
        if not sel:
            return
        values = self.appearances_tree.item(sel[0], "values")
        self.selected_appearance_id = values[0]

    def delete_appearance(self):
        if not self.selected_appearance_id:
            messagebox.showinfo("Seleziona", "Seleziona un convocato.")
            return
        db_query("DELETE FROM appearances WHERE id=?", (self.selected_appearance_id,))
        self.selected_appearance_id = None
        self.refresh_appearances()
        self.load_formation_slots()

    # ---------------- STATISTICHE ----------------

    def stats_dates_vars(self):
        start, end = default_dates()
        self.stat_start = getattr(self, "stat_start", tk.StringVar(value=start.strftime("%d-%m-%y")))
        self.stat_end = getattr(self, "stat_end", tk.StringVar(value=end.strftime("%d-%m-%y")))
        self.stat_competition = getattr(self, "stat_competition", tk.StringVar(value="Totale"))

    def competition_filter_sql(self, alias="m"):
        competition = self.stat_competition.get() if hasattr(self, "stat_competition") else "Totale"

        if competition == "Campionato":
            return f" AND {alias}.competition = ? ", ["Campionato"]

        if competition == "Coppa":
            return f" AND {alias}.competition = ? ", ["Coppa"]

        return "", []

    def stat_dates(self):
        try:
            start = datetime.strptime(self.stat_start.get(), "%d-%m-%y").date()
            end = datetime.strptime(self.stat_end.get(), "%d-%m-%y").date()
        except ValueError:
            messagebox.showerror("Errore", "Date non valide.")
            return None, None
        if end < start:
            start, end = end, start
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    def stats_filters(self, page, refresh_command):
        self.stats_dates_vars()
        filters = self.card(page, row=1, column=0, sticky="ew", padx=24, pady=(0, 14))
        ctk.CTkLabel(filters, text="Periodo da", text_color=COLORS["muted"], font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, padx=(16, 6), pady=14)
        self.date_picker_row(filters, self.stat_start).grid(row=0, column=1, padx=6)
        ctk.CTkLabel(filters, text="a", text_color=COLORS["muted"], font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=2, padx=6)
        self.date_picker_row(filters, self.stat_end).grid(row=0, column=3, padx=6)

        ctk.CTkLabel(
            filters,
            text="Competizione",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=0, column=4, padx=(12, 6), pady=14)

        ctk.CTkOptionMenu(
            filters,
            values=["Totale", "Campionato", "Coppa"],
            variable=self.stat_competition,
            height=36,
            width=140,
            fg_color=COLORS["gold_dark"]
        ).grid(row=0, column=5, padx=6, pady=14)

        ctk.CTkButton(filters, text="Applica filtro", fg_color=COLORS["red"], command=refresh_command).grid(row=0, column=6, padx=12)
        ctk.CTkButton(filters, text="Export CSV", fg_color=COLORS["gold_dark"], command=self.export_current_csv).grid(row=0, column=7, padx=6)

    def stats_page(self, nav, title, subtitle, columns, headers, widths, query_builder):
        self.set_active_nav(nav)
        self.clear_main()
        page = self.page_container()
        self.header(page, title, subtitle)
        self.stats_filters(page, lambda: self.refresh_stats_table(query_builder))

        table = self.card(page, row=2, column=0, sticky="nsew", padx=24, pady=(0, 24))
        self.stats_tree = self.create_tree(table, columns, headers, widths)
        self.current_query_builder = query_builder
        self.refresh_stats_table(query_builder)

    def _populate_tree(self, tree, rows):
        """Popola un Treeview in blocco: stacca il widget dal gestore prima degli insert
        per evitare un ridisegno per ogni riga (speedup netto su tabelle grandi)."""
        parent = tree.master
        tree.detach(*tree.get_children())
        try:
            tree.delete(*tree.get_children())
        except Exception:
            pass
        for row in rows:
            tree.insert("", "end", values=row)
        try:
            tree.tk.call(parent, "add", tree, {})
        except Exception:
            pass

    def refresh_stats_table(self, query_builder):
        start, end = self.stat_dates()
        if not start:
            return
        for item in self.stats_tree.get_children():
            self.stats_tree.delete(item)
        query, params = query_builder(start, end)
        for row in db_query(query, params, fetch=True):
            self.stats_tree.insert("", "end", values=row)

    def goals_assists_builder(self, start, end):
        comp_sql, comp_params = self.competition_filter_sql("m")
        comp_sql_cs, comp_params_cs = self.competition_filter_sql("mcs")

        # Sostituisce la subquery correlata (eseguita per ogni riga) con una JOIN aggregata laterale
        # che il planner può ottimizzare come un'unica scansione.
        query = f"""
            WITH clean_sheet_counts AS (
                SELECT acs.player_id, COUNT(*) AS cs
                FROM appearances acs
                JOIN matches mcs ON mcs.id = acs.match_id
                WHERE acs.minutes > 0
                  AND mcs.match_date BETWEEN ? AND ?
                  {comp_sql_cs}
                  AND (
                    (mcs.home_away = 'Casa' AND CAST(substr(mcs.result, POSITION('-' IN mcs.result) + 1) AS INTEGER) = 0)
                    OR
                    (mcs.home_away = 'Fuori' AND CAST(substr(mcs.result, 1, POSITION('-' IN mcs.result) - 1) AS INTEGER) = 0)
                  )
                GROUP BY acs.player_id
            )
            SELECT
                trim(p.first_name || ' ' || p.last_name),
                COALESCE(SUM(a.goals), 0),
                COALESCE(SUM(a.assists), 0),
                COALESCE(cs.cs, 0),
                COALESCE(SUM(a.goals + a.assists), 0)
            FROM players p
            LEFT JOIN appearances a ON a.player_id = p.id
            LEFT JOIN matches m ON m.id = a.match_id
            LEFT JOIN clean_sheet_counts cs ON cs.player_id = p.id
            WHERE ((m.match_date BETWEEN ? AND ? {comp_sql}) OR m.match_date IS NULL)
              AND LOWER(TRIM(COALESCE(p.role, ''))) NOT IN ('mister', 'pres')
            GROUP BY p.id, cs.cs
            ORDER BY COALESCE(SUM(a.goals), 0) DESC,
                     COALESCE(SUM(a.assists), 0) DESC,
                     p.last_name, p.first_name
        """

        params = [start, end] + comp_params_cs + [start, end] + comp_params
        return query, tuple(params)

    def show_goals_assists(self):
        self.set_active_nav("Gol & Assist")
        self.clear_main()
        page = self.page_container()
        self.header(page, "⚽  Classifica marcatori", "Capocannonieri e migliori assist-man filtrati per periodo.")

        self.stats_filters(page, self.refresh_goals_assists_page)

        table = self.card(page, row=2, column=0, sticky="nsew", padx=24, pady=(0, 14))

        # Colonna posizione + colonne principali
        columns = ("pos", "player", "goals", "assists", "clean_sheets", "total")
        headers = {
            "pos":          "#",
            "player":       "Giocatore",
            "goals":        "⚽  Gol",
            "assists":      "🅰  Assist",
            "clean_sheets": "🧤  Clean Sheet",
            "total":        "★  G+A",
        }
        widths = {"pos": 50, "player": 260, "goals": 90, "assists": 90, "clean_sheets": 120, "total": 100}
        self.stats_tree = self.create_tree(table, columns, headers, widths, style_name="Scorer.Treeview")

        # Allineamento: posizione centrata, giocatore a sinistra, numeri centrati
        self.stats_tree.column("pos",          anchor="center", width=50,  stretch=False)
        self.stats_tree.column("player",       anchor="w",      width=260, stretch=True)
        self.stats_tree.column("goals",        anchor="center", width=90,  stretch=False)
        self.stats_tree.column("assists",      anchor="center", width=90,  stretch=False)
        self.stats_tree.column("clean_sheets", anchor="center", width=120, stretch=False)
        self.stats_tree.column("total",        anchor="center", width=100, stretch=False)

        # Ordinamento per colonna: default goals desc (invariato)
        # _bonus_sort_col: colonna corrente | _bonus_sort_asc: True=asc False=desc
        self._bonus_sort_col = "goals"
        self._bonus_sort_asc = False
        self._bonus_rows_cache = []   # cache righe DB (player, goals, assists, cs, total)

        def _sort_bonus(col):
            if col == "pos":
                return  # colonna posizione non ordinabile
            if self._bonus_sort_col == col:
                self._bonus_sort_asc = not self._bonus_sort_asc
            else:
                self._bonus_sort_col = col
                self._bonus_sort_asc = False  # prima sempre desc
            self._render_bonus_rows(self._bonus_rows_cache)

        for col in columns:
            self.stats_tree.heading(col, command=lambda c=col: _sort_bonus(c))

        # Tag podio
        self.stats_tree.tag_configure("gold",   background="#3d2e00", foreground=COLORS["gold_light"], font=("Segoe UI", 11, "bold"))
        self.stats_tree.tag_configure("silver", background="#252525", foreground="#c0c0c0",             font=("Segoe UI", 11, "bold"))
        self.stats_tree.tag_configure("bronze", background="#2b1a00", foreground="#cd7f32",             font=("Segoe UI", 11, "bold"))
        self.stats_tree.tag_configure("odd",    background="#1c3520", foreground=COLORS["text"])
        self.stats_tree.tag_configure("even",   background="#162b19", foreground=COLORS["text"])

        charts = ctk.CTkFrame(page, fg_color=COLORS["bg"])
        charts.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 24))
        charts.grid_columnconfigure(0, weight=1)
        charts.grid_columnconfigure(1, weight=1)
        charts.grid_columnconfigure(2, weight=1)

        self.top_goals_card = self.card(charts, row=0, column=0, sticky="nsew", padx=(0, 7), pady=0)
        self.top_assists_card = self.card(charts, row=0, column=1, sticky="nsew", padx=7, pady=0)
        self.top_clean_sheets_card = self.card(charts, row=0, column=2, sticky="nsew", padx=(7, 0), pady=0)

        self.current_query_builder = self.goals_assists_builder
        self.refresh_goals_assists_page()

    def refresh_goals_assists_page(self):
        start, end = self.stat_dates()
        if not start:
            return

        query, params = self.goals_assists_builder(start, end)
        rows = db_query(query, params, fetch=True)

        # Salva in cache e ripristina ordinamento default (gol desc)
        self._bonus_rows_cache = list(rows)
        self._bonus_sort_col = "goals"
        self._bonus_sort_asc = False
        self._render_bonus_rows(self._bonus_rows_cache)

        self.draw_top5_chart(self.top_goals_card,        rows, metric_index=1, title="Top 5 marcatori",   ylabel="Gol",         bar_color=COLORS["gold_light"])
        self.draw_top5_chart(self.top_assists_card,      rows, metric_index=2, title="Top 5 assist",      ylabel="Assist",      bar_color=COLORS["green_bright"])
        self.draw_top5_chart(self.top_clean_sheets_card, rows, metric_index=3, title="Top 5 clean sheet", ylabel="Clean sheet", bar_color=COLORS["blue"])

    def _render_bonus_rows(self, rows):
        """Ordina e ridisegna la tabella Bonus in base a _bonus_sort_col/_bonus_sort_asc."""
        # Mappa colonna -> indice nella riga DB (player, goals, assists, clean_sheets, total)
        COL_INDEX = {"player": 0, "goals": 1, "assists": 2, "clean_sheets": 3, "total": 4}
        idx = COL_INDEX.get(self._bonus_sort_col, 1)

        def sort_key(r):
            v = r[idx]
            if self._bonus_sort_col == "player":
                return str(v or "").lower()
            try:
                return float(v or 0)
            except (ValueError, TypeError):
                return 0.0

        sorted_rows = sorted(rows, key=sort_key, reverse=not self._bonus_sort_asc)

        for item in self.stats_tree.get_children():
            self.stats_tree.delete(item)

        # Medaglie podio: solo quando si e' in ordinamento default gol desc
        MEDALS = ["🥇", "🥈", "🥉"]
        PODIUM_TAGS = ["gold", "silver", "bronze"]

        is_default_sort = (self._bonus_sort_col == "goals" and not self._bonus_sort_asc)
        podium_idx = 0
        for i, row in enumerate(sorted_rows):
            goals_val = int(row[1] or 0)

            if is_default_sort and podium_idx < 3 and goals_val > 0:
                pos_display = MEDALS[podium_idx]
                tag = PODIUM_TAGS[podium_idx]
                podium_idx += 1
            else:
                pos_display = str(i + 1)
                tag = "odd" if i % 2 == 0 else "even"

            display_row = (pos_display,) + tuple(row)
            self.stats_tree.insert("", "end", values=display_row, tags=(tag,))

    def draw_top5_chart(self, parent, rows, metric_index, title, ylabel, bar_color=None):
        for child in parent.winfo_children():
            child.destroy()

        if bar_color is None:
            bar_color = COLORS["gold_light"]

        ctk.CTkLabel(
            parent, text=title, text_color=COLORS["text"],
            font=ctk.CTkFont(size=15, weight="bold")
        ).pack(anchor="w", padx=16, pady=(14, 4))

        filtered = [(r[0], int(r[metric_index] or 0)) for r in rows if int(r[metric_index] or 0) > 0]
        filtered = sorted(filtered, key=lambda x: x[1], reverse=True)[:5]

        BG      = "#162b19"
        AXES_BG = "#1c3520"
        TEXT_C  = "#c8d8c8"
        GRID_C  = "#2a4a2e"

        fig = Figure(figsize=(5.2, 3.4), dpi=100, facecolor=BG)
        ax  = fig.add_subplot(111, facecolor=AXES_BG)

        if filtered:
            labels = [x[0].split()[-1] for x in filtered]   # cognome per brevità
            values = [x[1] for x in filtered]

            # Grafico orizzontale: più leggibile con nomi lunghi
            y_pos = range(len(labels))
            bars  = ax.barh(list(y_pos), values, color=bar_color, height=0.6,
                            edgecolor="none")

            # Gradient opacity: primo più pieno, ultimi più trasparenti
            for idx, bar in enumerate(bars):
                bar.set_alpha(max(0.45, 1.0 - idx * 0.12))

            # Etichette valore a destra delle barre
            for bar, val in zip(bars, values):
                ax.text(bar.get_width() + max(values) * 0.02, bar.get_y() + bar.get_height() / 2,
                        str(val), va="center", ha="left",
                        color=bar_color, fontsize=10, fontweight="bold")

            ax.set_yticks(list(y_pos))
            ax.set_yticklabels(labels, fontsize=9, color=TEXT_C)
            ax.invert_yaxis()
            ax.xaxis.set_visible(False)
            ax.spines[:].set_visible(False)
            ax.tick_params(axis="y", length=0)
            ax.set_xlim(0, max(values) * 1.25)
            ax.grid(False)
        else:
            ax.text(0.5, 0.5, "Nessun dato", ha="center", va="center",
                    color="#7a9a7e", fontsize=11, transform=ax.transAxes)
            ax.set_axis_off()

        fig.tight_layout(pad=1.4)
        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.get_tk_widget().configure(bg=BG, highlightthickness=0)
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=(0, 12))
        canvas.draw()

    def show_cards(self):
        columns = ("player", "yellow", "red", "total")
        headers = {"player": "Giocatore", "yellow": "Ammonizioni", "red": "Espulsioni", "total": "Totale"}
        widths = {"player": 300, "yellow": 130, "red": 130, "total": 120}

        def builder(start, end):
            comp_sql, comp_params = self.competition_filter_sql("m")
            query = f"""
                SELECT
                    trim(p.first_name || ' ' || p.last_name),
                    COALESCE(SUM(a.yellow_cards),0),
                    COALESCE(SUM(a.red_cards),0),
                    COALESCE(SUM(a.yellow_cards+a.red_cards),0)
                FROM players p
                LEFT JOIN appearances a ON a.player_id=p.id
                LEFT JOIN matches m ON m.id=a.match_id
                WHERE ((m.match_date BETWEEN ? AND ? {comp_sql}) OR m.match_date IS NULL)
                  AND LOWER(TRIM(COALESCE(p.role, ''))) NOT IN ('mister', 'pres')
                GROUP BY p.id
                ORDER BY COALESCE(SUM(a.yellow_cards+a.red_cards),0) DESC,
                         p.last_name, p.first_name
            """
            return query, tuple([start, end] + comp_params)

        self.stats_page("Cartellini", "Cartellini", "Conteggio ammonizioni ed espulsioni per giocatore.", columns, headers, widths, builder)

    def show_minutes(self):
        columns = ("player", "apps", "starts", "minutes")
        headers = {"player": "Giocatore", "apps": "Presenze", "starts": "Titolare", "minutes": "Minuti"}
        widths = {"player": 300, "apps": 120, "starts": 120, "minutes": 160}

        def builder(start, end):
            comp_sql, comp_params = self.competition_filter_sql("m")
            query = f"""
                SELECT
                    trim(p.first_name || ' ' || p.last_name),
                    COUNT(a.id),
                    COALESCE(SUM(a.starter),0),
                    COALESCE(SUM(a.minutes),0)
                FROM players p
                LEFT JOIN appearances a ON a.player_id=p.id
                LEFT JOIN matches m ON m.id=a.match_id
                WHERE ((m.match_date BETWEEN ? AND ? {comp_sql}) OR m.match_date IS NULL)
                  AND LOWER(TRIM(COALESCE(p.role, ''))) NOT IN ('mister', 'pres')
                GROUP BY p.id
                ORDER BY COALESCE(SUM(a.minutes),0) DESC,
                         p.last_name, p.first_name
            """
            return query, tuple([start, end] + comp_params)

        self.stats_page("Minuti", "Minuti giocati", "Minutaggio, presenze e titolarità per ogni giocatore.", columns, headers, widths, builder)

    def show_advanced_stats(self):
        columns = ("player", "apps", "starts", "subbed_in", "subbed_out", "minutes", "goals", "assists", "ga90", "cards")
        headers = {
            "player": "Giocatore",
            "apps": "Presenze",
            "starts": "Titolare",
            "subbed_in": "Subentrato",
            "subbed_out": "Sostituito",
            "minutes": "Minuti",
            "goals": "Gol",
            "assists": "Assist",
            "ga90": "G+A/90",
            "cards": "Cartellini"
        }
        widths = {
            "player": 220,
            "apps": 85,
            "starts": 85,
            "subbed_in": 95,
            "subbed_out": 95,
            "minutes": 95,
            "goals": 75,
            "assists": 75,
            "ga90": 85,
            "cards": 95
        }

        def builder(start, end):
            comp_sql, comp_params = self.competition_filter_sql("m")
            comp_sql_ms, comp_params_ms = self.competition_filter_sql("ms")

            query = f"""
                SELECT
                    trim(p.first_name || ' ' || p.last_name),
                    COUNT(a.id),
                    COALESCE(SUM(a.starter),0),
                    COALESCE((
                        SELECT COUNT(*)
                        FROM substitutions s
                        JOIN matches ms ON ms.id=s.match_id
                        WHERE s.player_in_id=p.id
                          AND ms.match_date BETWEEN ? AND ?
                          {comp_sql_ms}
                    ),0),
                    COALESCE((
                        SELECT COUNT(*)
                        FROM substitutions s
                        JOIN matches ms ON ms.id=s.match_id
                        WHERE s.player_out_id=p.id
                          AND ms.match_date BETWEEN ? AND ?
                          {comp_sql_ms}
                    ),0),
                    COALESCE(SUM(a.minutes),0),
                    COALESCE(SUM(a.goals),0),
                    COALESCE(SUM(a.assists),0),
                    ROUND(CASE WHEN COALESCE(SUM(a.minutes),0) > 0
                        THEN (SUM(a.goals+a.assists)*90.0)/SUM(a.minutes)
                        ELSE 0 END, 2),
                    COALESCE(SUM(a.yellow_cards+a.red_cards),0)
                FROM players p
                LEFT JOIN appearances a ON a.player_id=p.id
                LEFT JOIN matches m ON m.id=a.match_id
                WHERE ((m.match_date BETWEEN ? AND ? {comp_sql}) OR m.match_date IS NULL)
                  AND LOWER(TRIM(COALESCE(p.role, ''))) NOT IN ('mister', 'pres')
                GROUP BY p.id
                ORDER BY ROUND(CASE WHEN COALESCE(SUM(a.minutes),0) > 0
                        THEN (SUM(a.goals+a.assists)*90.0)/SUM(a.minutes)
                        ELSE 0 END, 2) DESC,
                         p.last_name, p.first_name
            """

            params = [start, end] + comp_params_ms + [start, end] + comp_params_ms + [start, end] + comp_params
            return query, tuple(params)

        self.stats_page("Statistiche +", "Statistiche avanzate", "Presenze, minuti, gol, assist, G+A per 90 minuti e cartellini.", columns, headers, widths, builder)

    # ---------------- ALLENAMENTI ----------------

    def training_player_options(self):
        if not self.selected_training_id:
            return self.player_options()

        rows = db_query("""
            SELECT id, first_name, last_name
            FROM players
            WHERE LOWER(TRIM(COALESCE(role, ''))) NOT IN ('mister', 'pres')
              AND id NOT IN (
                SELECT player_id
                FROM training_attendance
                WHERE session_id=?
            )
            ORDER BY last_name, first_name
        """, (self.selected_training_id,), fetch=True)

        return [f"{r[0]} - {r[2]} {r[1]}".strip() for r in rows] or ["Nessun giocatore disponibile"]

    def refresh_training_player_menu(self):
        menu = getattr(self, "training_player_menu", None)
        if not menu:
            return

        values = self.training_player_options()
        try:
            menu.configure(values=values)
            self.training_player_var.set("" if values and values[0] != "Nessun giocatore disponibile" else values[0])
        except Exception:
            pass

    def show_training(self):
        self.set_active_nav("Allenamenti")
        self.clear_main()
        page = self.page_container()
        self.header(page, "Allenamenti", "Gestione presenze: seleziona presenti e infortunati dalla lista completa giocatori.")

        top = ctk.CTkFrame(page, fg_color=COLORS["bg"])
        top.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 14))
        top.grid_columnconfigure(0, weight=1)
        top.grid_columnconfigure(1, weight=2)

        session_card = self.card(top, row=0, column=0, sticky="nsew", padx=(0, 8), pady=0)
        ctk.CTkLabel(
            session_card,
            text="Allenamento",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=18, pady=(16, 8))

        self.training_date_var = tk.StringVar(value=date.today().strftime("%d-%m-%y"))
        self.training_title_var = tk.StringVar(value="Allenamento")
        self.training_notes_var = tk.StringVar()

        self.form_field(session_card, "Data", self.date_picker_row(session_card, self.training_date_var))
        self.form_field(session_card, "Titolo", ctk.CTkEntry(session_card, textvariable=self.training_title_var, height=36, text_color="#111827"))
        self.form_field(session_card, "Note", ctk.CTkEntry(session_card, textvariable=self.training_notes_var, height=36, text_color="#111827"))

        training_buttons = ctk.CTkFrame(session_card, fg_color=COLORS["card"])
        training_buttons.pack(fill="x", padx=18, pady=(10, 18))

        ctk.CTkButton(
            training_buttons,
            text="Salva nuovo allenamento",
            fg_color=COLORS["red"],
            command=self.save_training
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            training_buttons,
            text="Elimina allenamento",
            fg_color=COLORS["red_dark"],
            command=self.delete_training
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        attendance_card = self.card(top, row=0, column=1, sticky="nsew", padx=(8, 0), pady=0)
        ctk.CTkLabel(
            attendance_card,
            text="Presenze giocatori",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=18, pady=(16, 4))

        self.training_selected_label = ctk.CTkLabel(
            attendance_card,
            text="Seleziona un allenamento dalla tabella sotto, oppure creane uno nuovo.",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.training_selected_label.pack(anchor="w", padx=18, pady=(0, 10))

        legend = ctk.CTkFrame(attendance_card, fg_color=COLORS["card"])
        legend.pack(fill="x", padx=18, pady=(0, 8))
        ctk.CTkLabel(
            legend,
            text="✓ Presente   |   🩹 Infortunato   |   nessuna selezione = Assente",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12, weight="bold")
        ).pack(anchor="w")

        players_box = ctk.CTkScrollableFrame(
            attendance_card,
            fg_color="#f8fafc",
            corner_radius=12,
            height=360
        )
        players_box.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        players_box.grid_columnconfigure(0, weight=1)

        self.training_attendance_vars = {}
        self.training_attendance_rows = []
        self.training_attendance_refreshers = []

        players = db_query("""
            SELECT id, first_name, last_name, role
            FROM players
            WHERE LOWER(TRIM(COALESCE(role, ''))) NOT IN ('mister', 'pres')
            ORDER BY last_name, first_name
        """, fetch=True)

        if not players:
            ctk.CTkLabel(
                players_box,
                text="Nessun giocatore inserito.",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=13)
            ).grid(row=0, column=0, padx=12, pady=12, sticky="w")
        else:
            header = ctk.CTkFrame(players_box, fg_color="#e5e7eb", corner_radius=8)
            header.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))
            header.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                header,
                text="Giocatore",
                text_color="#111827",
                font=ctk.CTkFont(size=12, weight="bold")
            ).grid(row=0, column=0, sticky="w", padx=10, pady=6)

            ctk.CTkLabel(
                header,
                text="Presente",
                text_color="#111827",
                font=ctk.CTkFont(size=12, weight="bold")
            ).grid(row=0, column=1, padx=12, pady=6)

            ctk.CTkLabel(
                header,
                text="Infortunato",
                text_color="#111827",
                font=ctk.CTkFont(size=12, weight="bold")
            ).grid(row=0, column=2, padx=12, pady=6)

            for idx, (player_id, first_name, last_name, role) in enumerate(players, start=1):
                var = tk.IntVar(value=0)
                self.training_attendance_vars[player_id] = var

                row = ctk.CTkFrame(
                    players_box,
                    fg_color="#ffffff" if idx % 2 else "#f1f5f9",
                    corner_radius=8
                )
                row.grid(row=idx, column=0, sticky="ew", padx=6, pady=2)
                row.grid_columnconfigure(0, weight=1)

                name = f"{last_name} {first_name}".strip()
                role_txt = f"  ·  {role}" if role else ""

                ctk.CTkLabel(
                    row,
                    text=f"{name}{role_txt}",
                    text_color="#111827",
                    font=ctk.CTkFont(size=12, weight="bold")
                ).grid(row=0, column=0, sticky="w", padx=10, pady=7)

                present_btn = ctk.CTkButton(
                    row,
                    text="",
                    width=22,
                    height=22,
                    corner_radius=11,
                    fg_color="#d1d5db",
                    hover_color=COLORS["green"]
                )
                present_btn.grid(row=0, column=1, padx=28, pady=7)

                injured_btn = ctk.CTkButton(
                    row,
                    text="",
                    width=22,
                    height=22,
                    corner_radius=11,
                    fg_color="#d1d5db",
                    hover_color=COLORS["red"]
                )
                injured_btn.grid(row=0, column=2, padx=36, pady=7)

                # Importante: uso parametri di default per evitare che tutti i pulsanti
                # controllino l'ultimo giocatore della lista.
                def refresh_buttons(v=var, p_btn=present_btn, i_btn=injured_btn):
                    current = v.get()

                    if current == 1:
                        p_btn.configure(fg_color=COLORS["green"])
                        i_btn.configure(fg_color="#d1d5db")
                    elif current == 2:
                        p_btn.configure(fg_color="#d1d5db")
                        i_btn.configure(fg_color=COLORS["red"])
                    else:
                        p_btn.configure(fg_color="#d1d5db")
                        i_btn.configure(fg_color="#d1d5db")

                def toggle_present(v=var, refresh=refresh_buttons):
                    if v.get() == 1:
                        v.set(0)
                    else:
                        v.set(1)
                    refresh()

                def toggle_injured(v=var, refresh=refresh_buttons):
                    if v.get() == 2:
                        v.set(0)
                    else:
                        v.set(2)
                    refresh()

                present_btn.configure(command=toggle_present)
                injured_btn.configure(command=toggle_injured)

                self.training_attendance_refreshers.append(refresh_buttons)
                refresh_buttons()

                self.training_attendance_rows.append(row)

        action_row = ctk.CTkFrame(attendance_card, fg_color=COLORS["card"])
        action_row.pack(fill="x", padx=18, pady=(0, 18))

        ctk.CTkButton(
            action_row,
            text="Salva presenze lista",
            fg_color=COLORS["red"],
            hover_color=COLORS["gold"],
            command=self.save_training_attendance_list
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            action_row,
            text="Svuota selezioni",
            fg_color=COLORS["gold_dark"],
            command=self.clear_training_attendance_selection
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        bottom_grid = ctk.CTkFrame(page, fg_color=COLORS["bg"])
        bottom_grid.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 24))
        bottom_grid.grid_columnconfigure(0, weight=2)
        bottom_grid.grid_columnconfigure(1, weight=1)

        bottom = self.card(bottom_grid, row=0, column=0, sticky="nsew", padx=(0, 8), pady=0)
        ctk.CTkLabel(
            bottom,
            text="Allenamenti",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=18, pady=(16, 8))

        columns = ("id", "date", "title", "present", "absent", "injured", "total", "percentage")
        headers = {
            "id": "ID",
            "date": "Data",
            "title": "Allenamento",
            "present": "Presenti",
            "absent": "Assenti",
            "injured": "Infortunati",
            "total": "Totale",
            "percentage": "Presenza %"
        }
        widths = {"id": 50, "date": 100, "title": 220, "present": 80, "absent": 80, "injured": 100, "total": 70, "percentage": 100}
        self.training_tree = self.create_tree(bottom, columns, headers, widths)
        self.training_tree.bind("<<TreeviewSelect>>", self.on_training_select)

        ranking = self.card(bottom_grid, row=0, column=1, sticky="nsew", padx=(8, 0), pady=0)
        ctk.CTkLabel(
            ranking,
            text="Classifica allenamenti",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=18, pady=(16, 8))

        ranking_columns = ("player", "present", "absent", "injured", "total")
        ranking_headers = {
            "player": "Giocatore",
            "present": "Presenze",
            "absent": "Assenze",
            "injured": "Infortunato",
            "total": "Totale"
        }
        ranking_widths = {"player": 190, "present": 80, "absent": 75, "injured": 85, "total": 65}
        self.training_ranking_tree = self.create_tree(ranking, ranking_columns, ranking_headers, ranking_widths)

        self.refresh_training()

    def clear_training_attendance_selection(self):
        for var in getattr(self, "training_attendance_vars", {}).values():
            var.set(0)

        for refresh in getattr(self, "training_attendance_refreshers", []):
            try:
                refresh()
            except Exception:
                pass

    def load_training_attendance_selection(self):
        self.clear_training_attendance_selection()

        if not self.selected_training_id:
            return

        rows = db_query("""
            SELECT player_id, present
            FROM training_attendance
            WHERE session_id=?
        """, (self.selected_training_id,), fetch=True)

        for player_id, present in rows:
            var = getattr(self, "training_attendance_vars", {}).get(player_id)
            if var:
                try:
                    var.set(int(present or 0))
                except Exception:
                    var.set(0)

        # aggiorna la grafica dei pulsanti
        for refresh in getattr(self, "training_attendance_refreshers", []):
            try:
                refresh()
            except Exception:
                pass
        self.update_idletasks()

    def save_training_attendance_list(self):
        if not self.selected_training_id:
            messagebox.showinfo("Seleziona", "Seleziona prima un allenamento dalla tabella oppure creane uno nuovo.")
            return

        if not getattr(self, "training_attendance_vars", None):
            messagebox.showinfo("Giocatori", "Non ci sono giocatori da salvare.")
            return

        # Batch: 1 DELETE + 1 INSERT unico invece di N query separate
        batch_rows = [
            (self.selected_training_id, player_id, int(var.get() or 0))
            for player_id, var in self.training_attendance_vars.items()
        ]

        db_query("DELETE FROM training_attendance WHERE session_id=?", (self.selected_training_id,))
        db_batch(
            "INSERT INTO training_attendance (session_id, player_id, present) VALUES (?, ?, ?)",
            batch_rows
        )

        self.refresh_training()
        self.load_training_attendance_selection()
        messagebox.showinfo("Salvato", "Presenze allenamento salvate correttamente.")

    def save_training(self):
        try:
            datetime.strptime(self.training_date_var.get(), "%d-%m-%y")
        except ValueError:
            messagebox.showerror("Errore", "Data non valida.")
            return

        db_query(
            "INSERT INTO training_sessions (training_date, title, notes) VALUES (?, ?, ?)",
            (ui_to_db_date(self.training_date_var.get()), self.training_title_var.get(), self.training_notes_var.get())
        )

        new_id = db_query("SELECT MAX(id) FROM training_sessions", fetch=True)[0][0]
        self.selected_training_id = new_id

        self.refresh_training()
        self.load_training_attendance_selection()

        if hasattr(self, "training_selected_label"):
            self.training_selected_label.configure(
                text=f"Allenamento selezionato: {self.training_date_var.get()} - {self.training_title_var.get()}"
            )

    def delete_training(self):
        if not self.selected_training_id:
            messagebox.showinfo("Seleziona", "Seleziona prima un allenamento dalla tabella.")
            return

        if not messagebox.askyesno(
            "Conferma eliminazione",
            "Vuoi eliminare l'allenamento selezionato?\n\nSaranno eliminate anche tutte le presenze, assenze e infortunati collegati."
        ):
            return

        db_query("DELETE FROM training_sessions WHERE id=?", (self.selected_training_id,))
        compact_training_ids()

        self.selected_training_id = None

        if hasattr(self, "training_date_var"):
            self.training_date_var.set(date.today().strftime("%d-%m-%y"))
        if hasattr(self, "training_title_var"):
            self.training_title_var.set("Allenamento")
        if hasattr(self, "training_notes_var"):
            self.training_notes_var.set("")
        if hasattr(self, "training_selected_label"):
            self.training_selected_label.configure(
                text="Seleziona un allenamento dalla tabella sotto, oppure creane uno nuovo."
            )

        self.clear_training_attendance_selection()
        self.refresh_training()

    def save_attendance(self):
        self.save_training_attendance_list()

    def refresh_training(self):
        if hasattr(self, "training_tree"):
            for item in self.training_tree.get_children():
                self.training_tree.delete(item)

            try:
                rows = db_query("""
                    SELECT
                        s.id,
                        s.training_date,
                        COALESCE(s.title, 'Allenamento') AS title,
                        COALESCE(SUM(CASE WHEN a.present=1 THEN 1 ELSE 0 END), 0) AS presenti,
                        COALESCE(SUM(CASE WHEN a.present IN (0,2) THEN 1 ELSE 0 END), 0) AS assenti,
                        COALESCE(SUM(CASE WHEN a.present=2 THEN 1 ELSE 0 END), 0) AS infortunati,
                        COUNT(a.id) AS totale,
                        CASE
                            WHEN COUNT(a.id) > 0
                            THEN CONCAT(ROUND((SUM(CASE WHEN a.present=1 THEN 1 ELSE 0 END) * 100.0) / COUNT(a.id), 1), '%%')
                            ELSE '0%%'
                        END AS percentuale
                    FROM training_sessions s
                    LEFT JOIN training_attendance a ON a.session_id=s.id
                    GROUP BY s.id, s.training_date, s.title
                    ORDER BY s.training_date DESC, s.id DESC
                """, fetch=True)
            except Exception as exc:
                messagebox.showerror(
                    "Errore allenamenti",
                    "Non riesco a caricare gli allenamenti:\n{}".format(exc)
                )
                rows = []

            for row in rows:
                row = list(row)
                if len(row) > 1:
                    row[1] = db_to_ui_date(row[1])
                self.safe_tree_insert(self.training_tree, row)

        if hasattr(self, "training_ranking_tree"):
            for item in self.training_ranking_tree.get_children():
                self.training_ranking_tree.delete(item)

            try:
                ranking_rows = db_query("""
                    SELECT
                        trim(p.last_name || ' ' || p.first_name) AS player,
                        COALESCE(SUM(CASE WHEN a.present=1 THEN 1 ELSE 0 END), 0) AS presenze,
                        COALESCE(SUM(CASE WHEN a.present IN (0,2) THEN 1 ELSE 0 END), 0) AS assenze,
                        COALESCE(SUM(CASE WHEN a.present=2 THEN 1 ELSE 0 END), 0) AS infortunati,
                        COUNT(a.id) AS totale
                    FROM players p
                    LEFT JOIN training_attendance a ON a.player_id = p.id
                    WHERE LOWER(TRIM(COALESCE(p.role, ''))) NOT IN ('mister', 'pres')
                    GROUP BY p.id, p.last_name, p.first_name
                    ORDER BY presenze DESC, totale DESC, p.last_name, p.first_name
                """, fetch=True)
            except Exception as exc:
                messagebox.showerror(
                    "Errore classifica allenamenti",
                    "Non riesco a caricare la classifica allenamenti:\n{}".format(exc)
                )
                ranking_rows = []

            for row in ranking_rows:
                self.safe_tree_insert(self.training_ranking_tree, row)

    def on_training_select(self, _event):
        sel = self.training_tree.selection()
        if not sel:
            return

        values = self.training_tree.item(sel[0], "values")
        self.selected_training_id = values[0]

        if hasattr(self, "training_date_var"):
            self.training_date_var.set(values[1])
        if hasattr(self, "training_title_var"):
            self.training_title_var.set(values[2])
        if hasattr(self, "training_selected_label"):
            self.training_selected_label.configure(
                text=f"Allenamento selezionato: {values[1]} - {values[2]}"
            )

        self.load_training_attendance_selection()

    # ---------------- EXPORT ----------------


    def pdf_add_table_page(self, pdf, title, columns, rows, col_widths=None, max_rows=28):
        """Crea pagine PDF con tabelle allineate senza usare matplotlib.table."""
        if not columns:
            return

        if col_widths is None or len(col_widths) != len(columns):
            col_widths = [1 / len(columns)] * len(columns)

        total_width = sum(col_widths) or 1
        col_widths = [w / total_width for w in col_widths]

        normalized_rows = []
        for row in rows:
            row = list(row)
            if len(row) < len(columns):
                row += [""] * (len(columns) - len(row))
            elif len(row) > len(columns):
                row = row[:len(columns)]
            normalized_rows.append([str(x) if x is not None else "" for x in row])

        chunks = [normalized_rows[i:i + max_rows] for i in range(0, len(normalized_rows), max_rows)]
        if not chunks:
            chunks = [[]]

        for page_index, chunk in enumerate(chunks, start=1):
            fig = Figure(figsize=(11.69, 8.27), dpi=100, facecolor="white")
            ax = fig.add_subplot(111)
            ax.axis("off")

            page_title = title if len(chunks) == 1 else f"{title} - pagina {page_index}"
            ax.text(
                0.02,
                0.965,
                page_title,
                fontsize=18,
                fontweight="bold",
                transform=ax.transAxes,
                va="top"
            )

            x0, y_top = 0.02, 0.89
            table_w, table_h = 0.96, 0.80
            row_count = max(1, len(chunk) + 1)
            row_h = table_h / row_count

            # Calcolo coordinate colonne
            xs = [x0]
            for w in col_widths:
                xs.append(xs[-1] + table_w * w)

            # Header
            for col_idx, col_name in enumerate(columns):
                x_left = xs[col_idx]
                x_right = xs[col_idx + 1]
                ax.add_patch(
                    plt.Rectangle(
                        (x_left, y_top - row_h),
                        x_right - x_left,
                        row_h,
                        transform=ax.transAxes,
                        facecolor="#e5e7eb",
                        edgecolor="#d1d5db",
                        linewidth=0.6
                    )
                )
                ax.text(
                    (x_left + x_right) / 2,
                    y_top - row_h / 2,
                    str(col_name),
                    fontsize=7.2,
                    fontweight="bold",
                    ha="center",
                    va="center",
                    transform=ax.transAxes
                )

            # Righe
            for row_idx, row in enumerate(chunk, start=1):
                y = y_top - row_h * (row_idx + 1)
                bg = "#ffffff" if row_idx % 2 else "#f9fafb"

                for col_idx, value in enumerate(row):
                    x_left = xs[col_idx]
                    x_right = xs[col_idx + 1]

                    ax.add_patch(
                        plt.Rectangle(
                            (x_left, y),
                            x_right - x_left,
                            row_h,
                            transform=ax.transAxes,
                            facecolor=bg,
                            edgecolor="#d1d5db",
                            linewidth=0.5
                        )
                    )

                    value = str(value)
                    max_chars = max(4, int((x_right - x_left) * 120))
                    if len(value) > max_chars:
                        value = value[:max_chars - 1] + "…"

                    ha = "left" if col_idx in [0, 1] else "center"
                    x_text = x_left + 0.004 if ha == "left" else (x_left + x_right) / 2

                    ax.text(
                        x_text,
                        y + row_h / 2,
                        value,
                        fontsize=6.8,
                        ha=ha,
                        va="center",
                        transform=ax.transAxes
                    )

            pdf.savefig(fig)

    def safe_int_result(self, result):
        try:
            left = int(str(result).split("-")[0].strip())
            right = int(str(result).split("-")[1].strip())
            return left, right
        except Exception:
            return None, None

    def export_season_pdf(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile="report_stagione_completo.pdf"
        )
        if not file_path:
            return

        try:
            matches = db_query("""
                SELECT match_date, opponent, competition, home_away, COALESCE(result, '')
                FROM matches
                ORDER BY match_date, id
            """, fetch=True)

            players = db_query("""
                SELECT
                    p.id,
                    trim(p.last_name || ' ' || p.first_name),
                    p.birth_date,
                    p.role,
                    COUNT(DISTINCT a.id),
                    COALESCE(SUM(a.starter),0),
                    COALESCE(si.subentrato, 0),
                    COALESCE(so.sostituito, 0),
                    COALESCE(SUM(a.minutes),0),
                    COALESCE(SUM(a.goals),0),
                    COALESCE(SUM(a.assists),0),
                    COALESCE(SUM(a.yellow_cards),0),
                    COALESCE(SUM(a.red_cards),0)
                FROM players p
                LEFT JOIN appearances a ON a.player_id=p.id
                LEFT JOIN (
                    SELECT player_in_id AS pid, COUNT(*) AS subentrato
                    FROM substitutions GROUP BY player_in_id
                ) si ON si.pid=p.id
                LEFT JOIN (
                    SELECT player_out_id AS pid, COUNT(*) AS sostituito
                    FROM substitutions GROUP BY player_out_id
                ) so ON so.pid=p.id
                WHERE LOWER(TRIM(COALESCE(p.role, ''))) NOT IN ('mister', 'pres')
                GROUP BY p.id, si.subentrato, so.sostituito
                ORDER BY COALESCE(SUM(a.minutes),0) DESC, p.last_name, p.first_name
            """, fetch=True)

            trainings = db_query("""
                SELECT
                    s.training_date,
                    s.title,
                    COALESCE(SUM(CASE WHEN a.present=1 THEN 1 ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN a.present IN (0,2) THEN 1 ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN a.present=2 THEN 1 ELSE 0 END), 0),
                    COUNT(a.id),
                    CASE
                        WHEN COUNT(a.id) > 0 THEN ROUND((SUM(CASE WHEN a.present=1 THEN 1 ELSE 0 END) * 100.0) / COUNT(a.id), 1)
                        ELSE 0
                    END
                FROM training_sessions s
                LEFT JOIN training_attendance a ON a.session_id=s.id
                GROUP BY s.id
                ORDER BY s.training_date
            """, fetch=True)

            total_matches = len(matches)
            total_players = len(players)
            total_trainings = len(trainings)
            total_goals = sum(int(p[9] or 0) for p in players)
            total_assists = sum(int(p[10] or 0) for p in players)

            goals_against = 0
            clean_sheets = 0
            wins = draws = losses = 0

            for match_date, opponent, competition, home_away, result in matches:
                left, right = self.safe_int_result(result)
                if left is None:
                    continue

                team_goals = left if home_away == "Casa" else right
                opp_goals = right if home_away == "Casa" else left

                goals_against += opp_goals
                clean_sheets += 1 if opp_goals == 0 else 0

                if team_goals > opp_goals:
                    wins += 1
                elif team_goals == opp_goals:
                    draws += 1
                else:
                    losses += 1

            with PdfPages(file_path) as pdf:
                # Copertina / riepilogo
                fig = Figure(figsize=(11.69, 8.27), dpi=100, facecolor="white")
                ax = fig.add_subplot(111)
                ax.axis("off")

                ax.text(0.05, 0.90, f"Gestionale {TEAM_NAME} {TEAM_SEASON}", fontsize=26, fontweight="bold", transform=ax.transAxes)
                ax.text(0.05, 0.82, "Report completo stagione", fontsize=18, transform=ax.transAxes)

                summary_rows = [
                    ["Partite", total_matches, "Allenamenti", total_trainings],
                    ["Giocatori", total_players, "Vittorie", wins],
                    ["Pareggi", draws, "Sconfitte", losses],
                    ["Gol fatti", total_goals, "Gol subiti", goals_against],
                    ["Clean sheet", clean_sheets, "Assist", total_assists],
                ]

                summary_table = ax.table(
                    cellText=summary_rows,
                    colLabels=["Voce", "Valore", "Voce", "Valore"],
                    colWidths=[0.22, 0.15, 0.22, 0.15],
                    cellLoc="center",
                    loc="center",
                    bbox=[0.14, 0.25, 0.72, 0.42]
                )
                summary_table.auto_set_font_size(False)
                summary_table.set_fontsize(12)
                summary_table.scale(1, 1.5)

                for (row, col), cell in summary_table.get_celld().items():
                    cell.set_edgecolor("#d1d5db")
                    cell.set_linewidth(0.7)
                    if row == 0:
                        cell.set_facecolor("#e5e7eb")
                        cell.set_text_props(weight="bold")
                    else:
                        cell.set_facecolor("#ffffff" if row % 2 else "#f9fafb")

                pdf.savefig(fig)

                # Tabella partite
                match_rows = [
                    [
                        db_to_ui_date(match_date),
                        opponent,
                        competition,
                        home_away,
                        result or "-"
                    ]
                    for match_date, opponent, competition, home_away, result in matches
                ]
                self.pdf_add_table_page(
                    pdf,
                    "Partite stagione",
                    ["Data", "Avversario", "Competizione", "Casa/Fuori", "Risultato"],
                    match_rows,
                    col_widths=[0.14, 0.36, 0.18, 0.16, 0.16],
                    max_rows=24
                )

                # Tabella statistiche totali giocatori, unica sezione aggregata
                player_rows = []
                for p in players:
                    _, name, birth, role, apps, starts, sub_in, sub_out, minutes, goals, assists, yellow, red = p
                    pct_starts = round((starts / apps) * 100, 1) if apps else 0
                    pct_sub = round((sub_in / apps) * 100, 1) if apps else 0
                    ga90 = round(((goals + assists) * 90 / minutes), 2) if minutes else 0

                    player_rows.append([
                        name,
                        role or "-",
                        apps,
                        starts,
                        f"{pct_starts}%",
                        sub_in,
                        f"{pct_sub}%",
                        sub_out,
                        minutes,
                        goals,
                        assists,
                        ga90,
                        yellow,
                        red
                    ])

                self.pdf_add_table_page(
                    pdf,
                    "Statistiche totali giocatori",
                    ["Giocatore", "Ruolo", "Pres", "Tit", "Tit%", "Sub", "Sub%", "Sost", "Min", "Gol", "Ast", "G+A/90", "Amm", "Esp"],
                    player_rows,
                    col_widths=[0.22, 0.07, 0.055, 0.055, 0.065, 0.055, 0.065, 0.06, 0.065, 0.055, 0.055, 0.075, 0.055, 0.055],
                    max_rows=24
                )

                # Tabella allenamenti
                training_rows = []
                for t in trainings:
                    # Supporta sia vecchie che nuove versioni della query:
                    # 6 colonne: data, titolo, presenti, assenti, totale, percentuale
                    # 7 colonne: data, titolo, presenti, assenti, infortunati, totale, percentuale
                    training_date = t[0] if len(t) > 0 else ""
                    title = t[1] if len(t) > 1 else ""
                    present = t[2] if len(t) > 2 else 0
                    absent = t[3] if len(t) > 3 else 0

                    if len(t) >= 7:
                        injured = t[4]
                        total = t[5]
                        percentage = t[6]
                    else:
                        injured = 0
                        total = t[4] if len(t) > 4 else 0
                        percentage = t[5] if len(t) > 5 else 0

                    training_rows.append([
                        db_to_ui_date(training_date),
                        title,
                        present,
                        absent,
                        injured,
                        total,
                        f"{percentage}%"
                    ])

                self.pdf_add_table_page(
                    pdf,
                    "Allenamenti",
                    ["Data", "Allenamento", "Presenti", "Assenti", "Infortunati", "Totale", "Presenza %"],
                    training_rows,
                    col_widths=[0.14, 0.34, 0.10, 0.10, 0.12, 0.08, 0.12],
                    max_rows=24
                )

            messagebox.showinfo("PDF creato", f"Report stagione creato correttamente:\n{file_path}")

        except Exception as exc:
            messagebox.showerror("Errore PDF", f"Non sono riuscito a creare il PDF stagione:\n{exc}")

    def find_scrollable_canvas(self, widget=None):
        """Trova il canvas interno della pagina scrollabile di CustomTkinter."""
        if widget is None:
            widget = self.main

        if hasattr(widget, "_parent_canvas"):
            return widget._parent_canvas

        for child in widget.winfo_children():
            found = self.find_scrollable_canvas(child)
            if found is not None:
                return found

        return None

    def capture_current_app_page_full(self):
        """Cattura solo la finestra dell'app e scorre la pagina per includere tutto il contenuto."""
        if ImageGrab is None or Image is None:
            raise RuntimeError("ImageGrab/Pillow non disponibile.")

        self.update_idletasks()
        self.update()
        time.sleep(0.25)

        canvas = self.find_scrollable_canvas()

        x = self.winfo_rootx()
        y = self.winfo_rooty()
        w = self.winfo_width()
        h = self.winfo_height()

        # Se non trovo una pagina scrollabile, catturo solo la finestra app.
        if canvas is None:
            return ImageGrab.grab(bbox=(x, y, x + w, y + h))

        try:
            scrollregion = canvas.bbox("all")
            total_height = max(1, scrollregion[3] - scrollregion[1])
            visible_height = max(1, canvas.winfo_height())
        except Exception:
            return ImageGrab.grab(bbox=(x, y, x + w, y + h))

        # Se la pagina sta già tutta nello schermo, catturo solo la finestra app.
        if total_height <= visible_height + 20:
            canvas.yview_moveto(0)
            self.update_idletasks()
            self.update()
            time.sleep(0.15)
            return ImageGrab.grab(bbox=(x, y, x + w, y + h))

        # Cattura più "pezzi" scorrendo la pagina.
        shots = []
        max_scroll = max(1, total_height - visible_height)
        step = max(1, visible_height - 80)

        positions = list(range(0, max_scroll + 1, step))
        if positions[-1] != max_scroll:
            positions.append(max_scroll)

        for pos in positions:
            frac = pos / max_scroll
            canvas.yview_moveto(frac)

            self.update_idletasks()
            self.update()
            time.sleep(0.25)

            shot = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            shots.append(shot)

        canvas.yview_moveto(0)
        self.update_idletasks()
        self.update()

        # Unisce verticalmente gli screenshot della sola app.
        # Taglio leggermente la parte alta nei pezzi successivi per ridurre le ripetizioni.
        cropped = []
        for idx, shot in enumerate(shots):
            if idx == 0:
                cropped.append(shot)
            else:
                crop_top = 120
                cropped.append(shot.crop((0, crop_top, shot.width, shot.height)))

        total_stitched_height = sum(img.height for img in cropped)
        stitched = Image.new("RGB", (w, total_stitched_height), "white")

        current_y = 0
        for img in cropped:
            stitched.paste(img.convert("RGB"), (0, current_y))
            current_y += img.height

        return stitched

    def export_app_screenshots_pdf(self):
        if ImageGrab is None or Image is None:
            messagebox.showerror(
                "Errore",
                "La funzione richiede Pillow/ImageGrab disponibile."
            )
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile="schermate_applicazione_complete.pdf"
        )

        if not file_path:
            return

        pages = [
            ("Dashboard", self.show_dashboard),
            ("Partite", self.show_matches),
            ("Formazione", self.show_formation),
            ("Giocatori", self.show_players),
            ("Figurine", self.show_player_cards),
            ("Bonus", self.show_goals_assists),
            ("Cartellini", self.show_cards),
            ("Minuti", self.show_minutes),
            ("Allenamenti", self.show_training),
            ("Statistiche +", self.show_advanced_stats),
        ]

        previous_page = getattr(self, "current_page", "Dashboard")

        try:
            with PdfPages(file_path) as pdf:
                for page_name, page_func in pages:
                    try:
                        page_func()
                        self.update_idletasks()
                        self.update()
                        time.sleep(0.4)

                        full_image = self.capture_current_app_page_full()

                        # PDF verticale se la schermata è lunga.
                        ratio = full_image.height / max(1, full_image.width)
                        fig_w = 11.69
                        fig_h = max(8.27, fig_w * ratio)

                        fig = Figure(figsize=(fig_w, fig_h), dpi=120, facecolor="white")
                        ax = fig.add_subplot(111)
                        ax.axis("off")

                        ax.set_title(
                            page_name,
                            fontsize=18,
                            fontweight="bold",
                            pad=12
                        )

                        ax.imshow(full_image)
                        pdf.savefig(fig, bbox_inches="tight")

                    except Exception as page_exc:
                        fig = Figure(figsize=(11.69, 8.27), dpi=100, facecolor="white")
                        ax = fig.add_subplot(111)
                        ax.axis("off")
                        ax.text(
                            0.05,
                            0.90,
                            f"Errore esportazione pagina: {page_name}",
                            fontsize=18,
                            fontweight="bold",
                            transform=ax.transAxes
                        )
                        ax.text(
                            0.05,
                            0.80,
                            str(page_exc),
                            fontsize=11,
                            transform=ax.transAxes,
                            wrap=True
                        )
                        pdf.savefig(fig)

            messagebox.showinfo(
                "PDF creato",
                f"PDF schermate complete creato correttamente:\n{file_path}"
            )

        except Exception as exc:
            messagebox.showerror(
                "Errore PDF",
                f"Non sono riuscito a creare il PDF schermate:\n{exc}"
            )

        finally:
            try:
                if previous_page == "Dashboard":
                    self.show_dashboard()
                elif previous_page == "Partite":
                    self.show_matches()
                elif previous_page == "Formazione":
                    self.show_formation()
                elif previous_page == "Giocatori":
                    self.show_players()
                elif previous_page == "Bonus":
                    self.show_goals_assists()
                elif previous_page == "Cartellini":
                    self.show_cards()
                elif previous_page == "Minuti":
                    self.show_minutes()
                elif previous_page == "Allenamenti":
                    self.show_training()
                elif previous_page == "Statistiche +":
                    self.show_advanced_stats()
                else:
                    self.show_dashboard()
            except Exception:
                self.show_dashboard()

    def export_current_csv(self):
        tree = None
        filename = "export.csv"

        if hasattr(self, "stats_tree") and self.current_page in ["Gol & Assist", "Cartellini", "Minuti", "Statistiche +"]:
            tree = self.stats_tree
            filename = f"{self.current_page.lower().replace(' ', '_')}.csv"
        elif hasattr(self, "matches_tree") and self.current_page == "Partite":
            tree = self.matches_tree
            filename = "partite.csv"
        elif hasattr(self, "training_tree") and self.current_page == "Allenamenti":
            tree = self.training_tree
            filename = "allenamenti.csv"

        if tree is None:
            messagebox.showinfo("Export", "Apri una pagina con una tabella da esportare.")
            return

        rows = [tree.item(item, "values") for item in tree.get_children()]
        if not rows:
            messagebox.showinfo("Export", "Nessun dato da esportare.")
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=filename
        )
        if not file_path:
            return

        headers = [tree.heading(col)["text"] for col in tree["columns"]]

        with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(headers)
            writer.writerows(rows)

        messagebox.showinfo("Export", f"File salvato:\n{file_path}")


def main():
    ensure_db()

    # Crea la finestra principale ma la nasconde finché il login non è superato.
    app = teamstats()
    app.withdraw()   # nascosta durante il login

    def on_login_success():
        app.deiconify()  # mostra la finestra principale

    login = LoginWindow(on_success=on_login_success)
    login.master = app   # serve a _on_close per distruggere l'app
    app.mainloop()


if __name__ == "__main__":
    main()
