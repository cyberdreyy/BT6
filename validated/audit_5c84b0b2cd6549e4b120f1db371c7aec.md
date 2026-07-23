### Title
Pusher self-revocation can be silently bypassed by creator replaying the original consent signature in `allowPushers` — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` does not check whether a pusher has previously self-revoked via `revokePusher()`. A creator who holds a still-valid (non-expired) consent signature can call `allowPushers` again after the pusher's revocation, silently re-establishing the delegation and allowing bad prices to continue flowing into pools through the creator's namespace.

---

### Finding Description

The `CompressedOracleV1` contract allows a creator to delegate price-push authority to external wallets via `allowPushers`. The pusher signs a consent message that binds `chainid`, `address(this)`, `deadline`, `pusher`, and `creator`. A pusher can self-revoke via `revokePusher()`, which clears `namespaceRemapping[pusher]` to `address(0)`.

The code comment on `allowPushers` explicitly acknowledges the risk:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."*

However, the deadline does **not** prevent re-establishment within the deadline window. `allowPushers` performs no check for a prior revocation:

```solidity
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);          // only checks expiry
    ...
    require(pusher == ECDSA.recover(hash, signatures[i]));  // no revocation check
    namespaceRemapping[pusher] = msg.sender;                // overwrites address(0) set by revokePusher
    emit PusherAuthorized(pusher, msg.sender);
}
``` [1](#0-0) 

After `revokePusher()` sets `namespaceRemapping[pusher] = address(0)`: [2](#0-1) 

…the creator can immediately call `allowPushers` again with the **same original signature** (deadline still valid) to restore `namespaceRemapping[pusher] = creator`. The pusher's self-revocation is completely undone without any fresh consent from the pusher.

The `fallback()` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [3](#0-2) 

So after the creator replays the signature, every subsequent push from the pusher's address lands in the creator's namespace again, exactly as before revocation.

---

### Impact Explanation

The creator's namespace feeds `CompressedOracleV1.price(feedId, pool)`, which is consumed by `AnchoredPriceProvider._readLeg()`: [4](#0-3) 

…which in turn drives `MetricOmmPool` swap pricing. If a pusher's key is compromised and the pusher calls `revokePusher()` to stop bad prices from entering the creator's namespace, the creator (by mistake or malicious action) can replay the original consent signature to re-establish the delegation. The attacker holding the compromised pusher key then continues pushing bad/stale prices into the creator's namespace, which propagate through `AnchoredPriceProvider` into live pool swaps — a direct bad-price execution impact.

---

### Likelihood Explanation

The scenario requires:
1. A pusher who signed a consent with a long deadline (common for operational convenience).
2. The pusher calling `revokePusher()` (e.g., after key compromise or disagreement).
3. The creator calling `allowPushers` again with the same signature before the deadline expires — either by mistake ("restoring" the delegation) or maliciously.

The comment in the code acknowledges this exact risk but incorrectly claims the deadline mitigates it. Deadlines are routinely set days-to-months in the future, leaving a large replay window. Likelihood: **Medium**.

---

### Recommendation

Track a per-pusher revocation nonce or a `revokedPushers` mapping. In `allowPushers`, reject any pusher whose current `namespaceRemapping` was explicitly cleared by a prior `revokePusher` call without a fresh opt-in:

```solidity
mapping(address => bool) public selfRevoked;

function revokePusher() external {
    ...
    selfRevoked[msg.sender] = true;   // record explicit self-revocation
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}

function allowPushers(...) external {
    ...
    require(!selfRevoked[pusher], "pusher self-revoked; fresh consent required");
    namespaceRemapping[pusher] = msg.sender;
    ...
}
```

Alternatively, include a monotonic nonce in the signed consent so that each delegation requires a new signature that cannot be replayed after revocation.

---

### Proof of Concept

```
1. creator calls allowPushers(deadline=T+365days, [pusher], [sig])
   → namespaceRemapping[pusher] = creator

2. pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)
   → pusher believes their data no longer flows into creator's namespace

3. creator calls allowPushers(deadline=T+365days, [pusher], [sig])  // SAME sig, still valid
   → namespaceRemapping[pusher] = creator  // revocation silently undone

4. attacker (holding compromised pusher key) calls fallback() with bad price data
   → creator = namespaceRemapping[attacker_key] = creator  (not address(0))
   → bad price written into creator's namespace slot

5. AnchoredPriceProvider._readLeg(feedId) reads creator's slot via CompressedOracleV1.price()
   → bad mid/spread propagates to MetricOmmPool swap
   → traders receive wrong execution price
```

### Citations

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
