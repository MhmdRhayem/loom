"""Per-turn token accounting. Pure bookkeeping, so no model and no database.

The reason this exists at all is that a single total_tokens number cannot be priced:
input and output bill several times apart and cached input is discounted, so the split
is the only thing that converts a turn into money.
"""

import asyncio
from types import SimpleNamespace

from backend.core import usage


def message(input_tokens=100, output_tokens=20, cache_read=0):
    return SimpleNamespace(
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {"cache_read": cache_read},
        }
    )


def test_reads_the_split_and_the_cached_subset():
    got = usage.from_message(message(100, 20, cache_read=80))
    assert (got.input, got.output, got.cached_input, got.calls) == (100, 20, 80, 1)
    assert got.total == 120


def test_cached_is_a_subset_of_input_not_an_addition():
    # The discount applies to these tokens; the rest bill at full rate. Counting them
    # separately from input would double the invoice.
    got = usage.from_message(message(100, 20, cache_read=80))
    assert got.cached_input <= got.input
    assert got.total == 120


def test_a_message_without_usage_metadata_is_zero_not_an_error():
    assert usage.from_message(SimpleNamespace()).total == 0
    assert usage.from_message(None).calls == 0


def test_record_outside_a_turn_is_a_no_op():
    usage._current.set(None)
    usage.record(message())  # must not raise


def test_a_turn_accumulates_every_call():
    turn = usage.start_turn()
    usage.record(message(100, 20))
    usage.record(message(50, 10))
    assert (turn.input, turn.output, turn.calls) == (150, 30, 2)
    assert turn.total == 180


def test_record_all_returns_the_subtotal_and_folds_into_the_turn():
    turn = usage.start_turn()
    subtotal = usage.record_all([message(100, 20), SimpleNamespace(), message(30, 5)])
    assert (subtotal.input, subtotal.output, subtotal.calls) == (130, 25, 2)
    assert (turn.input, turn.output) == (130, 25)


def test_parallel_tasks_accumulate_into_one_turn():
    # asyncio hands each task a copy of the context, so this only works because the
    # accumulator is a mutable object stored before the tasks are spawned. If it were
    # replaced per task, the router, the agents and the critic would each bill
    # separately and the turn total would silently be whichever finished last.
    async def main():
        turn = usage.start_turn()

        async def one():
            usage.record(message(10, 1))

        await asyncio.gather(*(one() for _ in range(5)))
        return turn

    turn = asyncio.run(main())
    assert (turn.input, turn.output, turn.calls) == (50, 5, 5)


def test_as_dict_names_the_columns_that_get_persisted():
    turn = usage.start_turn()
    usage.record(message(100, 20, cache_read=40))
    assert turn.as_dict() == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cached_input_tokens": 40,
        "total_tokens": 120,
        "model_calls": 1,
    }
