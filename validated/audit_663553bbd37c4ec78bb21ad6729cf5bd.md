### Title
Zero-initialized `confidenceParam` in `PriceProvider` silently ignores oracle spread, producing artificially tight bid/ask prices fed to pools — (`smart-contracts-poc/contracts/PriceProvider.sol`)

---

### Summary

`PriceProvider` zero-initializes `confidenceParam` (the multiplier applied to the oracle's reported spread). Until an explicit `setConfidenceParam` call is made, every freshly deployed provider multiplies the oracle spread by zero, collapsing the bid/ask spread to a value determined solely by the immutable `marginStep` bias. Pools consuming this provider receive quotes that are systematically tighter than the oracle's actual uncertainty warrants, enabling traders to swap at near-mid price while LPs silently absorb the adverse-selection loss.

---

### Finding Description

In `PriceProvider._getBidAndAskPrice()`, the oracle spread is scaled by `confidenceParam` before being applied:

```solidity
// PriceProvider.sol line 216-217
uint256 adjustedSpread = spread * confidenceParam;
(uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
```

`confidenceParam` is a plain storage variable with no constructor argument and no non-zero default:

```solidity
// PriceProvider.sol line 40-41
uint256 public confidenceParam;
uint256 public lastConfidenceUpdate;
```

When `confidenceParam == 0` (the state of every freshly deployed provider), `adjustedSpread = 0`, so `_getBidAskFrom` returns `bid = mid`, `ask = mid`:

```solidity
// PriceProvider.sol line 137-141
function _getBidAskFrom(uint256 midPrice, uint256 confidence) internal pure returns (uint256 bid, uint256 ask) {
    uint256 delta = midPrice * confidence / CONFIDENCE_BASE;
    bid = delta >= midPrice ? 0 : midPrice - delta;
    ask = midPrice + delta;
}
```

The step-adjustment then applies the immutable `marginStep` bias:

- If `marginStep == 0`: `stepBidFactor == stepAskFactor`, so `bidOut == askOut`, the `bidOut >= askOut` guard fires, and `getBidAndAskPrice()` reverts with `FeedStalled()` — **pool is bricked** until `setConfidenceParam` is called.
- If `marginStep > 0` (the common case): `bidOut < askOut`, the function returns a valid quote whose spread is `≈ 2 × marginStep / BPS_BASE_U` — **entirely independent of the oracle's reported spread**.

The oracle's actual uncertainty (e.g., 100 bps during a volatile period) is completely discarded. The pool quotes a spread of, say, 2 bps regardless.

This is structurally identical to the HSG `maxSigners` bug: a parameter that governs a core safety invariant is set once (here: zero at construction, immutable in effect until an admin acts) and, if wrong, causes the system to either halt or produce bad prices. Unlike `AnchoredPriceProvider`, `PriceProvider` has **no band clamp** to catch this — the comment in `AnchoredPriceProvider` explicitly calls the clamp "load-bearing":

```
// AnchoredPriceProvider.sol line 101-103
// That clamp is why marginStep needs no factory envelope bound; it must never be removed.
```

`PriceProvider` has no equivalent safety net.

---

### Impact Explanation

**Bad-price execution / LP principal loss.** When the oracle reports high spread (genuine market uncertainty), the pool still quotes a tight spread determined only by `marginStep`. Traders can swap at near-mid price, extracting value from LPs who are exposed to adverse selection without compensation. If `marginStep == 0`, the pool is completely unusable until an admin intervenes, breaking core swap/withdraw flows.

---

### Likelihood Explanation

Every `PriceProvider` deployment starts with `confidenceParam == 0`. `PriceProviderFactory.createPriceProvider` is permissionless and accepts no `confidenceParam` argument. There is a mandatory 1-minute cooldown between confidence updates, meaning even a vigilant admin cannot atomically deploy-and-configure. Any pool wired to a freshly deployed provider is vulnerable during this window — and indefinitely if the admin never calls `setConfidenceParam`.

---

### Recommendation

1. Add a `_confidenceParam` constructor argument to `PriceProvider` and require it to be non-zero (or within a validated range) at construction, mirroring how `marginStep` and `MAX_TIME_DELTA` are validated.
2. Alternatively, add a pre-use guard in `PriceProviderFactory` (or the pool factory) that rejects providers with `confidenceParam == 0`.
3. At minimum, document that pools must not be activated before `setConfidenceParam` is called, and enforce this with a deployment script check.

---

### Proof of Concept

1. Deploy `PriceProvider` via `PriceProviderFactory.createPriceProvider` with `marginStep = 1e14` (1 bps). `confidenceParam` is 0.
2. Wire the provider to a pool. LPs add liquidity.
3. Oracle reports `spread = 500` (5 bps, high uncertainty). `adjustedSpread = 500 * 0 = 0`.
4. `_getBidAskFrom(mid, 0)` → `bid = mid`, `ask = mid`.
5. After step adjustment: `bidOut ≈ mid × (1 − 1e-4)`, `askOut ≈ mid × (1 + 1e-4)` — a 2 bps spread.
6. Trader swaps at 2 bps spread while oracle uncertainty is 5 bps. LP absorbs the 3 bps adverse-selection gap on every swap.
7. Repeat with `marginStep = 0`: `bidOut == askOut`, `getBidAndAskPrice()` reverts with `FeedStalled()`, pool is bricked. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L40-41)
```text
    uint256 public confidenceParam;
    uint256 public lastConfidenceUpdate;
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L61-89)
```text
    constructor(
        address _factory,
        address _oracle,
        bytes32 _offchainFeedId,
        int256  _marginStep,
        uint256 _maxTimeDelta,
        address _baseToken,
        address _quoteToken
    ) {
        require(_factory != address(0));
        factory = _factory;

        offchainOracle = IOffchainOracle(_oracle);
        offchainFeedId = _offchainFeedId;

        require(_baseToken != address(0) && _quoteToken != address(0) && _baseToken != _quoteToken);
        baseToken = _baseToken;
        quoteToken = _quoteToken;

        if (_marginStep <= -BPS_BASE || _marginStep >= BPS_BASE) {
            revert MarginStepOutOfBounds();
        }
        marginStep       = _marginStep;
        stepBidFactor = uint256(BPS_BASE - _marginStep);
        stepAskFactor = uint256(BPS_BASE + _marginStep);

        if (_maxTimeDelta == 0 || _maxTimeDelta > 7 days) revert MaxTimeDeltaOutOfBounds();
        MAX_TIME_DELTA = _maxTimeDelta;
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L137-141)
```text
    function _getBidAskFrom(uint256 midPrice, uint256 confidence) internal pure returns (uint256 bid, uint256 ask) {
        uint256 delta = midPrice * confidence / CONFIDENCE_BASE;
        bid = delta >= midPrice ? 0 : midPrice - delta;
        ask = midPrice + delta;
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L216-217)
```text
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L226-228)
```text
        // 7. Hard invariant: bid must be strictly less than ask.
        //    Can be violated when marginStep < 0 and confidence is too small.
        if (bidOut >= askOut) return (0, type(uint128).max);
```

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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L99-103)
```text
    ///      quotes the band directly). marginStep can widen OR — for negative values — tighten/invert the
    ///      PRE-clamp shaped quote; what keeps the FINAL quote no tighter than the audited band, for ANY
    ///      marginStep sign, is the load-bearing band clamp in _computeBidAsk (min/max vs refBid/refAsk)
    ///      plus the bidOut>=askOut halt and Floor/Ceil rounding — NOT any monotonicity of marginStep.
    ///      That clamp is why marginStep needs no factory envelope bound; it must never be removed.
```
