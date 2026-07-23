The code is fully readable. Let me trace the exact path.

The grep confirms there is **no nonce, no used-signature tracking, and no `signatureUsed` mapping** anywhere in the oracle contracts. The `_ensureDeadline` check only validates `block.timestamp <= deadline` — it never marks the signature as consumed.

The vulnerability is real. Here is the full analysis:

---

### Title
Signature Replay in `allowPushers` Defeats `revokePusher` Finality Within the Deadline Window — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary
`allowPushers` verifies an EIP-191 signature but never records it as used. A creator who holds a valid (not-yet-expired) pusher signature can call `allowPushers` repeatedly, re-establishing `namespaceRemapping[pusher] = creator` every time the pusher calls `revokePusher()`, for the entire lifetime of the deadline.

### Finding Description

`allowPushers` constructs a hash over `(block.chainid, address(this), deadline, pusher, msg.sender)` and recovers the signer: [1](#0-0) 

The only replay guard is `_ensureDeadline(deadline)`, which checks `block.timestamp <= deadline`: [2](#0-1) 

There is no nonce, no `mapping(bytes32 => bool) usedSignatures`, and no other consumed-signature tracking anywhere in the contract. The code comment in `allowPushers` explicitly acknowledges the deadline is the sole mechanism to prevent re-establishment after revocation: [3](#0-2) 

But the deadline only prevents replay **after** it expires. Within the validity window, the same `(deadline, pusher, creator)` tuple is accepted an unlimited number of times.

`revokePusher` sets `namespaceRemapping[msg.sender] = address(0)`: [4](#0-3) 

The creator immediately replays the original signature, restoring `namespaceRemapping[pusher] = creator`. The pusher has no on-chain way to detect this without monitoring events, and no way to prevent it until the deadline expires.

The `fallback` push path routes based on `namespaceRemapping[msg.sender]`: [5](#0-4) 

So every push the pusher makes after their (silently-undone) revocation lands in the **creator's** namespace, not the pusher's own namespace.

### Impact Explanation

- The pusher's own namespace feeds receive **no updates** — any pools consuming feeds in the pusher's namespace see stale prices, which satisfies the "bad-price execution / stale oracle data reaching a pool swap" impact gate.
- The creator's namespace continues to receive the pusher's data against the pusher's explicit revocation intent — an admin-boundary break where an unprivileged creator bypasses the pusher-revocation finality invariant.
- The pusher cannot escape the re-delegation loop until the deadline timestamp passes; with a typical 1-day deadline the window is substantial.

### Likelihood Explanation

The creator must have retained the original signature bytes (trivially available from the original `allowPushers` calldata on-chain) and must call `allowPushers` again before the deadline. This is a deliberate, low-cost on-chain action requiring no special privilege beyond being the original creator who called `allowPushers` the first time.

### Recommendation

Record each consumed signature hash in a `mapping(bytes32 => bool) private _usedSignatures` and revert if the hash has already been seen:

```solidity
mapping(bytes32 => bool) private _usedSignatures;

// inside allowPushers, after recovering the signer:
require(!_usedSignatures[hash], SignatureAlreadyUsed());
_usedSignatures[hash] = true;
```

Alternatively, include a per-pusher nonce in the signed payload and increment it on each successful `allowPushers` call, so any previously-signed message is invalidated after first use.

### Proof of Concept

```solidity
// Foundry unit test sketch
function test_replayAllowPushersAfterRevoke() public {
    uint256 deadline = block.timestamp + 1 days;

    // pusher signs consent for creator
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
    );
    (uint8 v, bytes32 r, bytes32 s) = vm.sign(pusherKey, hash);
    bytes memory sig = abi.encodePacked(r, s, v);

    // creator delegates pusher
    address[] memory pushers = new address[](1); pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1); sigs[0] = sig;
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator);

    // pusher revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0));

    // creator replays the SAME signature — deadline still valid
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);  // succeeds, no revert
    assertEq(oracle.namespaceRemapping(pusher), creator); // pusher re-delegated

    // pusher's subsequent push lands in creator's namespace, not pusher's own
    vm.prank(pusher);
    (bool ok,) = address(oracle).call(slotWord);
    assertTrue(ok);
    // creator's feed is updated; pusher's own feed remains stale
}
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-192)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-317)
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
