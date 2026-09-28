### Title
`claimInstantWithdrawRequest` clears the aggregate receipt counter but not the per-epoch basis, enabling a second payout from the default-recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a setter that fails to clear previously written per-key state when "resetting". The strongest analog in this codebase is `IdleCreditVault.claimInstantWithdrawRequest`: when a funded instant-withdraw receipt is claimed, the function zeroes `instantWithdrawsRequests[_user]` but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or decrements `instantWithdrawClaimsByEpoch[epoch]`. If the same epoch later defaults and is finalized, `_claimDefaultedInstantWithdrawRequest` re-reads that stale per-epoch basis and pays the user again out of `defaultRecoveryReserve`, which is isolated for genuine default-epoch claimants.

### Finding Description
`requestInstantWithdraw` records receipt basis twice: in the aggregate `instantWithdrawsRequests[_user]` and per-epoch via `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` / `instantWithdrawClaimsByEpoch[currentEpoch]` (lines 366-374). On claim, `claimInstantWithdrawRequest` only resets the aggregate (lines 387-392) — the per-epoch entries remain stale, exactly like the uncleared `feeMapping` entries in the external report.

Later, `finalizeDefaultRecovery` includes `instantWithdrawClaimsByEpoch[epochNumber]` in `defaultPendingClaimBasis` when `pendingInstantWithdraws != 0` (lines 644-649, 696), and `_claimDefaultedInstantWithdrawRequest` pays `instantWithdrawsRequestsByEpoch[_user][defaultEpoch] * defaultRecoveryPrice` from the reserve (lines 842-856). Because the stale per-epoch entry was never cleared, a user who already claimed at par can claim a second time. The only extra requirement is holding enough receipt balance for `_burn(_user, claimBasis)` — and `requestInstantWithdraw` has no `defaultRecoveryFinalized` gate (lines 356-375), so the attacker can open a fresh instant request after default to mint receipt tokens before claiming.

Attack sequence (epoch N, prefunded/instant mode):
1. Attacker calls `requestInstantWithdraw` via the CDO; basis recorded at `instantWithdrawsRequestsByEpoch[attacker][N]`.
2. Funds are collected (`collectInstantWithdrawFunds`) and attacker claims via `claimInstantWithdrawRequest` — paid in full, aggregate zeroed, per-epoch entry stale.
3. Borrower defaults within the same epoch N (`epochNumber` still N, no `stopEpoch` bump) while another user's instant request is still unfunded (`pendingInstantWithdraws != 0`).
4. Owner calls `finalizeDefault`/`finalizeDefaultRecovery`; `defaultRecoveryEpoch == N` and the stale claim basis inflates `defaultPendingClaimBasis`, slightly diluting recovery price but reserving a payout keyed to the attacker.
5. Attacker calls `requestInstantWithdraw` again (post-default mint of receipt tokens, allowed), then `claimInstantWithdrawRequest`, which routes into `_claimDefaultedInstantWithdrawRequest`, clears the stale epoch-N basis and pays `claimBasis * defaultRecoveryPrice` from `defaultRecoveryReserve`.

### Impact Explanation
Direct theft of the isolated default-recovery reserve: the attacker is paid `instantWithdrawsRequestsByEpoch[attacker][defaultEpoch] * defaultRecoveryPrice / 1e18` a second time, reducing `defaultRecoveryReserve` available to legitimate defaulted-epoch claimants (one receipt, two payouts). With a large instant request relative to total basis, the stolen amount approaches the attacker's full original instant claim times the recovery ratio.

### Likelihood Explanation
Requires (a) instant-withdraw funding within the same epoch (prefunded/available-liquidity mode), (b) a borrower default finalized before `epochNumber` increments, and (c) some remaining `pendingInstantWithdraws` so `defaultInstantWithdrawsFinalized` is set. These are ordinary vault states, not privileged misbehavior; the attacker is a normal unprivileged user.

### Recommendation
In `claimInstantWithdrawRequest`, also clear the per-epoch accounting for the funded epochs: zero `instantWithdrawsRequestsByEpoch[_user][claimEpoch]` and decrement `instantWithdrawClaimsByEpoch[claimEpoch]` when the receipt is claimed, mirroring how `_claimDefaultedInstantWithdrawRequest` clears them (lines 847-853).

### Proof of Concept
```solidity
// Foundry fork test sketch (against test/foundry/IdleCreditVault.t.sol harness)
// Setup: epoch N running, vault has prefunded liquidity for instant withdraws.
uint256 amt = 50_000 * ONE_SCALE;
address attacker = makeAddr("attacker");

// 1) attacker instant-withdraws and gets funded + claimed in epoch N
idleCDO.requestInstantWithdraw(amt); // as attacker
// manager/strategy funds it via collectInstantWithdrawFunds during same epoch
cdoEpoch.getInstantWithdrawFunds(); // prefunded path
idleCDO.claimInstantWithdrawRequest(); // attacker paid at par
assertEq(vault.instantWithdrawsRequests(attacker), 0);
// BUG: stale basis remains
assertGt(vault.instantWithdrawsRequestsByEpoch(attacker, vault.epochNumber()), 0);

// 2) borrower defaults in the SAME epoch (no stopEpoch -> epochNumber unchanged),
//    while pendingInstantWithdraws > 0 for another user's request
// owner finalizes:
cdoEpoch.finalizeDefault(); // -> vault.finalizeDefaultRecovery(...)
assertTrue(vault.defaultInstantWithdrawsFinalized());
assertEq(vault.defaultRecoveryEpoch(), vault.epochNumber());

// 3) attacker re-mints receipt tokens post-default (requestInstantWithdraw not gated)
idleCDO.requestInstantWithdraw(amt2); // mints receipt balance >= stale claimBasis

// 4) attacker claims again -> paid from defaultRecoveryReserve
uint256 reservePre = vault.defaultRecoveryReserve();
idleCDO.claimInstantWithdrawRequest();
assertLt(vault.defaultRecoveryReserve(), reservePre); // second payout for same receipt
```