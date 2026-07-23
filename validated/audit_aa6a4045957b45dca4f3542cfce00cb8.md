### Title
`purgeStateGuardRole` Does Not Clear `pendingStateGuard`, Allowing a Stale Pending Guard to Seize Feed Control After Purge — (File: `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

`purgeStateGuardRole` deletes `stateGuard[feedId]` but leaves `pendingStateGuard[feedId]` intact. After the current guard purges themselves (intending to return authority to ADMIN), the previously nominated pending guard can still call `acceptStateGuardRole` and seize the feed's price-guard configuration. This is the direct analog of the "removeAgent with positive debt" class: an entity is removed without checking its outstanding pending state, leaving the system in an inconsistent and exploitable condition.

---

### Finding Description

`purgeStateGuardRole` only clears `stateGuard[feedId]`:

```solidity
// smart-contracts-poc/contracts/oracles/providers/OracleBase.sol
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];          // ← only this is cleared
    // pendingStateGuard[feedId] is NOT touched
    emit StateGuardDeleted(feedId);
}
```

`acceptStateGuardRole` only checks `pendingStateGuard`, with no requirement that `stateGuard` is currently set:

```solidity
function acceptStateGuardRole(bytes32 feedId) external {
    require(pendingStateGuard[feedId] == msg.sender, InvalidGuard(msg.sender));
    delete pendingStateGuard[feedId];
    stateGuard[feedId] = msg.sender;
    emit StateGuardUpdated(feedId, msg.sender);
}
```

Because `purgeStateGuardRole` never clears `pendingStateGuard[feedId]`, the pending guard's claim survives the purge and can be accepted at any time afterward.

The `checkRole` modifier falls back to ADMIN when `stateGuard[feedId] == address(0)`, so after the purge ADMIN believes it has regained sole authority — but the pending guard can still call `acceptStateGuardRole` and install itself as the new guard before ADMIN acts.

---

### Impact Explanation

Once the pending guard becomes `stateGuard[feedId]`, it controls `setPriceGuard` for that feed. In the price provider the guard is read as:

```solidity
(uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
guardMax = guardMax == 0 ? type(uint128).max : guardMax;
if (price < guardMin || price > guardMax) {
    return (0, type(uint128).max);
}
```

Setting `priceGuard[feedId] = PriceGuard({min: 0, max: 0})` makes `guardMax` resolve to `type(uint128).max`, effectively disabling the price guard entirely. Any oracle price — including stale, inverted, or unbounded values — then passes through to pool swaps, satisfying the "bad-price execution" impact gate.

---

### Likelihood Explanation

The trigger is:
1. A guard is set for a feed (normal operational setup).
2. The guard nominates a pending guard via `setStateGuardRole` (routine role-transfer flow).
3. The guard then calls `purgeStateGuardRole` to return authority to ADMIN (e.g., after a key rotation or incident response).
4. The pending guard calls `acceptStateGuardRole` before ADMIN clears it via `purgePendingStateGuardRole`.

Step 3 is the non-malicious path: a guard legitimately trying to relinquish control does not expect the pending transfer to survive the purge. The window between steps 3 and 4 is unbounded — there is no timelock or expiry on `pendingStateGuard`. Likelihood: **Medium**.

---

### Recommendation

Clear `pendingStateGuard[feedId]` inside `purgeStateGuardRole`:

```solidity
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];
    delete pendingStateGuard[feedId]; // ← add this
    emit StateGuardDeleted(feedId);
}
```

---

### Proof of Concept

```
1. Guard A is stateGuard[feedId].
2. Guard A calls setStateGuardRole(feedId, pendingGuard)
   → pendingStateGuard[feedId] = pendingGuard
3. Guard A calls purgeStateGuardRole(feedId)
   → stateGuard[feedId] = address(0)
   → pendingStateGuard[feedId] = pendingGuard  ← still set
4. ADMIN observes stateGuard[feedId] == address(0) and believes it has full authority.
5. pendingGuard calls acceptStateGuardRole(feedId)
   → stateGuard[feedId] = pendingGuard         ← guard role resurrected
6. pendingGuard calls setPriceGuard(feedId, 0, 0)
   → priceGuard[feedId] = {min:0, max:0}
7. In the price provider: guardMax == 0 → type(uint128).max
   → price guard is disabled; any oracle price reaches pool swaps.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L65-74)
```text
    modifier checkRole(bytes32 feedId) {
        address _guard = stateGuard[feedId];
        if (_guard != address(0)) {
            require(_guard == msg.sender, InvalidGuard(msg.sender));
        } else {
            _checkRole(ADMIN_ROLE);
        }

        _;
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L111-118)
```text
    function acceptStateGuardRole(bytes32 feedId) external {
        require(pendingStateGuard[feedId] == msg.sender, InvalidGuard(msg.sender));

        delete pendingStateGuard[feedId];
        stateGuard[feedId] = msg.sender;

        emit StateGuardUpdated(feedId, msg.sender);
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L120-124)
```text
    function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
        delete stateGuard[feedId];

        emit StateGuardDeleted(feedId);
    }
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProvider.sol (L201-206)
```text
        // 3. Price guard check
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (price < guardMin || price > guardMax) {
            return (0, type(uint128).max);
        }
```
