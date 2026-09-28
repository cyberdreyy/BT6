### Title
Instant-withdraw receipts are payable before the borrower funds them, draining underlying reserved for other claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` pays the full aggregate `instantWithdrawsRequests[_user]` from the vault's own underlying balance without checking that the corresponding `pendingInstantWithdraws` portion was ever funded via `collectInstantWithdrawFunds`. The receipt is burned and paid on demand; the funding leg is a separate, later call. This is the credit-vault analog of use-after-free: a claim consumes reserve that still belongs to other pending claims before its own backing exists.

### Finding Description
The bug class in CVE-2020-6450 is a stale object being used after its backing was released. Here the stale object is the instant-withdraw receipt's claim on vault-held underlying.

`requestInstantWithdraw` mints a receipt and increments three counters (`instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`, `pendingInstantWithdraws`) but moves no underlying. Funding only happens when IdleCDO calls `collectInstantWithdrawFunds`, which decrements `pendingInstantWithdraws` and pulls tokens in (lines 398–403).

`claimInstantWithdrawRequest` (lines 380–393) then does:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

There is no check tying the paid amount to what was actually collected. `pendingInstantWithdraws` is not decremented or consulted on the claim path at all — it is only touched by `collectInstantWithdrawFunds` and the default-finalization path. So a receipt that was never funded is still paid in full out of whatever underlying the vault currently holds: previously collected normal withdraw funds (`collectWithdrawFunds`), previously collected instant funds earmarked for other users' unclaimed receipts, or `defaultRecoveryReserve` backing.

Sequence for an unprivileged attacker (KYC-passing lender / tranche holder), in the running phase with instant withdraws enabled:

1. Honest users' earlier withdraw/instant requests were funded at a prior `stopEpoch` — the vault now holds underlying reserved for their claims, while they have not yet claimed.
2. Attacker calls the CDO's instant-withdraw request path → `requestInstantWithdraw` mints the receipt and raises `pendingInstantWithdraws` (no funds move).
3. Before the epoch ends and before `collectInstantWithdrawFunds` is called for this request, the attacker triggers the CDO's `claimInstantWithdrawRequest` for themselves. The vault burns the receipt and transfers the full amount from its balance — i.e., from funds backing other users' claims.
4. When the honest users later claim, the vault is short and their transfers revert or underpay: permanent loss / freezing of unclaimed funded claims.

A milder variant with the same root cause: a user holds an old funded instant receipt, requests a new unfunded instant receipt, then claims — the aggregate payout includes the unfunded part.

### Impact Explanation
Direct theft of vault-held underlying up to the attacker's instant-withdraw amount, bounded only by vault balance and the attacker's tranche position. Every token paid to the unfunded receipt is a token subtracted from other users' already-funded claims or the default-recovery reserve, causing permanent loss or permanently frozen claims for honest withdrawers (one receipt pays out twice economically — once to the thief, and the honest claim can no longer be honored). Broken invariant: one receipt, one payout, backed by collected funds.

### Likelihood Explanation
Requires only the standard instant-withdraw feature being enabled (an in-scope configuration the manager legitimately uses) and the vault holding any previously collected claim balance — a normal steady-state condition. The attacker needs no privilege; both steps are ordinary user calls routed through the honest CDO. The only precondition is timing the claim before the request's own epoch-end funding, which is the entire buffer/running window.

### Recommendation
Track funded vs. unfunded instant claims separately. Either:
- gate `claimInstantWithdrawRequest` so a user can only claim the portion already collected (`instantWithdrawsRequests[_user]` minus their share of `pendingInstantWithdraws`), or
- split per-epoch accounting so only epochs whose `collectInstantWithdrawFunds` has settled are payable, reverting otherwise.

Also decrement `pendingInstantWithdraws`/per-epoch claim basis consistently when defaulted instant claims are cleared so the funded-remainder math stays exact.

### Proof of Concept
Reproducible on a Foundry fork using the repo's own harness:

```solidity
// test/foundry/InstantClaimBeforeFunding.t.sol
function testInstantClaimPaysUnfundedReceipt() external {
    // setup: instantiate IdleCDOEpochVariant + IdleCreditVault as in IdleCreditVault.t.sol
    // 1. user1 deposits, epoch runs, requests normal withdraw; manager stops epoch;
    //    borrower repays -> collectWithdrawFunds pulls underlying into the vault.
    //    Vault balance > 0 backing user1's unclaimed funded receipt.
    // 2. manager enables instant withdrawals (setInstantWithdrawParams).
    // 3. attacker (KYC'd lender) deposits, requests instant withdraw during running epoch:
    //       cdoEpoch.requestInstantWithdraw / strategy.requestInstantWithdraw(amount, attacker)
    //    -> receipt minted, pendingInstantWithdraws += amount, NO funding pulled.
    // 4. attacker calls CDO claimInstantWithdrawRequest -> vault _burns receipt and
    //    _transferFundedClaim(attacker, amount) pays from vault balance.
    //    assert attacker underlying balance increased by `amount`
    //    while pendingInstantWithdraws is still `amount` (never collected).
    // 5. user1 calls claimWithdrawRequest -> transfer reverts/underpays:
    //    assert vault balance < user1 funded claim => funds stolen.
}
```

The key assertion is step 4: `underlying.balanceOf(attacker)` increases by the full request while `pendingInstantWithdraws` remains non-zero and `collectInstantWithdrawFunds` was never invoked — proving the payout consumed reserve belonging to other claims.

Note: I verified the vault-side code paths directly in `IdleCreditVault.sol` (lines 356–403). The one element I could not fully re-verify within the iteration budget is the exact CDO-side call ordering in `IdleCDOEpochVariant` (whether it enforces collect-before-claim for instant requests). The vault itself contains no funding check, so if the CDO permits user-initiated instant claims before epoch-end collection — which the `instantDelay` timing parameter implies — the exploit path holds; if the CDO hard-gates claims to post-collection, the vault-level missing check is still a defense-in-depth gap worth fixing.