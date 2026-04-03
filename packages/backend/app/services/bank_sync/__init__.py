"""Bank Sync Connector Architecture for FinMind.

Pluggable architecture for bank integrations with import & refresh support.
Includes a mock connector for development and testing.
"""
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship

from ..extensions import db
from .mock_data import generate_mock_transactions

logger = logging.getLogger("finmind.bank_sync")


# ---------------------------------------------------------------------------
# Abstract Connector Interface
# ---------------------------------------------------------------------------

class BankConnector(ABC):
    """Abstract base class for bank integrations.

    Subclass this to add support for a specific bank or financial API.
    """

    @abstractmethod
    def authenticate(self) -> bool:
        """Validate credentials and establish connection. Returns True on success."""

    @abstractmethod
    def fetch_transactions(self, from_date: date, to_date: date) -> list[dict]:
        """Fetch transactions between dates.

        Returns list of dicts with keys:
            external_id, amount, currency, description, date, type
        """

    @abstractmethod
    def get_status(self) -> dict:
        """Return connector status: {connected: bool, last_sync: str|None, error: str|None}"""

    def refresh(self, last_synced: Optional[date] = None) -> list[dict]:
        """Fetch transactions since last sync. Default: from last_synced to today."""
        to_date = date.today()
        from_date = last_synced or date(to_date.year - 1, to_date.month, to_date.day)
        return self.fetch_transactions(from_date, to_date)


# ---------------------------------------------------------------------------
# Connector Registry
# ---------------------------------------------------------------------------

_CONNECTORS: dict[str, type[BankConnector]] = {}


def register_connector(name: str, cls: type[BankConnector]):
    """Register a connector class by name."""
    _CONNECTORS[name] = cls
    logger.info("Bank connector registered: %s", name)


def get_connector(name: str) -> type[BankConnector]:
    """Get a registered connector class by name."""
    if name not in _CONNECTORS:
        raise ValueError(f"Unknown connector: {name}. Available: {list(_CONNECTORS.keys())}")
    return _CONNECTORS[name]


def list_connectors() -> list[str]:
    """List all registered connector names."""
    return list(_CONNECTORS.keys())


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class BankAccount(db.Model):
    """A linked bank account."""
    __tablename__ = "bank_accounts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    connector_type = Column(String(50), nullable=False)
    external_id = Column(String(255), nullable=False)
    account_name = Column(String(255), nullable=True)
    status = Column(String(20), default="active", nullable=False)  # active, disconnected, error
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    transactions = relationship("BankTransaction", back_populates="account", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "connector_type": self.connector_type,
            "external_id": self.external_id,
            "account_name": self.account_name,
            "status": self.status,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
            "created_at": self.created_at.isoformat(),
        }


class BankTransaction(db.Model):
    """A transaction imported from a bank connector."""
    __tablename__ = "bank_transactions"

    id = Column(Integer, primary_key=True)
    bank_account_id = Column(Integer, ForeignKey("bank_accounts.id"), nullable=False, index=True)
    external_tx_id = Column(String(255), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(10), default="INR", nullable=False)
    description = Column(String(500), nullable=True)
    tx_date = Column(Date, nullable=False)
    tx_type = Column(String(20), default="DEBIT", nullable=False)
    imported = Column(Boolean, default=False, nullable=False)
    imported_expense_id = Column(Integer, ForeignKey("expenses.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    account = relationship("BankAccount", back_populates="transactions")

    __table_args__ = (
        db.UniqueConstraint("bank_account_id", "external_tx_id", name="uq_bank_tx_external"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "external_tx_id": self.external_tx_id,
            "amount": float(self.amount),
            "currency": self.currency,
            "description": self.description,
            "tx_date": self.tx_date.isoformat(),
            "tx_type": self.tx_type,
            "imported": self.imported,
        }


# ---------------------------------------------------------------------------
# Core Service Functions
# ---------------------------------------------------------------------------

def connect_account(user_id: int, connector_type: str, external_id: str,
                    account_name: str | None = None, credentials: dict | None = None) -> BankAccount:
    """Link a bank account for a user."""
    # Validate connector type
    if connector_type not in _CONNECTORS:
        raise ValueError(f"Unknown connector: {connector_type}")

    # Check for duplicate
    existing = BankAccount.query.filter_by(
        user_id=user_id, connector_type=connector_type, external_id=external_id
    ).first()
    if existing:
        raise ValueError("Account already connected")

    account = BankAccount(
        user_id=user_id,
        connector_type=connector_type,
        external_id=external_id,
        account_name=account_name or f"{connector_type}:{external_id}",
    )
    db.session.add(account)
    db.session.commit()
    logger.info("Bank account connected: user=%s connector=%s", user_id, connector_type)
    return account


def disconnect_account(user_id: int, account_id: int) -> bool:
    """Remove a linked bank account."""
    account = BankAccount.query.filter_by(id=account_id, user_id=user_id).first()
    if not account:
        return False
    db.session.delete(account)
    db.session.commit()
    return True


def import_transactions(user_id: int, account_id: int) -> dict:
    """Import transactions from a bank connector into FinMind.

    Returns: {imported: int, skipped: int, total: int}
    """
    account = BankAccount.query.filter_by(id=account_id, user_id=user_id).first()
    if not account:
        raise ValueError("Account not found")

    connector_cls = get_connector(account.connector_type)
    connector = connector_cls()

    # Fetch transactions
    from_date = account.last_synced_at.date() if account.last_synced_at else date.today().replace(year=date.today().year - 1)
    to_date = date.today()
    raw_txs = connector.fetch_transactions(from_date, to_date)

    imported = 0
    skipped = 0

    for tx in raw_txs:
        # Deduplicate by external_tx_id
        existing = BankTransaction.query.filter_by(
            bank_account_id=account.id, external_tx_id=tx["external_id"]
        ).first()
        if existing:
            skipped += 1
            continue

        bank_tx = BankTransaction(
            bank_account_id=account.id,
            external_tx_id=tx["external_id"],
            amount=Decimal(str(tx["amount"])),
            currency=tx.get("currency", "INR"),
            description=tx.get("description", ""),
            tx_date=tx["date"],
            tx_type=tx.get("type", "DEBIT"),
        )
        db.session.add(bank_tx)
        imported += 1

    account.last_synced_at = datetime.now(timezone.utc)
    db.session.commit()

    logger.info("Bank import: account=%s imported=%s skipped=%s", account_id, imported, skipped)
    return {"imported": imported, "skipped": skipped, "total": len(raw_txs)}


def refresh_account(user_id: int, account_id: int) -> dict:
    """Re-import transactions since last sync."""
    return import_transactions(user_id, account_id)


# ---------------------------------------------------------------------------
# Mock Connector
# ---------------------------------------------------------------------------

class MockBankConnector(BankConnector):
    """Mock bank connector for development and testing."""

    def __init__(self, credentials: dict | None = None):
        self.credentials = credentials or {}
        self._connected = False

    def authenticate(self) -> bool:
        self._connected = True
        return True

    def fetch_transactions(self, from_date: date, to_date: date) -> list[dict]:
        if not self._connected:
            self.authenticate()
        return generate_mock_transactions(from_date, to_date)

    def get_status(self) -> dict:
        return {
            "connected": self._connected,
            "last_sync": None,
            "error": None,
        }


# Register mock connector
register_connector("mock", MockBankConnector)
