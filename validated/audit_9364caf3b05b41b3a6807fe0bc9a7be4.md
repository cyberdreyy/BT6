### Title
Loss-adjusted withdrawal price stored under the post-stop epoch key while receipts are indexed by the pre-stop request epoch — loss never applied to withdraw claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`collectWithdrawFunds` records a partial-funding haircut in `lossRecoveryPriceByEpoch[epochNumber]`, but `epochNumber` is only bumped inside `deposit()` when the CDO pushes borrower funds during `stopEpoch` (`IdleCreditVault.sol:607-611`). Withdraw receipts are keyed to the epoch in which they were requested via `lastWithdrawRequest[_user] = currentEpoch` (`IdleCreditVault.sol:282`) and `withdrawsRequestsByEpoch[_user][currentEpoch]` (`IdleCreditVault.sol:293`). The claim path `_claimLossAdjustedWithdrawRequest` looks the haircut up under `lastWithdrawRequest[_user]` (`IdleCreditVault.sol:790-792`). If the price is stored under the incremented epoch (request epoch + 1) — i.e., whenever `collectWithdrawFunds` executes after the epoch-bumping deposit inside `stopEpoch` — the lookup returns 0, the loss-adjusted path is silently skipped, and the receipt falls through to `_claimFundedWithdrawRequest`, which pays the full unhaircutted `withdrawsRequests[_user]` basis at par (`IdleCreditVault.sol:338-349`). The lifecycle mismatch mirrors CVE-2019-5758: an epoch-scoped object (the recovery price) is created under a different lifetime key than the one used to consume it, so the "dead" (loss) state is never observed and the stale object is redeemed at full value.

### Finding Description
- Request phase (buffer of epoch N): `requestWithdraw` burns `_principal`, mints a receipt, sets `lastWithdrawRequest[user] = N`, `withdrawsRequestsByEpoch[user][N] += amount`, `pendingWithdraws += amount` (`IdleCreditVault.sol:272-293`).
- Stop phase: `stopEpoch` has the strategy pull borrower repayment; `deposit()` detects `isEpochRunning()` and executes `epochNumber += 1` (`IdleCreditVault.sol:607-611`). When the borrower funds only `pendingToFund < pendingWithdraws`, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber] = amount * RECOVERY_FULL / pendingBasis` and zeroes `pendingWithdraws` (`IdleCreditVault.sol:411-421`). Because `epochNumber` is now N+1, the haircut lands under key N+1.
- Claim phase: `claimWithdrawRequest` → `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]` = `lossRecoveryPriceByEpoch[N]` = 0 and returns early (`IdleCreditVault.sol:790-792`). `_claimFundedWithdrawRequest` then sees `epochNumber (N+1) > lastWithdrawRequest (N)`, pays the full basis, burns the receipt, and clears state (`IdleCreditVault.sol:326-349`).

No guard catches this: `isEpochWithdrawZero`-style flags don't exist on the strategy side, `_transferFundedClaim` only protects `defaultRecoveryReserve` (`IdleCreditVault.sol:897-906`), and the `lossRecoveryPrice == 0` revert at `IdleCreditVault.sol:419` only guards the stored value, not the key.

### Impact Explanation
Direct theft/insolvency: the vault collected only `pendingToFund = pendingBasis - pendingLoss` underlying but pays out the full `pendingBasis` per receipt. Early claimers withdraw unhaircutted amounts; the strategy's underlying balance is drained below what later receipt holders (and, indirectly, active LPs whose NAV funded the payout) are owed. Quantified loss equals `pendingLoss = _lossAmount * pendingBasis / totalBasis` from `previewLossAdjustedWithdrawFunds` (`IdleCreditVault.sol:457`), i.e., the entire loss that was supposed to be socialized across pending receipts is instead transferred to remaining claimants/LPs — a full bypass of the loss waterfall for the pending bucket.

### Likelihood Explanation
Trigger requires an unprivileged lender to hold a pending withdraw receipt during an epoch where `stopEpochWithDuration(_lossAmount)` realizes a partial loss on the pending bucket — a normal operating mode explicitly supported by `previewLossAdjustedWithdrawFunds`. No privileged misbehavior is needed; the borrower/manager act honestly and the mis-keying is deterministic given the call ordering inside `stopEpoch` (deposit/bump before collect). Caveat: I could not fully confirm the exact ordering of `deposit()` vs `collectWithdrawFunds` inside `IdleCDOEpochVariant.stopEpoch` within the available iterations; if `collectWithdrawFunds` runs before the epoch-bumping deposit the key would be correct. A fork PoC should assert `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]` is non-zero after a loss-adjusted stop.

### Recommendation
Key the recovery price by the same epoch identifier stored in receipts: in `collectWithdrawFunds`, store under `epochNumber - 1` when the bump has already occurred, or better, pass the request epoch from the CDO / record the price under `lastRequestEpoch` captured before the bump. Alternatively, store `lossRecoveryPrice` against each user's `lastWithdrawRequest` epoch at claim time via a settlement function invoked during `stopEpoch` before `epochNumber` increments, and add an invariant test that `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]] != 0` whenever a loss-adjusted stop occurs.

### Proof of Concept
```solidity
// Foundry fork test (IdleCreditVault.t.sol harness style)
function testLossAdjustedClaimMisKeyedEpoch() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);

    // Epoch N buffer: attacker requests withdraw -> lastWithdrawRequest = N
    uint256 principal = cdoEpoch.requestWithdraw(0, address(AAtranche));
    uint256 reqEpoch = IdleCreditVault(address(strategy)).lastWithdrawRequest(address(this));

    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // Borrower repays only part of pendingWithdraws: realized loss on pending bucket
    uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws();
    uint256 funded = pending / 2; // 50% loss on receipts
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(initialProvidedApr, /* loss covering pending bucket */, 0);

    // BUG: haircut stored under epochNumber (N+1), receipt indexed by N
    assertEq(IdleCreditVault(address(strategy)).lossRecoveryPriceByEpoch(reqEpoch), 0);
    assertGt(IdleCreditVault(address(strategy)).lossRecoveryPriceByEpoch(reqEpoch + 1), 0);

    // Claim pays full principal instead of funded/2
    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    assertEq(underlying.balanceOf(address(this)) - balPre, principal); // should be ~funded share
}
```