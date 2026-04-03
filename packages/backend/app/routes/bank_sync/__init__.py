"""Bank Sync management API routes."""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity

from ..extensions import db
from ..services.bank_sync import (
    BankAccount,
    BankTransaction,
    connect_account,
    disconnect_account,
    import_transactions,
    refresh_account,
    list_connectors,
)

bp = Blueprint("bank_sync", __name__)


@bp.get("/connectors")
@jwt_required()
def available_connectors():
    """List available bank connector types."""
    return jsonify(connectors=list_connectors())


@bp.get("/accounts")
@jwt_required()
def list_accounts():
    """List all connected bank accounts for the current user."""
    uid = int(get_jwt_identity())
    accounts = BankAccount.query.filter_by(user_id=uid).all()
    return jsonify([a.to_dict() for a in accounts])


@bp.post("/connect")
@jwt_required()
def connect():
    """Link a new bank account."""
    uid = int(get_jwt_identity())
    body = request.get_json(force=True)
    connector_type = body.get("connector_type", "").strip()
    external_id = body.get("external_id", "").strip()
    account_name = body.get("account_name", "").strip() or None

    if not connector_type:
        return jsonify(error="connector_type is required"), 400
    if not external_id:
        return jsonify(error="external_id is required"), 400

    try:
        account = connect_account(uid, connector_type, external_id, account_name)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    return jsonify(account.to_dict()), 201


@bp.delete("/accounts/<int:account_id>")
@jwt_required()
def disconnect(account_id: int):
    """Disconnect a bank account."""
    uid = int(get_jwt_identity())
    if disconnect_account(uid, account_id):
        return "", 204
    return jsonify(error="account not found"), 404


@bp.post("/accounts/<int:account_id>/import")
@jwt_required()
def trigger_import(account_id: int):
    """Import transactions from a connected bank account."""
    uid = int(get_jwt_identity())
    account = BankAccount.query.filter_by(id=account_id, user_id=uid).first()
    if not account:
        return jsonify(error="account not found"), 404

    try:
        result = import_transactions(uid, account_id)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    return jsonify(result), 200


@bp.post("/accounts/<int:account_id>/refresh")
@jwt_required()
def trigger_refresh(account_id: int):
    """Refresh transactions since last sync."""
    uid = int(get_jwt_identity())
    account = BankAccount.query.filter_by(id=account_id, user_id=uid).first()
    if not account:
        return jsonify(error="account not found"), 404

    try:
        result = refresh_account(uid, account_id)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    return jsonify(result), 200


@bp.get("/accounts/<int:account_id>/transactions")
@jwt_required()
def list_transactions(account_id: int):
    """List imported transactions for a bank account."""
    uid = int(get_jwt_identity())
    account = BankAccount.query.filter_by(id=account_id, user_id=uid).first()
    if not account:
        return jsonify(error="account not found"), 404

    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 25, type=int)))

    pagination = (
        BankTransaction.query
        .filter_by(bank_account_id=account_id)
        .order_by(BankTransaction.tx_date.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return jsonify({
        "items": [t.to_dict() for t in pagination.items],
        "total": pagination.total,
        "page": page,
        "per_page": per_page,
    })
