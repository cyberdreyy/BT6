The bug class here is an **unchecked index into per-epoch accounting** — the vault analog of an unchecked array index. Let me verify the claim/funding paths around `stopEpochWithDuration` and epoch-keyed receipt lookups.### Title
Stale-epoch index in `defaultPendingClaimBasis` excludes old-epoch instant receipts from the recovery basis — unchecked-index analog of CVE-2018-11489 - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary

`defaultPendingClaimBasis()` indexes `instantWithdrawClaimsByEpoch` with only the **current** `epochNumber`, while `pendingInstantWithdraws` is a cross-epoch aggregate. Instant-withdraw receipts opened in an earlier epoch that were never funded keep `pendingInstantWithdraws > 0` but contribute nothing to the default-epoch claim basis. This is the same bug class as CVE-2018-11489: an index into a per-epoch table is trusted to cover the aggregate, and entries under other indices are silently skipped — here, skipped basis rather than a skipped array bound.

### Finding Description

In `IdleCreditVault.requestInstantWithdraw` (lines 356–375), each request is recorded per-epoch (`instantWithdrawsRequestsByEpoch[user][currentEpoch]`, `instantWithdrawClaimsByEpoch[currentEpoch]`) **and** in the aggregate `pendingInstantWithdraws`. Funds are pulled later via `collectInstantWithdrawFunds` (lines 398–403), which decrements only the aggregate. There is no mechanism that clears or migrates an unfunded instant receipt when the epoch rolls over.

At default finalization, `defaultPendingClaimBasis` (lines 644–649) computes:

```solidity
basis = pendingWithdraws;
if (pendingInstantWithdraws != 0) {
  basis += instantWithdrawClaimsByEpoch[epochNumber];
}
```

`pendingInstantWithdraws != 0` is satisfied by *any* unfunded instant receipt, including ones from epochs `< epochNumber`, but only `instantWithdrawClaimsByEpoch[epochNumber]` is added to the basis. Two consequences follow inside `finalizeDefaultRecovery` (lines 661–710):

1. **Understated basis, inflated `defaultRecoveryPrice`.** `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` divides the reserve over a basis that excludes the old-epoch instant claims. Every defaulted-epoch claimant (normal withdraws, current-epoch instant receipts, active AA/BB via the `activeFinalNAV` mint/burn at lines 699–705) is paid at a recovery ratio strictly above what the realized recovery supports. `defaultRecoveryReserve` is drained before all pro-rata claims are paid — classic over-indexed-payout insolvency.
2. **Mispayment of the old receipt itself.** `defaultInstantWithdrawsFinalized` is set true, so `claimInstantWithdrawRequest` (lines 380–393) first runs `_claimDefaultedInstantWithdrawRequest`, which only clears `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` (line 844). The old-epoch remainder in `instantWithdrawsRequests[user]` is then burned and paid **at par** through `_transferFundedClaim` (line 392) even though it was never funded and never included in the recovery basis — either stealing directly from `defaultRecoveryReserve`/strategy-held underlying, or permanently freezing when the balance is insufficient.

The guards that look relevant do not stop it: `_ensureDefaultRecoveryInitialized` does not reconcile the epoch index; `defaultInstantWithdrawsFinalized` assumes `pendingInstantWithdraws != 0` implies current-epoch claims, which is false once an instant receipt survives an epoch boundary unfunded.

### Impact Explanation

Broken invariants: *one receipt one payout* (recovery paid on a basis smaller than actual claims) and *solvency of the recovery reserve*. Concretely, a pre-existing unfunded instant receipt of size `I_old` reduces `totalBasis` by `I_old`, inflating `defaultRecoveryPrice` by a factor of `(activeBasis + pendingWithdraws + I_old) / (activeBasis + pendingWithdraws)`. Claimants who redeem early extract more than the recovered funds permit; later claimants or tranche holders absorbing `defaultBBNav` are left unpaid — a permanent loss proportional to `I_old` for whoever is last in line.

### Likelihood Explanation

Reachable by an unprivileged tranche-token holder with a single transaction: request an instant withdraw while liquidity for instant payouts is unavailable (e.g., during a running epoch or when `pendingInstantWithdraws` is not collected), let one or more honest `stopEpoch`/`startEpoch` cycles pass without `collectInstantWithdrawFunds` funding it, then wait for a borrower default and `finalizeDefaultRecovery`. No privileged misbehavior is required; the attacker merely needs their receipt to survive an epoch boundary, after which every honest finalization path misprices the recovery.

### Recommendation

Make the recovery-basis index cover the full aggregate it is meant to represent. Either (a) track instant claims by summing across all epochs — e.g., a global `instantWithdrawClaimsTotal` incremented in `requestInstantWithdraw` and decremented in `collectInstantWithdrawFunds`/`_claimDefaultedInstantWithdrawRequest` — and use that in `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve`, or (b) force-clear or roll forward stale per-epoch instant receipts into the current epoch at `stopEpoch`/before finalization so that `instantWithdrawClaimsByEpoch[epochNumber]` is provably complete. Correspondingly, `_claimDefaultedInstantWithdrawRequest` must clear each user's *entire* pending instant basis (all epochs), not only `defaultRecoveryEpoch`.

### Proof of Concept

Foundry fork test (mainnet, `FORK_BLOCK` style setup as in `test/foundry/IdleCreditVault.t.sol`, USDC underlying):

```solidity
function test_StaleEpochInstantReceiptSkewsRecovery() public {
    // Setup: KYC'd users userA (instant requester), userB (normal withdrawer)
    _depositWithUser(userA, 100e6);          // AA tranches
    _depositWithUser(userB, 100e6);

    // Epoch 1 running: borrower draws funds, instant liquidity is absent
    vm.prank(manager); cdoEpoch.startEpoch();
    // userA requests an instant withdraw; it stays pending/unfunded
    vm.prank(userA);  cdoEpoch.requestInstantWithdraw(50e6, AAtranche);
    assertEq(strategy.instantWithdrawClaimsByEpoch(1), 50e6);

    // Honest epochs roll; collectInstantWithdrawFunds never covers userA
    _stopCurrentEpoch();                      // epoch 2
    vm.prank(manager); cdoEpoch.startEpoch();
    // userB opens a normal withdraw request in epoch 2
    vm.prank(userB);  cdoEpoch.requestWithdraw(sharesB, AAtranche);
    _stopCurrentEpoch();

    // Borrower defaults; governance finalizes a partial recovery
    _makeBorrowerDefault(recovered);          // recovered < total real claims
    vm.prank(manager); cdoEpoch.finalizeDefaultRecovery(recovered, recoverySrc);

    // BUG: defaultPendingClaimBasis only counted instantWithdrawClaimsByEpoch[2]
    // (== 0 for userA whose receipt is in epoch 1), yet pendingInstantWithdraws
    // included userA's 50e6. defaultRecoveryPrice is inflated.
    uint256 basisCounted = strategy.defaultPendingClaimBasis(); // excludes 50e6
    uint256 realBasis    = basisCounted + 50e6;
    assertGt(strategy.defaultRecoveryPrice() * realBasis / 1e18,
             strategy.defaultRecoveryReserve(), "reserve < claims => insolvency");

    // userA still claims: defaulted-epoch clear finds nothing for epoch 1,
    // then the at-par path pays the stale receipt from reserve/strategy balance.
    uint256 pre = underlying.balanceOf(userA);
    vm.prank(userA); cdoEpoch.claimInstantWithdrawRequest();
    assertGt(underlying.balanceOf(userA) - pre, 0); // paid at par or drains reserve
    // A subsequent legitimate claimant's _transferDefaultRecovery reverts /
    // receives dust => permanent loss of I_old (=50e6) borne by last claimants.
}
```

Uncertainty note: the exact revert-vs-drain outcome at the final claim depends on `_transferFundedClaim`/`_transferDefaultRecovery` internals (lines ~897–960, not fully in scope here), but the accounting mismatch in `defaultPendingClaimBasis` is deterministic and is what produces either the overpayment or the frozen claim.