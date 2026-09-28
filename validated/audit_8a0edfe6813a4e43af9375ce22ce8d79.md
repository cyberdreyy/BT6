### Title
Instant-withdraw receipts outside `defaultRecoveryEpoch` bypass the recovery haircut and can drain funded-claim balance / permanently freeze - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The Gerbv bug is an out-of-bounds write driven by an attacker-controlled index (the drill T-code tool number) that lands writes/reads outside the intended slot. The closest credit-vault analog is epoch-indexed receipt accounting in `IdleCreditVault`: instant-withdraw claims are stored per request epoch (`instantWithdrawsRequestsByEpoch[user][epoch]`) but default and loss handling only ever processes the *current/default* epoch index. Receipts indexed under any other epoch silently escape the recovery haircut yet remain payable — or permanently unpayable — through the aggregate `instantWithdrawsRequests` counter.

### Finding Description
`requestInstantWithdraw` records receipts under the *current* `epochNumber` (`instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount`, line 371). `finalizeDefaultRecovery` computes the recovery basis via `defaultPendingClaimBasis`, which only adds `instantWithdrawClaimsByEpoch[epochNumber]` — i.e., only the *default* epoch's instant claims (lines 646-648). After finalization, `_claimDefaultedInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (line 844).

Two breaks follow:

1. `requestInstantWithdraw` has **no** `defaultRecoveryFinalized` handling (unlike `requestWithdraw`, lines 247-257). A post-default instant request is indexed under `epochNumber` (which is `> defaultRecoveryEpoch` once a new epoch starts, or equal while `defaultInstantWithdrawsFinalized` is already `true`). On claim, `_claimDefaultedInstantWithdrawRequest` returns 0 for that epoch, then `claimInstantWithdrawRequest` burns the user's entire `instantWithdrawsRequests` receipt and calls `_transferFundedClaim`, which reverts whenever `balance - defaultRecoveryReserve < amount` (line 904). Since all strategy-held underlying post-finalization is the isolated reserve, the user's strategy tokens were already burned at request time but the claim reverts forever — permanent loss of principal.

2. For pre-default instant receipts still pending from an epoch *earlier* than the default epoch (possible whenever `collectInstantWithdrawFunds` underfunds the queue across a `startEpoch`), those claims are excluded from `defaultPendingClaimBasis`, so they never receive the `defaultRecoveryPrice` haircut. They remain in `instantWithdrawsRequests[_user]` and are paid *at par* from `balance - reserve`, which is the pool backing funded (`collectWithdrawFunds` / loss-adjusted) withdraw claims. An attacker holding stale instant receipts drains underlying that was only funded at `lossRecoveryPrice < 1`, stealing from loss-adjusted withdraw claimants; if the balance isn't there, every funded claim behind them reverts (insolvency/freeze).

Broken invariant: every outstanding receipt must be either haircutted into the recovery reserve or fully funded — receipts indexed under a non-default epoch are neither, yet remain burnable/payable. No guard (`_transferFundedClaim` reserve check, `_claimDefaultedInstantWithdrawRequest` epoch filter, `defaultRecoveryFinalized` flag) reconciles the off-epoch index.

### Impact Explanation
Direct theft and/or permanent freezing. Stale-epoch instant receipts pay out 1:1 against funds provisioned at `lossRecoveryPrice`/`defaultRecoveryPrice < 1`, so each unit claimed steals `(1 - price)` from legitimate withdraw claimants; equivalently, post-default instant requests burn the user's strategy-token principal while their claim reverts indefinitely. Loss is bounded by the size of off-epoch instant receipts, which an attacker controls by simply calling `requestInstantWithdraw` through the CDO in an epoch that will not be the default epoch, or after finalization.

### Likelihood Explanation
Requires only an unprivileged tranche holder / KYC'd user able to route instant withdrawals through `IdleCDOEpochVariant`. Trigger conditions: an instant request pending in epoch N that stays unfunded while the borrower defaults in epoch N+k (recovery accounting then ignores it), or any `requestInstantWithdraw` accepted after `defaultRecoveryFinalized`. The attacker does not control privileged calls; honest manager sequencing (partial `collectInstantWithdrawFunds`, then default) is sufficient.

### Recommendation
- In `requestInstantWithdraw`, either revert when `defaultRecoveryFinalized` or route post-default instant receipts through the same haircut/`postDefaultRequests` mechanism as `requestWithdraw`.
- In `defaultPendingClaimBasis` and `_claimDefaultedInstantWithdrawRequest`, account for *all* epochs with outstanding instant claims (iterate or track an aggregate unfunded-instant basis), not only `instantWithdrawClaimsByEpoch[epochNumber]`/`defaultRecoveryEpoch`.
- Add a regression test: instant request in epoch N, default finalized in epoch N+1; assert the receipt is haircutted, not paid at par, and that post-default instant requests cannot strand principal.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";

// Scenario: off-epoch instant receipt escapes default recovery haircut.
contract OffEpochInstantClaimPoC is Test {
  // Deploy IdleCDOEpochVariant + IdleCreditVault per existing foundry setup
  // (see test/foundry/IdleCDOEpochQueue.t.sol harness).

  function test_staleEpochInstantReceiptStealsFundedClaims() external {
    // 1. Epoch N running: attacker requests instant withdraw of X underlyings.
    //    instantWithdrawsRequestsByEpoch[attacker][N] = X; pendingInstantWithdraws = X.
    // 2. Honest manager calls startEpoch; CDO underfunds instant queue
    //    (collectInstantWithdrawFunds < X) so pendingInstantWithdraws stays > 0.
    //    Attacker does not claim; epoch advances to N+1.
    // 3. Borrower defaults in epoch N+1; owner finalizes via
    //    IdleCDOEpochVariant.finalizeDefault -> strategy.finalizeDefaultRecovery.
    //    defaultPendingClaimBasis() = pendingWithdraws + instantWithdrawClaimsByEpoch[N+1]
    //    -> attacker's epoch-N claim of X is NOT in the basis, so it is not haircutted
    //    and consumes none of defaultRecoveryReserve.
    // 4. Meanwhile normal withdraw receipts funded at lossRecoveryPrice < 1 sit
    //    in the strategy balance above the reserve.
    // 5. Attacker calls CDO.claimInstantWithdrawRequest():
    //    _claimDefaultedInstantWithdrawRequest -> instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch] == 0 -> no-op
    //    _burn(attacker, X); _transferFundedClaim(attacker, X)
    //    pays X AT PAR from balance - reserve.
    assertEq(underlying.balanceOf(attacker) - balPre, X);       // paid at par
    // Funded loss-adjusted claimants now revert or are short by X * (1 - lossRecoveryPrice).
    vm.expectRevert(NotAllowed.selector);
    cdo.claimWithdrawRequest(victim);                           // balance - reserve < claim
  }

  function test_postDefaultInstantRequestStrandsPrincipal() external {
    // 1. Finalize default with defaultRecoveryFinalized = true.
    // 2. Epoch N+2 running (CDO allows instant withdraw path):
    //    attacker requestInstantWithdraw(Y) -> burns CDO strategy tokens,
    //    mints Y receipt, instantWithdrawsRequestsByEpoch[attacker][N+2] = Y.
    // 3. claimInstantWithdrawRequest: defaulted-epoch index is N+1 -> clears nothing;
    //    _burn(attacker, Y) then _transferFundedClaim reverts:
    //    balance == defaultRecoveryReserve  =>  balance - reserve (0) < Y.
    vm.expectRevert(NotAllowed.selector);
    cdo.claimInstantWithdrawRequest();
    // Attacker's Y underlyings are permanently burned/unclaimable.
  }
}
```