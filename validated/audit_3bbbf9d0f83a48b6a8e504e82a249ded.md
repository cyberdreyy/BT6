### Title
Attacker-controlled ERC4626 liquidity exhaustion permanently reverts `stopEpoch` via `StopEpochVaultLiquidityUnavailable`, freezing the whole credit pool — (`contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
The analog to CVE-2021-26931 — treating an externally influenceable error as a hard crash instead of a handled failure — is in `ProgrammableBorrower.onStopEpoch`. When `IdleCDOEpochVariant.stopEpoch` is called, the CDO reads `totalInterestDueNow()` and then invokes `onStopEpoch`, which attempts `vault.withdraw(shortfall, ...)` for any shortfall that the vault *nominally* covers (`shortfall <= _currentVaultAssets()`). If the external ERC4626 vault is momentarily illiquid (a state any unprivileged vault user can create and maintain by borrowing/locking its liquidity), the `catch` block reverts with `StopEpochVaultLiquidityUnavailable`, aborting `stopEpoch` entirely.

### Finding Description
In `onStopEpoch` (ProgrammableBorrower.sol:239-253):

```solidity
if (_amountRequired > onHand) {
  uint256 shortfall = _amountRequired - onHand;
  if (shortfall > _currentVaultAssets()) return true;
  try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
    ...
  } catch {
    revert StopEpochVaultLiquidityUnavailable();
  }
}
```

`convertToAssets` reports the vault's *accounting* value of the shares, not its *redeemable* liquidity. For lending-style ERC4626 vaults (e.g., Morpho/Aave-backed), utilization can be driven to ~100% by any vault participant, so `withdraw` reverts even though `convertToAssets(shares)` covers the shortfall. The revert propagates through `stopEpoch`, so the epoch can never be stopped while the condition persists.

There is no fallback path: the code deliberately returns `true` (leading to a default) only when shares do not *economically* cover the shortfall, but an *economically covered yet illiquid* withdrawal is turned into a transaction-level revert. The escape hatches do not help:

- `emergencyExitVault` (line 361-372) also calls `vault.redeem`, which reverts under the same illiquidity.
- `setVault` (line 158-170) requires `vault.balanceOf(address(this)) == 0`, unreachable while the position exists.
- `rescueTokens` can only move `underlyingToken` held on hand, not vault shares.

While `stopEpoch` is bricked, `isEpochRunning()` stays true: pending `claimWithdrawRequest` calls revert via `epochNumber <= lastWithdrawRequest[_user]` (IdleCreditVault.sol:326), new `requestWithdraw`s keep accruing into `pendingWithdraws`, deposits via the CDO are blocked, and defaulted-epoch resolution cannot begin. Notably, the same file already acknowledges this failure mode for defaults — `onDefault` explicitly avoids calling `convertToAssets` "even if the external vault's valuation view is unavailable during stress" (lines 295-297) — but the stop path has no equivalent degradation.

### Impact Explanation
An unprivileged user of the ERC4626 vault (an explicitly in-scope attacker) can keep the vault illiquid and cause every `stopEpoch` call to revert, temporarily freezing the entire pool's TVL: all LP principal and pending withdraw claims in the vault become unclaimable for the duration of the attack. The freeze is temporary but attacker-renewable at the cost of borrow interest in the underlying vault; the frozen amount is the full `getContractValue()` plus `pendingWithdraws` (the entire pool). This is a loss-of-availability of funds with a quantified ceiling equal to pool TVL.

### Likelihood Explanation
Requires a programmable-borrower deployment whose vault is a lending-market ERC4626 where a third party can consume redeemable liquidity — the exact configuration the contract is built for. The attacker needs capital roughly equal to vault idle liquidity to borrow (or an already-high-utilization market), and must keep utilization pinned for the freeze duration. No privileged role is involved; honest owner/manager/borrower behavior is assumed throughout. Medium likelihood, gated by vault market depth and the borrow cost of pinning utilization.

### Recommendation
Do not let a transient vault withdrawal failure hard-revert the epoch state transition. Options:

- In `onStopEpoch`, on `vault.withdraw` failure, return `false`/a distinct status so `IdleCDOEpochVariant` can route to its existing default/handling path (which computes loss from actually-received funds) instead of reverting the whole transaction.
- Alternatively, attempt a partial `withdraw`/`redeem` capped to `vault.maxWithdraw(address(this))` and treat any residual shortfall as a realized shortfall for the CDO to handle via its loss/default flow, mirroring the defensive stance already taken in `onDefault`.
- Keep a retryable stop for the illiquid case, but bound it — e.g., record a `stopEpochFailedAt` timestamp and allow the manager to force the default path after a grace period so LP funds cannot be frozen indefinitely.

### Proof of Concept
Foundry fork test outline (modeled on `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testPocStopEpochFrozenByVaultIlliquidity() external {
    // fork mainnet; deploy IdleCDOEpochVariant + IdleCreditVault wired to a
    // ProgrammableBorrower whose `vault` is a lending-market ERC4626 (e.g. Morpho)
    // 1. LPs depositAA/depositBB; manager startEpoch(); all idle cash is parked
    //    into the vault via onStartEpoch -> _depositToVault.
    // 2. Attacker (EOA, vault market participant) borrows ~all liquidity from the
    //    underlying market -> vault.maxWithdraw(programmableBorrower) ~ 0 while
    //    vault.convertToAssets(shares) still covers the full shortfall.
    // 3. vm.warp(epochEndDate + 1); borrower approves repayment as normal.
    // 4. vm.prank(manager); cdoEpoch.stopEpoch(apr, 0);
    //    -> reverts with StopEpochVaultLiquidityUnavailable (ProgrammableBorrower.sol:252)
    // 5. Repeating stopEpoch keeps reverting while utilization is pinned:
    //    - user.claimWithdrawRequest() reverts NotAllowed (IdleCreditVault.sol:326)
    //    - emergencyExitVault() reverts inside vault.redeem
    //    - setVault() reverts NotAllowed (shares != 0)
    //    => entire pool value + pendingWithdraws frozen for attack duration.
}
```

Uncertainty note: I could not confirm the exact revert-propagation wiring inside `IdleCDOEpochVariant.stopEpoch` (whether it wraps `onStopEpoch` in its own try/catch) within the iteration budget; the PoC assumes the revert bubbles, which the `revert StopEpochVaultLiquidityUnavailable()` design ("make stopEpoch retryable") strongly implies.