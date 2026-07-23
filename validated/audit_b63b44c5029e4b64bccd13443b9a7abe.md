### Title
Pusher Consent Signature Replay Within Deadline Window Allows Creator to Re-Establish Revoked Delegation and Hijack Feed Namespace — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` binds a pusher's EIP-191 consent to `(chainid, address(this), deadline, pusher, creator)` but carries no nonce and performs no used-signature tracking. Any creator who holds a valid, unexpired signature can call `allowPushers` an unlimited number of times within the deadline window. This means a pusher's `revokePusher()` call is silently undone the moment the creator replays the same signature, and a pusher's subsequent re-delegation to a different creator can be overridden the same way. The result is that the pusher's fallback writes land in the wrong namespace, feeding bad or mismatched prices into any pool that reads those feeds.

### Finding Description

`allowPushers` checks only that the deadline has not expired and that the recovered signer matches the pusher address:

```solidity
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));
    namespaceRemapping[pusher] = msg.sender;   // ← unconditional overwrite, no nonce
    emit PusherAuthorized(pusher, msg.sender);
}
``` [1](#0-0) 

There is no nonce, no per-signature consumed flag, and no check for whether the pusher is already delegated to a different creator. The code comment acknowledges the deadline is required to prevent re-establishment *after* the deadline expires, but it does not prevent re-establishment *within* the deadline window:

```
/// The deadline is likewise required: the signed consent carries no timestamp of
/// its own, so an undated signature could re-establish a delegation AFTER the
/// pusher revoked it.
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

But `allowPushers` immediately overwrites it back to `msg.sender` (creator A) on replay, with no state check. The fallback push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every subsequent push by the pusher lands in creator A's namespace instead of the intended namespace.

### Impact Explanation

The `fallback` push path writes packed slot words (price, spread0, spread1, timestamp) directly into the namespace resolved from `namespaceRemapping[msg.sender]`. If creator A hijacks the pusher's namespace back after the pusher has re-delegated to creator B, the pusher's data (e.g., ETH/USD) lands in creator A's namespace slots. Any pool configured to read `feedIdOf(creator_A, slotX, posY)` via `AnchoredPriceProvider` or `ProtectedPriceProvider` now receives mismatched price data. The `_readLeg` / `_computeBidAsk` path in `AnchoredPriceProvider` will consume this wrong mid price and produce a bad bid/ask that reaches the pool's swap math — a direct bad-price execution loss for traders. Creator B's feeds simultaneously go stale (no updates arrive), halting those pools. [5](#0-4) 

### Likelihood Explanation

The attacker is a creator who previously held legitimate delegation — a semi-trusted but unprivileged role. The replay requires only that the original deadline has not yet expired, which is a normal operational window (e.g., 1 day). No additional consent from the pusher is needed. The attack is a single public transaction (`allowPushers` with the already-public signature). The scenario where a pusher revokes and re-delegates to a different creator is a normal operational event (key rotation, provider switch), making the trigger realistic.

### Recommendation

Track consumed signatures with a per-pusher nonce or a `usedSignatures` mapping, and invalidate the signature on first use:

```solidity
mapping(bytes32 => bool) private _usedDelegationSigs;

function allowPushers(...) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = ...; // existing hash
    require(!_usedDelegationSigs[hash], "signature already used");
    require(pusher == ECDSA.recover(hash, signatures[i]));
    _usedDelegationSigs[hash] = true;
    namespaceRemapping[pusher] = msg.sender;
}
```

Alternatively, include a per-pusher nonce in the signed payload and increment it on each successful delegation, so any prior signature is invalidated the moment the pusher revokes and re-signs for a new creator.

### Proof of Concept

```
T=0:  Pusher P signs consent: hash(chainid, oracle, deadline=T+1day, P, creatorA)
T=1:  creatorA calls allowPushers(deadline, [P], [sig]) → namespaceRemapping[P] = creatorA
T=2:  P calls revokePusher()                           → namespaceRemapping[P] = address(0)
T=3:  P signs new consent for creatorB (deadline=T+2day)
T=4:  creatorB calls allowPushers(...)                 → namespaceRemapping[P] = creatorB
T=5:  creatorA replays original sig (still before T+1day):
        allowPushers(deadline=T+1day, [P], [original_sig])
        → namespaceRemapping[P] = creatorA  ← overrides creatorB
T=6:  P pushes ETH/USD slot data (intended for creatorB's pool)
        → fallback resolves namespaceRemapping[P] = creatorA
        → data lands in feedIdOf(creatorA, slotX, posY)
T=7:  creatorA's pool calls getBidAndAskPrice()
        → AnchoredPriceProvider reads feedIdOf(creatorA, slotX, posY)
        → receives ETH/USD price in a BTC/USD pool
        → bad bid/ask reaches swap math → bad-price execution
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
