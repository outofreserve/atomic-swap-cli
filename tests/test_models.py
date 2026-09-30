"""Unit tests for the swap state machine (atomic_swap_cli.models)."""
import pytest

from atomic_swap_cli.models import InvalidTransition, Swap, SwapRole, SwapState


def make_swap(**overrides) -> Swap:
    defaults = dict(
        swap_id="abc123",
        role=SwapRole.INITIATOR,
        state=SwapState.INITIATED,
        payment_hash="ab" * 32,
        preimage="cd" * 32,
        initiator_chain="sha256",
        responder_chain="blake2b",
        initiator_node="alice-sha256",
        responder_node="bob-blake2b",
        amount_msat_initiator=100_000,
        amount_msat_responder=100_000,
    )
    defaults.update(overrides)
    return Swap(**defaults)


def test_happy_path_transitions():
    swap = make_swap()
    assert swap.state == SwapState.INITIATED
    swap.transition(SwapState.COUNTERPARTY_LOCKED)
    assert swap.state == SwapState.COUNTERPARTY_LOCKED
    swap.transition(SwapState.BOTH_LOCKED)
    assert swap.state == SwapState.BOTH_LOCKED
    swap.transition(SwapState.SETTLED)
    assert swap.state == SwapState.SETTLED
    assert swap.is_terminal()
    assert len(swap.history) == 3


def test_illegal_skip_transition_rejected():
    swap = make_swap()
    with pytest.raises(InvalidTransition):
        swap.transition(SwapState.BOTH_LOCKED)  # can't skip COUNTERPARTY_LOCKED


def test_illegal_backwards_transition_rejected():
    swap = make_swap(state=SwapState.BOTH_LOCKED)
    with pytest.raises(InvalidTransition):
        swap.transition(SwapState.INITIATED)


def test_refund_allowed_from_any_nonterminal_state():
    for state in (SwapState.INITIATED, SwapState.COUNTERPARTY_LOCKED, SwapState.BOTH_LOCKED):
        swap = make_swap(state=state)
        swap.transition(SwapState.REFUNDED, note="timeout")
        assert swap.state == SwapState.REFUNDED
        assert swap.is_terminal()


def test_fail_allowed_from_any_nonterminal_state():
    swap = make_swap(state=SwapState.COUNTERPARTY_LOCKED)
    swap.transition(SwapState.FAILED, note="boom")
    assert swap.state == SwapState.FAILED


def test_terminal_state_cannot_transition_again():
    swap = make_swap(state=SwapState.SETTLED)
    with pytest.raises(InvalidTransition):
        swap.transition(SwapState.REFUNDED)


def test_serialization_round_trip():
    swap = make_swap()
    swap.transition(SwapState.COUNTERPARTY_LOCKED, note="mirror created")
    d = swap.to_dict()
    restored = Swap.from_dict(d)
    assert restored.state == SwapState.COUNTERPARTY_LOCKED
    assert restored.role == SwapRole.INITIATOR
    assert restored.history == swap.history
    assert restored.payment_hash == swap.payment_hash
