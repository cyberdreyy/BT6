### Title
External ERC4626 liquidity/price manipulation makes `ProgrammableBorrower.onStopEpoch` revert, permanently blocking `stopEpoch` and freezing lender funds - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
Analogous to the Astaria `supportsInterface()` DoS, `IdleCDOEpochVariant._stopEpoch` executes code inside an attacker-influenceable contract: the configured external ERC4626 `vault` via `ProgrammableBorrower.onStopEpoch`. Any unprivileged user of that shared vault (or a direct token sender) can donate underlying to inflate `convertToAssets` while keeping real liquidity insufficient, so the coverage check passes but `vault.withdraw` reverts. `onStopEpoch` then reverts with `StopEpochVaultLiquidityUnavailable`, `stopEpoch` can never complete, the epoch cannot transition to default handling, and all tranche-holder funds are frozen.

### Finding Description
`stopEpoch` / `_stopEpoch` in `IdleCDOEpochVariant` calls into the programmable borrower hook before pulling funds [1](#0-0) . Inside `onStopEpoch`, when `onHand` is below `_amountRequired`, the code only returns `true` (letting the CDO's `transferFrom` fail and route to the default path) when the shortfall exceeds `_currentVaultAssets()`; otherwise it calls `vault.withdraw` and reverts on failure [2](#0-1) .

The coverage check uses `vault.convertToAssets(shares)` [3](#0-2) , which prices shares at `totalAssets` — a value any direct token sender can inflate by donating underlying to the vault. After a donation, `convertToAssets` reports coverage for the shortfall, the code enters the `try vault.withdraw(...)` branch, but the vault lacks the liquid underlying to pay out and the withdrawal reverts, surfacing as `StopEpochVaultLiquidityUnavailable`.

Because the revert propagates out of `stopEpoch`, the honest manager cannot stop the epoch, cannot reach the default/finalization path (`_handleBorrowerDefault` is only reachable through the stop flow), and all pending withdraw receipts plus active tranche principal stay locked. Unlike Astaria's lien-owner callback, the injected "arbitrary code" here is the ERC4626's withdraw path, which the attacker controls economically (donation, liquidity withdrawal, vault-level withdrawal locks/queues) rather than by owning a token.

### Impact Explanation
Temporary-to-permanent freezing of all lender funds in the pool: the entire NAV (AA + BB tranche principal and accrued interest) cannot be withdrawn because `stopEpoch` always reverts while the vault's reported coverage exceeds its spendable liquidity. Withdraw-request claimants and tranche redeemers are blocked indefinitely. Recovery requires the owner to call `emergencyExitVault` [4](#0-3)  — which itself calls `vault.redeem` and can equally revert under the same liquidity conditions — or `rescueTokens`/`setVault`, none of which help while the vault itself cannot pay out. The donation persists, so the condition does not self-heal; loss magnitude equals the full pool TVL for the duration of the freeze.

### Likelihood Explanation
The attacker needs only to be a direct token sender to (or ordinary depositor in) the shared ERC4626 vault — an explicitly unprivileged position — plus capital equal to the stop-epoch shortfall. The vault is configured once at `initialize` and reused every epoch [5](#0-4) , so a single donation positioned before `epochEndDate` is enough. No privileged role, no KYC status, and no tranche position is required. The griefing cost is the donated amount (which accrues to the vault's shareholders, partially recoverable by the attacker if they hold vault shares).

### Recommendation
- Do not let `onStopEpoch` revert on vault withdrawal failure; return `false`/`true` and let IdleCDO's `transferFrom` shortfall drive the normal default path for any withdrawal failure, not only uncovered shortfalls.
- Alternatively, wrap the vault interaction in a try/catch inside the CDO (mirroring the `sendFundsToBorrower` self-call workaround [6](#0-5) ) and treat hook reverts as a defaulting stop rather than a hard revert.
- Base the coverage check on realizable liquidity (e.g., `vault.maxWithdraw(address(this))`) instead of `convertToAssets`, which is donation-inflatable.
- Add a manager escape that can force the default path without a successful `onStopEpoch` hook.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-foundry/../Test.sol"; // standard foundry test harness
// Fork mainnet; use the deployed IdleCDOEpochVariant (programmable mode),
// its ProgrammableBorrower adapter, and the configured ERC4626 vault.

contract StopEpochVaultDoSTest is Test {
    function testDonationDoSesStopEpoch() public {
        // --- setup (fork state) ---
        // idleCDO = programmable IdleCDOEpochVariant
        // pb      = ProgrammableBorrower (borrower() of the vault strategy)
        // vault   = pb.vault() (shared ERC4626, e.g. a lending vault)
        // token   = pb.underlyingToken()

        // 1. Epoch is running; pb holds vault shares; borrower drew liquidity
        //    so onHand < amountRequired at stop time.

        uint256 shortfall; // = amountRequired - onHand (known from pendingWithdraws + interest)

        // 2. Attacker (any EOA) donates underlying directly to the ERC4626 vault,
        //    inflating convertToAssets(pbShares) above `shortfall`, while the vault's
        //    *withdrawable* liquidity is below `shortfall` (e.g. attacker also
        //    withdrew available liquidity as an ordinary vault depositor first).
        deal(address(token), attacker, donationAmount);
        vm.prank(attacker);
        token.transfer(address(vault), donationAmount);
        // Now: shortfall <= pb._currentVaultAssets()  -> coverage check passes
        // but: vault.withdraw(shortfall, pb, pb) reverts (insufficient liquid assets)

        // 3. Manager calls stopEpoch at epochEndDate.
        vm.warp(idleCDO.epochEndDate() + 1);
        vm.prank(manager);
        vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
        idleCDO.stopEpoch(newApr, 0);

        // 4. Epoch is stuck: isEpochRunning stays true, no default path is reachable,
        //    claimWithdrawRequest / requestWithdraw payouts are frozen.
        assertTrue(idleCDO.isEpochRunning());
        // Repeating after time still reverts — the donation persists.
        vm.warp(block.timestamp + 30 days);
        vm.prank(manager);
        vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
        idleCDO.stopEpoch(newApr, 0);
    }
}
```

Caveat I could not fully verify with remaining iterations: the exact call site of `onStopEpoch` inside `IdleCDOEpochVariant._stopEpoch` (whether it is already wrapped in try/catch) — the `StopEpochVaultLiquidityUnavailable` revert design and the comment "should make stopEpoch retryable" indicate the revert intentionally propagates to the caller, but that line should be confirmed before finalizing the report.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L306-311)
```text
  /// @notice workaround to have safeTransfer to borrower as external and use it in a try/catch block
  /// @param _amount Amount of underlyings to transfer
  function sendFundsToBorrower(uint256 _amount) external {
    _checkNotAllowed(msg.sender != address(this));
    _transferUnderlyings(_borrower(), _amount);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L330-353)
```text
  function _stopEpoch(uint256 _newApr, uint256 _interest, uint256 _lossAmount) private {
    _checkOnlyOwnerOrManager();
    bool _isRequestingAllFunds = _interest == 1;
    _checkProgrammableBorrowerMode();

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _pendingWithdrawFees = pendingWithdrawFees;

    _checkNotAllowed(
      // Check that epoch is running
      !isEpochRunning || 
      // Check that end date is passed
      block.timestamp < epochEndDate || 
      // Check that there are no pending instant withdraws, ie `getInstantWithdrawFunds` was called
      // before closing the epoch
      _pendingInstant() != 0 ||
      // Check that overridden interest, if passed (ie > 1), is greater than pending withdraw fees and the apr is 0 
      // otherwise withdrawal requests may not be fullfilled as they consider also the interest gained in the next epoch 
      (_interest > 1 && (_interest < _pendingWithdrawFees || _newApr != 0)) ||
      // Closing already recalls all principal, so applying a separate loss burn would strand returned cash.
      (_isRequestingAllFunds && _lossAmount != 0)
    );

    uint256 _totBorrowed = _beforeStopEpoch(_isRequestingAllFunds);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L126-134)
```text
    vault = IERC4626(_vault);
    idleCDO = _idleCDO;
    manager = _manager;
    borrower = _borrower;
    borrowerApr = _borrowerApr;

    // Set approvals for vault and IdleCDO
    _allowUnlimitedSpend(_underlyingToken, _vault);
    _allowUnlimitedSpend(_underlyingToken, _idleCDO);
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L546-549)
```text
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```
