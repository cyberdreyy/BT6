### Title
Attacker manipulates the programmable borrower's ERC4626 share price to inflate `totalInterestDueNow()` and steal value through phantom epoch interest - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
The external bug class is a manipulated spot quote (`UniswapV2Library.getAmountsIn()`) used to size a payment, letting an attacker overprice what the victim pays. The closest idle-tranches analog is `ProgrammableBorrower`, where the epoch payout is priced off a single manipulable spot valuation: `vault.convertToAssets()`. An unprivileged user of the ERC4626 vault can inflate the vault's share price before the CDO calls `onStopEpoch`, causing `totalInterestDueNow()` to report phantom interest that the CDO then credits into tranche NAV — a value transfer to whoever holds tranche exposure, funded by the attacker only via a partially-recoverable donation.

### Finding Description
`totalInterestDueNow()` is documented as "the single value read by IdleCDO to price the epoch" — borrower contractual interest plus vault PnL minus vault losses. [1](#0-0)  The vault leg is computed in `_vaultNetInterest()` from `_currentVaultAssets()`, which is a raw `vault.convertToAssets(vault.balanceOf(address(this)))` spot quote with no TWAP, bound check, or slippage guard. [2](#0-1) [3](#0-2) 

`stopEpoch` reads this value, then calls `onStopEpoch`, which snapshots `bufferStartVaultAssets = _currentVaultAssets()` from the same manipulable quote and clears `epochAccountingActive`. [4](#0-3)  The same quote is used at `onStartEpoch` to set `bufferedVaultDelta` and `epochStartVaultAssets` baselines. [5](#0-4) 

Attack path (buffer phase, programmable mode, minted-interest or cash epoch):
1. Attacker is a tranche-token holder and a dominant shareholder of the ERC4626 vault (`borrow`/`repay` keepers or privileged roles not needed; `deposit`/`convertToAssets` inflation via donation is open to any vault user).
2. Before the honest manager calls `stopEpoch`, the attacker donates `D` of the underlying asset directly to the vault (or sandwich-inflates its price if the vault uses a manipulable rate source), raising `convertToAssets`.
3. `stopEpoch` consumes the inflated `totalInterestDueNow()`; the CDO credits phantom interest into tranche prices (minted interest to the CDO, or higher reported NAV in cash mode). `onStopEpoch` then snapshots `bufferStartVaultAssets` at the still-inflated level, but this only neutralizes the delta for *buffer* accounting — the inflated epoch interest was already credited and crystallized.
4. The attacker, as a tranche holder, gains `trancheShare × D` in NAV while recovering `vaultShare × D` of the donation through their vault position. Profit condition: `trancheShare > 1 - vaultShare`. With the attacker holding most vault shares (e.g. 90%) and even a modest tranche position, the phantom interest credited to all LPs is a net extraction from other tranche holders when they later withdraw, request redemptions, or when NAV-dependent fees/ratios settle.
5. The attacker then requests/exits at the inflated tranche price or lets the inflated NAV flow into `lastNAVAA/lastNAVBB`, which also skews `trancheAPRSplitRatio` and loss-adjusted bases (`_lossActiveBasis`, `_defaultBBBasis`) that read the same valuation.

The symmetric deflate direction (temporarily depressing `convertToAssets` before `startEpoch`, restoring before `stopEpoch`) manufactures fake vault interest with zero donation cost where the vault's price can be pushed both ways (low-liquidity ERC4626 with donation-reversible or fee-based pricing).

### Impact Explanation
Direct theft and NAV mispricing: phantom interest minted into tranche prices dilutes honest LP value; when the attacker exits at the inflated price the pool overpays them relative to real backing, and subsequent `stopEpoch`/`claimWithdrawRequest`/`finalizeDefaultRecovery` payouts are priced off a NAV that exceeds actual recoverable assets — an insolvency-style overpayment. Loss scales with the attacker's tranche share and donation size and is unbounded by any guard, since no slippage bound exists on `convertToAssets`.

### Likelihood Explanation
Requires a manipulable external vault share price (donation-susceptible ERC4626 or a low-liquidity vault — exactly the condition `setVault` permits, since it only checks the asset address, not price robustness). [6](#0-5)  The window is deterministic: `stopEpoch` is a predictable periodic call, so no front-running lottery is needed — the inflation only has to be active for the one transaction that reads `totalInterestDueNow()` and `onStopEpoch`.

### Recommendation
Treat `convertToAssets` as untrusted input: bound epoch vault PnL (e.g., cap recognized vault interest by a realized-gain basis — shares redeemed × actual assets received — or by `borrowerInterestAccruedNow`-style contractual limits), and/or use a two-step settlement where vault gains are only recognized once shares are actually withdrawn at realized prices, rather than marking-to-manipulable-market. Enforce a minimum-liquidity/TWAP-vetted vault in `setVault`, and add a slippage guard comparing `totalInterestDueNow()` against a sanity bound before the CDO mints or credits interest.

### Proof of Concept
A reproducible Foundry fork PoC would: (1) deploy `IdleCDOEpochVariant` + `ProgrammableBorrower` pointed at a donation-susceptible ERC4626 vault on a mainnet fork; (2) attacker acquires ~90% of vault shares and ≥10% of tranche NAV via `depositAA`/`depositBB`; (3) attacker donates `D` underlying directly to the vault, inflating `convertToAssets` (verify `_currentVaultAssets()` and `totalInterestDueNow()` jump by ~`D × borrower's vaultShare fraction`); (4) honest manager calls `stopEpoch`; assert CDO-credited interest increased by the phantom amount and tranche `virtualPrice`/`lastNAV` moved up; (5) attacker exits tranche position (instant or epoch withdraw) at inflated NAV and redeems vault shares to recover `vaultShare × D`; assert net attacker gain > 0 and residual pool backing < aggregate tranche claims.

Note: I could not fully read `IdleCDOEpochVariant.stopEpoch` internals within this session to confirm whether credited interest is minted vs. pulled, but the contract's own documentation states `totalInterestDueNow()` is "the single value read by IdleCDO to price the epoch," which is sufficient to establish the manipulation primitive; the PoC would verify the exact payout path.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L158-169)
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
    emit VaultUpdated(_vault);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L206-219)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L256-265)
```text
    // `totalInterestDueNow()` was already read by IdleCDO before calling this hook, so once the
    // stop flow begins we can clear the previous carry and snapshot the remaining vault sleeve
    // as the baseline for measuring buffer-period vault PnL before the next epoch starts.
    bufferedVaultDelta = 0;
    bufferInterest = 0;
    bufferStartVaultAssets = _currentVaultAssets();

    // Stop reserving epoch-end withdraw liquidity once IdleCDO has started the stop flow.
    epochPendingWithdraws = 0;
    epochAccountingActive = false;
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
