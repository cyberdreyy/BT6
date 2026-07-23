### Title
Pusher Self-Revocation Is Replayable Within the Deadline Window, Enabling Continued Bad-Price Injection Into Pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`revokePusher()` sets `namespaceRemapping[pusher] = address(0)`, but the creator can immediately replay the original `allowPushers` EIP-191 signature — which carries no revocation nonce — to re-establish `namespaceRemapping[pusher] = creator` at any point before the deadline. The pusher's self-revocation therefore has no permanent effect within the deadline window. If the pusher key is compromised, the attacker retains the ability to inject arbitrary prices into the creator's feed namespace, which flows through `AnchoredPriceProvider` into live pool swaps.

---

### Finding Description

**Delegation path — `allowPushers`**

`allowPushers` (line 192) accepts a pusher-signed EIP-191 message covering exactly:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no per-pusher nonce, no one-time-use flag, and no on-chain record that a revocation has occurred. The signature is structurally replayable by the creator for the entire `[now, deadline]` window.

**Revocation path — `revokePusher`**

`revokePusher` (line 238) sets `namespaceRemapping[msg.sender] = address(0)`: [2](#0-1) 

After this call the pusher's `fallback()` pushes land in the pusher's own namespace (harmless). However, the creator can immediately call `allowPushers` again with the identical `(deadline, pusher, sig)` tuple, writing `namespaceRemapping[pusher] = creator` again. No guard prevents this.

**Push path — `fallback()`**

The `fallback` (line 311) resolves the target namespace from `namespaceRemapping[msg.sender]`: [3](#0-2) 

Once the delegation is re-established, an attacker holding the compromised pusher key writes arbitrary price words into the creator's storage slots. The only per-word guards are a future-timestamp cap (`revertIfAfterBlockTimeWithDrift`) and monotonicity; neither prevents a fresh, plausible bad price.

**Read path — `AnchoredPriceProvider._readLeg`**

`AnchoredPriceProvider` reads the `CompressedOracle` via the permissionless `price(feedId, pool)` view (the `CompressedOracle` explicitly marks `pool` as unused and applies no `inSwap`/`registeredPool` gate): [4](#0-3) 

`_readLeg` then applies staleness, `priceGuard`, and `MAX_SPREAD_BPS` checks: [5](#0-4) 

`priceGuard` defaults to `[0, type(uint128).max]` when unset, so an attacker who crafts a price within the (wide) default bounds and with a fresh timestamp passes all three guards. The reference band in `_computeBidAsk` is derived from the same compromised feed, so the clamp does not protect against a manipulated mid.

---

### Impact Explanation

**Bad-price execution.** An attacker with a compromised pusher key can inject a manipulated mid price into the creator's feed. `AnchoredPriceProvider._computeBidAsk` derives `refBid`/`refAsk` from that mid, so the entire reference band shifts. The pool executes swaps at the attacker-controlled bid/ask, causing direct loss of trader principal or LP assets above Sherlock thresholds.

---

### Likelihood Explanation

**Medium.** Three conditions must hold simultaneously:

1. A pusher key is compromised.
2. The creator calls `allowPushers` again with the original signature before the deadline — plausible via an automated re-delegation keeper or routine maintenance that does not check for prior revocations.
3. The deadline has not yet expired. No on-chain cap is enforced on deadline length, so operators may sign consents valid for days or weeks.

The code comment at line 188–191 explicitly acknowledges that an undated signature "could re-establish a delegation AFTER the pusher revoked it" and states the deadline is the mitigation — but the deadline only closes the window after expiry, not within it. [6](#0-5) 

---

### Recommendation

Add a per-pusher revocation nonce stored in a `mapping(address => uint256) public pusherNonce`. Include the nonce in the signed message:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))
```

In `revokePusher()`, increment `pusherNonce[pusher]` after zeroing the remapping. Any previously signed consent is then cryptographically invalidated and cannot be replayed by the creator.

---

### Proof of Concept

```
1. Creator C calls allowPushers(deadline = now + 7 days, [P], [sig_P])
   → namespaceRemapping[P] = C
   → P's fallback() pushes land in C's namespace (feedId encodes C)

2. P's private key is compromised. Attacker A (holding P's key) pushes
   a manipulated price word into C's slot via fallback().

3. P (legitimate holder) calls revokePusher()
   → namespaceRemapping[P] = address(0)
   → A's subsequent pushes land in P's own namespace — C's feed is safe.

4. C's automated keeper detects PusherRevoked and re-calls
   allowPushers(deadline = now + 7 days, [P], [sig_P])   ← SAME signature
   → namespaceRemapping[P] = C  (replay succeeds; no nonce check)

5. A calls fallback() with a crafted slot word:
   - timestamp = block.timestamp (passes revertIfAfterBlockTimeWithDrift)
   - price = manipulated U64x32 value within priceGuard bounds
   - spread indexes = valid codebook entries
   → C's storage slot is overwritten with the bad price.

6. Pool reads C's feedId via AnchoredPriceProvider.getBidAndAskPrice()
   → _readLeg calls CompressedOracle.price(feedId, pool) (permissionless)
   → bad mid passes staleness + priceGuard + MAX_SPREAD_BPS
   → _computeBidAsk derives refBid/refAsk from the manipulated mid
   → pool executes swap at attacker-controlled bid/ask → loss of funds.
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L163-169)
```text
    function price(bytes32 feedId, address /* pool */)
        external
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        return _price(feedId);
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
