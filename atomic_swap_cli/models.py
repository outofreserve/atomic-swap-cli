"""Swap state machine.

A submarine/atomic swap between Alice and Bob across the two simulated
chains goes through the following states. The state machine enforces
legal transitions only; it does not itself talk to CLN/bitcoind (see
``swap_service.py`` for the orchestration logic that drives transitions
based on real RPC observations).

    INITIATED --------------------------------------------------+
        |                                                        |
        v                                                        |
    COUNTERPARTY_LOCKED (Bob's mirrored hold-invoice created)     |
        |                                                        |
        v                                                        |
    BOTH_LOCKED (both hold invoices show HTLCs ACCEPTED)          |
        |                                                        |
        v                                                        |
    SETTLED (preimage released, both invoices PAID)               |
                                                                  |
    Any state prior to SETTLED can transition to:                |
        REFUNDED  (timelock expired / invoice cancelled)  <-------+
        FAILED    (unexpected error, cancelled invoices, etc.)
"""
from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


class SwapRole(str, enum.Enum):
    INITIATOR = "initiator"  # picks the secret, creates the first hold invoice
    RESPONDER = "responder"  # mirrors the hold invoice on the other chain


class SwapState(str, enum.Enum):
    INITIATED = "initiated"
    COUNTERPARTY_LOCKED = "counterparty_locked"
    BOTH_LOCKED = "both_locked"
    SETTLED = "settled"
    REFUNDED = "refunded"
    EXPIRED = "expired"
    FAILED = "failed"


# Allowed forward transitions. REFUNDED/EXPIRED/FAILED are reachable from
# any non-terminal state (timeout/error handling), which is checked
# separately in `Swap.transition`.
_TERMINAL_STATES = {SwapState.SETTLED, SwapState.REFUNDED, SwapState.EXPIRED, SwapState.FAILED}

_FORWARD_TRANSITIONS: dict[SwapState, set[SwapState]] = {
    SwapState.INITIATED: {SwapState.COUNTERPARTY_LOCKED},
    SwapState.COUNTERPARTY_LOCKED: {SwapState.BOTH_LOCKED},
    SwapState.BOTH_LOCKED: {SwapState.SETTLED},
}


class InvalidTransition(Exception):
    pass


@dataclass
class Swap:
    """Persisted record of one swap attempt.

    Amounts are in millisatoshis. `payment_hash`/`preimage` are hex strings.
    """

    swap_id: str
    role: SwapRole
    state: SwapState

    payment_hash: str  # sha256(preimage), hex
    preimage: Optional[str]  # only known to the INITIATOR until released

    # Chain/node labels this swap runs across, e.g. "sha256" -> initiator's
    # invoice chain, "blake2b" -> counterparty's invoice chain.
    initiator_chain: str
    responder_chain: str
    initiator_node: str  # lightning node name, e.g. "alice-sha256"
    responder_node: str  # e.g. "bob-blake2b"

    amount_msat_initiator: int
    amount_msat_responder: int

    initiator_invoice: Optional[str] = None  # bolt11 created by initiator's node
    responder_invoice: Optional[str] = None  # bolt11 created by responder's node

    cltv_expiry_initiator: Optional[int] = None
    cltv_expiry_responder: Optional[int] = None

    # Absolute block heights (on each respective chain) at/after which the
    # corresponding hold invoice's CLTV delta has elapsed and it's safe to
    # auto-refund rather than risk the counterparty settling very late and
    # griefing an on-chain claim. Populated by swap_service when the
    # current chain height is available at invoice-creation time (see
    # `cli.py check-timeout`); None if unknown (e.g. regtest without a
    # supplied height, or a swap persisted before this field existed).
    expiry_height_initiator: Optional[int] = None
    expiry_height_responder: Optional[int] = None

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    history: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]

    def transition(self, new_state: SwapState, note: str = "") -> None:
        """Move to `new_state`, raising InvalidTransition if illegal."""
        if self.state in _TERMINAL_STATES:
            raise InvalidTransition(f"swap {self.swap_id} is already terminal ({self.state.value})")

        is_abort = new_state in {SwapState.REFUNDED, SwapState.EXPIRED, SwapState.FAILED}
        allowed = _FORWARD_TRANSITIONS.get(self.state, set())
        if new_state not in allowed and not is_abort:
            raise InvalidTransition(
                f"illegal transition for swap {self.swap_id}: {self.state.value} -> {new_state.value}"
            )

        self.history.append(
            {"from": self.state.value, "to": new_state.value, "at": time.time(), "note": note}
        )
        self.state = new_state
        self.updated_at = time.time()

    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["role"] = self.role.value
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Swap":
        d = dict(d)
        d["role"] = SwapRole(d["role"])
        d["state"] = SwapState(d["state"])
        return cls(**d)
