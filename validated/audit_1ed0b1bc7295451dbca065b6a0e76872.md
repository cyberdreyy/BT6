### Title
ADMIN_ROLE Cannot Override a Malicious `stateGuard` After Delegation — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

In `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`, the `checkRole` modifier grants exclusive authority to `stateGuard[feedId]` once it is set, and permanently strips `ADMIN_ROLE` of all authority over that feed's price-guard and state-guard management. There is no path by which `ADMIN_ROLE` can revoke or override a set `stateGuard`. A malicious or compromised `stateGuard` can therefore set an extreme `priceGuard` that permanently bricks every pool consuming that feed, with no admin recovery path.

---

### Finding Description

`OracleBase` (providers path) uses the following `checkRole` modifier:

```solidity
modifier checkRole(bytes32 feedId) {
    address _guard = stateGuard[feedId];
    if (_guard != address(0)) {
        require(_guard == msg.sender, InvalidGuard(msg.sender));
    } else {
        _checkRole(ADMIN_ROLE);
    }
    _;
}
``` [1](#0-0) 

Before any `stateGuard` is set, `ADMIN_ROLE` controls `setPriceGuard`, `setStateGuardRole`, `purgePendingStateGuardRole`, and `purgeStateGuardRole`. Once `ADMIN_ROLE` calls `setStateGuardRole` and the nominee calls `acceptStateGuardRole`, the `stateGuard` slot is non-zero and the `else` branch is never reached again for that feed. Every one of those functions — including `purgeStateGuardRole` — is gated by `checkRole`:

```solidity
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];
    emit StateGuardDeleted(feedId);
}
``` [2](#0-1) 

This is confirmed by the test suite, which explicitly documents the loss of admin authority:

```solidity
// Once a guard is set, ADMIN loses guard-setter authority for that feed
vm.expectRevert(abi.encodeWithSelector(IOffchainOracle.InvalidGuard.selector, address(this)));
oracle.setPriceGuard(feedId, 1, 100);
``` [3](#0-2) 

The `priceGuard` set by the `stateGuard` is consumed directly in the price-provider read path:

```solidity
(uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
guardMax = guardMax == 0 ? type(uint128).max : guardMax;
if (mid < guardMin || mid > guardMax) {
    return (0, type(uint128).max);
}
``` [4](#0-3) 

Returning `(0, type(uint128).max)` causes `getBidAndAskPrice` to revert with `FeedStalled` in every pool that uses the affected feed.

---

### Impact Explanation

A malicious or compromised `stateGuard` key can call `setPriceGuard(feedId, 1, 2)`. Because all real oracle prices are far above `2`, every subsequent call to `PriceProvider._getBidAndAskPrice()` or `PriceProviderL2._getBidAndAskPrice()` returns the stalled sentinel `(0, type(uint128).max)`. Every pool whose `priceProvider` reads that feed becomes permanently unable to execute swaps. LP positions are locked in an unusable pool. `ADMIN_ROLE` has no on-chain path to restore the guard because `purgeStateGuardRole` is itself gated by `checkRole`, which now only admits the malicious `stateGuard`. [5](#0-4) 

---

### Likelihood Explanation

`ADMIN_ROLE` intentionally delegates the `stateGuard` role to a separate key or multisig for operational reasons. Key compromise, insider threat, or a bug in the `stateGuard` contract are realistic events. The design gives `ADMIN_ROLE` no recovery lever once delegation occurs, making the impact permanent rather than temporary.

---

### Recommendation

Add an `ADMIN_ROLE`-only escape hatch that can forcibly clear a `stateGuard` regardless of the current holder, analogous to how the external report recommends allowing the owner to override `feeManager`:

```solidity
function forceRevokeStateGuard(bytes32 feedId) external onlyRole(ADMIN_ROLE) {
    delete stateGuard[feedId];
    delete pendingStateGuard[feedId];
    emit StateGuardDeleted(feedId);
}
```

This mirrors the pattern used for `setArbitrator`/`setVoteDelegate` in the referenced report — the top-level authority should always retain an override path over delegated roles.

---

### Proof of Concept

1. `ADMIN_ROLE` calls `setStateGuardRole(feedId, guardAddr)`.
2. `guardAddr` calls `acceptStateGuardRole(feedId)` → `stateGuard[feedId] = guardAddr`.
3. `guardAddr` (now malicious/compromised) calls `setPriceGuard(feedId, 1, 2)`.
4. `ADMIN_ROLE` attempts `setPriceGuard(feedId, 0, 0)` → reverts `InvalidGuard(admin)`.
5. `ADMIN_ROLE` attempts `purgeStateGuardRole(feedId)` → reverts `InvalidGuard(admin)`.
6. Any pool calling `provider.getBidAndAskPrice()` → `priceGuard` check: `mid > 2` → returns `(0, type(uint128).max)` → pool reverts `FeedStalled` on every swap.
7. No on-chain recovery path exists for `ADMIN_ROLE`. [6](#0-5)

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

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L99-124)
```text
    function setStateGuardRole(bytes32 feedId, address newGuard) external checkRole(feedId) {
        pendingStateGuard[feedId] = newGuard;

        emit StateGuardPending(feedId, newGuard);
    }

    function purgePendingStateGuardRole(bytes32 feedId) external checkRole(feedId) {
        delete pendingStateGuard[feedId];

        emit PendingStateGuardDeleted(feedId);
    }

    function acceptStateGuardRole(bytes32 feedId) external {
        require(pendingStateGuard[feedId] == msg.sender, InvalidGuard(msg.sender));

        delete pendingStateGuard[feedId];
        stateGuard[feedId] = msg.sender;

        emit StateGuardUpdated(feedId, msg.sender);
    }

    function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
        delete stateGuard[feedId];

        emit StateGuardDeleted(feedId);
    }
```

**File:** smart-contracts-poc/test/oracles/PythOracle.t.sol (L333-335)
```text
        // Once a guard is set, ADMIN loses guard-setter authority for that feed
        vm.expectRevert(abi.encodeWithSelector(IOffchainOracle.InvalidGuard.selector, address(this)));
        oracle.setPriceGuard(feedId, 1, 100);
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L208-212)
```text
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L224-229)
```text
        // 4. Price guard check (moved from oracle)
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) {
            return (0, type(uint128).max);
        }
```
