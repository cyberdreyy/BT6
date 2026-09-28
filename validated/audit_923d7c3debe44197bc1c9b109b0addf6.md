### Title
Unfunded instant-withdraw receipts are paid out of active vault liquidity - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to `passport-oauth2` granting authorization on merely receiving an HTTP-200 response without a valid access token, `IdleCreditVault.claimInstantWithdrawRequest` pays a withdraw receipt without verifying that the receipt was ever funded. The contract tracks the unfunded remainder precisely in `pendingInstantWithdraws`, but the claim path ignores it, so a receipt whose liquidity was never collected via `collectInstantWithdrawFunds` is paid from whatever underlying the strategy happens to hold — i.e., other depositors' principal or borrower funds parked in the vault.

### Finding Description
`requestInstantWithdraw` mints receipt strategy tokens to the user and increments both `instantWithdrawsRequests[_user]` and the global unfunded counter `pendingInstantWithdraws` (`IdleCreditVault.sol:356-375`). Liquidity only arrives later when the CDO calls `collectInstantWithdrawFunds`, which pulls underlying from the CDO and decrements `pendingInstantWithdraws` (`IdleCreditVault.sol:398-403`).

`claimInstantWithdrawRequest` then burns the full `instantWithdrawsRequests[_user]` and pays `_transferFundedClaim(_user, amount)` (`IdleCreditVault.sol:380-393`). `_transferFundedClaim` (`IdleCreditVault.sol:897-907`) only guards the `defaultRecoveryReserve`; it never checks `pendingInstantWithdraws` or any per-epoch funded flag. The contract therefore conflates "receipt exists" with "receipt is funded" — the exact bug class of the advisory (error/failure response treated as a valid token).

Broken invariant: one receipt one payout against actually-collected funds. A request created during a running/buffer epoch, before the CDO fronted any liquidity, is honored against strategy-held underlying that belongs to active LPs and pending normal withdrawals.

### Impact Explanation
If the CDO exposes a claim path that does not itself require the instant queue to be funded (there is no on-chain enforcement in this contract), an unprivileged lender can call `requestInstantWithdraw` while the vault holds idle underlying, then claim before `collectInstantWithdrawFunds` runs, receiving underlying that was never allocated to the instant queue. Loss is up to the strategy's on-hand underlying balance (borrower repayments, collected-but-not-yet-claimed funds, or normal-withdraw funding held in the strategy), stolen 1:1 by the attacker. Even where the CDO sequences funding correctly, the missing invariant means any partial-funding or reordering (e.g., funding tx failing while `pendingInstantWithdraws` stays non-zero) is payable out of unrelated balances — the same class of silent-success failure the advisory describes.

### Likelihood Explanation
- The attacker needs only to be a KYC-passed lender able to open an instant-withdraw request through the CDO; no privileged role is involved.
- The vault explicitly maintains `pendingInstantWithdraws` as the "unfunded remainder" (documented at `IdleCreditVault.sol:62-63` and used as such in `_claimDefaultedInstantWithdrawRequest`), yet the pre-default claim path never consults it, so the absence of the check is deliberate-looking but the funded/unfunded distinction is only enforced by off-chain CDO ordering.
- The risk materializes whenever a claim is executed while `pendingInstantWithdraws > 0` for that receipt — e.g., claim routed in the same window as the request, or after a failed/partial `collectInstantWithdrawFunds`.

### Recommendation
In `claimInstantWithdrawRequest` (or `_transferFundedClaim` for the instant path), require that the claimed amount is funded: track a funded-instant counter or per-epoch funding flag, and revert (or only pay `min(amount, funded)`) when `pendingInstantWithdraws` still covers the receipt. Alternatively decrement `pendingInstantWithdraws` only on claim-funding success and gate claims on it being zero for the user's epoch. Apply the same funded-vs-requested check to `_claimFundedWithdrawRequest` for symmetric protection.

### Proof of Concept
Foundry fork test outline (mainnet fork with a live `IdleCreditVault` + `IdleCDOEpochVariant`):

```solidity
// test/foundry/UnfundedInstantWithdraw.t.sol
function testUnfundedInstantClaimDrainsVault() public {
    // Setup: vault holds B units of underlying (e.g., borrower repayment / idle buffer)
    // with pendingInstantWithdraws == 0 and defaultRecoveryReserve == 0.
    deal(address(underlying), address(vault), B);

    // 1. Attacker (KYC'd lender) requests instant withdraw of A <= B via the CDO.
    vm.startPrank(attacker);
    cdo.requestInstantWithdraw(A); // mints receipt, pendingInstantWithdraws = A
    vm.stopPrank();
    assertEq(vault.pendingInstantWithdraws(), A);          // nothing funded yet

    // 2. Attacker claims before any collectInstantWithdrawFunds call.
    vm.prank(attacker);
    cdo.claimInstantWithdrawRequest();                      // succeeds despite 0 funding
    assertEq(underlying.balanceOf(attacker), A);           // paid from vault's other funds
    assertEq(vault.pendingInstantWithdraws(), A);          // still marked unfunded
}
```

Caveat I could not fully verify within the read scope: whether `IdleCDOEpochVariant.claimInstantWithdrawRequest` adds its own funding check before calling the strategy. If the CDO enforces funding, the bug reduces to a missing defense-in-depth invariant; if it does not (or only checks aggregate liquidity rather than per-request funding), this is a direct theft of `A` underlying per unfunded receipt. Confirm by tracing the CDO's claim path before finalizing severity.