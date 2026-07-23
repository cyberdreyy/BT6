Based on my investigation of the CompressedOracle pusher delegation system, I can identify a direct analog to the front-running cancellation bug.

---

### Title
Revoked Pusher Can Front-Run `removePushers` to Inject a Bad Price Into Creator's Oracle Namespace — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary
When a creator broadcasts `removePushers` to revoke a no-longer-trusted pusher, the pusher can observe the pending transaction in the mempool and front-run it with a manipulated price push. Because `namespaceRemapping[pusher]` still resolves to the creator at execution time, the bad price lands in the creator's namespace and is consumable by any pool bound to that feed.

### Finding Description
The `removePushers` function atomically clears `namespaceRemapping[pusher]` to `address(0)`: [1](#0-0) 

Between the moment the creator broadcasts this transaction and the moment it is mined, the pusher retains full write access to the creator's namespace. The push path resolves the namespace at execution time via `namespaceRemapping[msg.sender]`, so a push submitted with a higher gas price will be included first, writing a manipulated price to the creator's namespace before the revocation takes effect.

The timestamp monotonicity check does not prevent this: [2](#0-1) 

The pusher simply uses the current block timestamp (in milliseconds), which is strictly newer than any previously stored value, so `timestampMs.isAfter(oldTimestampMs)` returns `true` and the write proceeds.

The `allowPushers` comment explicitly acknowledges the deadline is needed to prevent a pusher from re-establishing delegation after self-revoking: [3](#0-2) 

However, no analogous protection exists for the creator-side `removePushers` path against a pusher who front-runs the revocation with a bad price.

### Impact Explanation
A bad price injected into the creator's namespace is consumed by any `PriceProvider` or `AnchoredPriceProvider` bound to that feed via the `price(feedId, pool)` read path: [4](#0-3) 

If the manipulated price passes the `AnchoredPriceProvider`'s deviation check (e.g., it is within the allowed deviation band but adversarially positioned at its edge), it reaches pool swaps as a bid/ask quote. This constitutes bad-price execution: a trader may receive more than the oracle/bin curve permits, or the pool may receive less than owed, causing direct loss of user principal or protocol fees for the duration of that block.

If no `priceGuard` (min/max bounds) has been set for the feed, the injected price is entirely unclamped at the oracle layer.

### Likelihood Explanation
The trigger requires a malicious or compromised pusher actively monitoring the mempool — a realistic scenario for a compromised oracle infrastructure key. The creator's intent to revoke is visible in the mempool and provides the signal. The attack requires no permissions beyond the existing (soon-to-be-revoked) delegation. The pusher is a valid semi-trusted trigger: not a factory owner or oracle admin, but a delegated party whose trust has been withdrawn.

### Recommendation
Add a two-step revocation: first mark the pusher as "pending revocation" (blocking further writes to the creator's namespace immediately), then finalize after one block. Alternatively, require the creator to set a tight `priceGuard` before revoking a pusher, so any injected price is bounded within an acceptable range regardless of the front-running window.

### Proof of Concept
1. Creator authorizes pusher via `allowPushers(deadline, [pusher], [sig])`. `namespaceRemapping[pusher] == creator`.
2. Creator decides pusher is compromised and broadcasts `removePushers([pusher])`.
3. Pusher observes the pending revocation in the mempool.
4. Pusher broadcasts a push transaction with a higher gas price, injecting a manipulated price (e.g., 2× the true mid) with `timestampMs = block.timestamp * 1000` (strictly newer than stored).
5. Push transaction is mined first: `namespaceRemapping[pusher] == creator` → bad price written to creator's namespace.
6. `removePushers` is mined next: `namespaceRemapping[pusher]` cleared to `address(0)`.
7. Any pool consuming the creator's feed via `PriceProvider`/`AnchoredPriceProvider` now holds a bad bid/ask for at least one block.
8. A swap executed in this window receives the manipulated quote; the trader extracts value or the pool is underpaid.

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L163-178)
```text
    function price(bytes32 feedId, address /* pool */)
        external
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        return _price(feedId);
    }

    function _price(bytes32 feedId)
        internal
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        OracleData memory data = getOracleData(feedId);
        return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-212)
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
    }
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L284-293)
```text
        TimeMs timestampMs = toTimeMs(newSlotValue >> 8 & X56);
        timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
        bytes32 key = bytes32(namespace | uint256(slotId));
        uint256 old = uint256(_loadStorage(key));
        TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

        bool newer = timestampMs.isAfter(oldTimestampMs);
        if (!newer) {
            return false;
        }
```
