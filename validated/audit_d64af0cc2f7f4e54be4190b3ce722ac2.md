### Title
Creator Can Replay Pusher Consent Signature to Permanently Re-Establish Revoked Delegation Within Deadline Window — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` in `CompressedOracleV1` contains no nonce or used-signature tracking. A creator who holds a pusher's signed consent can replay that same signature an unlimited number of times before the deadline expires, re-establishing `namespaceRemapping[pusher] = creator` immediately after every `revokePusher()` call. The pusher cannot permanently exit the delegation until the deadline timestamp passes. Any pool whose `feedId` encodes the pusher's address as creator will receive stale prices for the entire deadline window, enabling bad-price execution.

---

### Finding Description

`allowPushers` verifies an EIP-191 signature that covers `(block.chainid, address(this), deadline, pusher, msg.sender)`:

```solidity
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));
    namespaceRemapping[pusher] = msg.sender;   // ← overwrites any prior revocation
    emit PusherAuthorized(pusher, msg.sender);
}
``` [1](#0-0) 

`_ensureDeadline` only checks `block.timestamp <= deadline`:

```solidity
function _ensureDeadline(uint256 deadline) internal view virtual {
    require(block.timestamp <= deadline, DeadlineExceeded());
}
``` [2](#0-1) 

There is no nonce, no used-signature set, and no one-time-use flag. The same `(deadline, pusher, creator)` tuple produces the same hash every time. The creator can call `allowPushers` with the identical signature bytes on every block until the deadline expires.

`revokePusher` clears the mapping:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [3](#0-2) 

But the creator can immediately call `allowPushers` again with the same signature to restore `namespaceRemapping[pusher] = creator`. The pusher's `revokePusher` is effectively a no-op for the entire deadline window.

The code comment on `allowPushers` acknowledges the risk but only addresses the case of an *undated* signature:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [4](#0-3) 

The deadline prevents replay *after* expiry, but does nothing to prevent replay *within* the deadline window.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

So every push the pusher makes while the delegation is active lands in the creator's namespace, not the pusher's own namespace. Any pool whose `feedId` encodes the pusher's address (i.e., `feedIdOf(pusher, slot, pos)`) receives no updates and its price goes stale.

The `feedId` encodes the creator address directly:

```solidity
function feedIdOf(address creator, uint8 slotIndex, uint8 positionIndex) public view returns (bytes32) {
    return bytes32(
        uint256(uint160(creator)) << 96 | block.chainid << 16 | uint256(slotIndex) << 8 | positionIndex
    );
}
``` [6](#0-5) 

A pool configured with `feedIdOf(pusher, slot, pos)` reads from the pusher's namespace. If the pusher's pushes are being hijacked to the creator's namespace, the pool's feed timestamp stops advancing. The `AnchoredPriceProvider` and `ProtectedPriceProvider` both enforce staleness:

```solidity
function _isStale(uint256 refTime, uint256 nowTs, uint256 maxDelta) internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) return true;
    return (nowTs - refTime) > maxDelta;
}
``` [7](#0-6) 

Once the feed goes stale, `getBidAndAskPrice()` reverts `FeedStalled`, halting all swaps in the pool.

---

### Impact Explanation

A pool whose `feedId` encodes the pusher's address as creator is completely halted for the entire deadline window. All swaps revert `FeedStalled`. LP assets are locked in the pool — no swaps, no liquidity removal at fair price — for up to the full deadline duration (which can be set to any future timestamp the pusher agreed to at signing time, e.g. 30 days). This is a broken core pool functionality causing loss of funds / unusable withdraw/swap flows.

---

### Likelihood Explanation

- Pushers routinely sign consents with long deadlines for operational convenience (e.g., 30 days).
- The creator needs only to call `allowPushers` once per block after each `revokePusher` — a trivial on-chain action costing only gas.
- The pusher has no on-chain mechanism to invalidate the old signature; the only escape is waiting for the deadline to expire.
- Any dispute between a pusher and a creator (e.g., a terminated business relationship) triggers this scenario.

Likelihood: **Medium** (requires a prior signed consent with a non-expired deadline and an adversarial creator).

---

### Recommendation

Add a per-pusher nonce or a used-signature bitmap to `allowPushers`. The simplest fix is a `mapping(address => uint256) public pusherNonce` that the pusher increments to invalidate all prior signatures:

```solidity
mapping(address => uint256) public pusherNonce;

// Pusher signs: keccak256(abi.encode(block.chainid, address(this), deadline, pusher, creator, pusherNonce[pusher]))
// After revokePusher(), also increment pusherNonce[msg.sender] so all prior signatures are dead.
function revokePusher() external {
    ...
    namespaceRemapping[msg.sender] = address(0);
    pusherNonce[msg.sender]++;          // ← invalidates all prior consent signatures
    emit PusherRevoked(msg.sender, creator);
}
```

Alternatively, track used `(pusher, deadline, creator)` tuples in a `mapping(bytes32 => bool) usedConsents` and mark them consumed on first use.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = block.timestamp + 30 days
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
);
bytes memory sig = sign(PUSHER_KEY, hash);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared

// 4. Creator immediately replays the SAME signature — no revert
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // re-established!

// 5. Pusher's pushes now land in creator's namespace, not pusher's own.
//    Any pool reading feedIdOf(pusher, slot, pos) receives no updates.
//    After MAX_REF_STALENESS seconds, AnchoredPriceProvider.getBidAndAskPrice()
//    reverts FeedStalled — all swaps in the pool are halted.
vm.warp(block.timestamp + MAX_REF_STALENESS + 1);
vm.expectRevert(AnchoredPriceProvider.FeedStalled.selector);
pool.getBidAndAskPrice();
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L49-53)
```text
    function feedIdOf(address creator, uint8 slotIndex, uint8 positionIndex) public view returns (bytes32) {
        return bytes32(
            uint256(uint160(creator)) << 96 | block.chainid << 16 | uint256(slotIndex) << 8 | positionIndex
        );
    }
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L222-230)
```text
    function _isStale(
        uint256 refTime,
        uint256 nowTs,
        uint256 maxDelta
    ) internal pure returns (bool) {
        if (refTime == 0) return true;
        if (refTime > nowTs) return true;
        return (nowTs - refTime) > maxDelta;
    }
```
