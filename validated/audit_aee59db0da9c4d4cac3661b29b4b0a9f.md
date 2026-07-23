### Title
`confidenceParam` Zero-Initialization Silences Oracle Spread, Enabling Artificially Tight Bid/Ask Execution — (`smart-contracts-poc/contracts/PriceProvider.sol`)

---

### Summary

`PriceProvider` zero-initializes `confidenceParam` and imposes no requirement — in the constructor, factory, or any deployment path — that it be set to a non-zero value before the pool accepts swaps. When `confidenceParam == 0`, the oracle's reported spread (its uncertainty signal) is multiplied to zero, collapsing the bid/ask spread to a value derived solely from the immutable `marginStep` bias. The pool then executes swaps at prices that are blind to oracle uncertainty, exposing LP principal to adverse selection whenever the oracle's actual spread is non-trivial.

---

### Finding Description

In `_getBidAndAskPrice()`, the oracle spread is scaled by `confidenceParam` before being used to compute the bid/ask delta:

```solidity
// PriceProvider.sol line 216-217
uint256 adjustedSpread = spread * confidenceParam;
(uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
```

`_getBidAskFrom` computes:

```solidity
// PriceProvider.sol line 138-141
uint256 delta = midPrice * confidence / CONFIDENCE_BASE;
bid = delta >= midPrice ? 0 : midPrice - delta;
ask = midPrice + delta;
```

When `confidenceParam == 0` (the Solidity default, never overridden at construction):

- `adjustedSpread = spread * 0 = 0`
- `delta = mid * 0 / CONFIDENCE_BASE = 0`
- `bid = mid`, `ask = mid`

The only spread that survives is the immutable `marginStep` bias applied in steps 6–7:

```solidity
// PriceProvider.sol lines 220-228
(uint256 bidOut, bool bidOk) = _applyBidAdjustments(bid);   // mid × (1 − marginStep)
(uint256 askOut, bool askOk) = _applyAskAdjustments(ask);   // mid × (1 + marginStep)
if (bidOut >= askOut) return (0, type(uint128).max);
```

So the live bid/ask spread is `2 × marginStep / BPS_BASE`, regardless of what the oracle reports as its uncertainty. If the oracle signals a 5% spread (high uncertainty, e.g. a volatile market), the pool still quotes at `2 × marginStep`, which may be 0.1% or less.

The constructor enforces no lower bound on `confidenceParam`:

```solidity
// PriceProvider.sol lines 61-89 (constructor)
// No confidenceParam initialization or requirement
```

`setConfidenceParam` only enforces an upper bound and a cooldown:

```solidity
// PriceProvider.sol lines 92-104
if (newValue > CONFIDENCE_MAX) revert ConfidenceParamOutOfBounds();
if (block.timestamp < lastConfidenceUpdate + CONFIDENCE_COOLDOWN) revert CooldownNotElapsed();
```

Zero is explicitly accepted. There is no factory-level or deployment-level gate that forces `confidenceParam > 0` before the pool opens.

This is the direct analog to the external report: just as withdrawal penalties of 0 remove the economic deterrent against price manipulation, `confidenceParam == 0` removes the oracle-uncertainty-based spread that protects LP principal against adverse selection.

---

### Impact Explanation

- **Bad-price execution**: The pool quotes a spread that is structurally narrower than the oracle's own uncertainty signal. Swaps execute at prices the oracle itself considers unreliable.
- **LP principal loss**: Arbitrageurs can profitably trade against the artificially tight spread whenever the true market price deviates by more than `marginStep` but less than `marginStep + oracle_spread`. LPs absorb the difference.
- **No self-correction**: Because `confidenceParam` is not set at construction and the cooldown is 1 minute, there is a guaranteed window — potentially indefinite if the factory never calls `setConfidenceParam` — during which the pool is live with zero oracle-spread protection.

---

### Likelihood Explanation

Medium. Pool creators or factory operators may deploy `PriceProvider` without ever calling `setConfidenceParam`, either by oversight (the parameter is not required) or because the zero default appears harmless. The `marginStep` spread gives a false sense of security. The condition is reachable by any valid deployment of `PriceProvider` without any privileged or malicious action — it is the default state.

---

### Recommendation

Require `confidenceParam > 0` at construction, or require the factory to set it before the pool is registered as active. For example, add to the constructor:

```solidity
require(_confidenceParam > 0 && _confidenceParam <= CONFIDENCE_MAX, "confidenceParam out of bounds");
confidenceParam = _confidenceParam;
lastConfidenceUpdate = block.timestamp;
```

Alternatively, enforce in the factory's pool-registration path that `provider.confidenceParam() > 0` before the pool is allowed to accept swaps.

---

### Proof of Concept

1. Factory deploys `PriceProvider` with any valid `marginStep > 0` and never calls `setConfidenceParam`. `confidenceParam` remains 0.
2. Oracle reports `mid = 1e8` (price = 1.00), `spread = 500` (5% uncertainty).
3. Pool calls `getBidAndAskPrice()`.
4. `adjustedSpread = 500 * 0 = 0`. `delta = 0`. `bid = ask = 1e8`.
5. With `marginStep = 1e15` (0.1% in BPS_BASE_U scale): `bidOut ≈ 1e8 × 0.999`, `askOut ≈ 1e8 × 1.001`. Spread = 0.2%.
6. Oracle's actual 5% uncertainty is invisible to the pool. An arbitrageur who observes the true price at 1.03 (within oracle uncertainty) trades against the 0.2% spread, extracting ~2.8% from LP reserves per round trip, with no economic penalty. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L92-104)
```text
    function setConfidenceParam(uint256 newValue) external {
        require(msg.sender == factory, OnlyFactory());
        if (newValue > CONFIDENCE_MAX) {
            revert ConfidenceParamOutOfBounds();
        }
        if (block.timestamp < lastConfidenceUpdate + CONFIDENCE_COOLDOWN) {
            revert CooldownNotElapsed();
        }

        confidenceParam = newValue;
        lastConfidenceUpdate = block.timestamp;
        emit ConfidenceParamSet(newValue);
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

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L220-228)
```text
        (uint256 bidOut, bool bidOk) = _applyBidAdjustments(bid);
        if (!bidOk || bidOut > type(uint128).max) return (0, type(uint128).max);

        (uint256 askOut, bool askOk) = _applyAskAdjustments(ask);
        if (!askOk || askOut > type(uint128).max) return (0, type(uint128).max);

        // 7. Hard invariant: bid must be strictly less than ask.
        //    Can be violated when marginStep < 0 and confidence is too small.
        if (bidOut >= askOut) return (0, type(uint128).max);
```
