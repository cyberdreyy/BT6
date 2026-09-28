### Title
Fee-on-transfer underlying breaks `depositDuringEpoch` accounting — vault mints shares, strategy tokens, and forwards principal on the pre-fee amount - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The Ajna report describes a pool that books collateral/debt using the user-supplied amount while the actual ERC20 transfer settles for less because the token charges a transfer fee. In idle-tranches, the ordinary deposit path is already safe: `IdleCDOCreditVault._deposit` and `IdleCDO._deposit` snapshot the balance before `_transferUnderlyingsFrom` and mint shares on the received delta (`_contractTokenBalance(_token) - _preBal`). [1](#0-0) [2](#0-1)  The mid-epoch path `depositDuringEpoch` in `IdleCDOEpochVariant` skips this pattern entirely: every downstream accounting step — prorated interest, share mint, `mintStrategyTokens`, and the transfer of principal to the borrower — uses the nominal `_amount`, not the amount actually received.

### Finding Description
In `depositDuringEpoch`, the vault pulls tokens from the depositor but never measures what arrived:

```solidity
// Get underlyings from user
_transferUnderlyingsFrom(msg.sender, address(this), _amount);
``` [3](#0-2) 

All subsequent steps use `_amount`:
- interest is computed on `_amount` (`_calcInterest(_amount)`), [4](#0-3) 
- shares are minted so the depositor is owed `_amount + trancheInterest` at epoch end, [5](#0-4) 
- `expectedEpochInterest += interest` is booked, [6](#0-5) 
- `IdleCreditVault(strategy).mintStrategyTokens(_amount)` mints full backing, [7](#0-6) 
- `_transferUnderlyings(_borrower(), _amount)` sends the full nominal principal to the borrower. [8](#0-7) 

With a fee-on-transfer `token` (e.g., a USDT-style token with a fee enabled, or a rebasing/FoT stable), the contract receives `_amount - fee` but still forwards `_amount` to the borrower. The `fee` shortfall is paid out of the vault's existing cash — i.e., out of other depositors' liquidity — while NAV is credited as if `_amount` of new collateral arrived. `_skimDonatedAssets()` runs before the transfer and does not detect the shortfall, and the balance-delta guard present in `_deposit` is absent here. [9](#0-8) 

The same class reappears on the inflow side of `getFundsFromBorrower`: `stopEpoch` pulls `_amountToPullFromBorrower + _pendingWithdraws` via `transferFrom` and then calls `collectWithdrawFunds(_pendingWithdraws)`, crediting pending withdraw claims with the full nominal amount even though the vault received `fee` less. [10](#0-9) [11](#0-10) 

### Impact Explanation
Every FoT deposit during a running epoch mints a withdrawal claim on `_amount + trancheInterest` while the vault's real cash increase is `_amount - fee` and `_amount` is simultaneously sent to the borrower. NAV/strategy-token backing is overstated by `fee` per deposit. At `stopEpoch`, withdraw claims and tranche redemptions are paid from real balances, so the last claimants/withdrawers find the pool short by the cumulative fees — permanent insolvency for the tail of the queue, quantified as `sum(fee_i)` across all mid-epoch deposits (and borrower pulls). This mirrors the external report's "recorded collateral > actual collateral, final withdrawers can't be paid" impact.

### Likelihood Explanation
Requires the vault's `token` to be (or become) a fee-on-transfer ERC20. Several supported underlyings in this product class (USDT, which has a fee switch; various stablecoins with transfer hooks) satisfy this, and the attacker is only a KYC-passing lender calling the public `depositDuringEpoch` during `isEpochRunning` when `isDepositDuringEpochDisabled`, `isAYSActive`, `isProgrammableBorrower`, and `skipDefaultCheck` are all false. No privileged role misbehavior is needed; the honest borrower simply receives the full `_amount` it was sent.

### Recommendation
Measure received amounts in `depositDuringEpoch`, consistent with `_deposit`:

```solidity
address _token = token;
uint256 _preBal = _contractTokenBalance(_token);
_transferUnderlyingsFrom(msg.sender, address(this), _amount);
uint256 _received = _contractTokenBalance(_token) - _preBal;
```

Then compute `interest`, `_minted`, `mintStrategyTokens`, and the borrower transfer on `_received` (or revert if `_received != _amount`). Apply the same received-amount check to `getFundsFromBorrower` before `collectWithdrawFunds(_pendingWithdraws)` and `_transferUnderlyings(address(_strategy), _totBorrowed)` in `_stopEpoch`.

### Proof of Concept
Foundry fork test (mainnet, underlying = a FoT token, e.g., USDT with fee enabled, or a mock FoT ERC20 wired as `token`):

```solidity
function testFoTDepositDuringEpoch() public {
    // setup: vault seeded by honest LP with depositAA pre-epoch, epoch started (buffer over, isEpochRunning)
    vm.prank(manager); cdoEpoch.startEpoch();

    uint256 amount = 100_000e6;
    deal(address(fotToken), attacker, amount);
    vm.startPrank(attacker); // KYC-allowed wallet
    fotToken.approve(address(cdoEpoch), amount);
    uint256 balVaultPre = fotToken.balanceOf(address(cdoEpoch));
    uint256 minted = cdoEpoch.depositDuringEpoch(amount, address(AA));
    vm.stopPrank();

    uint256 received = fotToken.balanceOf(address(cdoEpoch)) - balVaultPre; // amount - fee
    assertLt(received, amount);                       // fee taken
    // vault still forwarded `amount` to borrower and minted `amount` strategy tokens
    assertEq(strategy.balanceOf(address(cdoEpoch)) - stratPre, amount);
    // minted shares entitle attacker to amount + trancheInterest > received
    // => NAV overstated by `fee`; last withdrawer at stopEpoch reverts/underpaid
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(address(fotToken), borrower, expectedOwed);
    vm.prank(borrower); fotToken.approve(address(cdoEpoch), type(uint256).max);
    vm.prank(manager); cdoEpoch.stopEpoch(newApr, 0);
    // final claimWithdrawRequest for the last receipt underflows / pays less than recorded
}
```

The assertion `received < amount` combined with `mintStrategyTokens(amount)` and `transfer(borrower, amount)` demonstrates the recorded-vs-actual gap directly; the epoch-end claim for the last LP then fails or is underpaid by the accumulated fee.

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L201-206)
```text
    // get underlyings from sender
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
```

**File:** contracts/IdleCDO.sol (L249-253)
```text
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
```

**File:** contracts/IdleCDOEpochVariant.sol (L408-410)
```text
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```

**File:** contracts/IdleCDOEpochVariant.sol (L550-553)
```text
  function getFundsFromBorrower(uint256 _amount) external {
    _checkNotAllowed(msg.sender != address(this));
    _transferUnderlyingsFrom(_borrower(), address(this), _amount);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L679-686)
```text
    _skimDonatedAssets();
    // Check that limit is not exceeded after removing skimmable raw donations.
    _guarded(_amount);
    _updateAccounting();

    // Get underlyings from user
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);

```

**File:** contracts/IdleCDOEpochVariant.sol (L691-697)
```text
    uint256 buffer = bufferPeriod;
    uint256 remaining = epochEndDate - block.timestamp;
    uint256 interest = _calcInterest(_amount) *
      // the time the depositor actually participates (remaining epoch + full buffer)
      (remaining + buffer) /
      // the total time baked into the scaled APR (epoch + buffer).
      (epochDuration + buffer);
```

**File:** contracts/IdleCDOEpochVariant.sol (L719-725)
```text
    // mint at a discounted price so depositor gets principal + its prorated interest at epoch end
    // A mid‑epoch depositor should get _amount + trancheInterest at epoch end.
    // So they need minted = (amount + trancheInterest) / priceEnd.
    // priceEnd = expectedFinal / _trancheTotSupply
    // so minted = (amount + trancheInterest) * _trancheTotSupply / expectedFinal
    _minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
    _mintShares(_tranche, msg.sender, _minted, _amount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L727-728)
```text
    // update expected epoch interest
    expectedEpochInterest += interest;
```

**File:** contracts/IdleCDOEpochVariant.sol (L729-730)
```text
    // mint strategy tokens to this contract
    IdleCreditVault(strategy).mintStrategyTokens(_amount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L731-732)
```text
    // transfer underlyings to the borrower
    _transferUnderlyings(_borrower(), _amount);
```
