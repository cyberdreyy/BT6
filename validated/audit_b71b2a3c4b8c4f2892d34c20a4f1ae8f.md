### Title
Permissionless `register()` Unconditionally Clears Admin Blacklist, Allowing Any Blacklisted Pool to Restore Oracle Price Access — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

The `register()` function in `OracleBase.sol` is permissionless and explicitly clears the `blacklisted` flag for any pool that re-registers. Because the default `registrationFee` is 1 wei and the only validation is that the pool belongs to an approved factory, any pool blacklisted by the oracle admin can immediately restore its oracle price-read access by calling `register()` with 1 wei. The blacklist — the sole runtime abuse-protection gate on the `price()` read path — is therefore not a durable security control.

---

### Finding Description

`OracleBase` implements a blacklist as its primary runtime abuse-protection mechanism. The oracle admin calls `setBlacklist(pool, true)` to revoke a pool's access to `price(feedId, pool)`. The `price()` function enforces this at lines 167–168:

```solidity
require(!blacklisted[pool], Blacklisted(pool));
require(registeredPool[feedId][pool], NotRegistered(feedId, pool));
```

However, the `register()` function — which is fully permissionless — unconditionally clears the blacklist flag whenever a pool re-registers:

```solidity
function register(bytes32 feedId, address pool, address factory) external payable {
    require(msg.value >= registrationFee, ...);   // default: 1 wei
    require(approvedFactories.contains(factory), ...);
    require(IPoolFactory(factory).isPool(pool), ...);

    if (blacklisted[pool]) {
        blacklisted[pool] = false;          // ← unconditional blacklist erasure
        emit BlacklistUpdated(pool, false);
    }
    registeredPool[feedId][pool] = true;
    ...
}
``` [1](#0-0) 

The only preconditions are: (1) pay `registrationFee` (1 wei by default), and (2) the pool must be recognized by an approved factory. A pool that was previously registered and then blacklisted trivially satisfies both conditions — it is already a real pool of an approved factory, and 1 wei is negligible.

This is the direct analog of the TON card bug: just as the text command "Close" bypassed the `state::closed` check and allowed a closed card to be reopened, `register()` bypasses the `blacklisted` check and allows a blacklisted pool to restore its oracle access. In both cases, one code path enforces the terminal state while an alternative path silently erases it. [2](#0-1) 

---

### Impact Explanation

The blacklist is the oracle's only runtime mechanism to cut off a pool that is abusing oracle reads (e.g., a pool found to be executing manipulative swaps anchored to oracle prices, or a pool draining fees). Once the admin blacklists such a pool, the pool owner (or any third party) can call `register()` with 1 wei to atomically clear the blacklist and restore `registeredPool[feedId][pool] = true`. The pool's next swap call proceeds through `price()` without any revert, as if it had never been blacklisted.

The consequence is that the oracle admin has no durable way to block a pool. Every blacklist action can be reversed in the same block by the pool owner for 1 wei. Pools that are blocked for price-manipulation or fee-extraction abuse can continue operating, feeding oracle-anchored prices into swaps and causing ongoing loss to traders or LPs in those pools. [3](#0-2) 

---

### Likelihood Explanation

- **Trigger is unprivileged**: any address can call `register()` — the pool owner, a bot, or any third party.
- **Cost is negligible**: `registrationFee` is initialized to 1 wei and can only be raised by the admin *after* observing abuse. The admin must race to raise the fee before the pool owner re-registers.
- **Pool is already valid**: a previously registered pool already satisfies `approvedFactories.contains(factory)` and `IPoolFactory(factory).isPool(pool)`, so no new setup is required.
- **No timelock or delay**: the blacklist is cleared in the same transaction as `register()`. [4](#0-3) 

---

### Recommendation

**Short term:** Add an admin-only guard to the blacklist-clearing branch of `register()`. The simplest fix is to require that the caller holds `ADMIN_ROLE` before clearing `blacklisted[pool]`, or to remove the automatic blacklist-clearing from `register()` entirely and provide a separate `ADMIN_ROLE`-gated `unblacklist(pool)` function.

```solidity
// Option A: separate admin-only redemption
function unblacklist(address pool) external onlyRole(ADMIN_ROLE) {
    blacklisted[pool] = false;
    emit BlacklistUpdated(pool, false);
}

// register() no longer touches blacklisted[]
function register(bytes32 feedId, address pool, address factory) external payable {
    require(!blacklisted[pool], Blacklisted(pool)); // ← reject blacklisted pools
    ...
}
```

**Long term:** Establish a consistent invariant: every path that grants or restores oracle read access must check the blacklist and require admin authorization to clear it. Document this constraint explicitly and add a unit test asserting that a blacklisted pool cannot restore access without an admin action, regardless of which entry point is used.

---

### Proof of Concept

```solidity
// 1. Admin blacklists Eve's pool for suspicious swap activity.
oracle.setBlacklist(evePool, true);

// 2. Eve's pool swap now reverts:
//    oracle.price(feedId, evePool) → Blacklisted(evePool)

// 3. Eve (or anyone) calls register() with 1 wei:
oracle.register{value: 1 wei}(feedId, evePool, approvedFactory);
//    → blacklisted[evePool] = false  (cleared unconditionally)
//    → registeredPool[feedId][evePool] = true

// 4. Eve's pool swap succeeds again — blacklist has no effect.
//    oracle.price(feedId, evePool) → returns (mid, spread, spread1, refTime)
``` [5](#0-4)

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L49-53)
```text
    constructor(address _owner, uint256 maxTimeDrift) {
        _grantRole(ADMIN_ROLE, _owner);
        _setRoleAdmin(ADMIN_ROLE, ADMIN_ROLE);
        MAX_TIME_DRIFT = maxTimeDrift;
        registrationFee = 1 wei; // very cheap default; ADMIN tunes via setRegistrationFee
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L160-172)
```text
    function price(bytes32 feedId, address pool)
        external
        feedExists(feedId)
        notBlacklisted
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        require(pool != address(0) && IPool(pool).inSwap() == msg.sender, InvalidInSwap());
        require(!blacklisted[pool], Blacklisted(pool));
        require(registeredPool[feedId][pool], NotRegistered(feedId, pool));

        (mid, spread, spread1, refTime) = _readPrice(feedId);
        emit PriceRead(pool, feedId);
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L201-213)
```text
    function register(bytes32 feedId, address pool, address factory) external payable {
        require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
        require(pool != address(0));
        require(approvedFactories.contains(factory), FactoryNotApproved(factory));
        require(IPoolFactory(factory).isPool(pool), NotAPool(pool));

        if (blacklisted[pool]) {
            blacklisted[pool] = false;
            emit BlacklistUpdated(pool, false);
        }

        registeredPool[feedId][pool] = true;
        emit PoolRegistered(feedId, pool, msg.sender, msg.value);
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L271-276)
```text
    function setBlacklist(address account, bool value) external onlyRole(ADMIN_ROLE) {
        require(account != address(0));
        if (blacklisted[account] == value) return;
        blacklisted[account] = value;
        emit BlacklistUpdated(account, value);
    }
```
