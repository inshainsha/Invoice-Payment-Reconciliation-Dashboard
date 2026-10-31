from decimal import Decimal, InvalidOperation

from storage import ReconciliationStore


class ReviewActions:
    def __init__(self, store):
        self.store = store

    def confirm_match(self, invoice_id, payment_id, amount=None, explanation="Confirmed match"):
        return self.store.confirm(invoice_id, payment_id, amount, explanation)

    def reject_match(self, invoice_id, payment_id, explanation="Rejected match"):
        return self.store.reject(invoice_id, payment_id, explanation)

    def manual_assign(self, invoice_id, payment_id, amount, explanation="Manual assignment"):
        return self.store.correct(invoice_id, payment_id, amount, explanation)


def parse_review_amount(value):
    try:
        amount = Decimal(str(value).strip().replace(",", "").replace("$", ""))
    except (InvalidOperation, AttributeError, ValueError):
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    return amount.quantize(Decimal("0.01"))
