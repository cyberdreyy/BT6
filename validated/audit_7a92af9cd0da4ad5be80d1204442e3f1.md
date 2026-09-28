### Title
Mid-epoch deposits are over-minted when `pendingWithdrawFees >= expectedEpochInterest`, letting an attacker steal epoch interest from existing tranche holders - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
Prototype pollution in vm2 lets an attacker corrupt shared program state through attacker-controlled inputs. The closest credit-vault analog is attacker-controlled pollution of the shared epoch accounting (`pendingWithdrawFees`) that feeds the share-pricing formula in `depositDuringEpoch`. When `expectedEpochInterest <= pendingWithdrawFees`, the code silently sets `trancheExpected = 0`, so the "expected final NAV" denominator ignores interest that existing holders will still accrue. A mid-epoch depositor is then minted more tranche tokens than the fair `(amount + trancheInterest)` entitlement, and at `stopEpoch` those extra shares claim a slice of other holders' interest.

### Finding Description
In `depositDuringEpoch` (IdleCDOEpochVariant.sol:656-725), shares are minted as:

```solidity
if (expectedInt > pendingFees) {
  trancheExpected = _calcTrancheInterestShare(
    _netGainAfterFees(expectedInt - pendingFees, _calculateManagementFee(lastNAVAA + lastNAVBB, remaining)),
    _tranche
  );
}
uint256 expectedFinal = _lastSavedNAV(_tranche) + trancheExpected;
_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
```

The fair price for a share that will be worth `lastNAV + existingHoldersInterest` at epoch end requires `expectedFinal` to include the existing holders' projected interest. When `expectedInt <= pendingFees`, `trancheExpected` collapses to 0 instead of reflecting the residual interest holders will actually receive. Note the accounting is asymmetric: at `stopEpoch`, `pendingWithdrawFees` is paid to fee receivers and the remaining `expectedEpochInterest` still accrues to tranche NAVs via `_updateAccounting` — but the deposit formula treated it as zero.

`pendingWithdrawFees` is fully attacker-controllable: `requestWithdraw` adds `_totalWithdrawFees(principal, interest)` to it (line 778), which charges an upfront management fee over `_withdrawRequestManagementFeeDuration()` = one full epoch plus remaining buffer (lines 900-931). A KYC'd lender can therefore inflate `pendingWithdrawFees` past `expectedEpochInterest` by submitting withdraw requests (with Wallet A), then call `depositDuringEpoch` (with Wallet B) while the condition holds.

Concrete effect: minted ≈ `(amount + trancheInterest) * supply / lastNAV` instead of `(amount + trancheInterest) * supply / (lastNAV + othersInterest)`. The minted shares are redeemable at the post-stop tranche price `(NAV + allInterest) / totalSupply`, so the attacker captures `minted * othersInterest/totalSupply`-worth of value that belongs to pre-existing holders.

Guards do not stop it: `_skimDonatedAssets`, `_updateAccounting`, `_guarded`, and `isWalletAllowed` all behave normally; the check `_trancheTotSupply == 0` revert only blocks the first-depositor edge, not this path.

### Impact Explanation
Direct theft of tranche yield: the attacker exits the epoch holding shares worth more than `amount + trancheInterest`, extracting a pro-rata portion of the interest that existing AA/BB holders accrued for the epoch. Loss magnitude ≈ `trancheExpected (suppressed) * minted / (supply + minted)` — i.e., a large deposit relative to tranche TVL can capture a significant fraction of an entire epoch's interest for that tranche.

### Likelihood Explanation
Requires: an epoch vault with `depositDuringEpoch` enabled (fixed-APR mode, non-AYS, non-programmable — all checked at lines 658-669), a management fee high enough (or withdraw requests large enough) that cumulative `pendingWithdrawFees` exceeds `expectedEpochInterest`, and the attacker to pass `isWalletAllowed` (KYC, in-scope). The attacker bears only the opportunity cost of the receipt locked in Wallet A's withdraw request, which is itself claimable after the epoch. The preconditions are ordinary protocol states, not privileged or adversarial configuration.

### Recommendation
When `expectedInt <= pendingFees`, `trancheExpected` should still reflect the net interest existing holders will accrue (it should floor at the true residual, or the formula should compute `trancheExpected = max(0, trancheShare)` independently of the pending-fees comparison rather than zeroing the whole projection). Alternatively, cap `_minted` at the value implied by the non-discounted current tranche price (`_amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche)`) so mid-epoch depositors can never receive more shares than a normal depositor.

### Proof of Concept
Foundry fork test (sketch) against `IdleCDOEpochVariant` / `IdleCreditVault` test harness in `test/foundry/IdleCreditVault.t.sol`:

```solidity
// Pre: epoch running, fixed APR, depositDuringEpoch enabled, mgmt fee > 0
// Wallet A (attacker, KYC'd, holds tranche tokens):
idleCDO.requestWithdraw(largeAmount, AA);   // pendingWithdrawFees += _totalWithdrawFees(...)
// Now pendingWithdrawFees > expectedEpochInterest => trancheExpected == 0

// Wallet B (attacker): depositDuringEpoch before epoch end
uint256 minted = idleCDO.depositDuringEpoch(amount, AA);

// Owner/manager: warp to epochEndDate, stopEpoch -> interest distributed
// Assert: redeem value of `minted` > amount + trancheInterest attributable to amount,
// i.e. attacker captured interest owed to pre-existing holders.
assertGt(trancheValueOf(minted), amount + attackerTrancheInterest);
```

Caveat: I verified the pricing asymmetry and the `requestWithdraw` → `pendingWithdrawFees` inflation path, but could not fully simulate whether realistic `managementFee` configurations push `pendingWithdrawFees` above `expectedEpochInterest` in a deployed vault; if management fee is zero and performance-fee-only withdraw fees are small, the precondition may be unreachable. If `pendingWithdrawFees` can never exceed `expectedEpochInterest` in practice, this reduces to a non-issue.