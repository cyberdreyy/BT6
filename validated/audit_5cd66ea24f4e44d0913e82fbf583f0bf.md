The vulnerability is real. Here is the full analysis:

---

### Title
Signature Replay in `allowPushers` Allows Creator to Re-Delegate a Revoked Pusher Without Fresh Consent — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` validates a pusher's EIP-191 signature but has no nonce or used-signature tracking. A creator who retains the original signature can call `allowPushers` again with the same signature after the pusher has called `revokePusher()`, as long as the original deadline has not expired. This re-establishes `namespaceRemapping[pusher] = creator` without the pusher's knowledge or fresh consent.

---

### Finding Description

The `allowPushers` function in `CompressedOracleV1` verifies a pusher's EIP-191 signature over `(chainid, oracle, deadline, pusher, creator)` and then unconditionally writes `namespaceRemapping[pusher] = msg.sender`: [1](#0-0) 

The only replay guard is `_ensureDeadline`, which only checks that `block.timestamp <= deadline`: [2](#0-1) 

`revokePusher` clears the mapping to `address(0)`: [3](#0-2) 

There is no nonce, no used-signature bitmap, and no check that the pusher's current mapping is `address(0)` before re-writing it. The code's own NatSpec comment acknowledges the concern ("an undated signature could re-establish a delegation AFTER the pusher revoked it") but the deadline-only guard does not close this window — it only prevents replay *after* expiry, not replay *within* the deadline window following a revocation: [4](#0-3) 

**Attack sequence:**
1. Pusher signs consent for `(chainid, oracle, deadline=T+1h, pusher, creator)`.
2. Creator calls `allowPushers` → `namespaceRemapping[pusher] = creator`.
3. Pusher calls `revokePusher()` → `namespaceRemapping[pusher] = address(0)`.
4. Creator calls `allowPushers` again with the **same** signature before `T+1h` → succeeds; `namespaceRemapping[pusher] = creator` is restored.

---

### Impact Explanation

After forced re-delegation:

- The pusher's subsequent pushes (which the pusher believes go to its own namespace) are silently redirected into the creator's namespace via the `fallback()` push path: [5](#0-4) 

- The pusher's own namespace feeds receive no updates → price 0 / timestamp 0 → any pool or consumer reading `feedIdOf(pusher, slotIndex, positionIndex)` gets stale data, which every consumer rejects as stale: [6](#0-5) 

- Pools that use the pusher's namespace feeds become unable to execute swaps (bad-price / stale-price execution path), breaking core pool functionality.

---

### Likelihood Explanation

- The creator already holds the original signature (they submitted it in the first `allowPushers` call).
- The creator only needs to act before the deadline expires — a window that could be hours or days.
- No special role or privileged access is required; the creator is an ordinary user.
- The pusher has no on-chain mechanism to invalidate the old signature short of waiting for the deadline to expire.

---

### Recommendation

Add a per-pusher nonce to the signed message and increment it on each successful `allowPushers` call, or record used `(pusher, deadline)` pairs in a mapping. Alternatively, on `revokePusher`, record a `revokedAt[pusher]` timestamp and reject any `allowPushers` call whose `deadline` was issued before `revokedAt[pusher]`. The simplest fix is a per-pusher nonce:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]++))

// In revokePusher:
pusherNonce[msg.sender]++; // invalidates all outstanding signatures
```

---

### Proof of Concept

```solidity
function testSignatureReplayAfterRevoke() public {
    uint256 deadline = block.timestamp + 1 hours;
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // Step 1: initial delegation
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator);

    // Step 2: pusher revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0));

    // Step 3: creator replays the SAME signature before deadline
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs); // succeeds — no revert
    assertEq(oracle.namespaceRemapping(pusher), creator); // re-delegated without fresh consent
}
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L22-23)
```text
///         A never-pushed position reads as price 0 / timestamp 0, which every consumer
///         already rejects as stale — no seeding or creation step is needed.
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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
