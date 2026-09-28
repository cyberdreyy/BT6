### Title
Stale per-epoch instant-withdraw bookkeeping inflates default recovery price and drains `defaultRecoveryReserve` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug is a removal path (`do_lock_file_wait`) that can fail or be denied after a create path succeeded, leaving a stale lock that a later reader (`lock_get_status`) dereferences. The analog in `IdleCreditVault` is the instant-withdraw receipt: `claimInstantWithdrawRequest` removes the aggregate `instantWithdrawsRequests[_user]` and burns the receipt, but never clears the per-epoch entries `instantWithdrawsRequestsByEpoch[_user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]`. Later, `finalizeDefaultRecovery` dereferences that stale state in `_defaultPrefundedInstantReserve`, counting already-paid-out claims as reserve backing and inflating `defaultRecoveryPrice`/`defaultRecoveryReserve`. The stale entry is then dereferenced again in `_claimDefaultedInstantWithdrawRequest`, which subtracts the full stale basis — letting an early claimant extract more than the real recovered funds and leaving later claimants' `_transferDefaultRecovery` to underflow.

### Finding Description
`requestInstantWithdraw` records three pieces of state: the aggregate `instantWithdrawsRequests[_user]`, the per-epoch `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and the global per-epoch `instantWithdrawClaimsByEpoch[currentEpoch]` [1](#0-0) .

The normal (non-default) removal path only clears the aggregate:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
``` [2](#0-1) 

`instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` remain non-zero forever — analogous to a lock left on the list after the failed removal.

When the borrower later defaults in the **same epoch** (`epochNumber` only advances at `stopEpoch`, so a mid-epoch default shares the epoch of those stale requests), `_defaultPrefundedInstantReserve` computes:

```solidity
uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
if (instantBasis > pendingInstant) {
  prefundedReserve = instantBasis - pendingInstant;
}
``` [3](#0-2) 

This treats the stale, already-claimed basis as underlying already held by the strategy, and adds it into `reserveAmount` / `defaultRecoveryReserve` and into the numerator of `recoveryPrice` [4](#0-3) .

Then `_claimDefaultedInstantWithdrawRequest` dereferences the same stale per-epoch entry as `claimBasis`, burning it and paying `(claimBasis * defaultRecoveryPrice) / RECOVERY_FULL` out of `defaultRecoveryReserve` [5](#0-4) . Any defaulted normal/APR0 claimant is paid the same inflated `defaultRecoveryPrice` from the phantom-backed reserve [6](#0-5) .

### Impact Explanation
Broken invariant: recovery isolation — `defaultRecoveryReserve` must equal real recovered underlyings. With a stale claimed amount `S` left in `instantWithdrawClaimsByEpoch[N]`:

- `defaultRecoveryReserve` is overstated by up to `S` (the `prefundedReserve` term), and `recoveryPrice` is inflated by `S / totalBasis`.
- A claimant (a second attacker address with a defaulted `withdrawsRequests` receipt) calling `claimWithdrawRequest` early receives `claimBasis * inflatedPrice`, paid in real underlying via `_transferDefaultRecovery` [7](#0-6) .
- Because actual recovered funds are smaller than `defaultRecoveryReserve`, later claimants' `_transferDefaultRecovery` either underflows (`defaultRecoveryReserve -= _amount`) or transfers nothing — permanent freezing of unclaimed recovery for honest lenders, and a direct overpayment theft by the early claimant.

The attacker needs no privilege: any EOA can `requestInstantWithdraw`, get funded via `collectInstantWithdrawFunds`, claim normally (leaving the stale per-epoch entry), and another unprivileged address only needs an ordinary `requestWithdraw` receipt outstanding when the honest borrower defaults. All guards pass: `defaultInstantWithdrawsFinalized` is enabled by any real pending instant request [8](#0-7) , and nothing in the funded-claim path validates per-epoch consistency.

### Likelihood Explanation
Requires a borrower default in an epoch where a previously-funded-and-claimed instant withdrawal exists plus at least one still-pending instant/normal receipt — an ordinary configuration (instant withdrawals and defaults are both supported flows). The attacker controls creation of the stale entry entirely; only the default itself is an external event, and the漏洞 pays out whenever it occurs.

### Recommendation
Mirror the kernel fix (`locks_remove_posix`, the same routine used by `filp_flush`): make the normal claim path use the same per-epoch removal as the default path. In `claimInstantWithdrawRequest`, clear `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` for the epochs being claimed (or track the request epoch and clear it), so no stale basis survives to be re-dereferenced by `_defaultPrefundedInstantReserve` or `_claimDefaultedInstantWithdrawRequest`.

### Proof of Concept
Foundry fork PoC sketch (epoch running, instant mode):

```solidity
// 1. Epoch N running. Attacker A requests instant withdraw of W1.
cdoEpoch.requestInstantWithdraw(W1);           // ByEpoch[A][N]=W1, claimsByEpoch[N]=W1

// 2. Manager collects W1 via getInstantWithdrawFunds -> pendingInstantWithdraws = 0.
// 3. A claims normally: instantWithdrawsRequests[A]=0, tokens burned.
//    BUG: ByEpoch[A][N] and claimsByEpoch[N] still = W1.
cdoEpoch.claimInstantWithdrawRequest();

// 4. Attacker B (same epoch N) makes a normal requestWithdraw of W2.
cdoEpoch.requestWithdraw(W2, address(tranche));

// 5. Borrower defaults mid-epoch; finalizeDefault runs at epochNumber == N.
//    _defaultPrefundedInstantReserve counts W1 as already-held reserve ->
//    recoveryPrice and defaultRecoveryReserve inflated by ~W1.
strategy.finalizeDefaultRecovery(...);

// 6. B calls claimWithdrawRequest -> receives W2 * inflatedPrice (overpaid,
//    drawn from real recovered funds). Honest claimants later revert in
//    _transferDefaultRecovery or receive nothing.
cdoEpoch.claimWithdrawRequest();
```

Assertions: `strategy.instantWithdrawClaimsByEpoch(N)` remains `W1` after step 3; `defaultRecoveryReserve` exceeds `underlying.balanceOf(strategy)` after step 5; B's payout exceeds its pro-rata share of real recovered funds.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L366-374)
```text
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-692)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L696-696)
```text
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-722)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L772-783)
```text
  function _claimDefaultedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, defaultEpoch, true);
    if (claimBasis == 0) return amount;

    // pendingWithdraws stores the claim basis owed by the borrower, including APR0 interest.
    pendingWithdraws -= claimBasis;
    // Only receipt principal exists as strategy tokens. APR0 interest is included in claimBasis
    // but was never minted as a user strategy-token receipt.
    _burn(_user, burnAmount);
    amount = (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL;
    _transferDefaultRecovery(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L843-855)
```text
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-916)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
```
