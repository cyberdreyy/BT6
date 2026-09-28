### Title
Closed-pool withdraw request mints interest-bearing receipts without epoch gating, letting a user claim unbacked funds - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Analogous to CVE-2018-10930 (an authenticated write that lands outside its intended scope), `IdleCreditVault.requestWithdraw` has a closed-pool path where a user's write escapes the pending-withdrawal accounting scope: when `epochEndDate == 0`, the vault mints the user a receipt for `_amount` (principal + interest − fees) while burning only `_principal` from the CDO, and skips `pendingWithdraws` accounting entirely. Because `_claimFundedWithdrawRequest` also skips the one-epoch waiting check when the pool is closed, the same user can immediately claim the inflated receipt against underlying held by the vault.

### Finding Description
In `requestWithdraw` (`IdleCreditVault.sol:243-295`):

```solidity
bool isClosed = IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0;
...
_burn(msg.sender, _principal);   // burns only principal from CDO
_mint(_user, _amount);           // mints principal + interest - fees to user
if (!isClosed) {
  pendingWithdraws += _amount;   // closed pool: receipt escapes pending accounting
}
lastWithdrawRequest[_user] = currentEpoch;
```

`_amount` comes from the CDO (`IdleCDOEpochVariant.requestWithdraw`, line ~776): `principal + interest - totalFees`, where `interest` is computed by `_calcInterestWithdrawRequest`. Only `_principal` of strategy-token backing is burned, so `interest - fees` of receipt is minted with no corresponding liability tracked anywhere — a write outside the volume's accounting scope.

Then in `_claimFundedWithdrawRequest` (`IdleCreditVault.sol:326-328`):

```solidity
if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
  revert NotAllowed();
}
```

The epoch-wait guard is conditional on the pool *not* being closed. With `epochEndDate == 0` the check is bypassed, so `withdrawsRequests[_user]` (credited with the full `_amount` at line 292-293, since the `unscaledApr == 0 && !isClosed` branch is skipped when `isClosed`) is immediately payable via `_transferFundedClaim`.

### Impact Explanation
A KYC'd tranche holder (`isWalletAllowed` + `allowAAWithdrawRequest`/`allowBBWithdrawRequest` are the only request-side gates) calls `IdleCDOEpochVariant.requestWithdraw` after the pool is closed and immediately calls `claimWithdrawRequest`, receiving `principal + interest - fees` in underlying while only `principal` of strategy-token backing was burned. The `interest - fees` portion is paid out of underlying held by the vault for other funded claims/holders — a direct theft/dilution equal to the phantom interest component. The "one receipt one payout" and solvency invariants break: receipt supply exceeds burned backing, and `pendingWithdraws` never recorded the liability so no funding was ever sourced from the borrower for it.

### Likelihood Explanation
Requires the pool to be in the closed state (`epochEndDate == 0`, reached via the stop-epoch "repay all" sentinel path), `unscaledApr != 0` so the request takes the normal `withdrawsRequests` branch, and the vault to still hold underlying backing outstanding claims. All attacker preconditions are unprivileged: any allowed wallet holding tranche tokens. No privileged-role misbehavior is needed; the guards that exist (`_onlyIdleCDO`, `_checkTranche`, wallet allowlist, loss-recovery claim-first check) do not cover this path — the loss-recovery check at lines 263-271 only fires when `lossRecoveryPriceByEpoch[lastWithdrawRequest]` is set.

### Recommendation
In `requestWithdraw`, when `isClosed` is true, either revert outright (a closed pool should not accept new withdrawal requests) or mint/record only `_principal` and route the claim through the same funded-accounting path. At minimum, do not credit `withdrawsRequests[_user]` with the interest component when no `pendingWithdraws` liability is tracked, and do not bypass the epoch-wait check in `_claimFundedWithdrawRequest` for requests created after close.

### Proof of Concept
Foundry fork PoC outline (not executed in this environment — the exact behavior of `_calcInterestWithdrawRequest` in closed-pool state and the vault's residual underlying balance at close should be confirmed on a mainnet fork):

```solidity
// setup: vault with unscaledApr > 0, user holds AA tranches
// 1. manager stops epoch with _interest == 1 (repay-all sentinel) -> epochEndDate = 0
// 2. user: cdoEpoch.requestWithdraw(trancheBal, AATranche)
//    -> vault burns principal, mints principal+interest-fees, pendingWithdraws unchanged
// 3. user: cdoEpoch.claimWithdrawRequest()
//    -> epochEndDate==0 skips epoch wait; _transferFundedClaim pays full amount
// assert userReceivedUnderlying > principal entitlement
```

Caveat: if in closed-pool state `_calcInterestWithdrawRequest` returns 0 or the vault holds no underlying after recall, the extractable amount collapses to zero and this reduces to a no-op; that dependency is the main unverified point and should be confirmed before treating the issue as exploitable.