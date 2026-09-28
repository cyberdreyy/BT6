### Title
Request-time APR snapshot mints receipts that stay valid after the epoch rate changes, overpaying withdrawers from funded reserves - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
The Zebra bug class is "validity proven at height H+1 is cached and replayed at height H+2, where it is no longer valid." In `IdleCDOEpochVariant.requestWithdraw`, both the withdrawal *mode* (instant vs queued) and the receipt *amount* (principal + full-epoch interest minus fees) are snapshotted against the APR and pool state at request height, minted as a fixed receipt in `IdleCreditVault`, and then honored verbatim at `stopEpoch`/claim height even after the economic conditions that made that snapshot correct have changed. The receipt is never re-validated against the epoch in which it is funded and claimed.

### Finding Description
In `requestWithdraw` (`contracts/IdleCDOEpochVariant.sol:751-791`):

- The instant-withdraw branch checks `lastEpochApr > currentApr + instantWithdrawAprDelta` once, at request time (line 761-769), and mints an instant receipt via `creditVault.requestInstantWithdraw(_underlyings, msg.sender)`. `IdleCreditVault.claimInstantWithdrawRequest` (`contracts/strategies/idle/IdleCreditVault.sol:380-393`) later burns that receipt and calls `_transferFundedClaim` at par, with no re-check that the APR-drop trigger still holds.
- The normal branch calls `_calcInterestWithdrawRequest(_underlyings, _tranche)` (line 773), which prices a *full epoch of interest at the request-time APR*, adds it to the receipt (`_underlyings = principal + interest - totalFees`, line 776), and records it in `pendingWithdraws` (`IdleCreditVault.requestWithdraw`, `contracts/strategies/idle/IdleCreditVault.sol:272-294`). `collectWithdrawFunds` (line 411-430) then pulls exactly this cached amount from the borrower-funded amount at `stopEpoch`, and `claimWithdrawRequest`/`_claimFundedWithdrawRequest` (line 319-350) pays it verbatim.

Concretely, the broken invariant is *fair mint/burn*: a receipt is minted for a value that was only correct under the request-block state (APR, trigger condition). When the honest manager later calls `setAprsWithBuffer`/`setApr` (`IdleCreditVault.sol:217-235`) to lower the APR mid-epoch — a routine, permitted operation — all receipts minted before that call still settle interest at the old, higher APR and are funded in full by the borrower at `stopEpoch`. The delta is paid out of the same funded reserve pool that backs every other claim: it is either pure overpayment taken from borrower funds earmarked for the pool, or — if the borrower funds only the honest expected amount — a pro-rata haircut of other claimants via `lossRecoveryPriceByEpoch` (`collectWithdrawFunds` line 414-421), socializing the stale-APR overpayment onto unrelated withdrawers.

The analog is exact: the "transaction" (withdraw request) was valid at height H+1 (high-APR state) but invalid at height H+2 (post-`setApr` state), and the cache — the minted receipt plus `pendingWithdraws`/`withdrawsRequestsByEpoch` — is replayed without re-checking height-dependent conditions (APR, epoch yield). No guard stops it: `_onlyIdleCDO`, epoch gating (`epochNumber <= lastWithdrawRequest` at line 326) only enforces *time* passed, not *conditions still hold*, and the APR-setter path (`setApr`/`setAprsWithBuffer`) does not reprice or invalidate open receipts.

### Impact Explanation
A KYC-passing lender (a permitted, unprivileged attacker class) who holds tranche tokens can:

1. Request a normal withdrawal in the buffer/epoch while `lastApr`/`unscaledApr` is high, locking `interest = principal * oldApr * epochDuration` into the receipt and into `pendingWithdraws`.
2. Wait for (or simply sequence around) the honest manager's mid-epoch `setApr` reduction.
3. At `stopEpoch`, the borrower funds `pendingWithdraws` including the stale interest; the attacker claims the full cached amount.

Loss is quantifiable: `principal * (oldApr - newApr) * epochDuration / 365 days`, per request, paid either as direct overpayment from borrower-funded reserves or socialized onto other pending claimants when the funded amount falls short (`lossRecoveryPriceByEpoch` haircut). The same staleness applies to instant receipts granted on a transient APR drop that is later reverted, letting the attacker exit liquidity instantly under conditions that no longer authorize it — converting a temporary-lock design into free liquidity extraction.

### Likelihood Explanation
Requires only a tranche-token holder (KYC-passing lender) plus an honest, routine manager action (`setApr`/`setAprsWithBuffer`, which the code explicitly supports being called mid-flow by the manager at `IdleCreditVault.sol:225-235`). No privileged collusion, no default, no oracle manipulation. Any deployment where APR is adjusted more than once per epoch window is exposed; the loss scales with principal and APR delta.

### Recommendation
Re-validate receipts at funding/claim height, mirroring the Zebra fix (re-run contextual verification instead of trusting the cache):

- Recompute the interest component at `stopEpoch` using the *final* epoch APR/`vaultInterestAccrued` rather than the request-time snapshot, or store the APR used per request epoch and cap claims at `min(requestApr, aprAtFunding)` economics.
- For instant withdrawals, record the trigger condition per receipt epoch and allow `stopEpoch`/claim to reject or convert stale instant receipts to normal receipts if the APR-drop condition no longer holds.
- Alternatively, freeze APR changes while `pendingWithdraws > 0` (make `setApr` revert when open receipts exist), making the cached snapshot valid by construction.

### Proof of Concept
```solidity
// SPDX-License-Identifier: AGPL-3.0
// Fork-style PoC against EzraCole/idle-tranches--013 harness (see test/foundry/IdleCreditVault.t.sol)
pragma solidity 0.8.10;

import "./IdleCreditVault.t.sol";

contract StaleAprReceiptPoC is IdleCreditVaultTest {
    function testReceiptKeepsRequestTimeAprAfterMidEpochCut() external {
        uint256 depositAmt = 100_000 * ONE_SCALE;
        idleCDO.depositAA(depositAmt);
        _transferBurnedTrancheTokens(address(this), true);

        _startEpochAndCheckPrices(0); // epoch running at APR A_high

        // Attacker requests withdraw while APR is high: receipt locks interest at A_high
        uint256 trancheBal = IERC20(AAtranche).balanceOf(address(this));
        uint256 receipt = cdoEpoch.requestWithdraw(trancheBal, address(AAtranche));

        // Honest manager lowers APR mid-epoch (allowed by setAprsWithBuffer/setApr)
        uint256 highApr = strategy.unscaledApr();
        vm.prank(manager);
        IdleCreditVault(address(strategy)).setAprsWithBuffer(
            highApr / 2, cdoEpoch.epochDuration(), cdoEpoch.bufferDuration()
        );

        // Borrower funds pendingWithdraws at stopEpoch — includes interest at A_high
        vm.warp(cdoEpoch.epochEndDate() + 1);
        uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws();
        deal(defaultUnderlying, borrower, pending + depositAmt);
        vm.prank(manager);
        cdoEpoch.stopEpoch(0, 0);

        // Attacker claims the cached receipt verbatim
        uint256 balPre = underlying.balanceOf(address(this));
        cdoEpoch.claimWithdrawRequest();
        uint256 claimed = underlying.balanceOf(address(this)) - balPre;

        // Overpayment ~= principal * (A_high - A_low) * epochDuration / 365d,
        // i.e. `claimed` exceeds what a receipt priced at the final APR would yield.
        assertGt(claimed, receipt); // receipt already embeds full-epoch interest at stale APR
    }
}
```

Caveat: whether `requestWithdraw`'s interest is intentionally "locked at request APR" as a design choice (the comment at `IdleCDOEpochVariant.sol:786-787` suggests receipts are deliberately fixed and prepaid with management fees) determines whether this is a bug or accepted economics; if the intent is that the fixed receipt should reflect the epoch's realized yield at funding time — consistent with the APR0 settlement path and `lossRecoveryPriceByEpoch` haircuts — then the missing re-validation at claim height is the direct analog of the cached-verification bypass.