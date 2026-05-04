import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
import logging

from models import Card, CardState, Deck, Rating, User

DB_PATH = Path("anki.db")
logging.basicConfig(level=logging.ERROR)


def get_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def db_session():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        logging.error(f"Database error: {e}")
        raise
    finally:
        conn.close()


# Добавлена таблица study_logs для активности и отмены
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    new_per_day INTEGER DEFAULT 20,
    review_per_day INTEGER DEFAULT 200,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    front TEXT NOT NULL,
    back TEXT NOT NULL,
    tags TEXT DEFAULT '',
    state INTEGER DEFAULT 0,
    due TEXT NOT NULL,
    interval INTEGER DEFAULT 0,
    ease REAL DEFAULT 2.5,
    reps INTEGER DEFAULT 0,
    lapses INTEGER DEFAULT 0,
    learning_step INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS study_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL,
    study_date TEXT NOT NULL,
    prev_state INTEGER,
    prev_interval INTEGER,
    prev_ease REAL,
    prev_due TEXT,
    prev_reps INTEGER,
    prev_lapses INTEGER,
    prev_learning_step INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cards_due ON cards(user_id, deck_id, due);
CREATE INDEX IF NOT EXISTS idx_logs_date ON study_logs(user_id, study_date);
"""


def init_db():
    with db_session() as conn:
        conn.executescript(SCHEMA)


# --- User & Deck Ops (Остаются без изменений) ---
def create_user(username: str, password_hash: str) -> int:
    with db_session() as conn:
        cur = conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash))
        return cur.lastrowid


def get_user_by_username(username: str) -> User | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return User(**dict(row)) if row else None


def get_user(user_id: int) -> User | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return User(**dict(row)) if row else None


def create_deck(user_id: int, name: str, description: str = "") -> Deck:
    deck = Deck(id=None, user_id=user_id, name=name, description=description)
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO decks(user_id, name, description, new_per_day, review_per_day, created_at) VALUES(?,?,?,?,?,?)",
            (deck.user_id, deck.name, deck.description, deck.new_per_day, deck.review_per_day, deck.created_at)
        )
        deck.id = cur.lastrowid
    return deck


def get_decks(user_id: int) -> list[Deck]:
    with db_session() as conn:
        rows = conn.execute("SELECT * FROM decks WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
    return [Deck(**dict(r)) for r in rows]


def get_deck(user_id: int, deck_id: int) -> Deck | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM decks WHERE id=? AND user_id=?", (deck_id, user_id)).fetchone()
    return Deck(**dict(row)) if row else None


def delete_deck(user_id: int, deck_id: int) -> None:
    with db_session() as conn:
        conn.execute("DELETE FROM decks WHERE id=? AND user_id=?", (deck_id, user_id))


# --- Card Ops ---
def create_card(user_id: int, deck_id: int, front: str, back: str, tags: str = "") -> Card:
    card = Card(id=None, deck_id=deck_id, user_id=user_id, front=front, back=back, tags=tags)
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO cards(deck_id, user_id, front, back, tags, state, due, interval, ease, reps, lapses, learning_step, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (card.deck_id, card.user_id, card.front, card.back, card.tags, int(card.state), card.due, card.interval,
             card.ease, card.reps, card.lapses, card.learning_step, card.created_at, card.updated_at)
        )
        card.id = cur.lastrowid
    return card


def save_card(card: Card) -> None:
    with db_session() as conn:
        conn.execute(
            "UPDATE cards SET front=?, back=?, tags=?, state=?, due=?, interval=?, ease=?, reps=?, lapses=?, learning_step=?, updated_at=? WHERE id=? AND user_id=?",
            (card.front, card.back, card.tags, int(card.state), card.due, card.interval, card.ease, card.reps,
             card.lapses, card.learning_step, card.updated_at, card.id, card.user_id)
        )


def delete_card(user_id: int, card_id: int) -> None:
    with db_session() as conn:
        conn.execute("DELETE FROM cards WHERE id=? AND user_id=?", (card_id, user_id))


def get_cards_for_deck(user_id: int, deck_id: int) -> list[Card]:
    with db_session() as conn:
        rows = conn.execute("SELECT * FROM cards WHERE deck_id=? AND user_id=? ORDER BY created_at DESC",
                            (deck_id, user_id)).fetchall()
    return [Card(**dict(r)) for r in rows]


def get_card(user_id: int, card_id: int) -> Card | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id=? AND user_id=?", (card_id, user_id)).fetchone()
    return Card(**dict(row)) if row else None


def get_due_cards(user_id: int, deck_id: int, limit: int = 50) -> list[Card]:
    now = datetime.now().isoformat()
    today = date.today().isoformat()
    with db_session() as conn:
        review = conn.execute(
            "SELECT * FROM cards WHERE deck_id=? AND user_id=? AND state=? AND due<=? ORDER BY due LIMIT ?",
            (deck_id, user_id, int(CardState.REVIEW), today, limit)).fetchall()
        learning = conn.execute(
            "SELECT * FROM cards WHERE deck_id=? AND user_id=? AND state IN (?,?) AND due<=? ORDER BY due LIMIT ?",
            (deck_id, user_id, int(CardState.LEARNING), int(CardState.RELEARN), now, limit)).fetchall()
        new_cards = conn.execute("SELECT * FROM cards WHERE deck_id=? AND user_id=? AND state=? ORDER BY id LIMIT ?",
                                 (deck_id, user_id, int(CardState.NEW), limit)).fetchall()
    return [Card(**dict(r)) for r in learning + review + new_cards][:limit]


def get_deck_stats(user_id: int, deck_id: int) -> dict:
    now = datetime.now().isoformat()
    today = date.today().isoformat()
    with db_session() as conn:
        total = conn.execute("SELECT COUNT(*) FROM cards WHERE deck_id=? AND user_id=?", (deck_id, user_id)).fetchone()[
            0]
        due = conn.execute(
            "SELECT COUNT(*) FROM cards WHERE deck_id=? AND user_id=? AND ((state=? AND due<=?) OR (state IN (?,?) AND due<=?))",
            (deck_id, user_id, int(CardState.REVIEW), today, int(CardState.LEARNING), int(CardState.RELEARN),
             now)).fetchone()[0]
        new_count = conn.execute("SELECT COUNT(*) FROM cards WHERE deck_id=? AND user_id=? AND state=?",
                                 (deck_id, user_id, int(CardState.NEW))).fetchone()[0]
    return {"total": total, "due": due, "new": new_count}


# --- Новые функции: Логи, Активность, Пиявки (Leeches) ---
def log_study(user_id: int, card: Card, rating: int, prev_state_dict: dict):
    today = date.today().isoformat()
    with db_session() as conn:
        conn.execute(
            "INSERT INTO study_logs (user_id, card_id, rating, study_date, prev_state, prev_interval, prev_ease, prev_due, prev_reps, prev_lapses, prev_learning_step) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, card.id, rating, today, prev_state_dict['state'], prev_state_dict['interval'],
             prev_state_dict['ease'], prev_state_dict['due'], prev_state_dict['reps'], prev_state_dict['lapses'],
             prev_state_dict['learning_step'])
        )


def get_activity_heatmap(user_id: int) -> dict:
    # Возвращает словарь { 'YYYY-MM-DD': кол-во_повторений } за последние 365 дней
    year_ago = (date.today() - timedelta(days=365)).isoformat()
    with db_session() as conn:
        rows = conn.execute(
            "SELECT study_date, COUNT(*) as cnt FROM study_logs WHERE user_id=? AND study_date >= ? GROUP BY study_date",
            (user_id, year_ago)
        ).fetchall()
    return {r['study_date']: r['cnt'] for r in rows}


def get_leeches(user_id: int, limit: int = 5) -> list[dict]:
    # Карточки, которые забывались (lapses) больше всего
    with db_session() as conn:
        rows = conn.execute(
            "SELECT front, lapses, deck_id FROM cards WHERE user_id=? AND lapses > 2 ORDER BY lapses DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    return [{"front": r["front"], "lapses": r["lapses"], "deck_id": r["deck_id"]} for r in rows]


def undo_last_study(user_id: int, card_id: int) -> bool:
    with db_session() as conn:
        log = conn.execute("SELECT * FROM study_logs WHERE user_id=? AND card_id=? ORDER BY id DESC LIMIT 1",
                           (user_id, card_id)).fetchone()
        if not log: return False

        # Восстанавливаем состояние карточки
        conn.execute(
            "UPDATE cards SET state=?, interval=?, ease=?, due=?, reps=?, lapses=?, learning_step=? WHERE id=? AND user_id=?",
            (log['prev_state'], log['prev_interval'], log['prev_ease'], log['prev_due'], log['prev_reps'],
             log['prev_lapses'], log['prev_learning_step'], card_id, user_id)
        )
        # Удаляем лог
        conn.execute("DELETE FROM study_logs WHERE id=?", (log['id'],))
        return True