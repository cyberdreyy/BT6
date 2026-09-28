### Title
Donation-based share inflation lets the first depositor steal subsequent lenders' principal via `getContractValue` counting raw token balance - (File: contracts/IdleCDOCreditVault.sol)

### Summary
The credit vault's state-changing accounting path (`_updateAccounting` → `getContractValue`) counts the raw `token` balance held by the contract, while the deposit mint path computes tranche price from the NAV updated by that same accounting call. An unprivileged lender can deposit 1 wei, directly transfer (donate) a large amount of `token` to the vault, and the next victim's deposit is priced against the inflated tranche price, minting them ~0 shares. The attacker then redeems their single share for the entire NAV, including the victim's principal.

### Finding Description
`getContractValue` returns `_contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees`, i.e. unsolicited `token` transfers sit in NAV [1](#0-0) . Although `_managedContractValue` deliberately excludes raw underlyings and is used by the `virtualPrice` view and management-fee accrual [2](#0-1) , the comment claims donations "are skimmed on interactions" — yet no `_skimDonatedAssets` (present in `IdleCDO.sol`) exists anywhere in this contract, so donated tokens are treated as real gain by `_updateAccounting` [3](#0-2) . `_deposit` runs `_updateAccounting()` before minting, so the donation is folded into `priceAA`/`priceBB` via `_virtualPriceAux` (`_virtualPrice = newNAV * ONE_TRANCHE_TOKEN / trancheSupply`) before shares are minted at that inflated price [4](#0-3) . With `trancheSupply == 1` (attacker's wei deposit) and `lastNAVAA == lastNAV`, the AA class absorbs the full fake gain, so `priceAA ≈ donation * 1e18` [5](#0-4) . The victim's `_mintSharesAtCurrPrice` computes `V * 1e18 / priceAA` which rounds to ~0 shares while `_mintShares` still credits their full `V` to `lastNAVAA` [6](#0-5) . The attacker burns their 1 share through the normal withdraw path (`_withdrawOps`) and redeems ~`1 + D + V`, profiting `V`.

Existing guards do not stop this: there is no skim of donated `token`, no minimum first-deposit or dead-share lock, `_tranchePrice` returns `oneToken` only when supply is zero (not when NAV was inflated) [7](#0-6) , and minting 0 shares does not revert. Access rules (KYC whitelist, `isBBDepositEnabled`, `_guarded` limit, `whenNotPaused`) are satisfiable by the permitted attacker profile (a KYC-passing AA lender in an open/buffer epoch).

### Impact Explanation
Direct theft of user funds: every lender who deposits after the donation, while attacker supply is still 1 wei-share, receives ~0 tranche tokens and loses up to 100% of principal, bounded only by the donation size relative to victim deposits. The same mechanism also siphons a performance fee into `unclaimedFees` on the fake gain. Quantified loss ≈ victim deposit `V` (minus dust) per attack round.

### Likelihood Explanation
Requires only: an early/mintable state of a tranche (supply small enough that `D + NAV` materially moves `priceAA`), which holds for a newly deployed vault or a tranche just reopened after a wiped/closed epoch, plus attacker capital `D` temporarily locked (mostly recoverable). No privileged action, no oracle, no borrower cooperation. The main limitation is that the attack window closes once honest supply grows, so likelihood is moderate — real but bounded to bootstrap/reset phases.

### Recommendation
Make the accounting path consistent with the managed-NAV view: exclude raw `token` balance from `getContractValue` (or implement `_skimDonatedAssets` to sweep unsolicited `token` before `_updateAccounting` reads NAV), and seed a minimum initial tranche supply / enforce a minimum first deposit so `priceAA` cannot be manipulated by a single wei-share. Alternatively, mint shares against `_managedContractValue`-derived virtual price rather than the donation-inclusive saved price.

### Proof of Concept
Foundry fork test sketch (mainnet fork, existing credit vault or fresh factory deployment):

```solidity
// attacker = KYC'd EOA; token = vault underlying (e.g. USDC)
uint256 D = 1_000_000e6;   // donation
uint256 V = 500_000e6;     // victim deposit

// 1) Attacker is first depositor: mint 1 share at price = oneToken
token.approve(vault, 1);
vault.depositAA(1);                       // attacker gets ~1e18 shares

// 2) Donate raw underlying directly to the vault (no skim exists)
token.transfer(vault, D);                 // getContractValue() now counts D

// 3) Victim deposits; _updateAccounting folds D into priceAA first
vm.prank(victim);
token.approve(vault, V);
vm.prank(victim);
vault.depositAA(V);
// minted = V * 1e18 / priceAA ≈ V / (1+D/1) -> 0 shares; lastNAVAA += V
assertEq(IERC20(AATranche).balanceOf(victim), 0);

// 4) Attacker redeems their 1 share for 1 + D + V via the vault's
//    withdraw path (instant/epoch claim -> _withdrawOps burn)
//    attacker net gain ≈ V
```

Uncertainty: withdrawals in this vault are exposed through the epoch variant/`IdleCreditVault` strategy layer (`requestWithdraw`/`claimWithdrawRequest`) rather than a direct `withdrawAA` in `IdleCDOCreditVault`; the PoC should use whichever redeem path the deployed vault exposes — the inflation invariant broken here (fair mint at accounting-updated price) is independent of the redeem mechanism.

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

**File:** contracts/IdleCDOCreditVault.sol (L200-208)
```text
    _updateAccounting();
    // get underlyings from sender
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));
```

**File:** contracts/IdleCDOCreditVault.sol (L227-236)
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
```

**File:** contracts/IdleCDOCreditVault.sol (L321-336)
```text
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

**File:** contracts/IdleCDOCreditVault.sol (L344-362)
```text
  function _mintSharesAtCurrPrice(uint256 _amount, address _to, address _tranche) internal virtual returns (uint256 _minted) {
    // calculate # of tranche token to mint based on current tranche price: _amount / tranchePrice
    _minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche);
    _mintShares(_tranche, _to, _minted, _amount);
  }

  /// @notice mint tranche tokens and updates tranche last NAV
  /// @param _tranche tranche address
  /// @param _to receiver address of the newly minted tranche tokens
  /// @param _shares number of tranche tokens to mint
  /// @param _underlyings amount of underlyings added to the tranche
  function _mintShares(address _tranche, address _to, uint256 _shares, uint256 _underlyings) internal {
    IdleCDOTranche(_tranche).mint(_to, _shares);
    // update NAV with the _amount of underlyings added
    if (_tranche == AATranche) {
      lastNAVAA += _underlyings;
    } else {
      lastNAVBB += _underlyings;
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
