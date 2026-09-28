### Title
Early `claimInstantWithdrawRequest` does not decrement `pendingInstantWithdraws`, causing double funding of the same instant receipt - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`claimInstantWithdrawRequest` pays an instant-withdraw receipt as soon as the strategy holds enough underlying — before the CDO has collected the corresponding funds at `startEpoch` — but it never decreases the global `pendingInstantWithdraws` counter. The stale counter is then used again at `startEpoch`/`getInstantWithdrawFunds` to pull the same amount a second time from the CDO, double-funding a receipt that was already paid. This is the direct analog of the libcurl PUT→POST bug: state left over from one request type (the not-yet-funded instant receipt) is erroneously reused by a later operation (the epoch-start funding pull), because the handle was never "reset" after the early claim.

### Finding Description
- `requestInstantWithdraw` mints a receipt to the user and increments both `instantWithdrawsRequests[_user]` and the global `pendingInstantWithdraws`. [1](#0-0) 
- `claimInstantWithdrawRequest` burns `instantWithdrawsRequests[_user]` and calls `_transferFundedClaim`, which only checks the strategy's underlying balance (and `defaultRecoveryReserve`). It does not verify that the borrower's instant funding was actually collected, and it never touches `pendingInstantWithdraws`. [2](#0-1) [3](#0-2) 
- `collectInstantWithdrawFunds` later does `pendingInstantWithdraws -= _amount` and pulls `_amount` from the CDO, so a receipt claimed early is still funded a second time, leaving surplus underlying sitting in the strategy. [4](#0-3) 
- The strategy can hold spendable underlying while the receipt is still unfunded: (a) buffer-period deposits (`totEpochDeposits`) sit in the strategy until `startEpoch`, and (b) `collectWithdrawFunds` leaves loss-adjusted `lossRecoveryPriceByEpoch` reserves in the strategy balance until claimed — the latter additionally means an early instant claim spends funds earmarked for haircut victims. [5](#0-4) [6](#0-5) 
- The test suite itself confirms claims succeed purely on balance availability: "can redeem right away because new deposits are enough to cover all instant withdraws" and the failure case is only "there are no underlyings available". [7](#0-6) 

### Impact Explanation
Attacker (KYC-passing lender) in a pool with `allowInstantWithdraw`:
1. Buffer phase: deposits exist in the strategy (or a prior `stopEpochWithDuration` left a loss-recovery reserve).
2. Attacker calls `requestWithdraw` routed to the instant path for `X`, then immediately `claimInstantWithdrawRequest` — paid `X` from deposits/loss reserve even though the borrower has funded nothing.
3. `pendingInstantWithdraws` still contains `X`. At `startEpoch` the honest manager/keeper triggers `getInstantWithdrawFunds`/`collectInstantWithdrawFunds(X)`, pulling another `X` from the CDO into the strategy.
4. Attacker deposits `X'` ≤ `X`, requests instant withdraw again, and claims — draining the surplus. Net theft of `X` (paid twice for one receipt); alternatively the early claim permanently freezes loss-adjusted claimants whose earmarked reserve was spent.

Invariant broken: one receipt, one payout; and isolation of loss-recovery reserves. Loss equals up to the full instant request amount per cycle, repeatable each epoch while `allowInstantWithdraw` is set.

### Likelihood Explanation
Requires `allowInstantWithdraw` enabled and spendable strategy balance at claim time (buffer deposits or a loss-adjusted reserve) — both are normal operating states, not attacker-privileged conditions. No existing guard stops it: `_transferFundedClaim` only ring-fences `defaultRecoveryReserve`, and `pendingInstantWithdraws` is only reconciled in `collectInstantWithdrawFunds`.

### Recommendation
In `claimInstantWithdrawRequest`, only allow claims against actually-funded instant amounts: track a funded instant bucket (incremented in `collectInstantWithdrawFunds`), decrement `pendingInstantWithdraws`/the funded bucket and `instantWithdrawClaimsByEpoch` at claim time, and revert when the claim exceeds funded balance rather than relying on raw token balance.

### Proof of Concept
Foundry fork PoC sketch (modeled on `test/foundry/IdleCreditVault.t.sol` instant-withdraw tests):

```solidity
// Setup: pool with allowInstantWithdraw, attacker deposits and mints AA tranche.
uint256 mintedAA = idleCDO.depositAA(10000e18);
// Buffer deposits sit in the strategy (or a prior loss epoch left a reserve).

// Epoch N buffer: request instant withdraw of X
uint256 X = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));
uint256 pendingBefore = strategy.pendingInstantWithdraws(); // == X

// Claim immediately WITHOUT getInstantFunds() — succeeds because strategy holds deposits
cdoEpoch.claimInstantWithdrawRequest();
assertEq(strategy.instantWithdrawsRequests(address(this)), 0);
assertEq(strategy.pendingInstantWithdraws(), pendingBefore); // BUG: still X

// startEpoch -> CDO funds the already-claimed receipt a second time
_startEpochAndCheckPrices(N);
// strategy now holds X surplus underlying belonging to no receipt

// Attacker deposits X' <= X, requests + claims instant again, drains the surplus
uint256 minted2 = idleCDO.depositAA(X);
cdoEpoch.requestWithdraw(minted2, address(AAtranche));
cdoEpoch.claimInstantWithdrawRequest();
// Net: attacker received X + X' for a real position of X' -> stole X from the pool
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-393)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-429)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-614)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-907)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
  }
```

**File:** test/foundry/IdleCreditVault.t.sol (L4783-4804)
```text
    // can redeem right away because new deposits are enough to cover all instant withdraws
    cdoEpoch.claimInstantWithdrawRequest();

    assertEq(IERC20Detailed(strategyToken).balanceOf(address(this)), 0, 'user has no strategy tokens');
    assertEq(
      IERC20Detailed(defaultUnderlying).balanceOf(address(this)) - balPre, requestedAA1 + requestedAA2 + requestedBB, 'claimInstantWithdrawRequest is wrong'
    );
    assertEq(_strategy.instantWithdrawsRequests(address(this)), 0, 'instantWithdrawsRequests after claim is wrong');

    // we start a new epoch with less apr so instant withdrawal are available
    _stopEpochAndCheckPrices(1, initialProvidedApr / 4, _expectedFundsEndEpoch());
    // make instant redeem request for user1
    vm.startPrank(user1);
    uint256 requestedAAUser1 = cdoEpoch.requestWithdraw(0, address(BBtranche));
    vm.stopPrank();

    _startEpochAndCheckPrices(2);

    // user cannot claim right away because there are no underlyings available and funds must be 
    // requested from the borrower
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimInstantWithdrawRequest();
```
