import asyncio
from dataclasses import dataclass, field


@dataclass(eq=False)
class Subscriber:
    queue: asyncio.Queue
    overflow: asyncio.Event = field(default_factory=asyncio.Event)


class Broadcast:
    """Publishing never waits on a socket. Slow clients must reconnect for a snapshot."""
    def __init__(self, capacity=32):
        self.capacity = capacity
        self.subscribers: set[Subscriber] = set()

    def subscribe(self):
        item = Subscriber(asyncio.Queue(maxsize=self.capacity))
        self.subscribers.add(item)
        return item

    def unsubscribe(self, item):
        self.subscribers.discard(item)

    def publish(self, message):
        for item in tuple(self.subscribers):
            if item.queue.full():
                item.overflow.set()
                self.unsubscribe(item)
            else:
                item.queue.put_nowait(message)
