### Title
Unfunded instant-withdraw receipts are paid from other users' funded withdrawal reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The rsync CVE is a time-of-check/time-of-use gap: a file is created at a predictable path, and in a later second step metadata is applied to whatever object currently sits at that path, so an attacker swaps in a symlink and the post-operation lands on a target outside the intended tree. The analog in idle-tranches is the instant-withdraw receipt flow: borrower funds for pending instant withdrawals are collected into a shared strategy balance at one point in time, but `claimInstantWithdrawRequest` pays any holder of an aggregate receipt balance — including receipts created *after* the funding — from that same balance. A later requester "substitutes" their unfunded receipt into the slot that was funded for earlier requesters, stealing the reserve and leaving the original requesters unpaid or defaulted.

### Finding Description
In `IdleCreditVault`, `requestInstantWithdraw` burns the CDO's strategy tokens, mints an equal receipt to the user, and increments the global `pendingInstantWithdraws` plus the per-user `instantWithdrawsRequests[_user]` [1](#0-0) . Funding happens asynchronously: the manager calls `getInstantWithdrawFunds` on the CDO, which triggers `collectInstantWithdrawFunds`, transferring underlying from the borrower to the strategy and decrementing `pendingInstantWithdraws` [2](#0-1) . The underlying is not earmarked per request — it just sits in the strategy's balance.

`claimInstantWithdrawRequest` then burns `instantWithdrawsRequests[_user]` and calls `_transferFundedClaim(_user, amount)`, which pays out of the strategy's general underlying balance with no check that *this user's* receipt was part of the funded batch — there is no per-epoch/per-request funding marker, only the defaulted-epoch logic which is skipped pre-default [3](#0-2) [4](#0-3) . The CDO-side wrapper only checks `allowInstantWithdraw` [5](#0-4) .

Because `requestInstantWithdraw` can be called by any KYC'd lender via `IdleCDOEpochVariant.requestWithdraw` whenever `lastEpochApr > currentApr + instantWithdrawAprDelta` (a standing condition for the whole epoch once triggered) [6](#0-5) , an attacker can race: (1) victim requests instant withdraw; (2) borrower funds it via `collectInstantWithdrawFunds` so the strategy holds the cash but `instantWithdrawsRequests[victim]` is still unsettled; (3) attacker creates a new instant receipt and immediately calls `claimInstantWithdrawRequest`, draining the funded reserve before the victim claims. The victim's claim then reverts on insufficient strategy balance (or is paid only if the borrower funds *again*). If the epoch ends and the borrower defaults/short-pays in between — the exact scenario `stopEpochWithDuration`/`finalizeDefault` exist for — the loss is socialized onto the victim's still-pending receipt while the attacker exited at par with funds earmarked for the victim.

The analogous guard for queued withdrawals already exists — per-epoch accounting (`withdrawsRequestsByEpoch`, `lossRecoveryPriceByEpoch`) plus a revert when a stale loss-adjusted receipt exists [7](#0-6)  — but instant receipts have no funded-vs-unfunded split until `defaultInstantWithdrawsFinalized`, i.e., only *after* a default is finalized, which is too late.

### Impact Explanation
Direct theft of unclaimed yield/principal: the attacker is paid at par from underlying that the borrower transferred specifically to satisfy earlier instant-withdraw receipts. The earlier requester suffers temporary freezing at minimum, and permanent loss if a default or `stopEpochWithDuration` loss intervenes before re-funding, because their claim then competes for `defaultRecoveryPrice`/loss-adjusted payouts while the attacker already took the funded reserve. Loss magnitude is bounded by the attacker's tranche position but can be up to the full funded instant-withdraw pool (the attacker only needs a receipt ≥ the funded amount; with a KYC'd wallet and enough deposited tranche balance they can request up to their full position).

### Likelihood Explanation
Requires the instant-withdraw branch to be live (`lastEpochApr > unscaledApr + instantWithdrawAprDelta`), which is a normal recurring state when the manager lowers APR, and an epoch in the running phase between borrower funding and victim claim — a real, externally visible window since `collectInstantWithdrawFunds`/borrower transfers are on-chain and the attacker can observe them in the mempool or on-chain before submitting their own `requestWithdraw` + `claimInstantWithdrawRequest` in the same transaction. Attacker is an ordinary KYC'd lender; no privileged role needed. The race is cheap: deposit, request, claim.

### Recommendation
Track funded instant-withdraw basis separately from newly created requests — e.g., a per-epoch funded watermark (similar to `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch`, but recording which receipts `collectInstantWithdrawFunds` actually covered) or a `fundedInstantWithdraws` counter consumed FIFO per user — and have `claimInstantWithdrawRequest` pay only the funded portion, reverting or queuing the remainder. Alternatively, route borrower-funded instant amounts into a segregated reserve mapping keyed by request epoch so later receipts cannot claim them.

### Proof of Concept
Foundry fork/harness sketch (pattern follows `testFundedInstantWithdrawRemainsClaimableAfterStopEpochDefault` in `test/foundry/IdleCreditVault.t.sol` around line 2699):

```solidity
function testInstantWithdrawRaceDrainsFundedReserve() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 0);
    address victim = makeAddr("victim");
    address attacker = makeAddr("attacker");
    uint256 depositAmt = 10_000 * ONE_SCALE;
    _depositWithUser(victim, depositAmt, true);
    _depositWithUser(attacker, depositAmt, true);

    // Epoch 0 ends with lower APR so instant-withdraw path activates
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 4, _expectedFundsEndEpoch());

    // Victim requests instant withdraw during epoch 1 setup window
    vm.prank(victim);
    uint256 victimReq = cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(1);
    // Borrower funds victim's instant receipt
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds(); // collectInstantWithdrawFunds: strategy now holds victimReq
    assertEq(creditVault.pendingInstantWithdraws(), 0);

    // Attacker creates an unfunded instant receipt in the same running epoch
    vm.prank(attacker);
    uint256 attackerReq = cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertEq(creditVault.pendingInstantWithdraws(), attackerReq); // unfunded

    // Attacker claims immediately — paid out of the reserve funded for the victim
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(underlying.balanceOf(attacker) - balPre, attackerReq); // attacker paid at par

    // Victim's claim now reverts / underpays despite their receipt being the funded one
    vm.prank(victim);
    vm.expectRevert(); // strategy balance drained below victimReq
    cdoEpoch.claimInstantWithdrawRequest();
}
```

The fix test: after the attacker's request, `claimInstantWithdrawRequest` should revert or pay zero until `pendingInstantWithdraws` for the attacker's epoch is funded, while the victim still claims `victimReq` in full.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L261-271)
```text
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-900)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
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

**File:** contracts/IdleCDOEpochVariant.sol (L975-979)
```text
  function claimInstantWithdrawRequest() external {
    // Check that instant withdraws are available
    _checkNotAllowed(!allowInstantWithdraw);
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
  }
```
