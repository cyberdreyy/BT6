### Title
Stale-epoch instant-withdraw receipts bypass the default recovery haircut and drain funded claim liquidity - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`instantWithdrawsRequestsByEpoch` and `instantWithdrawClaimsByEpoch` key instant-withdraw receipt basis by the `epochNumber` at request time, but `defaultPendingClaimBasis`, `_defaultPrefundedInstantReserve`, and `_claimDefaultedInstantWithdrawRequest` all look up only the **current** `epochNumber` / `defaultRecoveryEpoch`. Because `epochNumber` is incremented inside `deposit()` during `stopEpoch`, an instant receipt created in epoch N that is only partially funded and remains unclaimed into epoch N+1 becomes invisible to default-finalization accounting. After `finalizeDefault`, that stale receipt is still claimed at full par through `claimInstantWithdrawRequest` → `_transferFundedClaim`, spending underlyings that were collected for the instant queue but were never added to `defaultRecoveryReserve`. The result is a par payout to the stale-receipt holder while all defaulted-epoch claimants are haircut by `defaultRecoveryPrice` — a direct wealth transfer and reserve shortfall. This is the vault analog of the reported bug class (an index/key that resolves past the data it was written under, here an epoch key that has moved), producing fund-impacting misrouting rather than a mere read.

### Finding Description
- Request path: `requestInstantWithdraw` records basis under `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` (lines 367–374).
- `epochNumber` advances in `deposit()` whenever `isEpochRunning()` is true, i.e. on each `stopEpoch` (lines 607–611).
- At finalization, `defaultPendingClaimBasis()` reads only `instantWithdrawClaimsByEpoch[epochNumber]` (lines 644–649) and `_defaultPrefundedInstantReserve()` likewise (lines 716–723). Cash already collected via `collectInstantWithdrawFunds` for epoch N claims is therefore **not** counted as `prefundedReserve` once `epochNumber` has advanced.
- `_claimDefaultedInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (lines 843–855), so the stale receipt survives intact in the aggregate `instantWithdrawsRequests[_user]`.
- `claimInstantWithdrawRequest` then burns and pays the full remaining `instantWithdrawsRequests[_user]` via `_transferFundedClaim` (lines 387–392). The reserve guard at lines 899–905 only prevents spending `defaultRecoveryReserve`; the uncounted prefunded instant cash sits **outside** the reserve and is spent at par.

Broken invariant: one receipt, one haircut — every claim against defaulted borrower funds must be paid at `defaultRecoveryPrice`, and collected claim liquidity must be isolated inside `defaultRecoveryReserve`.

### Impact Explanation
Direct theft / dilution. A user holding an unfunded (or unclaimed funded) instant receipt from epoch N receives `claimBasis` at 100% instead of `claimBasis * defaultRecoveryPrice / 1e18`. With, e.g., a 1,000,000 USDC receipt and a 40% recovery price, the stale claimant extracts 600,000 USDC more than entitled — value that, under correct accounting, belongs in the recovery reserve shared by all defaulted-epoch receipt holders and active LPs. If multiple stale receipts exist, the earliest claimant drains the unreserved cash first; the recovery reserve itself is then the only backing left and is undersized relative to total claims.

### Likelihood Explanation
Requires a specific but plausible sequence, all performed by honest privileged actors plus unprivileged users:
1. During buffer of epoch N, APR drops by more than `instantWithdrawAprDelta`, so `requestWithdraw` routes to `requestInstantWithdraw` (IdleCDOEpochVariant lines 761–769).
2. At `startEpoch`, available cash covers only part of the instant queue — the code explicitly contemplates this ("that cash covered only part of the instant queue", line 638 comment), leaving `pendingInstantWithdraws != 0`.
3. Epoch N runs and is stopped (`epochNumber++` in `deposit`), the user does not claim, a subsequent epoch defaults, and `finalizeDefault` runs. Any unprivileged user in this position triggers the mispricing by a single `claimInstantWithdrawRequest` call — no malicious privileged role needed.

### Recommendation
Track instant-withdraw basis across all epochs with outstanding claims rather than only `epochNumber`. Concretely: maintain a running `instantWithdrawClaimsTotal` (or iterate/settle per-epoch claims), use it in `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve`, and have `_claimDefaultedInstantWithdrawRequest` clear `instantWithdrawsRequests[_user]` against the finalized `defaultRecoveryPrice` regardless of which epoch the receipts were recorded in. Alternatively, forbid `epochNumber` from advancing while `pendingInstantWithdraws != 0`, or force-claim/rollover stale instant receipts at `stopEpoch`.

### Proof of Concept
Foundry fork PoC (mainnet USDC vault setup as in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testStaleEpochInstantReceiptStealsRecovery() external {
    // --- Epoch N buffer: APR drops, user requests instant withdraw ---
    vm.prank(manager);
    IdleCreditVault(strategy).setAprs(unscaledApr - bigDelta, scaledApr - scaledDelta);
    uint256 instantAmt = 1_000_000 * ONE_SCALE;
    cdoEpoch.requestWithdraw(instantAmtTranches, address(AAtranche)); // routes to instant

    // --- startEpoch with insufficient liquidity: only part of instant queue funded ---
    // (borrower funded so CDO cash < pendingInstantWithdraws)
    _startEpochPartialInstantFunding();
    assertGt(IdleCreditVault(strategy).pendingInstantWithdraws(), 0);

    // --- epoch runs, stopEpoch bumps epochNumber; user does NOT claim ---
    vm.warp(cdoEpoch.epochEndDate() + 1);
    _stopEpochFullyFunded();
    assertGt(IdleCreditVault(strategy).epochNumber(), requestEpoch);

    // --- next epoch defaults; finalizeDefault misses the stale receipt ---
    _triggerBorrowerDefault();
    vm.prank(owner);
    cdoEpoch.finalizeDefault(recovered40pct, recoverySource);
    uint256 price = IdleCreditVault(strategy).defaultRecoveryPrice();
    assertLt(price, 1e18);

    // --- stale receipt pays at PAR, not at recovery price ---
    uint256 balPre = usdc.balanceOf(user);
    vm.prank(address(cdoEpoch));
    IdleCreditVault(strategy).claimInstantWithdrawRequest(user);
    uint256 paid = usdc.balanceOf(user) - balPre;
    assertEq(paid, instantAmt);                          // par payout
    assertGt(paid, instantAmt * price / 1e18);           // exceeds fair share
    // recovery claimants are shorted by paid - instantAmt * price / 1e18
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L367-374)
```text
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L381-393)
```text
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-723)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-856)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-907)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
  }
```
