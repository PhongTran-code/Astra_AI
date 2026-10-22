import pytest
from coworker.teams.contracts import ActorKind, ActorRef, ConversationKind
from coworker.teams.routing import ConversationRoutePolicy, ReplyMode, RouteReason, route_message

def test_agent_to_agent_routing_with_loop_guard():
    policy = ConversationRoutePolicy(
        conversation_id="conv-1",
        kind=ConversationKind.GROUP,
        member_actor_ids=("human-1", "agent-actor-1", "agent-actor-2"),
        agent_member_ids=("agent-1", "agent-2"),
        agent_actor_ids=("agent-actor-1", "agent-actor-2"),
        dispatchable_agent_ids=("agent-1", "agent-2"),
        dispatchable_agent_actor_ids=("agent-actor-1", "agent-actor-2"),
        reply_mode=ReplyMode.MENTION_ONLY,
        allow_agent_mentions=True,
    )

    sender_agent_1 = ActorRef("agent-actor-1", ActorKind.AGENT, "ArchitectBot")

    # 1. Agent 1 mentions Agent 2 -> should route to Agent 2 with discussion turn tracking
    decision = route_message(
        policy,
        sender_agent_1,
        mention_actor_ids=("agent-actor-2",),
        agent_dialogue_turns=1,
        max_agent_turns=4,
    )
    assert decision.target_agent_ids == ("agent-2",)
    assert decision.reason == RouteReason.EXPLICIT_MENTION

    # 2. Agent tries to mention itself -> rejected to prevent immediate loop
    decision_self = route_message(
        policy,
        sender_agent_1,
        mention_actor_ids=("agent-actor-1",),
        agent_dialogue_turns=1,
        max_agent_turns=4,
    )
    assert decision_self.target_agent_ids == ()

    # 3. Exceeded max turns (LoopGuard triggers)
    decision_limit = route_message(
        policy,
        sender_agent_1,
        mention_actor_ids=("agent-actor-2",),
        agent_dialogue_turns=5,
        max_agent_turns=4,
    )
    assert decision_limit.target_agent_ids == ()
    assert decision_limit.reason == RouteReason.STRUCTURED_HANDOFF_REQUIRED
