### Title
Pre-default-epoch instant-withdraw receipts escape the recovery haircut and claim at par from unreserved strategy float - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The Traefik bug class is "a strict option on one entry silently falls back to the default because resolution is shared across entries". The analog: instant-withdraw receipts share a single aggregate `pendingInstantWithdraws` counter and a single `defaultRecoveryEpoch`, so only receipts recorded in the default epoch get the `defaultRecoveryPrice` haircut. Receipts opened in an *earlier* epoch that are still (partially) unfunded at default finalization bypass `_claimDefaultedInstantWithdrawRequest` entirely and are paid 1:1 by `_transferFundedClaim`, out of any underlying the strategy happens to hold — including float the code itself acknowledges exists (failed borrower sends, prefunded instant cash). They jump ahead of haircutted claimants and consume value that was priced into the shared recovery pool.

### Finding Description
`finalizeDefaultRecovery` computes the recovery basis as `defaultPendingClaimBasis()`, which adds `instantWithdrawClaimsByEpoch[epochNumber]` — i.e. only instant claims recorded in the *current* (default) epoch — to `pendingWithdraws`, and only when the aggregate `pendingInstantWithdraws != 0` (`IdleCreditVault.sol:644-649`). Older-epoch instant receipts are excluded from `totalBasis` even though their unfunded share is still part of the aggregate `pendingInstantWithdraws`, so `recoveryPrice` is computed without them.

At claim time, `claimInstantWithdrawRequest` (`IdleCreditVault.sol:380-393`) only routes receipts keyed to `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` through `_claimDefaultedInstantWithdrawRequest` (the strict path, paying `claimBasis * defaultRecoveryPrice` from the isolated reserve). Any remaining balance in `instantWithdrawsRequests[user]` — which includes unfunded receipts from epochs *before* `defaultRecoveryEpoch` — falls through to:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

`_transferFundedClaim` (`IdleCreditVault.sol:897-907`) only guards that `balance - defaultRecoveryReserve >= amount`. It does not check that the strategy actually holds earmarked funds for that receipt. The code's own comments admit the strategy can hold unreserved underlying: "Some recovery funds may already be in this strategy: partially prefunded instant requests and borrower-send funds that failed at epoch start" (`IdleCreditVault.sol:683-684`). Only the prefunded-current-epoch part is folded into the reserve (`_defaultPrefundedInstantReserve`, lines 716-723); failed borrower-send float is not.

Net effect: a user who opened an instant withdraw in epoch E-1, left it unfunded, and survives a default finalized in epoch E redeems at 100% instead of `defaultRecoveryPrice`, while every other claimant takes the haircut. If no float exists, the same fallback reverts the transfer and the receipt is frozen forever — both outcomes map to the accepted impact classes.

### Impact Explanation
Quantified example: recovery finalizes at `defaultRecoveryPrice = 0.5e18`. Attacker holds a 100k instant receipt from epoch E-1; all default-epoch claimants recover 50%. The attacker calls `claimWithdrawRequest`/`claimInstantWithdrawRequest` and receives 100k underlying from strategy float that honest accounting should have either haircut to 50k or reserved for the recovery pool. That is direct theft of ~50k (the difference between par and the haircut) and dilution of the recovery reserve backing other claimants; where float is absent the receipt is permanently unclaimable.

### Likelihood Explanation
Only unprivileged actions are needed: the attacker is an ordinary KYC'd tranche holder who calls `requestInstantWithdraw` via the CDO in the epoch before default and simply does not get funded before the epoch rolls. Defaults are honest-triggered (`_handleBorrowerDefault` by borrower/manager), and instant-queue underfunding is routine (instant liquidity depends on available cash). The shared-resolution flaw is deterministic once those conditions hold; no privileged cooperation is required.

### Recommendation
Track instant receipts per epoch end-to-end: in `defaultPendingClaimBasis()` include all unfunded instant basis (e.g. iterate/accumulate `instantWithdrawClaimsByEpoch` for all epochs ≤ current, or maintain a per-epoch unfunded mapping rather than the single aggregate `pendingInstantWithdraws`), and in `claimInstantWithdrawRequest` route *every* receipt that was unfunded at `defaultRecoveryEpoch` through `_claimDefaultedInstantWithdrawRequest` instead of letting pre-default-epoch receipts fall through to `_transferFundedClaim`. Additionally, fold failed borrower-send float into `defaultRecoveryReserve` at finalization so `_transferFundedClaim` cannot spend it.

### Proof of Concept
Foundry sketch (extends `test/foundry/IdleCreditVault.t.sol` setup, epoch CDO + IdleCreditVault strategy):

```solidity
function test_preDefaultInstantReceiptEscapesHaircut() public {
    // --- epoch E-1: attacker queues instant withdraw, stays unfunded ---
    uint256 attackerAmt = 100_000 * ONE_SCALE;
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(attackerAmt, address(AAtranche));
    // do NOT fund pendingInstantWithdraws; roll epoch forward via manager stop/start
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    _startEpoch(); // epoch E begins; attacker receipt now epoch E-1

    // --- honest users deposit / queue normal withdraws in epoch E ---

    // --- borrower defaults; owner/manager finalize recovery at 50% ---
    _handleBorrowerDefault();
    deal(defaultUnderlying, recoverySource, recovered); // recovered = 50% of basis
    cdoEpoch.finalizeDefaultRecovery(recovered, recoverySource);
    assertEq(strategy.defaultRecoveryPrice(), 0.5e18);

    // seed strategy float the code itself anticipates (failed borrower send)
    deal(defaultUnderlying, address(strategy), attackerAmt);

    // attacker claims: pre-default-epoch receipt skips the haircut path
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(underlying.balanceOf(attacker), attackerAmt); // paid at PAR, not 50%
}
```

Limitation to verify in a full session: the exact ordering of `epochNumber` increment vs `collectWithdrawFunds`/`collectInstantWithdrawFunds` inside `IdleCDOEpochVariant.stopEpoch` (to confirm the E-1 receipt epoch keying), and a concrete float source in the target deployment (direct borrower repayment to the strategy). Both are supported by the code comments at `IdleCreditVault.sol:683-686` and the per-epoch maps at lines 107-109, but were not fully traced within this iteration budget.