### Title
Single-Step `transferProviderOwnership` Permanently Bricks Source and Updater Management — (`smart-contracts-poc/contracts/PriceProviderFactory.sol`, `PriceProviderFactoryL2.sol`, `AnchoredProviderFactory.sol`)

### Summary

`transferProviderOwnership` in all three factory contracts is a single-step transfer protected only by a zero-address check. If the caller supplies a wrong address (typo, inaccessible contract, burned address), the `providerOwner` slot is immediately and irrecoverably overwritten. There is no pending-owner pattern and no acceptance step.

### Finding Description

`PriceProviderFactory.transferProviderOwnership`, `PriceProviderFactoryL2.transferProviderOwnership`, and `AnchoredProviderFactory.transferProviderOwnership` all share the same pattern:

```solidity
function transferProviderOwnership(address provider, address newOwner)
    external override onlyProviderOwner(provider)
{
    require(_providers.contains(provider), ProviderNotTracked());
    require(newOwner != address(0));          // only guard
    address previousOwner = providerOwner[provider];
    providerOwner[provider] = newOwner;       // immediate, irrevocable
    ...
}
``` [1](#0-0) [2](#0-1) [3](#0-2) 

The `providerOwner` role is the exclusive gatekeeper for:

| Factory | Gated capability |
|---|---|
| `PriceProviderFactory` / `PriceProviderFactoryL2` | `grantUpdater`, `revokeUpdater` → controls who may call `setConfidence` (shapes `confidenceParam`) |
| `AnchoredProviderFactory` | `setSource` → swaps the bid/ask source contract wired to every pool using this provider | [4](#0-3) [5](#0-4) 

### Impact Explanation

**`AnchoredProviderFactory` path (most severe):** `setSource` is the only way to replace the bid/ask source contract attached to an `AnchoredPriceProvider`. If ownership is transferred to an inaccessible address, the source is permanently frozen. If the frozen source is a broken or adversarially-shaped contract that consistently returns prices at the extreme edges of the reference band, every swap through pools wired to that provider executes at the worst permitted bid or ask. The reference clamp prevents prices from going *outside* the band, but prices pinned to the band edge are still materially wrong relative to the fair mid-price, causing LP value leakage on every swap. [6](#0-5) 

**`PriceProviderFactory` / `PriceProviderFactoryL2` path:** `grantUpdater`/`revokeUpdater` become permanently inaccessible. A compromised or stale updater retains the ability to call `setConfidence` indefinitely, and a legitimate replacement updater can never be granted. `confidenceParam` is bounded by `CONFIDENCE_MAX` but can still skew quotes within that range. [7](#0-6) 

### Likelihood Explanation

The `providerOwner` is a semi-trusted, permissionless role — any address that calls `createProvider` / `createAnchoredProvider` becomes the owner. There is no DAO or multisig requirement. A single-character typo in the `newOwner` argument during a routine ownership handoff permanently bricks the provider. The zero-address guard does not protect against any other wrong address.

### Recommendation

Implement a two-step transfer identical to the pattern already used for `stateGuard` in `OracleBase`:

1. `transferProviderOwnership(provider, newOwner)` → writes `pendingProviderOwner[provider] = newOwner`.
2. `acceptProviderOwnership(provider)` → requires `msg.sender == pendingProviderOwner[provider]`, then finalises the transfer. [8](#0-7) 

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import "forge-std/Test.sol";
import {AnchoredProviderFactory} from "smart-contracts-poc/contracts/AnchoredProviderFactory.sol";
import {AnchoredPriceProvider}   from "smart-contracts-poc/contracts/AnchoredPriceProvider.sol";

contract SingleStepOwnershipPoC is Test {
    AnchoredProviderFactory factory;
    address oracle   = address(0xAA);
    address curator  = address(0xC0);
    address typoAddr = address(0xDEAD); // inaccessible — not zero, passes the guard

    function setUp() public {
        factory = new AnchoredProviderFactory(address(this));
    }

    function test_permanentlyBrickedSource() public {
        // curator creates a provider
        vm.prank(curator);
        address provider = factory.createAnchoredProvider(
            oracle, bytes32(uint256(1)), bytes32(0),
            1e14, 60, 500, false, 0,
            address(0x11), address(0x22)
        );

        // curator makes a typo — transfers to an inaccessible address
        vm.prank(curator);
        factory.transferProviderOwnership(provider, typoAddr); // passes require(newOwner != 0)

        // providerOwner is now typoAddr — no one can call setSource ever again
        assertEq(factory.providerOwner(provider), typoAddr);

        // curator (legitimate owner) can no longer replace a broken source
        vm.prank(curator);
        vm.expectRevert(IAnchoredProviderFactory.NotProviderOwner.selector);
        factory.setSource(provider, address(0xBEEF));

        // typoAddr cannot sign transactions — source is permanently frozen
    }
}
``` [3](#0-2) [1](#0-0) [2](#0-1)

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L80-102)
```text
    function grantUpdater(address provider, address updater) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        isUpdater[provider][updater] = true;
        emit UpdaterGranted(provider, updater);
    }

    function revokeUpdater(address provider, address updater) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        isUpdater[provider][updater] = false;
        emit UpdaterRevoked(provider, updater);
    }

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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L11-40)
```text
/// @notice Anchored Price Provider (APP) — the one standard provider for public pools. Every quote is
///         clamped to the reference band derived from the anchor oracle's own uncertainty:
///
///             bid = min(mid − spreadBps − minMargin, custom_bid)
///             ask = max(mid + spreadBps + minMargin, custom_ask)
///
///         Two modes, one contract:
///         - Reference mode (source = 0, default): quotes mid ± (spreadBps + minMargin) directly.
///         - Source mode: an arbitrary curator contract supplies bid/ask, clipped into the band.
///           The source is never reviewed — the reference bounds how wrong it can be.
///
///         Clamp parameters (minMargin, MAX_REF_STALENESS, MAX_SPREAD_BPS) and the reference binding are immutable;
///         the source pointer is instantly swappable (no timelock needed: any source is clamp-bounded
///         at all times). No proxies — upgrades are new deployments.
///
///         Two deployment variants, selected by the immutable MUTABLE_PARAMS flag:
///         - Immutable (false): nothing is tunable except the source pointer.
///         - Customizable (true): the curator may additionally tune confidenceParam; a fixed marginStep
///           bias is set at construction (immutable). The guarantee is ONE-DIRECTIONAL: the final quote
///           is never TIGHTER than mid ± (spreadBps + minMargin). confidence only ever shapes the quote
///           tighter and is clipped to the band edge (the most aggressive quote allowed); a positive
///           marginStep may widen the quote BEYOND the band (a wider, more conservative quote passes the
///           clamp unchanged). With confidence 0 and marginStep 0 the shaped quote degenerates to the
///           band edges — behaviorally identical to the immutable variant.
///
///         Reads go through the abuse-protected providers oracle (Pyth / Chainlink), exclusively via
///         the attributed, non-view `price(feedId, pool)` path: the pool marks itself in-swap with
///         this provider and calls `getBidAndAskPrice()` (no args); this provider forwards the pool
///         (its `msg.sender`) to the oracle, which binds the read via `pool.inSwap() == provider`.
contract AnchoredPriceProvider is IPriceProvider {
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
