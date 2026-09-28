### Title
Transient ERC4626 illiquidity can indefinitely block epoch settlement and withdrawals - ([File: `contracts/strategies/idle/ProgrammableBorrower.sol`](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower.onStopEpoch` treats a covered but failing ERC4626 withdrawal as a retryable error and reverts with `StopEpochVaultLiquidityUnavailable`. Because `IdleCDOEpochVariant._stopEpoch` lets that hook revert bubble, an unprivileged vault liquidity user can exhaust the vault’s immediately withdrawable liquidity while leaving its shares economically valuable, temporarily freezing all pool settlement and withdrawal funding.

### Finding Description
When IdleCDO requires more underlying than `ProgrammableBorrower` currently holds, `onStopEpoch` calculates the shortfall and compares it with the ERC4626 position’s `convertToAssets` value. If the shares nominally cover the shortfall but `vault.withdraw` reverts due to unavailable liquid assets, the function catches that call only to rethrow `StopEpochVaultLiquidityUnavailable`. [1](#0-0) 

`IdleCDOEpochVariant._stopEpoch` invokes this hook before calling `getFundsFromBorrower`; therefore the custom revert aborts the entire stop transaction rather than reaching the borrower-default path. [2](#0-1) 

An attacker who is an ordinary user of the configured ERC4626 vault can consume its available cash before the stop call—for example, by redeeming or borrowing the last withdrawable liquidity—while leaving `convertToAssets` high because the vault still accounts for illiquid lent or deployed assets. Every subsequent `stopEpoch` call reverts until another vault participant restores liquidity.

This breaks the epoch-state-machine invariant that a borrower funding outcome must resolve to either a successful stop or a handled default. It also prevents pending withdrawal receipts from being funded because `collectWithdrawFunds` is only reached after the hook succeeds. [3](#0-2) 

### Impact Explanation
The attack temporarily freezes up to the vault’s active NAV plus pending withdrawal obligations: `cdo.getContractValue() + IdleCreditVault.pendingWithdraws()`. Tranche holders cannot settle the current epoch, pending withdrawers cannot receive funded receipts, and the pool cannot cleanly enter its default-recovery path while the external vault remains illiquid.

The impact is a denial of service with direct fund impact because the frozen assets are pool and withdrawal funds, not merely gas or an unavailable view. The attacker does not need to make the vault insolvent; maintaining zero or insufficient instantly withdrawable liquidity is sufficient to keep settlement blocked.

### Likelihood Explanation
Likelihood depends on the configured ERC4626 vault exposing liquidity that unprivileged users can consume. The attacker model explicitly includes users of the programmable borrower’s ERC4626 vault, and many lending-style ERC4626 vaults can have high reported assets but little immediately withdrawable cash.

The required sequence is straightforward: wait for a running epoch, drain the vault’s liquid asset balance before its scheduled stop, and leave enough accounting value in the shares for the `shortfall <= _currentVaultAssets()` check to pass. Honest managers can retry after liquidity returns, but the code provides no deadline, fallback, or automatic transition to loss/default handling.

### Recommendation
Do not allow transient ERC4626 withdrawal failures to remain an indefinitely repeatable stop-epoch revert. Track the first liquidity failure and impose a bounded recovery window. For example:

```solidity
if (stopLiquidityFailedAt == 0) {
    stopLiquidityFailedAt = block.timestamp;
    revert StopEpochVaultLiquidityUnavailable();
}
if (block.timestamp < stopLiquidityFailedAt + liquidityGracePeriod) {
    revert StopEpochVaultLiquidityUnavailable();
}
return false;
```

The grace period should be configured by governance. After it expires, IdleCDO should enter an explicit default or loss-finalization path so LP and pending-withdrawal claims are not trapped by third-party vault liquidity indefinitely. The implementation should also document that `convertToAssets` measures economic coverage, not immediately available liquidity.

### Proof of Concept
The following Foundry fork test uses an existing ERC4626 participant with enough shares to drain the vault’s available cash. The important assertion is that the external position still nominally covers the stop-epoch shortfall, which forces execution into the caught withdrawal failure rather than the insufficient-assets branch.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";

interface IERC20Like {
    function balanceOf(address account) external view returns (uint256);
}

interface IERC4626Like {
    function asset() external view returns (address);
    function balanceOf(address account) external view returns (uint256);
    function convertToAssets(uint256 shares) external view returns (uint256);
    function withdraw(
        uint256 assets,
        address receiver,
        address owner
    ) external returns (uint256);
}

interface IProgrammableBorrowerLike {
    function vault() external view returns (IERC4626Like);
    function idleCDO() external view returns (address);
    function borrower() external view returns (address);
}

interface IIdleCDOEpochLike {
    function strategy() external view returns (address);
    function token() external view returns (address);
    function epochEndDate() external view returns (uint256);
    function isEpochRunning() external view returns (bool);
    function expectedEpochInterest() external view returns (uint256);
    function stopEpoch(uint256 newApr, uint256 interest) external;
}

interface IIdleCreditVaultLike {
    function pendingWithdraws() external view returns (uint256);
    function manager() external view returns (address);
}

contract ProgrammableBorrowerLiquidityDoSTest is Test {
    error StopEpochVaultLiquidityUnavailable();

    function test_Erc4626IlliquidityBlocksStopEpoch() external {
        vm.createSelectFork(vm.envString("FORK_RPC_URL"), vm.envUint("FORK_BLOCK"));

        IProgrammableBorrowerLike programmableBorrower =
            IProgrammableBorrowerLike(vm.envAddress("PROGRAMMABLE_BORROWER"));
        IIdleCDOEpochLike cdo = IIdleCDOEpochLike(programmableBorrower.idleCDO());
        IIdleCreditVaultLike creditVault = IIdleCreditVaultLike(cdo.strategy());
        IERC4626Like vault = programmableBorrower.vault();
        IERC20Like underlying = IERC20Like(cdo.token());

        require(cdo.isEpochRunning(), "epoch must be running");
        require(vault.asset() == cdo.token(), "vault asset mismatch");

        uint256 onHand = underlying.balanceOf(address(programmableBorrower));
        uint256 required =
            cdo.expectedEpochInterest() + creditVault.pendingWithdraws();
        uint256 shortfall = required - onHand;
        require(required > onHand, "test needs a vault withdrawal");

        // A large ordinary vault user withdraws/borrows all immediately available cash.
        // They retain no special role in IdleCDO or ProgrammableBorrower.
        address drainingVaultUser = vm.envAddress("VAULT_LIQUIDITY_DRAINER");
        uint256 idleCash = underlying.balanceOf(address(vault));
        require(idleCash != 0, "vault already illiquid");

        vm.prank(drainingVaultUser);
        vault.withdraw(idleCash, drainingVaultUser, drainingVaultUser);

        assertEq(
            underlying.balanceOf(address(vault)),
            0,
            "vault cash was not drained"
        );

        // The remaining shares are economically valuable, so the borrower enters
        // the withdrawal path instead of its insufficient-coverage path.
        uint256 shares = vault.balanceOf(address(programmableBorrower));
        uint256 accountedAssets = vault.convertToAssets(shares);
        assertGe(
            accountedAssets,
            shortfall,
            "shares must nominally cover the stopEpoch shortfall"
        );

        vm.warp(cdo.epochEndDate() + 1);

        vm.prank(creditVault.manager());
        vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
        cdo.stopEpoch(0, 0);

        // No default was recorded and no withdrawal funds were collected: the
        // transaction reverted before IdleCDO could reach either settlement path.
        assertTrue(cdo.isEpochRunning(), "epoch remains unsettled");
    }
}
```

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

**File:** contracts/IdleCDOEpochVariant.sol (L395-405)
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

**File:** contracts/IdleCDOEpochVariant.sol (L406-411)
```text
    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
      // Only settle borrower interest when CDO is fronting it (minted mode, not closing pool).
```
