### Title
Permissionless `register()` Unconditionally Clears Admin Blacklist, Bypassing Oracle Read-Access Safety Mechanism — (File: `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

The `register()` function in `OracleBase.sol` is permissionless and unconditionally clears the `blacklisted[pool]` flag for any pool recognized by an approved factory. Because `ADMIN_ROLE` is the only authority that can set the blacklist, but any unprivileged actor can clear it by paying the registration fee (default: 1 wei), the admin's safety mechanism is fully defeatable at negligible cost.

---

### Finding Description

`OracleBase.sol` exposes two separate code paths that govern the `blacklisted[pool]` flag:

**Setting the blacklist — ADMIN only:** [1](#0-0) 

**Clearing the blacklist — permissionless side-effect of `register()`:** [2](#0-1) 

The `register()` function requires only:
1. `msg.value >= registrationFee` — default is `1 wei` (line 53)
2. An approved factory that recognizes the pool via `isPool(pool)`

Both conditions are trivially satisfiable by any actor for any legitimate pool. The blacklist clear at lines 207–210 is unconditional — there is no ADMIN check, no timelock, and no way for the admin to prevent it.

The `price()` function enforces the blacklist at read time: [3](#0-2) 

So the invariant the protocol relies on is: *only ADMIN can decide whether a pool may read oracle prices*. The `register()` side-effect breaks this invariant completely.

---

### Impact Explanation

When the admin blacklists a pool — for example because the pool is being used for price manipulation, its extension contract is compromised, or it is draining LP assets — any unprivileged actor can immediately restore the pool's oracle read access by calling `register(feedId, pool, approvedFactory)` with 1 wei. The pool then passes the `!blacklisted[pool]` check in `price()`, receives live bid/ask quotes, and can execute swaps against those quotes. If the pool is compromised, users interacting with it suffer direct loss of principal through bad-price execution or drain of LP assets.

This matches the allowed impact gate: **admin-boundary break — factory/oracle role checks are bypassed by an unprivileged path**.

---

### Likelihood Explanation

- The registration fee is `1 wei` by default. [4](#0-3) 
- The approved factory list and pool membership are publicly observable on-chain (events + `isPool` getter).
- The attacker needs zero special privileges — only the ability to send a transaction.
- The attack can be executed in the same block the admin blacklists the pool (front-run or immediate follow-up).

Likelihood: **High**.

---

### Recommendation

Remove the blacklist-clearing side-effect from `register()`. Registration and blacklist management are orthogonal concerns. A blacklisted pool should remain blacklisted regardless of registration activity:

```solidity
function register(bytes32 feedId, address pool, address factory) external payable {
    require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
    require(pool != address(0));
+   require(!blacklisted[pool], Blacklisted(pool));   // reject, do not clear
    require(approvedFactories.contains(factory), FactoryNotApproved(factory));
    require(IPoolFactory(factory).isPool(pool), NotAPool(pool));

-   if (blacklisted[pool]) {
-       blacklisted[pool] = false;
-       emit BlacklistUpdated(pool, false);
-   }

    registeredPool[feedId][pool] = true;
    emit PoolRegistered(feedId, pool, msg.sender, msg.value);
}
```

If un-blacklisting on re-registration is intentional, it must be gated on `ADMIN_ROLE`.

---

### Proof of Concept

```
1. ADMIN calls setBlacklist(pool, true)          // pool is blacklisted; price() reverts
2. Attacker calls register(feedId, pool, factory) // pays 1 wei; blacklisted[pool] = false
3. pool.swap() → provider.getBidAndAskPrice()
   → oracle.price(feedId, pool)                  // passes !blacklisted[pool] check
4. Pool receives live oracle quote and executes swap against compromised state
   → user funds at risk
```

The only on-chain prerequisite is that `factory` is in `approvedFactories` and `factory.isPool(pool)` returns `true` — both are true for any legitimately deployed pool, which is exactly the scenario where the blacklist is most needed. [2](#0-1) [3](#0-2) [1](#0-0)

### Citations

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
