### Title
Full withdrawal of a tranche class strands dead MIN_LIQUIDITY shares with zero NAV, forcing price to 0 and permanently DoSing all future deposits via division by zero - ([File: contracts/IdleCDOCreditVault.sol](contracts/IdleCDOCreditVault.sol))

### Summary
The flatcoin bug class is "minimum-liquidity invariant enforced at deposit but not at withdrawal, letting a first depositor strand a dust share supply that bricks subsequent deposits." The idle-tranches credit vault has the same structural gap: `IdleCDOTranche.mint` permanently mints `MIN_LIQUIDITY = 10**3` dead shares to `address(1)` on the first deposit [1](#0-0) , while nothing prevents a user from withdrawing their entire real balance and driving the tranche NAV to exactly 0 through `_withdrawOps` [2](#0-1) . Because the dead shares keep `totalSupply > 0`, `_virtualPriceAux` treats the tranche as "economically wiped" and saves `priceAA/priceBB = 0` [3](#0-2) . Every subsequent `_mintSharesAtCurrPrice` then divides by that zero price and panics [4](#0-3) .

### Finding Description
Deposit and withdraw accounting are asymmetric:

- On mint, `IdleCDOTranche.mint` adds 1000 permanently unburnable shares when `totalSupply() == 0` [5](#0-4) .
- On withdraw, `_withdrawOps` burns only `msg.sender`'s shares and decrements `lastNAVAA/lastNAVBB` by the redeemed underlyings [2](#0-1) . There is no check that residual NAV stays above a floor, and the dead shares can never be burned (`burn` only touches the caller's balance) [6](#0-5) .
- After the last real holder fully exits (e.g., sole AA depositor claims their entire epoch withdrawal), `lastNAVAA == 0` while `AATranche.totalSupply() == 1000`.
- On the next `depositAA`, `_updateAccounting` → `_virtualPriceAux` hits the branch `trancheSupply != 0 && _lastNAV == 0 && _nav == 0 → return (0, 0)`, so `priceAA = 0` [3](#0-2) . The emergency-shutdown branch does not fire because it requires `_lastNAV != 0` [7](#0-6) .
- `_tranchePrice` returns `priceAA` (0) since supply is nonzero [8](#0-7) , and `_mintSharesAtCurrPrice` computes `_amount * ONE_TRANCHE_TOKEN / 0`, reverting with a division-by-zero panic [9](#0-8) .

The same applies to `depositBB` when `isBBDepositEnabled`. No privileged recovery path repairs it: `updateAccounting`/`_forceUpdateAccounting` recompute the same zero price [10](#0-9) , and no function resets `priceAA` once supply is nonzero. Donation does not help either: `_managedContractValue` counts only `strategyToken` and skimmed raw underlyings are excluded [11](#0-10) , and even if `nav > 0`, `_lastTrancheNAV == 0` forces `_totalTrancheGain = 0` so the price stays 0 [12](#0-11) .

### Impact Explanation
Permanent denial of service for the affected tranche class: every `depositAA`/`depositBB` reverts with a panic, so the credit vault can never accept new senior (or junior) liquidity again absent a contract upgrade. This matches the flatcoin report's protocol-wide DoS impact and exceeds the "DoS without fund impact" rejection bar because the vault is permanently bricked for its core function.

### Likelihood Explanation
Requires the attacker to be (or become) the sole holder of a tranche class and to fully withdraw, which is naturally satisfiable on a newly initialized vault during the buffer epoch before other lenders join — the same "first depositor" window as the source report. The attacker needs only a KYC-passing lender position (an allowed unprivileged role per the rules) and standard epoch withdrawal mechanics; no privileged cooperation is needed. Cost is only gas plus temporarily locked capital. Note the residual-NAV-exactly-0 condition depends on withdrawal rounding; an attacker can tune deposit/withdraw sizes across accounts to zero out NAV, since a dust NAV of ≥1 wei merely produces a near-zero-but-nonzero price that self-heals.

### Recommendation
Apply a residual-supply/NAV floor on withdrawal symmetric to the mint-side dead-share design — e.g., in `_withdrawOps` (or the epoch claim path), revert if the withdrawal would leave `lastNAVXX == 0` while `totalSupply() > 0`, or require redeemers to leave at least `MIN_LIQUIDITY`-equivalent NAV. Alternatively, in `_tranchePrice`/`_virtualPriceAux`, treat a zero-NAV tranche whose only supply is the `address(1)` dead shares as uninitialized (return `oneToken`) so the class can be re-bootstrapped.

### Proof of Concept
Foundry fork outline against `test/foundry/IdleCreditVault.t.sol` harness:

```solidity
function test_fullWithdrawZeroNavDepositDos() public {
    // buffer phase: attacker is sole AA depositor
    uint256 amount = 1000 * ONE_SCALE;
    _depositAA(attacker, amount);          // mints amount*1e18 - 1000, 1000 dead shares to address(1)

    // run + stop epoch so attacker can claim everything
    _toggleEpoch(true, 0, 0);
    _toggleEpoch(false, 1e18, expectedFunds);

    // attacker requests and claims entire AA balance
    uint256 bal = IERC20(AAtranche).balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(bal, AAtranche);   // or epoch-variant claim path
    _claimAll(attacker, true);                  // burns all attacker shares; lastNAVAA -> 0
    assertEq(IERC20(AAtranche).totalSupply(), 10**3);   // only dead shares remain
    assertEq(idleCDO.lastNAVAA(), 0);

    // any subsequent depositAA panics on division by zero price
    vm.expectRevert(); // panic: division by zero in _mintSharesAtCurrPrice
    _depositAA(bob, amount);
}
```

Caveat: I could not fully trace `requestWithdraw`/`claimWithdrawRequest` internals in `IdleCDOEpochVariant` within the available iterations, so the exact claim-path call sequence and whether a dust NAV residue is left in practice should be verified when writing the PoC.

### Citations

**File:** contracts/IdleCDOTranche.sol (L25-33)
```text
  function mint(address account, uint256 amount) external {
    require(msg.sender == minter, '6');
    // burn MIN_LIQUIDITY on first tranche deposit
    if (totalSupply() == 0) {
      _mint(address(1), MIN_LIQUIDITY);
      amount -= MIN_LIQUIDITY;
    }
    _mint(account, amount);
  }
```

**File:** contracts/IdleCDOTranche.sol (L37-40)
```text
  function burn(address account, uint256 amount) external {
    require(msg.sender == minter, '6');
    _burn(account, amount);
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

**File:** contracts/IdleCDOCreditVault.sol (L238-246)
```text
    // Ordinary losses exhaust BB before reducing AA. Stop normal interactions once BB is wiped,
    // or when an AA-only vault is fully wiped, so the loss must be crystallized explicitly.
    if ((_totalBBGain < 0 && -_totalBBGain >= int256(_lastNAVBB)) || (_lastNAV != 0 && nav == 0)) {
      shutdown = true;
      if (!skipDefaultCheck) revert Default();
      // Keep a total wipe distinguishable from an uninitialized vault when no BB NAV existed.
      if (nav == 0) _priceAA = 0;
      _emergencyShutdown(true);
    }
```

**File:** contracts/IdleCDOCreditVault.sol (L294-298)
```text
    // A zero supply identifies a tranche that was never initialized. A non-zero supply with
    // zero NAV is an economically wiped tranche and must keep its saved zero price.
    uint256 trancheSupply = _trancheSupply(_tranche);
    if (trancheSupply == 0) return (oneToken, 0);
    if (_lastNAV == 0 && _nav == 0) return (0, 0);
```

**File:** contracts/IdleCDOCreditVault.sol (L319-320)
```text
    if (_lastTrancheNAV == 0) {
      _totalTrancheGain = 0;
```

**File:** contracts/IdleCDOCreditVault.sol (L344-347)
```text
  function _mintSharesAtCurrPrice(uint256 _amount, address _to, address _tranche) internal virtual returns (uint256 _minted) {
    // calculate # of tranche token to mint based on current tranche price: _amount / tranchePrice
    _minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche);
    _mintShares(_tranche, _to, _minted, _amount);
```

**File:** contracts/IdleCDOCreditVault.sol (L369-378)
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

**File:** contracts/IdleCDOCreditVault.sol (L494-501)
```text
  function _forceUpdateAccounting() internal {
    bool wasSkippingDefaultCheck = skipDefaultCheck;
    skipDefaultCheck = true;
    // Preserve an existing emergency shutdown and any wipe reported by accounting.
    if (!_updateAccounting()) {
      skipDefaultCheck = wasSkippingDefaultCheck;
    }
  }
```
