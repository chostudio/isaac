"""LLM task planner: a tool-use loop over verified robot skills.

The model never emits joint angles or coordinates. It calls two skills,
`get_scene` and `pick_and_place`, and every `pick_and_place` result is
re-derived from simulator poses, so the model plans against what actually
happened rather than what it asked for.

Providers (all speak the same two tools):
- ollama:    free, local open model via Ollama's OpenAI-compatible API (default)
- nvidia:    NVIDIA API Catalog (build.nvidia.com) hosted models, NVIDIA_API_KEY
- anthropic: Claude, ANTHROPIC_API_KEY
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from .backend import Backend
from .world import DEFAULT_BLOCKS, TABLE_ZONES

MAX_TURNS = 16

PROVIDERS = {
    "ollama": {"model": "qwen3:8b", "base_url": "http://localhost:11434/v1", "key_env": None},
    "nvidia": {"model": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
               "base_url": "https://integrate.api.nvidia.com/v1", "key_env": "NVIDIA_API_KEY"},
    "anthropic": {"model": "claude-opus-5-5", "base_url": None, "key_env": "ANTHROPIC_API_KEY"},
}

SYSTEM_PROMPT = """You are the task planner for a Franka Panda robot arm in NVIDIA Isaac Sim.
The arm works on a tabletop with three cubes (red_cube, green_cube, blue_cube) and three
empty table zones (table_left, table_center, table_right).

You act only through tools:
- get_scene: current block positions and what each block rests on.
- pick_and_place: move ONE clear block onto another clear block or an empty table zone.
  The result is measured from the simulator after the move, so trust it over your plan.

How to work:
- Call get_scene before planning. Break the user's request into single moves and make
  exactly one tool call per turn.
- A block with something on top cannot be picked, and a block with something on top
  cannot receive another block. Clear blocks first if needed (move the top block to a
  free table zone).
- If a move reports success=false after executing, look at the returned scene and retry
  or re-plan. Give up on a move after 2 failed attempts and say so.
- If the request is impossible, ambiguous, or names something that is not in the scene
  (e.g. a purple cube), make no moves and explain briefly.
- Do not move blocks the request did not require.

Finish with one or two plain sentences describing the final arrangement."""

ONE_MOVE_PER_TURN = "not executed: one tool call per turn; call it again after reading the previous result"


def tool_specs() -> list[dict]:
    """Provider-neutral tool specs: name, description, JSON schema."""
    objects = sorted(DEFAULT_BLOCKS)
    return [
        {
            "name": "get_scene",
            "description": "Read the current scene: each block's position (meters), what it rests on, and which table zones are free.",
            "schema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "pick_and_place",
            "description": "Pick up one clear block and place it on top of another clear block or into an empty table zone. Returns whether the block was verified at the target, plus the updated scene.",
            "schema": {
                "type": "object",
                "properties": {
                    "object": {"type": "string", "enum": objects, "description": "Block to pick up."},
                    "target": {
                        "type": "string",
                        "enum": objects + sorted(TABLE_ZONES),
                        "description": "Block to stack onto, or a table zone.",
                    },
                },
                "required": ["object", "target"],
                "additionalProperties": False,
            },
        },
    ]


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    text: str
    calls: list[ToolCall]
    refused: bool = False


class AnthropicChat:
    """Claude via the Messages API. History keeps full content blocks (thinking included)."""

    def __init__(self, model: str, client=None, effort: str = "medium"):
        import anthropic

        self.client = client or anthropic.Anthropic()
        self.model, self.effort = model, effort
        self.tools = [
            {"name": t["name"], "description": t["description"], "input_schema": t["schema"], "strict": True}
            for t in tool_specs()
        ]
        self.messages: list = []

    def user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def tool_results(self, results: list[tuple[str, dict, bool]]) -> None:
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": cid, "content": json.dumps(out), "is_error": err}
            for cid, out, err in results
        ]})

    def next(self) -> Turn:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=self.tools,
            # One skill per turn: the planner always sees the verified result of a move before choosing the next.
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            messages=self.messages,
            output_config={"effort": self.effort},
            # Route a safety decline to the server's default fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            return Turn("The planner declined this request.", [], refused=True)
        self.messages.append({"role": "assistant", "content": response.content})
        return Turn(
            text=" ".join(b.text for b in response.content if b.type == "text").strip(),
            calls=[ToolCall(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"],
        )


class OpenAICompatChat:
    """Any OpenAI-compatible endpoint: Ollama locally, or NVIDIA's API Catalog."""

    def __init__(self, model: str, base_url: str, api_key: str, client=None, reasoning_effort: str | None = None):
        if client is None:
            from openai import OpenAI

            client = OpenAI(base_url=base_url, api_key=api_key)
        self.client, self.model, self.reasoning_effort = client, model, reasoning_effort
        self.tools = [
            {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["schema"]}}
            for t in tool_specs()
        ]
        self.messages: list = [{"role": "system", "content": SYSTEM_PROMPT}]

    def user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def tool_results(self, results: list[tuple[str, dict, bool]]) -> None:
        for cid, out, _ in results:
            self.messages.append({"role": "tool", "tool_call_id": cid, "content": json.dumps(out)})

    def next(self) -> Turn:
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        response = self.client.chat.completions.create(
            model=self.model, messages=self.messages, tools=self.tools, temperature=0.2, **extra,
        )
        msg = response.choices[0].message
        calls = []
        for c in msg.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_unparseable": c.function.arguments}
            calls.append(ToolCall(c.id, c.function.name, args))
        self.messages.append({
            "role": "assistant",
            "content": msg.content or "",
            **({"tool_calls": [c.model_dump() for c in msg.tool_calls]} if msg.tool_calls else {}),
        })
        # Reasoning models (e.g. Qwen3) may inline their chain of thought; keep only the answer.
        text = re.sub(r"<think>.*?</think>", "", msg.content or "", flags=re.DOTALL).strip()
        return Turn(text, calls)


def make_chat(provider: str = "ollama", model: str | None = None, client=None, effort: str = "low"):
    cfg = PROVIDERS[provider]
    model = model or cfg["model"]
    if provider == "anthropic":
        return AnthropicChat(model, client=client, effort=effort)
    key = os.environ.get(cfg["key_env"], "") if cfg["key_env"] else "ollama"
    if cfg["key_env"] and not key and client is None:
        raise SystemExit(f"{provider} needs {cfg['key_env']} set")
    # Ollama maps reasoning_effort onto thinking models like Qwen3. With "none", Qwen3 is fast but
    # misplans multi-step stacks; "low" plans them correctly.
    reasoning = effort if provider == "ollama" else None
    return OpenAICompatChat(model, cfg["base_url"], key, client=client, reasoning_effort=reasoning)


@dataclass
class Step:
    tool: str
    args: dict
    result: dict


@dataclass
class RunResult:
    command: str
    reply: str
    steps: list[Step] = field(default_factory=list)

    @property
    def moves(self) -> list[Step]:
        return [s for s in self.steps if s.tool == "pick_and_place" and "success" in s.result]


class LanguageAgent:
    def __init__(self, backend: Backend, provider: str = "ollama", model: str | None = None,
                 client=None, effort: str = "low", verbose: bool = True):
        self.backend = backend
        self.provider, self.model, self.client, self.effort = provider, model, client, effort
        self.verbose = verbose

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    def _run_tool(self, call: ToolCall) -> dict:
        if call.name == "get_scene":
            return self.backend.scene().describe()
        if call.name == "pick_and_place":
            obj, target = call.args["object"], call.args["target"]
            self._log(f"  -> pick_and_place({obj} -> {target})")
            result = self.backend.pick_and_place(obj, target)
            self._log(f"     {'OK' if result.success else 'FAIL'}: {result.message}")
            return result.to_tool_result()
        raise ValueError(f"unknown tool {call.name}")

    def run(self, command: str) -> RunResult:
        self._log(f"\n> {command}")
        run = RunResult(command=command, reply="")
        # Fresh conversation per command: the scene, not chat history, carries state between commands.
        chat = make_chat(self.provider, self.model, self.client, self.effort)
        chat.user(command)

        for _ in range(MAX_TURNS):
            turn = self.backend.wait_for(chat.next)
            if turn.refused or not turn.calls:
                run.reply = turn.text
                break

            results = []
            for i, call in enumerate(turn.calls):
                if i > 0:
                    output, is_error = {"error": ONE_MOVE_PER_TURN}, True
                else:
                    try:
                        output, is_error = self._run_tool(call), False
                    except Exception as exc:  # surface any skill failure to the model
                        output, is_error = {"error": f"{type(exc).__name__}: {exc}"}, True
                run.steps.append(Step(call.name, call.args, output))
                results.append((call.id, output, is_error))
            chat.tool_results(results)
        else:
            run.reply = f"Stopped after {MAX_TURNS} planning turns."

        self._log(f"< {run.reply}")
        return run
