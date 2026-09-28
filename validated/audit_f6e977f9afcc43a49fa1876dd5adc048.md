### Title
Post-default instant withdraw receipts are paid at par from the default recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug is a use-after-free: `bfq_exit_icq_bfqq()` freed `bfqq` and then `bic_set_bfqq()` dereferenced it. The ordering analog in `IdleCreditVault` is `requestInstantWithdraw`/`claimInstantWithdrawRequest`: after `finalizeDefaultRecovery` has crystallized the default epoch and sealed `defaultRecoveryReserve`, the vault still accepts new instant-withdraw requests through the pre-default code path and pays them out of strategy-held underlying, which is now the recovery reserve earmarked for defaulted-epoch claimants. `requestWithdraw` was explicitly rerouted for the post-default regime (`postDefaultRequests`), but `requestInstantWithdraw` has no `defaultRecoveryFinalized` check at all.

### Finding Description
`requestInstantWithdraw` (lines 356–375) burns CDO strategy tokens, mints receipts to the user, and increments `instantWithdrawsRequests[_user]`/`pendingInstantWithdraws` unconditionally — it never checks `defaultRecoveryFinalized`, unlike `requestWithdraw` which diverts into `postDefaultRequests` and reverts if prior receipts are open (lines 247–257).

On the claim side, `claimInstantWithdrawRequest` (lines 380–393) only routes through `_claimDefaultedInstantWithdrawRequest` when `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`. `defaultInstantWithdrawsFinalized` is set to `pendingInstantWithdraws != 0` at finalization (line 696). So when the instant queue was fully funded before the default (pendingInstantWithdraws == 0 at `finalizeDefaultRecovery`), the flag stays false and a post-default instant request is paid in full via `_transferFundedClaim(_user, amount)` — funded directly from underlying held by the strategy, i.e. `defaultRecoveryReserve` plus prefunded amounts reserved for defaulted-epoch receipt holders.

Even when `defaultInstantWithdrawsFinalized` is true, a post-default request is recorded at the current `epochNumber`, which no longer advances after default, so it aliases `defaultRecoveryEpoch` inside `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` and corrupts `_claimDefaultedInstantWithdrawRequest`'s per-epoch accounting (`instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis`, `pendingInstantWithdraws` saturation).

Invariant broken: one receipt one payout / loss waterfall — the post-default receipt burns CDO active-backing tokens priced at `defaultRecoveryPrice` but is served at par (or double-counted against the defaulted-epoch bucket), spending reserve that belongs to users who have not yet claimed their haircutted recovery.

### Impact Explanation
An unprivileged user holding AA/BB tranche tokens calls `cdoEpoch.requestInstantWithdraw` after `finalizeDefaultRecovery`. The resulting claim withdraws underlying at par while every defaulted-epoch claimant is only entitled to `basis * defaultRecoveryPrice`. The excess comes out of `defaultRecoveryReserve`, so late-claiming defaulted receipt holders (and active-LP recovery) are underpaid or the reserve is drained — direct theft of unclaimed recovery, quantified as `amount * (1 - defaultRecoveryPrice)` per request, repeatable until the reserve is exhausted.

### Likelihood Explanation
Requires only that a default was finalized and that `pendingInstantWithdraws == 0` at finalization (the common fully-funded instant queue case) — both reachable with honest privileged roles. The attacker needs only tranche tokens and the ordinary `requestInstantWithdraw`/`claimInstantWithdrawRequest` entry points; no epoch transition is needed. The main uncertainty is whether `_transferFundedClaim` internally subtracts `defaultRecoveryReserve` from the spendable balance — that helper's body was not fully verified in this review; if it does not enforce the reserve floor, the exploit path is open.

### Recommendation
Mirror the `requestWithdraw` post-default guard in `requestInstantWithdraw`: revert (or route through a haircutted `postDefaultRequests`-style bucket) whenever `defaultRecoveryFinalized` is true. Additionally, `_transferFundedClaim` should cap transfers at `underlyingToken.balanceOf(this) - defaultRecoveryReserve` once recovery is finalized, and `claimInstantWithdrawRequest` should key post-default receipts off a non-defaulted epoch marker so they cannot alias `defaultRecoveryEpoch`.

### Proof of Concept
```solidity
// Foundry fork test sketch (test/foundry/IdleCreditVault.t.sol harness)
// 1. user A deposits AA, requests a normal withdraw; user B deposits AA.
// 2. Run epoch, stopEpoch with borrower returning 0 -> defaulted (as in
//    testProcessPostDefaultWithdrawalAsNormalClaim).
// 3. finalizeDefault(recovered, manager) with recovered < totalBasis
//    -> defaultRecoveryPrice < 1e18, defaultInstantWithdrawsFinalized == false
//    (ensure pendingInstantWithdraws == 0 at finalization).
// 4. Attacker (any tranche holder, e.g. user B):
//       cdoEpoch.requestInstantWithdraw(amount, AAtranche);  // succeeds:
//       no defaultRecoveryFinalized check in requestInstantWithdraw
//       cdoEpoch.claimInstantWithdrawRequest();
// 5. Assert attacker received `amount` at par while
//    strategy.defaultRecoveryReserve() decreased by `amount`, leaving
//    reserve < pendingBasis * defaultRecoveryPrice -> user A's later
//    claimWithdrawRequest() underflows/reverts or pays less than owed.
```