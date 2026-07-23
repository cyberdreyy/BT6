### Title
Blacklist Bypass via Unvalidated `feedId` in `register` — (File: `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

`OracleBase.register` clears the abuse-protection blacklist for any legitimate pool as a side effect of a permissionless, 1-wei call, without ever validating that the supplied `feedId` has data in the oracle. An attacker (including the pool operator) can pass an arbitrary `feedId` — including `bytes32(0)` — to unconditionally un-blacklist a pool the admin just flagged, making the blacklist unenforceable.

---

### Finding Description

`register` (line 201) accepts any `bytes32 feedId` with no existence check: [1](#0-0) 

The only guards are:
- `msg.value >= registrationFee` (default **1 wei**)
- `pool != address(0)`
- `approvedFactories.contains(factory)`
- `IPoolFactory(factory).isPool(pool)`

None of these require the `feedId` to have ever received a verified push. The `feedExists` modifier that enforces `oracleData[feedId].timestampMs != 0` is applied only to the read paths (`price`, `integratorPrice`): [2](#0-1) 

Because `register` unconditionally executes the blacklist-clearing branch before writing `registeredPool[feedId][pool] = true`, a caller can obtain the side effect — clearing `blacklisted[pool]` — without any real feed association: [3](#0-2) 

**Attack path:**
1. ADMIN calls `setBlacklist(poolX, true)` to block an abusive pool.
2. Attacker calls `register{value: 1 wei}(bytes32(0), poolX, approvedFactory)`.
3. `blacklisted[poolX]` is set to `false`; `PoolRegistered` is emitted for a non-existent feed.
4. Pool X can now call `price(realFeedId, poolX)` for any `realFeedId` it was previously registered for, because both the `notBlacklisted` modifier on `msg.sender` and the `!blacklisted[pool]` check inside `price` now pass: [4](#0-3) 

The attack can be repeated every time the admin re-blacklists the pool, at 1 wei per bypass.

This is the direct analog to the external report: just as arbitrary pubkeys could be passed to `voluntaryExit` to emit events and corrupt `exitedCount`, an arbitrary `feedId` can be passed to `register` to emit `PoolRegistered` and corrupt the blacklist state — with the additional consequence that the blacklist bypass is immediately exploitable on the live read path.

---

### Impact Explanation

The blacklist is the sole runtime abuse-protection gate in `OracleBase`. A blacklisted pool cannot receive oracle prices; bypassing it allows a pool flagged for abuse to resume reading live bid/ask quotes and executing swaps. If the pool was blacklisted because it was found to be manipulating oracle reads or executing swaps at unauthorized prices, restoring its access leads to **bad-price execution** and potential loss of funds for traders interacting with that pool.

---

### Likelihood Explanation

- **Permissionless**: any EOA can call `register`.
- **Trivial cost**: default `registrationFee` is 1 wei.
- **No special knowledge**: the attacker only needs a legitimate pool address (from an approved factory) and any `bytes32` value as `feedId`.
- **Repeatable**: the admin cannot permanently enforce the blacklist; every re-blacklist can be cleared in the next block for 1 wei. [5](#0-4) 

---

### Recommendation

Add a `feedExists` guard to `register`, or decouple blacklist clearing from the permissionless registration path so it requires explicit admin action:

```solidity
function register(bytes32 feedId, address pool, address factory) external payable {
    require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
    require(pool != address(0));
    require(approvedFactories.contains(factory), FactoryNotApproved(factory));
    require(IPoolFactory(factory).isPool(pool), NotAPool(pool));
+   require(TimeMs.unwrap(oracleData[feedId].timestampMs) != 0, FeedNotFound(feedId));

    if (blacklisted[pool]) {
        blacklisted[pool] = false;
        emit BlacklistUpdated(pool, false);
    }

    registeredPool[feedId][pool] = true;
    emit PoolRegistered(feedId, pool, msg.sender, msg.value);
}
```

---

### Proof of Concept

```solidity
// Setup: admin blacklists pool for abuse
oracle.setBlacklist(pool, true);
assertEq(oracle.blacklisted(pool), true);

// Attacker bypasses blacklist with non-existent feedId (bytes32(0) has no data)
oracle.register{value: 1 wei}(bytes32(0), pool, approvedFactory);

// Pool is no longer blacklisted — 1 wei cost
assertEq(oracle.blacklisted(pool), false);

// Pool can now read prices for any feedId it was previously registered for:
// pool.swap() -> provider.getBidAndAskPrice() -> oracle.price(realFeedId, pool)
// Both `notBlacklisted` and `!blacklisted[pool]` now pass.
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L53-53)
```text
        registrationFee = 1 wei; // very cheap default; ADMIN tunes via setRegistrationFee
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L57-61)
```text
    modifier feedExists(bytes32 feedId) {
        require(TimeMs.unwrap(oracleData[feedId].timestampMs) != 0, FeedNotFound(feedId));

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
