### Title
`claimInstantWithdrawRequest` pays receipts without verifying they were actually funded, letting an unprivileged requester drain deposited/queued liquidity at par — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to `tpm2 checkquote` failing to verify that a quote was actually produced by the TPM, `IdleCreditVault.claimInstantWithdrawRequest` never verifies that an instant-withdraw receipt was actually *funded* before paying it. It simply burns `instantWithdrawsRequests[_user]` and transfers the full amount via `_transferFundedClaim`, which only checks the strategy's raw underlying balance (and the default-recovery reserve guard). Any underlying sitting in the strategy — most notably lender deposits collected by `deposit()` during the buffer period before `startEpoch` forwards them, or funds collected for *other* users' instant requests — is treated as proof of funding.

### Finding Description
- `requestInstantWithdraw` burns the CDO's strategy tokens, mints a receipt to the user, and increments `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` [1](#0-0) .
- Funding is a *separate* step: `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` and pulls underlying from the CDO [2](#0-1) . So at any moment, `instantWithdrawsRequests` mixes funded and unfunded receipts — there is no funded/unfunded per-user distinction (the "quote" is never bound to its "TPM").
- `claimInstantWithdrawRequest` pays `instantWithdrawsRequests[_user]` in full as long as `balanceOf(strategy)` covers it [3](#0-2) , and `_transferFundedClaim` performs no funding check — only a default-reserve isolation check [4](#0-3) .
- The strategy legitimately holds underlying that does not belong to instant claimants: `deposit()` pulls underlying into the strategy during the buffer period and keeps it there until the borrower/epoch flow moves it [5](#0-4) , and `collectInstantWithdrawFunds`/`collectWithdrawFunds` deposit funds earmarked for *other* claimants.

Attack path (buffer phase, running or stopped epoch, unprivileged KYC'd lender via the CDO):
1. LPs deposit; the strategy now holds `X` underlying awaiting `startEpoch` (or holds funded claims of other users).
2. Attacker deposits (or uses existing position) and calls `requestInstantWithdraw` for amount `A ≤ X` through the IdleCDO. `pendingInstantWithdraws += A`; no funding has occurred.
3. Attacker immediately calls `claimInstantWithdrawRequest`. The contract burns the receipt and pays `A` at par out of other users' liquidity, before `collectInstantWithdrawFunds` ever ran.
4. When the CDO later funds the queue, the collected `A` sits unclaimed in the strategy; the earlier legitimate claimants whose liquidity was consumed (buffer deposits now short, or other funded receipts) absorb the shortfall — insolvency/freeze propagates to them.

The same defect applies to `_claimFundedWithdrawRequest` for normal requests: `withdrawsRequests[_user]` is paid at par against raw balance with no per-request binding to `collectWithdrawFunds` proceeds [6](#0-5) .

### Impact Explanation
Direct theft of other users' funded claims and/or buffer-period deposits: an attacker exits instantly at 1:1 using liquidity earmarked for other claimants, violating the "one receipt, one funded payout" invariant. Quantified loss equals the strategy's non-reserve underlying balance at claim time. When the queue is eventually funded, residual claimants find the strategy undercollateralized and revert in `_transferFundedClaim`/`_transferDefaultRecovery` — permanent freezing for the last claimants if the shortfall is never topped up.

### Likelihood Explanation
Requires only an unprivileged tranche holder able to call the CDO's instant-withdraw path while the strategy holds underlying (buffer periods happen every epoch; funded-claim balances for slow claimers can persist indefinitely). No privileged collusion needed; the missing funded-vs-unfunded binding is structural. Partially mitigated only if integrators never leave underlying in the strategy — which the code itself does during every buffer and while receipts are pending.

### Recommendation
Track funded vs unfunded instant (and normal) receipts explicitly — e.g., a `fundedInstantWithdraws`/`fundedWithdraws` counter incremented only in `collectInstantWithdrawFunds`/`collectWithdrawFunds`, and per-user claimability derived from funded portions (or FIFO allocation). Revert `claimInstantWithdrawRequest`/`_claimFundedWithdrawRequest` when the user's receipt exceeds the funded allocation, rather than paying against raw `balanceOf`.

### Proof of Concept
Foundry fork PoC sketch (IdleCreditVault + IdleCDOEpochVariant deployed per `test/`):

```solidity
// Buffer phase: epoch not running, strategy holds idle deposits
vm.prank(user1); cdo.depositAA(1000e6);           // strategy now holds 1000 USDC
// Attacker already holds tranche tokens; instant withdraw enabled
vm.prank(attacker); cdo.requestInstantWithdraw(1000e6); // instantWithdrawsRequests[attacker]=1000e6, pendingInstant+=1000e6
// No collectInstantWithdrawFunds call — request is UNFUNDED
vm.prank(attacker); cdo.claimInstantWithdrawRequest();  // pays 1000e6 at par from user1's deposit
assertEq(usdc.balanceOf(attacker), attackerBefore + 1000e6);
assertEq(usdc.balanceOf(address(strategy)), 0);         // buffer deposits drained
// Later stopEpoch/collectInstantWithdrawFunds tops up the strategy, but user1's
// deposit backing is gone -> insolvency pushed onto remaining claimants/LPs.
```

Caveat: I could not fully trace the CDO-side gating (`isInstantWithdrawEnabled` / allowed flags in `IdleCDOEpochVariant.sol`) within this session; the PoC assumes instant withdrawals are enabled during the buffer phase as designed, and that `claimInstantWithdrawRequest` is reachable — both consistent with the code read.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-350)
```text
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    // Claim includes:
    // - settled APR0 principal from finalized epochs
    // - still-open APR0 principal: if pool-close mode was used (_interest == 1), IdleCDO sets
    //   epochEndDate = 0 and claims can be immediate, while _settleApr0 can still skip settlement
    //   for the current request epoch (reqEpoch >= epochNumber).
    // - settled APR0 interest
    uint256 normalAmount = withdrawsRequests[_user];
    uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
    uint256 apr0InterestAmount = _apr0User.settledInterest;
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
  }
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L595-617)
```text
  /// @param _amount number of underlyings to transfer
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
