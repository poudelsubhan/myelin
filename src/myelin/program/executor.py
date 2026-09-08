import time
from urllib.parse import urljoin
from uuid import uuid4

from myelin.adapters.crm import compatible
from myelin.contracts import ExecutionContext
from myelin.program.bindings import bound_url, html_input, json_path, resolve
from myelin.program.dataflow import validate_dataflow
from myelin.program.predicates import evaluate
from myelin.schema import Branch, Checkpoint, FailureContext, HttpStep, RunResult
from myelin.verification.oracle import verify


class StepFailure(RuntimeError):
    def __init__(self, kind, expected, observed):
        super().__init__(kind)
        self.kind, self.expected, self.observed = kind, expected, observed


class Executor:
    def __init__(self, admin, emit):
        self.admin, self.emit = admin, emit

    async def __call__(
        self,
        program,
        inputs,
        environment,
        session=None,
        *,
        allow_drift=False,
        start_at=0,
        completed_before=None,
        before_state=None,
    ):
        if session is None:
            raise ValueError("orchestrator must supply the owned session")
        if not allow_drift and not compatible(program, environment):
            raise ValueError("unsupported program/environment selection")
        validate_dataflow(program, set(inputs))
        start = time.monotonic()
        before = (
            before_state if before_state is not None else await self.admin.state(session.tenant)
        )
        context = ExecutionContext(
            session,
            inputs,
            environment,
            program.policy_revision,
            variables=session.variables,
            secret_store=session.secrets,
        )
        completed = list(completed_before or [])
        observations = {"response": {}, "business": before, "url": session.page.url, "aria": ""}
        failure = None
        checkpoint = None
        step = None
        sent = False
        last_kind = None
        assertions = []
        failed_step = None
        try:
            pending = list(program.steps[start_at:])
            while pending:
                item = pending.pop(0)
                finishing_branch = isinstance(item, tuple)
                step = item[0] if finishing_branch else item
                sent = False
                if finishing_branch:
                    if not all(evaluate(p, context, observations) for p in step.post):
                        raise StepFailure("postcondition", "branch postconditions", observations)
                    completed.append(step.id)
                    await self.emit("program.step", {"step_id": step.id, "success": True})
                    continue
                raw = await session.snapshot()
                obs = session.store.observation(raw, step.id)
                observations.update(url=raw.url, aria=raw.aria)
                op = f"{session.run_id}:{step.operation_key}" if step.operation_key else None
                checkpoint = Checkpoint(
                    run_id=session.run_id,
                    step_id=step.id,
                    completed_step_ids=list(completed),
                    variables=session.store.sanitizer.clean(context.variables),
                    secret_refs=list(context.secret_store),
                    resume_url=urljoin(
                        session.settings.crm_url,
                        resolve(step.resume_url, inputs, context.variables, context.secret_store),
                    )
                    if step.resume_url
                    else None,
                    observation_id=obs.id,
                    operation_id=op,
                )
                session.store.save("checkpoint.json", checkpoint)
                if not all(evaluate(p, context, observations) for p in step.pre):
                    raise StepFailure(
                        "precondition", [p.model_dump() for p in step.pre], observations
                    )
                if any(dep not in completed for dep in step.depends_on):
                    raise StepFailure("precondition", "dependencies completed", completed)
                session.action_id = step.id
                network_offset = len(session.network) if hasattr(session, "network") else 0
                if isinstance(step, Branch):
                    decision = evaluate(step.condition, context, observations)
                    row = {
                        "step_id": step.id,
                        "decision": decision,
                        "condition": step.condition.model_dump(mode="json"),
                        "source_steer_id": step.source_steer_id,
                        "inputs": inputs,
                    }
                    session.store.append("branches.jsonl", row)
                    await self.emit("program.branch", row)
                    pending = list(step.then if decision else step.otherwise) + [(step,)] + pending
                    continue
                if isinstance(step, HttpStep):

                    def resolver(r):
                        return resolve(r, inputs, context.variables, context.secret_store)

                    url = bound_url(
                        resolver(step.url), {k: resolver(v) for k, v in step.path_params.items()}
                    )
                    url = urljoin(session.settings.crm_url, url)
                    if step.query:
                        from urllib.parse import urlencode

                        url += ("&" if "?" in url else "?") + urlencode(
                            {k: resolver(v) for k, v in step.query.items()}
                        )
                    request_id = str(uuid4())
                    sent = True
                    response = await session.request(
                        request_id,
                        step.method,
                        url,
                        {k: str(resolver(v)) for k, v in step.headers.items()},
                        {k: resolver(v) for k, v in step.body.items()},
                        op,
                        body_kind=step.body_kind,
                    )
                    observations["response"] = response
                    if response["status"] >= 400:
                        raise StepFailure("status", "HTTP success", response)
                    for rule in step.extract:
                        if rule.source == "json_path":
                            value = json_path(response["body"], rule.expression)
                        elif rule.source == "html_input":
                            value = html_input(response["body"], rule.expression)
                        elif rule.source == "header":
                            value = response["headers"][rule.expression.lower()]
                        else:
                            cookies = await session.context.cookies()
                            value = next(
                                c["value"] for c in cookies if c["name"] == rule.expression
                            )
                        if rule.secret:
                            context.secret_store[rule.target_var] = value
                            session.store.sanitizer.register(str(value), rule.target_var)
                        else:
                            context.variables[rule.target_var] = value
                else:
                    args = {
                        k: resolve(v, inputs, context.variables, context.secret_store)
                        for k, v in step.arguments.items()
                    }
                    if step.action == "navigate":
                        args["url"] = urljoin(session.settings.crm_url, args["url"])
                    if last_kind == "http":
                        resume_url = (
                            args.get("url") if step.action == "navigate" else checkpoint.resume_url
                        )
                        if not resume_url:
                            raise StepFailure(
                                "precondition", "declared HTTP-to-UI resume URL", None
                            )
                        if environment.app == "expense":
                            from myelin.adapters.expense import resume

                            await resume(session, resume_url)
                        if step.action != "navigate":
                            await session.perform(
                                step.id + ":resume", "navigate", None, {"url": resume_url}
                            )
                    sent = True
                    await session.perform(step.id, step.action, step.target, args, op)
                    if hasattr(session, "network"):
                        sent = any(
                            e.method not in ("GET", "HEAD", "OPTIONS")
                            for e in session.network[network_offset:]
                        )
                raw = await session.snapshot()
                obs = session.store.observation(raw, step.id)
                context.variables["current_url"] = raw.url
                observations.update(
                    url=raw.url, aria=raw.aria, business=await self.admin.state(session.tenant)
                )
                if not all(evaluate(p, context, observations) for p in step.post):
                    raise StepFailure(
                        "postcondition", [p.model_dump() for p in step.post], observations
                    )
                completed.append(step.id)
                await self.emit(
                    "program.step", {"step_id": step.id, "success": True, "observation_id": obs.id}
                )
                last_kind = step.kind
            if not all(evaluate(p, context, observations) for p in program.final_post):
                raise StepFailure("postcondition", "program final assertions", observations)
        except Exception as exc:
            failed_step = step.id if step else None
            if checkpoint:
                kind = exc.kind if isinstance(exc, StepFailure) else "transport"
                effect = "unknown" if sent and step.effect == "write" else "not_applied"
                if kind == "status" and observations["response"].get("status") in (
                    401,
                    403,
                    404,
                    422,
                ):
                    effect = "not_applied"
                failure = FailureContext(
                    checkpoint=checkpoint,
                    request_id=None,
                    error_kind=kind,
                    effect_status=effect,
                    expected=exc.expected if isinstance(exc, StepFailure) else None,
                    observed=session.store.sanitizer.clean(
                        exc.observed
                        if isinstance(exc, StepFailure)
                        else {"error": type(exc).__name__}
                    ),
                    remaining_goal=f"Complete the original {program.workflow} task",
                )
            await self.emit(
                "program.step",
                {"step_id": failed_step, "success": False, "error": type(exc).__name__},
            )
        after = await self.admin.state(session.tenant)
        assertions = verify(
            program.workflow, before, after, inputs, {"revision": program.policy_revision}
        )
        final_obs = session.store.observation(await session.snapshot())
        success = failure is None and failed_step is None and all(a.passed for a in assertions)
        result = RunResult(
            run_id=session.run_id,
            success=success,
            status="completed" if success else "failed",
            program_hash=program.content_hash(),
            environment=environment,
            final_observation=final_obs.id,
            failed_step_id=failed_step,
            failure=failure,
            oracle_assertions=assertions,
            model_calls=0,
            model_usd="0",
            wall_ms=int((time.monotonic() - start) * 1000),
            http_requests=session.http_requests,
            ui_actions=session.ui_actions,
            work_units=session.http_requests + 10 * session.ui_actions,
        )
        session.store.save("result.json", result)
        return result
