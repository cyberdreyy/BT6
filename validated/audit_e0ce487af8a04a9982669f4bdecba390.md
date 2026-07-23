### Title
Revoked Pusher Delegation Can Be Silently Re-Established by Replaying the Original Signed Consent — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` contains no nonce and no check that the pusher is currently undelegated. A creator who holds a valid (not-yet-expired) signed consent can call `allowPushers` repeatedly — including after the pusher has explicitly called `revokePusher()` — to silently re-establish the delegation. The pusher's revocation is therefore ineffective for the entire remaining lifetime of the deadline, and any pushes the pusher makes to their own namespace are silently redirected into the creator's namespace instead, leaving the pusher's own feeds stale.

---

### Finding Description

`allowPushers` signs over `(chainid, address(this), deadline, pusher, msg.sender)`: [1](#0-0) 

There is no nonce in the signed message and no on-chain check that `namespaceRemapping[pusher]` is currently zero before writing. After a pusher calls `revokePusher()`: [2](#0-1) 

…the creator can immediately replay the original signed consent (same `deadline`, same `pusher`, same `msg.sender`) and overwrite `namespaceRemapping[pusher]` back to themselves. The code comment on line 189–191 acknowledges that the deadline is meant to prevent this, but the deadline only prevents *indefinite* replay of undated signatures — a dated signature with a future deadline is equally replayable within its window. [3](#0-2) 

The `fallback` push path resolves the namespace at call time: [4](#0-3) 

So every push the pusher makes after revocation — intending to write to their own namespace — is silently redirected into the creator's namespace as long as the re-delegation stands.

---

### Impact Explanation

Pools that are configured to read from the pusher's own namespace (feeds derived via `feedIdOf(pusher, slotIndex, positionIndex)`) receive no new updates once the pusher's writes are hijacked into the creator's namespace. Those feeds become stale. Any pool swap that consumes a stale price from the pusher's namespace executes at a bad (outdated) bid/ask, satisfying the "bad-price execution: stale quote reaches a pool swap" impact criterion. [5](#0-4) 

---

### Likelihood Explanation

The trigger requires a creator who is adversarial toward a pusher who has revoked. The pusher cannot prevent the replay without waiting for the deadline to expire; if the deadline was set far in the future (e.g., 30 days or more, which is normal for operational key-rotation flows), the pusher's own namespace feeds are stale for that entire window. The creator needs only to call `allowPushers` once after each `revokePusher()` call — a single transaction.

---

### Recommendation

Add a nonce to the signed consent so each consent can only be consumed once:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]++
    ))
);
```

Alternatively, check that the pusher is not currently delegated before accepting a new consent:

```solidity
require(
    namespaceRemapping[pusher] == address(0),
    "pusher already delegated; must revoke first"
);
```

Either fix ensures that a revocation cannot be silently undone by replaying a previously signed message.

---

### Proof of Concept

```
1. Pusher signs consent: keccak256(chainid, oracle, deadline=T+30days, pusher, creatorA)
2. CreatorA calls allowPushers(T+30days, [pusher], [sig])
   → namespaceRemapping[pusher] = creatorA
3. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)
4. Pusher begins pushing to their own namespace (feedIdOf(pusher, slot, pos))
5. CreatorA calls allowPushers(T+30days, [pusher], [sig])  ← SAME sig, deadline still valid
   → namespaceRemapping[pusher] = creatorA  (revocation undone)
6. Pusher's fallback pushes now land in creatorA's namespace
7. feedIdOf(pusher, slot, pos) receives no new data → timestampMs stale
8. Any pool reading feedIdOf(pusher, slot, pos) executes swaps at the stale price
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
