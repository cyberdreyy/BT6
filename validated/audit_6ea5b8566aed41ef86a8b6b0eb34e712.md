### Title
Closed-pool `requestWithdraw` mints interest-bearing receipts that are instantly claimable, stealing unearned interest - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestWithdraw` treats a closed pool (`epochEndDate() == 0`) as "no later stopEpoch", so it skips `pendingWithdraws` accounting — but still mints the user a strategy-token receipt for the full `_amount` (principal **plus** projected epoch interest) while only burning `_principal`. Because `_claimFundedWithdrawRequest` waives the one-epoch wait for closed pools, the attacker can claim the receipt in the same transaction and withdraw underlying that includes interest that was never earned, funded, or debited from NAV. This is the credit-vault analog of the CVE's "missing contextual validation" bug class: a value (the interest-inflated receipt amount) is accepted in a context (closed pool) where it is invalid.

### Finding Description
In `IdleCDOEpochVariant.requestWithdraw` (`contracts/IdleCDOEpochVariant.sol:772-790`), `_underlyings = principal + interest - totalFees`, where `interest` comes from `_calcInterestWithdrawRequest` — a forward-looking projection over `epochDuration` at the current APR (`contracts/IdleCDOEpochVariant.sol:856-878`). Nothing in `requestWithdraw` gates on the pool being closed (`epochEndDate == 0`), and `epochDuration`/APR are not cleared on close.

In `IdleCreditVault.requestWithdraw` (`contracts/strategies/idle/IdleCreditVault.sol:259-295`):
- `isClosed` is computed; when true, `pendingWithdraws += _amount` is skipped (line 277-280), so the borrower is never expected to fund the receipt.
- Yet `_mint(_user, _amount)` (line 275) still mints a receipt for principal + interest, while `_burn(msg.sender, _principal)` (line 273) removes only principal.

Claim side (`claimWithdrawRequest` → `_claimFundedWithdrawRequest`, lines 301-350): the gating check `epochNumber <= lastWithdrawRequest[_user]` is bypassed when `IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0` (line 326). The user then receives `amount = withdrawsRequests[_user]` — the interest-inclusive figure — via `_transferFundedClaim`, paid out of underlying held by the strategy that belongs to remaining LPs' strategy tokens.

### Impact Explanation
Direct theft. The interest component minted into the receipt corresponds to yield that would only accrue over a future epoch — but a closed pool has no future epoch, so that value is never funded by the borrower and never removed from active NAV (only `principal` is deducted via `_withdrawOps`). The attacker redeems it immediately against the strategy's underlying balance, diluting every other LP. Loss per call ≈ `_amount * apr * epochDuration / (365 days * 1e18) * trancheShare` minus withdraw fees; it is repeatable each request.

### Likelihood Explanation
Requires only an unprivileged tranche holder (KYC-passing lender) calling `requestWithdraw` then `claimWithdrawRequest` after the pool closes. Preconditions: `epochDuration > 0` and `unscaledApr`/strategy APR non-zero at close time, and sufficient strategy-held underlying to cover the phantom interest. If a deployment zeroes APR on close, the interest collapses to zero and the bug is inert — that configuration detail is the main uncertainty, since I could not verify whether close-path code clears `unscaledApr`/`epochDuration` (grep for `epochEndDate = 0` shows close assignments exist, but the APR state at close was not confirmed). The loss-claim guards (`lossRecoveryPriceByEpoch`, `defaultRecoveryFinalized`) do not apply since no default or shortfall occurred.

### Recommendation
In `IdleCreditVault.requestWithdraw` (or in the CDO), when `epochEndDate() == 0`, reject interest-bearing requests: either revert, or have the CDO pass `_amount == _principal` by short-circuiting `_calcInterestWithdrawRequest` to zero interest in closed-pool mode. Alternatively mint the receipt for `_principal` only so claim payout can never exceed the burned basis.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
// Foundry fork test sketch (test/foundry/ClosedPoolSteal.t.sol)
// Assumes helpers from IdleCreditVault.t.sol: _depositWithUser, _startEpochAndCheckPrices

function testClosedPoolInterestReceiptTheft() external {
    uint256 amount = 10_000 * ONE_SCALE;
    address victim = makeAddr("victim");
    address attacker = makeAddr("attacker");

    _depositWithUser(victim, amount, true);
    _depositWithUser(attacker, amount, true);
    _startEpochAndCheckPrices(0);

    // Borrower repays; manager closes the pool (epochEndDate set to 0, APR not cleared)
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    // close path that leaves epochEndDate == 0
    cdoEpoch.stopEpoch(0, 1); // pool-close sentinel per existing tests

    assertEq(cdoEpoch.epochEndDate(), 0);

    // Attacker requests a withdraw; receipt is minted with projected interest
    vm.prank(attacker);
    uint256 requested = cdoEpoch.requestWithdraw(0, address(AAtranche));
    uint256 trancheSupply = AAtranche.balanceOf(attacker);
    uint256 principal = trancheSupply * cdoEpoch.tranchePrice(address(AAtranche)) / ONE_TRANCHE_TOKEN;
    assertGt(requested, principal, "receipt includes unearned interest");

    // No wait required in closed pool: claim immediately pays principal + phantom interest
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 claimed = underlying.balanceOf(attacker) - balPre;
    assertEq(claimed, requested);
    assertGt(claimed, principal, "attacker extracted interest never funded by borrower");
}
```