from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis


class KeyPattern:
    CONV_STATE = "conv:{conversation_id}:state"
    AGENT_MEM = "agent:{agent_name}:mem:{key}"
    ROUTING_CACHE = "route:{fingerprint}"
    FEATURE_FLAG = "flag:{name}"
    TASK_STATUS = "task:{task_id}:status"
    TASK_INPUT = "task:{task_id}:input"
    TASK_OUTPUT = "task:{task_id}:output"
    TASK_APPROVAL = "task:{task_id}:approval"

    @staticmethod
    def conv_state(conversation_id: str) -> str:
        return KeyPattern.CONV_STATE.format(conversation_id=conversation_id)

    @staticmethod
    def agent_mem(agent_name: str, key: str) -> str:
        return KeyPattern.AGENT_MEM.format(agent_name=agent_name, key=key)

    @staticmethod
    def routing_cache(fingerprint: str) -> str:
        return KeyPattern.ROUTING_CACHE.format(fingerprint=fingerprint)

    @staticmethod
    def feature_flag(name: str) -> str:
        return KeyPattern.FEATURE_FLAG.format(name=name)

    @staticmethod
    def task_status(task_id: str) -> str:
        return KeyPattern.TASK_STATUS.format(task_id=task_id)

    @staticmethod
    def task_input(task_id: str) -> str:
        return KeyPattern.TASK_INPUT.format(task_id=task_id)

    @staticmethod
    def task_output(task_id: str) -> str:
        return KeyPattern.TASK_OUTPUT.format(task_id=task_id)

    @staticmethod
    def task_approval(task_id: str) -> str:
        return KeyPattern.TASK_APPROVAL.format(task_id=task_id)


class RedisStore:
    def __init__(self, url: str) -> None:
        self._url = url
        self._client: redis.Redis | None = None

    async def open(self) -> None:
        if self._client is not None:
            return
        self._client = redis.from_url(self._url, decode_responses=True)
        await self._client.ping()

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None

    @property
    def client(self) -> redis.Redis:
        if self._client is None:
            raise RuntimeError("RedisStore is not open; call await store.open() first")
        return self._client

    async def save_conv_state(self, conversation_id: str, state: dict[str, Any], ttl_seconds: int | None = None) -> None:
        key = KeyPattern.conv_state(conversation_id)
        await self.client.set(key, json.dumps(state), ex=ttl_seconds)

    async def load_conv_state(self, conversation_id: str) -> dict[str, Any] | None:
        raw = await self.client.get(KeyPattern.conv_state(conversation_id))
        return json.loads(raw) if raw else None

    async def set_routing_cache(self, fingerprint: str, decision: dict[str, Any], ttl_seconds: int = 300) -> None:
        await self.client.set(
            KeyPattern.routing_cache(fingerprint),
            json.dumps(decision),
            ex=ttl_seconds,
        )

    async def get_routing_cache(self, fingerprint: str) -> dict[str, Any] | None:
        raw = await self.client.get(KeyPattern.routing_cache(fingerprint))
        return json.loads(raw) if raw else None

    async def set_feature_flag(self, name: str, enabled: bool) -> None:
        await self.client.set(KeyPattern.feature_flag(name), "1" if enabled else "0")

    async def is_feature_enabled(self, name: str, default: bool = False) -> bool:
        value = await self.client.get(KeyPattern.feature_flag(name))
        if value is None:
            return default
        return value == "1"

    async def push_task_input(self, task_id: str, payload: dict[str, Any]) -> None:
        await self.client.set(KeyPattern.task_input(task_id), json.dumps(payload))
        await self.client.set(KeyPattern.task_status(task_id), "pending")

    _CLAIM_LUA = "if redis.call('GET', KEYS[1]) == 'pending' then " "redis.call('SET', KEYS[1], 'running') return 1 " "else return 0 end"

    async def claim_task(self, task_id: str) -> dict[str, Any] | None:
        ok = await self.client.eval(self._CLAIM_LUA, 1, KeyPattern.task_status(task_id))
        if not ok:
            return None
        raw = await self.client.get(KeyPattern.task_input(task_id))
        return json.loads(raw) if raw else None

    async def complete_task(self, task_id: str, output: dict[str, Any]) -> None:
        await self.client.set(KeyPattern.task_output(task_id), json.dumps(output))
        await self.client.set(KeyPattern.task_status(task_id), "done")

    async def request_approval(self, task_id: str, request: dict[str, Any]) -> None:
        await self.client.set(KeyPattern.task_approval(task_id), json.dumps(request))

    async def read_approval(self, task_id: str) -> dict[str, Any] | None:
        raw = await self.client.get(KeyPattern.task_approval(task_id))
        return json.loads(raw) if raw else None
