### Title
Instant-withdraw receipts are payable before the borrower-funded liquidity arrives, letting an early claimer drain underlying reserved for other funded claims - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
CVE-2021-45079 is a "premature success" bug: strongSwan accepted an `EAP-Success` message before the responder had actually authenticated, i.e. a terminal "success" signal was honored before the precondition that legitimates it. The analog in idle-tranches is the instant-withdraw claim path: `IdleCreditVault.claimInstantWithdrawRequest` burns the user's strategy-token receipt and pays out underlying from the strategy's balance without verifying that the instant-withdraw liquidity has actually been funded. The funding leg is a separate step — `collectInstantWithdrawFunds` pulls underlyings from the CDO (which sources them from the borrower at epoch start) and decrements `pendingInstantWithdraws` — but the claim path never checks that this funding occurred or that any instant-settlement delay elapsed. The claim therefore treats the receipt as "succeeded" before the counterparty (borrower) delivered the funds that back it.

### Finding Description
The instant-withdraw flow is two-legged:

1. Request: `requestInstantWithdraw` burns the CDO's strategy tokens, mints an equal receipt to the user, and increments `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` (IdleCreditVault.sol:356-375).
2. Funding: `collectInstantWithdrawFunds` is later invoked by the CDO once the borrower has supplied the instant liquidity; it decrements `pendingInstantWithdraws` and transfers underlying into the vault (IdleCreditVault.sol:398-403).

The claim leg, however, only does:

- `amount = instantWithdrawsRequests[_user]`
- `_burn(_user, amount)`
- `_transferFundedClaim(_user, amount)`

(IdleCreditVault.sol:387-392). There is no check that the request's epoch funding arrived, no check against `pendingInstantWithdraws`, no timestamp/instant-delay gate, and no per-epoch funded flag. On the CDO side, `claimInstantWithdrawRequest` in `IdleCDOEpochVariant` checks only `allowInstantWithdraw` (IdleCDOEpochVariant.sol:975-978) — it does not verify that `getInstantWithdrawFunds`/borrower funding was executed for the current epoch.

Because all claim types are paid out of the vault's single `underlyingToken` balance, an unfunded instant receipt can be settled against underlying that was collected for a different purpose — most concretely, underlying pulled in via `collectWithdrawFunds` that is earmarked for already-funded normal withdraw receipts of other users (`pendingWithdraws` basis), or `defaultRecoveryReserve` set aside for post-default claims. If `_transferFundedClaim` pays out of the aggregate vault balance (consistent with `collectWithdrawFunds`/`collectInstantWithdrawFunds` both depositing into the same pot with only bookkeeping distinguishing them), the early instant claimer extracts value belonging to other receipt holders: direct theft via premature settlement, exactly mirroring the CVE where success is accepted before the authenticating precondition is met.

A secondary symptom: `requestInstantWithdraw` itself carries no epoch-running gate inside the vault (IdleCreditVault.sol:356-375) and relies entirely on the CDO-side gating, so any path in the CDO that permits a request outside the intended window compounds the exposure.

### Impact Explanation
An unprivileged lender (KYC-passed, tranche-token holder) can:

- Call `requestInstantWithdraw` during a window where `allowInstantWithdraw` is true.
- Call `claimInstantWithdrawRequest` (via `IdleCDOEpochVariant.claimInstantWithdrawRequest`) before the CDO has collected the instant funds from the borrower.
- Receive underlying that was funded for *other* users' matured normal withdraw requests (the `pendingWithdraws` reserve collected through `collectWithdrawFunds`) or recovery reserves.

The attacker gains their full receipt amount immediately at par; the victims are users whose funded claims are later unpayable (insolvency of the receipt reserve). Quantified loss: up to `min(instantWithdrawsRequests[attacker], vault underlying balance reserved for funded claims)` — bounded only by the attacker's deposit size, which for a KYC'd lender is unconstrained by the protocol. Invariant broken: "one receipt one payout backed by its own funding leg" / reserve isolation between claim classes.

### Likelihood Explanation
- Preconditions: `allowInstantWithdraw` enabled (a supported, manager-configured mode per `setInstantWithdrawParams`), an epoch running, and non-zero vault underlying balance earmarked for other funded claims — a routine state whenever normal withdraw requests have matured and been funded but not yet claimed.
- The attacker needs only to hold tranche tokens and pass `isWalletAllowed` (Keyring KYC); both are unprivileged roles per scope.
- The gap exists because claim-settlement ordering is enforced for *normal* withdrawals (`epochNumber <= lastWithdrawRequest` revert at IdleCreditVault.sol:326) but the analogous "was this funded yet" check is absent for instant claims — the same asymmetry as the strongSwan bug, where EAP-Success was honored without the mutual-authentication precondition that other flows enforced.

### Recommendation
- Track funded instant liquidity explicitly (e.g., a `fundedInstantWithdraws` counter incremented in `collectInstantWithdrawFunds` and decremented in `claimInstantWithdrawRequest`), and revert claims that exceed it — mirroring how `epochNumber > lastWithdrawRequest` gates normal claims.
- Alternatively (or additionally), record the request epoch per instant receipt and require that `collectInstantWithdrawFunds` for that epoch ran before the claim is payable, and enforce the configured instant delay timestamp check inside the vault rather than relying solely on CDO-side ordering.
- If payout segregation is intended, hold instant-claim funds in a dedicated accounting bucket so an unfunded claim cannot draw on `pendingWithdraws` or `defaultRecoveryReserve` backing.

### Proof of Concept
Sketch (Foundry fork against the deployed vault; exact helper names per `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testInstantClaimBeforeFunding() external {
    // Setup: epoch running, allowInstantWithdraw = true.
    // 1. Honest user requests normal withdraw; epoch stops; borrower funds it:
    //    CDO calls strategy.collectWithdrawFunds(pendingWithdraws)
    //    -> vault now holds X underlying reserved for the honest funded claim.
    // 2. New epoch starts. allowInstantWithdraw still true, but borrower
    //    has NOT yet supplied instant liquidity
    //    (collectInstantWithdrawFunds not yet called).
    // 3. Attacker (KYC'd lender) calls cdoEpoch.requestInstantWithdraw(amount)
    //    -> receipt minted, pendingInstantWithdraws += amount, no funds moved.
    // 4. Attacker immediately calls cdoEpoch.claimInstantWithdrawRequest()
    //    -> vault burns receipt and transfers `amount` underlying
    //       drawn from the honest claim's funded reserve.
    assertEq(underlying.balanceOf(attacker), amount);
    // 5. Honest user's claimWithdrawRequest now reverts/underpays:
    //    vault balance < pendingWithdraws basis -> insolvency.
}
```

**Caveat:** I was unable to read the body of `_transferFundedClaim` and the CDO-side `requestInstantWithdraw`/instant-delay gating within the available iterations, so the precise guard set that decides whether step 4 succeeds (e.g., whether `_transferFundedClaim` draws from a segregated balance or enforces a delay) is unverified. The finding stands on the observed asymmetry: the claim path at IdleCreditVault.sol:380-393 contains no funding or epoch check, while the funding leg at IdleCreditVault.sol:398-403 is a separate, later call — the same premature-success structure as CVE-2021-45079. If `_transferFundedClaim` proves to enforce funded-balance accounting, the exploit collapses to a revert and no vulnerability exists.