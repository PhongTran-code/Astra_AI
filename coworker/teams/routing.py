"""Deterministic message routing for Astra Team conversations.

Routing is intentionally a pure function. It never calls a model, creates a run,
or grants authority. The durable coordinator persists the source message, calls
this module, authorizes the resulting recipients, and creates deliveries.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .contracts import (
    ActorKind,
    ActorRef,
    ContractError,
    ConversationKind,
    ReplyMode,
    _as_enum,
    _required_id,
    _unique_ids,
)


class RouteReason(str, Enum):
    DIRECT_TARGET = "direct_target"
    EXPLICIT_MENTION = "explicit_mention"
    AT_ALL = "at_all"
    COORDINATOR = "coordinator"
    ALL_MESSAGES = "all_messages"
    NO_AGENT_TARGET = "no_agent_target"
    MENTION_REQUIRED = "mention_required"
    STRUCTURED_HANDOFF_REQUIRED = "structured_handoff_required"


@dataclass(frozen=True)
class ConversationRoutePolicy:
    """Pinned routing policy for one conversation revision."""

    conversation_id: str
    kind: ConversationKind
    member_actor_ids: tuple[str, ...]
    agent_member_ids: tuple[str, ...]
    agent_actor_ids: Optional[tuple[str, ...]] = None
    dispatchable_agent_ids: Optional[tuple[str, ...]] = None
    dispatchable_agent_actor_ids: Optional[tuple[str, ...]] = None
    reply_mode: ReplyMode = ReplyMode.COORDINATOR
    coordinator_agent_id: Optional[str] = None
    allow_at_all: bool = False
    allow_agent_mentions: bool = False
    max_fanout: int = 8

    def __post_init__(self) -> None:
        conversation_id = _required_id(self.conversation_id, "conversation id")
        kind = _as_enum(ConversationKind, self.kind, "conversation kind")
        reply_mode = _as_enum(ReplyMode, self.reply_mode, "reply mode")
        members = _unique_ids(self.member_actor_ids, "member actor id", require_one=False)
        agents = _unique_ids(self.agent_member_ids, "agent member id", require_one=False)
        if not members:
            raise ContractError("conversation requires at least one member")
        agent_actors = (
            agents
            if self.agent_actor_ids is None
            else _unique_ids(self.agent_actor_ids, "agent actor id", require_one=False)
        )
        if len(agent_actors) != len(agents):
            raise ContractError("agent actor mapping must have one actor per agent member")
        unknown_agent_actors = tuple(actor_id for actor_id in agent_actors if actor_id not in members)
        if unknown_agent_actors:
            raise ContractError("every agent member actor must also be a conversation member")
        dispatchable_agents = (
            agents
            if self.dispatchable_agent_ids is None
            else _unique_ids(
                self.dispatchable_agent_ids,
                "dispatchable agent id",
                require_one=False,
            )
        )
        unknown_dispatchable_agents = tuple(
            agent_id for agent_id in dispatchable_agents if agent_id not in agents
        )
        if unknown_dispatchable_agents:
            raise ContractError("every dispatchable agent must be an agent member")
        actor_by_agent = dict(zip(agents, agent_actors))
        dispatchable_agent_actors = (
            tuple(actor_by_agent[agent_id] for agent_id in dispatchable_agents)
            if self.dispatchable_agent_actor_ids is None
            else _unique_ids(
                self.dispatchable_agent_actor_ids,
                "dispatchable agent actor id",
                require_one=False,
            )
        )
        if len(dispatchable_agent_actors) != len(dispatchable_agents):
            raise ContractError("dispatchable agent mapping must have one actor per agent")
        unknown_dispatchable_actors = tuple(
            actor_id for actor_id in dispatchable_agent_actors if actor_id not in agent_actors
        )
        if unknown_dispatchable_actors:
            raise ContractError("every dispatchable agent actor must be an agent member actor")
        coordinator = (
            _required_id(self.coordinator_agent_id, "coordinator agent id")
            if self.coordinator_agent_id
            else None
        )
        if coordinator is not None and coordinator not in agents:
            raise ContractError("coordinator must be an agent member of the conversation")
        if kind is ConversationKind.DIRECT:
            if len(agents) != 1:
                raise ContractError("a direct conversation requires exactly one agent member")
            if reply_mode is not ReplyMode.COORDINATOR:
                raise ContractError("direct conversations use coordinator routing semantics")
            if coordinator is not None and coordinator != agents[0]:
                raise ContractError("direct conversation coordinator must be its agent member")
            coordinator = agents[0]
        if isinstance(self.max_fanout, bool) or not isinstance(self.max_fanout, int):
            raise ContractError("max_fanout must be a positive integer")
        if self.max_fanout <= 0:
            raise ContractError("max_fanout must be a positive integer")

        object.__setattr__(self, "conversation_id", conversation_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "reply_mode", reply_mode)
        object.__setattr__(self, "member_actor_ids", members)
        object.__setattr__(self, "agent_member_ids", agents)
        object.__setattr__(self, "agent_actor_ids", agent_actors)
        object.__setattr__(self, "dispatchable_agent_ids", dispatchable_agents)
        object.__setattr__(self, "dispatchable_agent_actor_ids", dispatchable_agent_actors)
        object.__setattr__(self, "coordinator_agent_id", coordinator)


@dataclass(frozen=True)
class RoutingDecision:
    """Recipients plus the UI/dispatcher gate associated with the decision."""

    conversation_id: str
    target_agent_ids: tuple[str, ...]
    reason: RouteReason
    requires_confirmation: bool = False

    @property
    def dispatchable(self) -> bool:
        return bool(self.target_agent_ids) and not self.requires_confirmation

    @property
    def delivery_count(self) -> int:
        return len(self.target_agent_ids)


def route_message(
    policy: ConversationRoutePolicy,
    sender: ActorRef,
    *,
    mention_actor_ids: tuple[str, ...] = (),
    at_all: bool = False,
    fanout_confirmed: bool = False,
    agent_dialogue_turns: int = 0,
    max_agent_turns: int = 4,
) -> RoutingDecision:
    """Resolve one persisted message to zero or more agent recipients.

    Human messages route according to mention or coordinator policy.
    Agent-authored messages may route to explicitly mentioned peer agents in
    group discussions if governed by the LoopGuard (agent_dialogue_turns < max_agent_turns).
    """
    if not isinstance(policy, ConversationRoutePolicy):
        raise ContractError("policy must be a ConversationRoutePolicy")
    if not isinstance(sender, ActorRef):
        raise ContractError("sender must be an ActorRef")
    if sender.kind is not ActorKind.SYSTEM and sender.id not in policy.member_actor_ids:
        raise ContractError("message sender is not a conversation member")

    mentions = _unique_ids(mention_actor_ids, "mention actor id", require_one=False)
    unknown_mentions = tuple(actor_id for actor_id in mentions if actor_id not in policy.member_actor_ids)
    if unknown_mentions:
        raise ContractError("mentions must reference current conversation members")
    if at_all and mentions:
        raise ContractError("@all cannot be combined with explicit agent mentions")
    if sender.kind is not ActorKind.HUMAN:
        # Allow agent-to-agent explicit mention under LoopGuard only when discussion/agent_mentions is enabled
        if policy.kind is ConversationKind.GROUP and policy.allow_agent_mentions and mentions:
            if agent_dialogue_turns >= max_agent_turns:
                return RoutingDecision(
                    conversation_id=policy.conversation_id,
                    target_agent_ids=(),
                    reason=RouteReason.STRUCTURED_HANDOFF_REQUIRED,
                )
            # Prevent agent from mentioning itself to loop immediately
            peer_mentions = tuple(actor_id for actor_id in mentions if actor_id != sender.id)
            if not peer_mentions:
                return RoutingDecision(
                    conversation_id=policy.conversation_id,
                    target_agent_ids=(),
                    reason=RouteReason.NO_AGENT_TARGET,
                )
            mentioned_peers = tuple(
                agent_id
                for agent_id, actor_id in zip(
                    policy.dispatchable_agent_ids,
                    policy.dispatchable_agent_actor_ids or (),
                )
                if actor_id in peer_mentions
            )
            if mentioned_peers:
                _require_fanout(mentioned_peers, policy.max_fanout)
                return RoutingDecision(
                    conversation_id=policy.conversation_id,
                    target_agent_ids=mentioned_peers,
                    reason=RouteReason.EXPLICIT_MENTION,
                )
        return RoutingDecision(
            conversation_id=policy.conversation_id,
            target_agent_ids=(),
            reason=RouteReason.STRUCTURED_HANDOFF_REQUIRED,
        )
    if policy.kind is ConversationKind.DIRECT:
        return RoutingDecision(
            conversation_id=policy.conversation_id,
            target_agent_ids=policy.dispatchable_agent_ids,
            reason=(
                RouteReason.DIRECT_TARGET
                if policy.dispatchable_agent_ids
                else RouteReason.NO_AGENT_TARGET
            ),
        )
    if at_all:
        if not policy.allow_at_all:
            raise ContractError("@all is disabled for this conversation")
        _require_fanout(policy.dispatchable_agent_ids, policy.max_fanout)
        return RoutingDecision(
            conversation_id=policy.conversation_id,
            target_agent_ids=policy.dispatchable_agent_ids,
            reason=RouteReason.AT_ALL,
            requires_confirmation=not fanout_confirmed,
        )

    mentioned_agents = tuple(
        agent_id
        for agent_id, actor_id in zip(
            policy.dispatchable_agent_ids,
            policy.dispatchable_agent_actor_ids or (),
        )
        if actor_id in mentions
    )
    if mentioned_agents:
        _require_fanout(mentioned_agents, policy.max_fanout)
        return RoutingDecision(
            conversation_id=policy.conversation_id,
            target_agent_ids=mentioned_agents,
            reason=RouteReason.EXPLICIT_MENTION,
        )

    if policy.reply_mode is ReplyMode.MENTION_ONLY:
        reason = RouteReason.NO_AGENT_TARGET if mentions else RouteReason.MENTION_REQUIRED
        return RoutingDecision(policy.conversation_id, (), reason)

    if policy.reply_mode is ReplyMode.COORDINATOR:
        if policy.coordinator_agent_id is None:
            return RoutingDecision(
                policy.conversation_id,
                (),
                RouteReason.MENTION_REQUIRED,
            )
        if policy.coordinator_agent_id not in policy.dispatchable_agent_ids:
            return RoutingDecision(
                policy.conversation_id,
                (),
                RouteReason.NO_AGENT_TARGET,
            )
        return RoutingDecision(
            policy.conversation_id,
            (policy.coordinator_agent_id,),
            RouteReason.COORDINATOR,
        )

    _require_fanout(policy.dispatchable_agent_ids, policy.max_fanout)
    if not policy.dispatchable_agent_ids:
        return RoutingDecision(
            policy.conversation_id,
            (),
            RouteReason.NO_AGENT_TARGET,
        )
    return RoutingDecision(
        policy.conversation_id,
        policy.dispatchable_agent_ids,
        RouteReason.ALL_MESSAGES,
    )


def _require_fanout(agent_ids: tuple[str, ...], limit: int) -> None:
    if len(agent_ids) > limit:
        raise ContractError(
            f"routing fan-out of {len(agent_ids)} exceeds conversation limit {limit}"
        )
