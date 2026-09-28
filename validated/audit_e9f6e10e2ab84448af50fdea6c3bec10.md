### Title
Pending withdraw receipts re-enter interest accrual base, inflating `_calcInterestWithdrawRequest` payouts - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`requestWithdraw` mints a strategy-token receipt to the user equal to principal **plus** already-computed interest, while the CDO keeps that receipt value inside the managed pool base that `_calcInterestWithdrawRequest` later uses as `totInterest` input. Interest already embedded in outstanding receipts is therefore counted again when computing the interest owed to the *next* withdraw requester — the same class of bug as LoopFi's `calcDecrease`, where `profit` already containing `cumulativeQuotaInterest` was reused to derive `newCumulativeIndex`, pushing it past `cumulativeIndexNow`.

### Finding Description
In `requestWithdraw` (`IdleCDOEpochVariant.sol:739-791`), the projected interest for the requester is computed via `_calcInterestWithdrawRequest`, then the vault calls `creditVault.requestWithdraw(_underlyings, msg.sender, principal)` where `_underlyings` = principal + interest. In `IdleCreditVault.requestWithdraw` (`contracts/strategies/idle/IdleCreditVault.sol:243-295`) the CDO burns only `_principal` but mints the full `_amount` (principal + interest) to the user as a receipt, and adds `_amount` to `pendingWithdraws`.

The next requester's interest is computed in `_calcInterestWithdrawRequest` (`IdleCDOEpochVariant.sol:856-878`):

```solidity
uint256 totInterest = _calcInterest(_managedContractValue()) * _duration / (_duration + _buffer);
uint256 totTrancheInterest = _calcTrancheInterestShare(totInterest, _tranche);
uint256 _trancheBal = _lastSavedNAV(_tranche);
_interest = _amount * totTrancheInterest / _trancheBal;
```

The interest share of the *whole* tranche (`totTrancheInterest`) is derived from `_managedContractValue()`, which still reflects pending receipts whose value already embeds their promised interest (receipts are minted with interest included, while only principal was burned — the strategy-token supply/NAV base grows by the interest amount). The new requester therefore earns a pro-rata share of interest on base that already includes *other users'* promised interest, exactly as the external report's `profit` included `cumulativeQuotaInterest` in the denominator/numerator used for `newCumulativeIndex`. Analogously, `_diff` (`interestWithoutSplitRatio - _interest`) is then pushed into `interestForOverUnderPerformance` (`IdleCDOEpochVariant.sol:784`), which is later folded back into `expectedEpochInterest` at `startEpoch`, compounding the mispricing into the next epoch's fixed-APR accounting.

Note: the severity depends on whether `_managedContractValue()` excludes `pendingWithdraws`/receipt-embedded interest; if it does not, the double count stands. My index access did not include the `_managedContractValue` body, so that exclusion cannot be confirmed from what I retrieved — the finding holds under the reading that the managed base includes the minted receipt value.

### Impact Explanation
Each successive withdraw requester in the same epoch is credited interest computed on a base inflated by previously promised (but unclaimed) receipt interest. Later requesters mint receipts worth more than their fair share, while the NAV backing remaining tranche holders is correspondingly drained at claim time — a direct transfer of yield from non-withdrawing tranche holders to earlier/later requesters, and, if aggregated, more `pendingWithdraws` than the borrower/strategy can actually fund at `stopEpoch`, i.e. an insolvency/shortfall at settlement rather than merely a pricing display error.

### Likelihood Explanation
Triggering requires only a normal user action sequence inside a running epoch: one or more `requestWithdraw` calls leave pending receipts, then a subsequent `requestWithdraw` (or `maxWithdrawable`-priced claim path) computes interest over the inflated managed base. No privileged actor is needed; KYC'd tranche holders performing ordinary withdraw requests in sequence hit the inflated base automatically. Frequency is bounded by how much pending receipt value accumulates within one epoch.

### Recommendation
Compute `totInterest` on a base that excludes the interest already promised in pending receipts — e.g. use `_managedContractValue() - (pendingWithdraws - pendingPrincipal)` (receipt face value minus its principal component), or store per-epoch "interest already allocated to receipts" and subtract it in `_calcInterestWithdrawRequest`. Equivalently, keep receipts principal-only at mint time and settle interest at claim, mirroring the mitigation of using `amountToRepay` instead of `profit` (which already contained `cumulativeQuotaInterest`) in the LoopFi fix.

### Proof of Concept
```solidity
// foundry fork test sketch
// 1) epoch running with AA deposits; userA deposits amountA.
// 2) userA.requestWithdraw(amountA_tranche) -> receipt minted for principal+interest_A;
//    pendingWithdraws += principal+interest_A; only principal burned.
// 3) userB.requestWithdraw(amountB_tranche)
//    -> _calcInterestWithdrawRequest computes totInterest on _managedContractValue()
//       which still counts userA's receipt value incl. interest_A
//    -> interest_B > amountB * (epochAPR * duration) / trancheNAV_without_receipt_interest
// 4) warp past buffer; borrower funds stopEpoch with true interest only;
//    claimWithdrawRequest(userA) + claimWithdrawRequest(userB) total >
//    funded amount -> revert/shortfall, or remaining tranche NAV holders absorb the gap.
assertGt(interest_B, expectedInterestOnCleanBase);
```