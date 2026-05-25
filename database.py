import json
import logging
import os
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Row

from models import Card, CardState, Deck, User

DB_PATH = Path(__file__).resolve().parent / "anki.db"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_POSTGRES = bool(DATABASE_URL)
logging.basicConfig(level=logging.ERROR)


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


if IS_POSTGRES:
    engine = create_engine(normalize_database_url(DATABASE_URL), pool_pre_ping=True, future=True)
else:
    engine = create_engine(f"sqlite:///{DB_PATH}", future=True)


class DbSession:
    def __init__(self, conn: Connection):
        self.conn = conn

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None):
        statement, values = normalize_query(sql, params)
        return self.conn.execute(text(statement), values)

    def executescript(self, script: str) -> None:
        for statement in split_sql_script(script):
            self.conn.execute(text(statement))


def normalize_query(sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    if params is None:
        return sql, {}
    if isinstance(params, dict):
        return sql, params

    values: dict[str, Any] = {}
    statement = sql
    for index, value in enumerate(params):
        name = f"p{index}"
        statement = statement.replace("?", f":{name}", 1)
        values[name] = value
    return statement, values


def split_sql_script(script: str) -> list[str]:
    return [statement.strip() for statement in script.split(";") if statement.strip()]


@contextmanager
def db_session():
    with engine.begin() as conn:
        try:
            yield DbSession(conn)
        except Exception as e:
            logging.error(f"Database error: {e}")
            raise


SQLITE_SCHEMA = """
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

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decks (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    new_per_day INTEGER DEFAULT 20,
    review_per_day INTEGER DEFAULT 200,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS cards (
    id SERIAL PRIMARY KEY,
    deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    front TEXT NOT NULL,
    back TEXT NOT NULL,
    tags TEXT DEFAULT '',
    state INTEGER DEFAULT 0,
    due TEXT NOT NULL,
    interval INTEGER DEFAULT 0,
    ease DOUBLE PRECISION DEFAULT 2.5,
    reps INTEGER DEFAULT 0,
    lapses INTEGER DEFAULT 0,
    learning_step INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS study_log (
    id SERIAL PRIMARY KEY,
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
        conn.executescript(POSTGRES_SCHEMA if IS_POSTGRES else SQLITE_SCHEMA)
        ensure_column(conn, "study_log", "previous_state", "TEXT")
        ensure_column(conn, "study_log", "undone", "INTEGER DEFAULT 0")


def ensure_column(conn: DbSession, table: str, column: str, definition: str) -> None:
    if IS_POSTGRES:
        exists = conn.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name=:table AND column_name=:column
            """,
            {"table": table, "column": column},
        ).fetchone()
    else:
        exists = next(
            (row for row in conn.execute(f"PRAGMA table_info({table})").fetchall() if row_to_dict(row)["name"] == column),
            None,
        )
    if not exists:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def row_to_dict(row: Row | None) -> dict[str, Any] | None:
    return dict(row._mapping) if row else None


def scalar_int(row: Row) -> int:
    return int(row[0])


def get_activity_heatmap(user_id: int) -> dict:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT SUBSTR(timestamp, 1, 10) AS d, COUNT(*) AS count FROM study_log WHERE user_id=? GROUP BY d",
            (user_id,),
        ).fetchall()
    return {row_to_dict(row)["d"]: row_to_dict(row)["count"] for row in rows}


def log_study(user_id: int, card: Card, rating: int, prev_state: dict):
    with db_session() as conn:
        conn.execute(
            "INSERT INTO study_log (user_id, card_id, rating, timestamp, previous_state) VALUES (?, ?, ?, ?, ?)",
            (user_id, card.id, rating, datetime.now().isoformat(), json.dumps(prev_state, ensure_ascii=False)),
        )


def undo_last_study(user_id: int) -> Card | None:
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
        log_row = row_to_dict(row)
        if not log_row:
            return None

        previous = json.loads(log_row["previous_state"])
        card_row = row_to_dict(
            conn.execute(
                "SELECT * FROM cards WHERE id=? AND user_id=?",
                (log_row["card_id"], user_id),
            ).fetchone()
        )
        if not card_row:
            conn.execute("UPDATE study_log SET undone=1 WHERE id=?", (log_row["id"],))
            return None

        restored = Card(**card_row)
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
        conn.execute("UPDATE study_log SET undone=1 WHERE id=?", (log_row["id"],))
        return restored


def get_leeches(user_id: int, limit: int = 5) -> list[Card]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE user_id=? AND lapses > 0 ORDER BY lapses DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [Card(**row_to_dict(row)) for row in rows]


def create_user(username: str, password_hash: str) -> int:
    with db_session() as conn:
        row = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?) RETURNING id",
            (username, password_hash),
        ).fetchone()
        return scalar_int(row)


def get_user_by_username(username: str) -> User | None:
    with db_session() as conn:
        row = row_to_dict(conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone())
        return User(**row) if row else None


def get_user(user_id: int) -> User | None:
    with db_session() as conn:
        row = row_to_dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        return User(**row) if row else None


def get_decks(user_id: int) -> list[Deck]:
    with db_session() as conn:
        rows = conn.execute("SELECT * FROM decks WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
    return [Deck(**row_to_dict(row)) for row in rows]


def get_deck(user_id: int, deck_id: int) -> Deck | None:
    with db_session() as conn:
        row = row_to_dict(conn.execute("SELECT * FROM decks WHERE id=? AND user_id=?", (deck_id, user_id)).fetchone())
    return Deck(**row) if row else None


def create_deck(user_id: int, name: str, description: str = "") -> Deck:
    deck = Deck(id=None, user_id=user_id, name=name, description=description)
    with db_session() as conn:
        row = conn.execute(
            "INSERT INTO decks(user_id, name, description, new_per_day, review_per_day, created_at) "
            "VALUES(?,?,?,?,?,?) RETURNING id",
            (deck.user_id, deck.name, deck.description, deck.new_per_day, deck.review_per_day, deck.created_at),
        ).fetchone()
        deck.id = scalar_int(row)
    return deck


def delete_deck(user_id: int, deck_id: int) -> None:
    with db_session() as conn:
        conn.execute("DELETE FROM decks WHERE id=? AND user_id=?", (deck_id, user_id))


def create_card(user_id: int, deck_id: int, front: str, back: str, tags: str = "") -> Card:
    if not get_deck(user_id, deck_id):
        raise ValueError("Deck not found")
    card = Card(id=None, deck_id=deck_id, user_id=user_id, front=front, back=back, tags=tags)
    with db_session() as conn:
        row = conn.execute(
            "INSERT INTO cards(deck_id, user_id, front, back, tags, state, due, interval, ease, "
            "reps, lapses, learning_step, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
            (
                card.deck_id,
                card.user_id,
                card.front,
                card.back,
                card.tags,
                int(card.state),
                card.due,
                card.interval,
                card.ease,
                card.reps,
                card.lapses,
                card.learning_step,
                card.created_at,
                card.updated_at,
            ),
        ).fetchone()
        card.id = scalar_int(row)
    return card


def get_due_cards(user_id: int, deck_id: int, limit: int = 100) -> list[Card]:
    now = datetime.now().isoformat()
    today = date.today().isoformat()
    with db_session() as conn:
        deck_row = row_to_dict(
            conn.execute(
                "SELECT new_per_day, review_per_day FROM decks WHERE id=? AND user_id=?",
                (deck_id, user_id),
            ).fetchone()
        )
        if not deck_row:
            return []
        review_limit = min(limit, deck_row["review_per_day"])
        new_limit = max(0, min(limit, deck_row["new_per_day"]))

        rows = conn.execute(
            "SELECT * FROM cards WHERE user_id=? AND deck_id=? AND ("
            "(state IN (1, 3) AND due <= ?) OR (state = 2 AND due <= ?)) "
            "ORDER BY state DESC, due ASC LIMIT ?",
            (user_id, deck_id, now, today, review_limit),
        ).fetchall()

        if len(rows) < limit:
            new_rows = conn.execute(
                "SELECT * FROM cards WHERE user_id=? AND deck_id=? AND state=0 LIMIT ?",
                (user_id, deck_id, min(new_limit, limit - len(rows))),
            ).fetchall()
            rows.extend(new_rows)

    return [Card(**row_to_dict(row)) for row in rows]


def get_card(user_id: int, card_id: int) -> Card | None:
    with db_session() as conn:
        row = row_to_dict(conn.execute("SELECT * FROM cards WHERE id=? AND user_id=?", (card_id, user_id)).fetchone())
    return Card(**row) if row else None


def save_card(card: Card) -> None:
    card.updated_at = datetime.now().isoformat()
    with db_session() as conn:
        conn.execute(
            "UPDATE cards SET front=?, back=?, tags=?, state=?, due=?, interval=?, ease=?, "
            "reps=?, lapses=?, learning_step=?, updated_at=? WHERE id=? AND user_id=?",
            (
                card.front,
                card.back,
                card.tags,
                int(card.state),
                card.due,
                card.interval,
                card.ease,
                card.reps,
                card.lapses,
                card.learning_step,
                card.updated_at,
                card.id,
                card.user_id,
            ),
        )


def delete_card(user_id: int, card_id: int) -> None:
    with db_session() as conn:
        conn.execute("DELETE FROM cards WHERE id=? AND user_id=?", (card_id, user_id))


def get_cards_for_deck(user_id: int, deck_id: int) -> list[Card]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE deck_id=? AND user_id=? ORDER BY created_at",
            (deck_id, user_id),
        ).fetchall()
    return [Card(**row_to_dict(row)) for row in rows]


def get_deck_stats(user_id: int, deck_id: int) -> dict:
    now = datetime.now().isoformat()
    today = date.today().isoformat()
    with db_session() as conn:
        total = scalar_int(
            conn.execute("SELECT COUNT(*) FROM cards WHERE deck_id=? AND user_id=?", (deck_id, user_id)).fetchone()
        )
        due = scalar_int(
            conn.execute(
                "SELECT COUNT(*) FROM cards WHERE deck_id=? AND user_id=? AND "
                "((state=2 AND due<=?) OR (state IN (1,3) AND due<=?))",
                (deck_id, user_id, today, now),
            ).fetchone()
        )
        new_count = scalar_int(
            conn.execute(
                "SELECT COUNT(*) FROM cards WHERE deck_id=? AND user_id=? AND state=0",
                (deck_id, user_id),
            ).fetchone()
        )
    return {"total": total, "due": due, "new": new_count}
