### Title
Pusher Consent Signature Not Consumed After Use in `allowPushers`, Enabling Pre-Deadline Delegation Replay to Hijack Namespace and Stale Pool Prices — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies a pusher's EIP-191 consent signature and sets `namespaceRemapping[pusher] = msg.sender`, but never marks the signature as consumed. The only replay guard is the `deadline` field, which only blocks re-use **after** expiry. Within the deadline window, any creator who holds a valid consent signature can call `allowPushers` an unlimited number of times with the same bytes, overwriting `namespaceRemapping[pusher]` at will — including after the pusher has revoked and re-delegated to a different creator.

---

### Finding Description

`allowPushers` in `CompressedOracleV1` performs the following steps:

1. Checks `block.timestamp <= deadline` via `_ensureDeadline`.
2. Recovers the signer from `keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))`.
3. Writes `namespaceRemapping[pusher] = msg.sender`. [1](#0-0) 

There is no `usedSignatures` bitmap, nonce, or any other per-signature invalidation. The grep across all production contracts confirms no such tracking exists anywhere in the oracle stack.

The code's own NatSpec acknowledges the replay risk but misidentifies the deadline as a complete fix:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [2](#0-1) 

The deadline prevents replay **after** expiry, but not **before** it. Within the deadline window the exact same `(deadline, pusher, signature)` triple is accepted on every call.

**Concrete attack path:**

| Step | Actor | Action | `namespaceRemapping[pusher]` |
|---|---|---|---|
| 1 | Pusher | Signs consent for Creator A, deadline = T+7d | — |
| 2 | Creator A | `allowPushers(T+7d, [pusher], [sig_A])` | `creator_A` |
| 3 | Pusher | `revokePusher()` | `address(0)` |
| 4 | Pusher | Signs new consent for Creator B, deadline = T+1d | — |
| 5 | Creator B | `allowPushers(T+1d, [pusher], [sig_B])` | `creator_B` |
| 6 | Creator A | **Replays** `allowPushers(T+7d, [pusher], [sig_A])` | **`creator_A`** ← overwritten |

After step 6, every subsequent `fallback()` push from the pusher resolves `namespaceRemapping[msg.sender] = creator_A` and writes into Creator A's namespace: [3](#0-2) 

Creator B's namespace receives no new data. The pusher cannot escape this loop: each `revokePusher()` call clears the mapping, but Creator A immediately replays `sig_A` to restore it. The pusher is locked into Creator A's namespace until `T+7d` expires.

---

### Impact Explanation

Creator B's oracle slots stop receiving updates. `AnchoredPriceProvider._readLeg` checks staleness on every swap: [4](#0-3) 

Once `(block.timestamp - refTime) > MAX_REF_STALENESS`, `_readLeg` returns `ok = false`, `_getBidAndAskPrice` returns `(0, type(uint128).max)`, and `getBidAndAskPrice` reverts with `FeedStalled`: [5](#0-4) 

Every pool whose `IPriceProvider` is bound to Creator B's `feedId` becomes unable to execute swaps for the entire duration of the replay window — matching the allowed impact **"Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows"** and **"Bad-price execution: stale bid/ask quote reaches a pool swap"** (here the stale quote halts the pool entirely).

---

### Likelihood Explanation

- Creator A only needs to retain the original `(deadline, pusher, sig_A)` tuple — no on-chain state is required.
- The attack is a single permissionless `allowPushers` call; gas cost is negligible.
- The window is as long as the deadline the pusher originally agreed to (commonly days to weeks in off-chain oracle setups).
- The pusher has no on-chain recourse until the deadline expires; `revokePusher` is immediately reversible by Creator A.

Likelihood: **Medium** (requires a prior relationship between Creator A and the pusher, but the exploit itself is trivial once that relationship existed).

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedConsents` keyed on the full digest, and revert if the digest has already been used:

```solidity
mapping(bytes32 => bool) private _usedConsents;

function allowPushers(...) external {
    _ensureDeadline(deadline);
    for (...) {
        bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
        );
        require(!_usedConsents[hash], SignatureAlreadyUsed());
        require(pusher == ECDSA.recover(hash, signatures[i]));
        _usedConsents[hash] = true;
        namespaceRemapping[pusher] = msg.sender;
        emit PusherAuthorized(pusher, msg.sender);
    }
}
```

Alternatively, include a per-pusher nonce in the signed message so each consent is inherently single-use.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

// Foundry test demonstrating that Creator A can replay sig_A after
// the pusher has re-delegated to Creator B.

function testAllowPushersSignatureReplayOverridesNewDelegation() public {
    uint256 PUSHER_KEY  = 0xABCD;
    uint256 CREATOR_A_KEY = 0x1111;
    uint256 CREATOR_B_KEY = 0x2222;

    address pusher   = vm.addr(PUSHER_KEY);
    address creatorA = vm.addr(CREATOR_A_KEY);
    address creatorB = vm.addr(CREATOR_B_KEY);

    uint256 deadlineA = block.timestamp + 7 days;
    uint256 deadlineB = block.timestamp + 1 days;

    // Step 1: pusher signs consent for Creator A
    bytes memory sigA = _signConsent(PUSHER_KEY, deadlineA, pusher, creatorA);

    // Step 2: Creator A establishes delegation
    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sigA;
    vm.prank(creatorA);
    oracle.allowPushers(deadlineA, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creatorA);

    // Step 3: Pusher revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0));

    // Step 4-5: Pusher re-delegates to Creator B
    bytes memory sigB = _signConsent(PUSHER_KEY, deadlineB, pusher, creatorB);
    sigs[0] = sigB;
    vm.prank(creatorB);
    oracle.allowPushers(deadlineB, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creatorB, "should be creatorB");

    // Step 6: Creator A REPLAYS old sig_A — overwrites creatorB's delegation
    sigs[0] = sigA;
    vm.prank(creatorA);
    oracle.allowPushers(deadlineA, pushers, sigs); // succeeds — no replay guard

    // Pusher's pushes now land in Creator A's namespace, not Creator B's
    assertEq(oracle.namespaceRemapping(pusher), creatorA,
        "VULN: creatorA replayed sig to hijack pusher from creatorB");

    // Creator B's feeds will go stale → AnchoredPriceProvider reverts FeedStalled
}
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L282-283)
```text
        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```
