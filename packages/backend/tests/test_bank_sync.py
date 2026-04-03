"""Tests for Bank Sync Connector Architecture."""
import json
from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Unit Tests: Connector Interface
# ---------------------------------------------------------------------------

class TestBankConnector:
    """Test the abstract connector interface."""

    def test_cannot_instantiate_abc(self):
        from app.services.bank_sync import BankConnector
        with pytest.raises(TypeError):
            BankConnector()

    def test_subclass_must_implement_methods(self):
        from app.services.bank_sync import BankConnector

        class Incomplete(BankConnector):
            pass

        with pytest.raises(TypeError):
            Incomplete()


class TestConnectorRegistry:
    """Test connector registration and lookup."""

    def test_register_and_get(self):
        from app.services.bank_sync import register_connector, get_connector, BankConnector

        class DummyConnector(BankConnector):
            def authenticate(self): return True
            def fetch_transactions(self, from_date, to_date): return []
            def get_status(self): return {"connected": True, "last_sync": None, "error": None}

        register_connector("test_dummy", DummyConnector)
        assert get_connector("test_dummy") is DummyConnector

    def test_unknown_connector_raises(self):
        from app.services.bank_sync import get_connector
        with pytest.raises(ValueError, match="Unknown connector"):
            get_connector("nonexistent_bank")

    def test_list_connectors(self):
        from app.services.bank_sync import list_connectors
        connectors = list_connectors()
        assert "mock" in connectors


# ---------------------------------------------------------------------------
# Unit Tests: Mock Connector
# ---------------------------------------------------------------------------

class TestMockConnector:
    """Test the mock bank connector."""

    def test_authenticate(self):
        from app.services.bank_sync import MockBankConnector
        conn = MockBankConnector()
        assert conn.authenticate() is True

    def test_fetch_transactions(self):
        from app.services.bank_sync import MockBankConnector
        conn = MockBankConnector()
        txs = conn.fetch_transactions(date(2026, 1, 1), date(2026, 3, 1))
        assert isinstance(txs, list)
        assert len(txs) > 0
        assert all("external_id" in tx for tx in txs)
        assert all("amount" in tx for tx in txs)
        assert all("date" in tx for tx in txs)

    def test_get_status(self):
        from app.services.bank_sync import MockBankConnector
        conn = MockBankConnector()
        status = conn.get_status()
        assert "connected" in status
        assert "error" in status

    def test_refresh(self):
        from app.services.bank_sync import MockBankConnector
        conn = MockBankConnector()
        txs = conn.refresh(last_synced=date(2026, 3, 1))
        assert isinstance(txs, list)
        assert len(txs) > 0


# ---------------------------------------------------------------------------
# Unit Tests: Import Logic
# ---------------------------------------------------------------------------

class TestImportLogic:
    """Test transaction import with deduplication."""

    def test_import_deduplicates(self, app, db_session):
        from app.services.bank_sync import (
            connect_account, import_transactions, BankAccount, BankTransaction
        )

        account = connect_account(1, "mock", "ACC001")
        result1 = import_transactions(1, account.id)
        assert result1["imported"] > 0

        result2 = import_transactions(1, account.id)
        assert result2["imported"] == 0
        assert result2["skipped"] == result1["imported"]

    def test_import_updates_last_synced(self, app, db_session):
        from app.services.bank_sync import connect_account, import_transactions

        account = connect_account(1, "mock", "ACC002")
        assert account.last_synced_at is None

        import_transactions(1, account.id)
        assert account.last_synced_at is not None


# ---------------------------------------------------------------------------
# API Tests
# ---------------------------------------------------------------------------

class TestBankSyncAPI:
    """Test bank sync REST API endpoints."""

    def test_list_connectors(self, client, auth_header):
        resp = client.get("/bank-sync/connectors", headers=auth_header)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "connectors" in data
        assert "mock" in data["connectors"]

    def test_connect_account(self, client, auth_header):
        resp = client.post("/bank-sync/connect", headers=auth_header,
                          json={"connector_type": "mock", "external_id": "TEST001"})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["connector_type"] == "mock"
        assert data["external_id"] == "TEST001"

    def test_connect_duplicate(self, client, auth_header):
        client.post("/bank-sync/connect", headers=auth_header,
                   json={"connector_type": "mock", "external_id": "DUP001"})
        resp = client.post("/bank-sync/connect", headers=auth_header,
                          json={"connector_type": "mock", "external_id": "DUP001"})
        assert resp.status_code == 400

    def test_list_accounts(self, client, auth_header):
        client.post("/bank-sync/connect", headers=auth_header,
                   json={"connector_type": "mock", "external_id": "LIST001"})
        resp = client.get("/bank-sync/accounts", headers=auth_header)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_disconnect_account(self, client, auth_header):
        create = client.post("/bank-sync/connect", headers=auth_header,
                            json={"connector_type": "mock", "external_id": "DEL001"})
        account_id = create.get_json()["id"]
        resp = client.delete(f"/bank-sync/accounts/{account_id}", headers=auth_header)
        assert resp.status_code == 204

    def test_import_endpoint(self, client, auth_header):
        create = client.post("/bank-sync/connect", headers=auth_header,
                            json={"connector_type": "mock", "external_id": "IMP001"})
        account_id = create.get_json()["id"]
        resp = client.post(f"/bank-sync/accounts/{account_id}/import", headers=auth_header)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "imported" in data
        assert "total" in data

    def test_transactions_endpoint(self, client, auth_header):
        create = client.post("/bank-sync/connect", headers=auth_header,
                            json={"connector_type": "mock", "external_id": "TX001"})
        account_id = create.get_json()["id"]
        client.post(f"/bank-sync/accounts/{account_id}/import", headers=auth_header)
        resp = client.get(f"/bank-sync/accounts/{account_id}/transactions", headers=auth_header)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "total" in data

    def test_unauthorized_access(self, client):
        resp = client.get("/bank-sync/accounts")
        assert resp.status_code in (401, 422)
