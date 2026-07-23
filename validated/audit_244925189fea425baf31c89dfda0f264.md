### Title
Creator Can Forcibly Re-Delegate a Revoked Pusher via Signature Replay, Starving Pusher's Own Namespace Feeds — (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

---

### Summary

`allowPushers` in `CompressedOracle.sol` does not track consumed signatures. After a pusher self-revokes via `revokePusher`, the creator retains the original EIP-191 consent and can replay it — as many times as needed — to re-establish the delegation, as long as the deadline embedded in the signature has not yet expired. Because a delegated pusher's `fallback` writes are unconditionally redirected to the creator's namespace, the pusher cannot update their own namespace while re-delegated. Any pool whose price provider reads from the pusher's own namespace (feedIdOf(pusher, …)) will receive stale data, causing its staleness guard to return `(0, type(uint128).max)` and halt all swaps.

---

### Finding Description

`allowPushers` verifies the pusher's EIP-191 signature over `(chainid, oracle, deadline, pusher, creator)` and writes `namespaceRemapping[pusher] = msg.sender`:

```solidity
// CompressedOracle.sol lines 192–211
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.to