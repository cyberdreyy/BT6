### Title
Stale Updater Grants Persist After `transferProviderOwnership`, Allowing Ex-Owner's Delegates to Manipulate `confidenceParam` — (`smart-contracts-poc/contracts/PriceProviderFactory.sol`)

---

### Summary

`PriceProviderFactory.transferProviderOwnership` updates `providerOwner` and the `_providersByCreator` index but never clears the `isUpdater[provider][*]` mapping. Every address the previous owner granted via `grantUpdater` retains the ability to call `setConfidence` on the factory — and therefore `setConfidenceParam` on the underlying `PriceProvider` — indefinitely after the ownership transfer.

---

### Finding Description

`PriceProviderFactory` tracks two independent authorization layers for each provider:

- `providerOwner[provider]` — the current owner, who may grant/revoke updaters and transfer ownership.
- `isUpdater[provider][updater]` — a set of addresses allowed to push `confidenceParam` updates.

`transferProviderOwnership` performs:

```solidity
providerOwner[provider] = newOwner;
_providersByCreator[previousOwner].remove(provider);
_providersByCreator[newOwner].add(provider);
``` [1](#0-0) 

It does **not** touch `isUpdater`. The access gate for `setConfidence` is:

```solidity
function _requireUpdater(address provider) internal view {
    if (msg.sender != providerOwner[provider] && !isUpdater[provider][msg.sender])
        revert NotProviderUpdater();
}
``` [2](#0-1) 

Because `isUpdater[provider][oldUpdater]` is never cleared on transfer, every updater the previous owner granted remains authorized to call `setConfidence` on the factory, which forwards to `PriceProvider.setConfidenceParam`:

```solidity
PriceProvider(providers[i]).setConfidenceParam(values[i]);
``` [3](#0-2) 

`confidenceParam` directly scales the oracle spread used to compute the pool's bid/ask:

```solidity
uint256 adjustedSpread = spread * confidenceParam;
(uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
``` [4](#0-3) 

---

### Impact Explanation

An ex-owner's updater can set `confidenceParam` to either extreme:

**Path A — Pool stall (DoS):** Set `confidenceParam = 0`. With `marginStep = 0` (the common default), `adjustedSpread = 0` → `bid = ask = mid` → after step adjustment `bidOut == askOut` → the hard invariant check `if (bidOut >= askOut) return (0, type(uint128).max)` fires → `getBidAndAskPrice` reverts with `FeedStalled()`. Every swap through any pool using this provider is blocked. [5](#0-4) 

**Path B — Spread manipulation:** Set `confidenceParam = CONFIDENCE_MAX (1,000,000)`. The spread is multiplied 100×, pushing the bid far below and the ask far above the oracle mid. Traders executing swaps receive materially worse prices than the oracle warrants — a direct loss of swap output relative to the fair mid. [6](#0-5) 

Both paths are reachable without any privileged role — only a previously-granted updater address is required.

---

### Likelihood Explanation

The scenario is realistic: a provider owner routinely grants updaters (e.g., a keeper bot) to automate confidence refreshes, then transfers ownership (e.g., to a DAO multisig or a new deployer). The new owner has no on-chain visibility into which updaters the previous owner granted, and no prompt to revoke them. The 1-minute `CONFIDENCE_COOLDOWN` limits frequency but does not prevent the attack. [7](#0-6) 

---

### Recommendation

Clear all updater grants on ownership transfer, or enumerate and revoke them. The simplest fix is to add a version/epoch counter per provider and include it in the updater authorization check, so all pre-transfer grants are implicitly invalidated:

```solidity
// In transferProviderOwnership:
updaterEpoch[provider] += 1;
```

Alternatively, require the transferring owner to explicitly pass a list of updaters to revoke as part of the transfer call, or emit an event listing all active updaters so the new owner can revoke them atomically.

---

### Proof of Concept

1. Alice calls `createPriceProvider(...)` → becomes `providerOwner[P]`.
2. Alice calls `grantUpdater(P, mallory)` → `isUpdater[P][mallory] = true`.
3. Alice calls `transferProviderOwnership(P, bob)` → `providerOwner[P] = bob`; `isUpdater[P][mallory]` is **not** cleared.
4. Mallory calls `factory.setConfidence([P], [0])`.
   - `_requireUpdater(P)`: `msg.sender (mallory) != providerOwner[P] (bob)` but `isUpdater[P][mallory] == true` → passes.
   - `PriceProvider(P).setConfidenceParam(0)` executes.
5. Any pool using provider `P` now calls `getBidAndAskPrice()` → `adjustedSpread = 0` → `bid == ask` → `FeedStalled()` revert → all swaps through that pool are blocked. [1](#0-0) [8](#0-7)

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L34-37)
```text
    function _requireUpdater(address provider) internal view {
        if (msg.sender != providerOwner[provider] && !isUpdater[provider][msg.sender])
            revert NotProviderUpdater();
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L80-90)
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
```

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L92-101)
```text
    function transferProviderOwnership(address provider, address newOwner) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        require(newOwner != address(0));
        address previousOwner = providerOwner[provider];

        providerOwner[provider] = newOwner;
        _providersByCreator[previousOwner].remove(provider);
        _providersByCreator[newOwner].add(provider);

        emit ProviderOwnershipTransferred(provider, previousOwner, newOwner);
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

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L191-205)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
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
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L216-217)
```text
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L226-230)
```text
        // 7. Hard invariant: bid must be strictly less than ask.
        //    Can be violated when marginStep < 0 and confidence is too small.
        if (bidOut >= askOut) return (0, type(uint128).max);

        return (uint128(bidOut), uint128(askOut));
```
