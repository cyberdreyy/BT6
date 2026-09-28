### Title
Fee-on-transfer underlying breaks `IdleCreditVault` 1:1 strategy-token backing and causes insolvency at claim time - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.deposit` (called by the CDO during user deposits and during epoch funding) pulls `_amount` of underlying via `safeTransferFrom` and then mints exactly `_amount` strategy tokens to the CDO, without measuring the actual received amount. If the pool currency ever charges a transfer fee, the vault receives less than `_amount` while minting the full `_amount` of receipt tokens that are accounted 1:1 with underlyings. The same nominal-vs-received mismatch occurs in `collectWithdrawFunds` and `collectInstantWithdrawFunds`, where `pendingWithdraws`/`pendingInstantWithdraws` are decremented by the nominal `_amount` while the vault actually receives less, leaving funded claims undercollateralized.

### Finding Description
- `IdleCreditVault.deposit` mints nominal `_amount` regardless of tokens actually received:
```solidity
underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
_mint(msg.sender, _amount);
``` [1](#0-0) 
- `collectWithdrawFunds` reduces `pendingWithdraws` by `_amount` but pulls `_amount` nominally; with a fee the vault holds `received = _amount - fee` against `claimBasis = _amount` worth of receipts [2](#0-1) 
- `collectInstantWithdrawFunds` has the same issue for instant receipts [3](#0-2) 
- The parent `IdleCDO._deposit` correctly mints based on balance delta (`_contractTokenBalance(_token) - _preBal`), but then calls `IIdleCDOStrategy(strategy).deposit(_amount)` with the nominal amount for `directDeposit`, so the strategy mints unbacked tokens on the second transfer [4](#0-3) 

A user deposits 1000 FoT tokens: CDO receives 999 and mints shares on 999 (fair). CDO then `deposit(1000)` — wait, CDO only holds 999, so a strict ERC20 `transferFrom` of 1000 would revert. However when the manager/borrower funds the vault via `collectWithdrawFunds`/`collectInstantWithdrawFunds` or `sendInterestAndDeposits` flows around `stopEpoch`, the nominal accounting persists: `pendingWithdraws` is cleared against a nominal `_amount` while the vault balance is short the fee, so later `claimWithdrawRequest`/`claimInstantWithdrawRequest` calls revert on insufficient balance — last claimants are permanently unable to withdraw.

### Impact Explanation
Insolvency / permanent freezing of unclaimed yield: withdraw receipts funded through `collectWithdrawFunds` are recorded as fully funded at the nominal amount but backed by less underlying, so the final claimants' `safeTransfer` reverts. Direct loss equals the cumulative transfer fee on every funding transfer; unquantifiable in absolute terms but scales with total epoch funding.

### Likelihood Explanation
Low-to-medium. Requires the vault's underlying pool currency to be (or become, e.g. via token upgrade like USDT-style fee switches) a fee-on-transfer token. Pool currencies are typically USDC/USDT/DAI which currently charge no fee, but USDT historically had a fee mechanism and the code gives no guard. No privileged misbehavior needed — the accounting flaw triggers on honest manager/borrower funding calls.

### Recommendation
Measure actual received amounts everywhere underlying enters the vault:
- In `deposit`: `uint256 pre = underlyingToken.balanceOf(address(this)); safeTransferFrom(...); _mint(msg.sender, received)` where `received = balanceOf - pre`.
- In `collectWithdrawFunds`/`collectInstantWithdrawFunds`: decrement `pendingWithdraws`/`pendingInstantWithdraws` by the measured received amount, or require received == `_amount` and revert otherwise.
- Alternatively, explicitly document and enforce (e.g. at initialization/factory level) that fee-on-transfer tokens are unsupported.

### Proof of Concept
Foundry fork sketch: deploy `IdleCreditVault` with a mock FoT token (1% burn on transfer). Have borrower/manager fund `collectWithdrawFunds(1000)` — vault receives 990 but `pendingWithdraws` becomes 0 and `lossRecoveryPriceByEpoch` is unset. Two users each holding a 500 receipt call `claimWithdrawRequest`: the first receives 500, the second reverts (`balance 490 < 500`) — permanent freezing of 10 tokens per 1000 funded, scaling with funding volume. Same demonstration applies to `deposit`/`claimInstantWithdrawRequest`.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-430)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L596-617)
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

    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
    }

    return _amount;
  }
```

**File:** contracts/IdleCDO.sol (L249-259)
```text
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));

    if (directDeposit) {
      IIdleCDOStrategy(strategy).deposit(_amount);
    }
```
