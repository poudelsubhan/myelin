import asyncio
import json
import time
from collections import defaultdict

from myelin.schema import Event


class EventBus:
    def __init__(self, root):
        self.root = root
        self.locks = defaultdict(asyncio.Lock)
        self.subscribers = defaultdict(set)

    def replay(self, run_id, after_seq=0):
        path = self.root / run_id / "events.jsonl"
        if not path.exists():
            return []
        return [
            event
            for line in path.read_text().splitlines()
            if (event := Event.model_validate_json(line)).seq > after_seq
        ]

    async def emit(self, run_id, kind, payload):
        async with self.locks[run_id]:
            history = self.replay(run_id)
            seq = history[-1].seq + 1 if history else 1
            event = Event(seq=seq, timestamp=time.time(), run_id=run_id, kind=kind, payload=payload)
            path = self.root / run_id / "events.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as f:
                f.write(event.model_dump_json() + "\n")
                f.flush()
            for queue in list(self.subscribers[run_id]):
                if queue.full():
                    self.subscribers[run_id].remove(queue)
                    while not queue.empty():
                        queue.get_nowait()
                    queue.put_nowait(None)
                else:
                    queue.put_nowait(event)
            return event

    async def subscribe(self, run_id, after_seq=0):
        queue = asyncio.Queue(maxsize=256)
        async with self.locks[run_id]:
            history = self.replay(run_id, after_seq)
            self.subscribers[run_id].add(queue)
        try:
            for event in history:
                yield event
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            self.subscribers[run_id].discard(queue)

    async def sse(self, run_id, after_seq=0):
        async for event in self.subscribe(run_id, after_seq):
            yield f"id: {event.seq}\ndata: {json.dumps(event.model_dump())}\n\n"
