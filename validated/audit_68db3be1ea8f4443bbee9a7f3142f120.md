### Title
Permissionless `register` Unconditionally Clears Admin Blacklist, Allowing Any Blacklisted Pool to Bypass Oracle Abuse Protection — (File: `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

The `register` function in the providers `OracleBase` is permissionless and, as a documented side effect, clears the `blacklisted` flag for any pool that pays the registration fee (default: 1 wei) and is recognized by an approved factory. Because the pool operator is the actor most motivated to bypass the blacklist, and the fee is trivially cheap, the admin's only mechanism to halt an abusive pool's oracle access is completely ineffective.

---

### Finding Description

`OracleBase` (providers) maintains a `blacklisted` mapping as its abuse-protection gate. The admin sets it via `setBlacklist`. The `price(feedId, pool)` path enforces it with two checks:

```solidity
// OracleBase.sol – price()
modifier notBlacklisted() {
    require(!blacklisted[msg.sender], Blacklisted(msg.sender));
    _;
}
...
require(!blacklisted[pool], Blacklisted(pool));
``` [1](#0-0) [2](#0-1) 

However, the permissionless `register` function unconditionally clears the blacklist as a side effect of registration:

```solidity
// OracleBase.sol – register()
function register(bytes32 feedId, address pool, address factory) external payable {
    require(msg.value >= registrationFee, ...);   // default: 1 wei
    require(pool != address(0));
    require(approvedFactories.contains(factory), ...);
    require(IPoolFactory(factory).isPool(pool), ...);

    if (blacklisted[pool]) {
        blacklisted[pool] = false;          // ← unconditional blacklist clear
        emit BlacklistUpdated(pool, false);
    }

    registeredPool[feedId][pool] = true;
    ...
}
``` [3](#0-2) 

The only preconditions are:
1. Pay `registrationFee` (hardcoded default: `1 wei`)
2. Supply an `approvedFactory` address
3. The factory's `isPool(pool)` returns `true`

A pool that was blacklisted for abuse is still a legitimate pool from an approved factory — it was deployed by that factory. The pool operator (or any third party) can therefore call `register(anyFeedId, blacklistedPool, factory)` with 1 wei and immediately clear the blacklist. The admin has no recourse short of removing the entire factory from `approvedFactories`, which would collaterally block every other pool from that factory. [4](#0-3) 

---

### Impact Explanation

A blacklisted pool can bypass the admin's oracle abuse protection and resume reading live prices through the `price(feedId, pool)` path consumed by `AnchoredPriceProvider.getBidAndAskPrice()`. This re-enables swap execution against oracle data the admin explicitly revoked, constituting a broken admin-boundary invariant. Any swap executed by the re-enabled pool receives a live oracle quote it should not receive, which can cause bad-price execution or continued protocol abuse. [5](#0-4) [6](#0-5) 

---

### Likelihood Explanation

**High.** The attack costs 1 wei (the hardcoded default `registrationFee`). It is permissionless — any EOA can call `register`. The pool operator is the actor most directly motivated to clear their own blacklist. No off-chain coordination, no privileged role, and no timelock is required. Even if the admin raises `registrationFee`, the pool operator's incentive to bypass the blacklist far exceeds any realistic fee. [7](#0-6) 

---

### Recommendation

Remove the blacklist-clearing side effect from `register`. Blacklist management must be exclusively controlled by the admin via `setBlacklist`. If the protocol intends a "pay to un-blacklist" escape hatch, it should be a separate, explicit function gated on `ADMIN_ROLE` approval, not a silent side effect of a permissionless registration call.

```solidity
function register(bytes32 feedId, address pool, address factory) external payable {
    require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
    require(pool != address(0));
    require(approvedFactories.contains(factory), FactoryNotApproved(factory));
    require(IPoolFactory(factory).isPool(pool), NotAPool(pool));
+   require(!blacklisted[pool], Blacklisted(pool)); // refuse registration of blacklisted pools

-   if (blacklisted[pool]) {
-       blacklisted[pool] = false;
-       emit BlacklistUpdated(pool, false);
-   }

    registeredPool[feedId][pool] = true;
    emit PoolRegistered(feedId, pool, msg.sender, msg.value);
}
```

---

### Proof of Concept

1. Admin calls `setBlacklist(pool, true)` — pool is blocked from reading oracle prices.
2. Pool operator calls `register(anyFeedId, pool, approvedFactory)` with 1 wei.
3. Inside `register`: `blacklisted[pool]` is `true`, so the branch executes `blacklisted[pool] = false`.
4. Pool operator calls `pool.swap(...)` → `AnchoredPriceProvider.getBidAndAskPrice()` → `oracle.price(feedId, pool)`.
5. Both `notBlacklisted` (on `msg.sender` = provider) and `require(!blacklisted[pool])` now pass.
6. The pool receives a live oracle quote and executes the swap — the admin's blacklist is fully bypassed. [8](#0-7) [3](#0-2)

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

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L76-80)
```text
    modifier notBlacklisted() {
        require(!blacklisted[msg.sender], Blacklisted(msg.sender));

        _;
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-295)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

        // Basic validity — mid positive, spreadBps not the stalled/off-hours marker (the Chainlink oracle
        // writes spreadBps = ORACLE_BPS when an RWA market is closed).
        if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);

        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

        ok = true;
    }
```
