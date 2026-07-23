### Title
`allowPushers` Signature Replay Re-Establishes Revoked Pusher Delegation, Enabling Bad-Price Injection into Pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`CompressedOracleV1.allowPushers` signs consent over `(chainid, oracle, deadline, pusher, creator)` with no nonce and no on-chain revocation flag. A creator can replay the same EIP-191 signature to re-establish a pusher's delegation at any point before the deadline expires, even after the pusher has self-revoked via `revokePusher()`. If the pusher's key is compromised and the pusher revokes to stop the attacker, the creator (unaware of the compromise) can silently restore the delegation, allowing the attacker to push arbitrary prices into the creator's namespace. Pools backed by `AnchoredPriceProvider` or `PriceProvider` reading that namespace then execute swaps against the corrupted price.

### Finding Description

`allowPushers` verifies the pusher's EIP-191 consent and writes `namespaceRemapping[pusher] = creator`: [1](#0-0) 

The signed digest is:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [2](#0-1) 

`revokePusher` clears the mapping but does not invalidate the signature or record a revocation nonce: [3](#0-2) 

Because the signed message contains no nonce and no revocation flag, the creator can call `allowPushers` a second time with the identical `(deadline, pushers, signatures)` tuple. The deadline check passes (deadline is still in the future), the ECDSA recovery succeeds (same hash, same signature), and `namespaceRemapping[pusher]` is written back to `creator`. The pusher's self-revocation is silently undone.

The `fallback` push path resolves the namespace at call time: [4](#0-3) 

So every subsequent push from the compromised pusher key lands in the creator's namespace, not the pusher's own namespace.

The slot-structure documentation explicitly acknowledges the gap but treats the deadline as a complete fix — it is not: [5](#0-4) 

The deadline prevents replay *after* expiry; it does nothing to prevent re-establishment *within* the deadline window.

### Impact Explanation

`AnchoredPriceProvider` and `PriceProvider` read prices from the oracle's `price(feedId, pool)` path, which decodes the creator's namespace slot: [6](#0-5) 

An attacker holding a compromised pusher key can push any `U64x32`-encoded price and any codebook spread indices into the creator's slot. The staleness guard only checks that the timestamp is strictly increasing — it does not verify who pushed the data. A crafted slot word with a fresh timestamp and an extreme price passes all guards and reaches `_computeBidAsk`, producing a bad bid/ask that the pool uses for the swap. Traders receive more output than the true oracle price permits (swap conservation failure) or the pool receives less input than owed.

### Likelihood Explanation

The trigger is the creator calling `allowPushers` a second time with the original signature. This is a realistic, non-malicious action: the creator may believe the pusher's self-revocation was accidental and attempt to restore service. The pusher's key compromise is the precipitating event; the creator's re-delegation is the amplifying action. Both are reachable without any privileged or malicious setup assumption beyond the creator being unaware of the compromise. The window of exposure equals the remaining lifetime of the original deadline, which can be up to the maximum the creator chose (no protocol cap on deadline length is enforced).

### Recommendation

Add an on-chain per-pusher revocation nonce or a `revokedAt[pusher]` timestamp. Include the nonce in the signed digest so that a post-revocation replay produces a different hash and fails ECDSA recovery. Alternatively, record the block timestamp of the most recent revocation and reject any `allowPushers` call whose signature was issued before that timestamp (requires adding an issuedAt field to the signed message).

### Proof of Concept

```solidity
// 1. Creator delegates pusher (deadline = now + 1 day)
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = pusherSign(deadline, pusher, creator); // pusher's EIP-191 consent
vm.prank(creator);
oracle.allowPushers(deadline, toArray(pusher), toArray(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 2. Pusher self-revokes (key compromised)
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 3. Creator re-establishes with the SAME signature (deadline still valid)
vm.prank(creator);
oracle.allowPushers(deadline, toArray(pusher), toArray(sig)); // no revert
assertEq(oracle.namespaceRemapping(pusher), creator); // re-delegated!

// 4. Attacker (holding compromised pusher key) pushes extreme price
uint48 badRaw = packRaw(type(uint32).max, 0, 0); // max U64x32 price, zero spread
bytes memory payload = wordAt(slotId, pos, badRaw, uint56(block.timestamp * 1000));
vm.prank(attacker); // attacker == compromised pusher address
(bool ok,) = address(oracle).call(payload);
assertTrue(ok); // lands in creator's namespace

// 5. Pool swap reads corrupted price → bad-price execution
(uint128 bid, uint128 ask) = provider.getBidAndAskPrice(); // called inside pool.swap
// bid/ask derived from type(uint32).max price → pool misprices the swap
```

### Citations

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

**File:** smart-contracts-poc/contracts/oracles/compressed/docs/en/slot-structure.md (L27-29)
```markdown
Delegation (`allowPushers`) requires each pusher's EIP-191 signature (and a deadline:
the signed consent has no data timestamp, so an undated signature could re-establish a
delegation after the pusher revoked it).
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
