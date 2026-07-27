"""The chat ownership guard (_owned_conversation_id): the only thing stopping an
authenticated caller from resuming, and so reading back through replayed context,
another owner's conversation. Pure branching over a fake store, so no Postgres."""

from types import SimpleNamespace

from backend.service import _owned_conversation_id

CID = "11111111-1111-1111-1111-111111111111"


class FakeConversations:
    def __init__(self, conversation=None, raises=False):
        self.conversation = conversation
        self.raises = raises
        self.looked_up = []

    async def get_conversation(self, cid):
        if self.raises:
            raise RuntimeError("db down")
        self.looked_up.append(cid)
        return self.conversation


def fake_db(**kwargs):
    return SimpleNamespace(conversations=FakeConversations(**kwargs))


def conversation(owner_id):
    return SimpleNamespace(owner_id=owner_id)


async def test_another_owners_conversation_starts_a_fresh_one():
    # The whole point: the caller's id is dropped, so the turn opens a new conversation
    # instead of appending to (and replaying) someone else's.
    db = fake_db(conversation=conversation("victim@example.com"))
    assert await _owned_conversation_id(db, CID, "attacker@example.com") is None


async def test_own_conversation_passes_through():
    db = fake_db(conversation=conversation("me@example.com"))
    assert await _owned_conversation_id(db, CID, "me@example.com") == CID


async def test_unknown_conversation_id_passes_through():
    # No row yet: this is a brand-new conversation the client chose an id for.
    db = fake_db(conversation=None)
    assert await _owned_conversation_id(db, CID, "me@example.com") == CID


async def test_storage_error_fails_open():
    # Deliberate: a lookup failure must not take down chat. Pinned so the choice is
    # visible if anyone flips it.
    db = fake_db(raises=True)
    assert await _owned_conversation_id(db, CID, "me@example.com") == CID


async def test_malformed_conversation_id_fails_open():
    db = fake_db(conversation=conversation("someone@example.com"))
    assert await _owned_conversation_id(db, "not-a-uuid", "me@example.com") == "not-a-uuid"


async def test_anonymous_caller_skips_the_check():
    # owner_id None means no identity resolver is configured, so there is no owner to
    # compare against; the store is never even queried.
    db = fake_db(conversation=conversation("someone@example.com"))
    assert await _owned_conversation_id(db, CID, None) == CID
    assert db.conversations.looked_up == []


async def test_no_db_and_no_conversation_id_pass_through():
    assert await _owned_conversation_id(None, CID, "me@example.com") == CID
    assert await _owned_conversation_id(fake_db(), None, "me@example.com") is None
