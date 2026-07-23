### Title
`purgeStateGuardRole` Does Not Clear `pendingStateGuard`, Allowing a Revoked Pending Guard to Seize Feed Price-Guard Control — (File: `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

`purgeStateGuardRole` deletes `stateGuard[feedId]` but leaves `pendingStateGuard[feedId]` intact. A pending guard that was designated before the purge can still call `acceptStateGuardRole` afterward and gain unauthorized control over the feed's `setPriceGuard` settings, bypassing the intended return of authority to ADMIN.

---

### Finding Description

`OracleBase` (providers) implements a two-step guard-transfer pattern:

1. Current guard calls `setStateGuardRole(feedId, newGuard)` → writes `pendingStateGuard[feedId] = newGuard`.
2. `newGuard` calls `acceptStateGuardRole(feedId)` → clears `pendingStateGuard` and sets `stateGuard[feedId] = newGuard`.

The current guard can also call `purgeStateGuardRole` to fully relinquish authority, returning the feed to ADMIN control:

```solidity
// smart-contracts-poc/contracts/oracles/providers/OracleBase.sol
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];          // ← only this is cleared
    emit StateGuardDeleted(feedId);
    // pendingStateGuard[feedId] is NOT cleared
}
``` [1](#0-0) 

Because `pendingStateGuard[feedId]` is never touched, any address that was previously set as a pending guard can still call `acceptStateGuardRole` after the purge and become the new `stateGuard`:

```solidity
function acceptStateGuardRole(bytes32 feedId) external {
    require(pendingStateGuard[feedId] == msg.sender, InvalidGuard(msg.sender));
    delete pendingStateGuard[feedId];
    stateGuard[feedId] = msg.sender;   // ← pending guard seizes control
    emit StateGuardUpdated(feedId, msg.sender);
}
``` [2](#0-1) 

This is the direct analog to the external bug: the validator (stateGuard) is removed from storage, but the pending work (pendingStateGuard) is not tracked or cancelled, allowing it to be completed later in an unintended way.

The `checkRole` modifier after the purge falls back to ADMIN:

```solidity
modifier checkRole(bytes32 feedId) {
    address _guard = stateGuard[feedId];
    if (_guard != address(0)) {
        require(_guard == msg.sender, InvalidGuard(msg.sender));
    } else {
        _checkRole(ADMIN_ROLE);   // ← intended post-purge authority
    }
    _;
}
``` [3](#0-2) 

But the pending guard bypasses this entirely by calling `acceptStateGuardRole` directly, which only checks `pendingStateGuard[feedId] == msg.sender` — a condition that remains true after the purge.

---

### Impact Explanation

Once the pending guard seizes `stateGuard`, they control `setPriceGuard` for the feed:

```solidity
function setPriceGuard(bytes32 feedId, uint128 minPrice, uint128 maxPrice)
    external checkRole(feedId)
{
    require(minPrice < maxPrice);
    priceGuard[feedId] = PriceGuard({min: minPrice, max: maxPrice});
    ...
}
``` [4](#0-3) 

The price guard is consumed by `AnchoredPriceProvider._readLeg`:

```solidity
(uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
guardMax = guardMax == 0 ? type(uint128).max : guardMax;
if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);
``` [5](#0-4) 

Two concrete impacts:

- **Price guard disabled (bad-price execution):** Malicious guard sets `min=0, max=type(uint128).max`. Any oracle price now passes the guard. If the oracle feed is also pushed a manipulated price, it reaches pool swaps unchecked, violating the "bad-price execution" impact gate.
- **Price guard weaponized (pool DoS):** Malicious guard sets `min=type(uint128).max - 1, max=type(uint128).max`. All real prices fail the guard, `_readLeg` returns `ok=false`, `getBidAndAskPrice` reverts with `FeedStalled`, and the pool is bricked for swaps and liquidity operations.

---

### Likelihood Explanation

The trigger requires:
1. A guard transfer was initiated (`setStateGuardRole`) but not yet accepted.
2. The current guard calls `purgeStateGuardRole` without first calling `purgePendingStateGuardRole`.

This is a realistic operational mistake — a guard trying to quickly relinquish control in an emergency (e.g., key compromise) would naturally call `purgeStateGuardRole` without knowing they must also call `purgePendingStateGuardRole` first. The two functions are separate and there is no atomicity or warning. The pending guard (a semi-trusted party who was previously designated) then has an open window to accept.

---

### Recommendation

`purgeStateGuardRole` must atomically clear both mappings:

```solidity
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];
    delete pendingStateGuard[feedId]; // ADD: cancel any in-flight transfer
    emit StateGuardDeleted(feedId);
}
```

---

### Proof of Concept

```
1. ADMIN calls setStateGuardRole(feedId, guardA) + guardA calls acceptStateGuardRole(feedId)
   → stateGuard[feedId] = guardA

2. guardA calls setStateGuardRole(feedId, guardB)
   → pendingStateGuard[feedId] = guardB

3. guardA calls purgeStateGuardRole(feedId)
   → stateGuard[feedId] = address(0)
   → pendingStateGuard[feedId] = guardB  ← NOT cleared

4. guardB calls acceptStateGuardRole(feedId)
   → pendingStateGuard[feedId] == guardB ✓ (check passes)
   → stateGuard[feedId] = guardB         ← unauthorized takeover

5. guardB calls setPriceGuard(feedId, 0, type(uint128).max)
   → price guard disabled for the feed

6. AnchoredPriceProvider._readLeg now passes any oracle price through the guard,
   allowing a manipulated mid price to reach pool swaps via getBidAndAskPrice()
```

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

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L88-97)
```text
    function setPriceGuard(bytes32 feedId, uint128 minPrice, uint128 maxPrice)
        external
        checkRole(feedId)
    {
        require(minPrice < maxPrice);

        priceGuard[feedId] = PriceGuard({min: minPrice, max: maxPrice});

        emit PriceGuardUpdated(feedId, minPrice, maxPrice);
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L290-292)
```text
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);
```
