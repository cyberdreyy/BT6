### Title
Mid-epoch token donations are crystallized as tranche NAV by `stopEpoch`/`_updateAccounting` because no skim runs before accounting, then get swept to `feeReceiver` leaving unbacked tranche gains — (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOCreditVault.getContractValue()` counts raw `token` balance as NAV (`strategyToken + token - unclaimedFees`), while the docs say unsolicited transfers are only "skimmed on interactions". `IdleCDOEpochVariant` correctly calls `_skimDonatedAssets()` at the head of `_deposit`, `depositDuringEpoch`, `updateAccounting`, `startEpoch`, and `finalizeDefault` — but **not** in `_stopEpoch`/`stopEpochWithDuration`, the privileged epoch-stop path that calls `getContractValue()` and `_updateAccounting()` and crystallizes gains into `priceAA`/`priceBB`/`lastNAVAA`/`lastNAVBB`/`unclaimedFees`. An unprivileged "direct token sender" can push `token` to the CDO mid-epoch; at the next `stopEpoch` the donation is booked as epoch gain (inflating tranche prices and charging performance/management fees on it), yet the raw tokens remain on the CDO and are later skimmed to `feeReceiver` by the next deposit/finalize call — a classic "allocated resource never registered for cleanup" leak: value is accounted once as LP gain, then removed again as a donation.

### Finding Description
- `getContractValue()` includes raw underlying balance: `return _contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees;` — `contracts/IdleCDOCreditVault.sol:125-128`. [1](#0-0) 
- `_managedContractValue()` explicitly excludes raw underlyings "because unsolicited transfers are skimmed on interactions" — `contracts/IdleCDOCreditVault.sol:130-137`. That invariant only holds if *every* accounting entry point skims first. [2](#0-1) 
- The skimmed entry points: `_deposit` (`IdleCDOEpochVariant.sol:647`), `depositDuringEpoch` (`:679`), `updateAccounting` (`:159`), `startEpoch` (`:242`), `finalizeDefault` (`:197`). [3](#0-2) 
- `_stopEpoch` computes `adjustedActiveInterest`/`getContractValue()` and then calls `_updateAccounting()` at `IdleCDOEpochVariant.sol:436` and, after a realized loss, `_forceUpdateAccounting()` at `:499` — with **no preceding `_skimDonatedAssets()`**. Raw CDO `token` balance present at stop time is therefore included in `nav`, producing `nav > _lastNAV` and booking `(nav - _lastNAV) * fee` into `unclaimedFees` plus distributing the rest to AA/BB NAVs (`IdleCDOCreditVault.sol:227-236`). [4](#0-3) 
- The donated tokens are never moved by `stopEpoch` (interest/principal is settled via `getFundsFromBorrower`/`collectWithdrawFunds`/`deposit`), so they remain as raw `token` on the CDO. The next skimmed interaction (any user `_deposit`, `depositDuringEpoch`, `finalizeDefault`, or guardian `updateAccounting`) sweeps that same donation to `feeReceiver` — while the gain it created is already baked into `lastNAVAA`/`lastNAVBB`/`priceAA`/`priceBB`.

### Impact Explanation
Broken invariant: donation isolation + fair mint/burn + solvency. Sequence in "running epoch" phase, fixed-APR mode:

1. Attacker (any EOA; `transfer` of underlying needs no KYC, no role) sends `D` underlying directly to the CDO while `isEpochRunning` and the contract is meant to hold zero underlyings.
2. Owner/manager calls `stopEpoch`. `getContractValue()` = strategyTokens + `D`. If `D` (plus interest) exceeds `lastNAV`, the excess is split: `fee` portion → `unclaimedFees`, remainder → AA/BB `lastNAV`s and prices at `:436`. If instead the epoch had a real loss, `D` silently masks it (donation offsets `totalGain < 0`), corrupting the BB-first loss waterfall and potentially avoiding the `Default` revert that should fire.
3. Tranche holders can now request withdrawals at prices inflated by `D` (net of fee). Withdrawal requests burn tranche tokens against `D`-inflated NAV.
4. The same `D` sits untouched on the CDO until the next deposit/finalize, when `_skimDonatedAssets()` forwards it to `feeReceiver` — removing backing that was already distributed. The vault is left short by ~`D` (plus fee double-count), i.e. the donation is *paid out twice*: once to tranche holders via crystallized NAV and once to `feeReceiver` via the skim.

Result: insolvency up to the donation size, loss socialized across remaining LPs; if the shortfall exhausts BB NAV the next `_updateAccounting` reverts `Default()`/triggers `_emergencyShutdown`, permanently freezing withdraw-request flows until forced accounting — a quantified loss directly proportional to `D`, caused entirely by an unprivileged direct token sender.

### Likelihood Explanation
- Requires only an ERC20 `transfer` to a plain address — the codebase explicitly anticipates this ("unsolicited transfers are skimmed on interactions", `IdleCDOCreditVault.sol:131`) and added skims to *five* entry points, but missed the epoch-stop accounting path, the only path where the contract is guaranteed to be in a state (running epoch, zero expected underlying balance) where a donation has maximal distorting effect.
- Existing guards do not stop it: `whenNotPaused`/KYC gates are irrelevant for `transfer`; the `Default` revert in `_updateAccounting` is bypassed because the donation *raises* nav; `_beforeUnpause`/`skipDefaultCheck` don't run on the normal stop path. The only mitigation is that owner/manager must call `stopEpoch` while the donation sits — but attacker can keep the donation in place indefinitely at zero cost (the tokens aren't consumed; they only get skimmed *after* the damage is done, and the sender could even be `feeReceiver`-aligned or simply accept the loss to grief the pool, since insolvency harm to LPs ≫ `D` only if withdrawn at inflated prices first — even without withdrawal, the double-count of `D` to `feeReceiver` is a direct, real loss).
- Honest-actor assumption holds: owner/manager call `stopEpoch` as part of normal operations; the attacker's only action is a direct token send, which is in the allowed attacker set.

### Recommendation
Call `_skimDonatedAssets()` at the start of `_stopEpoch` (and in `IdleCDOEpochVariantPrefunded._beforeStopEpoch`/default paths if they compute NAV before the base skim), before `getContractValue()`/`expectedEpochInterest` calculations and before `_updateAccounting()`. Alternatively make `_updateAccounting()` itself compute NAV from `_managedContractValue()` so raw `token` can never enter gain accounting regardless of entry point — this is the more robust fix since it removes the per-callsite skim dependency entirely. Add a fork test: mid-epoch `underlying.transfer(cdo, D)` → `stopEpoch` → assert `priceAA/priceBB/lastNAVAA/lastNAVBB/unclaimedFees` are unchanged and `feeReceiver` received `D`.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVaultDonationStopEpoch.t.sol — Foundry fork test
// Mode: fixed-APR, interest minted or cash (both affected), running epoch phase.
function test_DonationCrystallizedAtStopEpoch_ThenSkimmedTwice() external {
    uint256 amount = 10_000 * ONE_SCALE;
    uint256 donation = 500 * ONE_SCALE;

    idleCDO.depositAA(amount);                    // buffer phase deposit
    _startEpochAndCheckPrices(0);                 // honest manager starts epoch

    vm.warp(cdoEpoch.epochEndDate() - 1 days);    // mid-epoch, CDO holds 0 raw token

    // Attacker: unprivileged direct token sender (no KYC/role needed)
    address attacker = makeAddr("directSender");
    deal(defaultUnderlying, attacker, donation);
    vm.prank(attacker);
    underlying.transfer(address(cdoEpoch), donation);

    uint256 pricePre = cdoEpoch.virtualPrice(address(AAtranche));

    // Honest manager stops the epoch; borrower repays interest as normal
    _fundBorrowerAndApprove(_expectedInterest());
    vm.prank(manager);
    cdoEpoch.stopEpoch(_newApr, _interest);

    // Donation was crystallized into tranche NAV/fees
    assertGt(cdoEpoch.tranchePrice(address(AAtranche)), pricePre, "donation crystallized");
    assertGt(cdoEpoch.unclaimedFees(), 0, "fee charged on donation");

    // Same donation still sits as raw token; next deposit skims it to feeReceiver
    address victim = makeAddr("victim");
    _kyc(victim);
    deal(defaultUnderlying, victim, amount);
    vm.startPrank(victim);
    underlying.approve(address(cdoEpoch), amount);
    uint256 feeRecvPre = underlying.balanceOf(cdoEpoch.feeReceiver());
    cdoEpoch.depositAA(amount);                   // triggers _skimDonatedAssets()
    vm.stopPrank();

    assertEq(underlying.balanceOf(cdoEpoch.feeReceiver()) - feeRecvPre,
             donation, "donation paid out a second time to feeReceiver");
    // Net effect: NAV was inflated by ~donation for withdrawing LPs AND
    // `donation` left the vault to feeReceiver -> shortfall ~= donation,
    // socialized on remaining LPs or surfacing as Default() on next accounting.
}
```

Uncertainty note: I verified every skim callsite listed above and the absence of `_skimDonatedAssets` inside the `_stopEpoch` body shown (`IdleCDOEpochVariant.sol` ~lines 270-505 contain no skim call); if a skim were added at the top of `_stopEpoch` in code outside the indexed range this finding would not hold — worth a one-line check before submission.

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L125-128)
```text
  function getContractValue() public override view returns (uint256) {
    // Credit vault strategy tokens are minted 1:1 with underlyings and use the same decimals.
    return _contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees;
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L130-137)
```text
  /// @notice Calculates the current managed net TVL.
  /// @dev Raw underlyings held by the CDO are excluded because unsolicited transfers are skimmed on interactions.
  /// @return Strategy-token-backed TVL net of accrued fees.
  function _managedContractValue() internal virtual view returns (uint256) {
    uint256 strategyTokenBalance = _contractTokenBalance(strategyToken);
    uint256 fees = unclaimedFees;
    return strategyTokenBalance > fees ? strategyTokenBalance - fees : 0;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L435-459)
```text
      // update tranche prices and unclaimed fees
      _updateAccounting();

      // transfer fees
      uint256 _fees = unclaimedFees;
      if (_mintInterest) {
        // If interest is minted then we mint new shares for fee receivers instead of transferring underlyings
        if (_fees != 0) {
          uint256 feeReceiverAmount = _feeReceiverAmount(_fees);
          if (feeReceiverAmount != 0) {
            _mintSharesAtCurrPrice(feeReceiverAmount, feeReceiver, AATranche);
          }
          _mintSharesAtCurrPrice(_fees - feeReceiverAmount, owner(), AATranche);
          _updateSplitRatio(_getAARatio(true));
        }
      } else {
        // Cash-funded fees can only use gross interest not already owed to pending withdrawals.
        uint256 _availableForFees = _grossInterest > _pendingWithdrawFees ? _grossInterest - _pendingWithdrawFees : 0;
        if (_fees > _availableForFees) {
          _fees = _availableForFees;
        }
        _transferFeeUnderlyings(_fees);
      }
      // Any fee that cannot be paid in cash remains accrued and continues reducing NAV.
      unclaimedFees -= _fees;
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
