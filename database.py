import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
import logging
import json

from models import Card, CardState, Deck, Rating, User

DB_PATH = Path(__file__).resolve().parent / "anki.db"
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


# ДОБАВИЛ таблицу study_log в SCHEMA для работы хитмапа
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
CREATE TABLE IF NOT EXISTS study_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    card_id INTEGER NOT NULL,
    rating INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    previous_state TEXT,
    undone INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cards_due ON cards(user_id, deck_id, due);
CREATE INDEX IF NOT EXISTS idx_study_log_user_timestamp ON study_log(user_id, timestamp);
"""


def init_db():
    with db_session() as conn:
        conn.executescript(SCHEMA)
        ensure_column(conn, "study_log", "previous_state", "TEXT")
        ensure_column(conn, "study_log", "undone", "INTEGER DEFAULT 0")


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


# --- НОВЫЕ ФУНКЦИИ (Исправляют AttributeError) ---

def get_activity_heatmap(user_id: int) -> dict:
    """Возвращает данные для графика активности: {дата: количество}"""
    with db_session() as conn:
        # Группируем логи по датам
        rows = conn.execute(
            "SELECT date(timestamp) as d, COUNT(*) FROM study_log WHERE user_id=? GROUP BY d",
            (user_id,)
        ).fetchall()
    return {row['d']: row[1] for row in rows}


def log_study(user_id: int, card: Card, rating: int, prev_state: dict):
    """Записывает факт ответа на карточку в базу"""
    with db_session() as conn:
        conn.execute(
            "INSERT INTO study_log (user_id, card_id, rating, timestamp, previous_state) VALUES (?, ?, ?, ?, ?)",
            (user_id, card.id, rating, datetime.now().isoformat(), json.dumps(prev_state, ensure_ascii=False))
        )


def undo_last_study(user_id: int) -> Card | None:
    """Откатывает последний ответ пользователя, если у него сохранено предыдущее состояние."""
    with db_session() as conn:
        row = conn.execute(
            """
            SELECT * FROM study_log
            WHERE user_id=? AND undone=0 AND previous_state IS NOT NULL
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if not row:
            return None

        previous = json.loads(row["previous_state"])
        card_row = conn.execute(
            "SELECT * FROM cards WHERE id=? AND user_id=?",
            (row["card_id"], user_id),
        ).fetchone()
        if not card_row:
            conn.execute("UPDATE study_log SET undone=1 WHERE id=?", (row["id"],))
            return None

        restored = Card(**dict(card_row))
        restored.state = CardState(previous["state"])
        restored.interval = previous["interval"]
        restored.ease = previous["ease"]
        restored.due = previous["due"]
        restored.reps = previous["reps"]
        restored.lapses = previous["lapses"]
        restored.learning_step = previous["learning_step"]
        restored.updated_at = datetime.now().isoformat()

        conn.execute(
            "UPDATE cards SET state=?, due=?, interval=?, ease=?, reps=?, lapses=?, learning_step=?, updated_at=? "
            "WHERE id=? AND user_id=?",
            (
                int(restored.state),
                restored.due,
                restored.interval,
                restored.ease,
                restored.reps,
                restored.lapses,
                restored.learning_step,
                restored.updated_at,
                restored.id,
                user_id,
            ),
        )
        conn.execute("UPDATE study_log SET undone=1 WHERE id=?", (row["id"],))
        return restored


def get_leeches(user_id: int, limit: int = 5) -> list[Card]:
    """Возвращает 'пиявки' — карточки с наибольшим количеством ошибок (lapses)"""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE user_id=? AND lapses > 0 ORDER BY lapses DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    return [Card(**dict(r)) for r in rows]


# --- ОСТАЛЬНЫЕ ОПЕРАЦИИ (User, Deck, Card) ---

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


def get_decks(user_id: int) -> list[Deck]:
    with db_session() as conn:
        rows = conn.execute("SELECT * FROM decks WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
    return [Deck(**dict(r)) for r in rows]


def get_deck(user_id: int, deck_id: int) -> Deck | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM decks WHERE id=? AND user_id=?", (deck_id, user_id)).fetchone()
    return Deck(**dict(row)) if row else None


def create_deck(user_id: int, name: str, description: str = "") -> Deck:
    deck = Deck(id=None, user_id=user_id, name=name, description=description)
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO decks(user_id, name, description, new_per_day, review_per_day, created_at) VALUES(?,?,?,?,?,?)",
            (deck.user_id, deck.name, deck.description, deck.new_per_day, deck.review_per_day, deck.created_at)
        )
        deck.id = cur.lastrowid
    return deck


def delete_deck(user_id: int, deck_id: int) -> None:
    with db_session() as conn:
        conn.execute("DELETE FROM decks WHERE id=? AND user_id=?", (deck_id, user_id))


def create_card(user_id: int, deck_id: int, front: str, back: str, tags: str = "") -> Card:
    if not get_deck(user_id, deck_id):
        raise ValueError("Deck not found")
    card = Card(id=None, deck_id=deck_id, user_id=user_id, front=front, back=back, tags=tags)
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO cards(deck_id, user_id, front, back, tags, state, due, interval, ease, "
            "reps, lapses, learning_step, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (card.deck_id, card.user_id, card.front, card.back, card.tags, int(card.state), card.due,
             card.interval, card.ease, card.reps, card.lapses, card.learning_step, card.created_at, card.updated_at)
        )
        card.id = cur.lastrowid
    return card


def get_due_cards(user_id: int, deck_id: int, limit: int = 100) -> list[Card]:
    """Возвращает карточки, которые пора учить"""
    now = datetime.now().isoformat()
    today = date.today().isoformat()
    with db_session() as conn:
        deck_row = conn.execute(
            "SELECT new_per_day, review_per_day FROM decks WHERE id=? AND user_id=?",
            (deck_id, user_id),
        ).fetchone()
        if not deck_row:
            return []
        review_limit = min(limit, deck_row["review_per_day"])
        new_limit = max(0, min(limit, deck_row["new_per_day"]))

        # Обучение и Повторение
        rows = conn.execute(
            "SELECT * FROM cards WHERE user_id=? AND deck_id=? AND ("
            "(state IN (1, 3) AND due <= ?) OR (state = 2 AND due <= ?)) "
            "ORDER BY state DESC, due ASC LIMIT ?",
            (user_id, deck_id, now, today, review_limit)
        ).fetchall()

        # Если карточек на повторение мало, добавляем новые (state=0)
        if len(rows) < limit:
            new_rows = conn.execute(
                "SELECT * FROM cards WHERE user_id=? AND deck_id=? AND state=0 LIMIT ?",
                (user_id, deck_id, min(new_limit, limit - len(rows)))
            ).fetchall()
            rows.extend(new_rows)

    return [Card(**dict(r)) for r in rows]


def get_card(user_id: int, card_id: int) -> Card | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id=? AND user_id=?", (card_id, user_id)).fetchone()
    return Card(**dict(row)) if row else None


def save_card(card: Card) -> None:
    card.updated_at = datetime.now().isoformat()
    with db_session() as conn:
        conn.execute(
            "UPDATE cards SET front=?, back=?, tags=?, state=?, due=?, interval=?, ease=?, "
            "reps=?, lapses=?, learning_step=?, updated_at=? WHERE id=? AND user_id=?",
            (card.front, card.back, card.tags, int(card.state), card.due,
             card.interval, card.ease, card.reps, card.lapses,
             card.learning_step, card.updated_at, card.id, card.user_id)
        )


def delete_card(user_id: int, card_id: int) -> None:
    with db_session() as conn:
        conn.execute("DELETE FROM cards WHERE id=? AND user_id=?", (card_id, user_id))


def get_cards_for_deck(user_id: int, deck_id: int) -> list[Card]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE deck_id=? AND user_id=? ORDER BY created_at",
            (deck_id, user_id)
        ).fetchall()
    return [Card(**dict(r)) for r in rows]


def get_deck_stats(user_id: int, deck_id: int) -> dict:
    now = datetime.now().isoformat()
    today = date.today().isoformat()
    with db_session() as conn:
        total = conn.execute("SELECT COUNT(*) FROM cards WHERE deck_id=? AND user_id=?", (deck_id, user_id)).fetchone()[
            0]
        due = conn.execute(
            "SELECT COUNT(*) FROM cards WHERE deck_id=? AND user_id=? AND "
            "((state=2 AND due<=?) OR (state IN (1,3) AND due<=?))",
            (deck_id, user_id, today, now)
        ).fetchone()[0]
        new_count = conn.execute("SELECT COUNT(*) FROM cards WHERE deck_id=? AND user_id=? AND state=0",
                                 (deck_id, user_id)).fetchone()[0]
    return {"total": total, "due": due, "new": new_count}
