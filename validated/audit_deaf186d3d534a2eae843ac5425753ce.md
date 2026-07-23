### Title
Permissionless `register()` Clears Admin Blacklist, Bypassing Abuse-Protection Gate — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

`OracleBase.register()` is a permissionless, fee-gated function that unconditionally clears `blacklisted[pool]` as a side-effect of pool registration. Because the default `registrationFee` is 1 wei and any caller may invoke it, an admin's blacklist decision — the sole on-chain mechanism to cut off a pool's oracle read access — can be reversed by anyone for a trivial cost, with no timelock or privileged check.

---

### Finding Description

`OracleBase.register()` is callable by any address: [1](#0-0) 

The function's only guards are:
- `msg.value >= registrationFee` (default **1 wei**)
- `approvedFactories.contains(factory)` — factory must be admin-approved
- `IPoolFactory(factory).isPool(pool)` — pool must be recognized by that factory

When those pass, it unconditionally executes:

```solidity
if (blacklisted[pool]) {
    blacklisted[pool] = false;          // ← clears admin blacklist
    emit BlacklistUpdated(pool, false);
}
registeredPool[feedId][pool] = true;
``` [2](#0-1) 

The blacklist is the abuse-protection gate checked inside `price()`: [3](#0-2) 

`setBlacklist()` is `ADMIN_ROLE`-only: [4](#0-3) 

There is no corresponding privilege check on `register()`. The admin sets the blacklist; any unprivileged caller erases it.

**Analog to the external bug:** `Voter.poke()` lacked the `onlyNewEpoch` modifier, so a guard that should have been present was simply absent, allowing repeated state mutation. Here, `register()` lacks any guard preventing it from overwriting the admin's `blacklisted[pool] = true` decision. Both are cases where a function callable by an unprivileged actor silently undoes a security invariant that a privileged path established.

---

### Impact Explanation

The blacklist is the only on-chain mechanism to revoke a pool's ability to read oracle prices mid-lifecycle (e.g., after a pool is found to be exploiting the oracle, front-running, or otherwise abusing the read path). Once the admin blacklists a pool:

1. The pool operator (or any third party) calls `register(feedId, pool, approvedFactory)` with 1 wei.
2. `blacklisted[pool]` is set back to `false`.
3. The pool can immediately call `price(feedId, pool)` again through its price provider.
4. The admin must re-blacklist; the attacker re-registers. This is an unbounded loop at 1 wei per iteration.

A pool that is blacklisted because it is compromised or is feeding manipulated swap outcomes can continuously regain oracle read access, allowing bad-price execution to reach live swaps.

---

### Likelihood Explanation

- The registration fee is **1 wei** by default.
- The only prerequisite is that the pool is still recognized by an approved factory — a condition that holds for any legitimately deployed pool.
- No signature, no timelock, no privileged role is required.
- The pool operator whose pool was blacklisted has a direct financial incentive to call `register()` immediately after every admin blacklist action.

---

### Recommendation

Remove the blacklist-clearing side-effect from `register()`. Pool registration and blacklist management are orthogonal concerns. If clearing a blacklist is intentional, gate it behind `ADMIN_ROLE` or require an explicit separate call:

```solidity
// Remove this block from register():
// if (blacklisted[pool]) {
//     blacklisted[pool] = false;
//     emit BlacklistUpdated(pool, false);
// }
```

If the intent is that paying the fee rehabilitates a pool, add an explicit `ADMIN_ROLE` co-signature or a timelock before the blacklist is cleared.

---

### Proof of Concept

```solidity
// 1. Admin blacklists a pool after detecting abuse.
oracle.setBlacklist(address(pool), true);

// 2. pool.price() now reverts: Blacklisted(pool).

// 3. Anyone (e.g., the pool operator) pays 1 wei to re-register.
oracle.register{value: 1 wei}(feedId, address(pool), approvedFactory);
// → blacklisted[pool] is now false again.

// 4. pool.price() succeeds — oracle read access fully restored.
// 5. Admin re-blacklists → attacker re-registers → unbounded loop at 1 wei/round.
```

Relevant state transitions:

| Step | `blacklisted[pool]` | `registeredPool[feedId][pool]` |
|------|--------------------|---------------------------------|
| After `setBlacklist(pool, true)` | `true` | `true` |
| After `register(feedId, pool, factory)` | **`false`** | `true` |
| `price(feedId, pool)` | **succeeds** | — |

### Citations

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
