from datetime import date, timedelta
import unittest

from models import Card, CardState, Rating


class CardSchedulingTest(unittest.TestCase):
    def test_easy_new_card_graduates_to_review(self):
        card = Card(id=1, deck_id=1, user_id=1, front="front", back="back")

        card.answer(Rating.EASY)

        self.assertEqual(card.state, CardState.REVIEW)
        self.assertGreaterEqual(card.interval, 4)

    def test_again_review_card_moves_to_relearn(self):
        card = Card(
            id=1,
            deck_id=1,
            user_id=1,
            front="front",
            back="back",
            state=CardState.REVIEW,
            interval=10,
            due=(date.today() - timedelta(days=1)).isoformat(),
        )

        card.answer(Rating.AGAIN)

        self.assertEqual(card.state, CardState.RELEARN)
        self.assertEqual(card.lapses, 1)
        self.assertLess(card.interval, 10)


if __name__ == "__main__":
    unittest.main()
