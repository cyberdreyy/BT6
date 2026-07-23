### Title
Single-Step `transferProviderOwnership` Allows Irrecoverable Loss of Oracle Provider Control, Enabling Malicious Source Injection into Pool Swaps - (File: `smart-contracts-poc/contracts/AnchoredProviderFactory.sol`, `smart-contracts-poc/contracts/PriceProviderFactory.sol`, `smart-contracts-poc/contracts/PriceProviderFactoryL2.sol`)

---

### Summary

`transferProviderOwnership` in all three oracle provider factories (`AnchoredProviderFactory`, `PriceProviderFactory`, `PriceProviderFactoryL2`) is a single-step transfer: the current owner calls the function and ownership is immediately and irrevocably assigned to the supplied address. There is no pending/accept pattern. A typo or mistake permanently hands the `providerOwner` role to an unintended address. In `AnchoredProviderFactory`, that role controls `setSource`, which can inject a malicious bid/ask source into any provider feeding a live pool, causing bad-price execution or making the pool unusable for swaps.

---

### Finding Description

`AnchoredProviderFactory.transferProviderOwnership` (and the identical pattern in `PriceProviderFactory` and `PriceProviderFactoryL2`) immediately writes the new owner into storage with no confirmation step:

```solidity
// AnchoredProviderFactory.sol lines 230-240
function transferProviderOwnership(address provider, address newOwner)
    external override onlyProviderOwner(provider)
{
    require(_providers.contains(provider), ProviderNotTracked());
    require(newOwner != address(0));
    address previousOwner = providerOwner[provider];

    providerOwner[provider] = newOwner;          // ← instant, no accept
    _providersByCreator[previousOwner].remove(provider);
    _providersByCreator[newOwner].add(provider);

    emit ProviderOwnershipTransferred(provider, previousOwner, newOwner);
}
```

The `providerOwner` role is the gatekeeper for `setSource` in `AnchoredProviderFactory`:

```solidity
// AnchoredProviderFactory.sol lines 224-228
function setSource(address provider, address newSource)
    external override onlyProviderOwner(provider)
{
    require(_providers.contains(provider), ProviderNotTracked());
    AnchoredPriceProvider(provider).setSource(newSource);
    emit SourceSet(provider, newSource);
}
```

`setSource` is intentionally instant and timelock-free because the `AnchoredPriceProvider` clamps any source output to the reference band. However, the clamp is a **widening** clamp, not a tightening one:

```solidity
// AnchoredPriceProvider.sol lines 342-343
uint256 bidOut = Math.min(refBid, cBid);   // takes the LOWER bid
uint256 askOut = Math.max(refAsk, cAsk);   // takes the HIGHER ask
```

A malicious source that returns `bid = 1` and `ask = type(uint128).max - 1` passes all validity checks in `_readSource` (`srcBid != 0`, `srcBid < srcAsk`, `srcAsk <= type(uint128).max`) and produces `bidOut = 1`, `askOut = type(uint128).max - 1` — an effectively infinite spread that makes every swap execute at the worst possible price or revert on slippage guards.

**Contrast with correctly implemented two-step patterns already present in the same codebase:**

- `MetricOmmPoolFactory`: `proposePoolAdminTransfer` + `acceptPoolAdmin` (two-step, new admin must accept)
- `OracleBase` (providers oracle): `setStateGuardRole` + `acceptStateGuardRole` (two-step)
- `OracleBase` (compressed oracle): `setPendingStateGuardRole` + `acceptStateGuardRole` (two-step)

The oracle provider factories are the only role-transfer surfaces that skip the accept step.

---

### Impact Explanation

Once ownership is transferred to a wrong address:

1. The new owner calls `AnchoredProviderFactory.setSource(provider, maliciousSource)`.
2. `maliciousSource.getBidAndAskPrice()` returns `(1, type(uint128).max - 1)`.
3. `_readSource` accepts this (all validity checks pass).
4. `_computeBidAsk` produces `bidOut = 1`, `askOut = type(uint128).max - 1`.
5. Every swap through any pool using this provider either:
   - Executes at a catastrophically bad price (direct loss of trader principal), or
   - Reverts on the router's `minAmountOut` slippage guard, making the pool completely unusable.

LP positions are also stranded: if swaps are blocked, LPs cannot rebalance or exit through normal swap flows. This satisfies both "bad-price execution" and "broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows."

---

### Likelihood Explanation

The trigger is a realistic operational mistake: a typo in the `newOwner` address during a routine ownership handover (e.g., multisig rotation, team change). The function is permissionless to call for the current owner and requires no special conditions. The mistake is permanent — there is no recovery path once the wrong address holds `providerOwner`.

---

### Recommendation

Apply the same two-step pattern already used for pool admin transfers in `MetricOmmPoolFactory`:

```solidity
// In AnchoredProviderFactory (and PriceProviderFactory, PriceProviderFactoryL2):
mapping(address provider => address) public pendingProviderOwner;

function proposeProviderOwnershipTransfer(address provider, address newOwner)
    external onlyProviderOwner(provider)
{
    require(_providers.contains(provider), ProviderNotTracked());
    require(newOwner != address(0) && newOwner != providerOwner[provider]);
    pendingProviderOwner[provider] = newOwner;
    emit ProviderOwnershipTransferProposed(provider, providerOwner[provider], newOwner);
}

function acceptProviderOwnership(address provider) external {
    address pending = pendingProviderOwner[provider];
    require(pending != address(0), NoPendingOwnerTransfer());
    require(msg.sender == pending, NotPendingOwner());
    address previous = providerOwner[provider];
    providerOwner[provider] = pending;
    _providersByCreator[previous].remove(provider);
    _providersByCreator[pending].add(provider);
    delete pendingProviderOwner[provider];
    emit ProviderOwnershipTransferred(provider, previous, pending);
}
```

---

### Proof of Concept

```solidity
// 1. Alice (legitimate owner) intends to transfer to 0xABCD...1234 but types 0xABCD...1235
factory.transferProviderOwnership(provider, address(0xABCD1235)); // typo — instant, no revert

// 2. Attacker at 0xABCD1235 (or anyone who controls that address) calls setSource
vm.prank(address(0xABCD1235));
factory.setSource(provider, address(new MaliciousSource()));

// MaliciousSource.getBidAndAskPrice() returns (1, type(uint128).max - 1)
// _readSource: srcBid=1 != 0, srcBid < srcAsk, srcAsk <= type(uint128).max → ok=true
// _computeBidAsk: bidOut = min(refBid, 1) = 1; askOut = max(refAsk, type(uint128).max-1)
// Pool now quotes bid=1, ask=~2^128 for every swap

// 3. Any trader calling pool.swap() either:
//    a) Receives 1 wei of token1 for their entire token0 input (direct loss), or
//    b) Hits minAmountOut slippage guard and reverts (pool unusable)
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L510-526)
```text
  function proposePoolAdminTransfer(address pool, address newAdmin) external override nonReentrant onlyPoolAdmin(pool) {
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
