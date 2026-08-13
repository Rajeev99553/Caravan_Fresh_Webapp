"""
Google Reviews integration adapter.

MOCK implementation. In production, replace `_fetch_raw` with a call to the
Google Business Profile API (accounts.locations.reviews). The rest of the app
only depends on the `fetch_reviews(store)` contract below, so swapping in the
real client requires no downstream changes.
"""
import hashlib
from datetime import date, timedelta


POSITIVE = [
    "Fresh produce and friendly staff.",
    "Great store, always clean and well stocked.",
    "Quick checkout and helpful team.",
    "Good quality vegetables, will come again.",
    "Excellent service and fresh items.",
]
NEUTRAL = [
    "It was okay, nothing special.",
    "Average experience, decent prices.",
    "Store is fine but can be crowded.",
]
NEGATIVE = [
    "Some items were not fresh.",
    "Long queue at billing, poor management.",
    "Staff was unhelpful and store looked untidy.",
    "Found expired stock on the shelf.",
]


def _seeded(store_id, salt=""):
    """Deterministic pseudo-random from store id, so demos are stable."""
    h = hashlib.md5(f"{store_id}-{salt}".encode()).hexdigest()
    return int(h, 16)


class GoogleReviewsAdapter:
    """Swap this class body for a real Google Business Profile client."""

    def __init__(self, api_key=None):
        self.api_key = api_key  # unused in mock

    def fetch_reviews(self, store, period_days=45):
        """
        Return a list of normalized review dicts for a store.
        Shape matches what the app persists into the Review model.
        """
        base = _seeded(store.id)
        count = 6 + (base % 9)          # 6..14 reviews
        reviews = []
        today = date.today()
        for i in range(count):
            r = _seeded(store.id, f"rev{i}")
            roll = r % 100
            if roll < 60:
                rating = 4 + (r % 2)     # 4 or 5
                text, sentiment, complaint = POSITIVE[r % len(POSITIVE)], "positive", False
            elif roll < 80:
                rating = 3
                text, sentiment, complaint = NEUTRAL[r % len(NEUTRAL)], "neutral", False
            else:
                rating = 1 + (r % 2)     # 1 or 2
                text, sentiment, complaint = NEGATIVE[r % len(NEGATIVE)], "negative", True
            day_offset = r % period_days
            reviews.append({
                "external_id": f"g-{store.id}-{i}",
                "source": "google",
                "rating": float(rating),
                "text": text,
                "sentiment": sentiment,
                "is_complaint": complaint,
                "review_date": today - timedelta(days=day_offset),
            })
        return reviews
