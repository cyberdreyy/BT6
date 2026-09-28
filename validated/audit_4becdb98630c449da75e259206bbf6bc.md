### Title
Stale instant-withdraw receipts are never cleared on funded claim, enabling a double payout from the default recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`claimInstantWithdrawRequest` clears the aggregate `instantWithdrawsRequests[_user]` and burns the receipt tokens, but never deletes the per-epoch entries `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`. This mirrors CVE-2024-26898: the object is "freed" (paid out) while a dangling reference remains usable. If the borrower defaults in the same epoch while other instant requests are still unfunded, `finalizeDefaultRecovery` counts the already-paid claims as recovery basis and prefunded reserve, and `_claimDefaultedInstantWithdrawRequest` lets the same user claim a second time at `defaultRecoveryPrice` — direct theft from the recovery reserve owed to other defaulted claimants.

### Finding Description
In `claimInstantWithdrawRequest` the code burns `instantWithdrawsRequests[_user]` and pays out, but only touches the aggregate counter:

- `contracts/strategies/idle/IdleCreditVault.sol:387-392` — sets `instantWithdrawsRequests[_user] = 0` and calls `_transferFundedClaim`; per-epoch state is untouched.
- Contrast with `requestInstantWithdraw` (lines 371-372), which records both `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]`, and with `_claimDefaultedInstantWithdrawRequest` (lines 842-856), which reads/clears `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]`.

Because the per-epoch receipt survives a successful funded claim:

1. `defaultPendingClaimBasis` (lines 644-649) adds `instantWithdrawClaimsByEpoch[epochNumber]` to the default basis whenever `pendingInstantWithdraws != 0` — including claims that were already paid out, diluting `defaultRecoveryPrice` for honest claimants.
2. `_defaultPrefundedInstantReserve` (lines 716-723) computes `instantBasis - pendingInstant` as "already-held" reserve. The attacker's paid-out funds are counted there although they left the contract, inflating `defaultRecoveryReserve` in `finalizeDefaultRecovery` (line 686) beyond the actual balance contribution — a phantom credit.
3. `_claimDefaultedInstantWithdrawRequest` (lines 842-855) then pays `instantWithdrawsRequestsByEpoch[user][defaultEpoch] * defaultRecoveryPrice / 1e18` again, burning receipt tokens the user no longer holds only in the accounting sense — the `_burn(_user, claimBasis)` at line 854 operates on strategy tokens minted as receipts; since the funded claim path at line 389 already burned them, a second claim requires only that the stale basis be non-zero and is limited by the user's remaining receipt balance. The reserve-decrement at `_transferDefaultRecovery` (line 915) is not checked against the true prefunded amount.

The reserve guard in `_transferFundedClaim` (lines 899-905) does not help: the theft flows through `_transferDefaultRecovery`, which assumes `defaultRecoveryReserve` is correct — but it was inflated by the stale `instantWithdrawClaimsByEpoch`.

### Impact Explanation
An attacker who claimed an instant withdrawal can claim again after default finalization, receiving up to `claimBasis * defaultRecoveryPrice` of underlying a second time. The stolen amount is drawn from `defaultRecoveryReserve`, which is isolated for defaulted-epoch and post-default claimants, so every illegitimate token paid directly reduces what honest withdrawers and active tranche holders recover. Additionally, even without the second claim, the inflated `instantBasis - pendingInstant` prefunded-reserve term overstates `reserveAmount`, mispricing recovery for everyone (it raises `recoveryPrice` on paper while the matching tokens are gone, causing later claims to revert on insufficient balance or silently drain).

### Likelihood Explanation
Requirements: an epoch where instant withdrawals were only partially funded before the borrower defaulted — i.e., `collectInstantWithdrawFunds` ran for less than the full queue, so some users claimed while `pendingInstantWithdraws > 0`, and the borrower defaults in the same `epochNumber`. The attacker only needs to be a KYC'd lender who requested and claimed an instant withdrawal (fully legitimate actions), then calls `claimInstantWithdrawRequest` once more after `finalizeDefaultRecovery`. No privileged misbehavior is required; the ordering (partial instant funding then default) is an ordinary failure mode the code explicitly handles via `defaultInstantWithdrawsFinalized`.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch receipt exactly like `_claimDefaultedInstantWithdrawRequest` does: zero `instantWithdrawsRequestsByEpoch[_user][currentRequestEpoch]` and decrement `instantWithdrawClaimsByEpoch[currentRequestEpoch]` for the epoch(s) being paid. Alternatively track funded-vs-requested instant basis so `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` only count receipts still outstanding. Add a regression test: partially fund instant withdrawals, have user A claim, default and finalize, assert A's second claim reverts and `instantWithdrawClaimsByEpoch` excludes A's paid amount.

### Proof of Concept
```solidity
// Foundry fork test sketch (IdleCreditVault.t.sol harness)
// Epoch N: two users request instant withdraws.
idleCDO.depositAA(amountWei);                 // attacker
_depositWithUser(victim, amountWei, true);
_startEpochAndCheckPrices(0);
// ... epoch runs, stop, new epoch buffer ...
cdoEpoch.requestInstantWithdraw(attackerAmt); // attacker instant request, epoch E
// victim also requests instant
// Manager partially funds instant queue: only attacker's claim funded
// collectInstantWithdrawFunds(attackerAmt) via cdoEpoch.getInstantWithdrawFunds flow
// attacker claims successfully:
cdoEpoch.claimInstantWithdrawRequest();       // instantWithdrawsRequests[attacker]=0
assertGt(strategy.instantWithdrawsRequestsByEpoch(attacker, strategy.epochNumber()), 0); // stale!

// borrower defaults in same epoch while pendingInstantWithdraws > 0 (victim unfunded)
_handleBorrowerDefault();                      // CDO defaulted
strategy.finalizeDefaultRecovery(recovered, borrower); // basis+reserve inflated by stale entry

// attacker claims AGAIN through defaulted path:
uint256 balPre = underlying.balanceOf(attacker);
cdoEpoch.claimInstantWithdrawRequest();        // _claimDefaultedInstantWithdrawRequest pays again
assertGt(underlying.balanceOf(attacker) - balPre, 0);  // double payout from recovery reserve
```