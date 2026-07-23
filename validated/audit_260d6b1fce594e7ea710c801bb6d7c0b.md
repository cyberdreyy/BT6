### Title
Creator Can Silently Re-Delegate a Self-Revoked Contract Pusher via `allowContractPushers`, Redirecting Its Price Pushes Into the Creator's Namespace and Feeding Bad Prices to Pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowContractPushers` has no deadline or revocation-state check. After a contract pusher self-revokes via `revokePusher()`, the creator can immediately call `allowContractPushers` again. Because consent is re-verified by a live `isPusher(creator)` staticcall — and most pusher contracts return `true` permanently for their configured creator — the delegation is silently re-established without the pusher's knowledge. The pusher's subsequent price pushes (now intended for its own namespace) are redirected into the creator's namespace, injecting prices for the wrong asset pair into feeds consumed by pools.

---

### Finding Description

The EOA delegation path (`allowPushers`) explicitly requires a deadline because:

> "the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [1](#0-0) 

The contract-pusher path (`allowContractPushers`) omits the deadline entirely, reasoning that a live `isPusher(creator)` call "has nothing to replay": [2](#0-1) 

This reasoning is incomplete. After a pusher calls `revokePusher()`: [3](#0-2) 

`namespaceRemapping[pusher]` is cleared to `address(0)`. The pusher now pushes into its own namespace. However, the creator can immediately call `allowContractPushers([pusher])` again. The function re-queries `pusher.isPusher(creator)` — which returns `true` for any pusher contract that does not internally track its own revocation state (the common case, as shown by `MockPusherAllowed` and `MockPusherSelective` in the test suite). The mapping is re-written:

```
namespaceRemapping[pusher] = msg.sender;  // creator
```

The pusher is unaware of the re-delegation. Its subsequent `fallback()` pushes resolve the namespace as: [4](#0-3) 

`creator = namespaceRemapping[msg.sender]` — now the creator's address again — so every slot word lands in the creator's storage namespace, not the pusher's own. The oracle stores whatever packed price/spread the pusher sends with no asset-pair validation: [5](#0-4) 

If the pusher is now pushing prices for a different asset pair (e.g., BTC/USD in its own namespace), those prices overwrite the creator's ETH/USD feed. Pools that registered against the creator's feedId via `AnchoredPriceProvider` or `ProtectedPriceProvider` consume the corrupted price on the next swap.

---

### Impact Explanation

The corrupted slot value is decoded by `getOracleData` → `_loadSlotLayout` → `U64x32.decode` and passed as `mid` to `AnchoredPriceProvider._readLeg`: [6](#0-5) 

A BTC/USD price (~`65_000 * 1e8`) injected into an ETH/USD feed (~`3_000 * 1e8`) passes all guards (non-zero, non-stale, within any loose `priceGuard` bounds) and produces a bid/ask band ~21× too wide on the high side. Swappers receive ETH at BTC prices, draining the pool of token1 or token0 depending on swap direction.

---

### Likelihood Explanation

- **Trigger**: Any creator (semi-trusted namespace owner) who wants to manipulate their own feeds.
- **Pusher contract requirement**: The pusher's `isPusher` must return `true` after revocation. This is the default for any simple bot contract that hard-codes its creator (e.g., `MockPusherAllowed`, `MockPusherSelective`). The production deployment script (`SetAllowance.sol`) uses `allowContractPushers` with no indication that pusher contracts track revocation state. [7](#0-6) 

- **No on-chain signal**: `revokePusher` emits `PusherRevoked`, but `allowContractPushers` emits `PusherAuthorized` — the re-delegation is indistinguishable from an initial delegation in the event log.

---

### Recommendation

Mirror the EOA path: add a deadline to `allowContractPushers` and require the pusher contract to sign or otherwise attest consent at a specific point in time, OR maintain an on-chain `revokedAt[pusher][creator]` timestamp and require that the live `isPusher` call post-dates it:

```solidity
function allowContractPushers(uint256 deadline, address[] calldata pushers) external {
    _ensureDeadline(deadline);
    // ... existing isPusher check ...
    require(revokedAt[pusher][msg.sender] == 0 || /* pusher re-consented after revocation */);
    namespaceRemapping[pusher] = msg.sender;
}
```

Alternatively, document that pusher contracts MUST set `isPusher(creator) = false` upon self-revocation and enforce this via an interface check.

---

### Proof of Concept

```solidity
// Pusher contract that permanently returns true (common pattern)
contract SimplePusher {
    function isPusher(address) external pure returns (bool) { return true; }
}

// Step 1: Creator delegates pusher
vm.prank(creator);
oracle.allowContractPushers(_pushers(address(pusher)));
assertEq(oracle.namespaceRemapping(address(pusher)), creator);

// Step 2: Pusher self-revokes — intends to push BTC/USD for its own namespace
vm.prank(address(pusher));
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(address(pusher)), address(0));

// Step 3: Pusher pushes BTC/USD price into its OWN namespace
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 btcRaw = _packRaw(uint32(65_000_00000), 3, 3); // BTC/

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-191)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L238-243)
```text
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-321)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L340-344)
```text
            bool newer = timestampMs.isAfter(oldTimestampMs);
            if (!newer) continue;

            _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
        }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-295)
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
    }
```

**File:** smart-contracts-poc/script/SetAllowance.sol (L19-23)
```text
        vm.startBroadcast(deployerKey);
        CompressedOracleV1 oracle = CompressedOracleV1(0x5EcF662aBB8C2AB099862F9Ef2DDc16CBC8A9977);
        oracle.removePushers(oldPushers);
        oracle.allowContractPushers(pushers);
    }
```
