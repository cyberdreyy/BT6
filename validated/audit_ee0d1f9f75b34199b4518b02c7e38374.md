### Title
Updater Role Is Not Revoked During Provider Ownership Transfer — (`smart-contracts-poc/contracts/PriceProviderFactory.sol`, `PriceProviderFactoryL2.sol`, `AnchoredProviderFactory.sol`)

### Summary

`transferProviderOwnership` in all three factory contracts updates `providerOwner` but never clears the `isUpdater[provider][*]` mapping. Any updater address granted by the previous owner retains the ability to call `setConfidence` and manipulate `confidenceParam` on the transferred provider indefinitely. The new owner has no way to enumerate stale updaters because `isUpdater` is a plain nested mapping with no enumerable set.

### Finding Description

All three factory contracts share the same pattern. `grantUpdater` writes `isUpdater[provider][updater] = true` and `_requireUpdater` passes if either `msg.sender == providerOwner[provider]` OR `isUpdater[provider][msg.sender]` is true. [1](#0-0) 

`transferProviderOwnership` only updates `providerOwner` and the `_providersByCreator` sets. It never touches `isUpdater`: [2](#0-1) 

The identical gap exists in `PriceProviderFactoryL2`: [3](#0-2) 

And in `AnchoredProviderFactory`: [4](#0-3) 

The `isUpdater` storage is a plain nested mapping with no enumerable set: [5](#0-4) 

The new owner cannot discover which addresses were granted updater access by the previous owner, so they cannot revoke them. The existing test suite confirms the gap: `testOldOwnerCannotUpdateAfterTransfer` verifies the old *owner* is blocked, but there is no corresponding test for old *updaters* after transfer. [6](#0-5) 

### Impact Explanation

`confidenceParam` directly controls the bid/ask spread emitted by `PriceProvider._getBidAndAskPrice()`: [7](#0-6) 

A stale updater calling `setConfidence([provider], [0])` sets `confidenceParam = 0`. With `marginStep = 0` this makes `bid == ask` after step adjustment, triggering the hard invariant guard: [8](#0-7) 

The provider returns `(0, type(uint128).max)`, causing `getBidAndAskPrice()` to revert with `FeedStalled`: [9](#0-8) 

Every pool swap that routes through this provider reverts. The pool's swap path calls `getBidAndAskPrice()` at execution time, so the stall blocks all swaps for the affected pool until the new owner discovers and revokes the stale updater — which requires knowing the address off-chain.

For `AnchoredPriceProvider` (mutable variant), setting `confidenceParam` to `CONFIDENCE_MAX` (1,000,000) can make `delta >= mid`, forcing `bid8 = 0`, which causes `_shapedQuote` to return `(false, 0, 0)` and the provider to emit the fail-closed sentinel: [10](#0-9) 

### Likelihood Explanation

The trigger requires: (1) the previous owner to have granted at least one updater before transferring, and (2) that updater to act adversarially after the transfer. This is a semi-trusted actor scenario — the updater was trusted by the previous owner, not by the new owner. Provider ownership transfer is an explicit, documented operation (`transferProviderOwnership` is a named public function), so the scenario is reachable in normal protocol operation (e.g., a curator selling or delegating a provider). The 1-minute `CONFIDENCE_COOLDOWN` limits the rate of manipulation but does not prevent it.

### Recommendation

Clear all updater grants atomically inside `transferProviderOwnership`. Because `isUpdater` is a plain mapping with no enumerable set, the cleanest fix is to replace it with an `EnumerableSet.AddressSet` per provider, then iterate and clear on transfer. Alternatively, introduce a per-provider `updaterEpoch` nonce and include it in the `_requireUpdater` check so that a single epoch increment on transfer invalidates all prior grants without enumeration.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

// Foundry test demonstrating stale updater retains write access after transfer.
// Run against PriceProviderFactory.

function testStaleUpdaterAfterTransfer() public {
    // 1. Owner creates a provider and grants an updater.
    address provider = factory.createPriceProvider(oracle, feedId, 0, 1 days, baseToken, quoteToken);
    address staleUpdater = address(0xBAD);
    factory.grantUpdater(provider, staleUpdater);

    // 2. Owner transfers the provider to a new owner.
    address newOwner = address(0xNEW);
    factory.transferProviderOwnership(provider, newOwner);

    // 3. staleUpdater is no longer authorized by the new owner,
    //    but isUpdater[provider][staleUpdater] is still true.
    assertTrue(factory.isUpdater(provider, staleUpdater)); // ← survives transfer

    // 4. staleUpdater sets confidenceParam to 0, stalling the provider.
    address[] memory providers = new address[](1);
    providers[0] = provider;
    uint256[] memory values = new uint256[](1);
    values[0] = 0;

    vm.prank(staleUpdater);
    factory.setConfidence(providers, values); // ← succeeds; should revert

    // 5. Pool swap now reverts with FeedStalled.
    vm.expectRevert(PriceProvider.FeedStalled.selector);
    vm.prank(address(pool));
    PriceProvider(provider).getBidAndAskPrice();
}
```

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L19-20)
```text
    mapping(address provider => address) public providerOwner;
    mapping(address provider => mapping(address updater => bool)) public isUpdater;
```

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

**File:** smart-contracts-poc/contracts/PriceProviderFactoryL2.sol (L95-105)
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

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L230-240)
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

**File:** smart-contracts-poc/test/PriceProviderFactory.t.sol (L322-333)
```text
    function testOldOwnerCannotUpdateAfterTransfer() public {
        address p = _create(FEED_A);
        factory.transferProviderOwnership(p, creatorB);

        address[] memory providers = new address[](1);
        providers[0] = p;
        uint256[] memory values = new uint256[](1);
        values[0] = 500_000;

        vm.expectRevert(IPriceProviderFactory.NotProviderUpdater.selector);
        factory.setConfidence(providers, values);
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

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L215-218)
```text
        //    confidenceParam multiplies oracle spread; 0 means no spread
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);

```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L226-229)
```text
        // 7. Hard invariant: bid must be strictly less than ask.
        //    Can be violated when marginStep < 0 and confidence is too small.
        if (bidOut >= askOut) return (0, type(uint128).max);

```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L360-372)
```text
    function _shapedQuote(uint256 mid, uint256 spreadBps)
        internal view returns (bool ok, uint256 sBid, uint256 sAsk)
    {
        uint256 delta = mid * (spreadBps * confidenceParam) / CONFIDENCE_BASE;
        uint256 bid8 = delta >= mid ? 0 : mid - delta;
        uint256 ask8 = mid + delta;

        sBid = _bandEdge(bid8, stepBidFactor, Math.Rounding.Floor);
        sAsk = _bandEdge(ask8, stepAskFactor, Math.Rounding.Ceil);
        if (sBid == 0 || sAsk > type(uint128).max) return (false, 0, 0);

        return (true, sBid, sAsk);
    }
```
