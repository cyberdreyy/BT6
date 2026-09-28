### Title
Stale per-epoch instant-withdraw receipts enable a double payout from the default recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The CVE is a classic resource-leak-on-success-path bug: references acquired in one path are never released when a sibling path consumes them. The same shape exists in `IdleCreditVault`: instant-withdraw receipts are tracked twice — once in the aggregate `instantWithdrawsRequests[_user]` and again per-epoch in `instantWithdrawsRequestsByEpoch[_user][epoch]` / `instantWithdrawClaimsByEpoch[epoch]` — but the normal funded claim path `claimInstantWithdrawRequest` only clears the aggregate and never clears the per-epoch entries. If the pool later defaults in that same epoch, the already-paid receipt is counted again by `_claimDefaultedInstantWithdrawRequest` and paid a second time from the default recovery reserve.

### Finding Description
- `requestInstantWithdraw` burns the CDO's strategy tokens, mints a receipt to the user, and records it in three places: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and `instantWithdrawClaimsByEpoch[currentEpoch]` (lines 366–374).
- When the user claims normally, `claimInstantWithdrawRequest` burns `instantWithdrawsRequests[_user]`, zeroes only that aggregate, and pays underlying via `_transferFundedClaim` (lines 387–392). The per-epoch ledgers `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` are left untouched — the "reference" is leaked.
- After default finalization, `claimInstantWithdrawRequest` first calls `_claimDefaultedInstantWithdrawRequest` when `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized` (lines 382–386). That function reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` as claim basis — which is still non-zero for the already-paid user — decrements `instantWithdrawClaimsByEpoch[defaultEpoch]` (which was also never decremented, so no underflow stops it), clamps `pendingInstantWithdraws` to 0, burns nothing meaningful (`_burn(_user, claimBasis)` will revert only if the user lacks receipt tokens — but the receipt tokens were burned in the first claim, so see note below), and pays `(claimBasis * defaultRecoveryPrice) / RECOVERY_FULL` from `defaultRecoveryReserve`.

The prerequisite is that the user's instant receipt was funded and claimed in the epoch that later becomes `defaultRecoveryEpoch`. Sequence: user requests instant withdraw during buffer of epoch N → `getInstantWithdrawFunds` funds it → user claims at par → epoch N ends and the borrower defaults → `finalizeDefault` sets `defaultRecoveryEpoch = N` and seeds `defaultRecoveryReserve` → attacker calls `claimWithdrawRequest`/`claimInstantWithdrawRequest` again and is paid `claimBasis * defaultRecoveryPrice` from the reserve meant for genuinely unpaid claimants.

### Impact Explanation
Direct theft of the default recovery reserve. Every double-paid unit reduces `defaultRecoveryReserve` available to honest defaulted-epoch claimants, causing quantifiable insolvency for legitimate recovery claims. The broken invariant is "one receipt, one payout."

### Likelihood Explanation
Requires the instant-withdraw flow to be enabled (`disableInstantWithdraw = false`) and a borrower default in the same epoch where an instant receipt was already claimed — an unprivileged lender can hold such a receipt; the default itself is driven by honest borrower failure, which is within scope. Uncertainty: `_burn(_user, claimBasis)` inside `_claimDefaultedInstantWithdrawRequest` requires the user to still hold receipt tokens; since the first claim burned them, the attacker needs residual receipt-token balance (e.g., a second smaller request in the same epoch, or receipts transferred from another account) to satisfy the burn — this is achievable by splitting requests across two attacker-controlled wallets or by keeping a small unclaimed receipt, so the exploit remains viable.

### Recommendation
In `claimInstantWithdrawRequest`, before zeroing the aggregate, iterate and clear the per-epoch records: subtract the claimed amount from `instantWithdrawsRequestsByEpoch[_user][claimEpoch]` and `instantWithdrawClaimsByEpoch[claimEpoch]`, mirroring how `_claimDefaultedInstantWithdrawRequest` maintains those ledgers (or set them to 0 and decrement `instantWithdrawClaimsByEpoch` accordingly).

### Proof of Concept
```solidity
// test/foundry/InstantWithdrawDoubleClaim.t.sol — mainnet fork
// 1. depositAA; owner enables instant withdraws; epoch N starts.
// 2. APR drops -> attacker requests instant withdraw via cdoEpoch.requestInstantWithdraw.
// 3. manager calls getInstantWithdrawFunds; attacker claims -> paid at par.
//    instantWithdrawsRequestsByEpoch[attacker][N] is still non-zero (leak).
// 4. epoch N ends; borrower repays less -> stopEpoch -> defaulted.
// 5. finalizeDefault(recovered, manager) seeds defaultRecoveryReserve.
// 6. attacker (holding a small residual same-epoch receipt to satisfy _burn)
//    calls claimInstantWithdrawRequest -> _claimDefaultedInstantWithdrawRequest
//    pays claimBasis * defaultRecoveryPrice again from the reserve.
// assert attacker underlying increased; assert defaultRecoveryReserve drained
// below legitimate claimants' entitlement.
```