### Title
Unfunded instant-withdraw receipts from prior epochs escape the default haircut and are paid at par - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Analogous to malcontent dropping failed nested archives from scan inputs, `defaultPendingClaimBasis` drops still-unfunded instant-withdraw receipts that were created in *earlier* epochs from the default-recovery claim basis. Only `instantWithdrawClaimsByEpoch[epochNumber]` (the default epoch) is included. Those dropped receipts remain fully claimable: `claimInstantWithdrawRequest` pays the whole `instantWithdrawsRequests[_user]` balance at par through `_transferFundedClaim`, even though no recovery reserve or haircut was provisioned for them.

### Finding Description
At `finalizeDefaultRecovery` the aggregate claim basis is `pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` (only when `pendingInstantWithdraws != 0`). `pendingInstantWithdraws` is a global bucket that can contain unfunded instant requests spanning multiple epochs — `collectInstantWithdrawFunds` only decreases it by what the CDO actually funds, and `startEpoch` may fund partially or not at all (`IdleCDOEpochVariant.sol:279-290`). Meanwhile `instantWithdrawClaimsByEpoch` is tracked per epoch. When the borrower defaults, the basis includes only the *current* epoch's instant claims; older unfunded instant receipts are excluded from `totalBasis`, so `recoveryPrice` is computed as if they did not exist.

On the claim side, `_claimDefaultedInstantWithdrawRequest` only clears `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` and decrements `instantWithdrawsRequests[user]` by that epoch's basis. The leftover balance — the prior-epoch unfunded receipts — then flows into the generic funded path at `IdleCreditVault.sol:387-392`, which burns the full `instantWithdrawsRequests[_user]` and pays `_amount` at 100% via `_transferFundedClaim`, whose only guard is not touching `defaultRecoveryReserve`.

So a claimant whose instant request was unfunded from a previous epoch (entirely normal — just unlucky timing when the CDO was short of cash) is paid par from the strategy's underlying balance, while active LPs and default-epoch claimants absorb a `recoveryPrice` haircut.

### Impact Explanation
Direct theft of recovery value / insolvency. The reserve `defaultRecoveryReserve = _recoveredAmount + prefundedReserve` is sized only for `totalBasis` claims. An old-epoch unfunded instant receipt of size `X` is paid `X` at par even though (a) it was counted as part of `pendingInstantWithdraws` — i.e., explicitly unfunded — and (b) it consumed none of the basis the reserve was sized against. The payment comes out of underlying that backs the active-LP/post-default recovery, so either earlier par claimants drain the strategy and later claimants (including defaulted-epoch receipt holders expecting `claimBasis * defaultRecoveryPrice`) get nothing, or the strategy is left insolvent by up to the full haircut value on dropped receipts (`X * (1 - defaultRecoveryPrice)` plus the par-vs-basis mismatch). Broken invariant: loss waterfall — all unfunded receipts must share `defaultRecoveryPrice`; these escape it entirely.

### Likelihood Explanation
Requires: (1) an instant withdraw request that remains unfunded at `startEpoch` because CDO cash is insufficient (`pendingInstant > totUnderlyings` path leaves `pendingInstantWithdraws > 0` carrying the old receipt into a new epoch), (2) the borrower then defaults or `stopEpochWithDuration` repayment fails in a later epoch so `finalizeDefaultRecovery` runs. Both steps are reachable by honest manager/owner sequencing around an unprivileged requester; the attacker only needs to hold the stale instant receipt and call `claimInstantWithdrawRequest` via the CDO after finalization. No privileged collusion needed.

### Recommendation
In `defaultPendingClaimBasis`, when `pendingInstantWithdraws != 0` include *all* unfunded instant claim basis, not just the current epoch's — e.g. track a global unfunded instant basis or sum `instantWithdrawClaimsByEpoch` across outstanding epochs. Symmetrically, `_claimDefaultedInstantWithdrawRequest` should clear every prior-epoch unfunded instant receipt at `defaultRecoveryPrice` (iterate `instantWithdrawsRequestsByEpoch` or store per-user unfunded basis), and the par funded path must only pay receipts that were actually funded via `collectInstantWithdrawFunds`.

### Proof of Concept
Foundry fork PoC outline:

1. Deploy `IdleCreditVault` + `IdleCDOEpochVariant` with instant withdraws enabled; deposit into AA/BB; `startEpoch()` with borrower pulling funds.
2. User requests `requestInstantWithdraw(X)`. Before next `startEpoch`, arrange CDO cash < pendingInstant (e.g., all funds are with borrower) so `startEpoch` collects only part/none: `pendingInstantWithdraws = X - funded` carries into epoch N+1.
3. Warp past `epochEndDate`; make borrower repayment to `stopEpoch` revert (deplete borrower balance/approval) → `_handleBorrowerDefault` fires.
4. Call `finalizeDefaultRecovery(recovered, source)` with partial recovery → `recoveryPrice < RECOVERY_FULL`, `defaultInstantWithdrawsFinalized = true`. Observe `defaultPendingClaimBasis` excludes the epoch-N instant basis (`instantWithdrawClaimsByEpoch[N]` never added; only `epochNumber = N+1` bucket counted).
5. Call `claimInstantWithdrawRequest(user)` via CDO: `_claimDefaultedInstantWithdrawRequest` clears 0 (nothing in epoch N+1 bucket for user), then burns full `instantWithdrawsRequests[user] = X` and transfers `X` at par from strategy balance.
6. Assert: user received `X > X * defaultRecoveryPrice / RECOVERY_FULL`; subsequent defaulted-epoch claimants or active LPs recover less than `recoveryPrice`-proportional share (reserve drained / transfer reverts on insufficient balance).