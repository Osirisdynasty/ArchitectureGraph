def publish(event_name, payload):
    return {"event": event_name, "payload": payload}

def subscribe(event_name, handler):
    return (event_name, handler)
