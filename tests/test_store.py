"""Unit tests for the sqlite-backed SwapStore (atomic_swap_cli.store)."""
from atomic_swap_cli.models import Swap, SwapRole, SwapState
from atomic_swap_cli.store import SwapStore


def make_swap(swap_id="s1", payment_hash="aa" * 32) -> Swap:
    return Swap(
        swap_id=swap_id,
        role=SwapRole.INITIATOR,
        state=SwapState.INITIATED,
        payment_hash=payment_hash,
        preimage="bb" * 32,
        initiator_chain="sha256",
        responder_chain="blake2b",
        initiator_node="alice-sha256",
        responder_node="bob-blake2b",
        amount_msat_initiator=1000,
        amount_msat_responder=1000,
    )


def test_save_and_get_round_trip():
    store = SwapStore(":memory:")
    swap = make_swap()
    store.save(swap)
    fetched = store.get(swap.swap_id)
    assert fetched is not None
    assert fetched.swap_id == swap.swap_id
    assert fetched.state == SwapState.INITIATED
    assert fetched.payment_hash == swap.payment_hash


def test_get_missing_returns_none():
    store = SwapStore(":memory:")
    assert store.get("does-not-exist") is None


def test_get_by_payment_hash():
    store = SwapStore(":memory:")
    swap = make_swap(payment_hash="cc" * 32)
    store.save(swap)
    fetched = store.get_by_payment_hash("cc" * 32)
    assert fetched is not None
    assert fetched.swap_id == swap.swap_id


def test_update_persists_state_change():
    store = SwapStore(":memory:")
    swap = make_swap()
    store.save(swap)
    swap.transition(SwapState.COUNTERPARTY_LOCKED)
    store.save(swap)
    fetched = store.get(swap.swap_id)
    assert fetched.state == SwapState.COUNTERPARTY_LOCKED
    assert len(fetched.history) == 1


def test_list_all_and_non_terminal():
    store = SwapStore(":memory:")
    s1 = make_swap("s1", "aa" * 32)
    s2 = make_swap("s2", "bb" * 32)
    s2.transition(SwapState.COUNTERPARTY_LOCKED)
    s2.transition(SwapState.BOTH_LOCKED)
    s2.transition(SwapState.SETTLED)
    store.save(s1)
    store.save(s2)

    all_swaps = store.list_all()
    assert {s.swap_id for s in all_swaps} == {"s1", "s2"}

    non_terminal = store.list_non_terminal()
    assert {s.swap_id for s in non_terminal} == {"s1"}


def test_delete():
    store = SwapStore(":memory:")
    swap = make_swap()
    store.save(swap)
    store.delete(swap.swap_id)
    assert store.get(swap.swap_id) is None
