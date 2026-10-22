"""Pure routing invariants for Astra Team conversations."""

import pytest

from coworker.teams import ActorKind, ActorRef, ContractError, ConversationKind, ReplyMode
from coworker.teams.routing import (
    ConversationRoutePolicy,
    RouteReason,
    route_message,
)


OWNER = ActorRef(id="human_owner", kind=ActorKind.HUMAN)
AGENT_SENDER = ActorRef(id="agent_builder", kind=ActorKind.AGENT)


def group_policy(**overrides):
    values = {
        "conversation_id": "conversation_1",
        "kind": ConversationKind.GROUP,
        "member_actor_ids": (
            "human_owner",
            "human_guest",
            "agent_lead",
            "agent_builder",
            "agent_reviewer",
        ),
        "agent_member_ids": ("agent_lead", "agent_builder", "agent_reviewer"),
        "reply_mode": ReplyMode.COORDINATOR,
        "coordinator_agent_id": "agent_lead",
        "allow_at_all": True,
        "max_fanout": 4,
    }
    values.update(overrides)
    return ConversationRoutePolicy(**values)


def test_direct_message_always_targets_its_single_agent():
    policy = ConversationRoutePolicy(
        conversation_id="conversation_dm",
        kind=ConversationKind.DIRECT,
        member_actor_ids=("human_owner", "agent_builder"),
        agent_member_ids=("agent_builder",),
    )
    decision = route_message(policy, OWNER)
    assert decision.target_agent_ids == ("agent_builder",)
    assert decision.reason is RouteReason.DIRECT_TARGET
    assert decision.dispatchable


def test_direct_message_rejects_zero_or_multiple_agent_members():
    with pytest.raises(ContractError, match="exactly one"):
        ConversationRoutePolicy(
            conversation_id="conversation_dm",
            kind="direct",
            member_actor_ids=("human_owner",),
            agent_member_ids=(),
        )


def test_direct_message_to_a_paused_or_archived_agent_has_no_dispatch_target():
    policy = ConversationRoutePolicy(
        conversation_id="conversation_archived_dm",
        kind=ConversationKind.DIRECT,
        member_actor_ids=("human_owner", "agent_builder"),
        agent_member_ids=("agent_builder",),
        dispatchable_agent_ids=(),
        dispatchable_agent_actor_ids=(),
    )
    decision = route_message(policy, OWNER)
    assert decision.target_agent_ids == ()
    assert decision.reason is RouteReason.NO_AGENT_TARGET
    assert not decision.dispatchable


def test_mentions_route_in_policy_order_not_input_order():
    decision = route_message(
        group_policy(),
        OWNER,
        mention_actor_ids=("agent_reviewer", "agent_builder", "agent_reviewer"),
    )
    assert decision.target_agent_ids == ("agent_builder", "agent_reviewer")
    assert decision.reason is RouteReason.EXPLICIT_MENTION


def test_human_only_mention_does_not_fall_through_to_coordinator():
    decision = route_message(
        group_policy(reply_mode=ReplyMode.MENTION_ONLY),
        OWNER,
        mention_actor_ids=("human_guest",),
    )
    assert decision.target_agent_ids == ()
    assert decision.reason is RouteReason.NO_AGENT_TARGET


def test_unmentioned_group_message_routes_only_to_coordinator():
    decision = route_message(group_policy(), OWNER)
    assert decision.target_agent_ids == ("agent_lead",)
    assert decision.reason is RouteReason.COORDINATOR


def test_group_without_coordinator_requests_a_mention():
    decision = route_message(group_policy(coordinator_agent_id=None), OWNER)
    assert decision.target_agent_ids == ()
    assert decision.reason is RouteReason.MENTION_REQUIRED
    assert not decision.dispatchable


def test_mention_only_mode_never_wakes_an_unmentioned_agent():
    decision = route_message(group_policy(reply_mode="mention_only"), OWNER)
    assert decision.target_agent_ids == ()
    assert decision.reason is RouteReason.MENTION_REQUIRED


def test_all_messages_mode_routes_to_each_agent_member():
    decision = route_message(group_policy(reply_mode="all_messages"), OWNER)
    assert decision.target_agent_ids == (
        "agent_lead",
        "agent_builder",
        "agent_reviewer",
    )
    assert decision.reason is RouteReason.ALL_MESSAGES


def test_at_all_requires_preview_confirmation_before_dispatch():
    preview = route_message(group_policy(), OWNER, at_all=True)
    assert preview.target_agent_ids == group_policy().agent_member_ids
    assert preview.delivery_count == 3
    assert preview.requires_confirmation
    assert not preview.dispatchable

    confirmed = route_message(
        group_policy(),
        OWNER,
        at_all=True,
        fanout_confirmed=True,
    )
    assert confirmed.dispatchable
    assert not confirmed.requires_confirmation


def test_at_all_must_be_enabled_and_cannot_mix_with_mentions():
    with pytest.raises(ContractError, match="disabled"):
        route_message(group_policy(allow_at_all=False), OWNER, at_all=True)
    with pytest.raises(ContractError, match="cannot be combined"):
        route_message(
            group_policy(),
            OWNER,
            at_all=True,
            mention_actor_ids=("agent_builder",),
        )


def test_every_route_respects_the_fanout_limit():
    with pytest.raises(ContractError, match="exceeds"):
        route_message(group_policy(max_fanout=2), OWNER, at_all=True)
    with pytest.raises(ContractError, match="exceeds"):
        route_message(
            group_policy(max_fanout=1),
            OWNER,
            mention_actor_ids=("agent_builder", "agent_reviewer"),
        )


def test_free_form_agent_message_never_wakes_another_agent():
    decision = route_message(
        group_policy(),
        AGENT_SENDER,
        mention_actor_ids=("agent_reviewer",),
    )
    assert decision.target_agent_ids == ()
    assert decision.reason is RouteReason.STRUCTURED_HANDOFF_REQUIRED


def test_sender_and_mentions_must_reference_conversation_members():
    with pytest.raises(ContractError, match="sender"):
        route_message(
            group_policy(),
            ActorRef(id="human_outsider", kind="human"),
        )
    with pytest.raises(ContractError, match="mentions"):
        route_message(
            group_policy(),
            OWNER,
            mention_actor_ids=("agent_outsider",),
        )


def test_policy_rejects_nonmember_agent_and_coordinator():
    with pytest.raises(ContractError, match="every agent member"):
        group_policy(agent_member_ids=("agent_outsider",))
    with pytest.raises(ContractError, match="coordinator"):
        group_policy(coordinator_agent_id="agent_outsider")
