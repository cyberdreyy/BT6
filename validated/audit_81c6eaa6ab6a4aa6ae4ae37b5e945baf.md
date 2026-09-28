### Title
Donated strategy tokens inflate tranche price enabling a first-depositor/share-inflation attack on depositor funds - (File: contracts/IdleCDOCreditVault.sol)

### Summary
`IdleCDOCreditVault` (and its child `IdleCDOEpochVariant`) mints tranche shares as `_amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche)`, where the price derives from `getContractValue()`. `getContractValue()` includes the CDO's raw `strategyToken` balance. The donation guard `_skimDonatedAssets()` only sweeps the underlying `token`, not `strategyToken`. An attacker who is a tranche holder and also holds `strategyToken` (IdleCreditVault vault shares) can make a 1-wei first deposit, donate a large `strategyToken` amount to the CDO to inflate the tranche price, and cause subsequent depositors to mint 0 (or dust) shares while their underlyings are credited to tranche NAV — the same inflation-attack bug class as M-09.

### Finding Description
- Share minting: `_mintSharesAtCurrPrice` computes `_minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche)` and `_mintShares` adds the full `_underlyings` to `lastNAVAA`/`lastNAVBB` regardless of how many shares were minted. With `trancheSupply == 0`, `_virtualPriceAux` returns `oneToken`, so the first depositor gets `amount` wei of shares — the classic seed position. [1](#0-0) 
- NAV includes attacker-donatable assets: `getContractValue()` returns `strategyToken balance + token balance - unclaimedFees`. `strategyToken` is the `IdleCreditVault` share token (an ERC20); anyone holding vault shares can `transfer` them directly to the CDO. [2](#0-1) 
- Incomplete donation guard: `_skimDonatedAssets()` is invoked before accounting in `_deposit`, `depositDuringEpoch`, `requestWithdraw`, and `updateAccounting`, but it only sweeps `token` (the underlying). Donated `strategyToken` stays in the contract and is crystallized as tranche gain by `_updateAccounting` → `_virtualPriceAux` → `_virtualPrice = (_lastTrancheNAV + _totalTrancheGain) * ONE / trancheSupply`. [3](#0-2) [4](#0-3) 

Attack sequence (buffer/stopped phase, fixed-APR mode):
1. Attacker (KYC-passed lender, `isWalletAllowed` satisfied) calls `depositAA(1)` — mints 1 wei of AA tranche at `priceAA = oneToken`.
2. Attacker transfers `D` `strategyToken` (vault shares obtained as a user of the borrower's ERC4626 vault) directly to the CDO address.
3. Victim calls `depositAA(V)` with `V < D`. `_deposit` skims only `token` donations; `_updateAccounting` treats the donated `strategyToken` as gain → `priceAA ≈ D * ONE`. Victim mints `V * ONE / priceAA ≈ V/D < 1` → 0 shares, while `lastNAVAA += V`.
4. Attacker calls `withdrawAA(0)`, redeeming ~`(D + V)` of underlying for their 1-wei share. Net profit ≈ `V`; the donation `D` is returned to the attacker (minus fees).

### Impact Explanation
Direct theft of depositor funds: each victim depositing less than the donated amount receives 0 tranche shares and their principal accrues to the attacker's dust share position. Loss is bounded per victim by the donation size but repeatable; the attacker's only cost is fees on the round-tripped donation.

### Likelihood Explanation
Requires: (a) attacker holds or can acquire `strategyToken` — plausible where `IdleCreditVault` shares are held by users of the programmable borrower's ERC4626 vault, or any leaked share balance; (b) attacker passes `isWalletAllowed`/Keyring for the initial dust deposit and the final withdraw — allowed per threat model (KYC-passing lender is a valid attacker); (c) tranche supply small enough that a donation meaningfully moves the price — trivially true at first deposit, and a donation large relative to NAV works thereafter. No existing guard stops it: skim covers only `token`, `depositDuringEpoch` additionally requires non-zero supply but `_deposit` has no such check, and `_updateAccounting` has no minimum-gain sanity check.

### Recommendation
Extend `_skimDonatedAssets()` (or add a parallel sweep) to forward unsolicited `strategyToken` balances to `feeReceiver` before `_updateAccounting` in `IdleCDOEpochVariant._deposit`, `depositDuringEpoch`, `requestWithdraw`, and `updateAccounting`, and in `IdleCDOCreditVault._deposit`. Alternatively, base `getContractValue()`/NAV accounting on internally tracked strategy-token balances (e.g., a `lastStrategyTokenBalance` updated only by `deposit`/`mintStrategyTokens`/redeem paths) rather than raw `balanceOf`, and/or enforce a minimum-mint (`_minted > 0`) check in `_mintSharesAtCurrPrice`.

### Proof of Concept
Foundry fork PoC (concept; assumes `strategyToken` is a transferable ERC20 held by the attacker):

```solidity
// test/foundry/ShareInflation.t.sol
function test_shareInflation() public {
    // setup: IdleCDOEpochVariant vault, attacker is KYC-allowed and holds
    // IdleCreditVault shares (strategyToken) as a vault user
    uint256 D = 1_000_000e6;   // donated strategyToken value
    uint256 V = 500_000e6;     // victim deposit

    // 1. dust first deposit -> 1 wei of AA tranche
    depositAA(attacker, 1);
    assertEq(IdleCDOTranche(AA).balanceOf(attacker), 1);

    // 2. donate strategyToken directly to the CDO (skim only covers `token`)
    strategyToken.transfer(cdo, D, {from: attacker});

    // 3. victim deposit mints 0 shares but NAV is credited
    uint256 minted = depositAA(victim, V);
    assertEq(minted, 0);

    // 4. attacker redeems -> recovers D + steals V (minus fees)
    uint256 balBefore = token.balanceOf(attacker);
    withdrawAA(attacker, 0);
    assertGt(token.balanceOf(attacker) - balBefore, D); // profit ~ V
}
```

Note on uncertainty: I could not fully confirm the transferability and holder distribution of `IdleCreditVault` shares within the iteration budget. If `strategyToken` transfers are restricted such that only the CDO can ever hold them, the donation leg fails and this reduces to the no-vulnerability case; that check is the first thing to validate in the PoC.

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L125-128)
```text
  function getContractValue() public override view returns (uint256) {
    // Credit vault strategy tokens are minted 1:1 with underlyings and use the same decimals.
    return _contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees;
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L306-336)
```text
    int256 totalGain = int256(_nav) - int256(_lastNAV);
    // Ordinary zero-delta interactions keep their saved price for compatibility. Forced
    // accounting recomputes NAV per share, which is required after discounted mid-epoch deposits.
    if (totalGain == 0 && !skipDefaultCheck) return (_tranchePrice(_tranche), 0);

    // Remove performance fee for gains
    if (totalGain > 0) {
      totalGain -= totalGain * int256(fee) / int256(FULL_ALLOC);
    }

    bool _isAATranche = _tranche == AATranche;
    // A class with no saved NAV cannot be revived by later gains. If only this class has saved
    // NAV, it receives the full gain or loss; otherwise both classes participate.
    if (_lastTrancheNAV == 0) {
      _totalTrancheGain = 0;
    } else if (_lastNAV == _lastTrancheNAV) {
      _totalTrancheGain = totalGain;
    } else {
      if (totalGain > 0) {
        // Split the net gain, according to _trancheAPRSplitRatio, with precision loss favoring the AA tranche.
        int256 totalBBGain = totalGain * int256(FULL_ALLOC - _trancheAPRSplitRatio) / int256(FULL_ALLOC);
        // The new NAV for the tranche is old NAV + total gain for the tranche
        _totalTrancheGain = _isAATranche ? (totalGain - totalBBGain) : totalBBGain;
      } else {
        int256 maxBBLoss = -int256(lastNAVBB);
        int256 totalBBLoss = totalGain > maxBBLoss ? totalGain : maxBBLoss;
        _totalTrancheGain = _isAATranche ? totalGain - totalBBLoss : totalBBLoss;
      }
    }
    // Split the new NAV (_lastTrancheNAV + _totalTrancheGain) per tranche token
    _virtualPrice = uint256(int256(_lastTrancheNAV) + _totalTrancheGain) * ONE_TRANCHE_TOKEN / trancheSupply;
```

**File:** contracts/IdleCDOCreditVault.sol (L344-348)
```text
  function _mintSharesAtCurrPrice(uint256 _amount, address _to, address _tranche) internal virtual returns (uint256 _minted) {
    // calculate # of tranche token to mint based on current tranche price: _amount / tranchePrice
    _minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche);
    _mintShares(_tranche, _to, _minted, _amount);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L644-649)
```text
  function _deposit(uint256 _amount, address _tranche) internal override whenNotPaused returns (uint256) {
    _checkNotAllowed(!isWalletAllowed(msg.sender));
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
    // do the inherited deposit flow
    return super._deposit(_amount, _tranche);
```
