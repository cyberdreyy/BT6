Looking at the `OracleBase.sol` `register` function and its blacklist-clearing side effect, I found a valid analog.

---

### Title
Permissionless `register` unconditionally clears admin blacklist for any valid pool, bypassing oracle abuse protection — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

`OracleBase.register` is callable by any unprivileged address for as little as 1 wei (the default `registrationFee`). As a side effect it unconditionally clears `blacklisted[pool]`. Because a blacklisted pool remains a valid pool in the factory, any attacker can re-enable a pool the admin deliberately disabled for 1 wei.

---

### Finding Description

`OracleBase.register` performs three actions in sequence:

```
require(msg.value >= registrationFee …)   // 1 wei default
require(approvedFactories.contains(factory) …)
require(IPoolFactory(factory).isPool(pool) …)

if (blacklisted[pool]) {
    blacklisted[pool] = false;            // ← unconditional side-effect
    emit BlacklistUpdated(pool, false);
}
registeredPool[feedId][pool] = true;
``` [1](#0-0) 

The blacklist is the sole runtime gate that prevents a pool from reading oracle prices through `price(feedId, pool)`:

```solidity
require(!blacklisted[pool], Blacklisted(pool));
require(registeredPool[feedId][pool], NotRegistered(feedId, pool));
``` [2](#0-1) 

The factory's `isPool` check is entirely independent of the oracle's blacklist — a blacklisted pool is still a valid pool in the factory. Therefore the three preconditions for `register` are trivially satisfiable for any blacklisted pool, and the blacklist entry is erased as a free side effect.

The default `registrationFee` is 1 wei:

```solidity
registrationFee = 1 wei; // very cheap default; ADMIN tunes via setRegistrationFee
``` [3](#0-2) 

The analog to the FrankenDAO `castVote` bug is exact: just as `_castVote` never checked `votes == 0` and let anyone trigger the refund path, `register` never checks whether the caller has any authority over the pool's blacklist status, letting anyone trigger the blacklist-clear path.

---

### Impact Explanation

The admin blacklist is the primary abuse-protection mechanism documented in the contract:

> "Public price getters are disabled: on-chain consumption must go through the abuse-protected attributed path `price(feedId, factory)`" [4](#0-3) 

If a pool is blacklisted because it is actively draining LP assets or executing bad-price swaps, an attacker can restore its oracle read access for 1 wei. The pool can then resume calling `price()`, receive live oracle quotes, and continue executing swaps — directly causing loss of LP principal and protocol fees. This satisfies the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" impact gate.

---

### Likelihood Explanation

- Cost: 1 wei (the hardcoded default `registrationFee`).
- Knowledge required: the pool address and any approved factory address — both are public on-chain.
- No special role, signature, or delegation needed.
- The attack is a single transaction.

Likelihood is **High**.

---

### Recommendation

Remove the blacklist-clearing side effect from the permissionless `register` path. Blacklist management should remain exclusively under `ADMIN_ROLE`:

```solidity
function register(bytes32 feedId, address pool, address factory) external payable {
    require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
    require(pool != address(0));
    require(approvedFactories.contains(factory), FactoryNotApproved(factory));
    require(IPoolFactory(factory).isPool(pool), NotAPool(pool));
-   if (blacklisted[pool]) {
-       blacklisted[pool] = false;
-       emit BlacklistUpdated(pool, false);
-   }
+   require(!blacklisted[pool], Blacklisted(pool));   // registration blocked while blacklisted
    registeredPool[feedId][pool] = true;
    emit PoolRegistered(feedId, pool, msg.sender, msg.value);
}
```

This preserves the permissionless registration model while keeping blacklist removal exclusively in `setBlacklist` (which is already `onlyRole(ADMIN_ROLE)`). [5](#0-4) 

---

### Proof of Concept

1. Admin calls `setBlacklist(poolX, true)` to disable a compromised pool's oracle access.
2. Attacker observes the `BlacklistUpdated` event on-chain.
3. Attacker calls `register(anyFeedId, poolX, approvedFactory)` with `msg.value = 1 wei`.
4. `IPoolFactory(approvedFactory).isPool(poolX)` returns `true` — the pool is still registered in the factory.
5. `blacklisted[poolX]` is set to `false`; `registeredPool[anyFeedId][poolX]` is set to `true`.
6. Pool X can now call `price(anyFeedId, poolX)` successfully, receiving live oracle quotes and resuming swaps that drain LP assets.

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L44-47)
```text
    /// @notice Public price getters are disabled: on-chain consumption must go through the
    ///         abuse-protected attributed path `price(feedId, factory)` (pools) or `integratorPrice`
    ///         (whitelisted integrators). Off-chain consumers read raw storage / events.
    error ReadDisabled();
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L53-53)
```text
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
