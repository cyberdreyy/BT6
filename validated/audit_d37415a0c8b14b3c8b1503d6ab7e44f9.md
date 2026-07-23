### Title
Pusher delegation signature replay bypasses `revokePusher`, silently re-routing price pushes into a creator's namespace — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies a pusher's EIP-191 consent signature and maps `namespaceRemapping[pusher] = creator`. No nonce or used-signature record is maintained. A creator who holds a still-valid (deadline not yet expired) consent signature can call `allowPushers` a second time after the pusher has called `revokePusher`, silently re-establishing the delegation the pusher explicitly cancelled. The pusher's subsequent pushes then land in the creator's namespace rather than their own (or a different creator's namespace they re-delegated to), feeding the creator's pool with prices the pusher never intended to publish there.

---

### Finding Description

`allowPushers` signs over `(chainid, oracle, deadline, pusher, creator)` and enforces only two guards: the deadline must be in the future and the recovered signer must equal the pusher. [1](#0-0) 

There is no nonce, no consumed-signature set, and no check that `namespaceRemapping[pusher]` is currently `address(0)` before writing. The code comment acknowledges the replay risk and presents the deadline as the mitigation: [2](#0-1) 

However, the deadline only prevents replay *after* it expires. Within the deadline window, the creator can call `allowPushers` with the identical signature any number of times, including after the pusher has self-revoked: [3](#0-2) 

`revokePusher` sets `namespaceRemapping[pusher] = address(0)`. The creator immediately overwrites that zero with the old signature, restoring the mapping. The pusher has no on-chain way to detect or prevent this.

The slot write path in `fallback` resolves the namespace from `namespaceRemapping[msg.sender]` at call time: [4](#0-3) 

So every push the pusher makes after the covert re-delegation lands in the creator's namespace, not the pusher's own.

---

### Impact Explanation

**Bad-price execution reaching a pool swap.** The attack path:

1. Pusher signs consent for Creator A with `deadline = now + 365 days`.
2. Creator A calls `allowPushers` → `namespaceRemapping[pusher] = creatorA`.
3. Pusher calls `revokePusher` → mapping cleared.
4. Pusher re-delegates to Creator B (signs new consent, Creator B calls `allowPushers`) → `namespaceRemapping[pusher] = creatorB`. Pusher now pushes ETH/USD prices into Creator B's namespace.
5. Creator A replays the original signature (deadline still valid) → `namespaceRemapping[pusher] = creatorA`, silently overwriting Creator B's delegation.
6. Pusher's ETH/USD pushes now land in Creator A's namespace (e.g. `feedIdOf(creatorA, slotIndex, positionIndex)`).
7. Creator A's pool, whose `AnchoredPriceProvider` reads `feedIdOf(creatorA, slotIndex, positionIndex)`, now receives ETH/USD prices on what is configured as, say, a BTC/USD feed.
8. `_readLeg` in `AnchoredPriceProvider` passes the staleness and guard checks (the data is fresh and within any configured price guard), and `_computeBidAsk` produces a bid/ask from the wrong mid price. [5](#0-4) 

Creator B's pool simultaneously loses its price feed (pusher's data no longer arrives there), causing `_isStale` to fire and halting swaps — a secondary DoS.

---

### Likelihood Explanation

- Any creator who previously obtained a pusher's consent signature retains the ability to replay it for the full lifetime of the deadline. Deadlines are chosen by the creator, not the pusher, and nothing prevents a creator from specifying `deadline = block.timestamp + 365 days`.
- The pusher has no on-chain visibility that their revocation was overturned; `namespaceRemapping` is a public mapping but the pusher must actively monitor it.
- No privileged role is required: any namespace creator (permissionless) can execute this.
- The pusher's continued pushing (thinking they are in their own or Creator B's namespace) is the normal operational assumption — the attack exploits that assumption silently.

---

### Recommendation

Add a per-pusher revocation nonce to the signature domain. Increment it inside `revokePusher` (and `removePushers`). Include the current nonce in the signed message so that any signature produced before the last revocation is permanently invalidated:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // <-- add nonce
    ))
);

// In revokePusher / removePushers:
pusherNonce[msg.sender]++;   // invalidates all prior signatures
```

This makes every revocation a cryptographic epoch boundary: signatures from before the revocation cannot be replayed.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creatorA (deadline = now + 365 days).
//    creatorA calls allowPushers → namespaceRemapping[pusher] = creatorA.

// 2. Pusher calls revokePusher → namespaceRemapping[pusher] = address(0).
//    Pusher believes they are free.

// 3. creatorA calls allowPushers again with the IDENTICAL signature.
//    _ensureDeadline passes (deadline still in the future).
//    ECDSA.recover returns pusher — same hash, same sig.
//    namespaceRemapping[pusher] = creatorA  ← revocation silently undone.

// 4. Pusher pushes ETH/USD slot word via fallback().
//    fallback resolves creator = namespaceRemapping[pusher] = creatorA.
//    Data lands in creatorA's namespace at the pushed slotId/position.

// 5. creatorA's pool reads feedIdOf(creatorA, slotId, position) via
//    AnchoredPriceProvider.getBidAndAskPrice() → _readLeg → oracle.price().
//    The pool receives ETH/USD mid as if it were the configured asset price.
//    _computeBidAsk produces a wrong bid/ask that passes all guards.
//    Swap executes at the wrong oracle-anchored price → bad-price execution.
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-321)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-295)
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
    }
```
