### Title
Post-default instant withdrawals bypass the default-routing guard in `requestWithdraw`, letting users mint un-haircutted receipts into the defaulted epoch bucket and drain `defaultRecoveryReserve` — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestWithdraw` contains a dedicated `defaultRecoveryFinalized` branch that routes post-default requests into `postDefaultRequests` and rejects users with outstanding receipts. The sibling entry point `requestInstantWithdraw` has no equivalent post-default handling: after recovery is finalized it keeps recording receipts under `instantWithdrawsRequestsByEpoch[user][epochNumber]`, where `epochNumber` is frozen at `defaultRecoveryEpoch`. Those receipts then claim against `defaultRecoveryReserve` through `_claimDefaultedInstantWithdrawRequest`, spending reserve that was sized exactly for pre-finalization claims — the same class of bug as the GPToke `extend()` bypassing the `stake()` cap check.

### Finding Description
In `IdleCreditVault.requestWithdraw`, once `defaultRecoveryFinalized` is true the function reverts unless the caller has no pending normal, instant, or post-default requests, and then records the request in `postDefaultRequests` instead of `pendingWithdraws`, precisely so post-default claims are paid 1:1 from the reserve and cannot be confused with defaulted-epoch receipts (IdleCreditVault.sol L243-L258).

`requestInstantWithdraw` performs none of this. It unconditionally burns the CDO's strategy tokens, mints a receipt to the user, increments `instantWithdrawsRequests`, `instantWithdrawsRequestsByEpoch[user][epochNumber]`, `instantWithdrawClaimsByEpoch[epochNumber]`, and `pendingInstantWithdraws` (IdleCreditVault.sol L356-L375). After a default there are no further epochs, so `epochNumber` stays equal to `defaultRecoveryEpoch` (it is only incremented inside `deposit()` during the epoch lifecycle, L609-L610).

When the attacker later calls `claimInstantWithdrawRequest`, because `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized` is true, `_claimDefaultedInstantWithdrawRequest` looks up `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` — which now contains the post-default receipt — and pays `claimBasis * defaultRecoveryPrice / 1e18` out of `defaultRecoveryReserve` (IdleCreditVault.sol L380-L393, L842-L856). `finalizeDefaultRecovery` sized the reserve to `reserveAmount` covering only the pre-finalization claim basis (`activeBasis + pendingBasis`), and `_transferDefaultRecovery` simply decrements the reserve (L686-L692, L912-L917). Every unit of post-default instant receipt therefore steals recovery value belonging to legitimate defaulted-epoch claimants, and the last claimants will find the reserve exhausted (insolvency for them).

### Impact Explanation
Direct theft / insolvency: an attacker holding tranche tokens converts already-repriced active NAV (whose loss was crystallized via `activeFinalNAV` in `finalizeDefaultRecovery`) into an additional reserve-denominated claim. They extract up to `amount * defaultRecoveryPrice` of reserve per request — reserve earmarked for pre-default receipt holders — leaving the recovery pool short by exactly that amount and causing later claims to underflow or underpay.

### Likelihood Explanation
Requires (a) the vault operating with instant withdrawals enabled so `pendingInstantWithdraws != 0` at finalization (making `defaultInstantWithdrawsFinalized` true), (b) a borrower default followed by recovery finalization, and (c) the attacker holding tranche tokens the CDO can burn via the instant-withdraw entry point. None of the existing guards stop it: `_onlyIdleCDO` is satisfied through the normal CDO call path, the `_transferFundedClaim` reserve guard never triggers because payout goes through `_transferDefaultRecovery`, and `epochNumber` cannot move past `defaultRecoveryEpoch` once epochs halt. Uncertainty: the exact CDO-side entry (e.g. in `IdleCDOEpochVariant`) that forwards to `requestInstantWithdraw` after finalization could not be fully traced in this session; if that entry is gated on a running epoch, the bug is unreachable.

### Recommendation
Mirror the `requestWithdraw` post-default logic in `requestInstantWithdraw`: when `defaultRecoveryFinalized` is true, revert if the user has any pending requests and record the request as a post-default claim paid 1:1, or reject post-default instant requests outright. Alternatively, key the defaulted-epoch claim lookup so receipts created after `defaultRecoveryEpoch` are excluded.

### Proof of Concept
```solidity
// Foundry fork test sketch
// 1. Deploy IdleCDOCreditVault + IdleCreditVault, KYC lender deposits AA and BB,
//    instant withdrawals enabled. startEpoch; borrower borrows funds.
// 2. Victim calls instantWithdraw -> requestInstantWithdraw leaves
//    pendingInstantWithdraws != 0 (funds only partially collected).
// 3. Epoch ends without borrower repayment -> _handleBorrowerDefault ->
//    finalizeDefaultRecovery(recoveredAmount, source):
//    defaultRecoveryReserve sized for basis = activeBasis + pendingBasis;
//    defaultInstantWithdrawsFinalized = true; epochNumber == defaultRecoveryEpoch.
// 4. Attacker (tranche holder) calls CDO instantWithdraw post-finalization:
//    requestInstantWithdraw records
//    instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch] += amount
//    and pendingInstantWithdraws += amount, with no post-default branch.
// 5. Attacker calls claimInstantWithdrawRequest:
//    _claimDefaultedInstantWithdrawRequest pays amount * defaultRecoveryPrice
//    from defaultRecoveryReserve even though the receipt was never in the basis.
// 6. Victim's defaulted-epoch instant claim then reverts/underpays:
//    assert defaultRecoveryReserve < accounted basis -> theft demonstrated.
```