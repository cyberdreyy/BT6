### Title
Pusher Consent Signature Lacks Nonce, Allowing Creator to Replay Revoked Delegation Within Deadline Window — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary
`allowPushers` in `CompressedOracleV1` signs pusher consent over `(chainid, oracle, deadline, pusher, creator)` with no nonce. After a pusher calls `revokePusher()`, the creator can immediately replay the original signature — while `block.timestamp ≤ deadline` — to re-establish the delegation. This bypasses the pusher's revocation and allows a compromised pusher key to continue writing bad prices into the creator's namespace, which flows to pools via `AnchoredPriceProvider`.

### Finding Description

`allowPushers` verifies a pusher's EIP-191 consent signature and sets `namespaceRemapping[pusher] = msg.sender`:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

There is **no nonce** in the signed message. The `deadline` is the only time-bound element, checked via `_ensureDeadline(deadline)` (`block.timestamp <= deadline`).

When a pusher calls `revokePusher()`, it clears `namespaceRemapping[pusher] = address(0)`:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [2](#0-1) 

Because the signature carries no nonce, the creator can immediately call `allowPushers` again with the **same signature** (while `block.timestamp ≤ deadline`) to re-establish `namespaceRemapping[pusher] = creator`, fully bypassing the pusher's revocation.

The code comment explicitly acknowledges the deadline is required to prevent this exact scenario:

> "the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [3](#0-2) 

But the deadline only prevents re-establishment **after it expires**. Within the deadline window — which is commonly set days or weeks in the future — the creator can replay the signature to bypass the pusher's revocation at any time.

### Impact Explanation

If a pusher's key is compromised, the pusher calls `revokePusher()` to stop bad prices from flowing into the creator's namespace. If the creator (or an automated delegation-management system) replays the original consent signature, the delegation is re-established and the attacker continues pushing bad prices via the `fallback()` path:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
// ... writes into creator's namespace
``` [4](#0-3) 

These bad prices flow to pools via `AnchoredPriceProvider._readLeg()` → `oracle.price(feedId, pool)`:

```solidity
(mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);
``` [5](#0-4) 

If the attacker pushes a price that passes the staleness, spread, and `priceGuard` checks, it reaches the pool and causes bad-price execution: traders receive more than the oracle permits or LPs lose funds from mispriced swaps.

### Likelihood Explanation

Medium. Requires: (1) a compromised pusher key actively pushing bad prices, and (2) the creator to replay the original signature — either via an automated delegation-management system that re-establishes cleared delegations, or unknowingly. Deadlines are commonly set far in the future (e.g., 30 days), giving a large window for replay. The `revokePusher` path is the pusher's only self-service security escape hatch; its failure is not obvious to the creator.

### Recommendation

Add a per-pusher nonce to the signed message and increment it on each successful delegation, invalidating all prior signatures:

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

Alternatively, on `revokePusher()` increment a per-pusher nonce so any outstanding signatures for that pusher are invalidated immediately.

### Proof of Concept

```
1. Pusher signs consent for creator with deadline = block.timestamp + 30 days
2. Creator calls allowPushers(deadline, [pusher], [sig])
   → namespaceRemapping[pusher] = creator ✓
3. Pusher's key is compromised; attacker pushes bad price (e.g., ETH = $1)
   via fallback() → lands in creator's namespace
4. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0) ✓
5. Creator's automated system detects cleared mapping, replays:
   allowPushers(deadline, [pusher], [SAME sig])
   → namespaceRemapping[pusher] = creator again ✓ (deadline not yet expired)
6. Attacker continues pushing bad prices into creator's namespace
7. AnchoredPriceProvider.getBidAndAskPrice() returns bad bid/ask
   → pool swap executes at wrong price → LP loss / swap conservation failure
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-209)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L280-283)
```text
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```
