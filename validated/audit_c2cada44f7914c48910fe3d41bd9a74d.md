### Title
Dust-funded pending-withdraw collection reverts `stopEpoch` loss settlement, freezing pool funds - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
CVE-2015-7852 crashes `ntpq` via a crafted mode-6 response — an externally supplied value that drives a response handler into a fatal path. The credit-vault analog lives in `IdleCreditVault.collectWithdrawFunds`: when the borrower-funded `_amount` for pending withdraw receipts is smaller than `pendingBasis`, the strategy computes `lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis` and **reverts if the truncated price is 0**. Any funded amount `< pendingBasis / 1e18` therefore makes the loss-adjusted funding path permanently revert, reverting the whole epoch stop and freezing every LP's funds.

### Finding Description
During `stopEpochWithDuration`-style settlement, the CDO calls `collectWithdrawFunds(_amount)` with the tokens the borrower actually returned for pending receipts. The partial-funding branch (lines 414-421) stores a per-epoch haircut price:

```solidity
uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
if (lossRecoveryPrice == 0) revert NotAllowed();
pendingWithdraws = 0;
lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
```

Because `RECOVERY_FULL = 1e18`, whenever `_amount < pendingBasis / 1e18` (i.e. less than ~1 wei of funding per 1e18 of pending basis — always true once `pendingBasis > ~1e18 * _amount`), `lossRecoveryPrice` truncates to 0 and the call reverts. The same revert propagates from `previewLossAdjustedWithdrawFunds` only in the sense that the CDO must obtain a nonzero `pendingToFund`; the hard revert is in `collectWithdrawFunds` itself. Since `lossRecoveryPrice == 0` is also the sentinel for "no loss-adjusted epoch" (checked in `_claimLossAdjustedWithdrawRequest`, line 792), the code conflates "unfunded" with "revert", making dust-funded settlement impossible.

The attacker does not need privileged roles: any lender can deposit and `requestWithdraw` to grow `pendingWithdraws`. With `pendingBasis` large enough (a large depositor, or aggregate pending receipts), a near-total-loss scenario — exactly the scenario where `lossRecoveryPrice` would matter most — produces a `pendingToFund` below the truncation threshold, so the honest manager/borrower cannot settle the epoch with a loss at all. The only remaining paths are (a) borrower funds the full `pendingBasis` at par, which honest-but-insolvent borrower cannot do, or (b) default handling, which forces all LPs into the heavier default/finalize flow even when a partial recovery was available.

### Impact Explanation
Permanent or long-lasting freezing of LP funds: `stopEpoch` with a realized loss cannot complete while `pendingBasis > _amount * 1e18`, so `epochEndDate` never resets, `pendingWithdraws` is never cleared, and no LP — active or pending — can withdraw or claim. This is not a mere DoS without fund impact: the vault's entire NAV is locked because the epoch state machine cannot advance past a revert that an unprivileged depositor's request size controls. Broken invariant: liveness of the epoch state machine / guaranteed settlement of pending receipts.

### Likelihood Explanation
Medium-low to medium. It requires `pendingWithdraws` to exceed the funded amount by a factor >1e18, which happens only in near-total-loss events with a large pending-receipt bucket (a whale withdraw request, or many aggregated requests). An attacker cannot create basis without depositing real capital, but aggregating pending receipts across users means a single epoch with heavy exits plus a severe borrower shortfall triggers it organically. Severity aligns with the source CVE's Medium rating: conditional, but fatal to the settlement path when hit.

### Recommendation
Replace the revert with graceful handling: if the truncated `lossRecoveryPrice` is 0, either (a) treat receipts as fully impaired and store a minimum nonzero sentinel (e.g. 1) while zeroing `pendingWithdraws`, letting claims pay ~0 from a dedicated dust bucket; or (b) store the raw funded `_amount` and `pendingBasis` and compute the payout at claim time as `claimBasis * funded / pendingBasis`, avoiding the 1e18-precision sentinel entirely. Decouple "no loss epoch recorded" from `lossRecoveryPrice == 0` by adding a per-epoch `bool`/bitmap flag, so a legitimately tiny recovery price does not brick `stopEpoch`.

### Proof of Concept
```solidity
// Foundry fork PoC sketch
function testDustFundedLossRevertsAndFreezesEpoch() external {
    // 1. Attacker deposits a large amount and requests full withdraw,
    //    inflating strategy.pendingWithdraws() to P (e.g. P = 1e24).
    uint256 minted = idleCDO.depositAA(1e24);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // 2. Borrower returns almost nothing: funded amount < P / 1e18.
    uint256 dust = IdleCreditVault(address(strategy)).pendingWithdraws() / 1e18; // == 0 when P<1e18; pick P=2e18 -> dust=1
    deal(defaultUnderlying, borrower, dust);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), dust);

    // 3. Manager tries to settle the loss; collectWithdrawFunds computes
    //    lossRecoveryPrice = dust * 1e18 / P == 0 -> revert NotAllowed().
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpoch(0, lossAmount);

    // 4. Epoch stays running forever unless borrower fully funds P at par:
    //    all LP funds (active + pending) are frozen; no claims possible.
}
```

Uncertainty note: I could not fully trace `IdleCDOEpochVariant.stopEpoch`'s call graph this session, but `collectWithdrawFunds` is the CDO's only channel for funding pending receipts, so a revert there necessarily blocks loss-adjusted epoch settlement.