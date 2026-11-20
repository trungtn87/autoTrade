"""The dashboard engine must match the live engine's Supabase pooler settings."""
from __future__ import annotations

from unittest import mock

from autotrade_v3.dashboard import data


def test_make_engine_disables_prepared_statements_for_transaction_pooler() -> None:
    with mock.patch.object(data, "create_engine") as create:
        data.make_engine("postgresql+psycopg://u:p@host:6543/db")
    kwargs = create.call_args.kwargs
    assert kwargs["connect_args"]["prepare_threshold"] is None
    assert "statement_timeout" in kwargs["connect_args"]["options"]


def test_database_url_normalises_postgres_scheme(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:6543/db")
    assert data.database_url() == "postgresql+psycopg://u:p@host:6543/db"
