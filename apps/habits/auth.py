"""habits shares ledger's token identity — one token works on both services.

Auth + token hashing live in apps.ledger; we re-export them so habits code can
keep importing from `.auth`, and so `request.user` resolves to the same
LedgerAccount whether the request hits /ledger/* or /habits/*.
"""

from apps.ledger.auth import LedgerTokenAuthentication as HabitTokenAuthentication
from apps.ledger.auth import hash_token

__all__ = ["HabitTokenAuthentication", "hash_token"]
