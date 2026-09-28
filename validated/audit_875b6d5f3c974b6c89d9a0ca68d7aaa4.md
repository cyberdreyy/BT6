### Title
Instant-withdraw claims pay the full aggregate receipt at par even when only partially funded, letting a claimant drain other users' funded withdraw reserves - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug is a stale "selection" that outlives a resize of the underlying buffer: the selection bounds were not cleared before the screen shrank, so a later remove touched out-of-bounds memory. The analog in `IdleCreditVault` is `claimInstantWithdrawRequest`: the claimable "selection" is the aggregate `instantWithdrawsRequests[_user]`, which is never reconciled against the amount actually funded for that epoch. Normal withdraw receipts gate claims on `epochNumber <= lastWithdrawRequest` and per-epoch bookkeeping (`withdrawsRequestsByEpoch`, `lossRecoveryPriceByEpoch`), and defaulted receipts are haircut via `_claimDefaultedInstantWithdrawRequest`. Instant receipts have no equivalent funded-vs-unfunded bound: the full aggregated amount is burned and paid at par through `_transferFundedClaim`.

### Finding Description
`requestInstantWithdraw` mints the user a receipt and increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[user][epochNumber]`, `instantWithdrawClaimsByEpoch[epochNumber]`, and `pendingInstantWithdraws`. Funding arrives later via `collectInstantWithdrawFunds`, which only decrements `pendingInstantWithdraws` and pulls `_amount` of underlying from the CDO — nothing records how much of a given user's receipt is actually backed. The code itself acknowledges the partially funded state: `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` exist precisely because `pendingInstantWithdraws != 0` means "current-epoch instant receipts were not fully funded" and `instantBasis > pendingInstant` is the already-funded remainder.

`claimInstantWithdrawRequest` then does:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

It pays `amount` — the entire aggregate including the unfunded remainder — at par. `_transferFundedClaim` only protects `defaultRecoveryReserve` (set at default finalization); before finalization there is no bound check against funded balances at all. So a user with an instant receipt in an epoch where the borrower repayment only partially covered the instant queue (`pendingInstantWithdraws > 0`) claims the unfunded portion out of underlying held in the strategy that belongs to other users' funded normal/instant withdraw receipts — exactly the "selection outside the new buffer size" pattern: the claim boundary was never resized to the funded size before the removal (payout) occurred.

Attack sequence (attacker = ordinary tranche holder / withdrawer, all privileged roles honest):
1. Buffer phase: attacker deposits via `depositAA`/`depositBB`, requests an instant withdraw of X via the CDO. Other users also have funded normal withdraw receipts whose underlying already sits in the strategy (collected via `collectWithdrawFunds`).
2. `startEpoch` moves available cash to the strategy but it covers only part of the instant queue, so `pendingInstantWithdraws` stays > 0 — a state the code explicitly models.
3. After `instantWithdrawDeadline`/`allowInstantWithdraw`, attacker calls `claimWithdrawRequest`/`claimInstantWithdrawRequest` on the CDO, which calls `IdleCreditVault.claimInstantWithdrawRequest`. The function burns X and transfers X underlying at par, spending other users' funded-claim underlying for the `X - funded` shortfall.

### Impact Explanation
Direct theft with quantified loss: the attacker extracts `X` underlying while only `funded(X)` was ever collected for them; the difference `X - funded` is taken from underlying reserved for other pending claimants, breaking one-receipt-one-payout and solvency of the funded-claim pool. Last claimants' receipts then revert on transfer or pay less — permanent loss up to the size of the unfunded instant remainder.

### Likelihood Explanation
Requires a partially funded instant queue (reachable whenever the epoch-start cash sweep covers only part of `instantWithdrawClaimsByEpoch`, which the prefunded-reserve logic treats as a normal case) plus `allowInstantWithdraw` being enabled for claims. No privileged misbehavior needed.

### Recommendation
Track funded basis per instant request (e.g., a per-epoch funded price analogous to `lossRecoveryPriceByEpoch`, or cap each claim at the amount collected via `collectInstantWithdrawFunds` attributed pro-rata to `instantWithdrawClaimsByEpoch[epoch]`). In `claimInstantWithdrawRequest`, bound the payout to the funded portion of `instantWithdrawsRequests[_user]` rather than the aggregate, and revert or defer the unfunded remainder until funded or defaulted.

### Proof of Concept
A Foundry fork PoC should:
1. Deploy `IdleCDOEpochVariant` + `IdleCreditVault` with instant withdraws enabled (`setInstantWithdrawParams`).
2. User B deposits and submits a normal `requestWithdraw`; let it be funded via `collectWithdrawFunds` so strategy holds underlying earmarked for B.
3. Attacker A requests `requestInstantWithdraw(X)` during the allowed window.
4. `startEpoch`/funding moves only `F < X` to the strategy via `collectInstantWithdrawFunds(F)` (borrower underfunds within its honest repayment amount).
5. After the deadline, call `claimInstantWithdrawRequest` for A; assert A receives `X` while only `F` was funded, and B's subsequent `claimWithdrawRequest` reverts or underpays by `X - F`.

Caveat: I could not fully trace the CDO-side `allowInstantWithdraw`/`instantWithdrawDeadline` gating before `claimInstantWithdrawRequest` within the iteration budget; if the CDO blocks claims while `pendingInstantWithdraws != 0` for the caller's epoch, this vector is gated and the finding reduces to a latent accounting hazard rather than a live exploit.