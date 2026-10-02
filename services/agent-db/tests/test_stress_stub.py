"""Stub-LLM contract tests (§4).

A stub is an instrument, so its contract is the thing that has to be pinned: the
latency model §4 demands (a function of prompt length, not a constant), the
per-request timings that make ``platform_overhead`` exact, and the ability to
emit a deterministic tool call in the exact shape
``LiteLLMProvider._tool_call`` accepts.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from agent_db.stress.stub_llm import (
    LatencyModel,
    StubServer,
    build_response,
    parse_chat_request,
)


class TestLatencyModel:
    def test_latency_grows_with_the_prompt(self):
        model = LatencyModel(p50_ms=100.0, prompt_ms_per_token=2.0)
        short = model.sample(prompt_tokens=10, draw=0.5)
        long = model.sample(prompt_tokens=100, draw=0.5)
        assert long > short
        assert short == pytest.approx(120.0)
        assert long == pytest.approx(300.0)

    def test_the_draw_is_the_quantile_of_the_configured_p50_p95(self):
        # §4 gives the profile a nominal p50 and p95, so the model is written in
        # those terms instead of in invented distribution parameters: draw is the
        # quantile, and the tail is taken above 1 - tail_probability.
        model = LatencyModel(p50_ms=100.0, p95_ms=150.0, tail_probability=0.05)
        assert model.sample(prompt_tokens=0, draw=0.0) == pytest.approx(100.0)
        assert model.sample(prompt_tokens=0, draw=0.5) == pytest.approx(100.0)
        assert model.sample(prompt_tokens=0, draw=0.95) == pytest.approx(150.0)
        assert model.sample(prompt_tokens=0, draw=1.0) == pytest.approx(150.0)

    def test_the_prompt_term_is_added_on_both_branches(self):
        # §4: the latency must be a function of prompt length, otherwise the
        # 8000-token history trim is invisible in platform_overhead.
        model = LatencyModel(
            p50_ms=100.0, p95_ms=150.0, prompt_ms_per_token=1.0, tail_probability=0.5
        )
        assert model.sample(prompt_tokens=10, draw=0.0) == pytest.approx(110.0)
        assert model.sample(prompt_tokens=10, draw=0.9) == pytest.approx(160.0)

    def test_from_profile_uses_the_configured_quantiles(self):
        model = LatencyModel.from_profile(
            p50_ms=800, p95_ms=2500, prompt_ms_per_token=2.0, tail_probability=0.05
        )
        assert model.p50_ms == 800
        assert model.p95_ms == 2500
        assert model.sample(prompt_tokens=0, draw=0.5) == pytest.approx(800.0)
        assert model.sample(prompt_tokens=0, draw=0.99) == pytest.approx(2500.0)

    def test_a_negative_or_inverted_setting_is_refused(self):
        with pytest.raises(ValueError):
            LatencyModel(p50_ms=-1.0)
        with pytest.raises(ValueError, match="p95"):
            LatencyModel(p50_ms=100.0, p95_ms=50.0)
        with pytest.raises(ValueError, match="tail_probability"):
            LatencyModel(p50_ms=1.0, tail_probability=1.5)


class TestRequestParsing:
    def test_the_openai_fields_are_read(self):
        request = parse_chat_request(
            {
                "model": "stub",
                "messages": [{"role": "user", "content": "привет"}],
                "temperature": 0.2,
                "tools": [{"type": "function", "function": {"name": "db_get"}}],
            }
        )
        assert request.model == "stub"
        assert request.messages[0]["content"] == "привет"
        assert request.tools and request.tools[0]["function"]["name"] == "db_get"

    def test_tool_names_come_from_the_request_not_from_a_config(self):
        request = parse_chat_request(
            {
                "model": "stub",
                "messages": [],
                "tools": [
                    {"type": "function", "function": {"name": "db_map"}},
                    {"type": "function", "function": {"name": "db_get"}},
                    {"not_a_function": True},
                ],
            }
        )
        assert request.tool_names() == ["db_map", "db_get"]

    def test_a_message_that_is_not_a_list_is_refused(self):
        with pytest.raises(ValueError, match="messages"):
            parse_chat_request({"model": "stub", "messages": "nope"})


class TestToolCallEmission:
    def test_the_script_wins_and_its_arguments_are_json_strings(self):
        request = parse_chat_request(
            {
                "model": "stub",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {"name": "db_get"}}],
            }
        )
        response = build_response(
            request,
            script=[{"tool": "db_get", "arguments": {"entity": "group", "id": "g1"}}],
            script_index=0,
            content_when_done="готово",
        )
        call = response["choices"][0]["message"]["tool_calls"][0]
        assert call["id"] == "stub-call-0"
        assert call["type"] == "function"
        assert call["function"]["name"] == "db_get"
        # LiteLLM hands ``arguments`` through as a string; the provider parses it.
        assert isinstance(call["function"]["arguments"], str)
        assert json.loads(call["function"]["arguments"]) == {
            "entity": "group",
            "id": "g1",
        }

    def test_the_script_is_consumed_round_by_round(self):
        request = parse_chat_request({"model": "stub", "messages": [], "tools": []})
        script = [{"tool": "db_map", "arguments": {}}]
        assert build_response(request, script=script, script_index=0)[
            "choices"
        ][0]["message"]["tool_calls"]
        # Past the end of the script the turn is finished with text: that is how
        # a multi-round dialogue is modelled at all (§4: the scripted provider
        # resets its cursor and can only ever replay round 1).
        final = build_response(
            request, script=script, script_index=1, content_when_done="готово"
        )
        assert "tool_calls" not in final["choices"][0]["message"]
        assert final["choices"][0]["message"]["content"] == "готово"

    def test_a_scripted_tool_the_request_does_not_advertise_is_refused(self):
        request = parse_chat_request(
            {
                "model": "stub",
                "messages": [],
                "tools": [{"type": "function", "function": {"name": "db_get"}}],
            }
        )
        # Handing back a tool the agent never offered produces a provider
        # protocol error inside api-service, which reads as a platform failure.
        # Refusing here names the real cause.
        with pytest.raises(ValueError, match="not advertised"):
            build_response(
                request, script=[{"tool": "db_map", "arguments": {}}], script_index=0
            )

    def test_rounds_count_only_the_current_turn_not_the_resent_history(self):
        # api-service flattens every stored turn (tool answers included) into
        # the history it resends before the new user message. A whole-
        # transcript count would drift upward each turn and exhaust the script
        # after two or three turns of one session - the drift bug the audit
        # caught before it could quietly remove all tool-path metrics.
        prior_turn = [
            {"role": "user", "content": "[stress:one_tool:1] ..."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "db_get", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "{}"},
            {"role": "assistant", "content": "готово"},
        ]
        request = parse_chat_request(
            {
                "model": "stub",
                "messages": [*prior_turn, {"role": "user", "content": "[stress:one_tool:2] ..."}],
                "tools": [{"type": "function", "function": {"name": "db_get"}}],
            }
        )
        # The prior turn's tool answer is history; the current turn is brand
        # new, so the script starts from its first round again.
        assert request.completed_tool_rounds() == 0

        answered = parse_chat_request(
            {
                "model": "stub",
                "messages": [
                    *prior_turn,
                    {"role": "user", "content": "..."},
                    {"role": "assistant", "content": "", "tool_calls": []},
                    {"role": "tool", "tool_call_id": "call-2", "content": "{}"},
                ],
                "tools": [{"type": "function", "function": {"name": "db_get"}}],
            }
        )
        assert answered.completed_tool_rounds() == 1

    def test_usage_reports_prompt_and_completion_tokens(self):
        request = parse_chat_request(
            {
                "model": "stub",
                "messages": [{"role": "user", "content": "a" * 400}],
                "tools": [],
            }
        )
        response = build_response(request, script=[], script_index=0)
        usage = response["usage"]
        assert usage["prompt_tokens"] > 0
        assert usage["total_tokens"] == usage["prompt_tokens"] + usage[
            "completion_tokens"
        ]


class TestCompositeToolResolution:
    """A script's logical tool name resolves to the advertised (possibly
    composite) name.

    A named agent spanning several tenants gets composite tools from the gateway
    (``{tenant}__db_map``: ``tools.go:177``), but the shipped script and the
    profiles carry logical names (``db_map``). The stub used to compare names
    exactly and so refused every composite turn - the L2 failure. It now reuses
    ``canonical_tool_name``/``composite_tool_name`` (``profile.py``) to resolve
    logical -> advertised, the same rule the manifest validation already applies.
    """

    @staticmethod
    def _request(*tools: str):
        return parse_chat_request(
            {
                "model": "stub",
                "messages": [],
                "tools": [
                    {"type": "function", "function": {"name": name}}
                    for name in tools
                ],
            }
        )

    def test_a_logical_script_tool_resolves_to_the_composite_advertised_name(self):
        request = self._request("stress-1__db_map")
        response = build_response(
            request, script=[{"tool": "db_map", "arguments": {}}], script_index=0
        )
        call = response["choices"][0]["message"]["tool_calls"][0]
        assert call["function"]["name"] == "stress-1__db_map"

    def test_a_script_step_selects_the_tenant_under_composite_scope(self):
        request = self._request("stress-1__db_map", "stress-2__db_map")
        response = build_response(
            request,
            script=[{"tool": "db_map", "tenant": "stress-2", "arguments": {}}],
            script_index=0,
        )
        call = response["choices"][0]["message"]["tool_calls"][0]
        assert call["function"]["name"] == "stress-2__db_map"

    def test_an_ambiguous_logical_tool_under_composite_needs_a_tenant(self):
        # Two tenants expose the same canonical tool and the step names no
        # tenant: silently funnelling every turn to one tenant would report a
        # two-tenant load that is really one. Refusing names the real cause.
        request = self._request("stress-1__db_map", "stress-2__db_map")
        with pytest.raises(ValueError, match="ambiguous"):
            build_response(
                request, script=[{"tool": "db_map", "arguments": {}}], script_index=0
            )

    def test_a_truly_unadvertised_tool_is_still_refused(self):
        # The guard is preserved: resolution never invents a tool the agent did
        # not advertise (db_map is not db_get under any prefix).
        request = self._request("stress-1__db_get")
        with pytest.raises(ValueError, match="not advertised"):
            build_response(
                request, script=[{"tool": "db_map", "arguments": {}}], script_index=0
            )

    def test_an_already_composite_script_name_is_emitted_verbatim(self):
        request = self._request("stress-1__db_map")
        response = build_response(
            request,
            script=[{"tool": "stress-1__db_map", "arguments": {}}],
            script_index=0,
        )
        call = response["choices"][0]["message"]["tool_calls"][0]
        assert call["function"]["name"] == "stress-1__db_map"


class TestHttpSurface:
    """The server end to end, on a real socket on loopback."""

    @pytest.fixture
    def server(self, tmp_path):
        instance = StubServer(
            latency=LatencyModel(p50_ms=5.0),
            script=[{"tool": "db_get", "arguments": {"entity": "group", "id": "g1"}}],
            log_path=tmp_path / "stub.jsonl",
            concurrency=2,
        )
        instance.start()
        yield instance
        instance.stop()

    def _post(self, url: str, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                # The anti-abuse UA blocklist would refuse a bare python client;
                # the stub is not the platform, but keeping the header realistic
                # means one less difference between a probe and the load.
                "User-Agent": "Mozilla/5.0 (stress-harness)",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode())

    def test_health_and_completions_are_served(self, server, tmp_path):
        with urllib.request.urlopen(f"{server.base_url}/health", timeout=5) as health:
            assert health.status == 200
            assert json.loads(health.read().decode())["status"] == "ok"

        status, body = self._post(
            f"{server.base_url}/v1/chat/completions",
            {
                "model": "stub",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {"name": "db_get"}}],
            },
        )
        assert status == 200
        assert body["object"] == "chat.completion"
        assert body["choices"][0]["finish_reason"] == "tool_calls"

        entries = [
            json.loads(line)
            for line in (tmp_path / "stub.jsonl").read_text().splitlines()
        ]
        assert len(entries) == 1
        entry = entries[0]
        # §4: the stub's own timings, so platform_overhead is exact rather than
        # inferred. service_ms is what the driver subtracts.
        for field in (
            "arrival_ms",
            "service_start_ms",
            "service_ms",
            "queue_wait_ms",
            "prompt_tokens",
        ):
            assert field in entry
        assert entry["service_ms"] >= 0
        assert entry["queue_wait_ms"] >= 0

    def test_the_correlation_header_is_recorded_for_joining(self, server, tmp_path):
        payload = {
            "model": "stub",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [],
        }
        request = urllib.request.Request(
            f"{server.base_url}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Correlation-ID": "stress-42",
                "User-Agent": "Mozilla/5.0 (stress-harness)",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10):
            pass
        entry = json.loads(
            (tmp_path / "stub.jsonl").read_text().splitlines()[-1]
        )
        assert entry["correlation_id"] == "stress-42"

    def test_an_unknown_path_is_a_404_and_an_unreadable_body_a_400(self, server):
        with pytest.raises(urllib.error.HTTPError) as not_found:
            urllib.request.urlopen(f"{server.base_url}/v1/models", timeout=5)
        assert not_found.value.code == 404

        request = urllib.request.Request(
            f"{server.base_url}/v1/chat/completions",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as bad_body:
            urllib.request.urlopen(request, timeout=5)
        assert bad_body.value.code == 400

    def test_concurrency_is_capped_and_the_excess_waits_in_the_stub_queue(
        self, tmp_path
    ):
        # §4: when the stub saturates, its own queue lands inside duration_ms, so
        # the queue has to be visible. One slot + three requests means at least
        # two of them wait.
        instance = StubServer(
            latency=LatencyModel(p50_ms=60.0),
            script=[],
            log_path=tmp_path / "stub.jsonl",
            concurrency=1,
        )
        instance.start()
        try:
            results: list[dict] = []
            lock = threading.Lock()

            def hit() -> None:
                _, body = self._post(
                    f"{instance.base_url}/v1/chat/completions",
                    {"model": "stub", "messages": [{"role": "user", "content": "x"}]},
                )
                with lock:
                    results.append(body)

            threads = [threading.Thread(target=hit) for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
        finally:
            instance.stop()

        assert len(results) == 3
        entries = [
            json.loads(line)
            for line in (tmp_path / "stub.jsonl").read_text().splitlines()
        ]
        assert len(entries) == 3
        assert max(entry["queue_wait_ms"] for entry in entries) > 30.0
