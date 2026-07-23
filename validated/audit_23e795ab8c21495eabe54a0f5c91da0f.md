### Title
`revokePusher()` Is Ineffective Within the Deadline Window Due to Missing Signature Consumption — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1::allowPushers` never marks a pusher's consent signature as consumed. The only replay guard is the deadline, which only prevents replay *after* it expires. Within the deadline window, the creator can call `allowPushers` again with the exact same signature to silently re-establish a delegation the pusher explicitly revoked via `revokePusher()`. The pusher's subsequent fallback pushes then land in the creator's namespace instead of their own, feeding the creator's oracle feeds — and any pool anchored to those feeds — with data the pusher believed was going to their own namespace.

---

### Finding Description

`allowPushers` verifies the pusher's EIP-191 consent signature and sets `namespaceRemapping[pusher] = msg.sender`: [1](#0-0) 

The only replay guard is `_ensureDeadline(deadline)`, which checks `block.timestamp <= deadline`: [2](#0-1) 

`revokePusher()` clears the mapping to `address(0)`: [3](#0-2) 

There is no nonce, no used-signature bitmap, and no per-pusher revocation epoch. The code comment itself acknowledges the concern but treats the deadline as sufficient protection: [4](#0-3) 

The deadline is **not** sufficient: it only prevents replay after expiry. Within the window, the creator can call `allowPushers(deadline, [pusher], [oldSig])` again with the identical arguments, restoring `namespaceRemapping[pusher] = creator` immediately after the pusher cleared it.

The `fallback` push path resolves the namespace at call time: [5](#0-4) 

So any push the pusher makes after their (now-nullified) revocation lands in the creator's namespace, not their own.

---

### Impact Explanation

Every pool that uses the creator's `feedId` via `AnchoredPriceProvider` or `ProtectedPriceProvider` reads prices from the creator's namespace: [6](#0-5) 

If the pusher revoked because they no longer consent to feed the creator's namespace (e.g., they discovered the creator is operating a pool they distrust), the creator can silently re-establish the delegation. The pusher, unaware the revocation was overridden, continues pushing data that flows into the creator's feeds and therefore into live pool swaps. The pusher's own namespace stays empty. The broken invariant is: **after `revokePusher()` succeeds, the pusher's subsequent pushes must land in their own namespace — this guarantee is violated within the deadline window.**

---

### Likelihood Explanation

- The creator retains the signed consent bytes off-chain (they submitted them in the original `allowPushers` call; the calldata is public on-chain).
- The pusher has no on-chain way to detect the re-establishment unless they monitor `PusherAuthorized` events.
- The attack requires only one additional transaction from the creator, callable by anyone who observed the original calldata.
- Deadlines are typically set days to weeks in the future (the test suite uses `block.timestamp + 1 days`), giving a wide exploitation window. [7](#0-6) 

---

### Recommendation

Track consumed signatures with a per-pusher revocation nonce or a `usedSignatures` bitmap keyed on the signature hash. The simplest fix: after a successful `allowPushers` call, record the signature hash as used and reject it on any subsequent call:

```solidity
// Add to state:
mapping(bytes32 => bool) private _usedDelegationSigs;

// In allowPushers, after ECDSA.recover succeeds:
bytes32 sigHash = keccak256(signatures[i]);
require(!_usedDelegationSigs[sigHash], SignatureAlreadyUsed());
_usedDelegationSigs[sigHash] = true;

namespaceRemapping[pusher] = msg.sender;
```

Alternatively, include a per-pusher nonce in the signed message and increment it on each successful delegation, so old signatures are automatically invalidated after revocation.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline 30 days out
uint256 deadline = block.timestamp + 30 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared

// 4. Creator replays the SAME signature — no revert, delegation restored
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs); // ← identical call, succeeds
assertEq(oracle.namespaceRemapping(pusher), creator); // re-established!

// 5. Pusher's next push lands in creator's namespace, not their own
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw = _packRaw(1_500_000, 4, 2);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok);

// Creator's feed is updated — pool swaps read this price
assertGt(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, 0);
// Pusher's own namespace stays empty
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0);
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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-283)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);

        bytes32 _quote = quoteFeedId;
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

        return _computeBidAsk(mid, spreadBps);
    }

    /// @dev Reads one feed and runs its per-leg guards. ok=false (→ caller halts, fail closed) on:
    ///      stale reference, mid == 0, spreadBps == the off-hours/stall marker (spreadBps >= ORACLE_BPS), or a
    ///      priceGuard violation. Each leg is read through the attributed path independently.
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L497-505)
```text
    function _allowPusher(uint256 deadline) internal {
        address[] memory pushers = new address[](1);
        pushers[0] = pusher;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = _signConsent(PUSHER_KEY, deadline, pusher, creator);

        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);
    }
```
