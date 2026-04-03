"""Generate realistic mock bank transactions for testing."""

import random
from datetime import date, timedelta

MERCHANTS = [
    ("Amazon India", "SHOPPING"),
    ("Swiggy", "FOOD"),
    ("Zomato", "FOOD"),
    ("Uber", "TRANSPORT"),
    ("Ola", "TRANSPORT"),
    ("BigBasket", "GROCERIES"),
    ("Flipkart", "SHOPPING"),
    ("Netflix", "ENTERTAINMENT"),
    ("Spotify", "ENTERTAINMENT"),
    ("Electricity Bill", "UTILITIES"),
    ("Water Bill", "UTILITIES"),
    ("Airtel Mobile", "TELECOM"),
    ("Jio Recharge", "TELECOM"),
    ("Salary Credit", "INCOME"),
    ("Freelance Payment", "INCOME"),
    ("ATM Withdrawal", "CASH"),
    ("Hospital", "HEALTH"),
    ("Apollo Pharmacy", "HEALTH"),
    ("Indian Oil", "FUEL"),
    ("HP Petrol", "FUEL"),
]


def generate_mock_transactions(from_date: date, to_date: date, count: int = 25) -> list[dict]:
    """Generate realistic mock transactions between dates."""
    transactions = []
    days = (to_date - from_date).days

    if days <= 0:
        return transactions

    for _ in range(min(count, days)):
        merchant, tx_type = random.choice(MERCHANTS)
        tx_date = from_date + timedelta(days=random.randint(0, days - 1))

        if tx_type == "INCOME":
            amount = round(random.uniform(5000.0, 150000.0), 2)
            tx_direction = "CREDIT"
        else:
            amount = round(random.uniform(50.0, 5000.0), 2)
            tx_direction = "DEBIT"

        transactions.append({
            "external_id": f"MOCK_{tx_date.strftime('%Y%m%d')}_{random.randint(10000, 99999)}",
            "amount": amount,
            "currency": "INR",
            "description": merchant,
            "date": tx_date,
            "type": tx_direction,
        })

    # Sort by date and deduplicate external_ids
    seen = set()
    unique = []
    for tx in sorted(transactions, key=lambda t: t["date"]):
        if tx["external_id"] not in seen:
            seen.add(tx["external_id"])
            unique.append(tx)

    return unique
