Looking at the `CompressedOracleV1` contract, I need to trace the pusher delegation path and the push execution path to find a missing gate check analogous to the HiFi `isRedeemHTokenAllowed` bug.

**Key observation:**

`allowContractPushers` verifies consent via a live `isPusher(creator)` staticcall at delegation time and sets `namespaceRemapping[pusher] = creator`. The `fallback()` push path only reads `namespaceRemapping[msg.sender]` — it never re-checks `isPusher()`.

---

### Title
Contract Pusher Delegation Persists After `isPusher()` Consent Is Revoked — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary
`allowContractPushers` gates delegation on a live `isPusher(creator)` staticcall, but the `fallback()` push path never re-checks that consent. Once `namespaceRemapping[pusher] = creator` is written, the contract pusher retains the ability to write prices into the creator's namespace even after `isPusher()` returns `false`, bypassing the intended revocation semantic.

### Finding Description

`allowContractPushers` explicitly documents its design rationale:

> "Contract-pusher variant: consent is proven by a LIVE `isPusher(creator)` staticcall instead of a signature, so there is nothing to replay and no deadline is needed."

The consent check at delegation time: [1](#0-0) 

Once the mapping is written, the `fallback()` push path resolves the target namespace solely from `namespaceRemapping[msg.sender]` with no re-verification of `isPusher()`: [2](#0-1) 

The full push loop that writes oracle slot data without any consent re-check: [3](#0-2) 

If the contract pusher's `isPusher()` later returns `false` — due to an upgrade, governance action, or deliberate manipulation — `namespaceRemapping[pusher]` still maps to `creator`. The contract pusher can continue pushing arbitrary price data into the creator's namespace. The creator must explicitly call `removePushers()` to revoke, but the `isPusher()` check creates a false expectation that revocation is automatic. [4](#0-3) 

### Impact Explanation

Pushed slot data flows directly into `getOracleData()` → `price()` → `AnchoredPriceProvider._readLeg()` → pool swap execution: [5](#0-4) 

The `AnchoredPriceProvider` uses the `CompressedOracleV1` as its reference oracle. A manipulated mid price from a still-delegated-but-revoked contract pusher directly sets the reference band (`refBid`/`refAsk`). The band clamp does not protect against a corrupted reference — it only clips the *source* to the band derived from that same corrupted reference: [6](#0-5) 

If the pushed price passes the staleness, spread, and `priceGuard` checks, it reaches pool swaps as a bad bid/ask, causing traders to receive more than the true oracle price permits or the pool to receive less than owed — direct loss of user principal or protocol fees.

### Likelihood Explanation

The scenario requires: (1) a contract pusher is authorized, (2) the pusher's `isPusher()` later returns `false` (upgrade, governance, or deliberate manipulation), (3) the creator does not proactively call `removePushers()`. The comment's framing of `isPusher()` as a "LIVE" check makes it reasonable for a creator to assume revocation is automatic. Likelihood is **medium** — it depends on the creator's operational awareness, but the code's own documentation misleads them.

### Recommendation

Re-check `isPusher(creator)` inside `fallback()` when `namespaceRemapping[msg.sender] != address(0)` and the pusher is a contract (e.g., `msg.sender.code.length > 0`). Alternatively, maintain a separate `contractPushers` mapping to distinguish contract pushers from EOA pushers and re-verify consent on each push. At minimum, remove the word "LIVE" from the `allowContractPushers` NatSpec and document explicitly that `isPusher()` is only checked at delegation time and that creators must call `removePushers()` to revoke.

### Proof of Concept

1. Creator A calls `allowContractPushers([pusherContract])` where `pusherContract.isPusher(A)` returns `true`. `namespaceRemapping[pusherContract] = A` is written.
2. `pusherContract` is upgraded or its governance changes so `isPusher(A)` now returns `false`. Creator A observes this and believes the delegation is revoked.
3. Creator A does **not** call `removePushers([pusherContract])` — relying on the "LIVE" check semantics.
4. An attacker controlling `pusherContract` calls `fallback()` with a crafted 32-byte slot word encoding a manipulated mid price and valid-looking spread indexes and a fresh timestamp.
5. The monotonicity check (`timestampMs.isAfter(oldTimestampMs)`) passes because the new timestamp is newer.
6. The slot is written to `namespaceRemapping[pusherContract]`'s namespace (Creator A's).
7. `AnchoredPriceProvider._readLeg(baseFeedId)` reads the manipulated price, passes staleness/spread/priceGuard checks, and computes a corrupted reference band.
8. Pool swaps execute against the corrupted bid/ask, causing direct loss to traders or the pool.

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L226-231)
```text
            (bool ok, bytes memory res) = pusher.staticcall(abi.encodeWithSignature("isPusher(address)", msg.sender));
            require(ok);
            bool allowed = abi.decode(res, (bool));
            require(allowed);

            namespaceRemapping[pusher] = msg.sender;
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L245-260)
```text
    function removePushers(address[] calldata pushers) external {
        uint256 l = pushers.length;
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];
            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            if (namespaceRemapping[pusher] == msg.sender) {
                namespaceRemapping[pusher] = address(0);
                emit PusherRevoked(pusher, msg.sender);
            } else {
                revert InvalidManager(msg.sender);
            }
        }
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L326-344)
```text
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L307-313)
```text
        // Reference band: mid ± (spreadBps + minMargin), bid rounded down, ask rounded up.
        uint256 half = spreadBps * ONE_BPS_E18 + minMargin; // < BPS_BASE_U by construction (spreadBps <= MAX_SPREAD_BPS here)
        uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
        uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
        if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
            return (0, type(uint128).max);
        }
```
