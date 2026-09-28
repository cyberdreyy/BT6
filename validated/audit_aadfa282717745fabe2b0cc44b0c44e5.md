### Title
Closed-pool withdraw requests bypass epoch gating and `pendingWithdraws` accounting, letting a new requester drain the partially-funded loss reserve at par while haircut claimants are left unpaid - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
When the CDO pool is closed (`epochEndDate == 0`), `requestWithdraw` stops adding the new receipt to `pendingWithdraws` but still mints a full receipt and records it in `withdrawsRequests`, and `_claimFundedWithdrawRequest` skips the `epochNumber <= lastWithdrawRequest` maturity gate entirely. If the closing `stopEpoch` realized a loss (`collectWithdrawFunds` was called with `_amount < pendingBasis`), earlier receipts are only payable at `lossRecoveryPrice`, yet a brand-new request claims 1:1 from the same funded reserve. An attacker (any tranche holder) can sandwich the loss settlement: request after the lossy close, claim immediately, and consume underlyings that were meant to fund the haircut claims of existing withdrawers. This is the credit-vault analog of a partially-initialized context inheriting privileges: the new request enters the "funded claim" path without being registered in the funded pool's `pendingBasis`.

### Finding Description
Relevant code:

- `requestWithdraw` in `contracts/strategies/idle/IdleCreditVault.sol` (~L259-294): computes `isClosed = epochEndDate() == 0`; when closed it skips `pendingWithdraws += _amount` but still `_mint(_user, _amount)`, sets `lastWithdrawRequest[_user] = currentEpoch`, and (because `unscaledApr == 0 && !isClosed` is false in closed mode) does `withdrawsRequests[_user] += _amount`.
- `_claimFundedWithdrawRequest` (~L326-349): the maturity check `IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])` is short-circuited when `epochEndDate == 0`, so `amount = withdrawsRequests[_user]` is burned and paid via `_transferFundedClaim` immediately, at par.
- `collectWithdrawFunds` (~L411-430): on partial funding it zeroes `pendingWithdraws` and stores `lossRecoveryPriceByEpoch[epochNumber]`, so the strategy holds only `funded < pendingBasis` underlyings for all receipts of that epoch.
- `_claimLossAdjustedWithdrawRequest` (~L789-801) pays `claimBasis * lossRecoveryPrice / RECOVERY_FULL` out of the same strategy balance the closed-pool claim drains at par.

Sequence (closed pool, `stopEpoch` with `_lossAmount` during the closing stop):

1. Epoch N runs; users have pending receipts with `pendingBasis = P`. Borrower/manager closes the pool with a realized loss; `collectWithdrawFunds(P_f)` is called with `P_f < P`, storing `lossRecoveryPriceByEpoch[N] = P_f/P` and leaving `P_f` underlyings in the strategy. `epochEndDate` is set to 0.
2. Attacker holding tranche tokens calls `cdoEpoch.requestWithdraw(amount, tranche)`. Because `isClosed`, the strategy does not increment `pendingWithdraws`, yet records `withdrawsRequests[attacker] += _amount` and mints the receipt.
3. Attacker calls `claimWithdrawRequest()` in the same block. `_claimLossAdjustedWithdrawRequest` returns 0 for the attacker (no receipt in epoch N), then `_claimFundedWithdrawRequest` skips the epoch gate (`epochEndDate() == 0`) and pays `withdrawsRequests[attacker]` at par from the `P_f` reserve.
4. Honest epoch-N receipt holders calling `claimWithdrawRequest` get `claimBasis * lossRecoveryPrice`, and once the attacker has consumed enough of `P_f`, their `_transferFundedClaim` reverts on insufficient balance — a permanent shortfall equal to the attacker's at-par payout.

The guards do not stop it: `requestWithdraw` has no loss-adjusted-receipt guard applicable to the attacker (he had no epoch-N receipt), the closed-pool claim gate is intentionally skipped, and `lossRecoveryPrice` only applies to receipts recorded under `lastWithdrawRequest` in the loss epoch — which the attacker's post-close request is not.

### Impact Explanation
Direct theft plus insolvency of the loss reserve. The attacker converts tranche tokens (priced at the post-loss `virtualPrice`) into an immediate 1:1 underlying payout, while contemporaneous epoch-N withdrawers are locked to `lossRecoveryPrice < 1`. The reserve `P_f` is only enough to pay `Σ claimBasis_i * lossRecoveryPrice`; every unit the attacker extracts reduces what honest claimants receive, and the last claimants' `_transferFundedClaim` reverts permanently — up to `min(attacker tranche value, P_f)` stolen/insolvent.

### Likelihood Explanation
Requires (a) a closing `stopEpoch`/`stopEpochWithDuration` that realizes a loss on pending receipts, and (b) tranche tokens still held by an unprivileged user at close — both normal configurations. The attacker only needs to hold tranche tokens and send two transactions after the honest manager's lossy close; no privileged role is involved. The attacker's request basis is derived from the post-loss `virtualPrice`, so his receipts are minted cheaply in tranche terms but still paid at par against the underlying reserve.

### Recommendation
In `requestWithdraw`, when `isClosed` (or whenever `lossRecoveryPriceByEpoch[epochNumber]` is set / pending receipts are not fully funded), either:
- record the new request under `pendingWithdraws` and route its claim through the same epoch maturity + loss-adjusted path, or
- revert new withdraw requests while `pendingWithdraws`/`lossRecoveryPriceByEpoch`-backed claims remain unfunded, or
- cap closed-pool immediate claims by a per-request recovery price so post-close requesters cannot outrank haircutted epoch-N receipts.

Also make `_claimFundedWithdrawRequest` reserve-aware: deduct the paid amount from the funded remainder earmarked for `lossRecoveryPriceByEpoch` claims, so new claims cannot consume funds reserved for existing haircut receipts.

### Proof of Concept
Foundry fork PoC outline (against the existing `IdleCreditVault.t.sol` harness):

```solidity
// 1. LP deposits AA, requests withdraw in epoch 0 (pendingBasis P).
// 2. startEpoch(0); warp past epochEndDate.
// 3. manager stops/closing epoch with a loss: stopEpochWithDuration(_lossAmount > 0)
//    so collectWithdrawFunds is called with P_f < P and epochEndDate == 0.
// 4. attacker (fresh AA depositor from epoch 0 who did NOT request) calls
//    cdoEpoch.requestWithdraw(attackerTrancheBal, AATranche)   // no revert, pendingWithdraws unchanged
//    cdoEpoch.claimWithdrawRequest()                            // pays attacker at par from P_f
// 5. victim calls claimWithdrawRequest():
//    receives claimBasis * lossRecoveryPrice (or reverts once reserve < dues).
// assert: attackerReceived == attackerBasis (par) while
//         victimReceived < victimBasis, and reserve shortfall == attackerReceived.
```

Key assertions: `strategy.pendingWithdraws()` unchanged by step 4's request; attacker claim succeeds in the same block (epoch gate skipped via `epochEndDate() == 0`); final `underlying.balanceOf(strategy)` < `Σ victimBasis_i * lossRecoveryPrice`, proving the reserve was drained by a claim outside the loss-adjusted accounting.