### Title
Fee-on-transfer underlying breaks share/strategy-token accounting and drains or freezes vault deposits - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
When the CDO's `token` is (or becomes) a fee-on-transfer ERC20 (e.g. USDT with fees enabled), two code paths in the credit-vault stack mis-account for the received amount:

1. `IdleCDOCreditVault._deposit` correctly mints tranche shares on the balance delta, but then calls `IIdleCDOStrategy(strategy).deposit(_amount)` with the *gross* amount. `IdleCreditVault.deposit` pulls `_amount` from the CDO and mints `_amount` strategy tokens while only `_amount - fee` arrives — minting unbacked strategy tokens and requiring the CDO to hold an extra `fee` of balance it never received.
2. `IdleCDOEpochVariant.depositDuringEpoch` mints tranche shares and strategy tokens on the gross `_amount` and forwards the gross `_amount` to the borrower, even though the CDO only received `_amount - fee`.

### Finding Description
In `IdleCDOCreditVault._deposit`, shares are minted on `_contractTokenBalance(_token) - _preBal`, which is fee-safe [1](#0-0) . However the subsequent `IIdleCDOStrategy(strategy).deposit(_amount)` uses the gross amount [2](#0-1) . `IdleCreditVault.deposit` then does `underlyingToken.safeTransferFrom(msg.sender, address(this), _amount)` and mints `_amount` strategy tokens 1:1 [3](#0-2) . With a fee token: (a) the second transfer charges another fee, so the strategy mints `_amount` receipts against `_amount - fee` cash; (b) the CDO's balance only grew by `_amount - fee`, so pulling `_amount` either reverts (permanent deposit DoS while CDO balance < fee) or silently spends other users'/donated funds.

In `depositDuringEpoch`, the same mistake is worse: shares are minted on gross `_amount` (`_mintShares(_tranche, msg.sender, _minted, _amount)` credits NAV by `_amount`), `mintStrategyTokens(_amount)` is called, and the gross `_amount` is forwarded to the borrower [4](#0-3) . The CDO's net balance change is `-fee` per call while NAV and strategy-token supply each grow by `_amount`, and the honest borrower is booked for `_amount` of debt while receiving only `_amount - fee`. Neither `_skimDonatedAssets` nor any balance check stops this [5](#0-4) .

### Impact Explanation
- Each mid-epoch deposit creates `fee` of unbacked tranche NAV + `fee` of unbacked strategy tokens (double-counted shortfall), or drains `fee` from contract-held funds (pending withdraw fees, donations, unfunded buffer cash) — insolvency borne by other tranche holders / pending withdrawers at claim time.
- If the CDO holds < `fee` spare balance, `safeTransfer`/`safeTransferFrom` underflows and `depositAA`/`depositBB`/`depositDuringEpoch` revert — deposits permanently unavailable for as long as the token charges fees, matching the original report's "part of the protocol becomes unavailable" impact.
- Quantified loss: `fee` per deposit, unbounded and repeatable by any KYC-passed lender.

### Likelihood Explanation
Requires the vault underlying to charge transfer fees. Like the original finding, this covers upgradeable stables (USDT) that can enable fees after deployment; the codebase does not restrict `token` to fee-less assets, and the strategy tokens are minted 1:1 with no reconciliation of amounts received. Unprivileged lender action in a running epoch (or buffer phase for `_deposit`) is sufficient.

### Recommendation
Measure actual received amounts everywhere:
- In `depositDuringEpoch`, compute `received = balanceAfter - balanceBefore` and use `received` for share minting, `mintStrategyTokens`, `expectedEpochInterest`/`_calcInterest` inputs, and the borrower transfer.
- In `IdleCDOCreditVault._deposit`, pass the received delta (not `_amount`) to `strategy.deposit`, or have `IdleCreditVault.deposit` mint strategy tokens on its own balance delta.
- Alternatively, document/enforce that only non-fee-on-transfer tokens are supported.

### Proof of Concept
Foundry fork PoC sketch: deploy `IdleCDOEpochVariant` + `IdleCreditVault` with a fee-on-transfer ERC20 (e.g. mainnet USDT with fees enabled via `setParams`, or a mock fee token on a fork test).

```solidity
// FeeOnTransfer.t.sol — assumes FeeToken charges 1% on transfer/transferFrom
function testFeeOnTransferMidEpoch() public {
    uint256 amount = 10_000e6;           // 1% fee => CDO receives 9_900e6
    deal(address(feeToken), user, amount);
    // epoch running, isDepositDuringEpochDisabled == false
    vm.startPrank(user);
    feeToken.approve(address(cdo), amount);
    cdo.depositDuringEpoch(amount, address(AAtranche));
    vm.stopPrank();

    // BUG 1: borrower got 9_900e6 but strategy minted 10_000e6 receipts to CDO
    assertEq(strategy.balanceOf(address(cdo)), amount);            // 10_000e6 claim
    assertEq(feeToken.balanceOf(borrower), amount * 99 / 100);     // 9_900e6 cash
    // BUG 2: CDO paid gross `amount` out but only received `amount - fee`:
    // net -100e6 drained from contract-held funds, or revert if balance < fee.
    // NAV grew by `amount` while real backing grew by `amount - fee`
    // => 100e6 insolvency per deposit; repeat to drain pending fees/donations.
}

function testFeeOnTransferBufferDepositDoS() public {
    // preBal == 0 at CDO (all prior deposits already in strategy)
    vm.startPrank(user);
    feeToken.approve(address(cdo), amount);
    vm.expectRevert(); // strategy.deposit(_amount) transferFrom underflows
    cdo.depositAA(amount);
    vm.stopPrank();
}
```

Relevant code: `depositDuringEpoch` at `contracts/IdleCDOEpochVariant.sol:656-733` and `IdleCreditVault.deposit` at `contracts/strategies/idle/IdleCreditVault.sol:596-617`.

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L203-206)
```text
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
```

**File:** contracts/IdleCDOCreditVault.sol (L210-212)
```text
    // direct deposit in the strategy
    IIdleCDOStrategy(strategy).deposit(_amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L596-605)
```text
  function deposit(uint256 _amount)
    external
    virtual
    override
    returns (uint256) {
    _onlyIdleCDO();
    if (_amount > 0) {
      underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
      _mint(msg.sender, _amount);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L679-685)
```text
    _skimDonatedAssets();
    // Check that limit is not exceeded after removing skimmable raw donations.
    _guarded(_amount);
    _updateAccounting();

    // Get underlyings from user
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L724-732)
```text
    _minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
    _mintShares(_tranche, msg.sender, _minted, _amount);

    // update expected epoch interest
    expectedEpochInterest += interest;
    // mint strategy tokens to this contract
    IdleCreditVault(strategy).mintStrategyTokens(_amount);
    // transfer underlyings to the borrower
    _transferUnderlyings(_borrower(), _amount);
```
