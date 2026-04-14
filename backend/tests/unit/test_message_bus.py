"""Unit tests for the AgentMessageBus."""

from __future__ import annotations

import asyncio

import pytest

from app.protocols.message_bus import AgentMessageBus


@pytest.mark.asyncio
async def test_send_and_receive_direct(redis_client):
    """send_direct publishes a message that can be received via subscribe_direct."""
    bus = AgentMessageBus(redis_client)

    sender_id = "agent-alice"
    recipient_id = "agent-bob"
    content = "Hello, Bob!"

    # Collect received messages
    received: list = []

    async def _subscriber():
        async for msg in bus.subscribe_direct(recipient_id):
            received.append(msg)
            break  # stop after first message

    # Start subscriber first
    subscriber_task = asyncio.create_task(_subscriber())

    # Give subscriber time to subscribe before publishing
    await asyncio.sleep(0.1)

    sent = await bus.send_direct(sender_id, recipient_id, content)

    # Wait for the subscriber to receive the message
    await asyncio.wait_for(subscriber_task, timeout=5.0)

    assert len(received) == 1
    msg = received[0]
    assert msg.sender_id == sender_id
    assert msg.recipient_id == recipient_id
    assert msg.content == content
    assert msg.message_type == "request"
    assert msg.message_id == sent.message_id


@pytest.mark.asyncio
async def test_send_broadcast_capability(redis_client):
    """broadcast_capability publishes to the capability channel."""
    bus = AgentMessageBus(redis_client)

    sender_id = "agent-planner"
    capability = "research"
    content = "New research task available"

    received: list = []

    async def _subscriber():
        async for msg in bus.subscribe_capability(capability):
            received.append(msg)
            break

    subscriber_task = asyncio.create_task(_subscriber())
    await asyncio.sleep(0.1)

    sent = await bus.broadcast_capability(sender_id, capability, content)

    await asyncio.wait_for(subscriber_task, timeout=5.0)

    assert len(received) == 1
    msg = received[0]
    assert msg.sender_id == sender_id
    assert msg.content == content
    assert msg.message_type == "notification"
    assert capability in msg.channel
    assert msg.message_id == sent.message_id


@pytest.mark.asyncio
async def test_request_response(redis_client):
    """request_response sends a request and receives a correlated reply."""
    bus = AgentMessageBus(redis_client)

    requester_id = "agent-requester"
    responder_id = "agent-responder"
    request_content = "What is the status?"
    response_content = "All systems operational."

    import dataclasses
    import json

    from app.protocols.base import AgentMessage
    from app.protocols.message_bus import _DIRECT_CHANNEL_PREFIX

    async def _responder():
        """Listen on the responder's direct channel, then send a correlated reply."""
        channel = f"{_DIRECT_CHANNEL_PREFIX}{responder_id}"
        async with redis_client.pubsub() as ps:
            await ps.subscribe(channel)
            async for raw in ps.listen():
                if raw["type"] != "message":
                    continue
                request = AgentMessage(**json.loads(raw["data"]))
                # Build and publish a correlated response
                reply = AgentMessage(
                    sender_id=responder_id,
                    recipient_id=requester_id,
                    channel=f"{_DIRECT_CHANNEL_PREFIX}{requester_id}",
                    content=response_content,
                    message_type="response",
                    correlation_id=request.correlation_id,
                )
                payload = json.dumps(dataclasses.asdict(reply))
                await redis_client.publish(reply.channel, payload)
                break

    responder_task = asyncio.create_task(_responder())
    await asyncio.sleep(0.1)

    result = await asyncio.wait_for(
        bus.request_response(requester_id, responder_id, request_content, timeout=10.0),
        timeout=12.0,
    )

    await asyncio.wait_for(responder_task, timeout=5.0)

    assert result.content == response_content
    assert result.message_type == "response"
    assert result.sender_id == responder_id
    assert result.recipient_id == requester_id
    # correlation_id must be set and match the original request
    assert result.correlation_id is not None
