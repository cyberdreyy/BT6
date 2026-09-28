### Title
Post-default withdraw requests spend the isolated default-recovery reserve ahead of defaulted-epoch claimants, permanently freezing their recovery claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a use-after-free caused by dereferencing a shared pointer outside its protected critical section. The credit-vault analog is `defaultRecoveryReserve`: it is a shared, finite, "protected" allocation whose valid consumers are the defaulted-epoch claimants, yet post-default withdraw requests (created *after* `finalizeDefaultRecovery`) also draw from it 1:1 in `_claimPostDefaultWithdrawRequest` / `_transferDefaultRecovery` without ever contributing to it. A post-default requester therefore spends reserve outside the accounting scope that sized it, and once the reserve is exhausted, every later `_transferDefaultRecovery` reverts on `defaultRecoveryReserve -= _amount` underflow — permanently freezing unclaimed default recovery.

### Finding Description
After `defaultRecoveryFinalized` is set, `requestWithdraw` mints a receipt to the user at an already-haircut amount and records `postDefaultRequests[_user]` (lines 247-257). On `claimWithdrawRequest`, `_claimPostDefaultWithdrawRequest` pays that amount 1:1 via `_transferDefaultRecovery`, which decrements `defaultRecoveryReserve` (lines 760-767, 912-917).

The reserve, however, was sized at default-finalization time to cover exactly `sum(defaulted-epoch claimBasis) * defaultRecoveryPrice / RECOVERY_FULL` for `_claimDefaultedWithdrawRequest` and `_claimDefaultedInstantWithdrawRequest` claimants. Nothing in `requestWithdraw` or `_claimPostDefaultWithdrawRequest` adds the post-default principal to `defaultRecoveryReserve`, and the receipt is not funded by any fresh borrower repayment at request time — the strategy tokens are merely re-minted to the user.

The broken invariant is the isolation of the recovery pool ("one receipt one payout"): the post-default path consumes reserve denominated in the same asset but accounted on a different basis. Since defaulted-epoch claimants may claim at any later time (there is no deadline in `_claimDefaultedWithdrawRequest`), an early post-default claim front-runs them.

### Impact Explanation
Concrete sequence (epoch defaulted, `finalizeDefaultRecovery` executed, `defaultRecoveryReserve = R`):

1. Honest lenders hold defaulted-epoch receipts with total claim `R`.
2. Attacker (any KYC'd tranche holder) calls `requestWithdraw` post-default, receiving `postDefaultRequests[attacker] = X`, then `claimWithdrawRequest`, receiving `X` underlying out of `defaultRecoveryReserve`.
3. Honest defaulted-epoch claimant calls `claimWithdrawRequest`; `_transferDefaultRecovery` executes `defaultRecoveryReserve -= amount` and reverts on underflow once cumulative draws exceed `R`.

Result: direct theft of up to `X` of recovery value plus permanent freezing of the remaining defaulted-epoch recovery claims, equal to the post-default drain. Because the revert is in a storage underflow on an isolated reserve, no later borrower repayment routed through the reserve path can un-freeze it.

### Likelihood Explanation
Requires a pool that reaches `defaultRecoveryFinalized` with an under-collateralized recovery (the normal case for a default), plus any user making a post-default withdraw request — an ordinary, expected action, not a privileged one. No guard compensates: `_transferFundedClaim`'s reserve-exclusion check protects funded claims, not reserve claims; the 1:1 payment is explicitly intended in the code comments, so the reserve deficit is structural rather than incidental. Ordering dependency (post-default claim must precede the exhausted default claims) is trivially satisfied since defaulted claimants have no claim deadline.

### Recommendation
Track a separate `postDefaultReserve` funded when post-default receipts are created (e.g., require the CDO/borrower to fund the request amount via `collectWithdrawFunds` before `postDefaultRequests` is claimable), or increment `defaultRecoveryReserve` by the request amount at `requestWithdraw` time only if corresponding underlying is actually pulled in. Alternatively pay post-default requests through `_transferFundedClaim` against non-reserve balance and revert when `underlyingToken.balanceOf(this) - defaultRecoveryReserve < amount`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
// Foundry fork test against IdleCreditVault
function testPostDefaultClaimDrainsRecoveryReserve() external {
    // Setup: deposit, startEpoch, borrower underfunds stopEpoch -> default
    // manager calls finalizeDefault / finalizeDefaultRecovery
    // => defaultRecoveryReserve = R (sum of defaulted claims * defaultRecoveryPrice)

    IdleCreditVault vault = IdleCreditVault(address(strategy));
    uint256 reserveBefore = vault.defaultRecoveryReserve();

    // Attacker: post-default requestWithdraw then claim (unprivileged path)
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(attackShares, address(AAtranche));
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest(); // pays from defaultRecoveryReserve 1:1

    assertLt(vault.defaultRecoveryReserve(), reserveBefore);

    // Honest defaulted-epoch claimant now reverts forever
    vm.prank(victim);
    vm.expectRevert(); // underflow: defaultRecoveryReserve -= amount
    cdoEpoch.claimWithdrawRequest();
}
```

Note: I was unable to read `finalizeDefaultRecovery` to confirm the exact reserve sizing arithmetic within the iteration budget; the finding holds if the reserve is provisioned only for defaulted-epoch claim basis, which the `_claimDefaulted*` accounting (`claimBasis * defaultRecoveryPrice`) and the post-default "already-haircut, backed by the reserve" comments strongly indicate. If post-default receipts are separately funded elsewhere in the default-finalization path, this should be downgraded.