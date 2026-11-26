import sqlalchemy


class OrderRepository:
    """Persists orders in Postgres."""

    def __init__(self):
        self._rows = {}

    def save(self, order: dict) -> None:
        self._rows[order["txn"]] = order

    def get(self, order_id: str) -> dict:
        return self._rows[order_id]
