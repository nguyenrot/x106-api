"""Ledger app — end-to-end smoke tests.

Cover the happy path (create account → token → CRUD transactions → summary)
plus the security-critical cases (no token / wrong token / cross-account isolation).
"""

from __future__ import annotations

import secrets
import string
from datetime import timedelta

import pytest
from django.test import Client

from apps.core.tz import local_today
from apps.ledger.auth import hash_token
from apps.ledger.models import LedgerAccount, LedgerTransaction


def _random_token() -> str:
    """10 char alphanumeric, matches the new TOKEN_RE."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(10))


def _post(client: Client, path: str, body: dict, token: str | None = None):
    headers = {}
    if token:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return client.post(path, data=body, content_type="application/json", **headers)


def _get(client: Client, path: str, token: str | None = None):
    headers = {}
    if token:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return client.get(path, **headers)


def _patch(client: Client, path: str, body: dict, token: str):
    return client.patch(
        path,
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


def _delete(client: Client, path: str, token: str):
    return client.delete(path, HTTP_AUTHORIZATION=f"Bearer {token}")


# ── Account lifecycle ────────────────────────────────────────────────────


def test_create_account_with_user_chosen_token():
    client = Client()
    raw = "MyToken123"
    response = client.post(
        "/api/v1/ledger/accounts",
        data={"token": raw},
        content_type="application/json",
    )
    assert response.status_code == 201, response.content
    payload = response.json()
    assert "id" in payload
    # The raw token is never echoed back — the user supplied it themselves.
    assert "token" not in payload
    # The server stores the hash, not the raw token.
    account = LedgerAccount.objects.get(id=payload["id"])
    assert account.token_hash == hash_token(raw)
    assert account.token_hash != raw


def test_create_account_rejects_duplicate_token():
    client = Client()
    raw = "Duplicate1"
    first = client.post(
        "/api/v1/ledger/accounts", data={"token": raw}, content_type="application/json"
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v1/ledger/accounts", data={"token": raw}, content_type="application/json"
    )
    assert second.status_code == 409
    assert second.json().get("error") == "token_taken"


@pytest.mark.parametrize(
    "bad_token",
    [
        "short",          # < 10
        "TooLongToken123", # > 10
        "hello-1234",     # contains "-"
        "with space",     # contains space
        "ăăăăăăăăăă",     # non-ASCII (Vietnamese)
        "",               # empty
    ],
)
def test_create_account_rejects_invalid_token(bad_token):
    client = Client()
    response = client.post(
        "/api/v1/ledger/accounts",
        data={"token": bad_token},
        content_type="application/json",
    )
    assert response.status_code == 400, (bad_token, response.content)


def test_create_account_rejects_missing_token_field():
    client = Client()
    response = client.post(
        "/api/v1/ledger/accounts",
        data={},
        content_type="application/json",
    )
    assert response.status_code == 400


def test_me_requires_token():
    client = Client()
    assert _get(client, "/api/v1/ledger/me").status_code == 401


def test_me_with_valid_token_returns_account():
    client = Client()
    raw = _random_token()
    create = client.post(
        "/api/v1/ledger/accounts",
        data={"token": raw},
        content_type="application/json",
    )
    assert create.status_code == 201
    response = _get(client, "/api/v1/ledger/me", token=raw)
    assert response.status_code == 200
    assert "id" in response.json() and "created_at" in response.json()


def test_me_with_wrong_token_returns_401():
    client = Client()
    assert _get(client, "/api/v1/ledger/me", token="not-a-real-token").status_code == 401


def test_categories_requires_auth():
    client = Client()
    assert client.get("/api/v1/ledger/categories").status_code == 401


def test_categories_seeded_on_account_create():
    """Brand-new accounts get the 9 default categories pre-seeded."""
    client = Client()
    raw = _random_token()
    client.post(
        "/api/v1/ledger/accounts",
        data={"token": raw},
        content_type="application/json",
    )
    response = _get(client, "/api/v1/ledger/categories", token=raw)
    assert response.status_code == 200
    payload = response.json()
    income_slugs = {c["slug"] for c in payload["income"]}
    expense_slugs = {c["slug"] for c in payload["expense"]}
    assert {"salary", "bonus", "other"} == income_slugs
    assert {"food", "transport", "shopping", "bills", "entertainment", "health", "other"} == expense_slugs
    # Income and expense both ship with their own "other" — independent lists.
    assert "other" in income_slugs and "other" in expense_slugs


# ── Transactions ─────────────────────────────────────────────────────────


@pytest.fixture
def account_token():
    client = Client()
    raw = _random_token()
    resp = client.post(
        "/api/v1/ledger/accounts",
        data={"token": raw},
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.content
    return raw


def test_create_transaction_then_list_today(account_token):
    client = Client()
    today = local_today().strftime("%Y-%m-%d")

    create = _post(
        client,
        "/api/v1/ledger/transactions",
        {
            "kind": "expense",
            "amount": 50000,
            "category": "food",
            "note": "Cơm tấm",
            "occurred_on": today,
        },
        token=account_token,
    )
    assert create.status_code == 201, create.content

    listing = _get(client, "/api/v1/ledger/transactions/today", token=account_token)
    assert listing.status_code == 200
    data = listing.json()
    assert data["date"] == today
    assert data["count"] == 1
    assert data["expense"] == 50000
    assert data["income"] == 0
    assert data["net"] == -50000


def test_amount_must_be_positive(account_token):
    client = Client()
    response = _post(
        client,
        "/api/v1/ledger/transactions",
        {"kind": "expense", "amount": -100, "category": "food"},
        token=account_token,
    )
    assert response.status_code == 400


def test_empty_category_rejected(account_token):
    """Categories are free-form per account (no global enum) — only emptiness
    is rejected at the transaction serializer level."""
    client = Client()
    response = _post(
        client,
        "/api/v1/ledger/transactions",
        {"kind": "expense", "amount": 10000, "category": ""},
        token=account_token,
    )
    assert response.status_code == 400


# ── Category CRUD ────────────────────────────────────────────────────────


def test_create_category_returns_unique_slug(account_token):
    client = Client()
    r = _post(
        client,
        "/api/v1/ledger/categories",
        {"kind": "expense", "name": "Du lịch", "color": "#fb923c"},
        token=account_token,
    )
    assert r.status_code == 201, r.content
    data = r.json()
    assert data["slug"] == "du-lich"
    assert data["name"] == "Du lịch"
    assert data["color"] == "#fb923c"
    assert data["kind"] == "expense"


def test_create_category_slug_collision_appends_suffix(account_token):
    """If the slug derived from the name collides with an existing one (e.g.
    `food` from the default seed), append `-2` to keep it unique."""
    client = Client()
    r = _post(
        client,
        "/api/v1/ledger/categories",
        {"kind": "expense", "name": "Food", "color": "#fbbf24"},
        token=account_token,
    )
    assert r.status_code == 201
    assert r.json()["slug"] == "food-2"


def test_create_category_rejects_bad_color(account_token):
    client = Client()
    r = _post(
        client,
        "/api/v1/ledger/categories",
        {"kind": "expense", "name": "Demo", "color": "tomato"},
        token=account_token,
    )
    assert r.status_code == 400


def test_create_category_separate_kinds(account_token):
    """An income category and an expense category may share the same name —
    they're independent lists."""
    client = Client()
    for kind in ("income", "expense"):
        r = _post(
            client,
            "/api/v1/ledger/categories",
            {"kind": kind, "name": "Đầu tư", "color": "#a78bfa"},
            token=account_token,
        )
        assert r.status_code == 201


def test_rename_category_keeps_slug(account_token):
    client = Client()
    # Find the seeded "food" category id.
    cats = _get(client, "/api/v1/ledger/categories", token=account_token).json()
    food = next(c for c in cats["expense"] if c["slug"] == "food")
    r = _patch(
        client,
        f"/api/v1/ledger/categories/{food['id']}",
        {"name": "Ăn vặt"},
        token=account_token,
    )
    assert r.status_code == 200
    updated = r.json()
    assert updated["name"] == "Ăn vặt"
    assert updated["slug"] == "food"  # slug doesn't move


def test_delete_category_archives_not_destroys(account_token):
    client = Client()
    create = _post(
        client,
        "/api/v1/ledger/categories",
        {"kind": "expense", "name": "Học phí", "color": "#22d3ee"},
        token=account_token,
    )
    cat_id = create.json()["id"]
    r = _delete(client, f"/api/v1/ledger/categories/{cat_id}", token=account_token)
    assert r.status_code == 204
    # Default list excludes archived entries.
    cats = _get(client, "/api/v1/ledger/categories", token=account_token).json()
    assert not any(c["id"] == cat_id for c in cats["expense"])


def test_categories_isolated_between_accounts(account_token):
    """Creating a category on account A doesn't appear on account B."""
    client = Client()
    _post(
        client,
        "/api/v1/ledger/categories",
        {"kind": "expense", "name": "Pet supplies", "color": "#f59e0b"},
        token=account_token,
    )
    other_raw = _random_token()
    client.post(
        "/api/v1/ledger/accounts",
        data={"token": other_raw},
        content_type="application/json",
    )
    cats = _get(client, "/api/v1/ledger/categories", token=other_raw).json()
    assert not any(c["name"] == "Pet supplies" for c in cats["expense"])


def test_kind_must_be_known(account_token):
    client = Client()
    response = _post(
        client,
        "/api/v1/ledger/transactions",
        {"kind": "giving", "amount": 10000, "category": "other"},
        token=account_token,
    )
    assert response.status_code == 400


def test_update_and_delete_transaction(account_token):
    client = Client()
    create = _post(
        client,
        "/api/v1/ledger/transactions",
        {"kind": "expense", "amount": 30000, "category": "food"},
        token=account_token,
    )
    tx_id = create.json()["id"]

    patch = _patch(
        client,
        f"/api/v1/ledger/transactions/{tx_id}",
        {"amount": 55000, "note": "Phở Hà Nội"},
        token=account_token,
    )
    assert patch.status_code == 200
    assert patch.json()["amount"] == 55000
    assert patch.json()["note"] == "Phở Hà Nội"

    delete = _delete(client, f"/api/v1/ledger/transactions/{tx_id}", token=account_token)
    assert delete.status_code == 204
    assert not LedgerTransaction.objects.filter(id=tx_id).exists()


def test_transactions_isolated_between_accounts(account_token):
    client = Client()
    # Account A creates a transaction.
    _post(
        client,
        "/api/v1/ledger/transactions",
        {"kind": "expense", "amount": 12345, "category": "food"},
        token=account_token,
    )
    # Account B starts fresh.
    other_token = _random_token()
    create_b = client.post(
        "/api/v1/ledger/accounts",
        data={"token": other_token},
        content_type="application/json",
    )
    assert create_b.status_code == 201
    listing = _get(client, "/api/v1/ledger/transactions", token=other_token)
    assert listing.status_code == 200
    assert listing.json() == []


# ── Summary ──────────────────────────────────────────────────────────────


def test_summary_totals_and_buckets(account_token):
    client = Client()
    today = local_today()
    yesterday = today - timedelta(days=1)

    rows = [
        ("income", 1_000_000, "salary", today),
        ("expense", 50_000, "food", today),
        ("expense", 30_000, "food", today),
        ("expense", 200_000, "shopping", yesterday),
    ]
    for kind, amount, category, occurred_on in rows:
        _post(
            client,
            "/api/v1/ledger/transactions",
            {
                "kind": kind,
                "amount": amount,
                "category": category,
                "occurred_on": occurred_on.strftime("%Y-%m-%d"),
            },
            token=account_token,
        )

    summary = _get(
        client,
        f"/api/v1/ledger/transactions/summary"
        f"?from={yesterday:%Y-%m-%d}&to={today:%Y-%m-%d}&group_by=day",
        token=account_token,
    ).json()

    assert summary["totals"]["income"] == 1_000_000
    assert summary["totals"]["expense"] == 280_000
    assert summary["totals"]["net"] == 720_000
    assert summary["totals"]["count"] == 4
    # Two day-buckets — yesterday + today.
    assert len(summary["buckets"]) == 2
    # Per-category breakdown sums match.
    expense_by_cat = {row["category"]: row["amount"] for row in summary["by_category"]["expense"]}
    assert expense_by_cat["food"] == 80_000
    assert expense_by_cat["shopping"] == 200_000


def test_summary_group_by_month(account_token):
    client = Client()
    today = local_today()
    _post(
        client,
        "/api/v1/ledger/transactions",
        {
            "kind": "income",
            "amount": 500_000,
            "category": "salary",
            "occurred_on": today.strftime("%Y-%m-%d"),
        },
        token=account_token,
    )
    summary = _get(
        client,
        f"/api/v1/ledger/transactions/summary"
        f"?from={today:%Y-%m-%d}&to={today:%Y-%m-%d}&group_by=month",
        token=account_token,
    ).json()
    assert summary["group_by"] == "month"
    assert summary["buckets"][0]["bucket"] == today.strftime("%Y-%m")
