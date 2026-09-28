### Title
Instant withdrawal claims skip funding verification, letting an unfunded request drain underlyings reserved for other claimants - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Analogous to GHSA-x93p-w2ch-fg67 — where a refactor dropped the "verify the old password" precondition so a sensitive action ran without its required check — `IdleCreditVault.claimInstantWithdrawRequest` pays out an instant-withdraw receipt without verifying that the request's epoch was actually funded by the borrower via `getInstantWithdrawFunds`/`collectInstantWithdrawFunds`. The only payout guard, `_transferFundedClaim`, protects `defaultRecoveryReserve` but not the vault balance backing other users' already-funded, unclaimed receipts.

### Finding Description
`claimInstantWithdrawRequest` (lines 380-393) does:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

Compare with the normal withdraw path: `_claimFundedWithdrawRequest` enforces epoch maturity (`epochNumber <= lastWithdrawRequest[_user]` → revert) before paying, because a request is only payable after `stopEpoch` collected borrower funds. The instant path has no equivalent "was this request funded" check — it never consults `instantWithdrawClaimsByEpoch`, `pendingInstantWithdraws`, or the epoch in which the request was created.

Flow:
1. Epoch N is running; vault holds underlyings previously collected for matured normal withdraws and/or earlier funded instant claims (pull-based claims routinely leave balance in the vault).
2. Attacker calls `requestInstantWithdraw(amount)`: their strategy tokens are burned, a receipt is minted, `instantWithdrawsRequests[attacker]` and `pendingInstantWithdraws` increase. At this point no borrower funds have been collected for this request.
3. Before `instantWithdrawDelay` elapses and before the manager calls `getInstantWithdrawFunds` (which pulls borrower funds through `collectInstantWithdrawFunds`), the attacker calls `claimInstantWithdrawRequest`.
4. `_transferFundedClaim` only checks `balance - defaultRecoveryReserve >= amount`; if `defaultRecoveryReserve == 0`, the whole vault balance is spendable. The attacker is paid from underlyings earmarked for other users' fulfilled receipts.

### Impact Explanation
Direct theft of other claimants' underlyings. The attacker burns receipt tokens worth `amount` and receives `amount` of underlyings immediately, while their request was never backed by collected borrower funds. Legitimate users' funded normal withdraws (`_claimFundedWithdrawRequest` → `_transferFundedClaim`) or earlier instant claims later revert on insufficient balance — permanent loss for them equal to the drained amount (bounded by the vault's unclaimed funded balance, which can be arbitrarily large during low-claim periods).

### Likelihood Explanation
Requires (a) instant withdraws enabled (`allowInstantWithdraw`, checked in `IdleCDOEpochVariant.claimInstantWithdrawRequest` line 977), (b) an epoch running so `requestInstantWithdraw` is reachable, and (c) vault balance > 0 from prior funding. Condition (c) is the normal state whenever claims are unclaimed. The attacker only needs to be a KYC-passing tranche holder — within the unprivileged threat model. No privileged cooperation needed; the manager's honest `getInstantWithdrawFunds` call would arrive too late.

Uncertainty I could not fully resolve within available context: whether a prefunded-mode variant pre-collects instant liquidity at `startEpoch`, which would already be vault balance legitimately attributable to instant requests — but even then the balance is fungible with matured normal-withdraw payouts, and nothing ties a claim to *its own epoch's* funding, so the misattribution still holds.

### Recommendation
Track funded instant claims per epoch and gate `claimInstantWithdrawRequest` on the request's epoch having been funded — e.g., record `instantWithdrawsRequestsByEpoch[user][epoch]` (already written at request time) and only pay receipts whose epoch was collected via `collectInstantWithdrawFunds`, or record a per-epoch funded flag/`fundedInstantClaims` counter and decrement on claim. Alternatively, escrow funded instant amounts in a dedicated reserve (like `defaultRecoveryReserve`) so `claimInstantWithdrawRequest` cannot spend generic vault balance.

### Proof of Concept
Foundry fork sketch (against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testUnfundedInstantClaimDrainsMaturedFunds() external {
    // victim deposits, epoch 0 runs and stops, victim requests normal withdraw
    uint256 amount = 10000 * ONE_SCALE;
    address victim = makeAddr('victim');
    _depositWithUser(victim, amount, true);
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    vm.prank(victim);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    // epoch 1 runs; borrower funds victim's receipt at stopEpoch -> vault holds amount
    _startEpochAndCheckPrices(1);
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());
    // victim does NOT claim yet

    // attacker deposits, epoch 2 starts, enables instant withdraw (manager, honest config)
    address attacker = makeAddr('attacker');
    uint256 atkAmt = amount;
    _depositWithUser(attacker, atkAmt, true);
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(1 days, 1e18, true);
    _startEpochAndCheckPrices(2);

    // attacker requests instant withdraw; NO getInstantWithdrawFunds call yet
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(0, address(AAtranche));

    // BUG: claim succeeds immediately, paying from victim's funded underlyings
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertGt(IERC20Detailed(defaultUnderlying).balanceOf(attacker) - balPre, 0);

    // victim's matured claim now reverts: vault was drained
    vm.expectRevert();
    vm.prank(victim);
    cdoEpoch.claimWithdrawRequest();
}
```

Key assertions: attacker receives underlyings before `pendingInstantWithdraws` for their request is funded, and victim's one-epoch-matured claim fails on insufficient vault balance. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-328)
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

**File:** contracts/IdleCDOEpochVariant.sol (L975-979)
```text
  function claimInstantWithdrawRequest() external {
    // Check that instant withdraws are available
    _checkNotAllowed(!allowInstantWithdraw);
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
  }
```
