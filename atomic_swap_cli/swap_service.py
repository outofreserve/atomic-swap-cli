"""Swap orchestration: ties together the state machine (`models.Swap`),
persistence (`store.SwapStore`), CLN RPC, and the hold-plugin gRPC client
to actually drive a cross-chain atomic swap.

Swap protocol (submarine swap over two simulated chains):

1. INITIATE (role=initiator, say Alice wants chain-sha256 funds -> chain-blake2b funds):
   - Alice's CLI picks a random 32-byte preimage `p`, computes
     `payment_hash = sha256(p)`.
   - Alice's node (via HoldClient against her own CLN+hold instance on
     chain A) creates a hold invoice for `payment_hash` for the amount
     she is *receiving* on chain A (i.e. Bob will pay her there).
   - State: INITIATED. The invoice + payment_hash are handed to Bob out
     of band (in this CLI, simply printed/copy-pasted or read from the
     shared sqlite demo dir).

2. ACCEPT (role=responder, Bob):
   - Bob's CLI takes the payment_hash from Alice's invoice and creates
     his *own* mirrored hold invoice, locked to the same payment_hash,
     on chain B, for the amount he is receiving there (i.e. Alice will
     pay him on chain B). Bob never learns the preimage at this point.
   - State: COUNTERPARTY_LOCKED.

3. PAY both directions:
   - Bob pays Alice's chain-A invoice (his CLN node sends the payment;
     Alice's hold plugin holds it as ACCEPTED, not yet settled).
   - Alice pays Bob's chain-B invoice (same: held as ACCEPTED).
   - Once both hold invoices report ACCEPTED HTLCs: State BOTH_LOCKED.

4. SETTLE:
   - Alice (who alone knows the preimage) calls Settle on her *own*
     chain-A hold invoice once she also observes Bob's chain-B invoice
     is ACCEPTED (i.e. her payment is in flight/accepted there too).
     Settling reveals the preimage on-chain/in the payment network.
   - Bob observes the revealed preimage (via Track/List on his own hold
     invoice on chain A... actually via the payment he made -- the
     preimage is returned to his `pay` call once Alice settles) and
     immediately calls Settle on his own chain-B hold invoice with the
     same preimage, claiming his payment.
   - State: SETTLED once both sides show PAID.

5. REFUND/EXPIRE:
   - If either leg's CLTV expiry passes without settlement, the
     corresponding hold invoice is cancelled (`HoldClient.cancel`) and
     the swap transitions to REFUNDED/EXPIRED. No preimage was ever
     revealed, so neither side can be griefed for partial completion.

This module intentionally keeps all gRPC/RPC calls behind small
injectable client objects so tests can supply mocks/fakes without any
live bitcoind/lightningd/hold process.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from .models import Swap, SwapRole, SwapState, InvalidTransition
from .store import SwapStore


class HoldClientProtocol(Protocol):
    """Structural interface satisfied by `hold_client.HoldClient` (and by
    test fakes) so swap_service never imports grpc directly."""

    def create_invoice(self, payment_hash: str, amount_msat: int, **kwargs) -> "object":
        ...

    def get_status(self, payment_hash: str) -> "object":
        ...

    def settle(self, preimage: str) -> None:
        ...

    def cancel(self, payment_hash: str) -> None:
        ...


class ClnClientProtocol(Protocol):
    def pay(self, bolt11: str, amount_msat: Optional[int] = None) -> dict:
        ...


def generate_preimage() -> str:
    """32 random bytes, hex-encoded."""
    return os.urandom(32).hex()


def sha256_hex(preimage_hex: str) -> str:
    return hashlib.sha256(bytes.fromhex(preimage_hex)).hexdigest()


DEFAULT_CLTV_INITIATOR = 144  # blocks; the initiator's invoice (settled first) gets a longer window
DEFAULT_CLTV_RESPONDER = 72  # responder's invoice must expire sooner so initiator can safely wait


class SwapService:
    def __init__(self, store: SwapStore):
        self.store = store

    # --- step 1: initiate --------------------------------------------------

    def initiate(
        self,
        *,
        initiator_chain: str,
        responder_chain: str,
        initiator_node: str,
        responder_node: str,
        amount_msat_initiator: int,
        amount_msat_responder: int,
        initiator_hold_client: HoldClientProtocol,
        current_height: Optional[int] = None,
    ) -> Swap:
        preimage = generate_preimage()
        payment_hash = sha256_hex(preimage)

        handle = initiator_hold_client.create_invoice(
            payment_hash,
            amount_msat_initiator,
            memo=f"atomic-swap {payment_hash[:8]}",
            min_final_cltv_expiry=DEFAULT_CLTV_INITIATOR,
        )

        swap = Swap(
            swap_id=Swap.new_id(),
            role=SwapRole.INITIATOR,
            state=SwapState.INITIATED,
            payment_hash=payment_hash,
            preimage=preimage,
            initiator_chain=initiator_chain,
            responder_chain=responder_chain,
            initiator_node=initiator_node,
            responder_node=responder_node,
            amount_msat_initiator=amount_msat_initiator,
            amount_msat_responder=amount_msat_responder,
            initiator_invoice=handle.bolt11,
            cltv_expiry_initiator=DEFAULT_CLTV_INITIATOR,
            expiry_height_initiator=(current_height + DEFAULT_CLTV_INITIATOR) if current_height is not None else None,
        )
        self.store.save(swap)
        return swap

    # --- step 2: accept ------------------------------------------------------

    def accept(
        self,
        *,
        payment_hash: str,
        initiator_chain: str,
        responder_chain: str,
        initiator_node: str,
        responder_node: str,
        amount_msat_initiator: int,
        amount_msat_responder: int,
        responder_hold_client: HoldClientProtocol,
        current_height: Optional[int] = None,
    ) -> Swap:
        """Called on the responder's side: creates the mirrored hold
        invoice for the *same* payment_hash on the responder's chain.
        The responder never needs/learns the preimage here.
        """
        handle = responder_hold_client.create_invoice(
            payment_hash,
            amount_msat_responder,
            memo=f"atomic-swap {payment_hash[:8]}",
            min_final_cltv_expiry=DEFAULT_CLTV_RESPONDER,
        )
        swap = Swap(
            swap_id=Swap.new_id(),
            role=SwapRole.RESPONDER,
            state=SwapState.INITIATED,
            payment_hash=payment_hash,
            preimage=None,
            initiator_chain=initiator_chain,
            responder_chain=responder_chain,
            initiator_node=initiator_node,
            responder_node=responder_node,
            amount_msat_initiator=amount_msat_initiator,
            amount_msat_responder=amount_msat_responder,
            responder_invoice=handle.bolt11,
            cltv_expiry_responder=DEFAULT_CLTV_RESPONDER,
            expiry_height_responder=(current_height + DEFAULT_CLTV_RESPONDER) if current_height is not None else None,
        )
        swap.transition(SwapState.COUNTERPARTY_LOCKED, note="mirrored hold invoice created")
        self.store.save(swap)
        return swap


    def mark_counterparty_locked(self, swap: Swap, note: str = "counterparty invoice observed") -> Swap:
        """Initiator-side transition: call once the initiator has learned
        that the responder created their mirrored hold invoice (e.g. by
        receiving it out of band), moving INITIATED -> COUNTERPARTY_LOCKED
        so `mark_both_locked_if_ready` can subsequently fire.
        """
        if swap.state == SwapState.INITIATED:
            swap.transition(SwapState.COUNTERPARTY_LOCKED, note=note)
            self.store.save(swap)
        return swap

    # --- step 3: pay both legs -----------------------------------------------

    def pay_counterparty_invoice(self, swap: Swap, bolt11: str, cln_client: ClnClientProtocol) -> dict:
        """Pay the other side's hold invoice. Because it's a hold
        invoice, this call will hang/return once the payment is
        *accepted*, not settled -- CLN's `pay` normally blocks until
        settlement, so in production you'd use `pay` with a timeout or
        `sendpay`/`waitsendpay` and treat "still in flight" as
        ACCEPTED. This wrapper is intentionally thin; see tests for how
        the mocked client simulates this.
        """
        return cln_client.pay(bolt11)

    def mark_both_locked_if_ready(
        self,
        swap: Swap,
        *,
        own_hold_client: HoldClientProtocol,
        own_payment_hash: str,
        counterparty_status_fn,
    ) -> Swap:
        """Check both hold-invoice statuses and advance to BOTH_LOCKED
        once both report ACCEPTED (i.e. HTLCs are locked in on both
        chains but neither preimage has been released yet).
        """
        own_status = own_hold_client.get_status(own_payment_hash)
        counterparty_status = counterparty_status_fn()
        if own_status.state == "ACCEPTED" and counterparty_status.state == "ACCEPTED":
            if swap.state == SwapState.COUNTERPARTY_LOCKED:
                swap.transition(SwapState.BOTH_LOCKED, note="both hold invoices ACCEPTED")
                self.store.save(swap)
        return swap

    # --- step 4: settle --------------------------------------------------------

    def settle(self, swap: Swap, hold_client: HoldClientProtocol) -> Swap:
        """Reveal the preimage by settling the caller's own hold
        invoice. Only the INITIATOR (who generated the preimage) can do
        this meaningfully as the *first* settle; the RESPONDER settles
        afterwards once they observe the revealed preimage (e.g. via
        their outgoing payment's return value).
        """
        if swap.preimage is None:
            raise ValueError("cannot settle: preimage unknown (are you the responder? use settle_with_preimage)")
        if swap.state != SwapState.BOTH_LOCKED:
            raise InvalidTransition(f"cannot settle swap in state {swap.state.value}, need BOTH_LOCKED")
        hold_client.settle(swap.preimage)
        swap.transition(SwapState.SETTLED, note="preimage released")
        self.store.save(swap)
        return swap

    def settle_with_preimage(self, swap: Swap, preimage: str, hold_client: HoldClientProtocol) -> Swap:
        """Responder-side settle: uses a preimage learned from the
        network (e.g. returned by a completed outgoing payment) rather
        than one generated locally.
        """
        if sha256_hex(preimage) != swap.payment_hash:
            raise ValueError("preimage does not match swap payment_hash")
        if swap.state not in (SwapState.BOTH_LOCKED,):
            raise InvalidTransition(f"cannot settle swap in state {swap.state.value}, need BOTH_LOCKED")
        hold_client.settle(preimage)
        swap.preimage = preimage
        swap.transition(SwapState.SETTLED, note="preimage observed and released")
        self.store.save(swap)
        return swap

    # --- step 5: refund / expire -----------------------------------------------

    def refund(self, swap: Swap, hold_client: HoldClientProtocol, reason: str = "timeout") -> Swap:
        if swap.is_terminal():
            raise InvalidTransition(f"swap {swap.swap_id} already terminal ({swap.state.value})")
        hold_client.cancel(swap.payment_hash)
        swap.transition(SwapState.REFUNDED, note=reason)
        self.store.save(swap)
        return swap

    def fail(self, swap: Swap, reason: str) -> Swap:
        swap.error = reason
        swap.transition(SwapState.FAILED, note=reason)
        self.store.save(swap)
        return swap

    def check_expiry(
        self,
        swap: Swap,
        *,
        initiator_height: Optional[int] = None,
        responder_height: Optional[int] = None,
    ) -> Optional[str]:
        """Return a human-readable reason if `swap` has passed either
        leg's CLTV expiry height and is not yet settled, else None.

        Safe to call on a settled/already-terminal swap or one with no
        recorded expiry heights (e.g. created before this field existed,
        or on regtest without a height supplied) -- returns None in
        those cases rather than raising, since "unknown" must never be
        treated as "expired".
        """
        if swap.is_terminal():
            return None
        if (
            swap.expiry_height_initiator is not None
            and initiator_height is not None
            and initiator_height >= swap.expiry_height_initiator
        ):
            return (
                f"initiator leg's CLTV expiry height {swap.expiry_height_initiator} reached "
                f"(current height {initiator_height}) without settlement"
            )
        if (
            swap.expiry_height_responder is not None
            and responder_height is not None
            and responder_height >= swap.expiry_height_responder
        ):
            return (
                f"responder leg's CLTV expiry height {swap.expiry_height_responder} reached "
                f"(current height {responder_height}) without settlement"
            )
        return None

    def auto_refund_if_expired(
        self,
        swap: Swap,
        hold_client: HoldClientProtocol,
        *,
        initiator_height: Optional[int] = None,
        responder_height: Optional[int] = None,
    ) -> tuple[Swap, Optional[str]]:
        """Check `check_expiry` and, if expired, refund (cancel the
        caller's own hold invoice + transition to REFUNDED).

        Returns (swap, reason) -- reason is None if nothing happened
        (swap not expired, or already terminal).
        """
        reason = self.check_expiry(swap, initiator_height=initiator_height, responder_height=responder_height)
        if reason is None:
            return swap, None
        swap = self.refund(swap, hold_client, reason=f"CLTV expiry: {reason}")
        return swap, reason
