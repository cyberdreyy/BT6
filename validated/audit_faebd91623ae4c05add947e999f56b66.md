### Title
Stale `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` entries survive claim and dilute default recovery pricing - ([File: contracts/strategies/idle/IdleCreditVault.sol](https://github.com/Thankgod67Ikhide/idle-tranches--003/blob/main/contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Analogous to the reference bug (re-registration leaves stale `name_to_addr`/`addr_to_name` mappings that later resolve to the old owner), `claimInstantWithdrawRequest` clears the aggregate receipt `instantWithdrawsRequests[_user]` but never clears the per-epoch mappings `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` written in `requestInstantWithdraw`. If a default is later finalized for that same epoch (`defaultRecoveryEpoch = epochNumber`), the stale per-epoch claim basis is re-counted in `defaultPendingClaimBasis`, inflating `totalBasis` and pushing `defaultRecoveryPrice` below its correct value. Honest defaulted-epoch claimants are underpaid and the excess recovery reserve is permanently stranded as dust.

### Finding Description
In `requestInstantWithdraw` (lines 366–374) the vault records three pieces of state: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and `instantWithdrawClaimsByEpoch[currentEpoch]`. The normal claim path `claimInstantWithdrawRequest` (lines 380–393) only zeroes `instantWithdrawsRequests[_user]`; the two per-epoch mappings are never decremented. `collectInstantWithdrawFunds` (lines 398–403) decrements `pendingInstantWithdraws` but likewise never touches `instantWithdrawClaimsByEpoch`.

At default finalization, `defaultPendingClaimBasis` (lines 644–649) adds `instantWithdrawClaimsByEpoch[epochNumber]` to the recovery basis whenever `pendingInstantWithdraws != 0`, and `finalizeDefaultRecovery` (lines 679–696) computes `defaultRecoveryPrice = reserveAmount * 1e18 / totalBasis` and sets `defaultRecoveryEpoch = epochNumber`. A stale, already-paid instant claim therefore:

1. Inflates `instantWithdrawClaimsByEpoch[defaultEpoch]` → inflates `totalBasis` → lowers `defaultRecoveryPrice` for every recovery claimant (active LPs via `activeFinalNAV` at lines 699–705 and receipt holders via `_claimDefaultedWithdrawRequest`/`_claimDefaultedInstantWithdrawRequest` at lines 782, 855).
2. Via `_defaultPrefundedInstantReserve` (lines 716–723) the stale `instantBasis > pendingInstant` gap also inflates `prefundedReserve`, further distorting the price.
3. The stranded portion of `defaultRecoveryReserve` corresponding to the phantom basis can never be claimed: the stale user's own `_claimDefaultedInstantWithdrawRequest` reverts on the `instantWithdrawsRequests[_user] -= claimBasis` underflow (line 848) and on `_burn` of receipt tokens they no longer hold, so the dust is permanently locked.

The mitigation in the reference report was "delete old mappings at re-registration instead of lazily"; here the equivalent lazy cleanup is simply missing on the normal claim path.

### Impact Explanation
Honest defaulted-epoch withdraw claimants and active tranche holders receive a strictly lower `defaultRecoveryPrice` than warranted, and the corresponding slice of the recovered reserve is permanently frozen in the strategy (unclaimable dust). Loss is bounded by the size of instant withdrawals claimed in the defaulting epoch relative to total recovery basis — e.g., a claimed instant receipt equal to 25% of the real default basis cuts every claimant's recovery payout by ~20%, with that value stranded in the contract rather than paid to anyone. This is a direct, quantified loss to honest users plus permanent freezing of recovery funds, not a DoS-only issue.

### Likelihood Explanation
Requires: (a) the pool runs in a mode with instant withdrawals enabled (prefunded/liquid epoch variant), (b) at least one instant withdrawal is requested *and claimed* inside the epoch that later defaults, (c) `pendingInstantWithdraws` is still nonzero at finalization (i.e., some other instant receipt of that epoch was only partially funded). Instant request + claim within one running epoch is the intended flow of the instant mode, and partial funding of the instant queue is precisely the scenario the `_defaultPrefundedInstantReserve` accounting was built for, so the preconditions are realistic. All actors are unprivileged users; the honest borrower/manager merely default and finalize. No existing guard (`defaultRecoveryFinalized` checks, `_onlyIdleCDO`, underflow reverts) prevents the basis inflation — the underflow only blocks the stale user's own second payout, after the diluted price has already been locked in.

### Recommendation
Clear the per-epoch mappings in the normal claim path: in `claimInstantWithdrawRequest`, after computing `amount`, delete `instantWithdrawsRequestsByEpoch[_user][epochNumber]` (or track and clear the request epoch) and decrement `instantWithdrawClaimsByEpoch[epochNumber]` by the claimed amount, mirroring how `_claimDefaultedInstantWithdrawRequest` (lines 847–853) already cleans up all three counters. Alternatively, record the request epoch per user so the claim path can clear the correct epoch entry rather than assuming the current one.

### Proof of Concept
```solidity
// Foundry fork PoC (mainnet fork, prefunded/instant epoch mode)
// 1. Epoch N running; strategy holds liquidity.
// 2. Attacker: cdo.requestInstantWithdraw(X) then claimInstantWithdrawRequest
//    -> instantWithdrawsRequests[attacker] = 0
//    -> instantWithdrawsRequestsByEpoch[attacker][N] = X   (STALE)
//    -> instantWithdrawClaimsByEpoch[N] = X                (STALE)
// 3. Victim: requestInstantWithdraw(Y), only partially funded
//    -> pendingInstantWithdraws = Y - funded > 0
// 4. Borrower defaults mid-epoch N; owner finalizes:
//    strategy.finalizeDefaultRecovery(recovered, source)
//    -> defaultPendingClaimBasis() = pendingWithdraws + instantWithdrawClaimsByEpoch[N]
//       counts victim's real Y plus attacker's stale X
//    -> defaultRecoveryPrice = reserve * 1e18 / (realBasis + X)  // diluted
// 5. Victim claims: receives claimBasis * dilutedPrice / 1e18
//    => assert victim payout < claimBasis * correctPrice / 1e18
// 6. Attacker re-claim via claimInstantWithdrawRequest reverts on
//    instantWithdrawsRequests underflow; the reserve slice matching X
//    remains locked in the strategy forever (unclaimable dust).
assertLt(strategy.defaultRecoveryPrice(), expectedUndilutedPrice);
```

Key references: `requestInstantWithdraw`/`claimInstantWithdrawRequest` `contracts/strategies/idle/IdleCreditVault.sol:356-393`, missing per-epoch cleanup vs `_claimDefaultedInstantWithdrawRequest` `:842-856`, `defaultPendingClaimBasis` `:644-649`, `finalizeDefaultRecovery` `:661-710`, `_defaultPrefundedInstantReserve` `:716-723`.

Caveat: this analysis assumes the deployed epoch variant permits an instant request and claim within the same epoch that later defaults (the prefunded/instant modes are designed for this), and that the CDO does not independently gate instant requests post-default; I could not fully verify the CDO-side `claimInstantWithdrawRequest` call path within the available iterations.