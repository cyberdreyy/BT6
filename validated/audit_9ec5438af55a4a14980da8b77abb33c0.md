### Title
Permissionless `register()` Unconditionally Clears Admin Blacklist, Allowing Anyone to Restore Oracle Read Access to a Blacklisted Pool — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

### Summary

The `register()` function in `providers/OracleBase.sol` is permissionless (any caller who pays `registrationFee`, defaulting to 1 wei) and unconditionally clears the admin-set blacklist for a pool as a side effect. This allows any unprivileged actor to undo the admin's security decision and restore a blacklisted pool's ability to consume live oracle prices through the `price(feedId, pool)` path.

### Finding Description

`OracleBase.register()` is the paid pool-registration entry point. Its stated purpose is to whitelist a pool for a specific feed so it can call `price(feedId, pool)`. However, it also silently clears the blacklist for the pool if one is set:

```solidity
// providers/OracleBase.sol lines 201-213
function register(bytes32 feedId, address pool, address factory) external payable {
    require(msg.value >= registrationFee, ...);          // 1 wei default
    require(pool != address(0));
    require(approvedFactories.contains(factory), ...);
    require(IPoolFactory(factory).isPool(pool), ...);

    if (blacklisted[pool]) {
        blacklisted[pool] = false;                       // ← unconditional clear
        emit BlacklistUpdated(pool, false);
    }

    registeredPool[feedId][pool] = true;
    emit PoolRegistered(feedId, pool, msg.sender, msg.value);
}
```

The admin blacklists pools via `setBlacklist()`, which is gated on `ADMIN_ROLE`. The `price()` read path enforces the blacklist on both the caller (provider) and the pool:

```solidity
// providers/OracleBase.sol lines 160-172
function price(bytes32 feedId, address pool)
    external feedExists(feedId) notBlacklisted
    returns (...)
{
    require(pool != address(0) && IPool(pool).inSwap() == msg.sender, InvalidInSwap());
    require(!blacklisted[pool], Blacklisted(pool));      // ← pool blacklist check
    require(registeredPool[feedId][pool], NotRegistered(feedId, pool));
    ...
}
```

Because `register()` has no access-control check on who may clear the blacklist, any caller can pay 1 wei, supply an approved factory that recognizes the pool (the factory deployed it, so `isPool` returns true regardless of the blacklist), and atomically un-blacklist the pool while also registering it for any feed. The admin's protective action is fully reversible by an unprivileged actor at negligible cost.

### Impact Explanation

Once a pool is un-blacklisted via `register()`, it satisfies all three checks in `price()`:
- `notBlacklisted` (caller/provider) — unrelated to the pool blacklist
- `!blacklisted[pool]` — now false after the register call
- `registeredPool[feedId][pool]` — set to true by the same register call

The pool can then call its price provider, which calls `oracle.price(feedId, pool)`, and receive a live oracle quote. If the pool was blacklisted because it is compromised, malicious, or operating outside protocol parameters, it can now execute swaps priced by the oracle it was supposed to be denied access to. This is a direct admin-boundary break: the oracle admin's ability to revoke a pool's read access is nullified by an unprivileged path.

### Likelihood Explanation

- `registrationFee` is initialized to `1 wei` and is only tunable by ADMIN. In practice it will be low.
- The attacker needs only a valid approved factory address (public, discoverable on-chain) and the pool address (also public).
- `IPoolFactory(factory).isPool(pool)` returns true for any pool the factory deployed, regardless of blacklist state — the factory has no knowledge of the oracle's blacklist.
- The attack is a single transaction with no special privileges.

### Recommendation

Separate the blacklist-clearing side effect from the permissionless registration path. Either:

1. Remove the blacklist-clearing logic from `register()` entirely and require the admin to explicitly call `setBlacklist(pool, false)` to restore access; or
2. Add an explicit access-control check before clearing the blacklist:

```solidity
if (blacklisted[pool]) {
    _checkRole(ADMIN_ROLE);   // only admin may un-blacklist
    blacklisted[pool] = false;
    emit BlacklistUpdated(pool, false);
}
```

### Proof of Concept

```
Setup:
  admin calls setBlacklist(pool, true)          // pool is blacklisted
  pool.price(feedId, pool) → reverts Blacklisted // confirmed blocked

Attack (single tx, 1 wei):
  attacker calls register{value: 1 wei}(feedId, pool, approvedFactory)
    → approvedFactories.contains(approvedFactory) == true
    → IPoolFactory(approvedFactory).isPool(pool) == true  (factory deployed pool)
    → blacklisted[pool] = false                           // blacklist cleared
    → registeredPool[feedId][pool] = true

After:
  pool.price(feedId, pool) → succeeds, returns live oracle quote
  pool executes swap priced by oracle it was denied access to
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L49-54)
```text
    constructor(address _owner, uint256 maxTimeDrift) {
        _grantRole(ADMIN_ROLE, _owner);
        _setRoleAdmin(ADMIN_ROLE, ADMIN_ROLE);
        MAX_TIME_DRIFT = maxTimeDrift;
        registrationFee = 1 wei; // very cheap default; ADMIN tunes via setRegistrationFee
    }
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

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L201-214)
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
    }
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
