### Title
Dust instant-withdraw requests permanently block `stopEpoch`, freezing the whole pool - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
Any KYC-allowed tranche holder can keep `pendingInstantWithdraws > 0` with dust-sized (1-wei) instant withdraw requests, which makes `stopEpoch` revert on every attempt. The epoch can never close, all borrowed principal stays locked with the borrower, and every pending withdraw receipt is frozen for as long as the attacker keeps spending gas.

### Finding Description
`IdleCDOEpochVariant._stopEpoch` refuses to close the epoch while any instant-withdraw receipt is unfunded: `_checkNotAllowed(... || _pendingInstant() != 0 || ...)` [1](#0-0) .

`requestWithdraw` converts a normal request into an instant one whenever the APR dropped vs. the last epoch (`lastEpochApr > currentApr + instantWithdrawAprDelta`), and forwards `_underlyings` to `IdleCreditVault.requestInstantWithdraw` [2](#0-1) . The vault mints the receipt and unconditionally increments `pendingInstantWithdraws` [3](#0-2) .

The only way to drain `pendingInstantWithdraws` is the manager-only `getInstantWithdrawFunds`, which itself requires `block.timestamp >= instantWithdrawDeadline` [4](#0-3) . After the manager funds the pending amount, the attacker simply submits another dust `requestWithdraw` (1 wei of tranche tokens) before `stopEpoch` is executed — the revert window between the two manager calls is permissionless to exploit.

Attack sequence (running epoch, APR was lowered at the previous `stopEpoch` so `lastEpochApr > currentApr + instantWithdrawAprDelta` holds all epoch):

1. Attacker holds 1 wei of AA tranche (KYC-passed lender, unprivileged per rules).
2. Epoch ends; manager must call `getInstantWithdrawFunds` then `stopEpoch`.
3. Attacker calls `requestWithdraw(1, AATranche)` → instant path → `pendingInstantWithdraws = 1`.
4. Manager calls `getInstantWithdrawFunds` → pulls 1 wei from borrower → `pendingInstantWithdraws = 0`.
5. Attacker re-runs step 3 (fresh dust request; condition on APR still true because `unscaledApr` is unchanged until the next successful `stopEpoch`).
6. `stopEpoch` always sees `_pendingInstant() != 0` → `NotAllowed` revert, forever.

### Impact Explanation
Temporary-to-permanent freezing of the entire vault, mirroring the CVE's hang/crash DoS. While the epoch cannot be stopped:

- All principal lent to the borrower (full TVL) cannot be recalled; the `_isRequestingAllFunds` close-pool path (`stopEpoch(.., 1)`) is equally blocked.
- No new epoch can start and no `expectedEpochInterest` can be realized.
- Every queued `withdrawsRequests`/`apr0Users` receipt stays claimable only after `epochNumber` advances, which requires `stopEpoch` — so pending withdrawers are frozen too [5](#0-4) .

Quantified loss: the attacker's cost is ~1 wei of tranche tokens plus gas per cycle; the frozen amount is `getContractValue()` (entire NAV) plus all pending receipts.

### Likelihood Explanation
Requires a deployment where the manager lowers the APR (so the instant-withdraw window opens — a normal operational event) and `disableInstantWithdraw == false`. The attacker needs only a KYC-passed wallet with a dust tranche balance — an allowed unprivileged role. No privileged misbehavior needed; the borrower only needs 1 wei of liquidity to satisfy `getInstantWithdrawFunds`, which doesn't break the loop. Guards don't stop it: `isWalletAllowed` passes, `_skimDonatedAssets` is irrelevant, and the epoch gating is exactly what is being abused.

### Recommendation
- Enforce a minimum `requestInstantWithdraw`/`requestWithdraw` amount (e.g., ≥ `1` unit of `ONE_TRANCHE_TOKEN` worth of underlying) to raise the griefing cost above dust.
- Alternatively, treat sub-dust pending amounts as funded (round `pendingInstantWithdraws` below a threshold to zero in `collectInstantWithdrawFunds`), or let `stopEpoch` sweep residual dust pending amounts into the claimable reserve automatically instead of reverting.

### Proof of Concept
Reproducible Foundry fork sketch on `test/foundry/IdleCreditVault.t.sol` setup:

```solidity
// after _stopEpochAndCheckPrices with a lower apr (instant window open)
address griefer = makeAddr('griefer');
_depositWithUser(griefer, 2, true); // dust AA position, KYC'd

_startEpochAndCheckPrices(1);
// loop: attacker keeps pendingInstantWithdraws > 0
for (uint i; i < 3; ++i) {
    vm.prank(griefer);
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // dust instant request
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();             // drains 1 wei
    vm.prank(griefer);
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // re-add dust
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(initialProvidedApr / 4, 0);   // always reverts
}
assertTrue(cdoEpoch.isEpochRunning()); // epoch never closes, TVL frozen
```

Note: I could not execute the PoC; residual uncertainty is whether `unscaledApr`/`lastEpochApr` keeps the instant condition open across repeated requests (it should, since `lastEpochApr` only updates on a successful `stopEpoch`, which is precisely what is being blocked).

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L338-351)
```text
    _checkNotAllowed(
      // Check that epoch is running
      !isEpochRunning || 
      // Check that end date is passed
      block.timestamp < epochEndDate || 
      // Check that there are no pending instant withdraws, ie `getInstantWithdrawFunds` was called
      // before closing the epoch
      _pendingInstant() != 0 ||
      // Check that overridden interest, if passed (ie > 1), is greater than pending withdraw fees and the apr is 0 
      // otherwise withdrawal requests may not be fullfilled as they consider also the interest gained in the next epoch 
      (_interest > 1 && (_interest < _pendingWithdrawFees || _newApr != 0)) ||
      // Closing already recalls all principal, so applying a separate loss burn would strand returned cash.
      (_isRequestingAllFunds && _lossAmount != 0)
    );
```

**File:** contracts/IdleCDOEpochVariant.sol (L558-573)
```text
  function getInstantWithdrawFunds() external {
    _checkOnlyOwnerOrManager();
    // Check that programmable mode is disabled, the epoch is running and the deadline passed.
    _checkNotAllowed(isProgrammableBorrower || !isEpochRunning || block.timestamp < instantWithdrawDeadline);

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _instantWithdraws = _pendingInstant();
    // transfer funds for instant withdraw to this contract
    try this.getFundsFromBorrower(_instantWithdraws) {
      // transfer funds to IdleCreditVault and decrease pendingInstantWithdraws
      _strategy.collectInstantWithdrawFunds(_instantWithdraws);
      // allow instant withdraws
      allowInstantWithdraw = true;
    } catch {
      _handleBorrowerDefault(_instantWithdraws);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L761-769)
```text
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-349)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-374)
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
```
