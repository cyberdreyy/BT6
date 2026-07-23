### Title
Missing Deadline in `updateBySignature` Allows Delayed Submission of Signed Slot Values, Feeding Stale Prices into Pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracle.updateBySignature` accepts a feed creator's signed slot value from any caller but does not enforce a deadline. The protocol's own top-level documentation explicitly requires a deadline and its inclusion in the signature hash. Without it, any party who observes a pending signed slot value in the mempool can hold it and submit it at a strategically chosen moment — when the embedded price is stale but the embedded timestamp still falls within the consumer's `MAX_TIME_DELTA` window — causing pools to execute swaps against an incorrect oracle price.

---

### Finding Description

`updateBySignature` is a permissionless function: anyone can call it to push a slot value signed by the feed creator into the oracle's storage. The signature currently commits to:

```
keccak256(abi.encode(chainid, oracleAddress, feedCreator, newSlotValue))
``` [1](#0-0) 

There is no `deadline` argument and no deadline term in the signed digest. The protocol's own top-level English documentation contradicts this implementation:

> `updateBySignature(feedCreator, deadline, newSlotValue, signature)` … signed by `feedCreator` over:
> `keccak256(abi.encode(chainid, oracleAddress, feedCreator, deadline, newSlotValue))`
> Required Guards: `deadline` must be in the future (`DeadlineExceeded` otherwise). [2](#0-1) 

The `allowPushers` function — which also accepts a signed consent — explicitly adds a deadline and explains the rationale in its NatSpec:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [3](#0-2) 

The identical reasoning applies to `updateBySignature`: the signed slot value carries its own embedded timestamp (the price-observation time), but there is nothing preventing the *submission* of that signed value from being delayed arbitrarily. The monotonicity guard only prevents a slot from being overwritten with an *older* timestamp; it does not prevent a valid-but-stale signed value from being submitted for the first time (or after a gap) at a moment chosen by the submitter.

The consumer staleness check in `PriceProvider._getBidAndAskPrice` uses the stored `refTime` (derived from the slot's embedded timestamp) against `block.timestamp`:

```solidity
if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
    return (0, type(uint128).max);
}
``` [4](#0-3) 

If the signed slot value's embedded timestamp is `T` and the attacker submits it at `T + Δ` where `Δ < MAX_TIME_DELTA`, the consumer accepts the price as fresh even though the actual market price may have moved significantly since `T`.

---

### Impact Explanation

A pool consuming a `CompressedOracle`-backed `PriceProvider` will execute swaps at the stale bid/ask derived from the delayed slot value. Traders can receive more output than the current oracle price permits (swap conservation failure), or LPs suffer losses because the pool quotes a price that no longer reflects the market. This is a direct loss of user principal and LP assets, satisfying the High/Critical impact gate.

---

### Likelihood Explanation

The attack requires:
1. A feed creator to broadcast a signed `updateBySignature` transaction (routine oracle maintenance).
2. An attacker to observe the pending transaction in the mempool and extract the signed slot value.
3. The attacker to wait for a favorable price movement within the `MAX_TIME_DELTA` window before submitting.

Step 1 is a normal operational event. Steps 2–3 require only mempool monitoring and timing — no privileged access, no special tokens, no malicious setup. Likelihood is **Medium**.

---

### Recommendation

Add a `deadline` parameter to `updateBySignature`, include it in the signed digest, and enforce it on-chain:

```solidity
function updateBySignature(
    address feedCreator,
    uint256 deadline,          // ← add
    uint256 newSlotValue,
    bytes calldata signature
) external returns (bool) {
    _ensureDeadline(deadline); // ← revert if block.timestamp > deadline

    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(
            block.chainid,
            address(this),
            feedCreator,
            deadline,          // ← include in digest
            newSlotValue
        ))
    );
    require(feedCreator == ECDSA.recover(hash, signature));
    // ... rest of function
}
```

This matches the design already documented in `smart-contracts-poc/docs/en/oracle-packet-structure.md` and mirrors the deadline enforcement already present in `allowPushers`. [5](#0-4) 

---

### Proof of Concept

1. At block timestamp `T`, the actual market price is `$100`. The feed creator signs a slot value embedding timestamp `T` and price `$100` and broadcasts an `updateBySignature` transaction.
2. An attacker observes the pending transaction, extracts `(feedCreator, newSlotValue, signature)`.
3. The attacker suppresses or ignores the original transaction (e.g., by front-running with a higher-gas no-op, or simply waiting for it to be dropped).
4. At `T + (MAX_TIME_DELTA - ε)`, the actual market price has fallen to `$50`.
5. The attacker submits the extracted `updateBySignature` call. The embedded timestamp `T` is accepted because `T ≤ block.timestamp` (not future) and the slot's stored timestamp is older than `T` (monotonicity passes).
6. The oracle now stores price `$100` with timestamp `T`. The consumer's staleness check: `(T + MAX_TIME_DELTA - ε) - T = MAX_TIME_DELTA - ε < MAX_TIME_DELTA` → **not stale, price accepted**.
7. The pool quotes bid/ask around `$100`. The attacker swaps, receiving tokens at `$100` when the true price is `$50`, extracting value from LPs. [6](#0-5) [7](#0-6)

### Citations

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L271-274)
```text
        uint256 slotValue = _slotValue(11, 0, raw, tsMs);

        bytes memory sig = _signSlotValue(CREATOR_KEY, creator, slotValue);
        bool updated = oracle.updateBySignature(creator, slotValue, sig);
```

**File:** smart-contracts-poc/docs/en/oracle-packet-structure.md (L37-47)
```markdown
`updateBySignature(feedCreator, deadline, newSlotValue, signature)` expects `newSlotValue` to be a single slot word (same layout), signed by `feedCreator` over:

```text
keccak256(abi.encode(chainid, oracleAddress, feedCreator, deadline, newSlotValue))
```

## Required Guards

- `deadline` must be in the future (`DeadlineExceeded` otherwise).
- `timestamp` must not be in the future (`FutureTimestamp` otherwise).
- `timestamp` must be strictly increasing per slot (older updates are ignored).
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

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L191-205)
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
```
