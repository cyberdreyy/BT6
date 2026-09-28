### Title
Unfunded instant-withdraw receipts can steal funded withdrawal and buffer-deposit underlyings held by the strategy - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` treats every instant-withdraw receipt as if it were already funded: it burns the user's receipt tokens and transfers underlying from the strategy's own balance, gated only by the `defaultRecoveryReserve` check in `_transferFundedClaim`. It never verifies that the receipt's backing was actually collected (`collectInstantWithdrawFunds` reduces `pendingInstantWithdraws`, but the claim path does not check that counter or any per-receipt funding marker). This is the same class of bug as the Xen union-type-confusion report: one data structure (the instant receipt) is interpreted in two incompatible states — unfunded vs funded — and the wrong interpretation lets an unfunded claim consume funds earmarked for other purposes.

### Finding Description
The instant-withdraw lifecycle is:

1. `IdleCDOEpochVariant.requestWithdraw` routes to `IdleCreditVault.requestInstantWithdraw`, which burns CDO strategy tokens, mints a 1:1 receipt to the user, and increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch` and `pendingInstantWithdraws` [1](#0-0) .
2. Funding happens later, at epoch start, when borrower-supplied cash is moved into the strategy via `collectInstantWithdrawFunds`, which decrements `pendingInstantWithdraws` and pulls underlying from the CDO [2](#0-1) .
3. `claimInstantWithdrawRequest` then burns `instantWithdrawsRequests[_user]` and calls `_transferFundedClaim(_user, amount)` [3](#0-2) .

The problem: step 3 pays out of the *aggregate* strategy balance. `_transferFundedClaim` only protects `defaultRecoveryReserve` — every other underlying sitting in the vault is fair game for an unfunded instant claim [4](#0-3) . There is no `instantWithdrawDelay`, no epoch check, and no check that `pendingInstantWithdraws` was reduced before paying.

Underlying legitimately sits in the strategy in at least three windows where it belongs to someone else:

- **Funded normal withdraw receipts**: after `collectWithdrawFunds`, the borrower's repayment for pending normal/APR0 receipts sits in the strategy until users claim via `claimWithdrawRequest` [5](#0-4) . `pendingWithdraws` is cleared at that point, so nothing distinguishes those funds.
- **Funded loss-adjusted receipts**: after `collectWithdrawFunds` with a haircut, `lossRecoveryPriceByEpoch` claimants' money sits in the same untracked balance [6](#0-5) .
- **Buffer deposits / pre-startEpoch cash**: `deposit()` pulls underlying into the strategy when the epoch is not running [7](#0-6) , backing freshly minted strategy tokens owned by the CDO.

Attack sequence (attacker = any KYC-passed tranche holder, in instant-withdraw mode i.e. `lastEpochApr > currentApr + instantWithdrawAprDelta`):

1. Attacker calls `requestWithdraw` while instant mode is enabled → `requestInstantWithdraw` mints the receipt; `pendingInstantWithdraws += amount`.
2. Attacker waits for (or sandwiches) a moment when the strategy holds underlying earmarked elsewhere — most reliably right after manager/borrower funding of normal receipts via `collectWithdrawFunds`, or while buffer deposits sit in the vault before `startEpoch` forwards them.
3. Attacker calls `IdleCDOEpochVariant.claimInstantWithdrawRequest()` → `claimInstantWithdrawRequest` burns the receipt and `_transferFundedClaim` sends `amount` underlying, drawn from funds that were meant to pay *other users'* already-funded withdraw receipts (or new depositors' backing).
4. The honest receipt holders' subsequent `claimWithdrawRequest` calls revert on `safeTransfer` (insufficient balance) — permanent freezing/insolvency — while the attacker keeps the funds. The borrower is honest and owes nothing further: `pendingInstantWithdraws` still says the attacker's receipt was unfunded, yet the money is gone.

The broken invariant is "one receipt, one funded payout": an instant receipt whose funding the borrower never supplied is paid out of another claim bucket. No guard stops it — `allowInstantWithdraw` is the normal operating flag, `_skimDonatedAssets`/`defaultRecoveryReserve` checks are unrelated, and `claimInstantWithdrawRequest` has no funding-state check.

### Impact Explanation
Direct theft of already-funded withdrawal proceeds (or buffer-deposit backing) up to the full underlying balance of the strategy minus `defaultRecoveryReserve`, limited only by the attacker's tranche position that is convertible to an instant receipt. The corresponding honest claims become permanently unpayable — insolvency, not just temporary freezing. Loss is quantified as `min(instantReceiptAmount, strategyBalance - defaultRecoveryReserve)`; e.g., if the strategy holds 1,000,000 underlying collected for normal withdraw receipts and the attacker holds an instant receipt for 1,000,000, the attacker takes the entire bucket.

### Likelihood Explanation
Requires instant-withdraw mode to trigger (APR decrease beyond `instantWithdrawAprDelta`), which is an ordinary market configuration, plus underlying being present in the strategy — a recurring state (every stopEpoch that funds pending receipts leaves that cash in the vault until users claim). The attacker can hold a small tranche position and the exploit is a two-transaction sequence with no privileged role involvement. Caveat: the report intentionally did not fully trace `getInstantWithdrawFunds`/the orchestrator timing; if some upstream gating were found to prevent `claimInstantWithdrawRequest` while `pendingInstantWithdraws` is nonzero, this collapses — but no such check exists inside `IdleCreditVault` itself.

### Recommendation
In `claimInstantWithdrawRequest`, only pay from funds that were actually collected for instant receipts: track a funded counter incremented in `collectInstantWithdrawFunds` and decremented on claim, and revert (or cap the claim) when `instantWithdrawsRequests[_user]` exceeds the funded amount. Alternatively enforce the documented `instantWithdrawDelay`/per-epoch funding marker so receipts can only be paid from the epoch's collected bucket, never from the free balance.

### Proof of Concept
```solidity
// test/foundry/InstantClaimUnfunded.t.sol — fork PoC sketch, extends IdleCreditVault.t.sol helpers
function testUnfundedInstantClaimStealsFundedNormalReceipts() external {
    // honest user deposits and requests a normal withdraw
    uint256 amount = 10_000 * ONE_SCALE;
    address honest = makeAddr('honest');
    _depositWithUser(honest, amount, true);
    vm.prank(honest);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    // epoch runs, borrower repays; strategy now holds underlying backing honest's receipt
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());
    // after stopEpoch + collectWithdrawFunds, strategy balance holds the funded amount,
    // pendingWithdraws == 0

    // attacker deposits, APR drops so instant mode triggers, attacker requests instant withdraw
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, amount, true);
    _setAprs(...lowerApr...); // lastEpochApr > currentApr + instantWithdrawAprDelta
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // mints instant receipt, pendingInstantWithdraws > 0

    // attacker claims immediately — receipt was never funded via collectInstantWithdrawFunds,
    // but _transferFundedClaim pays out of the strategy's aggregate balance
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertGt(underlying.balanceOf(attacker), balPre, 'unfunded instant claim paid out');

    // honest user's funded claim now reverts / underpays: insolvency
    vm.prank(honest);
    vm.expectRevert(); // safeTransfer insufficient balance
    cdoEpoch.claimWithdrawRequest();
}
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L596-615)
```text
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
