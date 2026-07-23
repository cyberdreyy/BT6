### Title
Signature Replay in `allowPushers` Lets Creator Re-Establish a Pusher's Self-Revoked Delegation Within the Deadline Window - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

### Summary

`allowPushers` in `CompressedOracleV1` has no nonce and no used-signature tracking. The signed consent is bound only to `(chainid, address(this), deadline, pusher, creator)`. Within the deadline window the creator can replay the exact same bytes to re-establish `namespaceRemapping[pusher] = creator` immediately after the pusher has self-revoked via `revokePusher()`, making the revocation ineffective until the deadline expires.

### Finding Description

The `allowPushers` function verifies an EIP-191 signature whose preimage is:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The only time-bounding guard is `_ensureDeadline(deadline)`, which checks `block.timestamp <= deadline`: [2](#0-1) 

There is no nonce, no per-signature consumed flag, and no state that changes between the first and second call with the same `(deadline, pusher, creator)` tuple. After the creator calls `allowPushers` once, the pusher can call `revokePusher()`: [3](#0-2) 

This sets `namespaceRemapping[pusher] = address(0)`. But the creator still holds the original signature bytes and can immediately call `allowPushers` again with the identical arguments, passing the same signature, which recovers to the same pusher address and writes `namespaceRemapping[pusher] = creator` again. The pusher's revocation is silently undone.

The code comment on line 189–191 explicitly acknowledges the replay risk ("an undated signature could re-establish a delegation AFTER the pusher revoked it") and claims the deadline mitigates it. The deadline prevents replay only **after** it expires; it does nothing to prevent replay **within** the window. [4](#0-3) 

### Impact Explanation

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

If the pusher's key is compromised and the pusher calls `revokePusher()` to stop bad data from reaching the creator's namespace, the creator (or anyone who observed the original `allowPushers` calldata on-chain) can replay the signature to re-establish the delegation. The compromised pusher's automated system continues writing bad slot values into the creator's namespace. Those values are decoded by `getOracleData` / `price` and consumed by `AnchoredPriceProvider` → `MetricOmmPool.swap`, producing bad-price execution against live LP positions.

### Likelihood Explanation

- The original `allowPushers` calldata (including the pusher's signature) is permanently visible on-chain.
- The creator needs only to re-submit the same transaction before the deadline.
- Deadlines are typically set days in the future (the test suite uses `block.timestamp + 1 days`), giving a wide replay window.
- The pusher has no on-chain mechanism to invalidate the signature before the deadline expires. [6](#0-5) 

### Recommendation

Add a per-pusher nonce or a `mapping(bytes32 => bool) usedSignatures` that marks each `(pusher, creator, deadline)` digest as consumed on first use. Alternatively, invalidate all outstanding signatures for a pusher when `revokePusher` or `removePushers` is called by incrementing a per-pusher nonce that is included in the signed preimage.

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = now + 1 day
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher self-revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Creator replays the SAME signature — no nonce, no consumed flag
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs); // succeeds, no revert
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation re-established

// 5. Pusher's fallback pushes now land in creator's namespace again
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw = _packRaw(BAD_PRICE, 5, 0);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok);
// Bad price is now live in creator's namespace, readable by AnchoredPriceProvider
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, U64x32.decode(uint32(raw >> 16)));
```

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

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L340-342)
```text
        uint256 deadline = block.timestamp + 1 days;
        _allowPusher(deadline);
        assertEq(oracle.namespaceRemapping(pusher), creator, "pusher should map to creator");
```
