### Title
Fee-on-transfer underlying breaks 1:1 strategy-token minting in `IdleCreditVault.deposit`, overstating CDO NAV and leaving withdraw receipts underfunded — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The CDO mints tranche shares against the *actual* balance delta received, but `IdleCreditVault.deposit` mints strategy tokens 1:1 against the *requested* `_amount` while only `safeTransferFrom`-ing `_amount`. For a fee-on-transfer underlying (e.g., USDT with fees enabled, or any pool currency that adds a transfer fee), the vault receives `_amount - fee` yet mints `_amount` strategy tokens. Since `IdleCreditVault.price()` is hardcoded to `oneToken`, the CDO's NAV is permanently overstated by the cumulative fee, and every withdraw receipt (`pendingWithdraws`, `instantWithdrawsRequests`) is denominated in a claim the vault cannot fully pay.

### Finding Description
In `IdleCDOCreditVault._deposit`, the number of tranche shares minted correctly uses the measured delta: [1](#0-0) . However, the full nominal `_amount` is then pushed to the strategy: [2](#0-1) . In `IdleCreditVault.deposit`, `_amount` is pulled again and minted 1:1 without measuring what actually arrived: [3](#0-2) . The strategy price is fixed at `oneToken`, so `getContractValue()` counts `balanceOf(idleCDO)` strategy tokens at par: [4](#0-3) . The same pattern exists in the buffered deposit paths `collectWithdrawFunds`/`collectInstantWithdrawFunds`, which transfer `_amount` nominal to back receipts: [5](#0-4) .

Concretely, during buffer phase an attacker (KYC-passed lender) deposits `X`. User→CDO transfer delivers `X·(1-f)`; CDO→strategy transfer pulls `X` (feasible if the CDO holds residual underlying from `getInstantWithdrawFunds`/`sendInterestAndDeposits` or simply earlier deposits), strategy receives `X·(1-f)` but mints `X` to the CDO. The CDO's claim on the vault exceeds real holdings by `X·f` per deposit. When the attacker later calls `requestWithdraw`/`requestInstantWithdraw`, receipts are created for the full minted amount while the vault holds less; claims are paid FIFO from `underlyingToken.balanceOf`, so early claimants drain the reserve and later claimants' `_transferFundedClaim` reverts on insufficient balance — a permanent shortfall, not recoverable via `transferToken` without owner loss.

### Impact Explanation
Each deposit creates `X·f` unbacked strategy tokens carried at par in NAV. Attackers withdrawing before honest users extract funded underlying that belongs to other receipt holders; the deficit is crystallized as a loss to the last claimants or is socialized through `stopEpochWithDuration(_lossAmount)` / `finalizeDefaultRecovery` haircuts — i.e., direct theft plus permanent freezing of unclaimed funded claims equal to the aggregate skimmed fee.

### Likelihood Explanation
Requires a fee-on-transfer pool currency — plausible because credit vault underlyings are admin-configured stablecoins (USDT supports fee-on-transfer on its contract), so the trigger is a real token configuration rather than a hypothetical. The CDO-side minting guard does not help because it measures the user→CDO leg, while the inflation happens on the CDO→strategy leg. No existing guard (`_guarded`, `_onlyIdleCDO`, `price()==1`, default checks) detects the shortfall; `_checkDefault` cannot fire because the strategy reports a constant price.

### Recommendation
Measure the actual received amount inside `IdleCreditVault.deposit` before minting, and propagate the received value to the CDO accounting:

```diff
 function deposit(uint256 _amount) external virtual override returns (uint256) {
     _onlyIdleCDO();
     if (_amount > 0) {
+        uint256 before_ = underlyingToken.balanceOf(address(this));
         underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
+        _amount = underlyingToken.balanceOf(address(this)) - before_;
         _mint(msg.sender, _amount);
     }
     ...
 }
```

Apply the same balance-delta measurement in `collectWithdrawFunds`, `collectInstantWithdrawFunds`, and `finalizeDefaultRecovery`'s `safeTransferFrom(_recoverySource, ...)`, or explicitly reject fee-on-transfer underlyings in `initialize`.

### Proof of Concept
Foundry fork sketch (epoch buffer phase, mock FoT USDT charging 10%):

```solidity
// FoTToken: transferFrom burns 10%
// setup: vault seeded so CDO holds residual underlying (e.g., prior deposits)
// attacker is KYC'd, calls depositAA via IdleCDO during buffer

uint256 X = 1000e6;
fot.approve(address(cdo), X);
cdo.depositAA(X);          // minted shares priced vs measured delta X*0.9 at CDO leg
// but strategy.deposit(X) pulls X from CDO, vault receives 0.9X, mints X to CDO

// attacker requests instant/normal withdraw of minted tranche value
cdo.requestInstantWithdrawAA(minted); // receipt = X basis, vault holds 0.9X (+residual)

// honest user's subsequent claim: claimInstantWithdrawRequest -> _transferFundedClaim
// reverts or is paid only 0.9X-worth; shortfall = X*feeRate per deposit
assertLt(underlying.balanceOf(address(vault)), pendingBasis);
```

Realistic feasibility note: on mainnet fork, use a USDT-like token with fees toggled or a deployed FoT stablecoin; PoC requires `cdo` underlying balance ≥ `_amount` at the CDO→strategy pull, achievable by depositing during buffer when CDO still holds prior collected funds, or by chaining deposits where residual `instantWithdraws` funding sits in the CDO.

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L202-206)
```text
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
```

**File:** contracts/IdleCDOCreditVault.sol (L210-211)
```text
    // direct deposit in the strategy
    IIdleCDOStrategy(strategy).deposit(_amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L172-176)
```text
  /// @notice return strategy token price which is always 1
  /// @return price in underlyings
  function price() public view virtual override returns (uint256) {
    return oneToken;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-430)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }

  /// @notice collect borrower-funded withdraw receipt funds
  /// @dev Only IdleCDO can call this function. When `_amount` is lower than the
  /// pending basis, the difference is a stopEpochWithDuration loss assigned to
  /// pending receipts and users later claim through `lossRecoveryPriceByEpoch`.
  /// Reverts if the resulting recovery price rounds to zero at `RECOVERY_FULL` precision.
  /// @param _amount number of funded tokens to collect
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
