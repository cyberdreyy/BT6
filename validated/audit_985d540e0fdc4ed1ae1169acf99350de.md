### Title
ADMIN Cannot Remove a Set `stateGuard`, Allowing It to Permanently Disable Price-Guard Protection on Any Feed — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

In `OracleBase.sol` (providers), the `checkRole` modifier completely locks the `ADMIN_ROLE` out of all guard-management functions the moment a `stateGuard` is accepted for a feed. Because `purgeStateGuardRole` itself is gated by `checkRole`, a malicious or compromised `stateGuard` can make its own position irremovable and simultaneously widen the feed's price bounds to `[1, type(uint128).max-1]`, letting any oracle price — including a manipulated or erroneous one — pass through to live pool swaps.

---

### Finding Description

`OracleBase.sol` (providers) defines:

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

Once `stateGuard[feedId] != address(0)`, the ADMIN branch (`_checkRole(ADMIN_ROLE)`) is never reached. Every function protected by `checkRole` becomes exclusively callable by the stateGuard:

| Function | Effect |
|---|---|
| `setPriceGuard` | sets min/max price bounds |
| `setStateGuardRole` | proposes a new stateGuard |
| `purgePendingStateGuardRole` | cancels a pending handover |
| **`purgeStateGuardRole`** | **removes the stateGuard itself** | [2](#0-1) 

The critical invariant break: `purgeStateGuardRole` is itself gated by `checkRole`.

```solidity
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];
    emit StateGuardDeleted(feedId);
}
``` [3](#0-2) 

The ADMIN has no path to evict a set stateGuard. The existing test suite explicitly documents this as expected behavior but does not recognize it as a security boundary violation:

```solidity
// Once a guard is set, ADMIN loses guard-setter authority for that feed
vm.expectRevert(abi.encodeWithSelector(IOffchainOracle.InvalidGuard.selector, address(this)));
oracle.setPriceGuard(feedId, 1, 100);
``` [4](#0-3) 

Note: the compressed oracle's `OracleBase.sol` has a different `checkRole` (falls back to `_defaultGuard`, i.e. the feed creator) and has no `purgeStateGuardRole` at all — the vulnerability is exclusive to the providers oracle. [5](#0-4) 

---

### Impact Explanation

The price guard is the last software-level circuit breaker between a bad oracle price and a live pool swap. `PriceProvider` and `PriceProviderL2` both read `priceGuard` from the oracle and return the stalled sentinel `(0, type(uint128).max)` when the price falls outside the guard range:

```solidity
(uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
guardMax = guardMax == 0 ? type(uint128).max : guardMax;
if (mid < guardMin || mid > guardMax) {
    return (0, type(uint128).max);
}
``` [6](#0-5) 

A malicious stateGuard calls `setPriceGuard(feedId, 1, type(uint128).max - 1)`. Every oracle price now passes the guard. The ADMIN cannot tighten the bounds (blocked by `checkRole`) and cannot remove the stateGuard (also blocked by `checkRole`). Any stale, inverted, or manipulated price from the underlying Pyth/Chainlink feed reaches pool swaps, causing bad-price execution and direct loss of LP principal.

---

### Likelihood Explanation

The ADMIN must first delegate the stateGuard role to a third-party address. Once that address is accepted, the attack surface is open: the stateGuard key can be compromised, the stateGuard contract can be upgraded maliciously, or the stateGuard operator can act adversarially. Because the ADMIN has no recovery path, a single key compromise is sufficient and permanent.

---

### Recommendation

Allow the ADMIN to always call `purgeStateGuardRole`, regardless of whether a stateGuard is set. The simplest fix:

```solidity
function purgeStateGuardRole(bytes32 feedId) external {
    address _guard = stateGuard[feedId];
    bool isAdmin = hasRole(ADMIN_ROLE, msg.sender);
    require(
        (_guard != address(0) && _guard == msg.sender) || isAdmin,
        InvalidGuard(msg.sender)
    );
    delete stateGuard[feedId];
    emit StateGuardDeleted(feedId);
}
```

Optionally, apply the same ADMIN override to `setPriceGuard` for emergency price-bound correction.

---

### Proof of Concept

```solidity
// 1. ADMIN delegates guard role to a third party
oracle.setStateGuardRole(feedId, maliciousGuard);          // ADMIN calls

// 2. Third party accepts
vm.prank(maliciousGuard);
oracle.acceptStateGuardRole(feedId);                        // stateGuard is now set

// 3. Malicious guard widens price bounds to accept any price
vm.prank(maliciousGuard);
oracle.setPriceGuard(feedId, 1, type(uint128).max - 1);    // succeeds

// 4. ADMIN tries to tighten bounds — REVERTS
vm.expectRevert(abi.encodeWithSelector(IOffchainOracle.InvalidGuard.selector, admin));
oracle.setPriceGuard(feedId, 90_000_000, 110_000_000);     // InvalidGuard

// 5. ADMIN tries to remove the stateGuard — REVERTS
vm.expectRevert(abi.encodeWithSelector(IOffchainOracle.InvalidGuard.selector, admin));
oracle.purgeStateGuardRole(feedId);                         // InvalidGuard

// 6. Any oracle price now passes the guard and reaches pool swaps
// e.g. a manipulated Pyth price of 1 wei passes: mid=1 >= 1 && mid=1 <= type(uint128).max-1
``` [7](#0-6)

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L65-124)
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

    modifier notBlacklisted() {
        require(!blacklisted[msg.sender], Blacklisted(msg.sender));

        _;
    }

    /*
     *
     * Service functions
     *
     */

    function setPriceGuard(bytes32 feedId, uint128 minPrice, uint128 maxPrice)
        external
        checkRole(feedId)
    {
        require(minPrice < maxPrice);

        priceGuard[feedId] = PriceGuard({min: minPrice, max: maxPrice});

        emit PriceGuardUpdated(feedId, minPrice, maxPrice);
    }

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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L31-41)
```text
    modifier checkRole(bytes32 feedId) {
        address guard = stateGuard[feedId];
        if (guard == address(0)) guard = _defaultGuard(feedId);
        require(guard == msg.sender, InvalidGuard(msg.sender));
        _;
    }

    /// The authority a feed falls back to before an explicit stateGuard is accepted.
    function _defaultGuard(bytes32) internal view virtual returns (address) {
        return address(0);
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
