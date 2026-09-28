### Title
Instant-withdraw claims pay out from pooled strategy liquidity before the request is funded, letting a later requester drain funds earmarked for earlier instant-withdraw receipts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` burns a user's instant-withdraw receipt and transfers underlying directly from the strategy's token balance, without checking that this specific request has actually been funded via `getInstantWithdrawFunds`/`collectInstantWithdrawFunds`. An unprivileged lender can request an instant withdrawal while the strategy still holds liquidity that was collected to satisfy *other* users' pending instant requests, claim it immediately, and leave the earlier receipt holders permanently underfunded.

### Finding Description
The instant-withdraw flow has two phases that are not bound to each other:

1. `requestInstantWithdraw` mints receipt strategy tokens to the user and increases `pendingInstantWithdraws`, but no underlying moves — funding happens later when the manager calls `getInstantWithdrawFunds` on the CDO, which routes borrower cash into the strategy and is consumed by `collectInstantWithdrawFunds` (which decrements `pendingInstantWithdraws`). [1](#0-0) [2](#0-1) 

2. `claimInstantWithdrawRequest` burns the user's receipt balance and calls `_transferFundedClaim`, which pays out of the strategy's aggregate underlying balance. There is no per-epoch funding marker, no "funded vs unfunded" split for instant receipts, and no epoch-wait gate like the one in `_claimFundedWithdrawRequest` (`epochNumber <= lastWithdrawRequest` revert). Any underlying sitting in the strategy — including liquidity already collected for earlier instant receipts — is spendable by whichever receipt holder claims first. [3](#0-2) [4](#0-3) 

The CDO side enables the attack path: `requestWithdraw` takes the instant branch whenever `lastEpochApr > unscaledApr + instantWithdrawAprDelta`, so the instant path stays open for any number of requests until the APR is raised again. [5](#0-4) 

The analogous "size check" failure: the claim validates only that the user *holds* receipt tokens, not that the *strategy's underlying balance is attributable to that receipt*. Funding and claiming are decoupled, so the receipt token is treated as proof of funded entitlement when it is only proof of an unfunded request.

### Impact Explanation
Direct theft of other users' funded withdrawal proceeds. Once victim A's instant request is funded (underlying transferred into the strategy), attacker B requests an instant withdraw and calls `claimInstantWithdrawRequest` in the same transaction or any time before A claims. B receives underlying up to the strategy's full balance — including A's funded amount — while B's own request remains counted in `pendingInstantWithdraws` but is already paid. When A later claims, `_transferFundedClaim` finds the strategy underfunded; A's payout is reduced or reverts. Loss equals the lesser of B's receipt amount and the funded balance earmarked for A. If the borrower never tops up, A's loss is permanent; the same applies to unfunded claims paid out of `defaultRecoveryReserve`-adjacent or recycled liquidity.

### Likelihood Explanation
Likelihood is bounded but realistic for an unprivileged KYC-passed lender:
- Instant mode must be active (`lastEpochApr > unscaledApr + instantWithdrawAprDelta`), which is a normal, honest-manager configuration state.
- The strategy must hold underlying, i.e., at least one earlier instant request must already be funded but unclaimed — a routine window between `getInstantWithdrawFunds` and the user's claim.
- The attacker needs only `requestWithdraw` (KYC-gated `isWalletAllowed`, satisfied by any whitelisted lender) followed by `claimInstantWithdrawRequest`; no privileged role is required, and the claim can be executed in the same block as funding if the funding call is observed in the mempool.

### Recommendation
Track funded vs. unfunded instant-withdraw basis explicitly and gate claims on funded availability:

- Record a per-epoch funded amount for instant receipts (e.g., `instantWithdrawFundedByEpoch`) incremented in `collectInstantWithdrawFunds`, and only allow `claimInstantWithdrawRequest` to pay up to the user's funded share; keep the unfunded remainder claimable only after subsequent funding or through the default-recovery path.
- Alternatively, revert `claimInstantWithdrawRequest` while `pendingInstantWithdraws` covers the user's receipt, so claims are only possible once their request has been funded.
- Add a test asserting that an instant receipt cannot withdraw underlying collected for a different epoch's/requester's instant request.

### Proof of Concept
Foundry fork PoC outline (against `IdleCDOEpochVariant` + `IdleCreditVault`, following the harness style of `test/foundry/IdleCreditVault.t.sol`):

```solidity
// Setup: standard credit vault deployment, feeReceiver set, AA deposits enabled.
// Users A and B are whitelisted (isWalletAllowed true via KeyringIdleWhitelist).

function testInstantClaimStealsPrefundedLiquidity() external {
    uint256 amount = 100_000 * ONE_SCALE;

    // A and B deposit into AA
    _depositWithUser(userA, amount, true);
    _depositWithUser(userB, amount, true);

    // Epoch 0 runs and is stopped; manager lowers APR so instant mode activates
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0); // unscaledApr drops below lastEpochApr - delta

    // A requests instant withdraw -> receipt minted, pendingInstantWithdraws += amtA
    vm.prank(userA);
    uint256 aReceipt = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // Manager funds pending instant withdraws: underlying moves into strategy for A
    // (getInstantWithdrawFunds pulls borrower cash, collectInstantWithdrawFunds decrements pending)
    _fundInstantWithdraws(aReceipt); // helper replicating manager getInstantWithdrawFunds flow
    assertGt(underlying.balanceOf(address(strategy)), 0);

    // B requests instant withdraw while strategy still holds A's funded liquidity
    vm.prank(userB);
    uint256 bReceipt = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // B claims immediately, BEFORE B's request is funded: paid out of A's earmarked funds
    uint256 bBalPre = underlying.balanceOf(userB);
    vm.prank(userB);
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 bStolen = underlying.balanceOf(userB) - bBalPre;
    assertEq(bStolen, bReceipt, "B paid from prefunded pool");

    // A's claim now underfunded: strategy balance < aReceipt -> transfer fails or pays less
    vm.prank(userA);
    vm.expectRevert(); // SafeERC20: transfer amount exceeds balance
    cdoEpoch.claimInstantWithdrawRequest();
}
```

Expected result: B is paid in full out of liquidity collected for A; A's claim reverts on insufficient strategy balance, and because `instantWithdrawsRequests[A]` still equals `aReceipt` while the strategy is drained, A's funded claim is permanently impaired absent further borrower repayment.

Note: I verified `requestInstantWithdraw`, `claimInstantWithdrawRequest`, `collectInstantWithdrawFunds` and the CDO instant branch directly; I did not fully trace `getInstantWithdrawFunds` and the CDO-side `claimInstantWithdrawRequest` wrapper within the iteration budget, so the exact funding entry point name in the PoC should be confirmed against the CDO's instant-funding function before execution.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
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
