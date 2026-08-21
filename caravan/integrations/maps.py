"""
Maps / GPS integration adapter.

MOCK implementation. Provides geofence validation for visit check-ins.
In production, distance could use a mapping/geocoding provider; here we use
the haversine formula against the store's stored coordinates.
"""
import math


class MapsAdapter:
    GEOFENCE_METERS = 100  # a check-in must be within this radius of the store

    @staticmethod
    def distance_meters(lat1, lng1, lat2, lng2):
        if None in (lat1, lng1, lat2, lng2):
            return None
        r = 6371000  # earth radius (m)
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlmb = math.radians(lng2 - lng1)
        a = (math.sin(dphi / 2) ** 2
             + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
        return 2 * r * math.asin(math.sqrt(a))

    def validate_checkin(self, store, lat, lng):
        """Return (is_valid, distance_m). If no GPS supplied, treated invalid."""
        d = self.distance_meters(store.latitude, store.longitude, lat, lng)
        if d is None:
            return False, None
        return d <= self.GEOFENCE_METERS, round(d, 1)
