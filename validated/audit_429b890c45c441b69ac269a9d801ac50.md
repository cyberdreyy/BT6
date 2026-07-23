### Title
Pusher Delegation Signature Replay Bypasses `revokePusher()` — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` accepts a pusher's EIP-191 consent signature and sets `namespaceRemapping[pusher] = creator`. There is no nonce or used-signature tracking. A creator can replay the same valid signature any number of times before the deadline expires, re-establishing delegation immediately after the pusher calls `revokePusher()`. The pusher's subsequent fallback pushes — which they believe are landing in their own namespace — are silently redirected into the creator's namespace, potentially feeding prices intended for a different feed/asset-pair into a live pool.

### Finding Description

`allowPushers` signs over `(chainid, oracle, deadline, pusher, creator)` and enforces only that `block.timestamp <= deadline`: [1](#0-0) 

`_ensureDeadline` is a single timestamp comparison with no consumed-signature registry: [2](#0-1) 

`revokePusher` clears the mapping: [3](#0-2) 

But nothing marks the original signature as spent. The creator holds the pusher's bytes and can call `allowPushers` again with the identical `(deadline, pusher, signature)` tuple, restoring `namespaceRemapping[pusher] = creator` in the same block as the revocation.

The `fallback` push path resolves the namespace at call time: [4](#0-3) 

So every push the pusher makes after their revocation — believing it targets their own namespace — is silently written into the creator's namespace instead.

The code's own NatDoc acknowledges the partial risk but presents the deadline as the complete fix: [5](#0-4) 

The deadline limits the *window* but does not prevent replay *within* that window. A 30-day deadline leaves a 30-day replay surface after the pusher revokes.

### Impact Explanation

After the creator replays the signature, the pusher's fallback pushes land in the creator's namespace. If the pusher has begun pushing data for a different asset pair (their own namespace, a different creator), those slot words — with a different price scale, spread, or asset — overwrite the creator's live feed slots. The `AnchoredPriceProvider` reads those slots through `offchainOracle.price(feedId, pool)`: [6](#0-5) 

A corrupted mid price or spread passes the staleness and zero-price guards (the timestamp is fresh, the price is non-zero) and reaches `_computeBidAsk`, producing an incorrect bid/ask that the pool executes swaps against. Traders receive more output than the correct oracle permits, or the pool receives less input than owed — direct loss of pool assets.

### Likelihood Explanation

- The creator must hold the pusher's original signature bytes (they received them during `allowPushers`).
- The pusher must be actively pushing to their own namespace after revocation (common: a pusher who revokes typically starts a new feed).
- The deadline window is configurable and can be days or weeks.
- No privileged role is required beyond the creator's normal position; the replay call is a standard public transaction.

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedConsents` keyed on `keccak256(abi.encode(chainid, oracle, deadline, pusher, creator))`. Mark the hash used on first acceptance and revert on any subsequent call with the same hash. This is the standard EIP-2612 / permit pattern and eliminates replay entirely without changing the delegation UX.

Alternatively, include a per-pusher nonce in the signed message and increment it on every successful `allowPushers` or `revokePusher`, so any previously issued signature is immediately invalidated by the revocation.

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = now + 30 days
bytes memory sig = pusher.sign(keccak256(abi.encode(
    block.chainid, oracle, deadline, pusherAddr, creatorAddr)));

// 2. Creator establishes delegation
oracle.allowPushers(deadline, [pusherAddr], [sig]);
// namespaceRemapping[pusher] == creator ✓

// 3. Pusher revokes
vm.prank(pusherAddr);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0) ✓

// 4. Creator replays the SAME signature — no nonce, no used-hash check
oracle.allowPushers(deadline, [pusherAddr], [sig]);
// namespaceRemapping[pusher] == creator again ✓

// 5. Pusher pushes what they believe is their own feed (e.g. ETH/BTC at price X)
vm.prank(pusherAddr);
(bool ok,) = address(oracle).call(slotWordForOwnFeed);
// Lands in CREATOR namespace, not pusher's own namespace
// Creator's pool (e.g. BTC/USD) now has ETH/BTC price in its feed slot
// Pool swap executes at wrong price → loss of pool assets
``` [7](#0-6)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L280-283)
```text
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```
