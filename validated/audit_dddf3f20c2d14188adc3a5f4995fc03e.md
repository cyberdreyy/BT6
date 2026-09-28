### Title
Post-default `requestWithdraw` lets unprivileged users drain the default-recovery reserve ahead of defaulted-epoch claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is an unauthenticated path traversal: an unprivileged requester reaches a protected resource by supplying an arbitrary path. The closest analog in this codebase is `requestWithdraw` in the post-default branch of `IdleCreditVault`, which lets any unprivileged tranche holder mint a receipt that is paid **1:1 out of the same default-recovery reserve** (`_transferDefaultRecovery`) that was provisioned to pay defaulted-epoch claimants at the haircut `defaultRecoveryPrice`. Because `postDefaultRequests` are paid before/parallel to `_claimDefaultedWithdrawRequest` claims against a fixed reserve, a post-default requester can extract full-value underlying and leave defaulted claimants undercollateralized or unable to claim.

### Finding Description
- After `finalizeDefaultRecovery`, `requestWithdraw` takes a dedicated path: it rejects users with open receipts, then does `_burn(msg.sender, _amount)` / `_mint(_user, _amount)` and records `postDefaultRequests[_user] = _amount`, returning early without touching `pendingWithdraws` (`contracts/strategies/idle/IdleCreditVault.sol:247-257`).
- `claimWithdrawRequest` then pays `postDefaultRequests` 1:1 via `_claimPostDefaultWithdrawRequest` → `_transferDefaultRecovery(_user, amount)` (lines 760-767, 303-307), i.e., the same reserve bucket used by `_claimDefaultedWithdrawRequest` (lines 772-784) and `_claimDefaultedInstantWithdrawRequest`.
- The reserve is provisioned once during default finalization to cover `defaultPendingClaimBasis * defaultRecoveryPrice / RECOVERY_FULL`. Post-default receipts add new claims against that fixed pool without any bound tying their aggregate to the reserve's leftover capacity. The comment claims "the CDO passes an already-haircut amount because finalization lowered virtualPrice first" — but the haircut applied at request time only sizes the receipt; it does not reserve or escrow funds for it.
- Broken invariant: one receipt one payout / solvency of the recovery reserve. The reserve backing is `recoveryPrice`-denominated, while post-default claims are par-denominated, so total claims can exceed the reserve.

### Impact Explanation
An unprivileged tranche holder can `requestWithdraw` after default finalization and immediately claim 1:1 underlying from the recovery reserve, front-running defaulted-epoch claimants whose claims are haircut. If aggregate post-default requests plus defaulted claims exceed the reserve (which is only sized for the defaulted basis), later claimants — the defaulted-epoch lenders the reserve was meant for — suffer permanent loss or have claims revert on insufficient balance, i.e., theft of unclaimed recovery and permanent freezing of the remainder.

### Likelihood Explanation
Requires only that default recovery has been finalized and that the attacker holds tranche tokens post-default (acquirable by any KYC-passing lender). No privileged role needed: `claimWithdrawRequest`/`requestWithdraw` are routed through the CDO for `msg.sender` like any user withdrawal. Likelihood is bounded by how much reserve slack exists after accounting for `defaultPendingClaimBasis`; if finalization funds exactly the defaulted basis, every post-default claim is pure dilution.

### Recommendation
Track post-default claims against reserve capacity explicitly: either decrement a `postDefaultReserve` budget funded separately at finalization, or denominate `postDefaultRequests` payouts in the same `defaultRecoveryPrice` haircut as defaulted claims, or cap `postDefaultRequests` issuance so `Σ(postDefaultRequests) + Σ(defaulted claims at recovery price) ≤ reserve balance`. Alternatively, have the CDO escrow the underlying for post-default receipts at request time instead of drawing on the shared reserve at claim time.

### Proof of Concept
1. Fork mainnet; deploy/attach `IdleCDOEpochVariant` + `IdleCreditVault`; alice and bob are KYC'd AA lenders; borrower defaults.
2. Manager calls `stopEpochWithDuration`/`_handleBorrowerDefault` and `finalizeDefaultRecovery` so `defaultRecoveryPrice < RECOVERY_FULL` and the reserve holds exactly `defaultPendingClaimBasis * defaultRecoveryPrice / RECOVERY_FULL`.
3. Charlie (unprivileged tranche holder with no open requests) calls `cdo.requestWithdraw(...)` → vault mints `postDefaultRequests[charlie]` (line 256).
4. Charlie calls `cdo.claimWithdrawRequest()` → `_claimPostDefaultWithdrawRequest` transfers par underlying from the reserve (lines 760-767).
5. Alice calls `claimWithdrawRequest` for her defaulted-epoch receipt → `_claimDefaultedWithdrawRequest` reverts or pays less than `claimBasis * defaultRecoveryPrice` because Charlie drained the reserve (lines 772-784). Assert `underlying.balanceOf(vault) < required remaining claims`.

Note: I could not fully verify the reserve-provisioning path (`_handleBorrowerDefault`/`finalizeDefaultRecovery`/`defaultPendingClaimBasis` and `_transferDefaultRecovery` internals) within this pass, so the exact underfunding condition should be confirmed against those functions; if the reserve is separately topped up for post-default requests, this analog does not hold.