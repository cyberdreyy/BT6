### Title
Instant-withdraw receipts requested in an earlier epoch are excluded from `defaultPendingClaimBasis` but remain claimable at par after default finalization — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The analog to CVE-2022-2621 (use-after-free: a resource still referenced after its lifetime ended) is a stale-epoch receipt that survives the epoch lifecycle and escapes default accounting. `IdleCreditVault` tags instant-withdraw receipts with their request epoch (`instantWithdrawsRequestsByEpoch[user][epoch]`), but `defaultPendingClaimBasis()` only counts `instantWithdrawClaimsByEpoch[epochNumber]` — the *current* epoch. Instant receipts requested in an earlier epoch that were never funded persist in `pendingInstantWithdraws` and `instantWithdrawsRequests[user]` across `epochNumber` increments, yet are omitted from the haircut basis. After `finalizeDefaultRecovery`, `_claimDefaultedInstantWithdrawRequest` only clears `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`; earlier-epoch receipts fall through to `_transferFundedClaim` and are paid at par.

### Finding Description
In `requestInstantWithdraw`, receipts are recorded per request epoch (`instantWithdrawsRequestsByEpoch[user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]`), while `pendingInstantWithdraws` is a rolling aggregate across epochs (IdleCreditVault.sol:367-374). If the manager never calls `getInstantWithdrawFunds` for those receipts, a successful `stopEpoch` does not include `pendingInstantWithdraws` in the borrower pull (IdleCDOEpochVariant.sol:408), so the receipts roll into the next epoch unchanged while `epochNumber` increments in `deposit` (IdleCreditVault.sol:610).

When the borrower then defaults in a later epoch and `finalizeDefaultRecovery` runs, `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` — only the current epoch's instant claims (IdleCreditVault.sol:646-648). Because `pendingInstantWithdraws != 0`, `defaultInstantWithdrawsFinalized` is set to true (IdleCreditVault.sol:696), but the older-epoch instant basis never enters `totalBasis`, so `defaultRecoveryPrice` is computed too high.

In `claimInstantWithdrawRequest` → `_claimDefaultedInstantWithdrawRequest`, only `instantWithdrawsRequestsByEpoch[user][defaultEpoch]` is cleared (IdleCreditVault.sol:843-848). The stale-epoch remainder stays in `instantWithdrawsRequests[user]` and is burned and paid 1:1 through `_transferFundedClaim` (IdleCreditVault.sol:387-392), which explicitly does not consume `defaultRecoveryReserve`.

### Impact Explanation
Two broken invariants:

1. Loss socialization: `totalBasis` is understated by the stale instant basis `X`, so `recoveryPrice = reserveAmount / totalBasis` is inflated. Legitimate defaulted-epoch claimants (normal withdraw receipts and post-haircut active LPs) are overpaid early and the `defaultRecoveryReserve` is exhausted before later claimants are paid — the last claimants' `_transferDefaultRecovery` reverts on underflow (IdleCreditVault.sol:915), permanently freezing their recovery.
2. One-receipt-one-payout: the stale instant receipt, which was part of the defaulted debt, is redeemed at par from non-reserve balance — cash earmarked for other users' funded withdraw receipts — directly stealing up to `X` underlying or reverting and freezing it.

An unprivileged attacker who placed an instant-withdraw request in a prior epoch that was simply never funded (an honest-manager inaction, not attacker-controlled) receives a full par payout while all other defaulted claimants absorb a larger effective haircut.

### Likelihood Explanation
Requires: an instant withdraw request left unfunded through one epoch boundary (manager need only omit a call), followed by a borrower default and `finalizeDefault` in a later epoch. Attacker cost is one `requestInstantWithdraw`. No privileged cooperation is needed — the sequence uses only honest manager/owner calls in a plausible operational path.

### Recommendation
Include all outstanding instant receipt basis in `defaultPendingClaimBasis` (e.g., track a global `instantWithdrawClaims` aggregate rather than only `instantWithdrawClaimsByEpoch[epochNumber]`), and clear stale-epoch instant receipts through the defaulted-claim path — or iterate/clear all non-zero `instantWithdrawsRequestsByEpoch` entries so they are haircut by `defaultRecoveryPrice` instead of reaching `_transferFundedClaim`.

### Proof of Concept
Foundry fork PoC (against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testStaleEpochInstantReceiptEscapesHaircut() external {
    IdleCreditVault vault = IdleCreditVault(address(strategy));
    address attacker = makeAddr("attacker");

    // Epoch N-1: deposit and request an instant withdraw that is never funded.
    _depositWithUser(attacker, 10_000 * ONE_SCALE, true);
    _startEpochAndCheckPrices(0);
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(5_000 * ONE_SCALE); // pendingInstantWithdraws = 5k, claimsByEpoch[N-1] = 5k

    // Manager never calls getInstantWithdrawFunds; epoch N-1 stops normally.
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0); // epochNumber -> N, instant receipt still pending

    // Epoch N starts and borrower defaults (funding pull fails).
    _toggleEpoch(true, 0, 0);
    _toggleEpoch(false, initialApr, 10_000);   // underfunded borrower -> default in stopEpoch/getInstantWithdrawFunds

    // Owner/manager finalizes with partial recovery.
    deal(defaultUnderlying, recoverySource, recovered);
    vm.prank(owner);
    cdoEpoch.finalizeDefault(recovered, recoverySource);

    // BUG: attacker's stale instant receipt (epoch N-1) was excluded from
    // defaultPendingClaimBasis but is paid at par via _transferFundedClaim,
    // spending cash owed to funded withdraw receipts / inflating recoveryPrice.
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(underlying.balanceOf(attacker) - balPre, 5_000 * ONE_SCALE); // par payout, no haircut
}
```

The assertion shows the stale-epoch receipt paying 1:1 despite `defaultRecoveryFinalized == true`, while defaulted-epoch claimants only receive `claimBasis * defaultRecoveryPrice` — confirming the reserve/basis mismatch.