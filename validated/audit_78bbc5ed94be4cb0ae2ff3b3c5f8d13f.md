### Title
ProgrammableBorrower trusts unseeded ERC4626 deposits, allowing first-depositor inflation to steal epoch principal - (contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The programmable-borrower integration accepts any ERC4626 vault whose `asset()` matches the pool token and deposits all idle epoch cash into that vault with no minimum-share check, no seed/liquidity check, and no slippage bound on `deposit`. If the configured vault is empty or economically empty and uses balance-based `totalAssets`, an unprivileged vault user can front-run the first `onStartEpoch` deposit with the classic 1-share + donation inflation pattern so the borrower adapter mints near-zero shares for the LP principal. The stolen principal is then recorded as a vault loss instead of being rejected.

### Finding Description
`initialize` and `setVault` validate only that the vault address is nonzero and that `IERC4626(_vault).asset()` equals the CDO underlying; they do not check `totalSupply`, `totalAssets`, decimals offset, previewed share output, or whether the vault is already safely seeded [1](#0-0) [2](#0-1) . During `onStartEpoch`, the adapter snapshots `startAssets` from cash plus `convertToAssets`, then calls `_depositToVault` with the full idle balance and `_principalAssets = 0` [3](#0-2) . `_depositToVault` calls `vault.deposit(_assetAmount, address(this))` and uses the returned `shares` only for an event; it never requires `shares > 0`, compares against `previewDeposit`, or enforces a minimum [4](#0-3) .

In an empty balance-accounted ERC4626, the attacker deposits `1` wei to hold the only share, then transfers enough underlying to the vault so the share price exceeds the upcoming LP deposit. When `onStartEpoch` executes, the adapter's deposit returns `0` or dust shares while the underlying remains inside the vault and becomes claimable by the attacker's single share through `redeem`. The adapter still sets `epochStartVaultAssets` to the pre-deposit cash amount, so the post-deposit `_currentVaultAssets()` collapse is interpreted by `_vaultNetInterest` as `loss` rather than as an invalid deposit [5](#0-4) [6](#0-5) .

### Impact Explanation
The direct theft is the first epoch deposit routed through `_depositToVault`: the attacker redeems their controlling share for attacker seed plus the LP principal. The pool-facing effect is insolvency rather than a mere revert, because `totalInterestDueNow` is computed from `vaultInterest + borrowerInterestAccruedNow + bufferInterest - loss`; once the donated-then-stolen deposit collapses the vault position, `totalInterestDueNow` returns `0` or a materially reduced value [7](#0-6) . `IdleCDOEpochVariant` treats `totalInterestDueNow()` as the source of truth for programmable mode [8](#0-7) , then proceeds through the minted-interest path and `_updateAccounting()` on successful borrower funding [9](#0-8) . The loss is therefore surfaced to tranche NAV through the normal accounting path rather than being contained at deposit time.

### Likelihood Explanation
Likelihood is conditional rather than universal: the attack requires the configured ERC4626 to be empty or near-empty at the first programmatic epoch, to use balance-based `totalAssets`, and to lack inflation-resistant decimals/offsets or deposit guards. Those conditions are exactly the unsafe ERC4626 dependency case this integration permits, because owner/manager honesty only means they did not intend a bad vault; it does not make an unseeded vault resistant to its public depositors. If the production-configured vault is a mature Morpho-style vault with material supply, virtual offsets, and no attacker-controllable first deposit, this specific path is not exploitable; the finding is that the adapter encodes no defense when a weaker vault is selected.

### Recommendation
Require a safe vault condition before enabling programmable mode: enforce `vault.totalSupply() != 0` and a minimum liquidity/price invariant in `initialize`/`setVault`, or require the owner to seed the vault before the first epoch. In `_depositToVault`, compute expected shares with `previewDeposit`, require `shares >= minShares` and `shares != 0`, and treat a material deposit/withdrawal basis mismatch as `NotAllowed` instead of letting `_vaultNetInterest` classify it as epoch loss. Prefer using `previewRedeem`/`maxWithdraw` style checks for liquidity-sensitive stops rather than fee-excluded `convertToAssets` alone.

### Proof of Concept
```solidity
// test/foundry/ProgrammableBorrowerFirstDepositor.t.sol
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";

contract Inflatable4626 is IERC20, IERC20Metadata, IERC4626 {
    IERC20Detailed public assetToken;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    constructor(address _asset) { assetToken = IERC20Detailed(_asset); }
    function asset() external view returns (address) { return address(assetToken); }
    function totalAssets() public view returns (uint256) { return assetToken.balanceOf(address(this)); }
    function convertToAssets(uint256 shares) external view returns (uint256) {
        return totalSupply == 0 ? shares : shares * totalAssets() / totalSupply;
    }
    function convertToShares(uint256 assets) external view returns (uint256) {
        uint256 supply = totalSupply;
        return supply == 0 ? assets : assets * supply / totalAssets();
    }
    function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
        uint256 supply = totalSupply;
        shares = supply == 0 ? assets : assets * supply / totalAssets(); // vulnerable: no offset, no min shares
        SafeERC20Upgradeable.safeTransferFrom(assetToken, msg.sender, address(this), assets);
        totalSupply += shares;
        balanceOf[receiver] += shares;
    }
    function redeem(uint256 shares, address receiver, address owner) external returns (uint256 assets) {
        assets = shares * totalAssets() / totalSupply;
        totalSupply -= shares;
        balanceOf[owner] -= shares;
        SafeERC20Upgradeable.safeTransfer(assetToken, receiver, assets);
    }
    // Remaining IERC4626/IERC20 functions omitted in the PoC sketch; implement standard stubs.
}

// Fork/test sequence:
// 1. Deploy Inflatable4626 for USDC with totalSupply == 0.
// 2. Initialize IdleCDOEpochVariant + IdleCreditVault + ProgrammableBorrower with that vault.
// 3. KYC/deposit LP principal P into the CDO during buffer.
// 4. Attacker, an unprivileged vault user, front-runs manager.startEpoch():
//    - vault.deposit(1 wei, attacker) -> attacker has the only share.
//    - transfer P + 1 wei underlying directly into the vault, so 1 share > P + P value.
// 5. Manager calls startEpoch(); onStartEpoch deposits P through _depositToVault and mints ~0 shares.
// 6. Attacker calls vault.redeem(1 share, attacker, attacker) and receives seed + P.
// 7. Advance past epochEndDate and call stopEpoch(0, 0); ProgrammableBorrower reports vaultLoss/P ~= P and totalInterestDueNow is suppressed, so the theft is realized as LP loss instead of reverting the deposit.
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L118-126)
```text
    address _underlyingToken = IIdleCDOToken(_idleCDO).token();
    if (_underlyingToken == address(0) || IERC4626(_vault).asset() != _underlyingToken) revert InvalidAddress();

    __Ownable_init();
    __ReentrancyGuard_init();
    transferOwnership(_owner);

    underlyingToken = IERC20Detailed(_underlyingToken);
    vault = IERC4626(_vault);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L158-168)
```text
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L206-222)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-334)
```text
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L378-384)
```text
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-436)
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
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
      // Only settle borrower interest when CDO is fronting it (minted mode, not closing pool).
      // When requesting all funds (_interest == 1) the CDO pulls cash directly, no fronting.
      if (_mintInterest && isProgrammableBorrower) {
        IProgrammableBorrower(_borrower()).settleBorrowerInterest();
      }
      // Split pending withdraw fees before update accounting
      // NOTE: Fees are sent with 2 different transfer calls, here and after updateAccounting, to avoid complicated calculations
      if (!_mintInterest) {
        _transferFeeUnderlyings(_pendingWithdrawFees);
      }

      if (_isRequestingAllFunds) {
        // we already have strategyTokens equal to _totBorrowed in this contract
        // so we transfer _totBorrowed to the strategy to avoid double counting for getContractValue
        _transferUnderlyings(address(_strategy), _totBorrowed);
      }

      if (_mintInterest) {
        // if interest is not transferred we mint strategy tokens equal to the full epoch interest
        if (_grossInterest != 0) _strategy.mintStrategyTokens(_grossInterest);
        // and increase unclaimedFees by pending withdraw fees before _updateAccounting
        unclaimedFees += _pendingWithdrawFees;
      }

      // update tranche prices and unclaimed fees
      _updateAccounting();
```

**File:** contracts/IdleCDOEpochVariant.sol (L997-1005)
```text
  /// @notice Resolve the stop-epoch interest value, optionally sourcing it from a programmable borrower.
  /// @dev Programmable borrowers are always the source of truth for epoch interest.
  /// In that mode `_interest` values `0` and `1` both resolve to the realized epoch interest,
  /// while `1` still separately signals the close-pool path to the caller.
  function _resolveStopEpochInterest(uint256 _interest) internal view returns (uint256 _resolvedInterest) {
    if (isProgrammableBorrower) {
      _checkNotAllowed(_interest > 1);
      return IProgrammableBorrower(_borrower()).totalInterestDueNow();
    }
```
