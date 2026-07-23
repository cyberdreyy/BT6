### Title
Creator Can Replay Pusher Consent Signature to Re-Establish Delegation After `revokePusher()`, Silently Redirecting Future Price Pushes Into the Wrong Namespace — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` in `CompressedOracleV1` only checks the deadline (a time-based condition) before writing `namespaceRemapping[pusher] = msg.sender`. It does not check whether the pusher has already self-revoked via `revokePusher()`. A creator who holds a still-valid (pre-deadline) consent signature can replay it at any time before the deadline expires to silently re-establish delegation, overriding the pusher's explicit revocation. This is the direct analog of the external bug: the protocol checks only elapsed time, not residual/revoked state, before overwriting a critical configuration.

---

### Finding Description

`allowPushers` requires three things before writing `namespaceRemapping[pusher] = msg.sender`:

1. `_ensureDeadline(deadline)` — the deadline has not yet passed (time-only gate)
2. `pusher != msg.sender` — no self-remapping
3. ECDSA recovery — the pusher signed `(chainid, oracle, deadline, pusher, creator)` [1](#0-0) 

There is no check that `namespaceRemapping[pusher]` is currently `address(0)` (i.e., that the pusher has not already revoked). The code's own comment acknowledges the risk:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [2](#0-1) 

The comment claims the deadline is the solution, but the deadline only blocks replay **after** it expires — it does nothing to prevent replay **before** it expires. The pusher's `revokePusher()` clears `namespaceRemapping[pusher] = address(0)`: [3](#0-2) 

But the creator still holds the original signed bytes. As long as `block.timestamp <= deadline`, the creator can call `allowPushers` again with the identical signature and overwrite the cleared mapping back to themselves — with no on-chain record distinguishing this from the original delegation.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every subsequent `fallback` push by the pusher — even pushes the pusher believes are going to their own namespace — lands in the creator's namespace.

---

### Impact Explanation

After the creator replays the signature:

- The pusher, believing they revoked, begins pushing prices for a **different asset** (e.g., ETH/USD for their own pool) via the `fallback` path.
- Those pushes land in the **creator's namespace** (e.g., the BTC/USD slot that creator A's pool reads).
- `AnchoredPriceProvider._readLeg` reads the creator's namespace feed and passes the ETH/USD price through staleness, spread, and price-guard checks — all of which may pass because the price is fresh and numerically plausible.
- The pool executes swaps at the wrong mid price, causing traders to receive more or less than the oracle/bin curve permits.

This is a **bad-price execution** impact: a stale or wrong-asset bid/ask quote reaches a live pool swap. [5](#0-4) 

---

### Likelihood Explanation

- The creator retains the original signed bytes from the first `allowPushers` call — no additional off-chain work is needed.
- The window is the full deadline duration (up to whatever the pusher agreed to, e.g., 1 day or more).
- The pusher has no on-chain way to detect or prevent the replay before the deadline expires.
- The pusher's `fallback` pushes are the normal operational path for a live price-feed operator.
- No privileged role (oracle admin, factory owner) is required — the creator is a semi-trusted pool admin.

---

### Recommendation

Include a per-pusher nonce or a revocation flag in the signed payload so that a revocation permanently invalidates all prior consent signatures for that `(pusher, creator)` pair. Concretely:

```solidity
mapping(address => uint256) public pusherNonce; // incremented on every revoke

// In allowPushers, bind the nonce:
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))

// In revokePusher, increment the nonce:
pusherNonce[msg.sender]++;
namespaceRemapping[msg.sender] = address(0);
```

Alternatively, check that `namespaceRemapping[pusher] == address(0)` before writing, so a creator cannot silently re-establish a delegation the pusher has cleared.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = now + 1 day
bytes memory sig = sign(PUSHER_KEY, abi.encode(chainid, oracle, deadline, pusher, creator));

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator ✓

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0) ✓

// 4. Creator replays the SAME signature (deadline not yet expired)
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator again — revocation bypassed ✓

// 5. Pusher pushes ETH/USD prices thinking they go to their own namespace
vm.prank(pusher);
(bool ok,) = address(oracle).call(buildSlotWord(slotId=0, pos=0, ethUsdPrice, tsMs));
// Prices land in creator's namespace (BTC/USD slot), not pusher's own

// 6. Creator's pool reads creator's namespace → gets ETH/USD price for BTC/USD pool
// AnchoredPriceProvider passes staleness/guard checks → bad-price swap executes
``` [6](#0-5) [3](#0-2) [7](#0-6)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-211)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
        _ensureDeadline(deadline);

        uint256 l = pushers.length;
        require(l == signatures.length);
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
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
