from events import publish
from payments import charge

def create_order(customer_id, amount):
    receipt = charge(customer_id, amount)
    publish("order.created", {"customer_id": customer_id, "receipt": receipt})
    return receipt
