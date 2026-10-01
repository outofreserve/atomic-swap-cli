"""Tests for the CLI commands, with all CLN/hold RPC clients monkeypatched
out so nothing here touches a live bitcoind/lightningd/hold process.
"""
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from atomic_swap_cli import cli as cli_module
from atomic_swap_cli.store import SwapStore


@dataclass
class FakeInvoiceHandle:
    bolt11: str
    payment_hash: str


@dataclass
class FakeStatus:
    payment_hash: str
    state: str
    bolt11: str
    preimage: Optional[str] = None


class FakeHoldClient:
    def __init__(self, name: str):
        self.name = name
        self.invoices: dict[str, FakeStatus] = {}

    def create_invoice(self, payment_hash, amount_msat, **kwargs):
        bolt11 = f"lnbcrt{amount_msat}p1{self.name}{payment_hash[:8]}"
        self.invoices[payment_hash] = FakeStatus(payment_hash, "UNPAID", bolt11)
        return FakeInvoiceHandle(bolt11, payment_hash)

    def get_status(self, payment_hash):
        return self.invoices[payment_hash]

    def settle(self, preimage):
        import hashlib

        payment_hash = hashlib.sha256(bytes.fromhex(preimage)).hexdigest()
        self.invoices[payment_hash].state = "PAID"
        self.invoices[payment_hash].preimage = preimage

    def cancel(self, payment_hash):
        self.invoices[payment_hash].state = "CANCELLED"


class FakeClnClient:
    def __init__(self, name: str):
        self.name = name
        self.funds_outputs: list[dict] = []

    def pay(self, bolt11, amount_msat=None):
        return {"status": "complete", "payment_preimage": "00" * 32}

    def getinfo(self):
        return {"id": f"03{'ab' * 32}", "binding": [{"port": 9735}]}

    def connect(self, node_id, host, port):
        return {"id": node_id}

    def fundchannel(self, node_id, amount_sat):
        return {"txid": "deadbeef"}

    def decode(self, bolt11):
        return {"amount_msat": 1000, "payment_hash": "ab" * 32}

    def listfunds(self):
        return {"outputs": self.funds_outputs}


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "swaps.sqlite3"
    monkeypatch.setattr(cli_module.config, "DB_PATH", db_path)
    return db_path


@pytest.fixture
def fake_clients(monkeypatch):
    hold_clients: dict[str, FakeHoldClient] = {}
    cln_clients: dict[str, FakeClnClient] = {}

    def _hold_client_for(node_name):
        return hold_clients.setdefault(node_name, FakeHoldClient(node_name))

    def _cln_client_for(node_name):
        return cln_clients.setdefault(node_name, FakeClnClient(node_name))

    monkeypatch.setattr(cli_module, "_hold_client_for", _hold_client_for)
    monkeypatch.setattr(cli_module, "_cln_client_for", _cln_client_for)
    return {"hold": hold_clients, "cln": cln_clients}


def run(*args):
    runner = CliRunner()
    return runner.invoke(cli_module.cli, args)


def test_initiate_command(isolated_db, fake_clients):
    result = run(
        "initiate",
        "--initiator-node", "alice-sha256",
        "--responder-node", "bob-blake2b",
        "--initiator-chain", "sha256",
        "--responder-chain", "blake2b",
        "--amount-initiator-msat", "100000",
        "--amount-responder-msat", "100000",
    )
    assert result.exit_code == 0, result.output
    assert "state:            initiated" in result.output
    assert "payment_hash:" in result.output


def test_initiate_then_accept_then_list(isolated_db, fake_clients):
    initiate_result = run(
        "initiate",
        "--initiator-node", "alice-sha256",
        "--responder-node", "bob-blake2b",
        "--initiator-chain", "sha256",
        "--responder-chain", "blake2b",
        "--amount-initiator-msat", "100000",
        "--amount-responder-msat", "50000",
    )
    assert initiate_result.exit_code == 0, initiate_result.output
    payment_hash = [
        line.split()[-1] for line in initiate_result.output.splitlines() if line.startswith("payment_hash:")
    ][0]

    accept_result = run(
        "accept",
        "--payment-hash", payment_hash,
        "--responder-node", "bob-blake2b",
        "--initiator-node", "alice-sha256",
        "--initiator-chain", "sha256",
        "--responder-chain", "blake2b",
        "--amount-initiator-msat", "100000",
        "--amount-responder-msat", "50000",
    )
    assert accept_result.exit_code == 0, accept_result.output
    assert "state:            counterparty_locked" in accept_result.output

    list_result = run("list")
    assert list_result.exit_code == 0
    assert "initiated" in list_result.output
    assert "counterparty_locked" in list_result.output


def test_status_unknown_swap_errors(isolated_db, fake_clients):
    result = run("status", "--swap-id", "nonexistent")
    assert result.exit_code != 0
    assert "no such swap" in result.output


def test_settle_and_refund_flow(isolated_db, fake_clients):
    initiate_result = run(
        "initiate",
        "--initiator-node", "alice-sha256",
        "--responder-node", "bob-blake2b",
        "--initiator-chain", "sha256",
        "--responder-chain", "blake2b",
        "--amount-initiator-msat", "100000",
        "--amount-responder-msat", "100000",
    )
    swap_id = [
        line.split()[-1] for line in initiate_result.output.splitlines() if line.startswith("swap_id:")
    ][0]

    with SwapStore(isolated_db) as store:
        swap = store.get(swap_id)
        swap.transition(swap.state.__class__.COUNTERPARTY_LOCKED)
        swap.transition(swap.state.__class__.BOTH_LOCKED)
        store.save(swap)

    settle_result = run("settle", "--swap-id", swap_id)
    assert settle_result.exit_code == 0, settle_result.output
    assert "settled" in settle_result.output

    # Once settled, refund should fail (terminal state already reached).
    refund_result = run("refund", "--swap-id", swap_id)
    assert refund_result.exit_code != 0


def test_refund_before_settle(isolated_db, fake_clients):
    initiate_result = run(
        "initiate",
        "--initiator-node", "alice-sha256",
        "--responder-node", "bob-blake2b",
        "--initiator-chain", "sha256",
        "--responder-chain", "blake2b",
        "--amount-initiator-msat", "100000",
        "--amount-responder-msat", "100000",
    )
    swap_id = [
        line.split()[-1] for line in initiate_result.output.splitlines() if line.startswith("swap_id:")
    ][0]
    refund_result = run("refund", "--swap-id", swap_id, "--reason", "counterparty never accepted")
    assert refund_result.exit_code == 0, refund_result.output
    assert "refunded" in refund_result.output


def test_pay_command(isolated_db, fake_clients):
    result = run("pay", "--from-node", "bob-blake2b", "--bolt11", "lnbcrt1234")
    assert result.exit_code == 0
    assert "payment status: complete" in result.output
    assert "preimage revealed:" in result.output


def test_open_channel_command(isolated_db, fake_clients):
    result = run("open-channel", "--from-node", "alice-sha256", "--to-node", "bob-sha256", "--amount-sat", "500000")
    assert result.exit_code == 0, result.output
    assert "channel funded" in result.output


def test_mainnet_command_refused_without_confirmation_flag(isolated_db, fake_clients):
    result = run("pay", "--from-node", "bob-sha256-mainnet", "--bolt11", "lnbcrt1234")
    assert result.exit_code != 0
    assert "REFUSING" in result.output
    assert "MAINNET" in result.output


def test_mainnet_command_proceeds_with_confirmation_flag(isolated_db, fake_clients):
    result = run("pay", "--from-node", "bob-sha256-mainnet", "--bolt11", "lnbcrt1234", "--yes-mainnet")
    assert result.exit_code == 0, result.output
    assert "MAINNET (REAL FUNDS)" in result.output
    assert "payment status: complete" in result.output


def test_pay_dry_run_does_not_pay(isolated_db, fake_clients):
    result = run("pay", "--from-node", "alice-sha256", "--bolt11", "lnbcrt1234", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "[dry-run]" in result.output
    assert "payment status" not in result.output


def test_open_channel_dry_run_does_not_fund(isolated_db, fake_clients, monkeypatch):
    rpc = MagicMock()
    rpc.estimatesmartfee.return_value = {"feerate": 0.0001}
    monkeypatch.setattr(cli_module, "_bitcoin_rpc_for", lambda name: rpc)
    result = run(
        "open-channel",
        "--from-node", "alice-sha256-testnet4",
        "--to-node", "bob-sha256-testnet4",
        "--amount-sat", "500000",
        "--dry-run",
    )
    assert result.exit_code == 0, result.output
    assert "[dry-run]" in result.output
    assert "channel funded" not in result.output


def test_open_channel_blocks_blake2b_pre_activation_funding(isolated_db, fake_clients, monkeypatch):
    rpc = MagicMock()
    rpc.estimatesmartfee.return_value = {"feerate": 0.0001}
    rpc.gettxout.return_value = {"confirmations": 5}
    rpc.getblockcount.return_value = 961_642  # height 961_638, pre-activation
    monkeypatch.setattr(cli_module, "_bitcoin_rpc_for", lambda name: rpc)

    from_client = fake_clients["cln"].setdefault("alice-blake2b-mainnet", FakeClnClient("alice-blake2b-mainnet"))
    from_client.funds_outputs = [{"txid": "ab" * 32, "output": 0, "status": "confirmed", "reserved": False}]

    result = run(
        "open-channel",
        "--from-node", "alice-blake2b-mainnet",
        "--to-node", "bob-blake2b-mainnet",
        "--amount-sat", "500000",
        "--yes-mainnet",
    )
    assert result.exit_code != 0
    assert "pre-activation" in result.output
    assert "channel funded" not in result.output


def test_open_channel_allows_blake2b_post_activation_funding(isolated_db, fake_clients, monkeypatch):
    rpc = MagicMock()
    rpc.estimatesmartfee.return_value = {"feerate": 0.0001}
    rpc.gettxout.return_value = {"confirmations": 1}
    rpc.getblockcount.return_value = 961_641  # post-activation
    monkeypatch.setattr(cli_module, "_bitcoin_rpc_for", lambda name: rpc)

    from_client = fake_clients["cln"].setdefault("alice-blake2b-mainnet", FakeClnClient("alice-blake2b-mainnet"))
    from_client.funds_outputs = [{"txid": "ab" * 32, "output": 0, "status": "confirmed", "reserved": False}]

    result = run(
        "open-channel",
        "--from-node", "alice-blake2b-mainnet",
        "--to-node", "bob-blake2b-mainnet",
        "--amount-sat", "500000",
        "--yes-mainnet",
    )
    assert result.exit_code == 0, result.output
    assert "channel funded" in result.output


def test_open_channel_accept_fund_safety_risk_overrides_block(isolated_db, fake_clients, monkeypatch):
    rpc = MagicMock()
    rpc.estimatesmartfee.return_value = {"feerate": 0.0001}
    rpc.gettxout.return_value = {"confirmations": 5}
    rpc.getblockcount.return_value = 961_642  # pre-activation
    monkeypatch.setattr(cli_module, "_bitcoin_rpc_for", lambda name: rpc)

    from_client = fake_clients["cln"].setdefault("alice-blake2b-mainnet", FakeClnClient("alice-blake2b-mainnet"))
    from_client.funds_outputs = [{"txid": "ab" * 32, "output": 0, "status": "confirmed", "reserved": False}]

    result = run(
        "open-channel",
        "--from-node", "alice-blake2b-mainnet",
        "--to-node", "bob-blake2b-mainnet",
        "--amount-sat", "500000",
        "--yes-mainnet",
        "--accept-fund-safety-risk",
    )
    assert result.exit_code == 0, result.output
    assert "channel funded" in result.output

