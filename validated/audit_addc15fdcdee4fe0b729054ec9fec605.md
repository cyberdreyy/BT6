### Title
Permissionless `register()` Bypasses Admin Blacklist, Preventing Permanent Removal of a Compromised Pool from Oracle Price Access — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

The `register()` function in `OracleBase` is permissionless and unconditionally clears the admin-set blacklist on a pool. Once an admin blacklists a compromised pool to halt its oracle price reads, any actor — including the attacker — can call `register()` again with the default fee of `1 wei` to instantly clear the blacklist and restore the pool's access. There is no admin-only path to permanently remove a pool from `registeredPool[feedId][pool]`.

---

### Finding Description

`OracleBase` implements a two-layer abuse-protection model: a `blacklisted` mapping (set by `ADMIN_ROLE` via `setBlacklist`) and a `registeredPool` mapping (set permissionlessly via `register`). The intended emergency stop is to blacklist a pool, which causes `price(feedId, pool)` to revert at:

```solidity
require(!blacklisted[pool], Blacklisted(pool));
```

However, `register()` is explicitly designed to clear the blacklist as a "paid redemption" path:

```solidity
function register(bytes32 feedId, address pool, address factory) external payable {
    require(msg.value >= registrationFee, ...);   // default: 1 wei
    require(approvedFactories.contains(factory), ...);
    require(IPoolFactory(factory).isPool(pool), ...);

    if (blacklisted[pool]) {
        blacklisted[pool] = false;          // ← clears the admin blacklist
        emit BlacklistUpdated(pool, false);
    }

    registeredPool[feedId][pool] = true;
    emit PoolRegistered(feedId, pool, msg.sender, msg.value);
}
``` [1](#0-0) 

`register()` has no `onlyRole` guard. Any caller who supplies `msg.value >= registrationFee` (default `1 wei`, set at construction) and passes the factory/pool validity checks can invoke it. The pool being re-registered is the same pool the admin just blacklisted — it is already recognized by an approved factory (it was registered before), so `IPoolFactory(factory).isPool(pool)` returns `true`. [2](#0-1) 

There is no `deregisterPool`, `permanentBlacklist`, or any admin-only function that sets `registeredPool[feedId][pool] = false`. The full set of admin controls over pool access is:

| Function | Effect | Bypassable? |
|---|---|---|
| `setBlacklist(pool, true)` | Blocks `price()` reads | Yes — via `register()` |
| `removeApprovedFactory(factory)` | Blocks new registrations from that factory | Collateral — blocks ALL pools of that factory |
| `setRegistrationFee(newFee)` | Raises the cost to re-register | Attacker can still pay any fee | [3](#0-2) 

The documentation explicitly acknowledges the design intent but does not flag the security consequence:

> *Note:* `removeApprovedFactory` blocks new registrations but not reads by already-registered pools of that factory — blacklist those if needed. [4](#0-3) 

---

### Impact Explanation

If a pool is actively being exploited (e.g., a reentrancy attack through `swap()` that reads oracle prices, or a price-manipulation loop), the admin's only targeted mitigation is `setBlacklist(pool, true)`. This halts `price(feedId, pool)` and therefore halts swaps through that pool.

An attacker can immediately call `register(feedId, pool, factory)` with `1 wei` to clear the blacklist and restore oracle access. The exploit resumes. The admin is in a race they cannot win: every `setBlacklist` transaction can be front-run or immediately followed by a `register` call. The protocol team would need to resort to removing the entire factory (collateral damage to all other pools) or raising the fee globally — neither is a targeted, permanent fix.

This directly matches the allowed impact gate: **broken core pool functionality causing loss of funds** (swaps through the compromised pool continue) and **admin-boundary break** (the admin's emergency-stop role is bypassed by an unprivileged path).

---

### Likelihood Explanation

- The trigger is unprivileged: any EOA with `1 wei` can call `register()`.
- The pool and factory are already known on-chain (emitted at first registration).
- The attacker is already interacting with the pool (they are the exploiter), so they have the pool address and can construct the `register()` call trivially.
- The default `registrationFee` is `1 wei`, making the bypass essentially free.

---

### Recommendation

Add an admin-controlled permanent-block flag that `register()` cannot clear:

```solidity
mapping(address => bool) public permanentlyBlocked;

function permanentBlock(address pool) external onlyRole(ADMIN_ROLE) {
    permanentlyBlocked[pool] = true;
    blacklisted[pool] = true;
    emit PermanentlyBlocked(pool);
}

// In register():
require(!permanentlyBlocked[pool], PermanentlyBlocked(pool));
```

Alternatively, split the blacklist into two tiers: a soft blacklist (clearable by `register()`) and a hard blacklist (ADMIN-only, not clearable by `register()`). The hard blacklist should also set `registeredPool[feedId][pool] = false` for all relevant feeds.

---

### Proof of Concept

```
1. Pool P is registered: register(feedId, P, factory) {value: 1 wei}
   → registeredPool[feedId][P] = true, blacklisted[P] = false

2. Attacker begins exploiting P (e.g., price manipulation via swap()).

3. Admin calls setBlacklist(P, true).
   → blacklisted[P] = true
   → price(feedId, P) now reverts with Blacklisted(P)

4. Attacker (or any EOA) calls register(feedId, P, factory) {value: 1 wei}.
   → blacklisted[P] = false  ← blacklist cleared
   → registeredPool[feedId][P] = true  ← still registered

5. price(feedId, P) succeeds again.
   → Exploit resumes. Admin's emergency stop is nullified.
```

The `factory.isPool(P)` check at step 4 passes because `MetricOmmPoolFactory` (or any approved factory) never removes a deployed pool from its `isPool` mapping — pools are permanent entries in the factory.

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

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L260-276)
```text
    function addApprovedFactory(address factory) external onlyRole(ADMIN_ROLE) {
        require(factory != address(0));
        require(approvedFactories.add(factory), FactoryAlreadyApproved(factory));
        emit ApprovedFactoryAdded(factory);
    }

    function removeApprovedFactory(address factory) external onlyRole(ADMIN_ROLE) {
        require(approvedFactories.remove(factory), FactoryNotApproved(factory));
        emit ApprovedFactoryRemoved(factory);
    }

    function setBlacklist(address account, bool value) external onlyRole(ADMIN_ROLE) {
        require(account != address(0));
        if (blacklisted[account] == value) return;
        blacklisted[account] = value;
        emit BlacklistUpdated(account, value);
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/docs/en/abuse-protection-integration.md (L259-261)
```markdown
- **Factory only at registration**: a pool can register only if an ADMIN-approved factory's `isPool`
  recognizes it; the read path does not consult the factory. *Note:* `removeApprovedFactory` blocks new
  registrations but not reads by already-registered pools of that factory — blacklist those if needed.
```
