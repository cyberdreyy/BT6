### Title
Partially funded instant-withdraw receipts are paid in full, draining funded normal-withdraw reserves - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analog of the `snprintf` integer-underflow report: in Wazuh, a "remaining size" counter underflows so subsequent writes lose their bound. Here, the "remaining unfunded instant-withdraw" counter `pendingInstantWithdraws` is decremented only by what was actually collected, but `claimInstantWithdrawRequest` never consults the residual deficit — it burns and pays the full user receipt. The funding shortfall is silently dropped from the accounting, exactly like the wrapped-around buffer-size budget, so an unfunded (or partially funded) claim is paid out of underlyings reserved for other users' receipts.

### Finding Description
At `startEpoch`, IdleCDO calls `collectInstantWithdrawFunds(min(pendingInstant, totUnderlyings))`. When `pendingInstant > totUnderlyings`, only part of the instant queue is funded: [1](#0-0) 

`collectInstantWithdrawFunds` reduces `pendingInstantWithdraws` by the collected amount only; per-user `instantWithdrawsRequests[_user]` is never haircut or reduced: [2](#0-1) 

`claimInstantWithdrawRequest` then burns the user's *entire* receipt and calls `_transferFundedClaim(_user, amount)` with the full `instantWithdrawsRequests[_user]`: [3](#0-2) 

The only guard in `_transferFundedClaim` protects `defaultRecoveryReserve`, which is zero outside a finalized default: [4](#0-3) 

So in a non-default epoch the strategy's balance contains underlyings collected via `collectWithdrawFunds` for normal pending receipts. A partially funded instant claimant is paid the full receipt from that balance, over-spending the strategy's funded budget — the deficit (`pendingInstantWithdraws`) is the "wrapped" remainder that is never checked.

### Impact Explanation
The instant claimant receives up to `instantWithdrawsRequests[user] - actuallyFunded` more underlyings than were reserved for them. Those funds are backing normal withdraw receipts funded in a prior `stopEpoch` (`pendingWithdraws` basis paid via `claimWithdrawRequest` → `_transferFundedClaim`). The first instant claimant steals that backing; later normal-receipt claimants' `safeTransfer` reverts or pays less, i.e. permanent loss/theft of funded, unclaimed yield for honest users — quantified as the unfunded portion of the instant queue, unbounded up to the strategy's whole funded balance.

### Likelihood Explanation
Requires an attacker who is a KYC'd tranche holder. Pre-conditions: an epoch where `unscaledApr` drops by more than `instantWithdrawAprDelta` so `requestWithdraw` enters the instant path (`IdleCDOEpochVariant.sol` ~L761-768, which mints the receipt), followed by a `startEpoch` where the CDO's free underlying balance (`totUnderlyings`) is smaller than `pendingInstant` — e.g. when most TVL is already lent to the borrower and buffer deposits are small. Both conditions arise through normal market operation and honest manager calls; the attacker only chooses timing/size of their instant request. One caveat not fully verified within the iteration limit: whether the CDO-side entry point that relays `claimInstantWithdrawRequest` (the `getInstantWithdrawFunds`/claim path gated by `allowInstantWithdraw`/`instantWithdrawDeadline`) independently blocks claims while `pendingInstantWithdraws != 0`. From the strategy code itself there is no such check, so the missing-deficit accounting stands on the strategy's own logic.

### Recommendation
In `claimInstantWithdrawRequest`, only allow payout of the funded portion: track per-user funded instant claims (or cap the payout at `instantWithdrawClaimsByEpoch[epoch] - outstandingDeficit`), and keep the unfunded remainder in `instantWithdrawsRequests`/`pendingInstantWithdraws` until a later `collectInstantWithdrawFunds` covers it — mirroring how `pendingWithdraws`/`collectWithdrawFunds` handle underfunding with `lossRecoveryPriceByEpoch`. Alternatively revert claims while `pendingInstantWithdraws != 0` for the current epoch.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVaultInstantUnderfund.t.sol
// Fork: mainnet, fixed-APR IdleCDOEpochVariant + IdleCreditVault.
function test_InstantClaimOverpaysUnfundedDeficit() external {
    // 1. KYC'd attacker deposits into AA tranche during buffer.
    _depositWithUser(attacker, 100_000 * ONE_SCALE, true); // idleCDO.depositAA

    // 2. Epoch runs, APR set low so next requests take instant path,
    //    or force lastEpochApr high then stopEpoch with low _newApr.
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    // honest manager stops epoch with much lower APR
    vm.prank(manager);
    cdoEpoch.stopEpoch(lowApr, interest); // lastEpochApr - newApr > instantWithdrawAprDelta

    // 3. Attacker requests instant withdraw; receipt minted in full.
    vm.prank(attacker);
    uint256 req = cdoEpoch.requestWithdraw(0, address(AAtranche)); // full balance

    // 4. Drain CDO free liquidity so pendingInstant > totUnderlyings:
    //    honest users' normal withdraws are funded in same/next stopEpoch,
    //    borrower already holds most TVL.
    //    ... (arrange so _contractTokenBalance < pendingInstant)

    // 5. startEpoch: collectInstantWithdrawFunds sends only totUnderlyings.
    vm.prank(manager);
    cdoEpoch.startEpoch();
    assertGt(strategy.pendingInstantWithdraws(), 0); // deficit remains

    // 6. Attacker claims the FULL receipt anyway.
    uint256 pre = underlying.balanceOf(attacker);
    // via the CDO instant-claim entry point
    cdoEpoch.claimInstantWithdrawRequest(); // or getInstantWithdrawFunds path
    uint256 paid = underlying.balanceOf(attacker) - pre;

    assertEq(paid, req); // paid in full despite partial funding
    // 7. Honest pending-withdraw claimant now fails: strategy balance drained.
    vm.expectRevert(); // safeTransfer underflow / insufficient balance
    cdoEpoch.claimWithdrawRequest();
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L279-290)
```text
    uint256 pendingInstant = _pendingInstant();
    uint256 totUnderlyings = _contractTokenBalance(token);
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
    _strategy.collectInstantWithdrawFunds(pendingInstant > totUnderlyings ? totUnderlyings : pendingInstant);

    // if there are more requests than the current underlyings we simply send all underlyings
    // to the IdleCreditVault contract
    if (pendingInstant > totUnderlyings) {
      // if borrower is programmable, notify epoch start even if no funds were sent
      _startEpochProgrammableBorrower(_pendingWithdraws);
      return;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-906)
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
```
