"""SiteContent — `app/section -> JSON` rows that frontends fetch at build time."""

from __future__ import annotations

from django.db import models


class SiteContent(models.Model):
    app = models.CharField(max_length=50)
    section = models.CharField(max_length=100)
    data = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    pk = models.CompositePrimaryKey("app", "section")

    class Meta:
        db_table = "site_content"
        ordering = ["app", "section"]
