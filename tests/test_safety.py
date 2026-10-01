"""Tests for the blake2b-chain post-activation fund-safety check.

All bitcoind RPC calls are mocked; no live node is touched.
"""
from unittest.mock import MagicMock

import pytest

from atomic_swap_cli import config
from atomic_swap_cli.safety import FundSafetyError, assert_safe_to_fund, check_utxo_post_activation


def _mock_rpc(gettxout_result, blockcount):
    rpc = MagicMock()
    rpc.gettxout.return_value = gettxout_result
    rpc.getblockcount.return_value = blockcount
    return rpc


def test_sha256_chain_always_safe_regardless_of_height():
    rpc = _mock_rpc({"confirmations": 1}, blockcount=1)
    result = check_utxo_post_activation(rpc, config.CHAIN_SHA256_MAINNET, "deadbeef", 0)
    assert result.is_safe
    assert result.activation_height is None
    rpc.gettxout.assert_not_called()


def test_regtest_blake2b_always_safe_no_real_split():
    rpc = _mock_rpc({"confirmations": 1}, blockcount=1)
    result = check_utxo_post_activation(rpc, config.CHAIN_BLAKE2B_REGTEST, "deadbeef", 0)
    assert result.is_safe
    rpc.gettxout.assert_not_called()


def test_blake2b_mainnet_pre_activation_coin_is_unsafe():
    # confirmations=5, tip=961_642 -> height = 961_642 - 5 + 1 = 961_638 (pre-activation)
    rpc = _mock_rpc({"confirmations": 5}, blockcount=961_642)
    result = check_utxo_post_activation(rpc, config.CHAIN_BLAKE2B_MAINNET, "deadbeef", 0)
    assert not result.is_safe
    assert result.height == 961_638
    assert result.activation_height == 961_640


def test_blake2b_mainnet_post_activation_coin_is_safe():
    # confirmations=1, tip=961_641 -> height = 961_641 (post-activation)
    rpc = _mock_rpc({"confirmations": 1}, blockcount=961_641)
    result = check_utxo_post_activation(rpc, config.CHAIN_BLAKE2B_MAINNET, "deadbeef", 0)
    assert result.is_safe
    assert result.height == 961_641


def test_blake2b_testnet4_uses_testnet4_activation_height():
    rpc = _mock_rpc({"confirmations": 1}, blockcount=150_307)
    result = check_utxo_post_activation(rpc, config.CHAIN_BLAKE2B_TESTNET4, "deadbeef", 0)
    assert not result.is_safe
    assert result.activation_height == 150_308


def test_unconfirmed_utxo_is_unsafe():
    rpc = _mock_rpc({"confirmations": 0}, blockcount=961_700)
    result = check_utxo_post_activation(rpc, config.CHAIN_BLAKE2B_MAINNET, "deadbeef", 0)
    assert not result.is_safe
    assert result.height is None
    assert "unconfirmed" in result.reason


def test_missing_utxo_raises():
    rpc = _mock_rpc(None, blockcount=961_700)
    with pytest.raises(FundSafetyError):
        check_utxo_post_activation(rpc, config.CHAIN_BLAKE2B_MAINNET, "deadbeef", 0)


def test_assert_safe_to_fund_raises_for_unsafe_utxo():
    rpc = _mock_rpc({"confirmations": 5}, blockcount=961_642)
    with pytest.raises(FundSafetyError):
        assert_safe_to_fund(rpc, config.CHAIN_BLAKE2B_MAINNET, "deadbeef", 0)


def test_assert_safe_to_fund_passes_for_safe_utxo():
    rpc = _mock_rpc({"confirmations": 1}, blockcount=961_641)
    result = assert_safe_to_fund(rpc, config.CHAIN_BLAKE2B_MAINNET, "deadbeef", 0)
    assert result.is_safe
