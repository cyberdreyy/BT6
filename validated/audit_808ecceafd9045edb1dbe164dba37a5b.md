### Title
`allowPushers` Consent Signature Has No Nonce — Creator Can Re-Delegate a Self-Revoked Pusher, Routing Wrong Prices Into Pool Feeds - (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies a pusher's EIP-191 consent but tracks no nonce and no used-signature set. After a pusher calls `revokePusher()`, the creator can immediately replay the original consent signature (same deadline, still valid) to re-establish the delegation. If the pusher's automated bot continues pushing — now believing it writes to its own namespace with different asset prices — those pushes land in the creator's namespace and are consumed by pools, producing bad-price execution.

---

### Finding Description

`allowPushers` signs over:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no used-signature bitmap, and no per-pusher revocation counter. The same `(chainid, oracle, deadline, pusher, creator)` tuple is valid for an unlimited number of `allowPushers` calls as long as `block.timestamp <= deadline`.

`revokePusher()` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But the creator can immediately call `allowPushers` again with the identical signature, writing `namespaceRemapping[pusher] = creator` again. The pusher's revocation is fully bypassed within the deadline window.

The code's own NatSpec acknowledges the risk but treats the deadline as sufficient mitigation:

> *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [3](#0-2) 

The deadline only bounds the window; it does not prevent replay within that window.

The `fallback()` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So any push made by the pusher after re-delegation lands in the creator's namespace, not the pusher's own.

---

### Impact Explanation

The creator's feed `feedIdOf(creator, slot, pos)` is bound to a pool via `AnchoredPriceProvider`. The provider reads prices through `_readLeg` → `oracle.price(feedId, pool)`: [5](#0-4) 

If the pusher's bot, after revoking, reconfigures to push prices for a different asset (e.g., BTC/USD instead of ETH/USD) and the creator replays the original consent to re-delegate, those BTC/USD prices overwrite the creator's ETH/USD feed. The pool then executes swaps at the wrong mid price, causing direct loss of user principal through bad-price execution.

---

### Likelihood Explanation

**Medium.** Three conditions are required:

1. A pusher revokes (legitimate action, e.g., to stop servicing a creator).
2. The creator replays the original signature before the deadline expires. Deadlines are typically set to days or weeks, giving ample replay window.
3. The pusher's automated bot continues pushing (for its own namespace) after revocation — a realistic scenario for bots that reconfigure rather than halt.

The creator is a valid semi-trusted trigger (controls their own namespace, not the protocol admin). No privileged role is needed.

---

### Recommendation

Add a per-pusher revocation nonce to the consent signature domain:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))

// In revokePusher / removePushers, after clearing the mapping:
pusherNonce[pusher]++;
```

This ensures that any signature issued before a revocation is invalidated immediately upon `revokePusher()` or `removePushers()`, regardless of the deadline.

---

### Proof of Concept

```solidity
// Setup: creator delegates pusher with deadline = now + 1 day
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;

vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
// namespaceRemapping[pusher] == creator ✓

// Step 1: Pusher self-revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared ✓

// Step 2: Creator replays the SAME signature (deadline still valid)
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs); // NO REVERT — same sig accepted again
assertEq(oracle.namespaceRemapping(pusher), creator); // re-delegated! ✓

// Step 3: Pusher's bot (now pushing BTC/USD for its own namespace) pushes
// These pushes land in creator's ETH/USD feed → wrong prices reach pool
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 btcPrice = _packRaw(BTC_USD_PRICE, 3, 3);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(slot, pos, btcPrice, tsMs));
assertTrue(ok);

// Creator's feed now contains BTC/USD price
IOffchainOracle.OracleData memory data = oracle.getOracleData(oracle.feedIdOf(creator, slot, pos));
assertEq(data.price, U64x32.decode(BTC_USD_PRICE)); // wrong price in ETH/USD feed
// Pool swap now executes at BTC/USD price → direct loss of funds
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-207)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));
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
