### Title
`transferProviderOwnership()` Does Not Clear `isUpdater` Mapping, Allowing Stale Updaters to Manipulate Bid/Ask Spread — (`smart-contracts-poc/contracts/PriceProviderFactory.sol`, `PriceProviderFactoryL2.sol`, `AnchoredProviderFactory.sol`)

---

### Summary

`transferProviderOwnership()` in all three factory contracts updates `providerOwner` and `_providersByCreator` but never clears the `isUpdater[provider][*]` mapping. Any updater address granted by the previous owner retains the ability to call `setConfidence()` after ownership transfer, directly manipulating the `confidenceParam` that scales the oracle spread into the bid/ask prices consumed by pools.

---

### Finding Description

All three factory contracts share the same pattern. The `isUpdater` mapping is a two-dimensional boolean:

```
mapping(address provider => mapping(address updater => bool)) public isUpdater;
``` [1](#0-0) [2](#0-1) 

The `_requireUpdater` guard that protects `setConfidence()` passes if either `msg.sender == providerOwner[provider]` OR `isUpdater[provider][msg.sender]` is true: [3](#0-2) 

`transferProviderOwnership()` only updates `providerOwner` and the `_providersByCreator` enumerable sets. It never touches `isUpdater`: [4](#0-3) [5](#0-4) [6](#0-5) 

After the transfer, any address for which the old owner had called `grantUpdater()` still satisfies `isUpdater[provider][staleUpdater] == true` and can call `setConfidence()` unchallenged.

`setConfidence()` calls `setConfidenceParam()` on the provider, which writes `confidenceParam`. This value directly multiplies the oracle spread to produce the adjusted spread fed into the bid/ask computation:

```solidity
uint256 adjustedSpread = spread * confidenceParam;
(uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
``` [7](#0-6) 

A stale updater can set `confidenceParam` to `CONFIDENCE_MAX` (1,000,000 — a 100× multiplier on the oracle spread), maximally widening the bid/ask spread delivered to every pool using that provider.

An additional aggravating factor: `isUpdater` is a plain mapping with no enumeration. The new owner has no on-chain way to discover which updater addresses were granted by the previous owner, making silent cleanup impossible without off-chain event indexing.

---

### Impact Explanation

`confidenceParam` is the sole multiplier on the oracle spread before it reaches the pool's bin-traversal logic. Setting it to `CONFIDENCE_MAX` widens the spread by up to 100×, causing pools to execute swaps at prices far outside the fair oracle mid. Traders receive worse execution (bad-price execution), and LP positions are exposed to adverse fills at the widened quote. For `PriceProvider` and `PriceProviderL2` there is no band clamp to limit this damage; the widened spread passes directly to the pool.

---

### Likelihood Explanation

Three conditions are required, all of which are normal operational steps:

1. The original owner calls `grantUpdater()` to delegate parameter management (expected usage).
2. The original owner later calls `transferProviderOwnership()` without first revoking all updaters (an easy oversight, especially since updaters are not enumerable).
3. The stale updater — now unaffiliated with the provider — calls `setConfidence()` with an extreme value.

The new owner cannot enumerate stale updaters on-chain and may not know they exist. This matches the medium-severity profile of the ODSafeManager analog: multiple conditions required, but the invariant is clearly broken and the new owner has no reliable way to detect the exposure.

---

### Recommendation

Clear all updater grants atomically inside `transferProviderOwnership()`. Because `isUpdater` is an unbounded mapping, the cleanest fix is to add an `updaterNonce[provider]` counter and include it in the `_requireUpdater` check, so incrementing the nonce on transfer invalidates all prior grants without iterating:

```solidity
mapping(address provider => uint256) public updaterNonce;
mapping(address provider => mapping(uint256 nonce => mapping(address updater => bool))) public isUpdater;

function _requireUpdater(address provider) internal view {
    uint256 nonce = updaterNonce[provider];
    if (msg.sender != providerOwner[provider] && !isUpdater[provider][nonce][msg.sender])
        revert NotProviderUpdater();
}

function transferProviderOwnership(address provider, address newOwner) external onlyProviderOwner(provider) {
    ...
    updaterNonce[provider]++;   // invalidates all prior isUpdater grants
    ...
}
```

Alternatively, require the caller to supply an explicit list of updaters to revoke before the transfer is accepted.

---

### Proof of Concept

```
State before transfer:
  providerOwner[P]       = Alice
  isUpdater[P][Mallory]  = true   // Alice granted Mallory as updater

Alice calls: factory.transferProviderOwnership(P, Bob)

State after transfer:
  providerOwner[P]       = Bob
  isUpdater[P][Mallory]  = true   // ← NOT cleared

_requireUpdater(P) for Mallory:
  msg.sender (Mallory) != providerOwner[P] (Bob)  → false
  isUpdater[P][Mallory]                           → true  ✓ PASSES

Mallory calls: factory.setConfidence([P], [1_000_000])
  → PriceProvider(P).setConfidenceParam(1_000_000)
  → confidenceParam = 1_000_000  (100× spread multiplier)

Next pool swap using provider P:
  adjustedSpread = oracleSpread * 1_000_000   // 100× wider than intended
  bid/ask delivered to pool at maximally widened spread
  → bad-price execution for all traders
```

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L20-20)
```text
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

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L46-46)
```text
    mapping(address provider => mapping(address updater => bool)) public isUpdater;
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

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L214-217)
```text
        // 5. Compute bid/ask from mid + confidence-adjusted spread
        //    confidenceParam multiplies oracle spread; 0 means no spread
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
```
