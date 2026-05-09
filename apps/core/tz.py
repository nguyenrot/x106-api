"""Timezone helpers. The whole API treats Asia/Ho_Chi_Minh as the user's local
day boundary — daily streaks, quota windows, and date-only fields all use this."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def local_today() -> date:
    return datetime.now(LOCAL_TZ).date()


def local_today_str() -> str:
    return local_today().strftime("%Y-%m-%d")
