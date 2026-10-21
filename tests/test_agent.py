"""Offline tests: a scripted stand-in for Claude drives the real loop and mock backend."""

import json
from types import SimpleNamespace as NS

from franka_agent.agent import LanguageAgent
from franka_agent.backend import MockBackend
from franka_agent.evals import CASES


def tool_use(name, **args):
    return NS(type="tool_use", id=f"tu_{name}_{len(args)}", name=name, input=args)


def text(t):
    return NS(type="text", text=t)


class FakeClient:
    """Replays a fixed list of model turns and records every request it received."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs["messages"], default=lambda o: vars(o))))
        content = self.turns.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
        return NS(stop_reason=stop, content=content)


def last_tool_result(client):
    return json.loads(client.requests[-1][-1]["content"][0]["content"])


def test_simple_stack():
    backend = MockBackend()
    client = FakeClient([
        [tool_use("get_scene")],
        [tool_use("pick_and_place", object="red_cube", target="blue_cube")],
        [text("Red is on blue.")],
    ])
    run = LanguageAgent(backend, provider="anthropic", client=client, verbose=False).run("put red on blue")
    assert backend.scene().support["red_cube"] == "blue_cube"
    assert run.reply == "Red is on blue." and len(run.moves) == 1
    assert last_tool_result(client)["success"] is True


def test_failed_grasp_is_reported_from_sim_state():
    backend = MockBackend(drop_rate=1.0, seed=0)
    client = FakeClient([
        [tool_use("pick_and_place", object="red_cube", target="blue_cube")],
        [text("It slipped.")],
    ])
    LanguageAgent(backend, provider="anthropic", client=client, verbose=False).run("put red on blue")
    result = last_tool_result(client)
    assert result["success"] is False and "verification failed" in result["message"]
    assert backend.scene().support["red_cube"] == "table"


def test_covered_block_is_refused_without_moving():
    backend = MockBackend()
    backend.pick_and_place("red_cube", "blue_cube")
    backend.moves = 0
    client = FakeClient([
        [tool_use("pick_and_place", object="blue_cube", target="green_cube")],
        [text("Blue is covered.")],
    ])
    LanguageAgent(backend, provider="anthropic", client=client, verbose=False).run("put blue on green")
    assert "red_cube on top" in last_tool_result(client)["message"]
    assert backend.moves == 0


def test_eval_goals_hold_for_scripted_solutions():
    """Each eval goal is reachable, and the untouched scene fails every goal that requires moves."""
    solutions = {
        "simple_stack": [("red_cube", "blue_cube")],
        "zone_move": [("green_cube", "table_left")],
        "three_tower": [("green_cube", "blue_cube"), ("red_cube", "green_cube")],
        "implicit_middle": [("blue_cube", "green_cube"), ("red_cube", "blue_cube")],
        "needs_clearing": [("red_cube", "table_center"), ("blue_cube", "red_cube")],
        "unstack": [("red_cube", "table_left"), ("green_cube", "table_center")],
        "unknown_object": [],
        "self_reference": [],
    }
    for case in CASES:
        backend = MockBackend()
        for move in case.setup:
            assert backend.pick_and_place(*move).success
        if case.min_moves:
            assert not case.goal(backend.scene()), case.name
        for move in solutions[case.name]:
            assert backend.pick_and_place(*move).success, (case.name, move)
        assert case.goal(backend.scene()), case.name
        assert len(solutions[case.name]) == case.min_moves, case.name


class FakeOpenAI:
    """Same idea for the OpenAI-compatible path (Ollama / NVIDIA API Catalog)."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests, self.kwargs = [], []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs["messages"])))
        self.kwargs.append({k: v for k, v in kwargs.items() if k not in ("messages", "tools")})
        calls, content = self.turns.pop(0)
        tool_calls = [
            NS(id=f"call_{i}", function=NS(name=n, arguments=json.dumps(a)),
               model_dump=lambda i=i, n=n, a=a: {"id": f"call_{i}", "type": "function",
                                                 "function": {"name": n, "arguments": json.dumps(a)}})
            for i, (n, a) in enumerate(calls)
        ] or None
        return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls))])


def test_openai_compat_path_runs_one_move_per_turn():
    backend = MockBackend()
    client = FakeOpenAI([
        # A model that tries two moves at once only gets the first executed.
        ([("pick_and_place", {"object": "green_cube", "target": "blue_cube"}),
          ("pick_and_place", {"object": "red_cube", "target": "green_cube"})], ""),
        ([("pick_and_place", {"object": "red_cube", "target": "green_cube"})], ""),
        ([], "<think>done?</think>Tower built."),
    ])
    run = LanguageAgent(backend, provider="ollama", client=client, verbose=False).run("tower")
    assert run.reply == "Tower built."
    assert backend.moves == 2
    tool_msgs = [m for m in client.requests[1] if m["role"] == "tool"]
    assert "one tool call per turn" in tool_msgs[1]["content"]
    scene = backend.scene()
    assert scene.support["green_cube"] == "blue_cube" and scene.support["red_cube"] == "green_cube"


def test_hallucinated_object_is_rejected_by_skill_layer():
    backend = MockBackend()
    client = FakeOpenAI([
        ([("pick_and_place", {"object": "purple_cube", "target": "red_cube"})], ""),
        ([], "There is no purple cube."),
    ])
    LanguageAgent(backend, provider="ollama", client=client, verbose=False).run("purple on red")
    assert backend.moves == 0
    assert "unknown object" in client.requests[1][-1]["content"]


def test_nemotron_is_default_and_sends_reasoning_switch():
    client = FakeOpenAI([([], "Nothing to do.")])
    LanguageAgent(MockBackend(), client=client, verbose=False).run("hello")
    sent = client.kwargs[0]
    assert sent["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert sent["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True, "low_effort": True}}
    assert "reasoning_effort" not in sent


class FakeTranslateClient:
    """Stands in for Riva Translate: records the direction and returns canned text."""

    def __init__(self, table):
        self.table, self.directions = table, []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        direction, text = kwargs["messages"][0]["content"], kwargs["messages"][1]["content"]
        self.directions.append(direction)
        return NS(choices=[NS(message=NS(content=self.table[text]))])


def test_translator_wraps_planner_in_english():
    from franka_agent.translate import Translator

    translate = FakeTranslateClient({
        "pon el cubo rojo sobre el azul": "put the red cube on the blue one",
        "Red is on blue.": "El rojo está sobre el azul.",
    })
    planner = FakeOpenAI([
        ([("pick_and_place", {"object": "red_cube", "target": "blue_cube"})], ""),
        ([], "Red is on blue."),
    ])
    backend = MockBackend()
    agent = LanguageAgent(backend, client=planner, verbose=False,
                          translator=Translator("es-US", client=translate))
    run = agent.run("pon el cubo rojo sobre el azul")
    assert planner.requests[0][1]["content"] == "put the red cube on the blue one"
    assert run.reply == "El rojo está sobre el azul."
    assert translate.directions == ["es-us-en", "en-es-us"]
    assert backend.scene().support["red_cube"] == "blue_cube"
