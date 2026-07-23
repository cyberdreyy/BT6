### Title
One-Step `transferProviderOwnership` Enables Instant Source Hijack After Accidental Ownership Misdirection — (`smart-contracts-poc/contracts/AnchoredProviderFactory.sol`)

### Summary

`AnchoredProviderFactory.transferProviderOwnership` immediately overwrites `providerOwner[provider]` with no two-step acceptance requirement. If the current owner mistypes or otherwise sends the role to a wrong address, the new "owner" can instantly call `setSource` to replace the price source with a malicious contract. Because `_computeBidAsk` clamps the source output by taking `min(refBid, srcBid)` and `max(refAsk, srcAsk)`, a malicious source can widen the spread to arbitrary extremes, delivering unbounded bad-price execution to every swap that goes through the affected provider.

### Finding Description

`transferProviderOwnership` performs a single-step, immediate ownership handover:

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
``` [1](#0-0) 

The only guard is `newOwner != address(0)`. Any other wrong address — a mistyped EOA, a dead multisig, an attacker-controlled contract — immediately becomes the provider owner with full authority to call `setSource`:

```solidity
function setSource(address provider, address newSource)
    external override onlyProviderOwner(provider)
{
    require(_providers.contains(provider), ProviderNotTracked());
    AnchoredPriceProvider(provider).setSource(newSource);
    emit SourceSet(provider, newSource);
}
``` [2](#0-1) 

`setSource` accepts any address with no validation. The malicious source is then consumed by `_computeBidAsk` in `AnchoredPriceProvider`:

```solidity
uint256 bidOut = Math.min(refBid, cBid);   // source can push bid arbitrarily low
uint256 askOut = Math.max(refAsk, cAsk);   // source can push ask arbitrarily high
if (bidOut == 0 || bidOut >= askOut) {
    return (0, type(uint128).max);
}
return (uint128(bidOut), uint128(askOut));
``` [3](#0-2) 

`_readSource` only rejects `srcBid == 0`, `srcBid >= srcAsk`, or `srcAsk > type(uint128).max`:

```solidity
if (srcBid == 0 || srcBid >= srcAsk || srcAsk > type(uint128).max) return (false, 0, 0);
return (true, srcBid, srcAsk);
``` [4](#0-3) 

A malicious source returning `srcBid = 1` and `srcAsk = type(uint128).max - 1` passes every check. The clamp then produces `bidOut = 1` and `askOut = type(uint128).max - 1`, which satisfies `bidOut < askOut` and is returned to the pool as a valid quote. Every swap through that provider executes at these extreme prices.

Contrast this with the pool-admin role, which correctly uses a two-step handover:

```solidity
function proposePoolAdminTransfer(address pool, address newAdmin) external ...
function acceptPoolAdmin(address pool) external ...
``` [5](#0-4) 

And the oracle state-guard role, which also uses a two-step pattern:

```solidity
function setStateGuardRole(bytes32 feedId, address newGuard) external checkRole(feedId) { ... }
function acceptStateGuardRole(bytes32 feedId) external { ... }
``` [6](#0-5) 

`transferProviderOwnership` is the only privileged role transfer in the system that skips this pattern.

### Impact Explanation

Every pool whose `AnchoredPriceProvider` has its source replaced by a malicious contract will execute swaps at an unbounded spread. Traders receive prices far outside the reference oracle band — a direct loss of user principal on every swap. The pool itself does not become insolvent (LP claims are unaffected), but the swap path is broken: rational traders will not transact, and any trader without slippage protection suffers the full spread widening as a loss. This matches the "bad-price execution: unbounded bid/ask quote reaches a pool swap" and "broken core pool functionality / unusable swap flows" impact categories.

### Likelihood Explanation

The provider owner is a semi-trusted party (the address that called `createAnchoredProvider`). A single-character typo in the `newOwner` argument, a compromised signing key, or a mistaken multisig payload is sufficient to trigger the vulnerability. No on-chain guard prevents the transfer from completing, and no recovery path exists once the wrong address holds the role (the original owner is immediately stripped). The likelihood is low-to-medium: the operation is infrequent, but the consequence of a single mistake is permanent and immediately exploitable.

### Recommendation

Replace the single-step transfer with a two-step pattern mirroring `proposePoolAdminTransfer` / `acceptPoolAdmin`:

1. `proposeProviderOwnershipTransfer(provider, newOwner)` — records `pendingProviderOwner[provider] = newOwner`; only the current owner may call.
2. `acceptProviderOwnership(provider)` — requires `msg.sender == pendingProviderOwner[provider]`; then atomically updates `providerOwner` and clears the pending slot.

This ensures the nominated address is live and active before authority is transferred, eliminating the accidental-misdirection vector.

### Proof of Concept

1. Legitimate owner calls `transferProviderOwnership(provider, wrongAddress)` (typo or compromised key).
2. `providerOwner[provider]` is immediately set to `wrongAddress`.
3. `wrongAddress` calls `AnchoredProviderFactory.setSource(provider, MaliciousSource)`.
4. `MaliciousSource.getBidAndAskPrice()` returns `(1, type(uint128).max - 1)`.
5. `_readSource` accepts these values (both checks pass).
6. `_computeBidAsk` computes `bidOut = min(refBid, 1) = 1`, `askOut = max(refAsk, type(uint128).max - 1) = type(uint128).max - 1`.
7. `bidOut < askOut` → returned to pool as a valid quote.
8. Every subsequent swap through this provider executes at the extreme spread; any trader without slippage protection loses the difference between the extreme price and the fair reference price.

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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L342-348)
```text
        uint256 bidOut = Math.min(refBid, cBid);
        uint256 askOut = Math.max(refAsk, cAsk);
        if (bidOut == 0 || bidOut >= askOut) {
            return (0, type(uint128).max);
        }

        return (uint128(bidOut), uint128(askOut));
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L404-410)
```text
        if (!success || retSize != 64) return (false, 0, 0);

        srcBid = b;
        srcAsk = a;
        if (srcBid == 0 || srcBid >= srcAsk || srcAsk > type(uint128).max) return (false, 0, 0);

        return (true, srcBid, srcAsk);
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L511-526)
```text
    if (newAdmin == address(0)) revert InvalidAdmin();
    if (newAdmin == poolAdmin[pool]) revert InvalidAdmin();
    pendingPoolAdmin[pool] = newAdmin;
    emit PoolAdminTransferProposed(pool, poolAdmin[pool], newAdmin);
  }

  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
  function acceptPoolAdmin(address pool) external override nonReentrant {
    address pending = pendingPoolAdmin[pool];
    if (pending == address(0)) revert NoPendingPoolAdminTransfer();
    if (msg.sender != pending) revert NotPendingPoolAdmin(pool, msg.sender, pending);
    address previousAdmin = poolAdmin[pool];
    poolAdmin[pool] = pending;
    delete pendingPoolAdmin[pool];
    emit PoolAdminTransferred(pool, previousAdmin, pending);
  }
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
