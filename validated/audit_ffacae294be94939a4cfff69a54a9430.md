### Title
Unprivileged lenders lock in stale-APR withdrawal receipts by timing manager/owner parameter updates that take effect instantly with no timelock - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The external report's bug class — admin changes taking effect immediately so users cannot rely on the behavior of a call — maps directly onto `IdleCDOEpochVariant`/`IdleCreditVault`: `setAprs`/`setApr`/`setAprsWithBuffer` (strategy, callable by `manager`), `setTrancheAPRSplitRatio` (`IdleCDO.sol:900`), `setInstantWithdrawParams`, `setEpochParams` and `setFeeParams` all take effect atomically with no notice period. Unlike the zAuction case, here the interest locked into a withdraw receipt (`requestWithdraw`) and the shares minted by `depositDuringEpoch` are computed from these mutable parameters at call time, while the cash that funds those receipts is computed later at `startEpoch`/`stopEpoch` from the *new* parameter values. An unprivileged (KYC-passing) lender can therefore sequence a `requestWithdraw` immediately before an honest manager APR reduction and crystallize a receipt priced at the old, higher APR, extracting interest the borrower was never expected to owe.

### Finding Description
`requestWithdraw` is only callable between epochs (`allowAAWithdrawRequest`/`allowBBWithdrawRequest` are false while `isEpochRunning`, `IdleCDOEpochVariant.sol:739-745`). It prices the receipt using the *currently stored* scaled APR via `_calcInterestWithdrawRequest` → `_calcInterest` → `_getStrategyApr()` (`IdleCreditVault.lastApr`), minting a fixed receipt `_amount = principal + interest − fees` in `IdleCreditVault.requestWithdraw` (`IdleCreditVault.sol:273-293`) and adding it to `pendingWithdraws`.

Two facts create the gap:

1. `IdleCreditVault.setApr`/`setAprs`/`setAprsWithBuffer` can be called by `manager` at **any** time, including mid-buffer between `stopEpoch` and `startEpoch` (`IdleCreditVault.sol:206-235`). There is no timelock and no epoch-phase gating on `setApr`.
2. At the next `startEpoch`, the borrower's liability `expectedEpochInterest` is recomputed from `getContractValue()` using the **new** APR (`_stopEpoch`→`startEpoch`, `IdleCDOEpochVariant.sol:260-262`), while the already-minted receipt retains the **old** APR amount in `pendingWithdraws`, which `stopEpoch` obliges the borrower to fund in full (`collectWithdrawFunds`, `IdleCreditVault.sol:411-430`).

So: epoch N stops with scaled APR = 12% (set via `_setScaledApr(_newApr)` at `IdleCDOEpochVariant.sol:471`). During the buffer the honest manager submits `setAprs` lowering it to 8% for epoch N+1. An attacker watching the mempool frontruns with `requestWithdraw(full balance)`: their interest is computed at 12%, the receipt is minted, and `pendingWithdraws` is owed that amount at the next `stopEpoch` even though epoch N+1 interest accrues at 8%. The excess (principal × Δapr × epochDuration/365) is paid by the borrower out of the same pool that funds other pending receipts; if the borrower funds only the correct amount, the shortfall is socialized to remaining LPs via `lossRecoveryPriceByEpoch`/`_claimLossAdjustedWithdrawRequest` (`IdleCreditVault.sol:417-421, 789-801`).

The same class applies to `trancheAPRSplitRatio` (`IdleCDO.sol:900`): `requestWithdraw` interest share and `depositDuringEpoch` minting math (`_calcTrancheInterestShare`, `IdleCDOEpochVariant.sol:883-886, 705-724`) both read the live ratio, so an attacker can request an AA withdraw (or BB deposit) immediately before an honest ratio update and lock the more favorable split. `setInstantWithdrawParams`/`setEpochParams` similarly change `requestWithdraw` outcomes (instant vs. one-epoch lock, interest duration) atomically with no warning.

### Impact Explanation
Direct yield extraction / NAV dilution. Quantified: attacker gain ≈ `principal × (oldApr − newApr) × epochDuration / 365 days` per buffer-period exploit, bounded only by `maxApr` and the attacker's KYC'd tranche balance. The loss is borne by the borrower (who funds `pendingWithdraws` at the stale rate) or, on partial funding, by all other pending-receipt holders and remaining LPs through the loss-adjusted claim price. The "fair mint/burn" invariant is broken: the attacker's receipt is minted at a rate that no longer corresponds to the epoch's actual APR.

### Likelihood Explanation
Requires only a KYC-passing lender front-running an honest manager `setApr`/`setAprs`/`setTrancheAPRSplitRatio` transaction during a buffer period — a routine, expected operation (APR is explicitly intended to change every epoch via `stopEpoch(_newApr,...)`). No malicious privileged role, no oracle, no reentrancy needed. Guards that do *not* stop it: `maxApr` caps the rate but not the delta, `_onlyIdleCDO`/KYC/`allowAAWithdrawRequest` all pass for a legitimate lender, and `_skimDonatedAssets` is irrelevant.

### Recommendation
Apply the report's own remediation: gate APR/split-ratio/fee changes behind a two-step timelock or, better, constrain when they apply. Concretely: (a) only allow `setApr`/`setAprs` effects to take hold at the next `stopEpoch`/`startEpoch` boundary so the APR used for `requestWithdraw` receipts always equals the APR used for `expectedEpochInterest`; (b) alternatively, snapshot the APR that priced each epoch's receipts and compute `pendingWithdraws` funding from that snapshot rather than the live value; (c) add a two-step `propose/apply` flow with a minimum delay for `setTrancheAPRSplitRatio`, `setInstantWithdrawParams`, `setFeeParams`, and strategy `setApr*`, and validate new addresses (`feeReceiver`, `keyring`, `idleCDO` in `setWhitelistedCDO`) against zero/current values.

### Proof of Concept
Foundry fork test sketch:

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";

contract StaleAprReceiptTest is Test {
  // setup: deploy IdleCDOEpochVariant + IdleCreditVault via IdleCreditVaultFactory,
  // apr 10% (scaled), epochDuration 30d, bufferPeriod 5d, KYC'd attacker + victim LPs.

  function testFrontrunAprChangeLocksStaleReceipt() public {
    // 1. attacker + victim deposit AA during buffer; epoch 1 starts and runs.
    // 2. manager calls stopEpoch(10%, 0) -> lastApr stays 10% for epoch 2.
    // 3. Buffer: manager broadcasts setAprs lowering to 6%.
    // 4. vm.prank(attacker) creditVault CDO.requestWithdraw(full, AATranche)
    //    -> _calcInterestWithdrawRequest uses 10% -> receipt amount R10 minted,
    //       pendingWithdraws += R10.
    // 5. vm.prank(manager) strategy.setAprs(6%...)  (honest tx lands second)
    // 6. startEpoch(); expectedEpochInterest computed at 6% on remaining NAV.
    // 7. stopEpoch(0,0): borrower must fund pendingWithdraws == R10,
    //    which includes interest at 10% while the epoch earned 6%.
  }
}
```

Assert: `withdrawsRequestsByEpoch[attacker][epoch]` implies interest at the stale 10% while `expectedEpochInterest` for the epoch was computed at 6%; attacker's `claimWithdrawRequest` payout exceeds what the current APR would have priced, with the delta (attackerPrincipal × 4% × 30/365) drawn from the borrower pool or haircut onto other claimants via `lossRecoveryPriceByEpoch`.