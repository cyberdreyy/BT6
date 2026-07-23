### Title
Single-Step `transferProviderOwnership` Permanently Locks `confidenceParam` Control, Stalling Pool Swaps — (`File: smart-contracts-poc/contracts/PriceProviderFactory.sol`)

---

### Summary

`PriceProviderFactory.transferProviderOwnership` transfers provider ownership in a single atomic step. If the `newOwner` address is wrong (typo, dead wallet, uncontrolled contract), the original owner immediately and permanently loses the ability to call `setConfidence`, which is the only path to set `confidenceParam` on a `PriceProvider`. A `PriceProvider` with `confidenceParam == 0` (the default) always returns the stall sentinel `(0, type(uint128).max)`, making every swap on any pool using that provider revert with `FeedStalled()`.

---

### Finding Description

`PriceProviderFactory.transferProviderOwnership` performs an immediate, unconditional ownership reassignment: [1](#0-0) 

```solidity
function transferProviderOwnership(address provider, address newOwner)
    external override onlyProviderOwner(provider)
{
    require(_providers.contains(provider), ProviderNotTracked());
    require(newOwner != address(0));
    address previousOwner = providerOwner[provider];

    providerOwner[provider] = newOwner;          // ← immediate, no acceptance step
    _providersByCreator[previousOwner].remove(provider);
    _providersByCreator[newOwner].add(provider);

    emit ProviderOwnershipTransferred(provider, previousOwner, newOwner);
}
```

There is no `pendingOwner` staging or `acceptOwnership` confirmation. The moment the transaction lands, the old owner is stripped of all authority.

The provider owner is the only address that satisfies `_requireUpdater`: [2](#0-1) 

`setConfidence` is the sole path to write `confidenceParam` on a `PriceProvider`: [3](#0-2) 

`confidenceParam` is zero-initialized (Solidity default). With `confidenceParam == 0`, the spread computation in `PriceProvider._getBidAndAskPrice` collapses: [4](#0-3) 

```solidity
uint256 adjustedSpread = spread * confidenceParam;   // == 0
(uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
// delta = 0 → bid = mid, ask = mid
```

With `bid == ask` (and `marginStep == 0` → `stepBidFactor == stepAskFactor`), the step adjustment produces `bidOut == askOut`, triggering: [5](#0-4) 

```solidity
if (bidOut >= askOut) return (0, type(uint128).max);
```

`getBidAndAskPrice` then reverts with `FeedStalled()`: [6](#0-5) 

The codebase already implements the correct two-step pattern for `stateGuard` transfers (`setPendingStateGuardRole` → `acceptStateGuardRole`): [7](#0-6) 

`transferProviderOwnership` is the only privileged transfer that skips this pattern.

---

### Impact Explanation

Any pool whose `IPriceProvider` is a `PriceProvider` with `confidenceParam == 0` (never set, or ownership lost before it was set) will have every swap revert with `FeedStalled()`. Because the provider owner is permanently lost, `confidenceParam` can never be raised, and the pool is permanently bricked for swaps. This matches the allowed impact: **broken core pool functionality / unusable swap flows**.

---

### Likelihood Explanation

Low. It requires the current provider owner to call `transferProviderOwnership` with an incorrect `newOwner` address (typo, dead wallet, undeployed contract). This is the same likelihood class as M-03 in the external report.

---

### Recommendation

Apply the same two-step pattern already used for `stateGuard` transfers:

1. Add a `mapping(address => address) public pendingProviderOwner` in `PriceProviderFactory`.
2. Replace the direct assignment in `transferProviderOwnership` with `pendingProviderOwner[provider] = newOwner`.
3. Add `acceptProviderOwnership(address provider)` that requires `msg.sender == pendingProviderOwner[provider]` before committing the change.

---

### Proof of Concept

1. Alice deploys a `PriceProvider` via `PriceProviderFactory.createPriceProvider`. `confidenceParam` is 0 (default). Alice is `providerOwner[provider]`.
2. Alice calls `transferProviderOwnership(provider, 0xDEAD...)` with a mistyped address. The call succeeds immediately; `providerOwner[provider] == 0xDEAD...`.
3. Alice (or anyone) calls `setConfidence([provider], [nonZeroValue])`. `_requireUpdater` checks `msg.sender != providerOwner[provider]` and `!isUpdater[provider][msg.sender]` → reverts `NotProviderUpdater()`.
4. The pool calls `getBidAndAskPrice()` → `_getBidAndAskPrice()` → `adjustedSpread = spread * 0 = 0` → `bidOut == askOut` → returns `(0, type(uint128).max)` → `FeedStalled()`. Every swap on the pool reverts permanently.

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L34-37)
```text
    function _requireUpdater(address provider) internal view {
        if (msg.sender != providerOwner[provider] && !isUpdater[provider][msg.sender])
            revert NotProviderUpdater();
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L92-102)
```text
    function transferProviderOwnership(address provider, address newOwner) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        require(newOwner != address(0));
        address previousOwner = providerOwner[provider];

        providerOwner[provider] = newOwner;
        _providersByCreator[previousOwner].remove(provider);
        _providersByCreator[newOwner].add(provider);

        emit ProviderOwnershipTransferred(provider, previousOwner, newOwner);
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L130-142)
```text
    function setConfidence(
        address[] calldata providers,
        uint256[] calldata values
    ) external override {
        uint256 l = providers.length;
        if (l != values.length) revert LengthMismatch();

        for (uint256 i; i < l; ++i) {
            require(_providers.contains(providers[i]), ProviderNotTracked());
            _requireUpdater(providers[i]);
            PriceProvider(providers[i]).setConfidenceParam(values[i]);
        }
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L115-120)
```text
    function getBidAndAskPrice()
        external override returns (uint128 bid, uint128 ask)
    {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L215-228)
```text
        //    confidenceParam multiplies oracle spread; 0 means no spread
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);

        // 6. Apply marginStep adjustment
        (uint256 bidOut, bool bidOk) = _applyBidAdjustments(bid);
        if (!bidOk || bidOut > type(uint128).max) return (0, type(uint128).max);

        (uint256 askOut, bool askOk) = _applyAskAdjustments(ask);
        if (!askOk || askOut > type(uint128).max) return (0, type(uint128).max);

        // 7. Hard invariant: bid must be strictly less than ask.
        //    Can be violated when marginStep < 0 and confidence is too small.
        if (bidOut >= askOut) return (0, type(uint128).max);
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L99-118)
```text
    function setStateGuardRole(bytes32 feedId, address newGuard) external checkRole(feedId) {
        pendingStateGuard[feedId] = newGuard;

        emit StateGuardPending(feedId, newGuard);
    }

    function purgePendingStateGuardRole(bytes32 feedId) external checkRole(feedId) {
        delete pendingStateGuard[feedId];

        emit PendingStateGuardDeleted(feedId);
    }

    function acceptStateGuardRole(bytes32 feedId) external {
        require(pendingStateGuard[feedId] == msg.sender, InvalidGuard(msg.sender));

        delete pendingStateGuard[feedId];
        stateGuard[feedId] = msg.sender;

        emit StateGuardUpdated(feedId, msg.sender);
    }
```
