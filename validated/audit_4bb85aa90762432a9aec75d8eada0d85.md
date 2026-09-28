### Title
Vault liquidity drain by a third-party ERC4626 user permanently blocks `stopEpoch` in programmable-borrower mode, freezing all tranche funds - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`IdleCDOEpochVariant` delegates pool liquidity to `ProgrammableBorrower`, which parks all idle underlying in an external ERC4626 `vault`. When `manager` calls `stopEpochWithDuration`, `onStopEpoch` must pull the required underlying out of that vault. If the vault's position is economically covered but temporarily illiquid (e.g., a MetaMorpho-style vault whose market liquidity has been fully borrowed by an unprivileged third-party user), `vault.withdraw` reverts and `onStopEpoch` reverts with `StopEpochVaultLiquidityUnavailable`, reverting the entire `stopEpoch`. The "default" escape path is unreachable because the revert happens before `transferFrom` can fail. An unprivileged attacker who is merely a user of the configured ERC4626 vault can repeatedly keep its liquidity drained, blocking every `stopEpoch` and freezing all pending withdraw requests, deposits-backed principal, and epoch settlement.

### Finding Description
In `ProgrammableBorrower.onStopEpoch` (contracts/strategies/idle/ProgrammableBorrower.sol:239-253):

```solidity
uint256 onHand = underlyingToken.balanceOf(address(this));
if (_amountRequired > onHand) {
  uint256 shortfall = _amountRequired - onHand;
  if (shortfall > _currentVaultAssets()) return true;   // real insolvency → default path
  try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
    ...
  } catch {
    revert StopEpochVaultLiquidityUnavailable();         // illiquid-but-covered → revert
  }
}
```

The explicit `return true` branch proves the authors distinguished "vault shares don't cover the shortfall" (default path) from "covered but withdraw fails" (revert). In the second case `stopEpochWithDuration` fully reverts (contracts/IdleCDOEpochVariant.sol:520-522 calls `_stopEpoch` which invokes this hook before pulling funds), so:

- `isEpochRunning` stays true, `epochAccountingActive` stays true.
- Pending withdraw requests (`epochPendingWithdraws`) can never be claimed — `claimWithdrawRequest` in `IdleCreditVault` requires the epoch to stop.
- `_handleBorrowerDefault` is unreachable: the revert precedes the CDO's `transferFrom`, and `getInstantWithdrawFunds` (the other default trigger) is explicitly disabled in programmable mode (`_checkNotAllowed(isProgrammableBorrower || ...)`, IdleCDOEpochVariant.sol:561).
- The owner/manager recovery path `emergencyExitVault` calls `vault.redeem`, which reverts on the same illiquidity (ProgrammableBorrower.sol:361-372).
- Meanwhile `borrowerPrincipal` keeps accruing contractual APR against the pool and `availableToBorrow` keeps reserving `epochPendingWithdraws`, so no value can move.

An attacker who is a borrower/user of the configured ERC4626 vault (explicitly in scope per the threat model) borrows all available liquidity from the vault's underlying markets right before, and maintains illiquidity across, every `stopEpoch` attempt (frontrunning any liquidity replenishment, or keeping utilization pinned via rolling borrows). Each `stopEpoch` call reverts. The freeze persists as long as the attacker keeps the vault drained; borrower-side interest distortion compounds the pool's accounting during the freeze.

### Impact Explanation
Temporary freezing of 100% of pool funds: all AA/BB tranche holders with pending withdraw requests and all depositors are unable to exit; the epoch can never settle while the attacker maintains the drain. For a pool holding e.g. 10M USDC, the entire 10M plus accrued yield is locked. The attacker has no profit motive needed beyond griefing cost equal to the vault borrow rate, and can release/re-grab liquidity to prolong the freeze indefinitely since each `stopEpoch` attempt is individually frontrunnable.

### Likelihood Explanation
Requires a programmable-borrower deployment whose `vault` is a lending-based ERC4626 (the codebase ships an `IMMVault` Morpho interface, and tests already fork `GAUNTLET_USDC_PRIME`) where an unprivileged third party can borrow the residual liquidity. No privileged role involvement is needed; the guard `shortfall > _currentVaultAssets()` only protects true insolvency, not illiquidity. Existing mitigations (`try/catch`, `emergencyExitVault`, `isInterestMinted`, flags) do not cover this path — `nonReentrant` and role checks are irrelevant because the attacker never touches this contract.

### Recommendation
Make `onStopEpoch` degrade gracefully instead of reverting on a covered-but-failed withdrawal: return `true` (or a tri-state) and let the CDO's `transferFrom` shortfall flow into the normal `_handleBorrowerDefault` / loss-accounting path, or expose a `partialWithdraw`/`maxWithdraw`-based settlement so the epoch can stop with whatever liquidity is actually returned. Additionally, `emergencyExitVault` should attempt `vault.withdraw(vault.maxWithdraw(...))` style partial exits so recovery does not depend on full vault liquidity.

### Proof of Concept
Foundry, mainnet fork (pattern follows `test/foundry/ProgrammableBorrowerCreditVault.t.sol` which already forks `GAUNTLET_USDC_PRIME`):

```solidity
// Setup (reusing _setUpProgrammableBorrowerCreditVault harness):
// 1. Deploy/configure ProgrammableBorrower with vault = GAUNTLET_USDC_PRIME (MetaMorpho).
// 2. idleCDO.depositAA(10_000e6); request withdraw; manager startEpoch.
// 3. Attacker EOA: on the Morpho markets backing GAUNTLET_USDC_PRIME, supply collateral
//    and borrow until morpho market liquidity (and thus vault.maxWithdraw(PB)) == 0.
// 4. manager calls cdoEpoch.stopEpochWithDuration(apr, interest, duration, 0);
//    → ProgrammableBorrower.onStopEpoch: shortfall <= _currentVaultAssets() (shares intact),
//      vault.withdraw reverts (no liquidity) → StopEpochVaultLiquidityUnavailable → tx reverts.
// 5. Warp/repeat: attacker keeps liquidity drained (re-borrow whenever repaid).
//    Assert: isEpochRunning() == true forever; strategy.claimWithdrawRequest(receipt) reverts;
//    emergencyExitVault(all shares) reverts; defaulted == false and no path can set it.
// 6. Measure frozen amount = tranche holders' pendingWithdraws + full pool principal.
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L361-372)
```text
  function emergencyExitVault(uint256 _shares) external nonReentrant returns (uint256 assets) {
    _checkOnlyOwnerOrManager();
    if (_shares == 0) {
      _shares = vault.balanceOf(address(this));
    }
    if (_shares == 0) revert InvalidAmount();
    assets = vault.redeem(_shares, address(this), address(this));
    if (epochAccountingActive) {
      epochWithdrawnFromVault += assets;
    }
    emit RedeemedFromVault(_shares, assets, address(this));
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L520-530)
```text
  function stopEpochWithDuration(uint256 _newApr, uint256 _interest, uint256 _duration, uint256 _lossAmount) public {
    // stop epoch checks that msg.sender is allowed
    _stopEpoch(_newApr, _interest, _lossAmount);
    if (_interest != 1 && !defaulted) {
      // buffer period is not changed
      setEpochParams(_duration, bufferPeriod);
      // scale the apr with the new duration and buffer
      _setScaledApr(_newApr);
    }
    _afterStopEpochWithDuration();
  }
```
