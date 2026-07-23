The vulnerability is confirmed. The code has no nonce, no used-signature bitmap, and no post-revocation guard in `allowPushers`. The contract's own NatSpec comment (lines 186–191) explicitly states the deadline is the mechanism preventing re-establishment after revocation — but the implementation only checks `deadline > block.timestamp`, not whether the pusher has already revoked.

---

### Title
Signature replay in `allowPushers` allows creator to silently re-establish delegation after pusher self-revokes — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary
`allowPushers` accepts any valid EIP-191 signature over `(chainid, oracle, deadline, pusher, creator)` as long as the deadline has not expired. It does not check whether `namespaceRemapping[pusher]` was previously cleared by a `revokePusher()` call. A creator who holds the original signed consent can replay it any number of times before the deadline, silently overwriting the pusher's self-revocation.

### Finding Description
The NatSpec comment on `allowPushers` explicitly acknowledges the risk:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."

The comment implies the deadline is the guard. But the deadline only prevents use *after* expiry — it does not prevent re-use *within* the deadline window after a revocation. There is no nonce, no used-signature mapping, and no check of the current `namespaceRemapping[pusher]` state before overwriting it. [1](#0-0) 

`revokePusher` clears the mapping to `address(0)`: [2](#0-1) 

But `allowPushers` unconditionally overwrites it with `msg.sender` on line 209 without checking whether the pusher had previously revoked: [3](#0-2) 

### Impact Explanation
After the creator replays the signature:

- `namespaceRemapping[pusher] == creator` is restored silently.
- The pusher, believing they have revoked, continues pushing data. Per the `fallback` path, that data is routed into the **creator's namespace** (not the pusher's own), because `namespaceRemapping[msg.sender]` is non-zero again. [4](#0-3) 

The creator's namespace feeds are consumed by `AnchoredPriceProvider` / pool swap pricing. A pusher who revoked because they detected a problem (stale data, wrong feed, compromise) can have their data silently re-injected into the creator's live price namespace, producing bad quotes that reach pool swaps.

### Likelihood Explanation
- The creator already holds the signed consent bytes (they used it in the first `allowPushers` call).
- No privileged role is needed — `allowPushers` is fully public.
- The window is bounded by the deadline, but deadlines are typically set days in the future (as shown in tests: `block.timestamp + 1 days`).
- The pusher has no on-chain way to invalidate the old signature short of waiting for the deadline to expire.

### Recommendation
Track consumed consents. Add a `mapping(bytes32 => bool) public usedConsents` keyed on `keccak256(abi.encode(chainid, oracle, deadline, pusher, creator))` and revert if the hash has already been used. Alternatively, add a per-pusher revocation nonce and include it in the signed message, so any revocation implicitly invalidates all prior signatures.

### Proof of Concept
```solidity
// Foundry test sketch
function testDeadlineReplayAfterRevoke() public {
    uint256 deadline = block.timestamp + 1 days;
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // Step 1: creator establishes delegation
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator);

    // Step 2: pusher self-revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0));

    // Step 3: creator replays the SAME signature before deadline
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs); // succeeds — no guard

    // REVOCATION_FINALITY_INVARIANT violated
    assertEq(oracle.namespaceRemapping(pusher), creator);
}
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-321)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }
```
