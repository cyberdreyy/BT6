### Title
Creator can silently re-establish pusher delegation after `revokePusher()` by replaying the original consent signature — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` in `CompressedOracleV1` contains no nonce, revocation flag, or "already-delegated" guard. After a pusher calls `revokePusher()` to clear `namespaceRemapping[pusher]`, the creator can immediately replay the original EIP-191 consent signature — unchanged — to re-write `namespaceRemapping[pusher] = creator`. The pusher's revocation is silently undone, and every subsequent fallback push lands in the creator's namespace instead of the pusher's own, without the pusher's knowledge or any new consent.

### Finding Description

`allowPushers` verifies the pusher's signature and unconditionally overwrites `namespaceRemapping[pusher]`:

```solidity
// CompressedOracle.sol lines 192-211
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));
    namespaceRemapping[pusher] = msg.sender;   // ← no check: already delegated? previously revoked?
    emit PusherAuthorized(pusher, msg.sender);
}
```

`revokePusher` clears the mapping:

```solidity
// CompressedOracle.sol lines 238-243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
```

The signed message is `(chainid, oracle, deadline, pusher, creator)`. It contains **no nonce and no revocation counter**. The code comment on line 189 explicitly acknowledges the risk: *"an undated signature could re-establish a delegation AFTER the pusher revoked it"* — but the deadline only bounds the attack window; it does not prevent re-establishment within that window. A creator who holds a signature with a deadline far in the future can call `allowPushers` again with the identical bytes immediately after the pusher's `revokePusher()` transaction, restoring `namespaceRemapping[pusher] = creator` in the same block.

This is the direct analog to the BkdLocker M-16 bug: just as `startBoost = 0` bypassed the initialization guard and allowed governance to re-set other parameters without new consent, a non-expired deadline bypasses the revocation and allows the creator to re-set the namespace mapping without new consent from the pusher.

### Impact Explanation

After the creator re-establishes delegation, every fallback push from the pusher lands in the creator's namespace rather than the pusher's own. Two concrete loss paths follow:

1. **Stale-price pool halt (broken