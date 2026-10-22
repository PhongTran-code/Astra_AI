"""Canonical Team capability binding and sandbox execution tests."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from threading import Event

import pytest

from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.server import SessionManager
from coworker.teams import (
    ActorKind,
    ActorRef,
    AgentRole,
    ComputerObservation,
    CoordinatorAuthorizationError,
)


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append(
            {"model": model, "messages": messages, "tools": tools, "settings": settings}
        )
        return self.turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _text(value: str) -> AssistantTurn:
    return AssistantTurn(text=value, finish_reason="stop")


def _call(name: str, arguments: dict, call_id: str) -> AssistantTurn:
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)]
    )


def _agent(
    manager,
    provider,
    *,
    capabilities,
    sandbox_policy,
    base_role=AgentRole.BUILDER,
    display_name="Sandbox worker",
    handle="sandbox-worker",
    budget_policy=None,
):
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    return manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name=display_name,
        handle=handle,
        instructions="Use the available tools and report what actually happened.",
        base_role=base_role,
        capabilities=capabilities,
        sandbox_policy=sandbox_policy,
        budget_policy=budget_policy,
    )


def test_declared_tools_execute_in_private_sandbox_and_count_usage(tmp_path):
    provider = ScriptedProvider(
        [
            _call(
                "write_file",
                {"path": "nested/result.txt", "content": "written by agent"},
                "write-1",
            ),
            _call(
                "run_shell",
                {"command": ["/bin/echo", "sandbox-ok"]},
                "shell-1",
            ),
            _text("done"),
        ]
    )
    manager = SessionManager(workspace=tmp_path, provider=provider)
    agent = _agent(
        manager,
        provider,
        capabilities=(
            {"capability_kind": "tool", "capability_ref": "write_file", "required": True},
            {"capability_kind": "tool", "capability_ref": "shell", "required": True},
        ),
        sandbox_policy={
            "actions": ["sandbox.read", "sandbox.write", "run.execute"],
            "max_wall_seconds": 10,
        },
    )
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=(agent["agent_id"],)
    )
    submitted = manager.team_service.send_message(
        conversation["conversation_id"],
        body="Create the file and run the check.",
        client_request_id="capability-run-1",
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert result is not None and result["status"] == "succeeded"
    sandbox = manager.team_sandboxes.get_sandbox_for_agent(agent["agent_id"])
    assert sandbox is not None
    assert (Path(sandbox["home_ref"]) / "nested" / "result.txt").read_text() == "written by agent"
    usage = manager.team_work.get_usage(submitted.request["request_id"])
    assert usage["tool_calls"] == 2
    assert provider.calls[0]["tools"] is not None
    names = {tool["function"]["name"] for tool in provider.calls[0]["tools"]}
    assert names == {"write_file", "run_shell"}
    assert "sandbox-ok" in provider.calls[2]["messages"][-1]["content"]


def test_required_tool_without_sandbox_authority_fails_closed(tmp_path):
    provider = ScriptedProvider([_text("should not run")])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    agent = _agent(
        manager,
        provider,
        capabilities=({"capability_kind": "tool", "capability_ref": "write_file", "required": True},),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
    )
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=(agent["agent_id"],)
    )
    submitted = manager.team_service.send_message(
        conversation["conversation_id"],
        body="Run a command.",
        client_request_id="capability-deny-1",
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert result is not None and result["status"] == "failed"
    assert "requires sandbox action sandbox.write" in result["error"]
    assert provider.calls == []
    assert manager.team_work.get_delivery(submitted.deliveries[0]["delivery_id"])["status"] == "retry_wait"


def test_empty_sandbox_actions_deny_model_execution_before_provider_call(tmp_path):
    provider = ScriptedProvider([_text("must not run")])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    agent = _agent(
        manager,
        provider,
        capabilities=(),
        sandbox_policy={"actions": []},
    )
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=(agent["agent_id"],)
    )
    with pytest.raises(CoordinatorAuthorizationError, match="run.execute"):
        manager.team_service.send_message(
            conversation["conversation_id"],
            body="Execution is disabled for this revision.",
            client_request_id="empty-sandbox-actions-1",
        )

    assert provider.calls == []


def test_write_tool_cannot_escape_private_sandbox(tmp_path):
    provider = ScriptedProvider(
        [
            _call(
                "write_file",
                {"path": "../outside.txt", "content": "must not escape"},
                "escape-1",
            ),
            _text("the write was rejected"),
        ]
    )
    manager = SessionManager(workspace=tmp_path, provider=provider)
    agent = _agent(
        manager,
        provider,
        capabilities=({"capability_kind": "tool", "capability_ref": "write_file", "required": True},),
        sandbox_policy={"actions": ["sandbox.read", "sandbox.write", "run.execute"]},
    )
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=(agent["agent_id"],)
    )
    manager.team_service.send_message(
        conversation["conversation_id"],
        body="Write outside.",
        client_request_id="capability-escape-1",
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert result is not None and result["status"] == "succeeded"
    assert not (tmp_path / "outside.txt").exists()


def test_cancel_live_team_run_prevents_reply_after_model_race(tmp_path):
    started = Event()
    release = Event()

    class BlockingProvider(ScriptedProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            self.calls.append({"model": model, "messages": messages, "tools": tools})
            started.set()
            assert release.wait(5)
            return _text("late reply must be discarded")

    provider = BlockingProvider([])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    agent = _agent(
        manager,
        provider,
        capabilities=(),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
    )
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=(agent["agent_id"],)
    )
    submitted = manager.team_service.send_message(
        conversation["conversation_id"],
        body="Start a cancellable run.",
        client_request_id="cancel-live-1",
    )

    async def run_and_cancel():
        task = asyncio.create_task(manager.drain_team_work_once(agent["agent_id"]))
        assert await asyncio.to_thread(started.wait, 5)
        with manager.team_database._lock:
            row = manager.team_database._conn.execute(
                "SELECT run_id FROM runs WHERE request_id = ? ORDER BY created_at DESC LIMIT 1",
                (submitted.request["request_id"],),
            ).fetchone()
        assert row is not None
        canceled = manager.cancel_canonical_team_run(
            row["run_id"], reason="user stopped the run"
        )
        release.set()
        result = await task
        return canceled, result, row["run_id"]

    canceled, result, run_id = asyncio.run(run_and_cancel())

    assert canceled["run"]["status"] == "canceled"
    assert result is not None and result["status"] == "canceled"
    assert manager.team_work.get_run(run_id)["status"] == "canceled"
    assert manager.team_work.get_delivery(submitted.deliveries[0]["delivery_id"])["status"] == "canceled"
    messages = manager.team_conversations.list_messages(
        conversation["conversation_id"], requester_actor_id=manager.team_actor_id
    )["messages"]
    assert [message["body"] for message in messages] == ["Start a cancellable run."]


def test_consequential_tool_failure_is_not_blindly_retried(tmp_path):
    class FailAfterShell(ProviderClient):
        def __init__(self):
            self.calls = 0

        def complete(self, *, model, messages, tools=None, **settings):
            self.calls += 1
            if self.calls == 1:
                return _call(
                    "run_shell",
                    {"command": ["/bin/echo", "state-changed"]},
                    "shell-side-effect-1",
                )
            raise RuntimeError("provider failed after command")

        def capabilities(self, model):
            return ModelCapabilities()

    provider = FailAfterShell()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    agent = _agent(
        manager,
        provider,
        capabilities=({"capability_kind": "tool", "capability_ref": "shell", "required": True},),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
    )
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=(agent["agent_id"],)
    )
    submitted = manager.team_service.send_message(
        conversation["conversation_id"],
        body="Run the state-changing command.",
        client_request_id="non-retryable-side-effect-1",
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert result is not None and result["status"] == "failed"
    assert provider.calls == 2
    delivery = manager.team_work.get_delivery(submitted.deliveries[0]["delivery_id"])
    assert delivery["status"] == "dead_letter"


def test_retry_replays_durable_output_without_invoking_model_again(tmp_path):
    provider = ScriptedProvider([])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    agent = _agent(
        manager,
        provider,
        capabilities=(),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
    )
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=(agent["agent_id"],)
    )
    submitted = manager.team_service.send_message(
        conversation["conversation_id"],
        body="Recover the already-generated answer.",
        client_request_id="replay-durable-output-1",
    )
    lease = manager._claim_canonical_team_delivery(agent["agent_id"])
    assert lease is not None
    request = manager.team_work.get_request(submitted.request["request_id"])
    assert request is not None
    installation = manager.team_agents.get_installation(agent["agent_id"])
    assert installation is not None
    sandbox = manager.team_sandboxes.get_sandbox_for_agent(agent["agent_id"])
    assert sandbox is not None
    manager.team_coordinator.start_delivery(
        lease,
        run_id="replay-output-run-1",
        agent_revision_id=installation["active_revision_id"],
        sandbox_id=sandbox["sandbox_id"],
    )
    manager.team_coordinator.record_run_output(
        "replay-output-run-1",
        actor_id=installation["actor_id"],
        text="The answer was already generated.",
    )
    manager.team_coordinator.settle_run(
        "replay-output-run-1",
        "failed",
        lease_token=lease["lease_token"],
        retry_policy={"max_attempts": 2, "base_delay_seconds": 1},
    )
    retry_lease = manager.team_work.lease_delivery(
        submitted.deliveries[0]["delivery_id"], "worker-replay"
    )

    result = asyncio.run(manager.team_runtime.run_delivery(retry_lease))

    assert result["status"] == "succeeded"
    assert provider.calls == []
    messages = manager.team_conversations.list_messages(
        conversation["conversation_id"], requester_actor_id=manager.team_actor_id
    )["messages"]
    assert [message["body"] for message in messages] == [
        "Recover the already-generated answer.",
        "The answer was already generated.",
    ]


def test_computer_capability_uses_pinned_computer_and_closes_run_session(tmp_path):
    class BrowserProvider:
        provider_kind = "fake_browser"

        def provision(self, *_args):
            return None

        def reconnect(self, _computer_id, session_ref):
            return session_ref or "fake-page"

        def suspend(self, *_args):
            return None

        def destroy(self, *_args):
            return None

        def observe(self, session_ref):
            return ComputerObservation(session_ref, url="https://example.test", title="Example")

        def screenshot(self, _session_ref):
            return b"png"

        def navigate(self, session_ref, url):
            return ComputerObservation(session_ref, url=url, title="Next")

        def action(self, session_ref, action):
            return ComputerObservation(session_ref, state={"kind": action["kind"]})

        def checkpoint(self, _session_ref):
            return {"checkpoint_ref": "safe"}

        def transfer_file(self, *_args, **_kwargs):
            return {}

        def close_session(self, _session_ref):
            return None

    provider = ScriptedProvider(
        [
            _call("computer_action", {"kind": "click", "target": "submit"}, "computer-1"),
            _text("clicked"),
        ]
    )
    manager = SessionManager(workspace=tmp_path, provider=provider)
    manager.team_computers.provider = BrowserProvider()
    agent = _agent(
        manager,
        provider,
        capabilities=({"capability_kind": "tool", "capability_ref": "computer", "required": True},),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
        display_name="Browser worker",
        handle="browser-worker",
    )
    manager.team_computer_repository.create_computer(
        "computer_runtime",
        manager.team_workspace_id,
        agent["agent_id"],
        "fake_browser",
    )
    manager.team_computers.provision("computer_runtime")
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=(agent["agent_id"],)
    )
    manager.team_service.send_message(
        conversation["conversation_id"],
        body="Click submit",
        client_request_id="computer-capability-1",
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert result is not None and result["status"] == "succeeded"
    with manager.team_database._lock:
        session = manager.team_database._conn.execute(
            "SELECT status FROM computer_sessions WHERE run_id = ?",
            (result["run"]["run_id"],),
        ).fetchone()
    assert session is not None and session["status"] == "closed"
    assert any(
        item["function"]["name"] == "computer_action"
        for item in provider.calls[0]["tools"]
    )


def test_computer_human_takeover_parks_and_resumes_the_same_run(tmp_path):
    class BrowserProvider:
        provider_kind = "fake_browser"

        def provision(self, *_args):
            return None

        def reconnect(self, _computer_id, session_ref):
            return session_ref or "fake-page"

        def suspend(self, *_args):
            return None

        def destroy(self, *_args):
            return None

        def observe(self, session_ref):
            return ComputerObservation(session_ref, url="https://example.test", title="Example")

        def screenshot(self, _session_ref):
            return b"png"

        def navigate(self, session_ref, url):
            return ComputerObservation(session_ref, url=url, title="Next")

        def action(self, session_ref, action):
            return ComputerObservation(session_ref, state={"kind": action["kind"]})

        def checkpoint(self, _session_ref):
            return {"checkpoint_ref": "safe"}

        def transfer_file(self, *_args, **_kwargs):
            return {}

        def close_session(self, _session_ref):
            return None

    provider = ScriptedProvider(
        [
            _call(
                "computer_request_takeover",
                {"reason": "Complete the MFA challenge"},
                "takeover-1",
            ),
            _text("MFA completed and the task is done."),
        ]
    )
    manager = SessionManager(workspace=tmp_path, provider=provider)
    manager.team_computers.provider = BrowserProvider()
    agent = _agent(
        manager,
        provider,
        capabilities=({"capability_kind": "tool", "capability_ref": "computer", "required": True},),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
        display_name="Takeover worker",
        handle="takeover-worker",
    )
    manager.team_computer_repository.create_computer(
        "computer_takeover",
        manager.team_workspace_id,
        agent["agent_id"],
        "fake_browser",
    )
    manager.team_computers.provision("computer_takeover")
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=(agent["agent_id"],)
    )
    submitted = manager.team_service.send_message(
        conversation["conversation_id"],
        body="Complete the MFA-protected task.",
        client_request_id="computer-takeover-1",
    )

    waiting = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert waiting is not None and waiting["status"] == "waiting_human"
    assert waiting["run"]["status"] == "waiting_input"
    takeover_id = waiting["takeover_request_id"]
    assert manager.team_computer_repository.get_takeover(takeover_id)["status"] == "pending"
    session = manager.team_computer_repository.get_session(
        f"computer-session-{__import__('hashlib').sha256(waiting['run']['run_id'].encode()).hexdigest()}"
    )
    assert session is not None and session["status"] == "waiting_human"

    manager.team_computers.decide_takeover(
        takeover_id,
        "approved",
        actor_id=manager.team_actor_id,
    )
    resumed = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert resumed is not None and resumed["status"] == "succeeded"
    assert resumed["run"]["run_id"] == waiting["run"]["run_id"]
    assert len(manager.team_work.list_runs_for_request(submitted.request["request_id"])) == 1
    assert manager.team_computer_repository.get_takeover(takeover_id)["status"] == "approved"


def test_team_shell_secret_is_injected_ephemerally_and_redacted_from_model_context(tmp_path):
    provider = ScriptedProvider(
        [
            _call(
                "run_shell",
                {
                    "command": [
                        __import__("sys").executable,
                        "-c",
                        "import os; from pathlib import Path; print(os.environ['CI_TOKEN']); Path('leak.txt').write_text(os.environ['CI_TOKEN'])",
                    ]
                },
                "secret-shell-1",
            ),
            _call(
                "read_file",
                {"path": "leak.txt"},
                "secret-read-1",
            ),
            _text("secret check completed"),
        ]
    )
    manager = SessionManager(workspace=tmp_path, provider=provider)
    manager.secrets.put("team:ci", {"token": "team-secret-value"})
    agent = _agent(
        manager,
        provider,
        capabilities=(
            {"capability_kind": "tool", "capability_ref": "run_shell", "required": True},
            {"capability_kind": "tool", "capability_ref": "read_file", "required": True},
        ),
        sandbox_policy={
            "actions": ["sandbox.read", "run.execute"],
            "secret_refs": [
                {"profile": "team:ci", "field": "token", "environment": "CI_TOKEN"}
            ],
        },
    )
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=(agent["agent_id"],)
    )
    manager.team_service.send_message(
        conversation["conversation_id"],
        body="Run the CI secret check.",
        client_request_id="secret-shell-1",
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert result is not None and result["status"] == "succeeded"
    serialized_calls = repr(provider.calls)
    assert "team-secret-value" not in serialized_calls
    assert "[REDACTED]" in serialized_calls
    assert manager.team_secret_broker.active_lease_count() == 0


def test_request_wall_budget_times_out_model_and_dead_letters_delivery(tmp_path):
    class SlowProvider(ProviderClient):
        def __init__(self):
            self.calls = 0

        def complete(self, *, model, messages, tools=None, **settings):
            self.calls += 1
            time.sleep(1.2)
            return _text("late answer must not be published")

        def capabilities(self, model):
            return ModelCapabilities()

    provider = SlowProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    agent = _agent(
        manager,
        provider,
        capabilities=(),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
        display_name="Budget worker",
        handle="budget-worker",
    )
    task = manager.team_service.create_task(
        title="Bounded task",
        acceptance_criteria="The worker must finish inside the request budget.",
        owner_agent_id=agent["agent_id"],
    )
    submitted = manager.team_service.dispatch_task(
        task["task_id"],
        budget={"max_wall_seconds": 1},
        client_request_id="wall-budget-1",
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert result is not None and result["status"] == "timed_out"
    assert result["run"]["status"] == "timed_out"
    assert result["delivery"]["status"] == "dead_letter"
    assert manager.team_work.get_request(submitted.request["request_id"])["status"] == "completed"
    task_thread = manager.team_conversations.get_thread(task["canonical_thread_id"])
    assert task_thread is not None
    messages = manager.team_conversations.list_messages(
        task_thread["conversation_id"], requester_actor_id=manager.team_actor_id
    )["messages"]
    assert all("late answer" not in message["body"] for message in messages)


def test_usage_budget_exhaustion_dead_letters_without_retry(tmp_path):
    provider = ScriptedProvider(
        [
            _call(
                "run_shell",
                {"command": ["/bin/echo", "one bounded tool"]},
                "quota-shell-1",
            ),
            _text("second model response must not be accepted"),
        ]
    )
    manager = SessionManager(workspace=tmp_path, provider=provider)
    agent = _agent(
        manager,
        provider,
        capabilities=({"capability_kind": "tool", "capability_ref": "shell", "required": True},),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
        budget_policy={"max_model_calls": 1},
        display_name="Quota worker",
        handle="quota-worker",
    )
    task = manager.team_service.create_task(
        title="Quota task",
        acceptance_criteria="The worker stops when its model-call quota is exhausted.",
        owner_agent_id=agent["agent_id"],
    )
    submitted = manager.team_service.dispatch_task(
        task["task_id"], client_request_id="model-quota-1"
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert result is not None and result["status"] == "timed_out"
    assert result["run"]["status"] == "timed_out"
    assert result["delivery"]["status"] == "dead_letter"
    assert result["delivery"]["attempts"][-1]["error_code"] == "BudgetExceeded"
    assert manager.team_work.get_request(submitted.request["request_id"])["status"] == "completed"
    assert manager.team_work.get_usage(submitted.request["request_id"])["model_calls"] == 1
    assert asyncio.run(manager.drain_team_work_once(agent["agent_id"])) is None
    assert len(provider.calls) == 2


def test_agent_budget_policy_caps_requested_task_budget(tmp_path):
    provider = ScriptedProvider([])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    agent = _agent(
        manager,
        provider,
        capabilities=(),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
        budget_policy={"max_model_calls": 1, "max_wall_seconds": 5},
        display_name="Capped worker",
        handle="capped-worker",
    )
    task = manager.team_service.create_task(
        title="Capped task",
        acceptance_criteria="The request uses the agent's immutable budget cap.",
        owner_agent_id=agent["agent_id"],
    )

    submitted = manager.team_service.dispatch_task(
        task["task_id"],
        budget={"max_model_calls": 9, "max_wall_seconds": 30},
        client_request_id="budget-policy-cap-1",
    )

    assert submitted.request is not None
    assert submitted.request["budget"] == {
        "max_tokens": None,
        "max_cost_micros": None,
        "max_model_calls": 1,
        "max_tool_calls": None,
        "max_wall_seconds": 5,
        "max_wakes": None,
        "max_handoffs": None,
        "max_fanout": None,
    }


def test_token_budget_reaches_provider_as_output_cap(tmp_path):
    provider = ScriptedProvider([_text("bounded output")])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    agent = _agent(
        manager,
        provider,
        capabilities=(),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
        budget_policy={"max_tokens": 12},
        display_name="Token worker",
        handle="token-worker",
    )
    task = manager.team_service.create_task(
        title="Token bounded task",
        acceptance_criteria="The provider receives the effective output cap.",
        owner_agent_id=agent["agent_id"],
    )
    submitted = manager.team_service.dispatch_task(
        task["task_id"],
        budget={"max_tokens": 40},
        client_request_id="token-budget-1",
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert result is not None and result["status"] == "succeeded"
    assert submitted.request["budget"]["max_tokens"] == 12
    assert provider.calls[0]["settings"]["max_tokens"] == 12


def test_agent_role_cannot_gain_a_tool_outside_its_authority(tmp_path):
    provider = ScriptedProvider([_text("should not run")])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    agent = _agent(
        manager,
        provider,
        capabilities=({"capability_kind": "tool", "capability_ref": "write_file", "required": True},),
        sandbox_policy={"actions": ["sandbox.read", "sandbox.write", "run.execute"]},
        base_role=AgentRole.VERIFIER,
    )
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=(agent["agent_id"],)
    )
    submitted = manager.team_service.send_message(
        conversation["conversation_id"],
        body="Write a file.",
        client_request_id="role-authority-1",
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert result is not None and result["status"] == "failed"
    assert "agent role authority" in result["error"]
    assert provider.calls == []
    assert manager.team_work.get_delivery(submitted.deliveries[0]["delivery_id"])["status"] == "retry_wait"


def test_agent_can_post_idempotent_progress_to_current_group_without_waking_peers(tmp_path):
    provider = ScriptedProvider(
        [
            _call(
                "team_send_message",
                {
                    "body": "Research checkpoint is ready.",
                    "message_key": "research-checkpoint-1",
                },
                "team-message-1",
            ),
            _text("checkpoint posted"),
        ]
    )
    manager = SessionManager(workspace=tmp_path, provider=provider)
    agent = _agent(
        manager,
        provider,
        capabilities=({"capability_kind": "tool", "capability_ref": "team_send_message", "required": True},),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
        display_name="Research worker",
        handle="research-worker",
    )
    peer = _agent(
        manager,
        provider,
        capabilities=(),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
        display_name="Builder peer",
        handle="builder-peer",
    )
    conversation = manager.team_service.create_conversation(
        kind="group",
        agent_ids=(agent["agent_id"], peer["agent_id"]),
        reply_mode="mention_only",
    )
    submitted = manager.team_service.send_message(
        conversation["conversation_id"],
        body="Research this and keep the group posted.",
        mention_actor_ids=(
            manager.team_agents.get_installation(agent["agent_id"])["actor_id"],
        ),
        client_request_id="team-message-run-1",
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert result is not None and result["status"] == "succeeded"
    messages = manager.team_conversations.list_messages(
        conversation["conversation_id"], requester_actor_id=manager.team_actor_id
    )["messages"]
    assert [message["body"] for message in messages] == [
        "Research this and keep the group posted.",
        "Research checkpoint is ready.",
        "checkpoint posted",
    ]
    assert messages[1]["author_actor_id"] == manager.team_agents.get_installation(
        agent["agent_id"]
    )["actor_id"]
    usage = manager.team_work.get_usage(submitted.request["request_id"])
    assert usage["tool_calls"] == 1
    # A progress post is a conversation fact, not an implicit delivery to the peer.
    assert manager.team_work.list_ready_deliveries(peer["agent_id"]) == []

    # Replaying the same request/key is idempotent at the message layer.
    posted = manager.team_service.post_agent_message(
        conversation["conversation_id"],
        agent_id=agent["agent_id"],
        body="Research checkpoint is ready.",
        message_key="research-checkpoint-1",
        request_id=submitted.request["request_id"],
        run_id="run-replay",
        correlation_id=submitted.request["correlation_id"],
    )
    assert posted["message_id"] == messages[1]["message_id"]
    assert len(
        manager.team_conversations.list_messages(
            conversation["conversation_id"], requester_actor_id=manager.team_actor_id
        )["messages"]
    ) == 3


def test_task_transition_cancels_descendant_tasks_and_work(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Task worker",
        handle="task-worker",
        instructions="Work on the assigned task.",
    )
    parent = manager.team_service.create_task(
        title="Stop parent",
        acceptance_criteria="Parent is stopped safely.",
    )
    child = manager.team_service.create_task(
        title="Stop child",
        acceptance_criteria="Child is stopped safely.",
        parent_task_id=parent["task_id"],
    )
    context = {agent["agent_id"]: manager.team_service._permission_context(agent["agent_id"])}
    submitted = manager.team_coordinator.submit_task(
        request_id="task-cancel-child-request",
        workspace_id=manager.team_workspace_id,
        task_id=child["task_id"],
        requester=human,
        target_agent_ids=(agent["agent_id"],),
        instruction="Run the child task.",
        idempotency_key="task-cancel-child-request-key",
        correlation_id="task-cancel-child-trace",
        context_by_agent=context,
    )

    updated = manager.team_service.transition_task(parent["task_id"], "canceled")

    assert updated["status"] == "canceled"
    assert updated["cancellation"]["canceled_task_ids"] == [
        parent["task_id"],
        child["task_id"],
    ]
    assert manager.team_tasks.get_task(child["task_id"])["status"] == "canceled"
    assert manager.team_work.get_request(submitted.request["request_id"])["status"] == "canceled"
    assert updated["cancellation"]["canceled_run_ids"] == []


def test_dispatch_task_queues_assigned_owner_and_replays_one_request(tmp_path):
    provider = ScriptedProvider([_text("task work finished")])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    agent = _agent(
        manager,
        provider,
        capabilities=(),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
        display_name="Task owner",
        handle="task-owner",
    )
    task = manager.team_service.create_task(
        title="Run assigned task",
        description="Perform the requested work.",
        acceptance_criteria="The owner returns a result.",
        owner_agent_id=agent["agent_id"],
    )

    submitted = manager.team_service.dispatch_task(
        task["task_id"], client_request_id="task-dispatch-1"
    )

    assert submitted.request is not None
    assert manager.team_tasks.get_task(task["task_id"])["status"] == "in_progress"
    assert submitted.request["source_kind"] == "task"
    assert submitted.request["task_id"] == task["task_id"]
    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))
    assert result is not None and result["status"] == "succeeded"
    assert manager.team_work.get_request(submitted.request["request_id"])["status"] == "completed"

    replay = manager.team_service.dispatch_task(
        task["task_id"], client_request_id="task-dispatch-1"
    )
    assert replay.request is not None
    assert replay.request["request_id"] == submitted.request["request_id"]
    assert len(replay.deliveries) == 1
    assert len(provider.calls) == 1


def test_agent_can_enqueue_idempotent_task_handoff_to_another_agent(tmp_path):
    provider = ScriptedProvider(
        [
            _call(
                "team_handoff",
                {
                    "recipient_agent_id": "recipient-agent",
            "purpose": "Review the implementation and report blockers.",
            "expected_output": "A concise review report.",
            "handoff_key": "review-1",
            "budget": {"max_model_calls": 9, "max_wall_seconds": 30},
                },
                "handoff-1",
            ),
            _text("review handoff queued"),
        ]
    )
    manager = SessionManager(workspace=tmp_path, provider=provider)
    sender = _agent(
        manager,
        provider,
        capabilities=({"capability_kind": "tool", "capability_ref": "team_handoff", "required": True},),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
        display_name="Builder sender",
        handle="builder-sender",
    )
    recipient = _agent(
        manager,
        provider,
        capabilities=(),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
        base_role=AgentRole.VERIFIER,
        budget_policy={"max_model_calls": 2, "max_wall_seconds": 7},
        display_name="Verifier recipient",
        handle="verifier-recipient",
    )
    provider.turns[0].tool_calls[0].arguments["recipient_agent_id"] = recipient["agent_id"]
    task = manager.team_service.create_task(
        title="Review implementation",
        acceptance_criteria="A review report is returned.",
        owner_agent_id=sender["agent_id"],
    )
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    submitted = manager.team_coordinator.submit_task(
        request_id="task-handoff-request",
        workspace_id=manager.team_workspace_id,
        task_id=task["task_id"],
        requester=human,
        target_agent_ids=(sender["agent_id"],),
        instruction="Delegate the review to the verifier.",
        idempotency_key="task-handoff-request-key",
        correlation_id="task-handoff-trace",
        context_by_agent={
            sender["agent_id"]: manager.team_service._permission_context(sender["agent_id"])
        },
    )

    result = asyncio.run(manager.drain_team_work_once(sender["agent_id"]))

    assert result is not None and result["status"] == "succeeded"
    sender_installation = manager.team_agents.get_installation(sender["agent_id"])
    assert sender_installation is not None
    handoffs = manager.team_service.list_handoffs(task["task_id"])
    assert len(handoffs) == 1
    assert handoffs[0]["sender_agent_id"] == sender["agent_id"]
    assert handoffs[0]["recipient_agent_id"] == recipient["agent_id"]
    assert handoffs[0]["requester_actor_id"] == sender_installation["actor_id"]
    assert handoffs[0]["status"] == "pending"
    request = manager.team_work.get_request(
        manager.team_work.list_request_ids_for_scope("task", task["task_id"])[-1]
    )
    assert request is not None
    assert request["source_kind"] == "handoff"
    assert request["causation_id"] == result["run"]["run_id"]
    assert request["target_agent_ids"] == [recipient["agent_id"]]
    assert request["budget"]["max_model_calls"] == 2
    assert request["budget"]["max_wall_seconds"] == 7
    assert len(manager.team_work.list_ready_deliveries(recipient["agent_id"])) == 1

    # The same request/key can be replayed after a worker crash without creating
    # another handoff, request, or delivery.
    replay = manager.team_service.create_agent_handoff(
        task["task_id"],
        sender_agent_id=sender["agent_id"],
        recipient_agent_id=recipient["agent_id"],
        purpose="Review the implementation and report blockers.",
        expected_output="A concise review report.",
        request_id=submitted.request["request_id"],
        handoff_key="review-1",
        run_id="run-replay",
        correlation_id="task-handoff-trace",
    )
    assert replay.request["request_id"] == request["request_id"]
    assert len(manager.team_service.list_handoffs(task["task_id"])) == 1
    assert len(manager.team_work.list_ready_deliveries(recipient["agent_id"])) == 1


def test_team_agent_reasoning_and_web_tools_execution(tmp_path):
    """Verify reasoning capture, tool execution (web_search/web_fetch), and message metadata."""
    turn1 = AssistantTurn(
        tool_calls=[
            ToolCall(
                id="search-call-1",
                name="web_search",
                arguments={"query": "Astra desktop agent", "max_results": 3},
            )
        ],
        reasoning="User is asking about Astra desktop agent. Let me search the web for details.",
    )
    turn2 = AssistantTurn(
        text="Astra is a local-first desktop AI coworker platform.",
        reasoning="Now I have the search results. I will provide a clear, concise answer.",
        finish_reason="stop",
    )
    provider = ScriptedProvider([turn1, turn2])
    manager = SessionManager(workspace=tmp_path, provider=provider)

    agent = _agent(
        manager,
        provider,
        capabilities=(
            {"capability_kind": "tool", "capability_ref": "web_search", "required": True},
            {"capability_kind": "tool", "capability_ref": "web_fetch", "required": False},
        ),
        sandbox_policy={"actions": ["sandbox.read", "run.execute"]},
        display_name="Web Researcher",
        handle="researcher",
    )

    conv = manager.team_service.create_conversation(
        kind="direct",
        agent_ids=[agent["agent_id"]],
    )
    conv_id = conv["conversation_id"]

    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    manager.team_coordinator.submit_message(
        message_id="msg-web-1",
        conversation_id=conv_id,
        sender=human,
        body="What is Astra?",
        correlation_id="trace-web-1",
        context_by_agent={agent["agent_id"]: manager.team_service._permission_context(agent["agent_id"])},
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))
    assert result is not None
    assert result["status"] == "succeeded"

    # Check messages in conversation
    msgs = manager.team_conversations.list_messages(conv_id)["messages"]
    agent_msg = next((m for m in msgs if m["author_actor_id"] != manager.team_actor_id), None)
    assert agent_msg is not None
    assert "Astra is a local-first desktop" in agent_msg["body"]
    assert agent_msg["reasoning"] == "Now I have the search results. I will provide a clear, concise answer."
    assert agent_msg["metadata"] is not None
    assert "tool_calls" in agent_msg["metadata"]
    assert len(agent_msg["metadata"]["tool_calls"]) > 0
    assert agent_msg["metadata"]["tool_calls"][0]["name"] == "web_search"

    # Check run events include reasoning delta or tool events
    run_events = manager.team_work.list_run_events(result["run"]["run_id"])
    event_types = [e["event_type"] for e in run_events]
    assert "agent.reasoning.delta" in event_types or "tool.started" in event_types or "tool.finished" in event_types
