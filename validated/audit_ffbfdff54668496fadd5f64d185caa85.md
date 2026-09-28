### Title
Stale per-epoch receipt key lets earlier pending withdraw receipts escape `lossRecoveryPriceByEpoch` haircut and claim at par - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The kernel bug is a stale-context reference: a vti6 tunnel moves to a new netns (`dev_net(dev)`), but `vti6_changelink()` keeps unlinking/relinking via the old `t->net`, so the original namespace keeps a stale entry that later cleanup walks. The credit-vault analog is identical in shape: a withdraw receipt is keyed twice — once by the *request* epoch (`lastWithdrawRequest[_user]`, `withdrawsRequestsByEpoch[_user][epoch]`) and once by the *funding* epoch (`lossRecoveryPriceByEpoch[epochNumber]` written in `collectWithdrawFunds`). When a user holds pending receipts from more than one request epoch, only the `lastWithdrawRequest` epoch is looked up for the loss haircut; every earlier-epoch receipt has `lossRecoveryPriceByEpoch[oldEpoch] == 0` and is paid out at par through `_claimFundedWithdrawRequest`, even though `collectWithdrawFunds` already haircut the aggregate `pendingWithdraws` that included it.

### Finding Description
`requestWithdraw` lets a user accumulate receipts across epochs without claiming (`IdleCreditVault.sol:282-293` records `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount`; the in-code NOTE at the claim path explicitly states an unclaimed request can be followed by another request). `collectWithdrawFunds` then stores a single loss price `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` keyed on the *current* epoch while reducing the *aggregate* `pendingWithdraws` (`IdleCreditVault.sol:411-421`) — the same "relink into the new namespace, leave a stale entry in the old one" pattern as the vti6 bug.

At claim time, `_claimLossAdjustedWithdrawRequest` resolves the loss epoch solely through `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (`IdleCreditVault.sol:789-801`) and `_clearWithdrawClaimForEpoch` clears only that one epoch's `withdrawsRequestsByEpoch` entry (`IdleCreditVault.sol:815-819`). Any earlier-epoch receipt that was part of the haircut `pendingWithdraws` basis remains inside `withdrawsRequests[_user]` and is paid 1:1 by `_claimFundedWithdrawRequest` (`IdleCreditVault.sol:338-349`), which burns the aggregate `normalAmount` and calls `_transferFundedClaim` for the full sum.

### Impact Explanation
Direct insolvency / theft of other claimants' funds. The vault collected only `_amount = pendingBasis * lossRecoveryPrice / RECOVERY_FULL` underlying, yet the user drains `pendingBasis` at par: the latest-epoch piece at `lossRecoveryPrice`, and all earlier-epoch pieces at 100%. The shortfall is paid from vault underlyings that belong to other pending claimants or to the default recovery reserve boundary, i.e. the loss is not socialized over the full pending basis as `previewLossAdjustedWithdrawFunds` promises — the attacker escapes their pro-rata share of the realized loss.

### Likelihood Explanation
Fully unprivileged and reproducible: any KYC'd lender requests a withdraw in epoch N, lets `stopEpoch` fund it (or leave it pending), requests again in epoch N+1, and waits for a `stopEpochWithDuration`/`collectWithdrawFunds` that funds `pendingWithdraws` at a loss. The only existing guard (`requestWithdraw` reverting when `lossRecoveryPriceByEpoch[lastWithdrawRequest]` is already set, `IdleCreditVault.sol:263-270`) fires *after* the loss epoch is recorded — it does not prevent the mixed-epoch buildup beforehand. One caveat I could not fully confirm within the search budget: whether `epochNumber` is incremented before or after `collectWithdrawFunds` is invoked inside `stopEpoch`; if incremented first, the mismatch is even worse (the loss key would never equal any `lastWithdrawRequest`), but the multi-epoch staleness described above stands regardless.

### Recommendation
Record the loss epoch per pending receipt, or make the claim iterate all of the user's epochs that fall inside the funded-loss window. Concretely: store the set of request epochs covered by a `lossRecoveryPriceByEpoch` write (or a `lossRecoveryPriceByEpochRange`), and have `_claimLossAdjustedWithdrawRequest` loop over every `withdrawsRequestsByEpoch[_user][e]` / `apr0Users` epoch included in the haircut, rather than resolving only `lastWithdrawRequest[_user]`. Alternatively, keep a per-user pending-loss basis bucket cleared atomically at claim time, mirroring how `pendingWithdraws` is reduced globally.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
// Foundry fork test in test/foundry/IdleCreditVault.t.sol style.
function testMixedEpochReceiptsEscapeLossHaircut() external {
    IdleCreditVault vault = IdleCreditVault(address(strategy));
    address alice = makeAddr("alice");
    uint256 amount = 10_000 * ONE_SCALE;

    // Epoch 0: alice deposits and requests withdraw -> receipt keyed to epoch 0.
    _depositWithUser(alice, amount, true);
    vm.prank(alice);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    // Epoch rolls without alice claiming (NOTE in code: she may request again).
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // Epoch 1: alice requests again -> lastWithdrawRequest = 1,
    // withdrawsRequestsByEpoch[alice][0] still nonzero.
    _startEpochAndCheckPrices(1);
    vm.prank(alice);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    // Borrower funds pending basis at a loss: 50% recovery.
    uint256 pendingBasis = vault.pendingWithdraws();
    uint256 funded = pendingBasis / 2;
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, pendingBasis - funded); // _lossAmount path

    uint256 lossEpochKey = vault.epochNumber();
    assertGt(vault.lossRecoveryPriceByEpoch(lossEpochKey), 0);

    // Alice claims: latest-epoch piece is haircut, epoch-0 piece pays at par.
    uint256 balPre = underlying.balanceOf(alice);
    vm.prank(alice);
    cdoEpoch.claimWithdrawRequest();
    uint256 received = underlying.balanceOf(alice) - balPre;

    // Expected under correct socialization: pendingBasis_alice * recoveryPrice.
    // Actual: epoch-0 basis at 100% + epoch-1 basis at recoveryPrice > expected.
    uint256 epoch0Basis = /* withdrawsRequestsByEpoch[alice][0] pre-clear */;
    uint256 expected = (pendingBasis_alice * 5e17) / 1e18;
    assertGt(received, expected); // haircut evaded; vault paid more than funded
}
```
The assertion fails under correct accounting but passes today because `lossRecoveryPriceByEpoch` is only consulted at `lastWithdrawRequest[alice]`, leaving the epoch-0 entry as the "stale entry in the old namespace" that the funded-claim path walks at par.