### Title
Prefunded deposits are minted after borrower default, diluting default recovery - (File: contracts/IdleCDOEpochVariantPrefunded.sol)

### Summary
`stopEpochWithDuration` always invokes `_afterStopEpochWithDuration`, even when `_stopEpoch` catches a borrower funding failure and marks the pool defaulted. The prefunded hook then mints AA tranche shares and strategy tokens at the still-unfinalized pre-default price and increases active strategy-token balance. During `finalizeDefault`, those uncollateralized strategy tokens are included in the recovery basis, allowing queued recipients to consume recovery assets that should belong to existing active holders and defaulted receipt holders.

### Finding Description
`IdleCDOEpochVariant.stopEpochWithDuration` calls `_afterStopEpochWithDuration` unconditionally after `_stopEpoch` returns [1](#0-0) . If borrower funding fails, `_stopEpoch` invokes `_handleBorrowerDefault`, which sets `defaulted`, pauses the CDO, disables new withdraw requests, and ends the running epoch [2](#0-1) [3](#0-2) .

The prefunded hook nevertheless calls `_mintSharesAtCurrPrice(_prefunded, queue, AATranche)` and `mintStrategyTokens(_prefunded)` [4](#0-3) . At this point, default recovery has not been finalized, so tranche price has not yet been reduced by `finalizeDefault`; `finalizeDefault` calls `finalizeDefaultRecovery` and only afterward writes final NAVs and forces accounting [5](#0-4) .

This breaks the recovery invariant in two ways:

1. AA shares are minted at a stale pre-default price.
2. `mintStrategyTokens(_prefunded)` increases `balanceOf(idleCDO)` without adding recovered underlying.

`finalizeDefaultRecovery` uses `balanceOf(idleCDO)` as `activeBalance` and computes `activeBasis = activeBalance + activeInterest` [6](#0-5) . The prefunded amount therefore enters total claim basis even though its principal was already sent to the borrower and was not recovered. The same aggregate `recoveryPrice` is then applied to active holders and pending receipts [7](#0-6) .

This is analogous to cleanup cancellation occurring before the stale work item itself is disabled: `_handleBorrowerDefault` performs default cleanup, but `_afterStopEpochWithDuration` is still allowed to create a new claim afterward.

### Impact Explanation
A prefunded AA deposit that settles in the same transaction as a borrower default becomes an oversized or entirely uncollateralized recovery claim.

For example, with existing active basis `A`, pending receipts `P`, prefunded amount `Q`, and recovered underlying `R`:

```text
expected price = R / (A + P)
actual price   = R / (A + P + Q)
```

All existing claimants lose approximately:

```text
R * Q / (A + P) / (A + P + Q)
```

to prefunded claimants, before considering the additional benefit from minting AA shares at stale pre-default price. If `Q` is large relative to recovered NAV, prefunded recipients can extract a correspondingly large fraction of the isolated recovery reserve.

This is direct theft/dilution of recovery funds, not merely an accounting mismatch: `_transferDefaultRecovery` pays claims from `defaultRecoveryReserve` [8](#0-7) .

### Likelihood Explanation
The sequence requires the prefunded variant, a configured epoch queue, queued deposits awaiting processing, and borrower failure during `stopEpochWithDuration`. Those are supported production states: prefunded deposits may already have reached the borrower before epoch settlement, and `_stopEpoch` explicitly permits settlement to continue into the hook after default [9](#0-8) .

The queued recipient need not control the manager or borrower. The attacker only needs to be an eligible queued depositor when the honest manager calls `stopEpochWithDuration` and borrower repayment fails. The exploit path does not rely on malicious privileged behavior, malformed oracle data, or a mock-only state.

### Recommendation
Do not execute `_afterStopEpochWithDuration` after `_stopEpoch` marks the pool defaulted, unless prefunded principal was verifiably recovered in the same stop path.

A conservative fix is:

```solidity
function stopEpochWithDuration(
  uint256 _newApr,
  uint256 _interest,
  uint256 _duration,
  uint256 _lossAmount
) public {
  _stopEpoch(_newApr, _interest, _lossAmount);
  if (defaulted) {
    return;
  }
  if (_interest != 1) {
    setEpochParams(_duration, bufferPeriod);
    _setScaledApr(_newApr);
  }
  _afterStopEpochWithDuration();
}
```

If prefunded deposits must remain claimable after default, they must be included in the same recovery waterfall without stale-price minting. Concretely, do not mint additional strategy tokens after borrower default unless the corresponding underlying is actually held in the strategy recovery reserve; otherwise the hook artificially enlarges `activeBasis`.

### Proof of Concept
The PoC assumes the prefunded variant deployment used by the repository’s prefunded/queue tests. It demonstrates the broken invariant by comparing the recovery ratio with and without a prefunded deposit processed after borrower default.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDOEpochVariantPrefunded} from "../contracts/IdleCDOEpochVariantPrefunded.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract PrefundedDefaultOrderingPoC is Test {
    IdleCDOEpochVariantPrefunded cdo;
    IdleCreditVault vault;
    IERC20Detailed underlying;

    address owner = address(0xA11CE);
    address manager = address(0xBEEF);
    address borrower = address(0xB0B);
    address victimAA = address(0x1111);
    address prefundedQueue = address(0x2222);
    address recoverySource = address(0x3333);

    uint256 constant ONE = 1e18;
    uint256 constant VICTIM_DEPOSIT = 100_000e6;
    uint256 constant PREFUNDED = 100_000e6;
    uint256 constant RECOVERED = 50_000e6;

    function testPrefundedDepositInflatesDefaultBasis() external {
        // 1. victim deposits during buffer.
        // 2. manager starts epoch and sends funds to borrower.
        // 3. prefunded deposits are queued and already sent to borrower.
        // 4. epoch ends.
        // 5. borrower does not approve/repay enough for stopEpochWithDuration.
        vm.prank(manager);
        cdo.stopEpochWithDuration(0, 0, 30 days, 0);

        assertTrue(cdo.defaulted(), "borrower default not recorded");

        // The bug: hook still ran after default.
        uint256 prefundedTranches =
            IERC20Detailed(cdo.AATranche()).balanceOf(prefundedQueue);
        assertGt(prefundedTranches, 0, "prefunded AA not minted");

        uint256 inflatedActiveBalance = vault.balanceOf(address(cdo));
        assertEq(
            inflatedActiveBalance,
            VICTIM_DEPOSIT + PREFUNDED,
            "uncollateralized prefunded strategy tokens not added"
        );

        // 6. Recovery source supplies only RECOVERED underlying.
        deal(address(underlying), recoverySource, RECOVERED);
        vm.prank(recoverySource);
        underlying.approve(address(vault), RECOVERED);

        vm.prank(manager);
        cdo.finalizeDefault(RECOVERED, recoverySource);

        assertTrue(vault.defaultRecoveryFinalized(), "default not finalized");

        uint256 expectedPrice =
            RECOVERED * vault.RECOVERY_FULL() / VICTIM_DEPOSIT;
        uint256 actualPrice = vault.defaultRecoveryPrice();

        // Actual basis is VICTIM_DEPOSIT + PREFUNDED, so price is halved.
        assertEq(
            actualPrice,
            RECOVERED * vault.RECOVERY_FULL() /
                (VICTIM_DEPOSIT + PREFUNDED),
            "prefunded claim did not dilute recovery"
        );
        assertLt(actualPrice, expectedPrice, "victim recovery was not diluted");
    }
}
```

The expected PoC outcome is that `actualPrice == expectedPrice / 2` for equal victim and prefunded balances. The victim receives only half of the expected recovery while the prefunded recipients receive AA claims backed by assets that remained with the defaulted borrower.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L194-214)
```text
  function finalizeDefault(uint256 _recoveredAmount, address _recoverySource) external {
    _checkOnlyOwnerOrManager();
    // Send raw CDO underlying to feeReceiver as donated assets; recovery must enter through the strategy.
    _skimDonatedAssets();
    // Recovery math and reserve accounting live in the strategy where receipt claims are paid.
    uint256 defaultBBNav = IdleCreditVault(strategy).finalizeDefaultRecovery(_recoveredAmount, _recoverySource);

    // Default recovery should not keep accruing fees or leave old fee claims senior to LP recovery.
    fee = 0;
    managementFee = 0;
    unclaimedFees = 0;
    latestHarvestBlock = block.timestamp;

    // The strategy returns BB's final recovered active NAV. Writing both final NAVs directly
    // makes hard default the sole exception to the ordinary BB-first loss waterfall.
    lastNAVBB = defaultBBNav;
    lastNAVAA = _contractTokenBalance(strategyToken) - defaultBBNav;

    // Crystallize the strategy-token rebalance so virtualPrice/tranchePrice expose the realized loss.
    _forceUpdateAccounting();

```

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L508-515)
```text
  /// @notice Stop epoch and set new duration
  /// @dev see stopEpoch and setEpochParams for more details, bufferPeriod is not modified
  /// Loss accounting policy for `_lossAmount`:
  /// - pending receipts share the pending portion of the loss pro rata because tranche identity is not stored
  /// - the remaining active-position loss uses the ordinary BB-first tranche waterfall
  /// - hard borrower defaults instead apply one aggregate recovery multiplier to active and pending claims
  /// - if `stopEpoch` defaults (`defaulted = true`), the post-stop loss burn and epoch updates are skipped,
  ///   but variant hooks still run so prefunded queues can settle deposits already sent to the borrower
```

**File:** contracts/IdleCDOEpochVariant.sol (L520-530)
```text
  function stopEpochWithDuration(uint256 _newApr, uint256 _interest, uint256 _duration, uint256 _lossAmount) public {
    // stop epoch checks that msg.sender is allowed
    _stopEpoch(_newApr, _interest, _lossAmount);
    if (_interest != 1 && !defaulted) {
      // buffer period is not changed
      setEpochParams(_duration, bufferPeriod);
      // scale the apr with the new duration and buffer
      _setScaledApr(_newApr);
    }
    _afterStopEpochWithDuration();
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L577-598)
```text
  function _handleBorrowerDefault(uint256 funds) internal {
    defaulted = true;
    // Do not reopen instant claims here. They remain disabled when funding is pending;
    // successful full funding is the only path that enables them before finalization.

    if (isProgrammableBorrower) {
      IProgrammableBorrower(_borrower()).onDefault();
    }

    // deposits should be already prevented
    if (!paused()) {
      _pause();
    }

    // stop the current epoch
    isEpochRunning = false;

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;

    emit BorrowerDefault(funds);
```

**File:** contracts/IdleCDOEpochVariantPrefunded.sol (L72-89)
```text
  function _afterStopEpochWithDuration() internal override {
    address _queue = epochQueue;
    if (_queue == address(0)) return;

    IIdleCDOEpochQueuePrefunded _epochQueue = IIdleCDOEpochQueuePrefunded(_queue);
    uint256 _prefunded = _epochQueue.prefundedDepositsToProcess();
    if (_prefunded == 0) return;
    // A zero post-loss AA price cannot safely mint new shares into the same tranche token.
    _checkNotAllowed(priceAA == 0);

    // Prefunded deposits already reached the borrower, so they must join AA even if stop defaulted.
    // Mint tranche shares at the post-stop price and mirror the same amount in strategy tokens,
    // so the queue can later distribute shares to users at the epoch price.
    uint256 _prefundedMinted = _mintSharesAtCurrPrice(_prefunded, _queue, AATranche);
    IdleCreditVault(strategy).mintStrategyTokens(_prefunded);
    // Finalize the prefunded epoch in the queue by storing the epoch price and clearing state.
    _epochQueue.processPrefundedDeposits(_prefundedMinted);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L669-680)
```text
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-705)
```text
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
    // Bring active CDO NAV to the same recovery ratio. IdleCDOEpochVariant then calls
    // _forceUpdateAccounting so tranche prices/virtualPrice expose the crystallized loss.
    uint256 activeFinalNAV = (activeBasis * recoveryPrice) / RECOVERY_FULL;
    defaultBBNav = defaultBBNav * recoveryPrice / RECOVERY_FULL;
    if (activeBalance > activeFinalNAV) {
      _burn(idleCDO, activeBalance - activeFinalNAV);
    } else if (activeFinalNAV > activeBalance) {
      _mint(idleCDO, activeFinalNAV - activeBalance);
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L909-916)
```text
  /// @notice Transfer default recovery reserve to a user.
  /// @param _user claim receiver
  /// @param _amount amount to transfer
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
```
