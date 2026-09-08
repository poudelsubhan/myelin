import asyncio
import json
import re
import time
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from playwright.async_api import async_playwright

from myelin.contracts import RawObservation
from myelin.program.bindings import allow_url
from myelin.schema import NetworkEvent


def graphql_signature(query):
    """Ignore transport formatting, preserving all quoted string contents."""
    return tuple(re.findall(r'"""[\s\S]*?"""|"(?:\\.|[^"\\])*"|[^\s,]', query))


class Session:
    """One async owner, context and page per run. Raw traffic stays memory-only."""

    def __init__(self, settings, store, run_id, tenant, *, site=None, profiles=None):
        self.settings, self.store = settings, store
        self.run_id, self.tenant = run_id, tenant
        self.action_id = None
        self.operation_id = None
        self.repair_identity = None
        self.lose_response_step = None
        self.network = []
        self.pending = set()
        self.requests = {}
        self.inflight = {}
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
        self.site, self.profiles = site, profiles
        self.profile_lock = None
        self.effects = None
        self.existing_browser = False
        self.diagnostics = []
        if site:
            from myelin.live.origins import OriginPolicy

            self.origin_policy = OriginPolicy(site)
            self.allowed_origins = set(site.navigation_origins)
            self.secrets = {}
            self.store.sanitizer.sensitive_keys.update(k.lower() for k in site.sensitive_keys)

    async def open(self, environment, tenant):
        self.environment = environment
        self.existing_browser = bool(
            self.site and self.profiles.is_existing(self.site.auth_profile_id)
        )
        if self.existing_browser:
            self.profile_lock = self.profiles.acquire(self.site.auth_profile_id)
            attached = await self.profiles.existing_browser(self.site.auth_profile_id)
            self.context = attached.contexts[0]
            self.page = await self.context.new_page()
            await self.page.route("**/*", self._route)
            cdp = await self.context.new_cdp_session(self.page)
            await cdp.send("Network.setBypassServiceWorker", {"bypass": True})
        elif self.site:
            self.pw = await async_playwright().start()
            self.profile_lock = self.profiles.acquire(self.site.auth_profile_id)
            self.context = await self.pw.chromium.launch_persistent_context(
                str(self.profiles.folder(self.site.auth_profile_id) / "browser"),
                headless=self.settings.headless,
                channel="chrome",
                service_workers="block",
            )
        else:
            self.pw = await async_playwright().start()
            self.browser = await self.pw.chromium.launch(headless=self.settings.headless)
            self.context = await self.browser.new_context()
        if not self.existing_browser:
            self.context.set_default_timeout(15000)
            await self.context.route("**/*", self._route)
            self.page = (
                self.context.pages[0]
                if self.site and self.context.pages
                else await self.context.new_page()
            )
        self.page.set_default_timeout(15000)
        owner = self.context if self.site and not self.existing_browser else self.page
        owner.on("request", self._on_request)
        owner.on("response", lambda r: self._task(self._on_response(r)))
        owner.on("requestfailed", self._on_failed)
        if self.site:
            # Popups stay owned but cannot silently become the execution page.
            if not self.existing_browser:
                self.context.on("page", lambda page: self.diagnostics.append("owned popup opened"))
            else:
                self.page.on("popup", lambda page: self._task(page.close()))
            for cookie in await self.context.cookies(
                self.site.navigation_origins + self.site.api_origins
            ):
                if len(cookie["value"]) >= 8 and (
                    cookie.get("httpOnly")
                    or cookie["name"].lower() in set(self.site.sensitive_keys)
                    or re.search(r"token|session|auth|csrf", cookie["name"], re.I)
                ):
                    self.store.sanitizer.register(cookie["value"], cookie["name"])
            await self.page.goto(self.site.start_url, wait_until="domcontentloaded")

    def _task(self, coro):
        task = asyncio.create_task(coro)
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def _route(self, route):
        request = route.request
        if self.site:
            try:
                self.origin_policy.permit(request.method, request.url, request.resource_type)
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    if self.effects is None:
                        raise ValueError("live mutations require a task effect boundary")
                    body = request.post_data
                    if body:
                        try:
                            body = json.loads(body)
                        except ValueError:
                            body = dict(parse_qsl(body))
                    read_query = (
                        isinstance(body, dict)
                        and isinstance(body.get("query"), str)
                        and graphql_signature(body["query"])
                        in {graphql_signature(q) for q in self.site.read_only_graphql_queries}
                    )
                    if not read_query:
                        if isinstance(body, dict) and re.match(
                            r"\s*query\b", str(body.get("query", ""))
                        ):
                            raise ValueError("unregistered GraphQL read blocked")
                        # Background writes (view history, telemetry, presence) are
                        # blocked, never ticketed as a business effect.
                        path = urlsplit(request.url).path
                        if not any(
                            e.request.method == request.method
                            and re.fullmatch(e.request.path_pattern, path)
                            for e in self.effects.effects.values()
                        ):
                            raise ValueError("unregistered background mutation blocked")
                        await self.effects.authorize_request(
                            self, request.method, request.url, body
                        )
            except Exception as exc:
                self.diagnostics.append(str(exc))
                await route.abort()
                return
            # No demo headers, auth copying, or fake provider idempotency.
            await route.continue_()
            return
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
            if self.repair_identity and request.url == self.repair_identity["url"]:
                op = self.repair_identity["operation_id"]
            headers["X-Myelin-Operation-ID"] = op
            if request in self.requests:
                self.requests[request].request_headers["X-Myelin-Operation-ID"] = op
        await route.continue_(headers=headers)

    def _on_request(self, request):
        if self.site:
            try:
                self.origin_policy.permit(request.method, request.url, request.resource_type)
            except ValueError:
                return
        self.inflight[request] = asyncio.get_running_loop().create_future()
        try:
            body = request.post_data
        except UnicodeDecodeError:
            body = None
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
        try:
            event.response_headers = await response.all_headers()
        except Exception:
            event.response_headers = {}
        try:
            content_type = event.response_headers.get("content-type", "")
            if self.site:
                event.body_omitted_reason = "live_response_body_private"
            elif not any(t in content_type for t in ("text/", "json", "javascript")):
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
        if (
            not self.site
            and event.url.endswith("/api/login")
            and isinstance(event.response_body, dict)
        ):
            token = event.response_body.get("token")
            if isinstance(token, str):
                self.secrets["bearer_token"] = token
                self.store.sanitizer.register(token, "bearer_token")
        self.store.append("network.jsonl", event)
        future = self.inflight.pop(response.request, None)
        if future and not future.done():
            future.set_result(None)

    def _on_failed(self, request):
        future = self.inflight.pop(request, None)
        if future and not future.done():
            future.set_result(None)
        event = self.requests.get(request)
        if event:
            event.body_omitted_reason = "request_failed"
            self.store.append("network.jsonl", event)

    async def drain(self):
        if self.site:
            # Polling/streaming never defines action completion. Verification uses
            # explicit read conditions; only this action's finite response tasks settle.
            selected = list(self.pending)
            if selected:
                await asyncio.wait(selected, timeout=1.0)
            await asyncio.sleep(0.1)
            return
        # Owned apps use finite requests. Wait for SPA writes already sent by this action,
        # including requests whose response event has not arrived yet.
        async with asyncio.timeout(15):
            while self.pending or self.inflight:
                await asyncio.gather(*list(self.pending), *list(self.inflight.values()))

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
        if not self.site:
            return await self._perform(
                action_id, operation, target, resolved_arguments, operation_id
            )
        if self.effects is None:
            if operation not in ("navigate", "inspect"):
                raise ValueError("live actions require a task context")
            return await self._perform(action_id, operation, target, resolved_arguments)
        key = getattr(self, "next_effect_key", None)
        self.next_effect_key = None
        if not await self.effects.begin(action_id, key):
            return  # Previously read-back-verified logical effect, regardless of step/run ID.
        self.store.save("effect-bindings.json", self.effects.action_bindings)
        error = None
        try:
            return await self._perform(action_id, operation, target, resolved_arguments)
        except BaseException as exc:
            error = exc
            raise
        finally:
            await self.effects.finish(self, error)

    async def _perform(self, action_id, operation, target, resolved_arguments, operation_id=None):
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
                elif operation == "press":
                    await locator.press(str(args["key"]))
                elif operation == "fill":
                    await locator.fill(str(args["value"]))
                    if self.site and await locator.get_attribute("contenteditable") == "true":
                        # Rich editors debounce their document model after DOM input.
                        await asyncio.sleep(0.5)
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
        self, request_id, method, url, headers, body, operation_id=None, body_kind="json", retry=0
    ):
        if self.site:
            from myelin.live.http_recipes import execute

            return await execute(self, method, url, headers, body)
        allow_url(url, self.allowed_origins)
        if any(k.lower().startswith("x-myelin-demo") for k in headers):
            raise ValueError("test administration headers prohibited")
        headers = dict(headers)
        if self.environment.app == "expense":
            for key in list(headers):
                if key.lower() == "authorization" and not headers[key].startswith("Bearer "):
                    headers[key] = "Bearer " + headers[key]
        if operation_id:
            headers["X-Myelin-Operation-ID"] = operation_id
        self.http_requests += 1
        kwargs = (
            {"form" if body_kind == "form" else "data": body}
            if body or (body_kind == "json" and method not in ("GET", "HEAD"))
            else {}
        )
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
        if self.lose_response_step == self.action_id:
            self.lose_response_step = None
            raise ConnectionError("Injected applied-write/lost-response after handler completion")
        if response.status == 429 and method in ("GET", "HEAD") and retry < 2:
            try:
                delay = min(1.0, max(0.0, float(response.headers.get("retry-after", "0.1"))))
            except ValueError:
                delay = 0.1
            await asyncio.sleep(delay)
            return await self.request(
                str(uuid4()), method, url, headers, body, operation_id, body_kind, retry + 1
            )
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
        if self.site:
            for task in list(self.pending):
                task.cancel()
            await asyncio.gather(*list(self.pending), return_exceptions=True)
        if self.existing_browser and self.page:
            await self.page.unroute_all(behavior="ignoreErrors")
            await self.page.close()
        elif self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()
        if self.profile_lock:
            self.profile_lock.close()
            self.profile_lock = None
