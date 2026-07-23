### Title
Single-Step Provider Ownership Transfer Enables Immediate Source Hijack and Bad-Price Execution — (File: smart-contracts-poc/contracts/AnchoredProviderFactory.sol)

### Summary

`AnchoredProviderFactory.transferProviderOwnership` (and the same function in `PriceProviderFactory` and `PriceProviderFactoryL2`) transfers `providerOwner` in a single atomic step with no acceptance requirement from the new owner. If the current owner sends ownership to a wrong address (e.g., a typo), the unintended recipient immediately holds the `onlyProviderOwner` role and can call `setSource` to point the provider at a malicious contract. The `AnchoredPriceProvider` clamp (`bidOut = min(refBid, cBid)`, `askOut = max(refAsk, cAsk)`) only prevents the source from quoting *tighter* than the reference band; it explicitly allows the source to push bid arbitrarily below `refBid` and ask arbitrarily above `refAsk`. Swaps executed against such a provider deliver bad prices to traders.

---

### Finding Description

`AnchoredProviderFactory.transferProviderOwnership` immediately overwrites `providerOwner[provider]` with `newOwner` in one transaction: [1](#0-0) 

There is no pending-owner state, no acceptance step, and no cancellation path. This is structurally identical to the River `setAdministrator` single-step pattern flagged in the external report.

By contrast, `MetricOmmPoolFactory` already implements the two-step pattern for pool-admin transfers (`proposePoolAdminTransfer` → `acceptPoolAdmin`), demonstrating that the protocol is aware of the risk: [2](#0-1) 

The same single-step defect exists in `PriceProviderFactory` and `PriceProviderFactoryL2`: [3](#0-2) [4](#0-3) 

Once ownership lands on the wrong address, the unintended owner can immediately call `setSource` through the factory: [5](#0-4) 

`setSource` is explicitly designed with no timelock ("instant, no timelock: any source is clamp-bounded by the provider at all times"). The clamp in `_computeBidAsk` is: [6](#0-5) 

`bidOut = Math.min(refBid, cBid)` — if the malicious source returns `cBid = 1`, then `bidOut = 1` (far below the reference band). `askOut = Math.max(refAsk, cAsk)` — if the source returns `cAsk = type(uint128).max`, then `askOut = type(uint128).max`. The clamp only prevents the source from quoting *inside* the band (higher bid, lower ask); it does not prevent the source from quoting *outside* the band (lower bid, higher ask). The `_readSource` validity gate only requires `srcBid > 0`, `srcBid < srcAsk`, and `srcAsk ≤ type(uint128).max`: [7](#0-6) 

So `(srcBid=1, srcAsk=type(uint128).max)` passes all guards and produces `bidOut=1`, `askOut=type(uint128).max` — extreme bad-price execution delivered to every pool swap that reads this provider.

---

### Impact Explanation

Any pool whose `priceProvider` is the hijacked `AnchoredPriceProvider` will execute swaps at `bid=1` / `ask=type(uint128).max`. Traders selling base tokens receive 1 unit of quote token instead of the fair reference price; traders buying base tokens pay the maximum representable price. This is a direct loss of trader principal on every swap routed through the affected pool, satisfying the "bad-price execution: unbounded bid/ask quote reaches a pool swap" impact gate.

---

### Likelihood Explanation

The trigger is an accidental address typo or copy-paste error by the provider owner during a routine ownership transfer — a realistic operational mistake. The transfer is irreversible (no cancel, no pending state), and the unintended recipient can act in the same block. The protocol already applies the two-step pattern to pool-admin transfers, confirming awareness of the risk class; the omission in the provider factories is an inconsistency, not a deliberate design choice.

---

### Recommendation

Apply the same two-step pattern used for pool-admin transfers to all three provider factories:

1. Add a `pendingProviderOwner` mapping.
2. Replace the current `transferProviderOwnership` with a `proposeProviderOwnerTransfer` that writes only to `pendingProviderOwner`.
3. Add an `acceptProviderOwnership` that requires `msg.sender == pendingProviderOwner[provider]` before committing the change to `providerOwner`.
4. Add a `cancelProviderOwnerTransfer` callable by the current owner.

This mirrors the existing `proposePoolAdminTransfer` / `acceptPoolAdmin` / `cancelPoolAdminTransfer` pattern in `MetricOmmPoolFactory`.

---

### Proof of Concept

```
// 1. Legitimate owner accidentally transfers to wrong address
vm.prank(legitimateOwner);
anchoredFactory.transferProviderOwnership(provider, wrongAddress); // typo — irreversible

// 2. Unintended recipient deploys a malicious source
MaliciousSource mal = new MaliciousSource(); // returns (1, type(uint128).max)

// 3. Unintended recipient immediately sets the source — no timelock
vm.prank(wrongAddress);
anchoredFactory.setSource(provider, address(mal));

// 4. Pool swap reads the provider
// AnchoredPriceProvider._computeBidAsk:
//   cBid = 1, cAsk = type(uint128).max  (passes _readSource validity gate)
//   bidOut = min(refBid, 1)             = 1
//   askOut = max(refAsk, type(uint128).max) = type(uint128).max
//   → pool executes swap at bid=1, ask=type(uint128).max

// 5. Trader selling base tokens receives 1 wei of quote token instead of fair value
//    → direct loss of trader principal
```

### Citations

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L224-228)
```text
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

**File:** metric-core/contracts/interfaces/IMetricOmmPoolFactory/IMetricOmmPoolFactoryPoolAdmin.sol (L62-66)
```text
  /// @notice Start two-step admin transfer to `newAdmin`.
  function proposePoolAdminTransfer(address pool, address newAdmin) external;

  /// @notice Accept pending admin role for `pool` (must be pending admin).
  function acceptPoolAdmin(address pool) external;
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L342-346)
```text
        uint256 bidOut = Math.min(refBid, cBid);
        uint256 askOut = Math.max(refAsk, cAsk);
        if (bidOut == 0 || bidOut >= askOut) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L404-408)
```text
        if (!success || retSize != 64) return (false, 0, 0);

        srcBid = b;
        srcAsk = a;
        if (srcBid == 0 || srcBid >= srcAsk || srcAsk > type(uint128).max) return (false, 0, 0);
```
