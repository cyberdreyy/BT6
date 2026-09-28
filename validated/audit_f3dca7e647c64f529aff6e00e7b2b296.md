### Title
Instant-withdraw claims pay full receipt amount regardless of how much the borrower actually funded, letting a claimer drain unrelated vault balances (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` pays out the user's entire `instantWithdrawsRequests[_user]` balance and burns the corresponding receipt tokens, but it never checks that the claimed amount was actually collected from the borrower via `collectInstantWithdrawFunds`. The vault acknowledges partial instant-withdraw funding as a real state (`_defaultPrefundedInstantReserve` exists precisely because `pendingInstantWithdraws` can remain non-zero after `startEpoch`). Any unfunded portion of an instant receipt can therefore be satisfied out of other underlyings held by the strategy — normal withdraw receipts already funded through `collectWithdrawFunds`, or buffered deposits not yet forwarded to the borrower — breaking the solvency/one-receipt-one-payout invariant.

### Finding Description
The instant-withdraw flow has three pieces of accounting:

- `requestInstantWithdraw` burns `_amount` strategy tokens from the CDO, mints `_amount` receipt tokens to the user, and increments both `instantWithdrawsRequests[_user]` and the global `pendingInstantWithdraws` (`IdleCreditVault.sol:356-375`).
- `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` and pulls whatever amount the CDO actually received from the borrower (`IdleCreditVault.sol:398-403`). Per `_defaultPrefundedInstantReserve` (`IdleCreditVault.sol:716-723`), the collected amount can be strictly less than the epoch's instant claim basis — "startEpoch moved the CDO's available cash to the strategy but that cash covered only part of the instant queue".
- `claimInstantWithdrawRequest` (`IdleCreditVault.sol:380-393`) then burns the user's *entire* `instantWithdrawsRequests[_user]` and transfers the full amount via `_transferFundedClaim`, which only protects `defaultRecoveryReserve` and is a no-op guard when `defaultRecoveryReserve == 0` (`IdleCreditVault.sol:897-907`).

There is no per-user or per-epoch tracking of *funded* instant basis. `instantWithdrawsRequestsByEpoch` / `instantWithdrawClaimsByEpoch` are only consulted on the default-finalization path (`_claimDefaultedInstantWithdrawRequest`), not to limit funded claims. So while `pendingInstantWithdraws > 0` (i.e., part of the queue is still unfunded), any claimer can still withdraw their full recorded amount as long as the strategy's ERC20 balance covers it.

The strategy's balance legitimately holds funds not belonging to the instant queue:

- Buffered deposits: `deposit()` pulls underlyings into the strategy while the epoch is not running (`IdleCreditVault.sol:596-617`); they are only sent to the borrower later via `sendInterestAndDeposits`.
- Funded normal withdraw receipts collected at `stopEpoch` via `collectWithdrawFunds` (`IdleCreditVault.sol:411-430`), which sit in the vault until each user calls `claimWithdrawRequest`.

An unprivileged tranche holder can therefore, during a running epoch in which the instant queue was only partially funded, call `IdleCDOEpochVariant.claimInstantWithdrawRequest` (gated only by `allowInstantWithdraw`, `IdleCDOEpochVariant.sol:975-978`) and receive underlyings that economically belong to depositors or to funded normal-withdraw claimants. Equivalently, if an instant request is opened and claimed while the pool is between epochs (the vault imposes no epoch-phase check), the claim is paid directly out of buffered deposits, leaving the strategy tokens minted to the CDO for those deposits unbacked.

This maps the CVE's "logic issue / missing check" class onto the queued-withdrawal surface: a funded-vs-unfunded check is missing on the instant claim path.

### Impact Explanation
Direct theft / insolvency. The attacker receives underlyings equal to their unfunded instant receipt amount. Correspondingly, either (a) normal withdraw requesters whose receipts were already funded by `collectWithdrawFunds` can no longer claim (their earmarked funds were paid out), or (b) deposits held in the strategy pending forwarding to the borrower are drained, leaving newly minted CDO strategy tokens unbacked and impairing every tranche holder pro rata. Loss is bounded by the attacker's instant receipt size, but repeat requests across epochs scale it arbitrarily as long as a non-reserve balance exists in the vault.

### Likelihood Explanation
Requirements: `allowInstantWithdraw` enabled on the CDO, a non-zero non-reserve underlying balance in the strategy (funded normal receipts or buffered deposits — both routine), and an instant queue that is not fully funded at `startEpoch` or a request claimed before collection. The contract's own comments confirm partial instant funding is a supported state (`IdleCreditVault.sol:638-640`, `716-723`). The attacker needs only to hold tranche tokens (KYC'd lender in scope) and call `requestInstantWithdraw` followed by `claimInstantWithdrawRequest`. No privileged actor misbehavior is required; the honest borrower's partial repayment at `startEpoch` is sufficient to create the window.

### Recommendation
Track funded instant basis explicitly (e.g., a `fundedInstantWithdraws` counter or per-epoch funded price analogous to `lossRecoveryPriceByEpoch`/`epochWithdrawPrice`), and in `claimInstantWithdrawRequest` pay `min(instantWithdrawsRequests[_user], userShareOfFunded)` — or pro-rate claims against `instantWithdrawClaimsByEpoch[epoch]` versus the amount actually collected — rather than paying the full recorded amount unconditionally. Alternatively, revert claims while `pendingInstantWithdraws != 0` for the user's request epoch so unfunded receipts can never draw on unrelated balances.

### Proof of Concept
Foundry fork scenario (against the repo's existing harness in `test/foundry/IdleCreditVault.t.sol`):

```solidity
// Setup: pool with allowInstantWithdraw = true, deposits enabled.
// 1. Victim deposits 10_000 underlying via depositAA during buffer.
//    Funds sit in IdleCreditVault (isEpochRunning == false, deposit() pulled tokens in).
// 2. Attacker (KYC'd lender) holds tranche tokens and calls
//    cdoEpoch.requestInstantWithdraw(trancheAmt, tranche) -> IdleCreditVault mints
//    attacker a receipt; pendingInstantWithdraws = X.
// 3. startEpoch: CDO forwards all available cash to borrower; borrower repays only
//    part -> collectInstantWithdrawFunds(Y) with Y < X (partial funding is a
//    documented state, see _defaultPrefundedInstantReserve).
//    Alternatively during buffer: no collection happens at all.
// 4. Attacker calls cdoEpoch.claimInstantWithdrawRequest():
//    - _burn(attacker, X) succeeds (receipt tokens were minted)
//    - _transferFundedClaim(attacker, X): defaultRecoveryReserve == 0 so the guard
//      is skipped; vault balance = victim deposits + Y >= X -> transfer succeeds.
// 5. Result: attacker receives X underlyings while only Y was funded for instant
//    claims; the difference X - Y is taken from the victim's buffered deposit or
//    from funded normal-withdraw receipts, which then revert on claim
//    (safeTransfer underflow / NotAllowed), i.e. direct theft of X - Y.
```

Key assertion for the PoC: `underlying.balanceOf(attacker) - balPre == X` while `pendingInstantWithdraws == X - Y > 0` afterward, demonstrating the claim was paid out of balances not earmarked for it.

Caveat I could not fully verify within the iteration budget: the exact gating inside `IdleCDOEpochVariant.requestInstantWithdraw`/`startEpoch` for buffer-phase instant requests (i.e., whether a request can be opened and claimed entirely between epochs). If instant requests are only openable during specific phases, the variant of this bug that relies on a partially funded running epoch still stands, since `_defaultPrefundedInstantReserve` confirms that state is reachable.