import sqlite3
from datetime import datetime, date, timedelta
from dataclasses import dataclass
from typing import Optional, Dict
from enum import IntEnum
from flask_login import UserMixin

INITIAL_EASE = 2.5
MIN_EASE = 1.3
EASY_BONUS = 1.3
HARD_INTERVAL_FACTOR = 1.2

class Rating(IntEnum):
    AGAIN = 1
    HARD  = 2
    GOOD  = 3
    EASY  = 4

class CardState(IntEnum):
    NEW      = 0
    LEARNING = 1
    REVIEW   = 2
    RELEARN  = 3

@dataclass
class User(UserMixin):
    id: int
    username: str
    password_hash: str

    def get_id(self) -> str:
        return str(self.id)

@dataclass
class Card:
    id: Optional[int]
    deck_id: int
    user_id: int
    front: str
    back: str
    tags: str = ""
    state: CardState = CardState.NEW
    due: str = ""
    interval: int = 0
    ease: float = INITIAL_EASE
    reps: int = 0
    lapses: int = 0
    learning_step: int = 0
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        now = datetime.now().isoformat()
        if not self.due:
            self.due = date.today().isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        # Приводим state к CardState если пришёл int из БД
        if isinstance(self.state, int):
            self.state = CardState(self.state)

    LEARNING_STEPS = [1, 10]
    RELEARN_STEPS  = [10]

    def _minutes_from_now(self, minutes: int) -> str:
        return (datetime.now() + timedelta(minutes=minutes)).isoformat()

    def _days_from_today(self, days: int) -> str:
        return (date.today() + timedelta(days=days)).isoformat()

    def _graduate(self):
        self.interval = 1
        self.state = CardState.REVIEW
        self.reps = 1
        self.due = self._days_from_today(self.interval)

    def _graduate_easy(self):
        self.ease = min(self.ease + 0.15, 3.0)
        self.interval = max(4, round(1 * self.ease * EASY_BONUS))
        self.state = CardState.REVIEW
        self.reps = 1
        self.due = self._days_from_today(self.interval)

    def answer(self, rating: Rating) -> Dict:
        now = datetime.now().isoformat()
        result = {"prev_state": self.state, "rating": rating}

        if self.state == CardState.NEW:
            self._process_new(rating)
        elif self.state == CardState.LEARNING:
            self._process_learning(rating)
        elif self.state == CardState.REVIEW:
            self._process_review(rating)
        elif self.state == CardState.RELEARN:
            self._process_relearn(rating)

        self.updated_at = now
        result.update({"new_state": self.state, "next_due": self.due, "interval": self.interval})
        return result

    def _process_new(self, rating: Rating):
        if rating == Rating.AGAIN:
            self.learning_step = 0
            self.state = CardState.LEARNING
            self.due = self._minutes_from_now(self.LEARNING_STEPS[0])
        elif rating == Rating.HARD:
            self.state = CardState.LEARNING
            self.due = self._minutes_from_now(self.LEARNING_STEPS[self.learning_step])
        elif rating == Rating.GOOD:
            self.learning_step += 1
            if self.learning_step >= len(self.LEARNING_STEPS):
                self._graduate()
            else:
                self.state = CardState.LEARNING
                self.due = self._minutes_from_now(self.LEARNING_STEPS[self.learning_step])
        elif rating == Rating.EASY:
            self._graduate_easy()

    def _process_learning(self, rating: Rating):
        if rating == Rating.AGAIN:
            self.learning_step = 0
            self.due = self._minutes_from_now(self.LEARNING_STEPS[0])
        elif rating == Rating.HARD:
            self.due = self._minutes_from_now(self.LEARNING_STEPS[self.learning_step])
        elif rating == Rating.GOOD:
            self.learning_step += 1
            if self.learning_step >= len(self.LEARNING_STEPS):
                self._graduate()
            else:
                self.due = self._minutes_from_now(self.LEARNING_STEPS[self.learning_step])
        elif rating == Rating.EASY:
            self._graduate_easy()

    def _process_review(self, rating: Rating):
        if rating == Rating.AGAIN:
            self.lapses += 1
            self.ease = max(MIN_EASE, self.ease - 0.2)
            self.interval = max(1, round(self.interval * 0.5))
            self.state = CardState.RELEARN
            self.learning_step = 0
            self.due = self._minutes_from_now(self.RELEARN_STEPS[0])
        else:
            self.reps += 1
            if rating == Rating.HARD:
                self.ease = max(MIN_EASE, self.ease - 0.15)
                new_interval = round(self.interval * HARD_INTERVAL_FACTOR)
            elif rating == Rating.GOOD:
                new_interval = round(self.interval * self.ease)
            elif rating == Rating.EASY:
                self.ease = min(self.ease + 0.15, 3.0)
                new_interval = round(self.interval * self.ease * EASY_BONUS)
            else:
                new_interval = self.interval
            self.interval = max(self.interval + 1, new_interval)
            self.due = self._days_from_today(self.interval)

    def _process_relearn(self, rating: Rating):
        if rating == Rating.AGAIN:
            self.learning_step = 0
            self.due = self._minutes_from_now(self.RELEARN_STEPS[0])
        elif rating in (Rating.HARD, Rating.GOOD):
            self.learning_step += 1
            if self.learning_step >= len(self.RELEARN_STEPS):
                self.interval = max(1, round(self.interval * 0.5))
                self.state = CardState.REVIEW
                self.due = self._days_from_today(self.interval)
            else:
                self.due = self._minutes_from_now(self.RELEARN_STEPS[self.learning_step])
        elif rating == Rating.EASY:
            self.ease = min(self.ease + 0.15, 3.0)
            self.state = CardState.REVIEW
            self.due = self._days_from_today(self.interval)

    def next_intervals(self) -> Dict[int, str]:
        import copy
        result = {}
        for r in Rating:
            c = copy.deepcopy(self)
            c.answer(r)
            if c.state == CardState.REVIEW:
                result[r.value] = f"{c.interval}д"
            else:
                due_dt = datetime.fromisoformat(c.due)
                mins = max(1, int((due_dt - datetime.now()).total_seconds() / 60))
                result[r.value] = f"{mins}мин" if mins < 60 else f"{mins // 60}ч"
        return result


@dataclass
class Deck:
    id: Optional[int]
    user_id: int
    name: str
    description: str = ""
    new_per_day: int = 20
    review_per_day: int = 200
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()