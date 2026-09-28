### Title
`lastEpochInterest` is never cleared after `startEpoch` consumes it, so the previous epoch's interest is re-requested from the borrower on every subsequent epoch start - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
Analogous to `lastRewardGlobal` not being cleared on deposit, `lastEpochInterest` is an accumulator that is *consumed* inside `startEpoch` (added to the amount the CDO must forward to the strategy/borrower) but is only overwritten — never reset — in `_stopEpoch`. Any path that consumes the value without clearing it leaves the stale value to be double-applied on the next epoch start.

### Finding Description
`lastEpochInterest` is set in `_stopEpoch` to the net gain of the epoch (`netInterest = _grossInterest - _totalFees`) and the corresponding underlyings are immediately pushed into the strategy via `_strategy.deposit(netInterest)` [1](#0-0) . In `startEpoch`, the same variable is added to the funds that must be forwarded: `uint256 _toSend = isInterestMinted ? _totEpochDeposits : lastEpochInterest + _totEpochDeposits;` and `_strategy.sendInterestAndDeposits(_toSend)` [2](#0-1) . `startEpoch` never resets `lastEpochInterest` to 0.

Concretely:

- Epoch N stops: `lastEpochInterest = I_N`; the CDO deposits `I_N` underlyings into `IdleCreditVault` (`_strategy.deposit(netInterest)`), so those funds now back `strategyToken` held by the CDO — they no longer sit as raw `token` in the CDO.
- Epoch N+1 starts (non-minted mode, `isInterestMinted == false`): `sendInterestAndDeposits` is asked for `lastEpochInterest + totEpochDeposits = I_N + deposits`. The `I_N` component has already been settled into the strategy once; the CDO only holds the buffer deposits plus any residual, since raw underlyings are skimmed/deposited and `sendFundsToBorrower` drains the surplus each epoch.
- Result: `sendInterestAndDeposits` either pulls funds the CDO does not hold (revert → `startEpoch` permanently fails → epoch can never start, lenders' tranche tokens frozen because `epochDuration`/epoch gating blocks normal flows) or, if the strategy fronts the amount, the borrower is credited `I_N` a second time, i.e., the same interest is paid twice — direct value leakage equal to the previous epoch's net interest.

The invariant violated is "a settled accumulator must be cleared when consumed" — exactly the `lastRewardGlobal` bug class: a value tracking the pending reward/interest between settlement points survives its settlement and corrupts the next accounting round. No guard catches this: `startEpoch` only checks `defaulted`, buffer timing, and `epochDuration != 0` [3](#0-2) ; `_skimDonatedAssets` runs before but does not touch `lastEpochInterest`; `finalizeDefault` zeroes it only on the hard-default path [4](#0-3) . In minted mode the bug is masked because `_toSend` ignores `lastEpochInterest` [5](#0-4) .

### Impact Explanation
- Permanent freezing: `startEpoch` reverts every time once `lastEpochInterest > 0` exceeds the CDO's raw token balance, so the vault can never transition to a running epoch; withdraw requests, deposits, and claims are gated on the epoch state machine and funds are locked (in scope: permanent freezing of user funds).
- If the strategy tolerates the overpull, the borrower/epoch accounting is charged `lastEpochInterest` twice — a quantified double payment equal to the previous epoch's net interest.

### Likelihood Explanation
Requires no attacker action beyond normal sequencing: any non-minted credit vault that completes one profitable epoch (`lastEpochInterest > 0`) and then has the honest owner/manager call `startEpoch` for the next epoch hits it. No privileged role misbehaves; the bug is deterministic once the state is reached.

### Recommendation
Clear `lastEpochInterest` inside `startEpoch` after it is consumed (set `lastEpochInterest = 0` alongside `expectedEpochInterest`/request-flag resets), or recompute the amount to forward from current balances rather than from a persisted accumulator.

### Proof of Concept
Foundry fork PoC sketch (requires `isInterestMinted == false` mode):

```solidity
// 1. Lenders deposit AA during buffer; owner calls startEpoch() -> epoch 1 running.
// 2. Warp past epochEndDate; owner calls stopEpoch(newApr, 0).
//    -> lastEpochInterest = netInterest > 0; _strategy.deposit(netInterest)
//       moves I_1 underlyings out of the CDO's raw token balance.
// 3. Lenders deposit again during buffer (totEpochDeposits = D).
// 4. Warp bufferPeriod; owner calls startEpoch().
//    -> _toSend = lastEpochInterest + D = I_1 + D, but the CDO only holds ~D
//       (I_1 is already strategyToken-backed inside IdleCreditVault).
//    -> assert: vm.expectRevert on the strategy's pull of I_1 + D, or
//       assert token.balanceOf(cdo) < I_1 + D before the call.
// 5. Epoch can never start again: epochEndDate stays in the past and every
//    retry requests the same stale I_1 -> funds permanently frozen.
```

Uncertainty note: the exact revert site depends on `IdleCreditVault.sendInterestAndDeposits` internals (whether it pulls raw underlyings from the CDO via `transferFrom` or forwards strategy-held buffer deposits); the CDO-side accounting confirms `lastEpochInterest` is never reset in `startEpoch`, so the stale-value reuse is present regardless of which sub-branch of `sendInterestAndDeposits` executes.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L215-224)
```text
    expectedEpochInterest = 0;
    pendingWithdrawFees = 0;
    allowAAWithdrawRequest = true;
    allowBBWithdrawRequest = true;
    // This flag gates claimInstantWithdrawRequest. New instant requests stay disabled below.
    allowInstantWithdraw = true;
    disableInstantWithdraw = true;
    epochDuration = 0;
    epochEndDate = 0;
    _setScaledApr(0);
```

**File:** contracts/IdleCDOEpochVariant.sol (L233-247)
```text
  function startEpoch() external {
    _checkOnlyOwnerOrManager();

    // Check that buffer period passed (and epoch is not running as epochEndDate is set)
    // and that the pool is not closed (ie epochDuration == 0)
    uint256 _epochDuration = epochDuration; 
    _checkNotAllowed(defaulted || block.timestamp < (epochEndDate + bufferPeriod) || _epochDuration == 0);
    _checkProgrammableBorrowerMode();
    // Remove raw donated underlyings before calculating epoch interest or borrower transfer amounts.
    _skimDonatedAssets();

    isEpochRunning = true;
    // prevent deposits
    _pause();

```

**File:** contracts/IdleCDOEpochVariant.sol (L270-274)
```text
    // transfer in this contract funds from interest payment (if any) and buffer deposits sent to the strategy
    uint256 _totEpochDeposits = _strategy.totEpochDeposits();
    // If interest is minted we do not transfer interest to the strategy
    uint256 _toSend = isInterestMinted ? _totEpochDeposits : lastEpochInterest + _totEpochDeposits;
    _strategy.sendInterestAndDeposits(_toSend);
```

**File:** contracts/IdleCDOEpochVariant.sol (L461-466)
```text
      uint256 _totalFees = _fees + (_mintInterest ? 0 : _pendingWithdrawFees);
      // save net gain (this does not include interest gained for pending withdrawals)
      uint256 netInterest = _grossInterest > _totalFees ? _grossInterest - _totalFees : 0;
      lastEpochInterest = netInterest;
      // mint strategyTokens equal to interest and send underlying to strategy to avoid double counting for NAV
      _strategy.deposit(_mintInterest ? 0 : netInterest);
```
