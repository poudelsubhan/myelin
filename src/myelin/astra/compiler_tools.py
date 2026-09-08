"""Actual hosted analysis plus local, allowlisted candidate patch application."""

import hashlib
import json
from uuid import uuid4

from openai import AsyncOpenAI

from myelin.astra.client import Astra
from myelin.program.staging import Staging


class CompilerTools:
    def __init__(self, settings, store, emit, prior):
        self.settings, self.store, self.emit = settings, store, emit
        self.staging = Staging(
            store.folder / ("staging-" + str(uuid4())),
            prior.model_copy(
                update={"parent_hash": prior.content_hash(), "version": prior.version + 1}
            ),
            store,
        )
        self.initial_hash = self.staging.program().content_hash()
        self.context_data = {}
        self.read_calls = set()
        self.patch_calls = []

    def tools(self):
        return [
            {"type": "apply_patch"},
            {
                "type": "function",
                "name": "read_candidate",
                "strict": True,
                "description": "Read the staged program or source map only.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "enum": ["program.json", "source-map.json"]}
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        ]

    def compiler_context(self):
        return self.context_data | {
            "staged_program": (self.staging.root / "program.json").read_text(),
            "instruction": (
                "Use apply_patch to update program.json notes with real hosted findings. "
                "Preserve all executable steps exactly. Parent/version are staged. "
                "Optionally add source-map.json with grounded findings. Read files as needed. "
                "Return the EXACT staged Program JSON. Patch application is not promotion."
            ),
        }

    async def prepare(self, trace, network):
        sanitizer = self.store.sanitizer
        sanitizer.register(self.settings.api_key, "model-api-key")
        sanitizer.register(self.settings.demo_token, "private-admin-token")
        bundle = sanitizer.clean(
            {
                "trace": trace.model_dump(mode="json"),
                "network": [e.model_dump(mode="json") for e in network],
            }
        )
        raw = json.dumps(bundle, ensure_ascii=False).encode()
        for secret in (self.settings.api_key, self.settings.demo_token):
            if secret and secret.encode() in raw:
                raise ValueError("upload contains a configured secret")
        self.store.save("hosted-upload.json", bundle)
        async with AsyncOpenAI(
            api_key=self.settings.api_key, base_url=self.settings.base_url, max_retries=0
        ) as client:
            container = await client.containers.create(
                name="myelin-trace-analysis",
                memory_limit="1g",
                expires_after={"anchor": "last_active_at", "minutes": 20},
                network_policy={"type": "disabled"},
            )
            uploaded = await client.containers.files.create(
                container.id, file=("trace-bundle.json", raw, "application/json")
            )
        metadata = {
            "container_id": container.id,
            "file_id": uploaded.id,
            "path": uploaded.path,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
        self.store.save("hosted-container.json", metadata)
        tools = [
            {
                "type": "shell",
                "environment": {"type": "container_reference", "container_id": container.id},
            }
        ]
        astra = Astra(self.settings, self.store, self.emit)
        instructions = (
            "Use the actual hosted shell to read the uploaded sanitized trace JSON. "
            "The hosted environment cannot access local apps, private files or localhost. "
            "Analyze observed request dependencies and distinguish evidence from assumptions. "
            "Do not attempt network requests. Never invent a shell execution result."
        )
        first = await astra.respond(
            "compile",
            instructions=instructions,
            tools=tools,
            reasoning={"effort": "high"},
            input=(
                f"Read {uploaded.path} with shell. Correlate action IDs and request fields: "
                "fresh auth/CSRF, entity identity and ambiguous literals. "
                "Count actions and requests and report concrete findings."
            ),
        )
        self.store.save("hosted-analysis-response.json", first.model_dump(mode="json"))
        if not any(i.type == "shell_call_output" for i in first.output):
            raise ValueError("hosted execution output was not observed")
        followup = await astra.respond(
            "compile",
            instructions=instructions,
            tools=tools,
            reasoning={"effort": "high"},
            previous_response_id=first.id,
            input=(
                "Use the same container to compute the uploaded SHA-256 and verify mutating "
                "request counts. Give a concise dependency annotation for program notes. "
                "Use actual shell results."
            ),
        )
        self.store.save("hosted-followup-response.json", followup.model_dump(mode="json"))
        if not any(i.type == "shell_call_output" for i in followup.output):
            raise ValueError("hosted follow-up output was not observed")
        self.context_data = metadata | {
            "response_ids": [first.id, followup.id],
            "analysis": first.output_text,
            "verified_followup": followup.output_text,
        }
        await self.emit("compiler.hosted_analysis", self.context_data)
        return self.context_data

    async def execute(self, call):
        if call.type == "apply_patch_call":
            result = self.staging.apply(call.call_id, call.operation.model_dump(mode="json"))
            self.patch_calls.append(result)
            await self.emit("compiler.patch", result)
            return [result]
        if call.type == "function_call" and call.name == "read_candidate":
            if call.call_id in self.read_calls:
                raise ValueError("duplicate candidate read call")
            self.read_calls.add(call.call_id)
            path = json.loads(call.arguments)["path"]
            if path not in Staging.ALLOWED:
                text = "Path is not allowed"
            else:
                file = self.staging.root / path
                text = file.read_text() if file.exists() else "File does not exist"
            return [{"type": "function_call_output", "call_id": call.call_id, "output": text}]
        raise ValueError("unhandled compiler tool call; hosted shell outputs are service-owned")

    def validate_output(self, program):
        staged = self.staging.program()
        if not any(c["status"] == "completed" for c in self.patch_calls):
            raise ValueError("no completed apply_patch operation")
        if (
            staged.content_hash() != program.content_hash()
            or staged.content_hash() == self.initial_hash
        ):
            raise ValueError("output must equal the changed staged candidate")
        return program
