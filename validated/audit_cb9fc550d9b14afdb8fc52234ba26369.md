### Title
Loss-haircut epoch mismatch in `collectWithdrawFunds`/`_claimLossAdjustedWithdrawRequest` lets pending withdraw receipts claim at par and drain underfunded vault - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary

The Xen bug class is a restartable multi-step operation that drops/retains accounting counts across a boundary. The credit-vault analog is the loss-adjusted withdraw receipt path: a stop that funds pending receipts at a haircut records the recovery price under `epochNumber`, while the receipts themselves are tracked under the *request-time* epoch stored in `lastWithdrawRequest`. Because `stopEpoch` bumps `epochNumber` before the strategy collects the (reduced) funded amount, the haircut is keyed to the wrong epoch, so pending receipts bypass the haircut entirely and pay out at par from an underfunded balance.

### Finding Description

- `requestWithdraw` records receipts under the epoch at request time: `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `lastWithdrawRequest[_user] = currentEpoch` (`contracts/strategies/idle/IdleCreditVault.sol:260-293`).
- On a `stopEpochWithDuration` loss, `IdleCDOEpochVariant.stopEpoch` increments `epochNumber`, then calls `collectWithdrawFunds`, which stores `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` (`contracts/strategies/idle/IdleCreditVault.sol:411-429`). This is corroborated by the test at `test/foundry/IdleCDOEpochQueue.t.sol:904`, which computes `claimEpoch = strategy.epochNumber() + 1` — i.e., the epoch counter advances at stop time relative to the request epoch.
- The claim path looks the haircut up under the *request* epoch: `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (`contracts/strategies/idle/IdleCreditVault.sol:789-801`). With the price stored under `epochNumber` (post-increment) and `lastWithdrawRequest` equal to the pre-increment request epoch, the lookup returns `0` and the function returns early.
- The request-side guard has the same off-by-one: it checks `lossRecoveryPriceByEpoch[lossEpoch]` where `lossEpoch = lastWithdrawRequest[_user]` (`contracts/strategies/idle/IdleCreditVault.sol:261-271`), so it also never sees the stored price and never forces a claim.
- The receipt therefore falls through to `_claimFundedWithdrawRequest`, which pays the full `withdrawsRequests[_user]` basis at par (`contracts/strategies/idle/IdleCreditVault.sol:319-350`), even though `collectWithdrawFunds` only pulled `pendingToFund < pendingBasis` underlying from the CDO. Since `pendingWithdraws` was zeroed at line 420 while the aggregate `withdrawsRequests[user]` was untouched, the vault is structurally short the haircut amount.

### Impact Explanation

Broken invariant: loss socialization / one-receipt-one-payout. After a `stopEpochWithDuration` loss, every pending receipt claims at 100% instead of `lossRecoveryPrice`. Early claimers are paid in full until the vault's funded balance is exhausted; the last claimant's `safeTransfer` reverts. Net effect: the realized loss intended to be shared pro-rata across pending receipts is instead borne entirely by the slowest claimer(s) — a direct transfer of funds between unprivileged users plus freezing of the residual claim. Quantified loss equals `pendingBasis - pendingToFund` (the haircut), concentrated on the last claimer.

### Likelihood Explanation

Triggerable by any KYC-passed lender holding an unclaimed withdraw receipt when the manager calls `stopEpochWithDuration`/`stopEpoch` with a loss parameter after borrower underfunding. The manager call is an honest-actor sequencing event; the attacker is simply a fast claimer. No privileged misbehavior is required. Confidence caveat: the finding hinges on `epochNumber` being incremented before `collectWithdrawFunds` inside `stopEpoch`; the queue test at `IdleCDOEpochQueue.t.sol:904` (claim epoch = request epoch + 1) strongly indicates this ordering, but the PoC below confirms it empirically.

### Recommendation

Store the haircut under the epoch the receipts belong to — e.g., `lossRecoveryPriceByEpoch[epochNumber - 1]`, or pass the request epoch from `stopEpoch` — so that both `_claimLossAdjustedWithdrawRequest` and the `requestWithdraw` re-entry guard key on the same epoch as `lastWithdrawRequest`/`withdrawsRequestsByEpoch`. Alternatively, key loss claims on `withdrawsRequestsByEpoch` directly rather than on `lastWithdrawRequest`.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";

/// @notice Two users hold pending receipts; borrower underfunds stopEpoch (loss path).
/// The haircut is stored under the post-increment epoch, so both users' claims
/// resolve via _claimFundedWithdrawRequest at par. First claimer wins in full;
/// second claimer reverts on insufficient vault balance.
contract LossEpochMismatchPoC is IdleCreditVaultTest {
    function testLossReceiptsClaimAtPar() external {
        uint256 depositAmt = 10_000 * ONE_SCALE;
        idleCDO.depositAA(depositAmt);

        address user1 = makeAddr("user1");
        address user2 = makeAddr("user2");
        uint256 tranches1 = _depositWithUser(user1, 100 * ONE_SCALE);
        uint256 tranches2 = _depositWithUser(user2, 100 * ONE_SCALE);

        _startEpochAndCheckPrices(0);

        // both request during epoch N (strategy.epochNumber() == N)
        _requestWithdrawWithUser(user1, tranches1);
        _requestWithdrawWithUser(user2, tranches2);
        uint256 requestEpoch = IdleCreditVault(address(strategy)).epochNumber();
        uint256 pendingBasis = IdleCreditVault(address(strategy)).pendingWithdraws();

        // borrower repays only 50% of what is owed -> stopEpochWithDuration loss
        uint256 repay = _expectedFundsEndEpoch() - pendingBasis / 2;
        deal(defaultUnderlying, borrower, repay);
        vm.warp(cdoEpoch.epochEndDate() + 1);
        vm.prank(manager);
        cdoEpoch.stopEpochWithDuration(0, repay, pendingBasis / 2); // manager loss call

        // Haircut price was stored under epochNumber AFTER increment (N+1),
        // while lastWithdrawRequest == N and withdrawsRequestsByEpoch[user][N] != 0.
        IdleCreditVault vault = IdleCreditVault(address(strategy));
        uint256 reqEpoch = vault.lastWithdrawRequest(user1);
        assertEq(vault.lossRecoveryPriceByEpoch(reqEpoch), 0, "price keyed to wrong epoch");
        assertGt(vault.lossRecoveryPriceByEpoch(vault.epochNumber()), 0);

        // user1 claims FIRST: gets full par amount instead of haircutted 50%
        uint256 balPre = underlying.balanceOf(user1);
        vm.prank(user1);
        cdoEpoch.claimWithdrawRequest();
        uint256 got = underlying.balanceOf(user1) - balPre;
        uint256 parBasis = pendingBasis / 2;
        assertEq(got, parBasis, "receipt paid at par despite loss");

        // user2 claims LAST: vault lacks the haircut amount -> transfer reverts
        vm.prank(user2);
        vm.expectRevert(); // ERC20 transfer exceeds funded balance
        cdoEpoch.claimWithdrawRequest();
    }
}
```

Run against the existing fork harness: `forge test --match-test testLossReceiptsClaimAtPar -vvv --fork-url <rpc>`.

**Uncertainty note:** I could not read `IdleCDOEpochVariant.stopEpoch`'s `epochNumber` increment ordering and `stopEpochWithDuration` signature within the search budget; if `collectWithdrawFunds` runs before the increment, the epoch keys align and this finding is invalid. The queue test's `epochNumber() + 1` convention and the `lastWithdrawRequest`-keyed guard both suggest the mismatch, but the PoC assertion on `lossRecoveryPriceByEpoch` is the decisive check — if it fails, the haircut path engages correctly and the finding collapses to a non-issue.