### Title
Permissionless `register()` Unconditionally Clears Admin Blacklist, Rendering Abuse-Protection Ineffective — (`File: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

`OracleBase.register()` is callable by any address and unconditionally clears the admin-controlled blacklist for any valid pool. Because the default `registrationFee` is `1 wei`, a blacklisted pool (or any third party on its behalf) can immediately re-register and restore on-chain price-read access, defeating the entire abuse-protection layer.

---

### Finding Description

`OracleBase` implements an abuse-protection layer whose primary enforcement tool is the admin blacklist: the admin monitors `PriceRead` events, identifies abusive pools, and calls `setBlacklist(pool, true)`. A blacklisted pool's call to `price(feedId, pool)` reverts with `Blacklisted(pool)`.

The recovery path from a blacklist is `register()`:

```solidity
// OracleBase.sol lines 201-214
function register(bytes32 feedId, address pool, address factory) external payable {
    require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
    require(pool != address(0));
    require(approvedFactories.contains(factory), FactoryNotApproved(factory));
    require(IPoolFactory(factory).isPool(pool), NotAPool(pool));

    if (blacklisted[pool]) {
        blacklisted[pool] = false;          // ← unconditional blacklist clear
        emit BlacklistUpdated(pool, false);
    }

    registeredPool[feedId][pool] = true;
    emit PoolRegistered(feedId, pool, msg.sender, msg.value);
}
```

Three properties combine to make the blacklist unenforceable:

1. **Permissionless caller** — `register()` has no `onlyRole` or pool-ownership check; any EOA or contract may call it for any valid pool.
2. **Unconditional blacklist clear** — the `if (blacklisted[pool])` branch fires and sets `blacklisted[pool] = false` regardless of who is calling.
3. **Trivial cost** — `registrationFee` is initialized to `1 wei` and is only raised by an admin action that must race against the attacker. [1](#0-0) 

The moment the admin calls `setBlacklist(pool, true)`, the pool owner (or any sympathetic third party) calls `register(feedId, pool, approvedFactory)` with `1 wei`, the blacklist entry is erased, and `price(feedId, pool)` succeeds again. The admin is locked in an unwinnable race. [2](#0-1) 

---

### Impact Explanation

The blacklist is the **only runtime enforcement mechanism** the abuse-protection layer provides. The `PriceRead` event, the `registrationFee` deterrent, and the `setBlacklist` admin function are all described as the complete abuse-response toolkit in the integration documentation. [3](#0-2) 

If the blacklist can be cleared by any unprivileged caller for `1 wei`, the admin has no effective way to revoke on-chain price-read access from an abusive pool. An abusive pool can:

- Drain oracle read capacity / emit unbounded `PriceRead` events.
- Continue consuming prices after the admin has explicitly revoked access.
- Force the admin into a perpetual fee-raising arms race with no guaranteed win condition.

This is an **admin-boundary break**: an ADMIN-role action (`setBlacklist`) is silently undone by an unprivileged path (`register`), violating the invariant that only ADMIN can grant or revoke blacklist status.

---

### Likelihood Explanation

Exploitation requires only that the attacker know the pool address, an approved factory address, and have `1 wei`. All three are trivially available on-chain. No special role, no signature, no timing window is needed. The attack can be scripted to fire in the same block as the admin's `setBlacklist` transaction. [4](#0-3) 

---

### Recommendation

Restrict who may clear the blacklist inside `register()`. Two options:

**Option A — Pool-owner only recovery.** Add an ownership check so only the pool itself (or its designated owner) can clear its own blacklist entry:

```solidity
function register(bytes32 feedId, address pool, address factory) external payable {
    require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
    require(pool != address(0));
    require(approvedFactories.contains(factory), FactoryNotApproved(factory));
    require(IPoolFactory(factory).isPool(pool), NotAPool(pool));

    if (blacklisted[pool]) {
        require(msg.sender == pool || msg.sender == IPool(pool).owner(), NotAuthorized());
        blacklisted[pool] = false;
        emit BlacklistUpdated(pool, false);
    }

    registeredPool[feedId][pool] = true;
    emit PoolRegistered(feedId, pool, msg.sender, msg.value);
}
```

**Option B — Admin-only unblacklist.** Remove the blacklist-clearing logic from `register()` entirely and require the admin to explicitly call `setBlacklist(pool, false)` before a blacklisted pool can re-register. This gives the admin full, uncontested control over the blacklist lifecycle.

---

### Proof of Concept

```solidity
// 1. Admin blacklists an abusive pool.
oracle.setBlacklist(abusivePool, true);
// abusivePool.swap() now reverts Blacklisted(abusivePool).

// 2. Anyone — including the pool owner or a bot — calls register with 1 wei.
oracle.register{value: 1 wei}(feedId, abusivePool, approvedFactory);
// register() hits the `if (blacklisted[pool])` branch and sets blacklisted[abusivePool] = false.

// 3. Blacklist is gone. abusivePool.swap() succeeds again.
// Admin's enforcement action is completely nullified.
```

The attack cost is `1 wei` (the default `registrationFee`). [5](#0-4) [6](#0-5)

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

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L201-205)
```text
    function register(bytes32 feedId, address pool, address factory) external payable {
        require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
        require(pool != address(0));
        require(approvedFactories.contains(factory), FactoryNotApproved(factory));
        require(IPoolFactory(factory).isPool(pool), NotAPool(pool));
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L207-213)
```text
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

**File:** smart-contracts-poc/contracts/oracles/providers/docs/en/abuse-protection-integration.md (L28-29)
```markdown
- **Economics:** blacklist = access revocation; re-`register` = (paid) redemption. Raise
  `registrationFee` if abusers appear.
```
