### Title
Pusher Delegation Signature Replay Allows Creator to Permanently Override `revokePusher()` Within Deadline Window — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` does not track consumed signatures or invalidate them when a pusher self-revokes. A creator who holds a valid (non-expired) pusher-consent signature can replay it an unlimited number of times after `revokePusher()` is called, silently re-establishing delegation and redirecting every subsequent push from the pusher's own namespace into the creator's namespace.

---

### Finding Description

`allowPushers` verifies an EIP-191 signature over `(chainid, oracle, deadline, pusher, creator)` and writes `namespaceRemapping[pusher] = creator`. [1](#0-0) 

`revokePusher` clears the mapping to `address(0)`: [2](#0-1) 

There is **no nonce, no used-signature bitmap, and no on-chain record that a given signature has already been consumed**. The only replay guard is the `deadline` field, which the code's own NatSpec acknowledges is insufficient:

> *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it"* [3](#0-2) 

The deadline only bounds the *outer* replay window; it does nothing to prevent the creator from replaying the same signature repeatedly **within** that window, each time overwriting the pusher's revocation.

The `fallback` push path resolves the effective namespace from `namespaceRemapping[msg.sender]`, falling back to the pusher's own address only when the mapping is `address(0)`: [4](#0-3) 

So every push the pusher makes after the creator replays the signature lands in the **creator's** namespace, not the pusher's own.

---

### Impact Explanation

A pusher who is also a feed creator (their own namespace feeds are consumed by pools) cannot reliably update their own feeds. The creator can front-run any push with a replayed `allowPushers` call, redirecting the pusher's slot word into the creator's namespace. The pusher's own feeds accumulate staleness. Once `MAX_TIME_DELTA` elapses, `PriceProvider._getBidAndAskPrice` returns the stalled sentinel `(0, type(uint128).max)`: [5](#0-4) 

`getBidAndAskPrice` then reverts with `FeedStalled`: [6](#0-5) 

The pool's `swap` call cannot obtain a valid bid/ask price, making the pool's swap flow permanently unusable for as long as the creator keeps replaying the signature — a direct match to the "broken core pool functionality / unusable swap flows" impact gate.

---

### Likelihood Explanation

- The creator is a semi-trusted party (namespace owner) who already holds the pusher's signed consent.
- No special privilege beyond the already-held signature is required.
- The attack is a single on-chain call (`allowPushers`) that can be repeated or front-run at negligible cost.
- Pushers who signed long-lived consents (e.g., 30-day or 1-year deadlines, which are operationally common for automated bots) are fully exposed for the entire remaining deadline window.

---

### Recommendation

Track consumed signatures with a per-pusher nonce or a `mapping(bytes32 => bool) usedConsentHashes` and mark each signature hash as spent on first use. Alternatively, include a per-pusher revocation counter in the signed message so that `revokePusher()` increments the counter and invalidates all previously issued signatures:

```solidity
// In the signed message:
keccak256(abi.encode(
    block.chainid, address(this), deadline,
    pusher, msg.sender,
    revocationNonce[pusher]   // ← new field
))

// In revokePusher():
revocationNonce[msg.sender]++;
namespaceRemapping[msg.sender] = address(0);
```

---

### Proof of Concept

```
1. Pusher P signs consent for creator C with deadline = block.timestamp + 365 days.
   digest = keccak256(abi.encode(chainid, oracle, deadline, P, C))

2. C calls allowPushers(deadline, [P], [sig])
   → namespaceRemapping[P] = C
   → P's pushes land in C's namespace ✓

3. P calls revokePusher()
   → namespaceRemapping[P] = address(0)
   → P's pushes now land in P's own namespace ✓

4. C calls allowPushers(deadline, [P], [sig])   ← SAME signature, no revert
   → namespaceRemapping[P] = C again
   → P's pushes land in C's namespace again ✗

5. P's own feeds (feedIdOf(P, slotX, posY)) receive no updates.
   After MAX_TIME_DELTA seconds, any pool whose priceProvider reads
   feedIdOf(P, slotX, posY) gets (0, type(uint128).max) from _getBidAndAskPrice,
   causing getBidAndAskPrice() to revert FeedStalled on every swap.

6. C can repeat step 4 indefinitely until the deadline expires,
   keeping P's own feeds permanently stale.
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-321)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L197-200)
```text
        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```
