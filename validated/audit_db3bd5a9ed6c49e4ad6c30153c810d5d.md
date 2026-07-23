### Title
Creator Can Replay Pusher Consent Signature to Re-establish Delegation After Revocation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` in `CompressedOracleV1` does not track used signatures or maintain a per-pusher nonce. A creator who holds a valid (non-expired) pusher consent signature can call `allowPushers` repeatedly with the same signature, re-establishing delegation every time the pusher calls `revokePusher()`. The pusher cannot permanently revoke within the deadline window, and their subsequent fallback pushes land in the creator's namespace against their intent.

---

### Finding Description

The `allowPushers` function verifies a pusher's EIP-191 signature over the tuple `(chainid, oracle_address, deadline, pusher, creator)`: [1](#0-0) 

There is no nonce, no `usedSignatures` bitmap, and no revocation timestamp check. The only replay guard is the deadline expiry enforced by `_ensureDeadline(deadline)`. Within the deadline window, the exact same `(deadline, pusher, creator)` signature is accepted an unlimited number of times.

`revokePusher()` clears `namespaceRemapping[msg.sender]` to `address(0)`: [2](#0-1) 

But it records no revocation timestamp and invalidates no outstanding signatures. The creator can immediately call `allowPushers` again with the identical signature to restore `namespaceRemapping[pusher] = creator`. This cycle repeats until the deadline expires.

The code's own comment acknowledges the deadline is the sole protection: [3](#0-2) 

However, the deadline only bounds the window — it does not bound the number of re-establishments within that window.

The `fallback` push path resolves the namespace at call time: [4](#0-3) 

So every push the revoked pusher makes after revocation — intending to write to their own namespace — is silently redirected to the creator's namespace once the creator replays the delegation.

The `CompressedOracle.price()` function is open (no in-swap binding, `pool` parameter ignored): [5](#0-4) 

Any pool using this oracle via `AnchoredPriceProvider._readLeg` consumes whatever is stored in the creator's namespace: [6](#0-5) 

---

### Impact Explanation

After the creator replays the delegation, the pusher's fallback pushes — which may carry prices calibrated for the pusher's own feeds, or prices the pusher no longer intends for the creator's context — overwrite the creator's namespace slots. `AnchoredPriceProvider` reads those slots during live swaps via `getBidAndAskPrice()`. A pusher who has revoked and begun pushing data for a different purpose (e.g., their own pools) will inadvertently feed that data into the creator's production pool, constituting bad-price execution. The `AnchoredPriceProvider` staleness and spread guards may not catch a price that is numerically plausible but contextually wrong.

---

### Likelihood Explanation

The creator must have obtained a valid pusher consent signature with a future deadline — a normal operational step. No special privileges are required beyond being the `msg.sender` who originally called `allowPushers`. The pusher's only recourse is to wait for the deadline to expire; during that window the creator can re-establish delegation after every revocation attempt. The attack is fully permissionless from the creator's side and requires a single stored signature.

---

### Recommendation

Track a per-pusher revocation timestamp and reject any `allowPushers` call whose deadline predates the most recent revocation:

```diff
+ mapping(address => uint256) public pusherRevokedAt;

  function revokePusher() external {
      address creator = namespaceRemapping[msg.sender];
      if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
      namespaceRemapping[msg.sender] = address(0);
+     pusherRevokedAt[msg.sender] = block.timestamp;
      emit PusherRevoked(msg.sender, creator);
  }

  function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
      _ensureDeadline(deadline);
      uint256 l = pushers.length;
      require(l == signatures.length);
      for (uint256 i; i < l; i++) {
          address pusher = pushers[i];
          if (pusher == msg.sender) revert NoSelfRemapping();
+         require(deadline > pusherRevokedAt[pusher], SignaturePreRevocation());
          bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
              keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
          );
          require(pusher == ECDSA.recover(hash, signatures[i]));
          namespaceRemapping[pusher] = msg.sender;
          emit PusherAuthorized(pusher, msg.sender);
      }
  }
```

This ensures that any signature whose deadline was issued before the pusher's last revocation is permanently invalidated, requiring a fresh consent from the pusher.

---

### Proof of Concept

```
t=0:  pusher signs sig = EIP191(keccak256(abi.encode(chainid, oracle, deadline=t+1day, pusher, creator)))
t=1:  creator calls allowPushers(deadline, [pusher], [sig])
      → namespaceRemapping[pusher] = creator  ✓

t=2:  pusher calls revokePusher()
      → namespaceRemapping[pusher] = address(0)  ✓

t=3:  creator calls allowPushers(deadline, [pusher], [sig])  // SAME signature
      → _ensureDeadline passes (deadline = t+1day > t=3)
      → ECDSA.recover returns pusher  ✓
      → namespaceRemapping[pusher] = creator  ← delegation re-established

t=4:  pusher's fallback push (intended for own namespace) lands in creator's namespace
      → creator's pool reads wrong price via AnchoredPriceProvider

t=5:  pusher calls revokePusher() again → cleared
t=6:  creator replays sig again → re-established
      ... repeats until t = t+1day
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
