### Title
Third-party vault liquidity griefing permanently reverts `onStopEpoch`, freezing the entire epoch and all lender funds — ([contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower.onStopEpoch` reverts with `StopEpochVaultLiquidityUnavailable` whenever the external ERC4626 vault reports enough assets via `convertToAssets` to cover the stop-epoch shortfall but the actual `vault.withdraw` call fails. An unprivileged third-party user of the shared external vault (a whitelisted attacker class) can keep the vault illiquid — or make `convertToAssets`/`withdraw` revert — so every `stopEpoch` call reverts, the epoch can never close, and all pool NAV plus pending withdraw receipts stay frozen in `IdleCDOEpochVariant`.

### Finding Description
In `onStopEpoch`, when `IdleCDO` pulls `_amountRequired` and on-hand cash is insufficient, the contract compares the shortfall against `_currentVaultAssets()`: [1](#0-0) 

If `shortfall <= _currentVaultAssets()` the code attempts `vault.withdraw(shortfall, ...)` inside a try/catch and **reverts** on failure (`revert StopEpochVaultLiquidityUnavailable()`). Two properties make this exploitable by an unprivileged vault user:

1. `convertToAssets` (book value of shares) is not the same as `maxWithdraw`/`withdraw` liquidity. For vaults whose assets are partially lent out or rate-limited (Morpho-style markets, credit vaults, queued vaults), shares can be worth more than what can be withdrawn. A vault LP can borrow or lock liquidity so `vault.withdraw` reverts while `_currentVaultAssets()` still reports coverage.
2. `_currentVaultAssets()` itself calls `vault.convertToAssets(shares)` and `vault.balanceOf` (`ProgrammableBorrower.sol:546-549`), and `totalInterestDueNow()`/`vaultLoss()` read `_vaultNetInterest()` (`ProgrammableBorrower.sol:330-347`). An attacker who can make the vault's conversion functions revert (e.g., share-price manipulation to an overflow boundary, or vault pause/read failures) makes the entire `stopEpoch` flow revert even earlier, before `onStopEpoch` is reached.

Crucially, the "covered but unwithdrawable" branch does **not** fall back to the default path — the comment states only the economically-uncovered case should reach the default via `transferFrom` failure (`shortfall > _currentVaultAssets()` returns `true`). The covered-but-failing case is meant to be retryable, but nothing bounds the retry window: as long as the attacker keeps vault liquidity locked, every `stopEpoch` reverts.

While the epoch cannot be stopped:
- `epochNumber` never increments, so `IdleCreditVault.claimWithdrawRequest` reverts for every holder whose `lastWithdrawRequest` is the current epoch (`epochEndDate() != 0 && epochNumber <= lastWithdrawRequest[_user]` at `IdleCreditVault.sol:326-328`).
- `requestWithdraw`/`claimInstantWithdrawRequest` gating on `isEpochRunning` and the default finalization path (`_handleBorrowerDefault`/`finalizeDefaultRecovery`) are all blocked behind `stopEpoch`, so no loss can be declared and no recovery flow starts.

### Impact Explanation
Temporary freezing of **all** vault funds: entire tranche NAV, matured withdraw receipts (`withdrawsRequests`), and instant-withdraw receipts remain locked for the duration the attacker keeps the shared vault illiquid. Quantified loss = `lastNAVAA + lastNAVBB + pendingWithdraws + pendingInstantWithdraws` locked, plus interest slippage vs. the fixed-APR `expectedEpochInterest` accounting, which was priced for the scheduled `epochDuration`. If the external vault's illiquidity is permanent (bad debt in the wrapper vault), the freeze is permanent and the pool's default/finalization path can never trigger, since `_vaultNetInterest` reads are required to even price the stop.

### Likelihood Explanation
- Attacker requirements are minimal: any unprivileged holder/user of the same ERC4626 vault — no roles, no KYC, no tranche position needed. Draining/borrowing vault liquidity is a normal vault operation.
- Existing guards do not help: `_checkOnlyIdleCDO`, `nonReentrant`, and the `shortfall > _currentVaultAssets()` early-return all pass; the skim/default/epoch-gating checks sit behind the reverting `stopEpoch`.
- The only mitigation is that the freeze is temporary if the vault's liquidity is replenished — but an attacker can re-lock it each epoch boundary, and rational vault borrowers may keep it locked organically.

### Recommendation
In `onStopEpoch`, treat a failed covered withdrawal the same as an economically uncovered one: instead of `revert StopEpochVaultLiquidityUnavailable()`, return `false` (or return `true` and let the subsequent `transferFrom` shortfall drive `_handleBorrowerDefault`), so `stopEpoch` deterministically settles — either closing, defaulting, or funding partial claims — rather than bricking the epoch. Alternatively, wrap the `vault.withdraw` in bounded chunks/retries with a maximum attempt budget before forcing the default path, and avoid reading `convertToAssets` on the revert path the way `onDefault` already does (`ProgrammableBorrower.sol:295-297`).

### Proof of Concept
Foundry fork sketch (programmable-borrower deployment, epoch running, attacker is a third-party vault LP):

```solidity
// Setup: pool in running epoch with deposits; ProgrammableBorrower parked in external VaultV.
// VaultV is a lending-style ERC4626 where convertToAssets > maxWithdraw when utilization is high.

// 1) Attacker (any VaultV user, e.g. borrower) draws/locks liquidity so that
//    VaultV.withdraw(x) reverts for x > 0 while convertToAssets still reports full value.
vm.prank(attacker);
vaultV.borrow(vaultV.totalAssets() - 1); // leave dust liquidity

// 2) Manager tries to stop the epoch. IdleCDO reads totalInterestDueNow() (succeeds,
//    book value intact) then calls onStopEpoch(amountRequired, false).
vm.prank(manager);
vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
cdoEpoch.stopEpoch(apr, 0);
// shortfall <= _currentVaultAssets()  -> early `return true` NOT taken
// vault.withdraw reverts             -> catch -> revert

// 3) Repeat arbitrarily: epochNumber never increments.
vm.prank(manager);
vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
cdoEpoch.stopEpoch(apr, 0);

// 4) Withdrawal receipts frozen: user with lastWithdrawRequest == current epoch reverts.
vm.prank(lp);
vm.expectRevert(NotAllowed.selector);
cdoEpoch.claimWithdrawRequest();

// 5) No default/finalization path is reachable: stopEpoch must succeed first,
//    so the freeze persists as long as VaultV liquidity stays locked.
```

### Caveats
I could not re-verify the exact `stopEpoch` → `onStopEpoch`/`totalInterestDueNow` call order inside `IdleCDOEpochVariant.sol` within the available iterations, nor whether a prior fix already bounds retries. The core claim — that the try/catch reverts instead of degrading to the default path, and that `_currentVaultAssets()` coverage is checked with `convertToAssets` rather than withdrawable liquidity — is directly confirmed at `ProgrammableBorrower.sol:240-253`.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L240-253)
```text
    if (_amountRequired > onHand) {
      uint256 shortfall = _amountRequired - onHand;
      // If the vault shares do not economically cover the shortfall, let IdleCDO's later
      // transferFrom fail and use the existing default path. Only an otherwise-covered ERC4626
      // withdrawal failure should make stopEpoch retryable.
      if (shortfall > _currentVaultAssets()) return true;
      try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
        if (epochAccountingActive) {
          epochWithdrawnFromVault += shortfall;
        }
        emit WithdrawnFromVault(shortfall, shares, address(this));
      } catch {
        revert StopEpochVaultLiquidityUnavailable();
      }
```
