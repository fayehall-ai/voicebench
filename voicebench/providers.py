"""Provider implementations.

Each provider owns exactly one thing: turning a (model, scenario) pair into
a Sample, with T0 immediately before the first request and T1 at the first
SPEAKABLE token. Identical boundaries across providers is the only reason a
cross-provider delta means anything.

Providers declare what they CANNOT do via supports(), so an unsupported
combination is skipped with a reason instead of failing mysteriously.
"""

from __future__ import annotations

import json

from .config import (ANTHROPIC_WORKSPACE, DEFAULT_REGION, MAX_TOKENS,
                     ModelSpec, Scenario)
from .measure import Clock, Sample, eligibility, quiet

TOOL_NAME = "is_eligible"
TOOL_DESC = "Check whether a member has active coverage on a date of service."
TOOL_PROPS = {
    "member_id": {"type": "string", "description": "Member ID, e.g. A4471."},
    "date_of_service": {"type": "string", "description": "ISO date, YYYY-MM-DD."},
}
TOOL_REQUIRED = ["member_id", "date_of_service"]


class Provider:
    name: str = "?"

    def model_id(self, model: ModelSpec) -> str | None:
        raise NotImplementedError

    def supports(self, model: ModelSpec, scenario: Scenario) -> tuple[bool, str]:
        if self.model_id(model) is None:
            return False, f"{model.name} not offered on {self.name}"
        if scenario.effort and not model.supports_effort:
            return False, f"{model.name} has no effort capability"
        return True, ""

    def blocking(self, model: ModelSpec, scenario: Scenario) -> Sample:
        raise NotImplementedError

    async def streaming(self, model: ModelSpec, scenario: Scenario) -> Sample:
        raise NotImplementedError

    def env(self) -> dict:
        """Recorded in the run manifest so results are attributable."""
        return {"provider": self.name}


# ==========================================================================
# Bedrock, raw boto3
# ==========================================================================


class BedrockProvider(Provider):
    def __init__(self, region: str = DEFAULT_REGION) -> None:
        import boto3
        from botocore.config import Config

        self.region = region
        self.name = "bedrock" if region == DEFAULT_REGION else f"bedrock:{region}"
        # Retries disabled: a retry sleeps INSIDE the timer, so a throttled
        # call reads as a slow model. This one line is the difference
        # between measuring a model and measuring a quota.
        self.client = boto3.client(
            "bedrock-runtime", region_name=region,
            config=Config(retries={"max_attempts": 1, "mode": "standard"}),
        )

    def model_id(self, model):
        return model.bedrock

    def supports(self, model, scenario):
        ok, why = super().supports(model, scenario)
        if ok and scenario.cache:
            # Bedrock uses cachePoint blocks, a different shape from the
            # Anthropic cache_control block. Not implemented.
            return False, "prefix caching not implemented for bedrock"
        return ok, why

    def env(self):
        import boto3
        return {"provider": self.name, "region": self.region,
                "boto3": boto3.__version__}

    # ------------------------------------------------------------------

    def _request(self, model, scenario, messages) -> dict:
        body = {
            "modelId": self.model_id(model),
            "system": [{"text": scenario.system}],
            "inferenceConfig": {"maxTokens": MAX_TOKENS},
            "messages": messages,
        }
        if scenario.tools:
            body["toolConfig"] = {"tools": [{
                "toolSpec": {
                    "name": TOOL_NAME,
                    "description": TOOL_DESC,
                    "inputSchema": {"json": {
                        "type": "object",
                        "properties": TOOL_PROPS,
                        "required": TOOL_REQUIRED,
                    }},
                }
            }]}
        return body

    @staticmethod
    def _tool_results(content: list[dict]) -> list[dict]:
        """Several toolUse blocks in one turn come back as several blocks
        in ONE user message. Role is 'user', not 'tool' — everything that
        is not the model is the environment, and the environment speaks in
        the user slot."""
        out = []
        for blk in content:
            if "toolUse" in blk:
                use = blk["toolUse"]
                out.append({"toolResult": {
                    "toolUseId": use["toolUseId"],
                    "content": [{"json": eligibility(**use["input"])}],
                }})
        return out

    def blocking(self, model, scenario) -> Sample:
        clock = Clock()
        messages = [{"role": "user", "content": [{"text": scenario.prompt}]}]
        tokens = chars = 0
        fired = False

        for _ in range(2):
            response = self.client.converse(**self._request(model, scenario, messages))
            message = response["output"]["message"]
            chars += sum(len(b["text"]) for b in message["content"] if "text" in b)
            tokens += response.get("usage", {}).get("outputTokens", 0)

            if not scenario.run_loop or response["stopReason"] != "tool_use":
                break

            fired = True
            messages.append(message)
            messages.append({"role": "user",
                             "content": self._tool_results(message["content"])})

        return Sample(None, clock.ms(), tokens, chars, fired)

    async def streaming(self, model, scenario) -> Sample:
        clock = Clock()
        messages = [{"role": "user", "content": [{"text": scenario.prompt}]}]
        spoken: float | None = None
        tokens = chars = 0
        fired = False

        for _ in range(2):
            content: list[dict] = []
            pending: dict | None = None
            tool_json = ""
            stop = None

            body = self._request(model, scenario, messages)
            for event in self.client.converse_stream(**body)["stream"]:
                if "contentBlockStart" in event:
                    start = event["contentBlockStart"].get("start", {})
                    if "toolUse" in start:
                        pending, tool_json = dict(start["toolUse"]), ""

                elif "contentBlockDelta" in event:
                    delta = event["contentBlockDelta"]["delta"]
                    if delta.get("text"):
                        chars += len(delta["text"])
                        content.append({"text": delta["text"]})
                        if spoken is None:
                            spoken = clock.ms()
                    elif "toolUse" in delta:
                        tool_json += delta["toolUse"].get("input", "")

                elif "contentBlockStop" in event and pending:
                    pending["input"] = json.loads(tool_json or "{}")
                    content.append({"toolUse": pending})
                    pending = None

                elif "messageStop" in event:
                    stop = event["messageStop"]["stopReason"]

                elif "metadata" in event:
                    tokens += event["metadata"].get("usage", {}).get("outputTokens", 0)

            if not scenario.run_loop or stop != "tool_use":
                break

            fired = True
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": self._tool_results(content)})

        return Sample(spoken, clock.ms(), tokens, chars, fired)


# ==========================================================================
# Anthropic direct
# ==========================================================================


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self) -> None:
        import anthropic
        self._sdk_version = anthropic.__version__
        headers = ({"anthropic-workspace-id": ANTHROPIC_WORKSPACE}
                   if ANTHROPIC_WORKSPACE else None)
        # max_retries=0 for the same reason boto3 retries are disabled.
        self.client = anthropic.Anthropic(max_retries=0, default_headers=headers)

    def model_id(self, model):
        return model.anthropic

    def env(self):
        return {"provider": self.name, "anthropic_sdk": self._sdk_version,
                "workspace_scoped": bool(ANTHROPIC_WORKSPACE)}

    # ------------------------------------------------------------------

    def _kwargs(self, model, scenario, messages) -> dict:
        system = ([{"type": "text", "text": scenario.system,
                    "cache_control": {"type": "ephemeral"}}]
                  if scenario.cache else scenario.system)

        kwargs = {
            "model": self.model_id(model),
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": messages,
        }
        if scenario.tools:
            kwargs["tools"] = [{
                "name": TOOL_NAME,
                "description": TOOL_DESC,
                "input_schema": {"type": "object", "properties": TOOL_PROPS,
                                 "required": TOOL_REQUIRED},
            }]
        if scenario.effort:
            # Verified against the API reference and by a clean run: effort is
            # GA, needs no beta header, and nests inside output_config.
            # Omitting it entirely is not "no effort" — it is the default,
            # which is why the effort suite carries a plain baseline row.
            kwargs["output_config"] = {"effort": scenario.effort}

        # temperature is absent because the SDK removed sampling parameters
        # in 1.x. It therefore cannot be matched against Bedrock rows.
        return kwargs

    @staticmethod
    def _tool_results(blocks) -> list[dict]:
        out = []
        for blk in blocks:
            if getattr(blk, "type", None) == "tool_use":
                out.append({"type": "tool_result", "tool_use_id": blk.id,
                            "content": json.dumps(eligibility(**blk.input))})
        return out

    def blocking(self, model, scenario) -> Sample:
        clock = Clock()
        messages = [{"role": "user", "content": scenario.prompt}]
        tokens = chars = cache_read = 0
        fired = False

        for _ in range(2):
            msg = self.client.messages.create(**self._kwargs(model, scenario, messages))
            tokens += msg.usage.output_tokens
            cache_read += getattr(msg.usage, "cache_read_input_tokens", 0) or 0
            chars += sum(len(b.text) for b in msg.content
                         if getattr(b, "type", None) == "text")

            if not scenario.run_loop or msg.stop_reason != "tool_use":
                break

            fired = True
            messages.append({"role": "assistant", "content": msg.content})
            messages.append({"role": "user", "content": self._tool_results(msg.content)})

        return Sample(None, clock.ms(), tokens, chars, fired, cache_read)

    async def streaming(self, model, scenario) -> Sample:
        clock = Clock()
        messages = [{"role": "user", "content": scenario.prompt}]
        spoken: float | None = None
        tokens = chars = cache_read = 0
        fired = False

        for _ in range(2):
            kwargs = self._kwargs(model, scenario, messages)
            kwargs["stream"] = True

            blocks: list[dict] = []
            cur: dict | None = None
            stop = None

            for event in self.client.messages.create(**kwargs):
                kind = event.type

                if kind == "message_start":
                    usage = event.message.usage
                    cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0

                elif kind == "content_block_start":
                    block = event.content_block
                    cur = ({"type": "tool_use", "id": block.id,
                            "name": block.name, "json": ""}
                           if block.type == "tool_use"
                           else {"type": "text", "text": ""})

                elif kind == "content_block_delta":
                    delta = event.delta
                    dtype = getattr(delta, "type", "")
                    # Text deltas only. An input_json_delta is tool arguments,
                    # which never reach TTS and must not set spoken_ms.
                    if dtype == "text_delta" and delta.text:
                        chars += len(delta.text)
                        if cur is not None:
                            cur["text"] = cur.get("text", "") + delta.text
                        if spoken is None:
                            spoken = clock.ms()
                    elif dtype == "input_json_delta" and cur is not None:
                        cur["json"] += delta.partial_json

                elif kind == "content_block_stop" and cur is not None:
                    blocks.append(cur)
                    cur = None

                elif kind == "message_delta":
                    tokens += event.usage.output_tokens
                    stop = getattr(event.delta, "stop_reason", None) or stop

            if not scenario.run_loop or stop != "tool_use":
                break

            fired = True
            assistant, results = [], []
            for block in blocks:
                if block["type"] == "text" and block.get("text"):
                    assistant.append({"type": "text", "text": block["text"]})
                elif block["type"] == "tool_use":
                    args = json.loads(block["json"] or "{}")
                    assistant.append({"type": "tool_use", "id": block["id"],
                                      "name": block["name"], "input": args})
                    results.append({"type": "tool_result", "tool_use_id": block["id"],
                                    "content": json.dumps(eligibility(**args))})
            messages.append({"role": "assistant", "content": assistant})
            messages.append({"role": "user", "content": results})

        return Sample(spoken, clock.ms(), tokens, chars, fired, cache_read)


# ==========================================================================
# Strands, on Bedrock
# ==========================================================================


class StrandsProvider(Provider):
    name = "strands"

    def __init__(self, region: str = DEFAULT_REGION) -> None:
        from botocore.config import Config
        from strands import Agent, tool
        from strands.models import BedrockModel

        self.region = region
        self._Agent = Agent
        self._BedrockModel = BedrockModel
        self._config = Config(retries={"max_attempts": 1, "mode": "standard"})
        self._models: dict[str, object] = {}

        @tool
        def is_eligible(member_id: str, date_of_service: str) -> dict:
            """Check whether a member has active coverage on a date of service.

            Args:
                member_id: Alphanumeric member ID from the member's card.
                date_of_service: Date of service as an ISO date, YYYY-MM-DD.
            """
            return eligibility(member_id, date_of_service)

        self._tool = is_eligible

    def model_id(self, model):
        return model.bedrock

    def supports(self, model, scenario):
        ok, why = super().supports(model, scenario)
        if ok and scenario.cache:
            return False, "prefix caching not implemented for strands"
        return ok, why

    def env(self):
        try:
            import strands
            version = getattr(strands, "__version__", "unknown")
        except Exception:
            version = "unknown"
        return {"provider": self.name, "region": self.region, "strands": version}

    # ------------------------------------------------------------------

    def build_agent(self, model, scenario):
        """Called OUTSIDE the timer. Schema generation is a one-time cost,
        not a per-call one, and timing it would overstate the framework."""
        mid = self.model_id(model)
        if mid not in self._models:
            self._models[mid] = self._BedrockModel(
                model_id=mid, boto_client_config=self._config)
        return self._Agent(model=self._models[mid], system_prompt=scenario.system,
                           tools=[self._tool] if scenario.tools else [])

    @staticmethod
    def _saw_tool(agent) -> bool:
        """Strands hides the loop, so tool use is inferred, not observed.
        If this reads False on lookup rows, print agent.messages and fix
        the probe — it does not affect the timings."""
        try:
            for message in getattr(agent, "messages", []) or []:
                content = message.get("content") if isinstance(message, dict) else None
                for block in content or []:
                    if isinstance(block, dict) and "toolUse" in block:
                        return True
        except Exception:
            pass
        return False

    def blocking(self, model, scenario) -> Sample:
        agent = self.build_agent(model, scenario)
        clock = Clock()
        with quiet():
            result = agent(scenario.prompt)
        return Sample(None, clock.ms(), 0, len(str(result)), self._saw_tool(agent))

    async def streaming(self, model, scenario) -> Sample:
        agent = self.build_agent(model, scenario)
        clock = Clock()
        spoken: float | None = None
        tokens = chars = 0
        fired = False

        with quiet():
            async for chunk in agent.stream_async(scenario.prompt):
                match chunk:
                    # Text deltas only. A toolUse delta is JSON nobody hears.
                    case {"event": {"contentBlockDelta": {"delta": {"text": text}}}} if text:
                        chars += len(text)
                        if spoken is None:
                            spoken = clock.ms()
                    case {"event": {"contentBlockStart": {"start": {"toolUse": _}}}}:
                        fired = True
                    case {"event": {"metadata": {"usage": {"outputTokens": n}}}}:
                        tokens += n

        return Sample(spoken, clock.ms(), tokens, chars,
                      fired or self._saw_tool(agent))


# ==========================================================================

_CACHE: dict[str, Provider] = {}


def get_provider(name: str) -> Provider:
    """Providers are cached so clients and models are built once per run."""
    if name in _CACHE:
        return _CACHE[name]

    if name == "bedrock":
        provider = BedrockProvider()
    elif name.startswith("bedrock:"):
        provider = BedrockProvider(region=name.split(":", 1)[1])
    elif name == "anthropic":
        provider = AnthropicProvider()
    elif name == "strands":
        provider = StrandsProvider()
    else:
        raise SystemExit(f"unknown provider: {name}")

    _CACHE[name] = provider
    return provider


def active_providers() -> list[Provider]:
    return list(_CACHE.values())
