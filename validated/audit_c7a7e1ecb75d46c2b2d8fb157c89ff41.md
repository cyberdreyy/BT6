### Title
Attacker-controlled ERC4626 liquidity crunch makes `stopEpoch` permanently revert, freezing all pool funds - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower.onStopEpoch` reverts with `StopEpochVaultLiquidityUnavailable` whenever the configured ERC4626 vault cannot honor a withdrawal whose size is covered by the borrower's *share value* but not by the vault's *available liquidity*. Because `IdleCDOEpochVariant._stopEpoch` deliberately lets this revert bubble (no try/catch, no default fallback), any unprivileged user of the underlying ERC4626 vault (e.g., a Morpho borrower draining idle liquidity) can make every `stopEpoch` call revert. The epoch can never be stopped, so no withdraw requests can be funded or claimed and all LP principal is frozen for as long as the vault stays illiquid.

### Finding Description
In `contracts/IdleCDOEpochVariant.sol:395-404`, when the borrower is programmable, `_stopEpoch` calls `IProgrammableBorrower(_borrower()).onStopEpoch(...)` *outside* the `try this.getFundsFromBorrower(...)`/`catch` block that routes failures into `_handleBorrowerDefault`. The comment states "Hook reverts bubble so transient ERC4626 liquidity failures can be retried." [1](#0-0) 

In `contracts/strategies/idle/ProgrammableBorrower.sol:239-253`, the hook compares the shortfall against `_currentVaultAssets()` (share *value* via `convertToAssets`), not against `vault.maxWithdraw(...)` / actual vault liquidity, and reverts when `vault.withdraw` fails: [2](#0-1) 

```solidity
if (shortfall > _currentVaultAssets()) return true;   // coverage by share VALUE
try vault.withdraw(shortfall, address(this), address(this)) returns (...) {
    ...
} catch {
    revert StopEpochVaultLiquidityUnavailable();       // bubbles out of stopEpoch
}
```

This is the direct analog of the emmett-core bug: an unhandled error condition on attacker-influenced input (vault liquidity state) is surfaced as a hard revert in a mandatory protocol path, instead of being handled gracefully. The "retry later" assumption only holds if liquidity is transient — but liquidity availability in lending vaults like Morpho is itself a market outcome an attacker can control by borrowing all available assets.

### Impact Explanation
While `onStopEpoch` reverts, the epoch can never be stopped:
- `pendingWithdraws` are never funded, so `claimWithdrawRequest` reverts (`epochNumber <= lastWithdrawRequest[_user]` guard at `contracts/strategies/idle/IdleCreditVault.sol:326`).
- The pool cannot be closed (`stopEpoch(0, 1)` also goes through the same hook), and `epochEndDate` never reaches 0.
- Result: temporary freezing of the entire pool TVL (all AA/BB principal plus pending withdraw receipts) for a duration fully controlled by the attacker — in a Morpho-style vault, a borrower can keep utilization at ~100% indefinitely by not repaying, which is normal, cheap market behavior requiring no protocol privilege.

### Likelihood Explanation
The attacker is explicitly in scope: "a user of the programmable borrower's ERC4626 vault." No IdleCDO privilege is needed — only borrowing/withdrawing from the external vault. The only precondition is that the vault has some non-zero withdrawable liquidity below `shortfall` while the borrower's shares still cover it (a common state once a vault is utilized). The design intentionally converts this state into a revert rather than the default path, so the freeze triggers reliably on every `stopEpoch` attempt while the condition holds.

### Recommendation
Handle the vault-liquidity failure gracefully, mirroring the emmett fix pattern (`except CookieError: continue` → degrade instead of crash): check `vault.maxWithdraw(address(this))` (or the vault's idle liquidity) before deciding coverage, and if the withdrawal cannot succeed, either withdraw what's available and let `getFundsFromBorrower`'s `transferFrom` shortfall route into the existing `_handleBorrowerDefault` path, or return `false` so the default flow is triggered deterministically instead of an unbounded revert loop. Alternatively, cap the number of retries / add a manager-forced "treat as default" escape after N failed stops so liquidity conditions cannot freeze the epoch indefinitely.

### Proof of Concept
Foundry fork sketch against a real deployment with a Morpho-like vault:

```solidity
function testStopEpochFrozenByVaultLiquidity() external {
    // setup: programmable borrower pool, deposits made, epoch running
    idleCDO.depositAA(1_000_000e6);
    _startEpoch(); // manager starts epoch, PB deposits idle cash into vault

    // Attacker: ordinary user of the same ERC4626 vault (e.g. Morpho borrower)
    // borrows all available liquidity so vault.withdraw reverts,
    // while PB's shares still value-cover the shortfall.
    vm.prank(attacker); // attacker is just a vault borrower
    morpho.borrow(allAvailableLiquidity, ...);

    // Manager attempts to stop the epoch -> onStopEpoch reverts
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0);

    // Retries keep reverting while attacker keeps the loan open
    vm.warp(block.timestamp + 30 days);
    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0);

    // LPs with pending withdraw requests cannot claim: epochNumber never advances
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.claimWithdrawRequest();
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L395-404)
```text
    if (isProgrammableBorrower) {
      // Ask the programmable borrower to recall ERC4626 liquidity before IdleCDO pulls funds.
      // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        // Emit the exact cash liability requested from the borrower, including recalled principal
        // in close-pool mode and excluding interest fronted through minted accounting.
        _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
        return;
      }
    }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-253)
```text
    uint256 onHand = underlyingToken.balanceOf(address(this));
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
