### Title
Malicious ERC4626 vault user can indefinitely block `stopEpoch` by withholding vault liquidity, freezing all pending withdrawals - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The mapped analog of "withhold credit → block session closure indefinitely" is `ProgrammableBorrower.onStopEpoch`: before `IdleCDOEpochVariant._stopEpoch` pulls cash from the borrower, the borrower must withdraw any shortfall from its external ERC4626 vault. If that vault's redeemable liquidity is insufficient, `vault.withdraw` reverts and `onStopEpoch` reverts `StopEpochVaultLiquidityUnavailable` — and because this call is *not* inside a try/catch, the whole `stopEpoch` reverts, the epoch stays running, and no withdraw receipts are funded. An unprivileged user of the ERC4626 vault (an allowed attacker role) can keep vault liquidity drained to block epoch closure indefinitely.

### Finding Description
In `IdleCDOEpochVariant._stopEpoch`, the programmable-borrower path calls `onStopEpoch` directly, so a revert there aborts `stopEpoch` entirely:

```solidity
// contracts/IdleCDOEpochVariant.sol ~L395-404
if (isProgrammableBorrower) {
    if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        _handleBorrowerDefault(...);
        return;
    }
}
// only getFundsFromBorrower is wrapped in try/catch
try this.getFundsFromBorrower(...) { ... } catch { _handleBorrowerDefault(...); }
```

In `ProgrammableBorrower.onStopEpoch` (`contracts/strategies/idle/ProgrammableBorrower.sol` L239-253):

```solidity
uint256 onHand = underlyingToken.balanceOf(address(this));
if (_amountRequired > onHand) {
    uint256 shortfall = _amountRequired - onHand;
    // share *value* covers the shortfall → does NOT take the default path
    if (shortfall > _currentVaultAssets()) return true;
    try vault.withdraw(shortfall, address(this), address(this)) returns (...) { ... }
    catch { revert StopEpochVaultLiquidityUnavailable(); }  // bubbles up → stopEpoch reverts
}
```

The check `shortfall > _currentVaultAssets()` uses `convertToAssets`-style valuation, which reports shares' *book* value. For a lending-style ERC4626 vault (e.g., the Morpho vault used in `test/foundry/ProgrammableBorrowerCreditVault.t.sol`), shares can be fully backed while *withdrawable* liquidity is lent out. An attacker who is an ordinary user/borrower of that vault can borrow or otherwise remove essentially all available liquidity. Then:

- `shortfall <= _currentVaultAssets()` (shares are nominally backed), so the "return true / let transferFrom fail / default" path is not taken;
- `vault.withdraw(shortfall, ...)` reverts with insufficient liquidity;
- `StopEpochVaultLiquidityUnavailable` propagates out of `stopEpoch`;
- `isEpochRunning` remains true, `pendingWithdraws` are never funded, `epochNumber` never advances — this is exactly the state observed in `testProgrammableBorrowerStopEpochVaultLiquidityRevert` (t.sol ~L391-398).

The attacker can keep the vault illiquid every time the manager retries (or frontrun each `stopEpoch` by draining freshly returned liquidity), converting an acknowledged *transient* failure into an indefinite block, at a cost bounded by the external vault's borrow rate.

### Impact Explanation
While the block is maintained:

- All pending withdraw receipts (`withdrawsRequests`/`instantWithdrawsRequests`) remain unfunded — `claimWithdrawRequest`/`claimInstantWithdrawRequest` cannot pay out because `collectWithdrawFunds`/`collectInstantWithdrawFunds` never run.
- The epoch cannot end: `epochEndDate` passes but `isEpochRunning` stays true; no new epoch starts; `depositDuringEpoch` and claims stay frozen.
- The borrower-side escape routes don't help: closing the pool (`stopEpoch(0,1)`) goes through the same `onStopEpoch` path and reverts identically; `_handleBorrowerDefault` is never reached because the revert propagates rather than returning `false`.

Result: temporary, arbitrarily extensible freezing of the entire vault TVL and all pending claims, triggered by an unprivileged third party with no position in IdleCDO at all.

### Likelihood Explanation
- Attacker needs no IdleCDO position, KYC, or privileged role — only the ability to draw/borrow liquidity in the external ERC4626 vault, which the prompt explicitly allows ("a user of the programmable borrower's ERC4626 vault").
- The window is structural: every `stopEpoch` must pass through `onStopEpoch` whenever `onHand < _amountRequired`, which is the normal case since idle cash is parked in the vault (`onStartEpoch` deposits all on-hand balance, L216).
- Cost to sustain the grief is the vault's own borrow/funding cost, which can be small relative to a large IdleCDO TVL.

### Recommendation
Treat vault-liquidity failure as a graceful condition instead of a hard revert, analogous to the webtransport-go fix (deadline → reset instead of blocking):

- Return `false` (or a tri-state) from `onStopEpoch` when `vault.withdraw` reverts, letting `IdleCDOEpochVariant` decide whether to treat it as a borrower default or a retryable stop — e.g., only make it retryable for owner/manager while allowing a separate manager-confirmed "force default on liquidity failure" escape.
- Alternatively, give the CDO a bounded retry path: after a failed `stopEpoch`, allow `stopEpoch` to proceed once either vault liquidity is restored *or* a manager/guardian explicitly elects default handling, so an external vault user can never hold epoch closure hostage indefinitely.

### Proof of Concept
Foundry fork test (extend `test/foundry/ProgrammableBorrowerCreditVault.t.sol`, which already forks a real Morpho ERC4626 vault):

```solidity
// 1. LPs deposit, epoch starts; ProgrammableBorrower parks all cash in the vault.
idleCDO.depositAA(amount);
_startEpochAndCheckPrices(0);

// 2. Attacker (ordinary vault user, no IdleCDO position) drains vault liquidity,
//    e.g. by borrowing against collateral in the underlying Morpho market until
//    vault.withdraw(shortfall) reverts on insufficient liquidity.
vm.prank(attacker);
morpho.borrow(marketId, vaultLiquidity, 0, attacker, attacker);

// 3. Epoch ends; manager tries to close → stopEpoch reverts, nothing is funded.
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
cdoEpoch.stopEpoch(0, 0);

// No default is recorded either — the revert bypasses _handleBorrowerDefault.
assertTrue(cdoEpoch.isEpochRunning());
assertFalse(cdoEpoch.defaulted());
assertGt(strategy.pendingWithdraws(), 0); // receipts unfunded

// 4. Repeat indefinitely: whenever liquidity returns, attacker re-drains before
//    the next stopEpoch attempt, keeping all LP funds and pending claims frozen.
```