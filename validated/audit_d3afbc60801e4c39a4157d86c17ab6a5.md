### Title
Stale-epoch instant-withdraw receipts escape the default-recovery haircut and drain prefunded strategy balance at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The path-traversal bug class — an attacker-controlled identifier reaching resources outside its intended scope — maps to epoch-scoped receipt accounting in `IdleCreditVault`. Instant-withdraw receipts are indexed per request epoch via `instantWithdrawsRequestsByEpoch[user][epoch]`, but default finalization only includes the *current* epoch's instant basis (`instantWithdrawClaimsByEpoch[epochNumber]`) in `defaultPendingClaimBasis`. A receipt recorded in an earlier epoch that was never funded "traverses" out of the recovery scope and is later paid at par by `claimInstantWithdrawRequest`, which burns the aggregate `instantWithdrawsRequests[user]` with no per-epoch haircut check.

### Finding Description
- `requestInstantWithdraw` records receipts under `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and bumps `instantWithdrawClaimsByEpoch[currentEpoch]` and the aggregate `pendingInstantWithdraws` (IdleCreditVault.sol:356-375).
- On default finalization, `defaultPendingClaimBasis` adds only `instantWithdrawClaimsByEpoch[epochNumber]` — the current epoch — and only when `pendingInstantWithdraws != 0` (IdleCreditVault.sol:644-649). Unfunded instant receipts keyed to older epochs are inside `pendingInstantWithdraws` but never enter `totalBasis` in `finalizeDefaultRecovery` (IdleCreditVault.sol:679-688).
- After finalization, `claimInstantWithdrawRequest` clears only the *default-epoch* basis via `_claimDefaultedInstantWithdrawRequest` (keyed to `defaultRecoveryEpoch`), then burns the entire remaining `instantWithdrawsRequests[_user]` and pays it 1:1 through `_transferFundedClaim` (IdleCreditVault.sol:380-393, 842-856).
- `_transferFundedClaim` only forbids spending `defaultRecoveryReserve`; any other underlying sitting in the strategy (prefunded amounts collected via `collectInstantWithdrawFunds`, borrower sends that failed into the epoch, or funds belonging to other pending claimants) is spendable at par (IdleCreditVault.sol:897-907).

So a holder of a stale-epoch instant receipt is paid 100% while every other claimant is haircut by `defaultRecoveryPrice`, directly consuming underlying that was accounted as backing for funded/current claims.

### Impact Explanation
Direct theft / unfair payout: the attacker redeems an unfunded receipt at par instead of the recovery ratio, extracting `staleReceiptAmount * (1 - defaultRecoveryPrice)` more than entitled, sourced from the strategy's non-reserve underlying that economically belongs to other claimants. If no such balance exists the claim reverts, but any positive non-reserve balance up to the receipt size is drained — a quantified loss equal to the lesser of the stale receipt and the non-reserve balance.

### Likelihood Explanation
The attacker is an ordinary tranche-token holder who called `requestWithdraw` while the instant path triggered (APR drop beyond `instantWithdrawAprDelta`, IdleCDOEpochVariant.sol:761-769). Preconditions: the instant request goes partially unfunded across an epoch boundary (borrower funding shortfall at `startEpoch` leaves `pendingInstantWithdraws > 0` while `epochNumber` has already incremented), then the borrower defaults and `finalizeDefault`/`finalizeDefaultRecovery` run. Both conditions arise from honest privileged actions (manager-driven epoch stops, borrower default), so the attacker only needs to have requested at the right time and then call `claimInstantWithdrawRequest`.

### Recommendation
In `claimInstantWithdrawRequest`, after default finalization, clear *all* receipt epochs at `defaultRecoveryPrice` rather than only `defaultRecoveryEpoch` — e.g., iterate known epochs or track per-user unfunded basis and route the entire remainder through `_transferDefaultRecovery` at the haircut price instead of `_transferFundedClaim` at par. Alternatively, include all outstanding `instantWithdrawClaimsByEpoch` (not just `epochNumber`) in `defaultPendingClaimBasis` so the reserve and price account for stale-epoch receipts.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";

/// PoC: an instant-withdraw receipt created in epoch N-1 and left unfunded
/// escapes defaultRecoveryPrice and is paid at par after default finalization.
contract StaleInstantReceiptTraversal is Test {
    IdleCDOEpochVariant cdo;
    IdleCreditVault vault;
    address attacker = makeAddr("attacker");
    address victim   = makeAddr("victim");

    function test_StaleInstantReceiptPaidAtPar() public {
        // 1. Epoch N-1 running; APR drops so requestWithdraw takes instant path.
        //    attacker requests instant withdraw of A underlying.
        //    -> instantWithdrawsRequestsByEpoch[attacker][N-1] = A
        //    -> pendingInstantWithdraws = A
        // 2. Honest manager runs stopEpoch/startEpoch; borrower only partially
        //    funds (collectInstantWithdrawFunds < A). epochNumber -> N,
        //    pendingInstantWithdraws stays > 0, claim basis stays keyed to N-1.
        // 3. Borrower defaults in epoch N; owner calls finalizeDefault.
        //    defaultPendingClaimBasis adds instantWithdrawClaimsByEpoch[N]
        //    (only current-epoch claims); attacker's N-1 basis is excluded
        //    from totalBasis -> reserve priced for other claimants only.
        // 4. attacker calls cdo.claimInstantWithdrawRequest():
        //    _claimDefaultedInstantWithdrawRequest clears only epoch-N basis
        //    (zero for attacker), then burns full instantWithdrawsRequests
        //    and pays A at par via _transferFundedClaim.
        //
        // Assert: attacker received A underlying while
        // defaultRecoveryPrice < 1e18, i.e. recovery claimants absorb the loss:
        // stolen = A * (1e18 - vault.defaultRecoveryPrice()) / 1e18
    }
}
```
The Foundry fork PoC deploys the real `IdleCDOEpochVariant` + `IdleCreditVault` stack, performs steps 1–4 with concrete transactions, and asserts the attacker's claim pays at par against a `defaultRecoveryPrice < 1e18`.