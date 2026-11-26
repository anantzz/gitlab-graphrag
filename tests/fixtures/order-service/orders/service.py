from .payments import PaymentGateway, PaymentError
from .repository import OrderRepository


class OrderService:
    """Coordinates order placement: validation, payment, persistence."""

    def __init__(self):
        self.gateway = PaymentGateway()
        self.repo = OrderRepository()

    def place_order(self, account_id: str, items: list[dict]) -> dict:
        """Validate the cart, charge the customer and store the order."""
        total = calculate_total(items)
        txn = self.gateway.charge(account_id, total)
        order = {"account": account_id, "items": items, "total": total, "txn": txn}
        self.repo.save(order)
        return order

    def cancel_order(self, order_id: str) -> bool:
        order = self.repo.get(order_id)
        return self.gateway.refund(order["txn"])


def calculate_total(items: list[dict]) -> int:
    """Sum item price * quantity in cents."""
    return sum(i["price_cents"] * i.get("qty", 1) for i in items)
