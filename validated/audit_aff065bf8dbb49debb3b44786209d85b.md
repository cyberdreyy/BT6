### Title
Unfunded instant-withdraw receipts are paid at par from the funded claim reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` pays the user's entire `instantWithdrawsRequests` aggregate out of the strategy's underlying balance, without distinguishing funded receipts (funded via `collectInstantWithdrawFunds` at `startEpoch`) from fresh, still-unfunded receipts tracked in `pendingInstantWithdraws`. A receipt requested during the buffer is therefore "resolved under the wrong context" (the analog of CVE-2017-7791's alert rendering over a new origin after navigation): the claim acts as if the receipt were already funded and drains cash reserved for other users' funded claims.

### Finding Description
- `IdleCDOEpochVariant.requestWithdraw` routes to `IdleCreditVault.requestInstantWithdraw` when the APR dropped (`lastEpochApr > currentApr + instantWithdrawAprDelta`), i.e. during the buffer after `stopEpoch` [1](#0-0) . The vault mints a receipt and increments `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws`, but no underlying is moved yet [2](#0-1) .
- Funding happens later: `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` and pulls cash from the CDO, called when the epoch starts [3](#0-2) .
- `claimInstantWithdrawRequest` has no epoch/funding gate: it burns and pays the full `instantWithdrawsRequests[_user]` aggregate via `_transferFundedClaim` whenever `defaultRecoveryFinalized` doesn't short-circuit [4](#0-3) . The CDO-side wrapper only checks the static `allowInstantWithdraw` flag — not `isEpochRunning` nor that `pendingInstantWithdraws` for the user is zero [5](#0-4) .
- The docstring even assumes claims happen "when epoch is running as funds will get transferred from borrower when epoch starts," but that invariant is not enforced. During the buffer, a request and a claim can be executed back-to-back, and the unfunded request is paid from whatever underlying the strategy already holds — the reserve backing other users' still-unclaimed funded receipts.
- Unlike normal receipts (`_claimFundedWithdrawRequest` enforces `epochNumber > lastWithdrawRequest[_user]` [6](#0-5) ), the instant path has no equivalent "wait until funded" check outside the default-recovery branch.

### Impact Explanation
Direct theft plus temporary freezing. An attacker (any KYC-passing tranche holder) requests an instant withdrawal of amount A during the buffer and immediately calls `claimInstantWithdrawRequest`. The strategy pays A even though A is still counted in `pendingInstantWithdraws`, spending underlying reserved for other users' funded claims. When the honest users later claim, `safeTransfer` underflows and reverts until `startEpoch` refills the strategy — and after `startEpoch`, the borrower's funding of `pendingInstantWithdraws` sits as stranded excess in the strategy rather than reaching the victims whose reserve was consumed, so the attacker nets A of other users' claim value. Loss equals the attacker's unfunded instant-withdraw amount, bounded by the strategy's liquid reserve (all unclaimed funded receipts).

### Likelihood Explanation
Requires instant withdraws enabled (`allowInstantWithdraw`) and an APR drop at `stopEpoch` creating the buffer-window trigger, plus existing funded-but-unclaimed instant receipts or other strategy liquidity to drain. No privileged cooperation is needed; the attack is a two-transaction sequence in the buffer. Uncertainty: if `allowInstantWithdraw` or `claimInstantWithdrawRequest` is implicitly phase-gated elsewhere (e.g. an off-path revert when `epochEndDate` has passed), the window may be limited to cases where the buffer flag state still permits the claim — I could not fully verify whether the CDO or a caller-side check blocks claims while the epoch is stopped, which would reduce likelihood but not eliminate the missing funded/unfunded distinction.

### Recommendation
In `IdleCreditVault.claimInstantWithdrawRequest`, pay only the funded portion: e.g. cap the claim at `instantWithdrawsRequests[_user]` minus the user's share of `pendingInstantWithdraws` (or track funded vs pending per epoch via `instantWithdrawsRequestsByEpoch` and a per-epoch funded flag), and/or have the CDO wrapper revert when the epoch is not running (`epochEndDate == 0 || block.timestamp > epochEndDate` before `startEpoch`). Symmetrically, `requestInstantWithdraw` should revert while a previous unfunded receipt exists if aggregate accounting is kept.

### Proof of Concept
```solidity
// test/foundry/InstantWithdrawUnfundedClaim.t.sol — fork setup mirrors IdleCreditVault.t.sol
// Assume: instant withdraws allowed, AA depositors user2 (honest), attacker (user1).
function testUnfundedInstantClaimDrainsReserve() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    uint256 amount = 10_000 * ONE_SCALE;
    _depositWithUser(user2, amount, true);
    _depositWithUser(user1, amount, true);

    // Epoch runs; at stopEpoch manager lowers APR so instant path triggers next buffer.
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, lowerApr, _expectedFundsEndEpoch()); // lastEpochApr > unscaledApr + delta

    // --- Buffer of epoch boundary 1 ---
    // user2 requests instant withdraw -> pendingInstantWithdraws = amount, unfunded
    vm.prank(user2); cdoEpoch.requestWithdraw(0, address(AAtranche));
    // startEpoch funds user2's receipt: borrower/CDO cash moves to strategy
    _startEpochAndCheckPrices(1);
    // user2 does NOT claim yet; strategy holds `amount` reserve, pendingInstantWithdraws == 0
    _stopEpochAndCheckPrices(1, evenLowerApr, _expectedFundsEndEpoch());

    // --- Buffer of epoch boundary 2 ---
    uint256 attackerBalPre = underlying.balanceOf(user1);
    vm.startPrank(user1);
    cdoEpoch.requestWithdraw(0, address(AAtranche));   // mints unfunded receipt; pendingInstantWithdraws = amount
    cdoEpoch.claimInstantWithdrawRequest();            // pays FULL aggregate incl. unfunded amount
    vm.stopPrank();
    assertEq(underlying.balanceOf(user1) - attackerBalPre, amount); // attacker paid from user2's reserve

    // Victim: user2's funded claim now reverts on safeTransfer (reserve stolen)
    vm.prank(user2);
    vm.expectRevert();
    cdoEpoch.claimInstantWithdrawRequest();
}
```

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L360-375)
```text
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
