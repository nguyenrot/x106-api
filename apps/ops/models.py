"""Admin ops cockpit — the only persisted state is a few JSON preferences
(e.g. the console's command snippets).

Deliberately NOT stored in `site_content`: that table is readable by anyone
through the public `/api/v1/content/{app}/{section}` route, and a snippet can
easily end up holding a password.
"""

from __future__ import annotations

from django.db import models


class OpsPref(models.Model):
    key = models.CharField(primary_key=True, max_length=64)
    data = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ops_prefs"

    def __str__(self) -> str:
        return self.key
