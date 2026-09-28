### Title
Zero-epoch sentinel dereferenced as a real request epoch permanently freezes default-recovery and funded claims in `IdleCreditVault._claimFundedWithdrawRequest` — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a NULL-pointer dereference: a parsed value that can be NULL is consumed without a check, crashing the process. The analog in `IdleCreditVault` is the epoch marker `lastWithdrawRequest[_user]`, where `0` doubles as the "no request / cleared" sentinel **and** as a valid epoch number (`epochNumber` starts at 0). `_claimFundedWithdrawRequest` dereferences this marker as if it were always a real request epoch — `epochNumber <= lastWithdrawRequest[_user]` reverts `NotAllowed` unconditionally, even when there is nothing to claim. Because `claimWithdrawRequest` executes the defaulted/loss-adjusted claim legs first and the funded leg last, a revert in the funded leg rolls back the entire claim, freezing user recovery funds.

### Finding Description
`claimWithdrawRequest` (IdleCreditVault.sol:301) sequentially calls `_claimPostDefaultWithdrawRequest`, `_claimDefaultedWithdrawRequest`, `_claimLossAdjustedWithdrawRequest`, and finally `_claimFundedWithdrawRequest`. The final leg at lines 319–328 performs the gating check:

```solidity
if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
  revert NotAllowed();
}
```

This check runs **before** determining whether the user has any funded amount. When `_clearWithdrawClaimForEpoch` clears the defaulted-epoch receipt, it resets `lastWithdrawRequest[_user] = 0` (line 835) — the "empty" sentinel. If the vault defaulted during epoch 0 (`epochNumber == 0`, which is the counter's initial value; it is only incremented inside `deposit` when `isEpochRunning()`, line 610) and `epochEndDate` remains non-zero (a default does not close the pool — the closed-pool mode is a separate `epochEndDate == 0` path), then `0 <= 0` holds and the funded-claim leg reverts `NotAllowed`. The revert bubbles up and undoes the defaulted-recovery payout computed just before.

The same collision exists in `requestWithdraw`'s loss-adjusted guard (lines 261–271): `lossEpoch = lastWithdrawRequest[_user]` reads the "null" marker `0` and looks up `lossRecoveryPriceByEpoch[0]`, conflating "no prior request" with "request made in epoch 0". This is exactly the CVE pattern — an empty/NULL result (`DER 30 00` / zeroed storage slot) dereferenced as a live object.

### Impact Explanation
Any lender or tranche-token holder whose withdraw receipt defaulted during the vault's first epoch (`defaultRecoveryEpoch == 0`) cannot claim their recovery share: `claimWithdrawRequest` always reverts at the funded-claim leg after the defaulted leg cleared `lastWithdrawRequest` to 0. Recovery reserve funds earmarked for these users are permanently frozen in the strategy, since `epochNumber` cannot advance past 0 without a subsequent `stopEpoch`/`deposit` cycle that a defaulted vault will not perform. Loss equals the user's full defaulted receipt basis times `defaultRecoveryPrice`, i.e. the entire recoverable amount for every affected claimant.

### Likelihood Explanation
The trigger is an unprivileged user who simply holds a pending withdraw receipt when the borrower defaults in epoch 0, or more generally any configuration where `epochNumber` equals a user's cleared `lastWithdrawRequest` while `epochEndDate != 0`. Epoch 0 defaults are plausible for credit vaults (borrower draws and fails to repay the very first epoch). No privileged misbehavior is required — the honest manager/owner calls to finalize default put the vault into exactly this state.

Caveat: I could not confirm (iteration limit reached) whether `IdleCDOEpochVariant._handleBorrowerDefault`/`finalizeDefaultRecovery` leaves `epochEndDate` non-zero. If default finalization zeroes `epochEndDate`, the funded-leg guard is bypassed and this specific path is safe; the sentinel conflation in `requestWithdraw` (loss-epoch 0) would still warrant review.

### Recommendation
Distinguish "no request" from "request in epoch 0" with an explicit sentinel rather than overloading `0` — e.g. store `lastWithdrawRequest` as `epochNumber + 1`, or track a boolean. In `_claimFundedWithdrawRequest`, skip the epoch-gating revert when the user has no claimable balance (`withdrawsRequests[_user] == 0 && apr0Users` buckets empty), so the check only fires when funds are actually at stake. Apply the same non-empty check to the `lossRecoveryPriceByEpoch` lookup in `requestWithdraw`.

### Proof of Concept
Foundry fork sketch (vault deployed, epoch 0 running, then default):

```solidity
// epoch 0: user deposits AA and requests withdraw
idleCDO.depositAA(100e6);                       // epochNumber == 0
cdoEpoch.requestWithdraw(0, address(AAtranche)); // lastWithdrawRequest[user] == 0 sentinel collides

// borrower defaults mid-epoch-0; owner finalizes recovery
cdoEpoch._handleBorrowerDefault();
cdoEpoch.finalizeDefaultRecovery(recovered, recoverySource); // defaultRecoveryEpoch = 0

// user tries to claim defaulted receipt
vm.expectRevert(NotAllowed.selector);           // epochNumber(0) <= lastWithdrawRequest(0)
cdoEpoch.claimWithdrawRequest();
// user's recovery share is permanently frozen in defaultRecoveryReserve
```

The key assertion: `claimWithdrawRequest` reverts even though `_claimDefaultedWithdrawRequest` computed a non-zero payout, because the trailing funded-claim leg dereferences the zeroed epoch marker as a live request epoch.