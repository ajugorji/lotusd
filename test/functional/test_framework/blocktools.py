#!/usr/bin/env python3
# Copyright (c) 2015-2019 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Utilities for manipulating blocks and transactions."""

import struct
import time
import unittest

from binascii import a2b_hex
from decimal import Decimal

from .messages import (
    COIN,
    CBlock,
    COutPoint,
    CTransaction,
    CTxIn,
    CTxOut,
    FromHex,
    ToHex,
    hash256,
)
from .script import (
    OP_1,
    OP_CHECKSIG,
    OP_DUP,
    OP_EQUALVERIFY,
    OP_HASH160,
    OP_RETURN,
    OP_TRUE,
    CScript,
    CScriptNum,
    CScriptOp,
)
from .txtools import pad_tx
from .util import assert_equal, satoshi_round

# Genesis block time (regtest)
TIME_GENESIS_BLOCK = 1600000000
SUBSIDY = Decimal('260')


def create_block(hashprev=None, coinbase=None, height=None, ntime=None,
                 *, version=None, tmpl=None):
    """Create a block (with regtest difficulty)."""
    block = CBlock()
    if tmpl is None:
        tmpl = {}
    block.nHeaderVersion = version or tmpl.get('version', 1)
    block.nTime = ntime or tmpl.get('curtime', int(time.time() + 600))
    block.nHeight = height or tmpl.get("height")
    assert block.nHeight is not None
    block.hashPrevBlock = hashprev or int(tmpl['previousblockhash'], 0x10)
    if tmpl.get('bits') is not None:
        block.nBits = struct.unpack('>I', a2b_hex(tmpl['bits']))[0]
    else:
        # difficulty retargeting is disabled in REGTEST chainparams
        block.nBits = 0x207fffff
    block.vtx.append(coinbase or create_coinbase(height=tmpl['height']))
    block.hashMerkleRoot = block.calc_merkle_root()
    block.calc_sha256()
    return block


def make_conform_to_ctor(block):
    for tx in block.vtx:
        tx.rehash()
    block.vtx = [block.vtx[0]] + \
        sorted(block.vtx[1:], key=lambda tx: tx.get_id())


def prepare_block(block):
    make_conform_to_ctor(block)
    block.hashMerkleRoot = block.calc_merkle_root()
    block.update_size()
    block.rehash_extended_metadata()
    block.solve()


def script_coinbase_height(height):
    if height <= 16:
        num = CScriptOp.encode_op_n(height)
    else:
        num = CScriptNum(height)
    return CScript([OP_RETURN, b'logos', num])


def create_coinbase(height, pubkey=None):
    """Create a coinbase transaction, assuming no miner fees.

    If pubkey is passed in, the coinbase output will be a P2PK output;
    otherwise an anyone-can-spend output."""
    coinbase = CTransaction()
    coinbase.vin.append(CTxIn(COutPoint(0, 0xffffffff),
                              bytes(80),
                              0xffffffff))
    op_return_output = CTxOut()
    op_return_output.scriptPubKey = script_coinbase_height(height)
    coinbaseoutput = CTxOut()
    coinbaseoutput.nValue = int(SUBSIDY * COIN)
    if (pubkey is not None):
        coinbaseoutput.scriptPubKey = CScript([pubkey, OP_CHECKSIG])
    else:
        coinbaseoutput.scriptPubKey = CScript([OP_TRUE])
    coinbase.vout = [op_return_output, coinbaseoutput]

    coinbase.rehash()
    return coinbase


def create_tx_with_script(prevtx, n, script_sig=b"", *,
                          amount, script_pub_key=CScript()):
    """Return one-input, one-output transaction object
       spending the prevtx's n-th output with the given amount.

       Can optionally pass scriptPubKey and scriptSig, default is anyone-can-spend output.
    """
    tx = CTransaction()
    assert(n < len(prevtx.vout))
    tx.vin.append(CTxIn(COutPoint(prevtx.txid, n), script_sig, 0xffffffff))
    tx.vout.append(CTxOut(amount, script_pub_key))
    pad_tx(tx)
    tx.rehash()
    return tx


def create_transaction(node, txid, to_address, *, amount, vout=0):
    """ Return signed transaction spending the first output of the
        input txid. Note that the node must be able to sign for the
        output that is being spent, and the node must not be running
        multiple wallets.
    """
    raw_tx = create_raw_transaction(node, txid, to_address, amount=amount, vout=vout)
    tx = FromHex(CTransaction(), raw_tx)
    return tx


def create_raw_transaction(node, txid, to_address, *, amount, vout=0):
    """ Return raw signed transaction spending the first output of the
        input txid. Note that the node must be able to sign for the
        output that is being spent, and the node must not be running
        multiple wallets.
    """
    rawtx = node.createrawtransaction(
        inputs=[{"txid": txid, "vout": vout}], outputs={to_address: amount})
    signresult = node.signrawtransactionwithwallet(rawtx)
    assert_equal(signresult["complete"], True)
    return signresult['hex']


def get_legacy_sigopcount_block(block, accurate=True):
    count = 0
    for tx in block.vtx:
        count += get_legacy_sigopcount_tx(tx, accurate)
    return count


def get_legacy_sigopcount_tx(tx, accurate=True):
    count = 0
    for i in tx.vout:
        count += i.scriptPubKey.GetSigOpCount(accurate)
    for j in tx.vin:
        # scriptSig might be of type bytes, so convert to CScript for the
        # moment
        count += CScript(j.scriptSig).GetSigOpCount(accurate)
    return count


def create_confirmed_utxos(node, count, age=101):
    """
    Helper to create at least "count" utxos
    """
    to_generate = int(0.5 * count) + age
    while to_generate > 0:
        node.generate(min(25, to_generate))
        to_generate -= 25
    utxos = node.listunspent()
    iterations = count - len(utxos)
    addr1 = node.getnewaddress()
    addr2 = node.getnewaddress()
    if iterations <= 0:
        return utxos
    for i in range(iterations):
        t = utxos.pop()
        inputs = []
        inputs.append({"txid": t["txid"], "vout": t["vout"]})
        outputs = {}
        outputs[addr1] = satoshi_round(t['amount'] / 2)
        outputs[addr2] = satoshi_round(t['amount'] / 2)
        raw_tx = node.createrawtransaction(inputs, outputs)
        ctx = FromHex(CTransaction(), raw_tx)
        fee = node.calculate_fee(ctx) // 2
        ctx.vout[0].nValue -= fee
        # Due to possible truncation, we go ahead and take another satoshi in
        # fees to ensure the transaction gets through
        ctx.vout[1].nValue -= fee + 1
        signed_tx = node.signrawtransactionwithwallet(ToHex(ctx))["hex"]
        node.sendrawtransaction(signed_tx)

    while (node.getmempoolinfo()['size'] > 0):
        node.generate(1)

    utxos = node.listunspent()
    assert len(utxos) >= count
    return utxos


def mine_big_block(node, utxos=None):
    # generate a 66k transaction,
    # and 14 of them is close to the 1MB block limit
    num = 14
    utxos = utxos if utxos is not None else []
    if len(utxos) < num:
        utxos.clear()
        utxos.extend(node.listunspent())
    send_big_transactions(node, utxos, num, 100)
    node.generate(1)


def send_big_transactions(node, utxos, num, fee_multiplier):
    from .cashaddr import decode
    txids = []
    padding = "1" * 512
    addrHash = decode(node.getnewaddress())[2]

    for _ in range(num):
        ctx = CTransaction()
        utxo = utxos.pop()
        txid = int(utxo['txid'], 16)
        ctx.vin.append(CTxIn(COutPoint(txid, int(utxo["vout"])), b""))
        ctx.vout.append(
            CTxOut(int(satoshi_round(utxo['amount'] * COIN)),
                   CScript([OP_DUP, OP_HASH160, addrHash, OP_EQUALVERIFY, OP_CHECKSIG])))
        for i in range(0, 127):
            ctx.vout.append(CTxOut(0, CScript(
                [OP_RETURN, bytes(padding, 'utf-8')])))
        # Create a proper fee for the transaction to be mined
        ctx.vout[0].nValue -= int(fee_multiplier * node.calculate_fee(ctx))
        signresult = node.signrawtransactionwithwallet(
            ToHex(ctx), None, "NONE|FORKID")
        txid = node.sendrawtransaction(signresult["hex"], 0)
        txids.append(txid)
    return txids


class TestFrameworkBlockTools(unittest.TestCase):
    def test_create_coinbase(self):
        height = 20
        coinbase_tx = create_coinbase(height=height)
        assert_equal(
            CScriptNum.decode(coinbase_tx.vout[0].scriptPubKey[len(b'_\x05logos'):]),
            height)
