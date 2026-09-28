### Title
Instant-withdraw receipts escape loss socialization on a lossy `stopEpoch`, letting an attacker pre-position into a par-claimable bucket before the loss is applied - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault` keeps instant-withdraw requests in a separate accounting bucket (`instantWithdrawsRequests`, `pendingInstantWithdraws`, `instantWithdrawsRequestsByEpoch`) that is completely excluded from the stop-epoch loss split. `previewLossAdjustedWithdrawFunds` divides a realized loss only between active LPs (`activeBasis`) and normal pending receipts (`pendingWithdraws` via `lossRecoveryPriceByEpoch`), and `claimInstantWithdrawRequest` pays the full requested amount at par with no epoch wait and no haircut check. An unprivileged lender who anticipates a lossy epoch end (e.g. distressed borrower, observable on-chain or via the loss-approval flow where `stopEpochWithDuration`/`stopEpoch` with a loss parameter is signed and executed) can call `requestInstantWithdraw` during the buffer while the pool is still healthy, and later claim the receipt at 100 cents while every other LP and normal withdraw requester absorbs the attacker's share of the loss. This mirrors the referenced bug class: a claim is created before the protective measure (the loss haircut applied at `stopEpoch`) is enabled, and is then used to bypass it.

### Finding Description
- `requestInstantWithdraw` (IdleCreditVault.sol:356-375) burns the CDO's strategy tokens at current price, mints a par receipt to the user, and increments `pendingInstantWithdraws` and `instantWithdrawsRequestsByEpoch[user][epochNumber]`. There is no loss-awareness: no check that the current epoch might end with a shortfall.
- `previewLossAdjustedWithdrawFunds` (IdleCreditVault.sol:440-460) computes `pendingLoss = _lossAmount * pendingBasis / totalBasis` where `totalBasis = activeBasis + pendingBasis`. `pendingBasis` is `pendingWithdraws` only — `pendingInstantWithdraws` is never added to the loss-bearing basis. Loss-adjusted receipts are haircut via `lossRecoveryPriceByEpoch` in `collectWithdrawFunds` (lines 411-430); no equivalent `lossRecoveryPrice` exists for instant claims.
- `claimInstantWithdrawRequest` (lines 380-393) burns `instantWithdrawsRequests[_user]` and pays the full amount via `_transferFundedClaim`, whose only guard is the default-recovery reserve isolation (lines 897-907). It never consults `lossRecoveryPriceByEpoch` or any haircut.

Concretely: with active LP basis `A`, pending normal receipts `P`, and instant receipts `I`, a loss `L` should spread over `A + P + I`. Instead it spreads over `A + P` only, and the CDO is expected to fund instant claims at par through `collectInstantWithdrawFunds`. Each dollar of instant receipts therefore transfers its pro-rata loss share `L * I / (A + P + I)` onto active LPs and normal receipt holders.

### Impact Explanation
Direct loss transfer / broken loss-waterfall invariant. An attacker holding tranche tokens converts them into an instant receipt during the buffer phase while `virtualPrice` is unimpaired; after the lossy `stopEpoch` they claim at par. Remaining LPs and normal withdraw requesters absorb the shifted loss — quantifiable as the attacker's avoided loss share `L * receipt / totalBasis`, which is stolen from honest claimants' funded recovery. If the pool is thin, paying instant claims at par can also leave the funded balance insufficient for loss-adjusted receipts, causing permanent underpayment or `NotAllowed` reverts for later claimants.

### Likelihood Explanation
Requires (a) the vault to support instant withdrawals and (b) a loss event on an epoch the attacker can anticipate. Losses on these credit pools arise from borrower under-repayment executed via manager-signed `stopEpoch` calls; borrower distress is frequently visible before epoch end, and the buffer window between `stopEpoch` (which increments `epochNumber` and records `lossRecoveryPriceByEpoch`) gives an open window to request instant withdrawals. Any KYC-passing tranche holder can execute it with a single transaction. The only mitigation is operational funding order on the honest manager side; nothing in the vault code haircuts instant claims.

### Recommendation
Include `pendingInstantWithdraws` in the loss-bearing basis in `previewLossAdjustedWithdrawFunds` and store a per-epoch `instantLossRecoveryPrice` keyed on the request epoch (mirroring `instantWithdrawsRequestsByEpoch`), then apply the haircut inside `claimInstantWithdrawRequest` exactly as `_claimLossAdjustedWithdrawRequest` does for normal receipts. Alternatively, disallow `requestInstantWithdraw` once `stopEpoch` has been called with a non-zero loss or freeze instant claims until the loss accounting for the request epoch is finalized.

### Proof of Concept
Foundry fork PoC (style follows `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testInstantWithdrawEscapesEpochLoss() external {
    uint256 amountWei = 10_000 * ONE_SCALE;
    address attacker = makeAddr("attacker");
    address victim   = makeAddr("victim");

    // Both deposit into AA tranche during buffer of epoch 0
    _depositWithUser(attacker, amountWei, true);
    _depositWithUser(victim,   amountWei, true);

    // Attacker anticipates a lossy epoch and converts to a par instant receipt
    uint256 attackerTranche = IERC20(AAtranche).balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(attackerTranche); // burns at current price, mints par receipt

    // Victim keeps a normal pending request (haircut target)
    vm.prank(victim);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(0);

    // Borrower repays with a shortfall -> lossy stopEpoch
    uint256 loss = cdoEpoch.getContractValue() / 2;
    _stopEpochWithLossAndCheck(0, initialProvidedApr, loss); // stopEpochWithDuration / loss path

    IdleCreditVault vault = IdleCreditVault(address(strategy));

    // Instant claims funded at par (no entry in lossRecoveryPriceByEpoch)
    uint256 lossPrice = vault.lossRecoveryPriceByEpoch(0);
    assertLt(lossPrice, ONE_TRANCHE, "loss epoch recorded");

    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    uint256 receipt = vault.instantWithdrawsRequests(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();            // pays 1:1, no haircut
    uint256 attackerOut = IERC20Detailed(defaultUnderlying).balanceOf(attacker) - balPre;
    assertEq(attackerOut, receipt, "attacker paid at par despite epoch loss");

    // Victim's normal receipt is haircut by lossRecoveryPrice
    uint256 victimPre = IERC20Detailed(defaultUnderlying).balanceOf(victim);
    vm.prank(victim);
    cdoEpoch.claimWithdrawRequest();
    uint256 victimOut = IERC20Detailed(defaultUnderlying).balanceOf(victim) - victimPre;
    uint256 victimBasis = amountWei; // ~receipt basis
    assertApproxEqAbs(victimOut, victimBasis * lossPrice / ONE_TRANCHE, 5, "victim haircut");

    // Net effect: victim's loss share is larger by exactly attacker's avoided haircut
    assertLt(victimOut, victimBasis, "victim absorbed shifted loss");
}
```

Note on residual uncertainty: this analysis was completed with limited iterations, so the CDO-side funding order inside `IdleCDOEpochVariant.stopEpoch`/`stopEpochWithDuration` (whether `collectInstantWithdrawFunds` is invoked with the full `pendingInstantWithdraws` at par) was not fully read. If the CDO already underfunds instant claims proportionally to the loss, the exploit reduces to a freezing/underfunding inconsistency rather than par payout; either way the instant bucket's exclusion from the loss basis is incorrect accounting.