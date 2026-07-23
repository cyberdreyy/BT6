### Title
Pusher Revocation Bypassable via Signature Replay Within Deadline Window — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

After a pusher calls `revokePusher()` to clear their `namespaceRemapping` entry, the creator can immediately re-establish the delegation by replaying the pusher's original EIP-191 signature (with the same deadline) in a second call to `allowPushers`. The revocation is silently undone without any new consent from the pusher, causing the pusher's subsequent slot writes to land in the creator's namespace rather than their own.

### Finding Description

`allowPushers` signs over `(block.chainid, address(this), deadline, pusher, msg.sender)` and enforces only that `block.timestamp <= deadline`. There is no nonce, no per-pusher revocation counter, and no check that the pusher has not already revoked. [1](#0-0) 

When `revokePusher` is called it simply zeroes the mapping: [2](#0-1) 

Because the signed message contains no revocation-awareness (no nonce, no "issued-at" timestamp), the creator can call `allowPushers` a second time with the identical `(deadline, pusher, sig)` tuple — still valid as long as `block.timestamp < deadline` — and overwrite `namespaceRemapping[pusher]` back to `msg.sender`. The pusher's revocation is undone without their knowledge or consent.

The code comment acknowledges the deadline is the sole replay guard: [3](#0-2) 

But the deadline only prevents using the signature *after* it expires; it does not prevent the creator from replaying it *between* the revocation and the deadline.

The `fallback` push path resolves the namespace at call time: [4](#0-3) 

So every push the pusher makes after their (bypassed) revocation still lands in the creator's namespace.

### Impact Explanation

After the pusher revokes, they may begin pushing data for a different feed or asset into their own namespace (e.g., slot 0, pos 0 now carries BTC/USD instead of ETH/USD). Because the creator silently re-established delegation, those writes land in the creator's namespace under the same slot/position. Any `AnchoredPriceProvider` bound to the creator's feed now reads the wrong asset's price. The `_readLeg` staleness and spread guards do not detect asset substitution — they only check timestamp freshness and spread magnitude: [5](#0-4) 

A pool consuming the creator's feed via `getBidAndAskPrice()` executes swaps at the wrong mid price, causing traders to receive more output than the oracle permits or the pool to receive less input than owed — a direct swap conservation failure.

### Likelihood Explanation

The creator naturally retains the pusher's original signature (they needed it to call `allowPushers` the first time). Re-calling `allowPushers` with the same arguments costs one transaction and requires no special access. Any creator who wishes to retain a pusher's data stream after the pusher revokes can do so trivially within the deadline window. Deadlines are typically set days in the future (the test suite uses `block.timestamp + 1 days`), giving a wide exploitation window. [6](#0-5) 

### Recommendation

Introduce a per-pusher revocation nonce or a "revoked-at" timestamp stored on-chain. Include this value in the signed digest so that a signature issued before a revocation cannot be replayed after it. For example:

```solidity
mapping(address => uint256) public pusherNonce; // incremented on every revoke

// in allowPushers:
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))

// in revokePusher:
pusherNonce[msg.sender]++;
namespaceRemapping[msg.sender] = address(0);
```

This ensures any signature signed before the revocation is invalidated by the nonce increment, exactly as the external report's patch re-adds the collection to the linked list to restore the invariant.

### Proof of Concept

1. Pusher signs consent: `sig = sign(keccak256(abi.encode(chainid, oracle, deadline, pusher, creator)))` with `deadline = block.timestamp + 1 days`.
2. Creator calls `allowPushers(deadline, [pusher], [sig])` → `namespaceRemapping[pusher] = creator`.
3. Pusher calls `revokePusher()` → `namespaceRemapping[pusher] = address(0)`. Pusher now intends to push BTC/USD data into their own namespace.
4. Creator calls `allowPushers(deadline, [pusher], [sig])` again (same args, deadline still valid) → `namespaceRemapping[pusher] = creator` again. No revert.
5. Pusher pushes a slot word with BTC/USD price into `(slotId=0)`. The `fallback` resolves `namespaceRemapping[pusher] == creator` and writes to the creator's storage key `bytes32(uint160(creator) << 96 | 0)`.
6. `AnchoredPriceProvider` reads `feedIdOf(creator, 0, 0)` — now carrying BTC/USD price — and delivers it to a pool configured for ETH/USD. The pool executes swaps at the wrong price. [7](#0-6) [8](#0-7)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-211)
```text
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

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L340-342)
```text
        uint256 deadline = block.timestamp + 1 days;
        _allowPusher(deadline);
        assertEq(oracle.namespaceRemapping(pusher), creator, "pusher should map to creator");
```
