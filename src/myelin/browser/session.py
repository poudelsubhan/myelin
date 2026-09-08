import asyncio
import json
import time
from urllib.parse import parse_qsl
from uuid import uuid4

from playwright.async_api import async_playwright

from myelin.contracts import RawObservation
from myelin.program.bindings import allow_url
from myelin.schema import NetworkEvent


class Session:
    """One async owner, context and page per run. Raw traffic stays memory-only."""

    def __init__(self, settings, store, run_id, tenant):
        self.settings, self.store = settings, store
        self.run_id, self.tenant = run_id, tenant
        self.action_id = None
        self.operation_id = None
        self.network = []
        self.pending = set()
        self.requests = {}
        self.http_requests = 0
        self.ui_actions = 0
        self.variables = {}
        self.secrets = {
            "email": settings.demo_email,
            "password": settings.demo_password,
            "tenant": tenant,
        }
        self.store.sanitizer.register(settings.demo_password, "password")
        self.allowed_origins = {settings.crm_url.rstrip("/")}
        self.pw = self.browser = self.context = self.page = None

    async def open(self, environment, tenant):
        self.environment = environment
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=self.settings.headless)
        self.context = await self.browser.new_context()
        self.context.set_default_timeout(15000)
        await self.context.route("**/*", self._route)
        self.page = await self.context.new_page()
        self.page.on("request", self._on_request)
        self.page.on("response", lambda r: self._task(self._on_response(r)))
        self.page.on("requestfailed", self._on_failed)

    def _task(self, coro):
        task = asyncio.create_task(coro)
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def _route(self, route):
        request = route.request
        try:
            allow_url(request.url, self.allowed_origins)
        except ValueError:
            await route.abort()
            return
        headers = await request.all_headers()
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            ordinal = sum(
                1
                for e in self.network
                if e.action_id == self.action_id and e.method not in ("GET", "HEAD", "OPTIONS")
            )
            op = self.operation_id or f"{self.run_id}:{self.action_id}:{ordinal}"
            headers["X-Myelin-Operation-ID"] = op
            if request in self.requests:
                self.requests[request].request_headers["X-Myelin-Operation-ID"] = op
        await route.continue_(headers=headers)

    def _on_request(self, request):
        body = request.post_data
        if body:
            try:
                body = json.loads(body)
            except ValueError:
                body = dict(parse_qsl(body))
        event = NetworkEvent(
            request_id=str(uuid4()),
            action_id=self.action_id,
            timestamp=time.time(),
            method=request.method,
            url=request.url,
            resource_type=request.resource_type,
            request_headers=request.headers,
            request_body=body,
            redirect_from=(
                self.requests[request.redirected_from].request_id
                if request.redirected_from in self.requests
                else None
            ),
        )
        self.requests[request] = event
        self.network.append(event)
        self.http_requests += 1

    async def _on_response(self, response):
        event = self.requests.get(response.request)
        if event is None:
            return
        event.response_status = response.status
        event.response_headers = await response.all_headers()
        try:
            content_type = event.response_headers.get("content-type", "")
            if not any(t in content_type for t in ("text/", "json", "javascript")):
                event.body_omitted_reason = "non_text_content"
            else:
                body = await response.body()
                if len(body) > 256 * 1024:
                    event.body_omitted_reason = "over_256_kib"
                else:
                    text = body.decode(errors="replace")
                    try:
                        event.response_body = json.loads(text)
                    except ValueError:
                        event.response_body = text
        except Exception:
            event.body_omitted_reason = "response_body_unavailable"
        self.store.append("network.jsonl", event)

    def _on_failed(self, request):
        event = self.requests.get(request)
        if event:
            event.body_omitted_reason = "request_failed"
            self.store.append("network.jsonl", event)

    async def drain(self):
        if self.pending:
            await asyncio.gather(*list(self.pending))

    def locator(self, target):
        if target is None:
            raise ValueError("locator required")
        if target.strategy == "role":
            return self.page.get_by_role(target.role, name=target.value, exact=target.exact)
        if target.strategy == "label":
            return self.page.get_by_label(target.value, exact=target.exact)
        if target.strategy == "text":
            return self.page.get_by_text(target.value, exact=target.exact)
        return self.page.get_by_test_id(target.value)

    async def perform(self, action_id, operation, target, resolved_arguments, operation_id=None):
        self.action_id, self.operation_id = action_id, operation_id
        args = resolved_arguments
        self.ui_actions += operation != "inspect"
        if target and "target_value" in args:
            target = target.model_copy(update={"value": str(args["target_value"])})
        try:
            if operation == "navigate":
                await self.page.goto(allow_url(str(args["url"]), self.allowed_origins))
            elif operation == "inspect":
                pass
            else:
                locator = self.locator(target)
                if operation in ("click", "submit"):
                    await locator.click()
                elif operation == "fill":
                    await locator.fill(str(args["value"]))
                elif operation == "select":
                    await locator.select_option(str(args["value"]))
                elif operation == "check":
                    await locator.set_checked(bool(args.get("value", True)))
                else:
                    raise ValueError("unsupported browser operation")
            await self.page.wait_for_load_state("domcontentloaded")
            await self.drain()
        finally:
            self.operation_id = None

    async def request(
        self, request_id, method, url, headers, body, operation_id=None, body_kind="json"
    ):
        allow_url(url, self.allowed_origins)
        if any(k.lower().startswith("x-myelin-demo") for k in headers):
            raise ValueError("test administration headers prohibited")
        headers = dict(headers)
        if operation_id:
            headers["X-Myelin-Operation-ID"] = operation_id
        self.http_requests += 1
        kwargs = {"form" if body_kind == "form" else "data": body} if body else {}
        response = await self.context.request.fetch(
            url, method=method, headers=headers, max_redirects=0, **kwargs
        )
        text = await response.text()
        try:
            data = json.loads(text)
        except ValueError:
            data = text
        result = {
            "status": response.status,
            "headers": response.headers,
            "body": data,
            "url": response.url,
        }
        event = NetworkEvent(
            request_id=request_id,
            action_id=self.action_id,
            timestamp=time.time(),
            method=method,
            url=url,
            resource_type="fetch",
            request_headers=headers,
            request_body=body,
            response_status=response.status,
            response_headers=response.headers,
            response_body=data if len(text.encode()) <= 256 * 1024 else None,
            body_omitted_reason="over_256_kib" if len(text.encode()) > 256 * 1024 else None,
        )
        self.network.append(event)
        self.store.append("network.jsonl", event)
        return result

    async def snapshot(self):
        await self.drain()
        return RawObservation(
            self.page.url,
            await self.page.screenshot(mask=[self.page.locator('input[type="password"]')]),
            await self.page.locator("body").aria_snapshot(),
        )

    async def close(self):
        await self.drain()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()
