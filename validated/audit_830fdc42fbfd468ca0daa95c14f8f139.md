### Title
`ProgrammableBorrower.emergencyExitVault` can be DoS'd by front-running liquidity drain of the external ERC4626 vault - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The restricted-role `emergencyExitVault` function calls `vault.redeem(_shares, ...)` directly and reverts whenever the external ERC4626 vault lacks enough liquid assets. Any unprivileged user of that same ERC4626 vault can front-run the emergency exit by withdrawing (or borrowing, for lending vaults like Morpho) the available liquidity, forcing the redeem to revert and leaving the credit facility's funds stuck exactly when the operator is trying to rescue them.

### Finding Description
`ProgrammableBorrower.emergencyExitVault` is the emergency escape hatch intended to pull the facility's idle capital back out of the external ERC4626 vault during a security incident:

`contracts/strategies/idle/ProgrammableBorrower.sol` lines 361-372:

```solidity
function emergencyExitVault(uint256 _shares) external nonReentrant returns (uint256 assets) {
    _checkOnlyOwnerOrManager();
    if (_shares == 0) {
        _shares = vault.balanceOf(address(this));
    }
    if (_shares == 0) revert InvalidAmount();
    assets = vault.redeem(_shares, address(this), address(this));
    ...
}
``` [1](#0-0) 

`redeem` on a real ERC4626 (e.g. a Morpho vault) burns shares and pulls `convertToAssets(shares)` assets from the vault's liquid balance. If the vault's available liquidity is lower than the requested asset amount at execution time, the redeem reverts inside the external vault and `emergencyExitVault` propagates the revert.

The same code already acknowledges this failure mode elsewhere: `onStopEpoch` wraps the identical withdrawal in `try/catch` and reverts with the explicit `StopEpochVaultLiquidityUnavailable` error precisely because external-vault liquidity is outside the protocol's control:

`contracts/strategies/idle/ProgrammableBorrower.sol` lines 246-253:

```solidity
try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
    ...
} catch {
    revert StopEpochVaultLiquidityUnavailable();
}
``` [2](#0-1) 

`emergencyExitVault` has no such handling and no clamping to `vault.maxRedeem`/`vault.maxWithdraw`, so a liquidity shortfall blocks the call entirely. This is the same bug class as the external report: a privileged emergency-exit call parameterized on the *current* claim (`_shares` resolved to `vault.balanceOf`, redeemable for the *current* liquidity) is invalidated by an unprivileged transaction that reduces the available claim between mempool observation and execution.

### Impact Explanation
Emergency exit is only invoked under perceived material risk to user funds. An attacker who observes the emergency-exit transaction (or who simply wants to keep the facility's funds exposed, e.g. to keep borrow liquidity available or to grief during a vault incident) can:

1. Front-run `emergencyExitVault` and call `withdraw`/`borrow` on the shared ERC4626 vault to drain its liquid balance to dust.
2. `vault.redeem` then reverts (`ERC4626: redeem more than max` / insufficient liquidity in the underlying market).
3. The attack is repeatable at the cost of gas and, for lending vaults, a borrow that can be repaid later — keeping the facility's capital locked for as long as an attacker is willing to fund the position.

Result: temporary freezing of the pool's funds (up to the full vault position, e.g. the entire undrawn epoch principal parked via `_depositToVault`) during the exact window where emergency exit matters. Loss magnitude: 100% of `vaultSharesBalance()` temporarily locked; potentially permanent if the underlying emergency is time-sensitive (e.g. the vault itself is being exploited and liquidity is genuinely gone).

### Likelihood Explanation
- The attacker is fully unprivileged: any EOA interacting with the external ERC4626 vault — explicitly in scope ("a user of the programmable borrower's ERC4626 vault").
- Trigger cost is low: one `withdraw`/`borrow` transaction plus gas. For a Morpho-style vault, draining liquidity only requires borrowing against collateral, which can be unwound afterwards.
- No existing guard prevents it: `nonReentrant` and `onlyOwnerOrManager` do not help; the redeem target is external shared liquidity. `onStopEpoch`'s try/catch shows the codebase treats vault liquidity failure as realistic.
- Owner/manager are honest, but the honest action itself is what's being griefed — identical threat model to the source report.

### Recommendation
Clamp the redeem amount to the currently executable size and/or treat partial liquidity as success, mirroring the source report's fix (`claimToExit > state.totalPoolClaim → claimToExit = state.totalPoolClaim`):

```diff
function emergencyExitVault(uint256 _shares) external nonReentrant returns (uint256 assets) {
    _checkOnlyOwnerOrManager();
    uint256 maxRedeemable = vault.maxRedeem(address(this));
    if (_shares == 0 || _shares > maxRedeemable) {
        _shares = maxRedeemable;
    }
    if (_shares == 0) revert InvalidAmount();
    assets = vault.redeem(_shares, address(this), address(this));
    ...
}
```

This guarantees the emergency exit always pulls out whatever liquidity is actually available instead of reverting when the full share balance temporarily exceeds redeemable liquidity. Alternatively wrap `vault.redeem` in `try/catch` and fall back to `vault.withdraw(vault.maxWithdraw(address(this)), ...)`.

### Proof of Concept
Foundry fork test (based on the existing `test/foundry/ProgrammableBorrowerCreditVault.t.sol` setup with a real Morpho ERC4626 vault):

```solidity
function test_FrontRunDrain_blocks_emergencyExitVault() external {
    uint256 amount = 10_000 * oneScale;
    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);
    idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);           // parks funds in the ERC4626 vault

    uint256 shares = morphoVault.balanceOf(address(programmableBorrower));
    assertGt(shares, 0);

    // Unprivileged front-run: attacker drains vault liquidity
    address attacker = makeAddr("attacker");
    deal(address(morphoVault), attacker, 1); // or deposit shares
    uint256 liq = underlying.balanceOf(address(morphoVault)); // available liquidity
    // attacker withdraws max / borrows max so that redeem(shares) exceeds available
    vm.prank(attacker);
    morphoVault.withdraw(liq, attacker, attacker);   // or borrow on the market

    // Owner's emergency exit now reverts inside vault.redeem
    vm.prank(owner);
    vm.expectRevert();                       // ERC4626 redeem > maxRedeem / liquidity
    programmableBorrower.emergencyExitVault(shares);
    // same for the 0 = "redeem all" path:
    vm.prank(owner);
    vm.expectRevert();
    programmableBorrower.emergencyExitVault(0);
}
```

With the recommended clamp, `emergencyExitVault` would instead redeem `maxRedeem` shares and recover whatever liquidity remains, fulfilling the caller's intent as best as possible during the emergency.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L246-253)
```text
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
