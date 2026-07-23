### Title
Permissionless `register()` unconditionally clears pool blacklist, bypassing oracle admin abuse-protection — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

The `register()` function in `OracleBase.sol` is callable by any unprivileged address and, as a documented side-effect, unconditionally clears the blacklist on any pool it registers. Because the registration fee defaults to 1 wei and the only gate is that the pool belongs to an approved factory, any caller can pay 1 wei to re-enable a pool the oracle admin deliberately blacklisted, restoring its ability to read live oracle prices and execute swaps.

---

### Finding Description

`OracleBase.sol::register` is the permissionless paid path that whitelists a `(feedId, pool)` pair for the attributed `price()` read: [1](#0-0) 

The function's only guards are:
1. `msg.value >= registrationFee` (default: **1 wei**, set at construction)
2. `approvedFactories.contains(factory)` — factory must be admin-approved
3. `IPoolFactory(factory).isPool(pool)` — pool must be a real pool from that factory

None of these checks consult the blacklist. If the pool is blacklisted, the function clears it unconditionally before writing `registeredPool[feedId][pool] = true`: [2](#0-1) 

The `price()` read path enforces the blacklist at call time: [3](#0-2) 

Once `register()` clears `blacklisted[pool]`, the `require(!blacklisted[pool])` check at line 167 passes, and the pool can read live oracle prices again — exactly what the admin intended to prevent.

The admin's only tool to revoke a pool's oracle access is `setBlacklist`: [4](#0-3) 

This is an `ADMIN_ROLE`-gated operation. The invariant it enforces — "this pool must not read prices" — is silently destroyed by any unprivileged caller who pays 1 wei.

The analog to the external bug (H-2) is direct: just as an attacker creates an unlisted pool for a listed token to bypass listing validation and siphon rewards, here an attacker re-registers a blacklisted pool for any `feedId` to bypass the admin's abuse-protection and restore oracle read access. In both cases, a per-token/per-feed global action (registration / reward accrual) is performed without validating the legitimacy of the specific pool triggering it.

---

### Impact Explanation

The blacklist is the oracle's primary runtime abuse-protection control. A pool is blacklisted when the admin determines it is abusing the oracle (e.g., price manipulation, sandwich extraction, or other adversarial swap patterns). Bypassing the blacklist allows the pool to:

- Resume reading live oracle prices via `price(feedId, pool)`, enabling continued swap execution.
- Execute swaps at oracle-derived bid/ask quotes that the admin intended to deny, potentially causing bad-price execution for counterparties or LPs.
- Undermine the protocol's ability to respond to detected abuse in real time.

The `registrationFee` is 1 wei by default and is explicitly described as tunable by ADMIN — it is not a security control. The attacker does not need to own or control the pool; any address can call `register` on behalf of any pool.

---

### Likelihood Explanation

Medium. The precondition is that the admin has blacklisted a pool (an active admin response to detected abuse). Once that happens, the bypass is trivially cheap (1 wei), requires no special role, and can be executed in a single transaction by any EOA. The attacker only needs to observe the `BlacklistUpdated` event emitted by `setBlacklist` and respond before the admin takes further action.

---

### Recommendation

Remove the blacklist-clearing side-effect from `register()`. A pool that has been blacklisted by the admin should not be re-enabled by a permissionless payment. If rehabilitation is desired, it should require an explicit admin action:

```solidity
function register(bytes32 feedId, address pool, address factory) external payable {
    require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
    require(pool != address(0));
    require(approvedFactories.contains(factory), FactoryNotApproved(factory));
    require(IPoolFactory(factory).isPool(pool), NotAPool(pool));
    require(!blacklisted[pool], Blacklisted(pool)); // ← add: reject blacklisted pools

    // Remove the blacklist-clearing block entirely.

    registeredPool[feedId][pool] = true;
    emit PoolRegistered(feedId, pool, msg.sender, msg.value);
}
```

If the intent is to allow a pool to pay its way off the blacklist, that path should be a separate, explicitly documented function with its own access controls or a higher fee threshold set by the admin.

---

### Proof of Concept

```
1. Admin detects pool A is abusing the oracle and calls:
       oracle.setBlacklist(poolA, true)
   → blacklisted[poolA] == true

2. Pool A attempts oracle.price(feedId, poolA) → reverts Blacklisted(poolA). ✓

3. Attacker (any EOA) calls:
       oracle.register{value: 1 wei}(anyFeedId, poolA, approvedFactory)
   Preconditions satisfied:
     - msg.value (1 wei) >= registrationFee (1 wei) ✓
     - approvedFactory is admin-approved ✓
     - IPoolFactory(approvedFactory).isPool(poolA) == true (pool A is still a real pool) ✓
   Side-effect fires:
     - blacklisted[poolA] = false ← blacklist cleared

4. Pool A calls oracle.price(feedId, poolA) → succeeds.
   Admin's blacklist decision is nullified.
```

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
