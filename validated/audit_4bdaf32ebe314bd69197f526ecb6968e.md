### Title
Missing Contract/Interface Validation on `source` in `AnchoredPriceProvider.setSource()` Causes Permanent Swap DoS on Any Pool Using the Provider — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

`AnchoredPriceProvider.setSource()` accepts any address as the new `source` without verifying it is a deployed contract implementing `IAnchorSource.getBidAndAskPrice()`. When a non-implementing address is stored, every subsequent call to `getBidAndAskPrice()` reverts with `FeedStalled`, permanently breaking swaps on every pool that uses this provider until the source is corrected.

---

### Finding Description

`AnchoredProviderFactory.setSource()` is callable by any `providerOwner` — a permissionless role granted to whoever called `createAnchoredProvider`. It forwards directly to `AnchoredPriceProvider.setSource()`:

```solidity
// AnchoredProviderFactory.sol
function setSource(address provider, address newSource) external override onlyProviderOwner(provider) {
    require(_providers.contains(provider), ProviderNotTracked());
    AnchoredPriceProvider(provider).setSource(newSource);   // no validation of newSource
    emit SourceSet(provider, newSource);
}
``` [1](#0-0) 

```solidity
// AnchoredPriceProvider.sol
function setSource(address newSource) external {
    require(msg.sender == factory, OnlyFactory());
    source = newSource;          // stored with zero validation
    emit SourceSet(newSource);
}
``` [2](#0-1) 

At swap time, `_computeBidAsk` branches into `_readSource` whenever `source != address(0)`:

```solidity
if (_source != address(0)) {
    bool ok;
    (ok, cBid, cAsk) = _readSource(_source);
    if (!ok) {
        return (0, type(uint128).max);   // fail-closed sentinel
    }
}
``` [3](#0-2) 

`_readSource` performs a gas-bounded `staticcall` and requires exactly 64 bytes of returndata:

```solidity
success := staticcall(SOURCE_GAS_LIMIT, _source, ptr, 0x04, ptr, 0x40)
retSize := returndatasize()
...
if (!success || retSize != 64) return (false, 0, 0);
``` [4](#0-3) 

When `_source` is an EOA, `staticcall` returns `success = true` but `returndatasize() = 0`, so `retSize != 64` triggers and `_readSource` returns `(false, 0, 0)`. When `_source` is a contract that does not implement `getBidAndAskPrice`, the call reverts and `success = false`. In both cases `_computeBidAsk` returns the `(0, type(uint128).max)` sentinel, and the outer `getBidAndAskPrice()` reverts with `FeedStalled`:

```solidity
function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
    (bid, ask) = _getBidAndAskPrice();
    if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
}
``` [5](#0-4) 

Every pool swap that calls this provider reverts for as long as the bad source remains set.

---

### Impact Explanation

All swaps on every pool whose `priceProvider` is the affected `AnchoredPriceProvider` are permanently broken. The pool is not insolvent — LPs can still withdraw — but the core swap flow is completely unusable. This matches the allowed impact: **"Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows."**

---

### Likelihood Explanation

`createAnchoredProvider` is permissionless; any user becomes `providerOwner` of their deployed provider. A provider created by the factory is immediately eligible for use in public pools (`isProvider()` returns true). The provider owner can call `setSource` at any time with an arbitrary address — including an EOA or a contract that does not implement `IAnchorSource` — with no on-chain guard preventing it. The trigger requires only a single transaction from the provider owner, with no special preconditions.

---

### Recommendation

In both `AnchoredPriceProvider.setSource()` and `AnchoredProviderFactory.setSource()`, validate the new source before storing it:

1. **Contract existence check**: `require(newSource.code.length > 0 || newSource == address(0))` — allows zero (reference mode) but rejects EOAs.
2. **Interface probe**: perform a bounded `staticcall` to `IAnchorSource.getBidAndAskPrice.selector` on `newSource` and require it returns exactly 64 bytes with a valid (non-zero, non-inverted) bid/ask pair before accepting the address.

This mirrors the recommendation in the external report: validate that the target address is a contract implementing the required interface before storing it.

---

### Proof of Concept

1. Alice calls `AnchoredProviderFactory.createAnchoredProvider(...)` — she becomes `providerOwner`.
2. A public pool is configured to use Alice's provider.
3. Alice calls `AnchoredProviderFactory.setSource(provider, aliceEOA)` where `aliceEOA` is a plain wallet address.
4. Any user calls `pool.swap(...)`.
5. The pool calls `provider.getBidAndAskPrice()`.
6. `_readSource(aliceEOA)` → `staticcall` succeeds but `returndatasize() == 0 != 64` → returns `(false, 0, 0)`.
7. `_computeBidAsk` returns `(0, type(uint128).max)`.
8. `getBidAndAskPrice()` reverts with `FeedStalled`.
9. All swaps on the pool revert until Alice (or a new owner) calls `setSource` again with a valid address. [6](#0-5) [1](#0-0)

### Citations

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L224-228)
```text
    function setSource(address provider, address newSource) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        AnchoredPriceProvider(provider).setSource(newSource);
        emit SourceSet(provider, newSource);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L178-182)
```text
    function setSource(address newSource) external {
        require(msg.sender == factory, OnlyFactory());
        source = newSource;
        emit SourceSet(newSource);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L320-327)
```text
        if (_source != address(0)) {
            // 7a. Source mode: any failure (revert, OOG, garbage, zero, inverted) halts — fail
            //     closed. Knobs do NOT post-process the source output (the source shapes itself).
            bool ok;
            (ok, cBid, cAsk) = _readSource(_source);
            if (!ok) {
                return (0, type(uint128).max);
            }
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
