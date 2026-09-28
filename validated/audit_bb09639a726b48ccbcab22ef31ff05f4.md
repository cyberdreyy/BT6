### Title
Unfunded instant-withdraw receipts are paid from other users' funded claim reserves - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The kernel bug iterated heterogeneous `tc_u_hnode`/`tc_u_knode` objects and blindly treated every entry as a `tc_u_knode`, skipping the subtype check. `IdleCreditVault.claimInstantWithdrawRequest` does the same with receipt subtypes: it treats every entry in `instantWithdrawsRequests[_user]` as a *funded* instant receipt and pays it out of the strategy's underlying balance, even though the bucket mixes already-funded instant receipts with not-yet-funded ones (`pendingInstantWithdraws` is the unfunded remainder that the borrower only covers via `collectInstantWithdrawFunds` at `startEpoch`). There is no discriminant check equivalent to `TC_U32_KEY(handle)` that distinguishes "funded" from "pending" instant receipts before paying.

### Finding Description
In `requestInstantWithdraw` (`IdleCreditVault.sol:356-375`), the CDO's strategy tokens are burned, the user is minted a receipt, and both `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` are increased. The receipt is only backed by underlying once the borrower funds it and the CDO calls `collectInstantWithdrawFunds`, which decrements `pendingInstantWithdraws` and pulls underlying into the strategy (`:398-403`).

`claimInstantWithdrawRequest` (`:380-393`) pays `instantWithdrawsRequests[_user]` in full via `_transferFundedClaim` (`:897-907`), whose only guard is that the payout must not dip below `defaultRecoveryReserve`. It never checks `pendingInstantWithdraws` or `instantWithdrawClaimsByEpoch[epochNumber]` to verify that this receipt's epoch was actually funded. So whenever the strategy holds underlying destined for other claimants — funded normal withdraw requests collected via `collectWithdrawFunds` (`:411-430`), or already-funded instant receipts of other users — an unfunded instant receipt can spend it.

The CDO-side entry point `IdleCDOEpochVariant.claimInstantWithdrawRequest` (`contracts/IdleCDOEpochVariant.sol:975-979`) gates only on `allowInstantWithdraw`; it does not verify that the request's epoch has been funded.

Additionally, `claimInstantWithdrawRequest` never decrements `pendingInstantWithdraws` even for legitimately funded claims, leaving the unfunded counter permanently overstated; on a subsequent `collectInstantWithdrawFunds` the subtraction `pendingInstantWithdraws -= _amount` can underflow and revert, blocking funding of other users' instant receipts.

### Impact Explanation
An unprivileged lender can steal funded withdrawal reserves. During a running/buffer phase, after the strategy has collected underlying for pending normal withdraw requests (borrower-funded via `stopEpoch` → `collectWithdrawFunds`), the attacker requests an instant withdraw of the same magnitude and immediately calls `claimWithdrawRequest`→`claimInstantWithdrawRequest`. `_transferFundedClaim` sees `defaultRecoveryReserve == 0` and transfers underlying that belongs to the funded normal-withdraw claimants. Those users' `claimWithdrawRequest` then reverts on insufficient balance — direct theft plus freezing of their unclaimed payouts. Loss is bounded by the funded underlying held in the strategy, up to the attacker's deposit size (the receipt is minted 1:1 against burned strategy tokens, so the attacker needs a real position, making this a capital-backed but fully permissionless theft of a like amount).

### Likelihood Explanation
Requires `allowInstantWithdraw` enabled pools and instant requests that are requestable while the strategy holds funded-but-unclaimed reserves — i.e., between `stopEpoch` funding and users claiming, or whenever funded reserves sit idle. Attacker is any KYC-passed lender (tranche-token holder), which is in-scope. No privileged cooperation needed; sequence is entirely around honest manager calls (`stopEpoch`, `collectWithdrawFunds`).

### Recommendation
Track funded vs unfunded instant receipts separately (the missing "subtype check"): only allow `claimInstantWithdrawRequest` to pay amounts whose request epoch was collected, e.g. decrement `pendingInstantWithdraws` inside the claim for funded portions or gate claims on a per-epoch `instantFundedByEpoch` flag set by `collectInstantWithdrawFunds`. Equivalent to the kernel fix's `TC_U32_KEY(handle)` skip: do not treat a pending instant receipt as a funded one.

### Proof of Concept
Foundry fork sketch (pool with `allowInstantWithdraw`):

```solidity
// epoch N: user A requests normal withdraw; manager stops epoch,
// borrower funds -> collectWithdrawFunds moves underlying into strategy.
// Strategy balance now holds A's funded claim, pendingWithdraws cleared.

// Attacker (KYC'd lender) in buffer phase before startEpoch:
cdoEpoch.depositAA(attackAmount);            // attacker gets tranches
cdoEpoch.requestInstantWithdraw(attackAmount, tranche); // pendingInstantWithdraws += amt, no underlying yet
cdoEpoch.claimInstantWithdrawRequest();      // pays from strategy balance (A's reserve)

// Attacker received attackAmount underlying without funding arriving.
// Later: user A calls claimWithdrawRequest() -> underlyingToken.safeTransfer
// reverts (balance drained); A's funded claim is stolen.
// Also: subsequent collectInstantWithdrawFunds(attackAmount) underflows
// pendingInstantWithdraws (still includes attacker's receipt) or double-funds.
```

Assertion targets: attacker underlying balance delta ≈ `attackAmount`; `strategy.underlyingToken.balanceOf(strategy)` decreased below funded normal-reserve amount; `pendingInstantWithdraws` unchanged after the claim (secondary accounting bug).

Caveat: I verified the strategy-side paths in `IdleCreditVault.sol` and the CDO entry gate in `IdleCDOEpochVariant.sol:975-979`, but did not fully trace every call ordering inside `stopEpoch`/`startEpoch` (e.g., whether the CDO holds instant receipts' strategy tokens elsewhere or reverts claims earlier in its own flow). If `IdleCDOEpochVariant.requestInstantWithdraw`/`claimInstantWithdrawRequest` internally restricts claims to epochs already collected via `collectInstantWithdrawFunds`, the primary theft leg reduces to the `pendingInstantWithdraws` never-decremented accounting inconsistency (claim-before-collect desync), which still merits the same fix.