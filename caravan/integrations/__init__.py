"""
Integration adapters.

Each adapter exposes a clean interface so the mock implementation can be
swapped for a real one (Google Business Profile API, ERP settlement API,
Maps/geocoding) without touching the rest of the application.
"""
from .google_reviews import GoogleReviewsAdapter
from .erp import ERPAdapter
from .maps import MapsAdapter

__all__ = ["GoogleReviewsAdapter", "ERPAdapter", "MapsAdapter"]
