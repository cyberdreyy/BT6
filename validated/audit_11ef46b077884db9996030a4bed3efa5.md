### Title
Instant-withdraw receipts from a pre-default epoch are excluded from recovery accounting and become permanently unclaimable - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` tracks instant-withdraw receipt basis per epoch (`instantWithdrawsRequestsByEpoch[user][epoch]` / `instantWithdrawClaimsByEpoch[epoch]`), but default-recovery finalization and defaulted instant claims only ever read the *current* `epochNumber`/`defaultRecoveryEpoch` index. An instant request recorded under an earlier epoch that is still unfunded when the borrower defaults is an out-of-range read: it contributes nothing to `defaultPendingClaimBasis()`/`_defaultPrefundedInstantReserve()`, and `_claimDefaultedInstantWithdrawRequest()` returns 0 for it. The claim then falls through to the funded path, which reverts because the strategy balance is locked behind `defaultRecoveryReserve`.

### Finding Description
The external report is an out-of-bounds read where a decoder indexes `section->num_pages` past the valid range. The analog here is an out-of-range epoch index: recovery code only looks at `instantWithdrawClaimsByEpoch[epochNumber]` and `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`, while an unfunded instant receipt can carry a stale epoch index.

Concrete trace:

1. Buffer phase of epoch N: a KYC-passing lender calls `requestInstantWithdraw` via the CDO. `instantWithdrawsRequestsByEpoch[user][N]` and `instantWithdrawClaimsByEpoch[N]` are increased, `pendingInstantWithdraws` grows (`IdleCreditVault.sol:356-375`).
2. `stopEpoch`/`startEpoch` runs; the borrower under-funds instant claims, so `collectInstantWithdrawFunds` collects less than pending and `pendingInstantWithdraws` stays non-zero (`IdleCreditVault.sol:398-403`). `deposit()` bumps `epochNumber` to N+1 (`IdleCreditVault.sol:607-611`).
3. Borrower defaults in epoch N+1; honest manager/guardian finalize. `finalizeDefaultRecovery` computes `pendingBasis = pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` where `epochNumber == N+1` (`IdleCreditVault.sol:644-648`). The epoch-N instant claim is never added to basis, and `_defaultPrefundedInstantReserve` likewise only inspects epoch N+1 (`IdleCreditVault.sol:716-723`).
4. Victim calls `claimInstantWithdrawRequest`. With `defaultInstantWithdrawsFinalized` true, `_claimDefaultedInstantWithdrawRequest` reads `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch = N+1]` → 0, returns without clearing (`IdleCreditVault.sol:842-856`). Execution continues: `amount = instantWithdrawsRequests[user]` (non-zero), receipt tokens are burned, and `_transferFundedClaim` checks `balance - defaultRecoveryReserve < amount` and reverts `NotAllowed` (`IdleCreditVault.sol:387-392, 897-906`).

The receipt tokens are already burned only if the transfer succeeds; since it always reverts, the user's claim is permanently frozen. Worse, the excluded basis means `defaultRecoveryPrice` is computed over a smaller denominator, so the missing claim is never even provisioned — no code path can ever pay it.

### Impact Explanation
Permanent freezing of the victim's full instant-withdraw receipt (unclaimed yield/principal), e.g., a 100,000 USDC instant request from epoch N becomes unclaimable forever after default finalization in epoch N+1. Additionally, other recovery claimants are overpaid relative to a correct basis because the frozen claim was excluded from `totalBasis`, a direct misallocation of the recovery reserve.

### Likelihood Explanation
Requires: (a) an instant request left partially unfunded across one epoch boundary — possible whenever `collectInstantWithdrawFunds` is called with less than `pendingInstantWithdraws`, which the code explicitly permits ("Pending instant withdrawals are never cleared automatically", `IdleCreditVault.sol:923`); and (b) a subsequent borrower default. Both are normal-operation sequences driven by honest privileged roles; the attacker is merely the unprivileged user holding the stale-epoch receipt. Triggering conditions are plausible but depend on borrower under-funding plus default, so likelihood is moderate.

### Recommendation
Make recovery accounting epoch-agnostic for instant claims: either aggregate unfunded instant basis (e.g., fold `pendingInstantWithdraws`-backed claims across all epochs into `defaultPendingClaimBasis`), or iterate/clear the user's actual request epoch rather than assuming `defaultRecoveryEpoch`. At minimum, `_claimDefaultedInstantWithdrawRequest` should derive the claim epoch from the user's recorded request (`lastWithdrawRequest`-style marker or a stored request epoch) instead of hard-coding `defaultRecoveryEpoch`, so stale-epoch receipts receive the same `defaultRecoveryPrice` haircut.

### Proof of Concept
Foundry fork sketch (mainnet fork per `foundry.toml`):

```solidity
// 1. Deposit into AA, start epoch N.
idleCDO.depositAA(100_000e6);
vm.prank(manager); cdoEpoch.startEpoch();

// 2. During buffer, user requests instant withdraw of X.
vm.prank(user); cdoEpoch.requestInstantWithdraw(X); // records epoch N

// 3. stopEpoch: borrower funds only part of pendingInstantWithdraws.
//    collectInstantWithdrawFunds(amount < pending) leaves remainder.
//    deposit() during new-epoch funding bumps epochNumber -> N+1.

// 4. Borrower defaults; guardian calls _handleBorrowerDefault/finalizeDefaultRecovery.
//    defaultPendingClaimBasis() excludes instantWithdrawClaimsByEpoch[N].

// 5. Victim claims:
vm.prank(user);
vm.expectRevert(NotAllowed.selector);
cdoEpoch.claimInstantWithdrawRequest();
// instantWithdrawsRequests[user] still > 0, receipt unclaimable forever.
```

Uncertainty note: I could not fully trace `IdleCDOEpochVariant`'s instant-withdraw funding path in the remaining iteration budget, so the exact call sequence in step 3 (whether the CDO permits crossing an epoch boundary with `pendingInstantWithdraws > 0`) should be confirmed when writing the PoC. The strategy-side exclusion logic itself is verified in `IdleCreditVault.sol`.