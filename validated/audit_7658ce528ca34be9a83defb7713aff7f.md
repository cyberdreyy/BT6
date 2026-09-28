### Title
Post-default withdraw receipts are paid 1:1 from the shared `defaultRecoveryReserve`, depleting haircut-priced recovery owed to defaulted claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`keyget`'s prototype pollution lets an attacker write attacker-chosen keys into a shared object so later lookups return corrupted state. The analog in `IdleCreditVault` is the shared recovery bucket: after `finalizeDefaultRecovery` fixes `defaultRecoveryReserve` and `defaultRecoveryPrice`, post-default withdraw requests created through `requestWithdraw` write new claim "keys" (`postDefaultRequests[_user]`) into a payout pool (`_transferDefaultRecovery`) that was sized only for pre-default, haircut-priced claims. Each post-default claim consumes the reserve at par, shrinking it for every legit defaulted claimant that has not yet claimed.

### Finding Description
- `finalizeDefaultRecovery` computes `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` and `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis`, then stores `defaultRecoveryReserve = reserveAmount` (contracts/strategies/idle/IdleCreditVault.sol:686-692). The reserve is exactly sized so that all defaulted receipts (normal + instant) together receive `basis * recoveryPrice / RECOVERY_FULL`. Nothing is added for future claims.
- After finalization, `requestWithdraw` still accepts new requests: it mints a fresh strategy-token receipt to `_user` and records `postDefaultRequests[_user] = _amount` (lines 247-257). It deliberately does not increase `pendingWithdraws` and no underlying is added to `defaultRecoveryReserve`.
- On claim, `_claimPostDefaultWithdrawRequest` pays `postDefaultRequests[_user]` **1:1** out of `defaultRecoveryReserve` via `_transferDefaultRecovery` (lines 760-767, 912-917), the same reserve meant to pay defaulted claims only at `recoveryPrice` (< 1 in a real default).
- Consequence: `sum(defaulted claims) * recoveryPrice + sum(postDefault claims) > reserve`. Since `_transferDefaultRecovery` does `defaultRecoveryReserve -= _amount` and reverts on underflow, whichever defaulted claimants claim last can never claim — permanent freeze — and every post-default claimant extracts `(1 - recoveryPrice) * amount` more than their actuarial share. An attacker (any KYC-passing lender) can deposit after default, request a withdraw, and claim 1:1 ahead of defaulted claimants, directly profiting when the reserve has any surplus, or at minimum stranding other users' recovery.

### Impact Explanation
Direct theft / permanent freezing of unclaimed recovery. Each post-default receipt of size `A` removes `A` from a reserve that only owes `recoveryPrice * basis` to defaulted claimants; the excess `(1 - recoveryPrice) * A` is taken from victims' recovery, and trailing claimants revert permanently in `_transferDefaultRecovery`. Quantified loss = `sum(postDefault claims paid at par) * (1 - recoveryPrice)` plus stranded residual claims.

### Likelihood Explanation
Requires only that the vault is defaulted and finalized, and that an unprivileged user can still deposit/request withdraw post-default — a path explicitly preserved by the code ("Preserve request/claim UX after default", line 252). The attacker controls timing: claim immediately after request, before defaulted claimants. No privileged misbehavior needed; sequencing around the honest CDO/manager finalize calls suffices.

### Recommendation
Fund post-default receipts from their own backing, not `defaultRecoveryReserve`: either route post-default claims through `_transferFundedClaim` (with the reserve-exclusion check already present at lines 897-907) backed by the underlying that arrived with the post-default deposit, or track a separate `postDefaultReserve` incremented when the CDO funds those requests. Alternatively, block new `requestWithdraw` once `defaultRecoveryFinalized` until the reserve is fully claimed/distributed.

### Proof of Concept
Foundry fork sketch (sequence only; concrete harness to be built against the deployed vault + IdleCDOEpochVariant):

```solidity
// Pool running -> borrower defaults -> manager/guardian call finalizeDefaultRecovery
// reserve = R, recoveryPrice = p < RECOVERY_FULL, defaulted basis B with R = B*p

// Attacker (KYC'd lender) post-default:
cdo.deposit(underlyingAmt, AAorBB);            // tranches minted at post-haircut price
vault.requestWithdraw(amount, attacker, principal); // via CDO; postDefaultRequests[attacker]=amount
vault.claimWithdrawRequest(attacker);          // pays `amount` 1:1 from defaultRecoveryReserve

// Victim defaulted claimant later:
vault.claimWithdrawRequest(victim);            // _claimDefaultedWithdrawRequest -> _transferDefaultRecovery
// reverts (underflow) or pays less than basis*p once attacker drained (1-p)*amount extra
assertLt(victimReceived, victimBasis * defaultRecoveryPrice / RECOVERY_FULL);
```

Key assertions: `defaultRecoveryReserve` decreased by the attacker's full `amount` (line 915); sum of all defaulted claims now exceeds remaining reserve, so the last claimant's `_transferDefaultRecovery` underflows → permanent freeze.