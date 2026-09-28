### Title
Post-default withdraw receipts drain `defaultRecoveryReserve` earmarked for defaulted-epoch claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a credential scoped to one host leaking to a redirected host: state valid in context A is silently spent in context B. The analog in `IdleCreditVault` is `defaultRecoveryReserve` — an underlying pool sized, at `finalizeDefaultRecovery`, exactly for the defaulted epoch's claim basis (`activeBasis + pendingBasis`) — being spent 1:1 by *post-default* withdraw requests (`postDefaultRequests`) that were never part of that basis and never added any funding to the reserve. Each post-default claim steals recovery underlying from defaulted-epoch receipt holders.

### Finding Description
`finalizeDefaultRecovery` computes `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` and `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis`, then stores `defaultRecoveryReserve = reserveAmount` (lines 685–692). `totalBasis` covers only active LPs and pre-existing pending receipts — it does **not** include any future `postDefaultRequests`.

After finalization, `requestWithdraw` takes the `defaultRecoveryFinalized` branch (lines 247–257): it burns `_amount` strategy tokens from the CDO, mints a receipt to the user, and records `postDefaultRequests[_user] = _amount`. Critically, **no `safeTransferFrom` pulls underlying in**, and `defaultRecoveryReserve` is not increased — contrast with `collectWithdrawFunds`/`collectInstantWithdrawFunds`, which pull funding, and `reserveDefaultRecovery`, which is blocked once `defaultRecoveryFinalized` (line 631).

`_claimPostDefaultWithdrawRequest` then pays the full request 1:1 via `_transferDefaultRecovery`, which decrements `defaultRecoveryReserve` (lines 760–767, 912–917). The per-epoch scoping that protects every other claim path (`withdrawsRequestsByEpoch`, `instantWithdrawsRequestsByEpoch`, `lossRecoveryPriceByEpoch`) does not apply here: post-default receipts consume the same reserve bucket that `defaultRecoveryPrice` assumed belonged entirely to defaulted-epoch claimants.

### Impact Explanation
Broken invariant: isolation of the default recovery distribution (one receipt, one payout from a correctly-sized reserve). A post-default requester who calls `claimWithdrawRequest` before the legitimate defaulted claimants extract their share removes underlying pro tanto from the reserve. Since `recoveryPrice` assumed `reserveAmount / totalBasis`, the last defaulted claimants will find `defaultRecoveryReserve` underflowing (`defaultRecoveryReserve -= _amount` reverts) or the strategy balance insufficient — their recovery is permanently stolen/unclaimable. Loss is bounded by the sum of post-default requests but can reach nearly the whole reserve if post-default request volume is large. Attacker is any ordinary post-default withdrawer (KYC'd lender/tranche holder via the CDO), requiring no privileged role.

### Likelihood Explanation
Requires the pool to go through `_handleBorrowerDefault` → `finalizeDefaultRecovery`, after which the vault keeps serving request/claim UX by design (per the comments). Any user then requesting a withdraw gets a fully-funded-looking receipt paid from the misappropriated reserve on a first-come-first-served basis. Honest sequencing — not even front-running — suffices; the first post-default claimer wins over defaulted claimants regardless of intent. Uncertainty: I could not fully inspect `IdleCDOEpochVariant`'s post-default `requestWithdraw` wrapper to confirm whether it pre-funds the strategy for post-default requests out-of-band; nothing in `IdleCreditVault.requestWithdraw` pulls or accounts for such funding, and `reserveDefaultRecovery` is explicitly blocked post-finalization, so the leak appears real on the strategy's own accounting.

### Recommendation
Either fund post-default requests with real underlying (pull from the CDO/borrower in `requestWithdraw` or a dedicated collect function that increments `defaultRecoveryReserve`), or pay them from a segregated balance rather than `_transferDefaultRecovery` — e.g., extend `_transferFundedClaim`'s reserve guard to post-default claims. Simplest: add `defaultRecoveryReserve += _amount` funding or route the payout through a non-reserve transfer so `reserveAmount / totalBasis` remains the invariant for defaulted claimants.

### Proof of Concept
Foundry fork scenario:

```solidity
// 1. Epoch runs; userA has pending withdraw receipt (withdrawsRequestsByEpoch[userA][E] > 0).
// 2. Borrower defaults; owner/manager calls finalizeDefaultRecovery(recovered, source).
//    defaultRecoveryReserve == recovered, priced for userA's basis.
// 3. Attacker (userB, tranche holder) calls cdoEpoch.requestWithdraw(...) post-finalization.
//    Strategy mints receipt, sets postDefaultRequests[userB], pulls NO underlying.
// 4. userB calls cdoEpoch.claimWithdrawRequest() -> _claimPostDefaultWithdrawRequest
//    pays userB 1:1, decrementing defaultRecoveryReserve.
// 5. userA calls claimWithdrawRequest() -> _claimDefaultedWithdrawRequest
//    -> _transferDefaultRecovery reverts or pays less than claimBasis * defaultRecoveryPrice.
// assert userB received amount while userA's entitled recovery is short by that amount.
```