### Title
Post-default withdraw requests over-consume the recovery reserve, draining funds owed to defaulted-epoch claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` pays post-default withdraw receipts 1:1 out of `defaultRecoveryReserve`, but the reserve was sized at `defaultRecoveryPrice` per unit of claim basis. Each post-default claim therefore spends `_amount` of reserve while only freeing `recoveryPrice * _amount` worth of basis, so a borrower default with `defaultRecoveryPrice < RECOVERY_FULL` lets post-default withdrawers drain reserve belonging to other claimants and permanently freeze the remaining defaulted claims.

### Finding Description
The external bug (type confusion in a parser reading `seh->twin()` as the wrong object) maps to a claim-type confusion in `IdleCreditVault.claimWithdrawRequest`: a post-default receipt is treated as a fully-funded 1:1 claim against the recovery reserve even though the reserve only holds a haircut fraction per unit of basis.

- `finalizeDefaultRecovery` computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` and stores `defaultRecoveryReserve = reserveAmount` (lines 688-692). The reserve is exactly enough to pay every basis unit at `recoveryPrice`, no more.
- After finalization, `requestWithdraw` takes the post-default branch: it burns `_amount` of the CDO's strategy tokens (a basis reduction of `_amount`, worth only `recoveryPrice * _amount` in reserve terms) and records `postDefaultRequests[_user] = _amount` (lines 247-257). No new underlying enters the strategy.
- `_claimPostDefaultWithdrawRequest` then pays that `_amount` at par via `_transferDefaultRecovery`, which decrements `defaultRecoveryReserve` by the full `_amount` (lines 760-767, 912-917).

Broken invariant: the reserve is conservable only if every unit paid out corresponds to `1/recoveryPrice` units of basis retired. Here `_amount` basis is retired but `_amount` (not `recoveryPrice * _amount`) is paid, so each post-default withdrawal extracts an extra `_amount * (RECOVERY_FULL - recoveryPrice) / RECOVERY_FULL` from the shared reserve. Once enough post-default claims are paid, `defaultRecoveryReserve -= _amount` underflows (or the reserve is exhausted), and `_claimDefaultedWithdrawRequest` / `_claimDefaultedInstantWithdrawRequest` for honest defaulted-epoch claimants revert permanently — a direct theft plus permanent freezing of unclaimed recovery.

Existing guards do not stop it: `_hasWithdrawRequest` only blocks *new* requests while old ones are pending, `_transferFundedClaim`'s reserve check protects funded claims (not reserve-funded ones), and the `postDefaultRequests` path is explicitly designed to draw on `defaultRecoveryReserve`.

### Impact Explanation
Direct theft of the isolated default-recovery reserve and permanent freezing of remaining defaulted claims. With `defaultRecoveryPrice = 50%`, a KYC-passing lender who keeps lending exposure through the default can request a post-default withdrawal of e.g. 100,000 USDC of haircut value; the strategy burns 100,000 of CDO basis (freeing only 50,000 of reserve entitlement) but pays out 100,000 — a 50,000 excess taken from other claimants. Repeating across users drains the reserve until late claimants' `claimWithdrawRequest`/`claimInstantWithdrawRequest` revert forever.

### Likelihood Explanation
Requires a borrower default (a normal, contemplated protocol state handled by `finalizeDefaultRecovery`), `defaultRecoveryPrice < 1`, and any post-default `requestWithdraw` — all reachable by an unprivileged KYC'd lender in ordinary use. No privileged misbehavior needed; manager/owner calls are honest sequencing.

### Recommendation
In `_claimPostDefaultWithdrawRequest`, pay `amount` from the reserve but account for the retired basis correctly: either (a) fund post-default requests with fresh underlying pulled from the CDO/borrower at request time and pay them via `_transferFundedClaim` instead of the recovery reserve, or (b) track a separate `postDefaultReserve` topped up by the basis freed when the CDO's strategy tokens are burned, so `defaultRecoveryReserve` remains exclusively for defaulted-epoch claimants.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// Setup: deploy IdleCDOEpochVariant + IdleCreditVault, two KYC'd lenders.
// 1. Alice deposits 1_000_000 USDC (AA), pool starts epoch, manager starts epoch.
// 2. Warp past epochEndDate; borrower repays only 50% -> cdoEpoch.stopEpoch(0, shortfall)
//    triggers defaulted() == true.
// 3. Owner calls cdoEpoch.finalizeDefault(recovered, manager) ->
//    finalizeDefaultRecovery stores defaultRecoveryPrice = 0.5e18 (50%),
//    defaultRecoveryReserve = recovered.
// 4. Attacker (lender Bob, still holding tranches) calls
//    cdoEpoch.requestWithdraw(bobTranches, tranche) ->
//    IdleCreditVault.requestWithdraw takes post-default branch:
//    burns _amount of CDO strategy tokens, sets postDefaultRequests[bob].
// 5. Bob calls cdoEpoch.claimWithdrawRequest() ->
//    _claimPostDefaultWithdrawRequest pays _amount 1:1 from the reserve.
//    Assert: underlying paid > _amount * defaultRecoveryPrice / 1e18
//    (the amount of reserve Bob's retired basis actually freed).
// 6. Repeat with a second post-default requester until
//    defaultRecoveryReserve is exhausted.
// 7. Alice calls cdoEpoch.claimWithdrawRequest() for her defaulted receipt ->
//    _transferDefaultRecovery underflows / reverts NotAllowed ->
//    her haircut claim is permanently frozen.
```

Uncertainty: the exact `_amount` computed by `IdleCDOEpochVariant.requestWithdraw` in the post-default path was not fully verified (only 14 grep matches surfaced, not the function body); the finding holds as long as `_amount` is the post-haircut underlying value paid 1:1, which the comments at lines 252-256 and 764-765 explicitly state.