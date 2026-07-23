### Title
Blacklisted pool self-clears via `register` with a non-existent `feedId` - (File: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol)

### Summary
`OracleBase.register` unconditionally clears a pool's blacklist entry whenever `msg.value >= registrationFee`, but never verifies that the supplied `feedId` has ever received a verified push. A blacklisted pool can pay the trivially-small registration fee (default 1 wei) against a fabricated, never-pushed `feedId` to erase its blacklist entry, then immediately re-register against a live `feedId` and resume reading oracle prices.

### Finding Description
`OracleBase.register` performs four checks before writing state: [1](#0-0) 

1. `msg.value >= registrationFee`
2. `pool != address(0)`
3. `approvedFactories.contains(factory)`
4. `IPoolFactory(factory).isPool(pool)`

None of these checks verify that `feedId` corresponds to a feed that has ever been pushed (i.e., `oracleData[feedId].timestampMs != 0`). The function then unconditionally clears the pool's blacklist entry and sets `registeredPool[feedId][pool] = true`. Because `feedId` is a free `bytes32` parameter, any caller can supply an arbitrary value that

### Citations

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
