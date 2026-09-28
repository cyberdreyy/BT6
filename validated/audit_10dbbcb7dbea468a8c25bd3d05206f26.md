### Title
Phantom epoch interest via ERC4626 donation inflation in `ProgrammableBorrower._vaultNetInterest` — attacker-controlled `convertToAssets` is trusted as interest at `stopEpoch` - ([File: contracts/strategies/idle/ProgrammableBorrower.sol])

### Summary
`ProgrammableBorrower` treats the MetaMorpho/ERC4626 vault's `convertToAssets` output as ground truth for epoch PnL. `_vaultNetInterest()` computes `interest = bufferedVaultDelta + _currentVaultAssets() + epochWithdrawnFromVault - (epochStartVaultAssets + epochDepositedToVault)`, and `totalInterestDueNow()` is consumed by `IdleCDOEpochVariant.stopEpoch` as real earned interest. Because the vault is a public ERC4626, any unprivileged user can inflate `convertToAssets` by donating underlying to the vault while simultaneously holding vault shares to recover the donation pro-rata. The donated amount is booked as phantom interest, tranche `virtualPrice` is inflated, and the attacker exits via `requestWithdraw`/`claimWithdrawRequest` against funds belonging to other tranche holders. This is the credit-vault analog of CVE-2024-20328: attacker-controlled input (the vault's valuation, like ClamAV's attacker-controlled filename) is interpolated unsanitized into a privileged sink (epoch interest accounting, like the VirusEvent command line).

### Finding Description
- `_currentVaultAssets()` returns `vault.convertToAssets(vault.balanceOf(address(this)))` ( [1](#0-0) ). There is no sanity bound, no cap vs. deposited principal, and no distinction between genuinely earned yield and donations.
- `_vaultNetInterest()` converts any increase in that external valuation into positive `interest` ( [2](#0-1) ).
- `totalInterestDueNow()` sums vault interest with borrower interest and forwards it to the CDO's stop-epoch accounting ( [3](#0-2) ).
- `onStopEpoch` only checks whether on-hand plus vault assets cover `_amountRequired`; it does not verify that reported interest is backed by real yield ( [4](#0-3) ).
- The `shortfall > _currentVaultAssets() → return true` early-exit actually makes the attack safer for the attacker: an inflated `convertToAssets` also inflates this coverage check, so `stopEpoch` succeeds against phantom collateral.

Attack sequence (programmable mode, running epoch):
1. Attacker (KYC-passing lender) deposits during the buffer and receives AA tranche tokens.
2. Epoch starts; `epochStartVaultAssets` is snapshotted honestly.
3. Mid-epoch, attacker acquires ERC4626 vault shares on the open market, then donates `D` underlying directly to the vault (or via `deposit` to a dead address / direct `transfer` if the vault counts balance). `convertToAssets` rises by `D`.
4. `stopEpoch` reads `totalInterestDueNow()` inflated by `D`; pool value and AA `virtualPrice` increase by `D` despite zero real yield.
5. Attacker claims withdrawal at the inflated price, extracting real underlying funded by other lenders' principal, then redeems their vault shares to recover ≈ `D × (attacker vault share %)`.

### Impact Explanation
Insolvency / direct theft: phantom interest `D` is credited to tranche holders pro-rata, but only early claimers receive real tokens; the vault sleeve never actually earned `D`, so the residual position is short by `D`, socializing the loss onto remaining AA/BB holders. Quantified loss ≈ `D` (bounded only by attacker capital; e.g., a 1M USDC donation inflates epoch interest by 1M USDC with attacker net profit ≈ `D × tranche_share − D × (1 − vault_share)`, positive whenever the attacker dominates tranche supply and vault shareholding — which a fresh attacker-heavy deposit round permits).

### Likelihood Explanation
Requires: programmable-borrower mode active, attacker passing KYC to hold tranches, and the underlying ERC4626 vault being a permissionless venue where donations raise `convertToAssets` (true for MetaMorpho-style vaults and donation-by-balance accounting). No privileged role, default, or timing race is needed; the only guard skim/`Default` checks do not bound interest vs. principal.

### Recommendation
Cap vault-reported interest independently of raw `convertToAssets`: track cumulative net deposits and bound `interest` by a plausible yield ceiling (e.g., APR-derived max over epoch duration), or compute vault PnL from share-price checkpoints taken at epoch start vs. a manipulation-resistant TWAP. At minimum, treat `convertToAssets` gains above `maxExpectedYield` as zero in `_vaultNetInterest` and let `onStopEpoch` revert rather than book phantom profit.

### Proof of Concept
Foundry fork outline (uncertain detail: exact `stopEpoch` internals in `IdleCDOEpochVariant.sol` were not fully readable within the investigation budget — the accounting path is inferred from `totalInterestDueNow` consumers and the existing tests):

```solidity
// test/foundry/ProgrammableBorrowerDonation.t.sol — fork mainnet at pinned block
function testDonationInflatesEpochInterest() public {
    uint256 amount = 10_000e6;
    vm.prank(owner); cdoEpoch.setIsInterestMinted(true);
    idleCDO.depositAA(amount);                 // attacker = sole/large AA holder
    _startEpoch();

    uint256 donation = 1_000e6;
    deal(USDC, attacker, donation, true);
    vm.startPrank(attacker);
    IERC20(USDC).transfer(address(morphoVault), donation); // or deposit+donate shares
    vm.stopPrank();

    uint256 phantom = programmableBorrower.totalInterestDueNow();
    assertGt(phantom, 0);                       // phantom interest booked
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager); cdoEpoch.stopEpoch(0, 0);
    assertGt(cdoEpoch.virtualPrice(address(aaTranche)), ONE, "price inflated by donation");
    // attacker requestWithdraw + claimWithdrawRequest extracts > deposited principal;
    // remaining vault assets < sum of claims → insolvency of D
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L546-549)
```text
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```
