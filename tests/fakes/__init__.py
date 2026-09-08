from myelin.contracts import RawObservation


class FakeBrowserSession:
    """Deterministic protocol fake; no target-app or sibling engine imports."""

    def __init__(self):
        self.commands = []
        self.url = "about:blank"
        self.closed = False
        self.responses = []

    async def open(self, environment, tenant):
        self.commands.append(("open", environment, tenant))

    async def perform(self, action_id, operation, target, resolved_arguments, operation_id=None):
        self.commands.append((operation, action_id, target, resolved_arguments, operation_id))
        if operation == "navigate":
            self.url = resolved_arguments["url"]

    async def request(self, request_id, method, url, headers, body, operation_id=None):
        self.commands.append(("request", request_id, method, url, headers, body, operation_id))
        if not self.responses:
            raise AssertionError("fake HTTP response not supplied")
        return self.responses.pop(0)

    async def snapshot(self):
        return RawObservation(self.url, b"fake-png", "heading: Fake")

    async def close(self):
        self.closed = True
