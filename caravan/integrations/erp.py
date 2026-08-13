"""
ERP / Finance settlement integration adapter.

MOCK implementation. In production, `post_settlement` would call the ERP
settlement/payment API. Here it returns a deterministic settlement reference
and never actually posts money -- matching the BRD rule that recommendations
must not be paid without approval.
"""
import hashlib
from datetime import datetime


class ERPAdapter:
    def __init__(self, endpoint=None, api_key=None):
        self.endpoint = endpoint
        self.api_key = api_key

    def post_settlement(self, commission):
        """
        Simulate posting an approved commission to the ERP.
        Returns a settlement reference string.
        """
        seed = f"{commission.id}-{commission.net_amount}-{datetime.utcnow().date()}"
        ref = "STL-" + hashlib.md5(seed.encode()).hexdigest()[:10].upper()
        return ref
