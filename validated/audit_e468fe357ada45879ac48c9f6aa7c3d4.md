Let me examine the strategy contract for the claim/receipt paths, which are the closest analog to "spoofed displayed state vs. actual settlement."### Title
Already-funded instant-withdraw receipts are re-pooled into the default recovery reserve and haircut, diluting funded claimants to benefit active tranche holders - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

`finalizeDefaultRecovery` computes the recovery basis and reserve inconsistently for partially-funded instant withdrawals. `defaultPendingClaimBasis()` adds the *full* current-epoch instant claim basis (`instantWithdrawClaimsByEpoch[epochNumber]`) to `totalBasis`, while `_defaultPrefundedInstantReserve()` adds only the funded portion (`instantBasis - pendingInstant`) to the reserve. Because the prefunded cash earmarked 1:1 for instant receipt holders is pooled into a single reserve shared pro-rata with active LP claims and normal pending receipts, funded instant claimants are haircut to `defaultRecoveryPrice` and the difference accrues to active tranche holders — a "receipt displayed as funded at par but settled under a shared haircut" analog of the CVE-2019-13701 spoofed-authoritative-display bug class.

### Finding Description

The relevant code path:

- `startEpoch` sends `min(pendingInstant, totUnderlyings)` to the strategy via `collectInstantWithdrawFunds`, reducing `pendingInstantWithdraws` but leaving `instantWithdrawsRequests`/`instantWithdrawClaimsByEpoch` intact (`contracts/IdleCDOEpochVariant.sol:279-289`, `contracts/strategies/idle/IdleCreditVault.sol:398-403`).
- If borrower funding later fails (e.g., the `sendFundsToBorrower`/`getFundsFromBorrower` try/catch path), `_handleBorrowerDefault` sets `defaulted = true` (`contracts/IdleCDOEpochVariant.sol:295-303`, `577-599`).
- At `finalizeDefault` → `finalizeDefaultRecovery` (`contracts/strategies/idle/IdleCreditVault.sol:644-723`):
  - `defaultPendingClaimBasis` returns `pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` — the **full** instant claim amount, funded + unfunded.
  - `_defaultPrefundedInstantReserve` returns `instantBasis - pendingInstantWithdraws` — only the **funded** remainder.
  - `recoveryPrice = reserveAmount / totalBasis`, and `_claimDefaultedInstantWithdrawRequest` pays `claimBasis * recoveryPrice / RECOVERY_FULL` (lines 842-856).

The funded portion of an instant receipt was already moved into the strategy and is claimable at par pre-default via `claimInstantWithdrawRequest` (lines 387-392). At finalization it is instead credited to the shared reserve and paid back at `recoveryPrice < 1`. The "lost" difference is absorbed by `totalBasis`, which includes active LP strategy-token claims — i.e., active tranche holders (which can include an attacker holding KYC'd AA/BB tranche tokens) receive an inflated recovery price funded by instant claimants' already-segregated cash.

### Impact Explanation

Direct redistribution: cash that was already collected 1:1 for instant withdrawers is partially redirected to active LP claims. With e.g. 100k USDC of instant receipts funded at par into the strategy, 900k unfunded remainder, active basis 1M and no external recovery, `recoveryPrice ≈ 100k/(1M + pending)` — instant claimants recover a fraction of the 100k that was already theirs in full; the rest raises the recovery multiplier for active tranche holders. Attacker profit is bounded by their share of active basis, but the theft from funded instant claimants is quantifiable and grows with the prefunded amount.

### Likelihood Explanation

Requires a borrower default occurring in an epoch where instant withdrawals were only partially funded at `startEpoch` (`pendingInstant > totUnderlyings`) or where `sendFundsToBorrower` reverted after collection. Honest privileged actions (manager `startEpoch`, owner `finalizeDefault`) sequence the state; the attacker is an ordinary tranche holder who passively receives an inflated recovery share — no privileged role needed. Likelihood is limited by the need for a default coinciding with a partially-funded instant queue, but the accounting error is deterministic once reached.

### Recommendation

Keep funded instant receipts out of the recovery pool: either pay `instantBasis - pendingInstantWithdraws` to those claimants at par from their dedicated collected funds before pooling the remainder, or exclude the funded portion from `defaultPendingClaimBasis` and reserve it in a separate bucket paid 1:1 in `claimInstantWithdrawRequest`. Alternatively, track funded vs. unfunded instant basis per epoch so `defaultPendingClaimBasis` only counts the unfunded remainder.

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
// assumes test setup from test/foundry/IdleCreditVault.t.sol

contract FundedInstantReceiptDilution is Test {
    // Contracts: IdleCDOEpochVariant cdoEpoch, IdleCreditVault strategy,
    // AAtranche/BBtranche, borrower, owner/manager, defaultUnderlying

    function testPrefundedInstantReceiptsDiluted() public {
        // --- Setup (honest flows) ---
        // 1. Attacker (KYC'd lender) deposits 1_000_000e6 into AA.
        // 2. Epoch starts; borrower APR drops at next stopEpoch so
        //    requestWithdraw takes the instant path for the victim.
        // 3. Victim requests instant withdraw of 200_000e6.
        // 4. Manager calls startEpoch for the next epoch with the CDO
        //    holding only 100_000e6 of raw underlyings, so
        //    collectInstantWithdrawFunds moves 100k to the strategy and
        //    pendingInstantWithdraws = 100k remains.
        // 5. Borrower funding fails (getInstantWithdrawFunds or stopEpoch
        //    pull reverts) -> _handleBorrowerDefault, defaulted = true.
        // 6. Owner calls finalizeDefault(recoveredAmount, source).

        // --- Assertions ---
        // instantBasis      = 200_000e6
        // prefundedReserve  = 200k - 100k(pendingInstant) = 100k
        // totalBasis        = activeBasis + pendingWithdraws + 200k
        // recoveryPrice     = reserve / totalBasis  < 1
        //
        // victimClaim = 200k * recoveryPrice / 1e18
        // assertLt(victimClaim, 100_000e6);   // funded part was 100k at par
        //
        // Attacker's AA recovery share is inflated because the victim's
        // 100k prefunded cash was pooled into defaultRecoveryReserve and
        // shared across totalBasis instead of paid to the victim at par.
        // assertGt(attackerRecovery, attackerBasis * naivePrice / 1e18);
    }
}
```

The concrete numbers depend on the deployment setup in `test/foundry/IdleCreditVault.t.sol`; the invariant to assert is `victimClaim < min(instantBasis, fundedPortion)` — i.e., the strategy's own collected cash should pay instant claimants at par before any pooling.

Caveat: I verified the basis/reserve asymmetry in `defaultPendingClaimBasis` (`contracts/strategies/idle/IdleCreditVault.sol:644-649`), `_defaultPrefundedInstantReserve` (`contracts/strategies/idle/IdleCreditVault.sol:716-723`), and `finalizeDefaultRecovery` (`contracts/strategies/idle/IdleCreditVault.sol:685-709`), but could not run a fork test to confirm end-to-end claim amounts; the PoC skeleton assumes the standard test harness. If `claimInstantWithdrawRequest`'s funded-path ordering already drains the prefunded portion before finalization for these users, the impact may be reduced — that ordering (claim via `_claimDefaultedInstantWithdrawRequest` first, then `instantWithdrawsRequests` remainder at par) should be double-checked in a live run.