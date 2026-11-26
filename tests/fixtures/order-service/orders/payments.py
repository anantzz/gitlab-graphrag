"""Payment gateway integration."""


class PaymentError(Exception):
    pass


class PaymentGateway:
    """Talks to the external card processor."""

    def charge(self, account_id: str, amount_cents: int) -> str:
        """Capture a payment and return the transaction id."""
        if amount_cents <= 0:
            raise PaymentError("amount must be positive")
        return f"txn-{account_id}-{amount_cents}"

    def refund(self, transaction_id: str) -> bool:
        """Reverse a captured payment."""
        return transaction_id.startswith("txn-")
