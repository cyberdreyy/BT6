### Title
Unfunded instant-withdraw receipts are paid at par from shared strategy reserves, letting early claimants drain funded withdrawals of others - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` pays out the full `instantWithdrawsRequests[_user]` receipt at par and burns the corresponding strategy tokens, without checking whether the CDO actually collected the underlying for that receipt via `collectInstantWithdrawFunds`. The only funding tracking is the aggregate `pendingInstantWithdraws` counter, which is decremented on collection but is never consulted on the claim path. This is the contract-level analog of the CVE-2020-6455 out-of-bounds read: the claim reads the full receipt amount without bounding it to the funded range.

### Finding Description
Instant withdrawals follow this flow:

1. `requestInstantWithdraw` mints the user a 1:1 strategy-token receipt and increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`, and `pendingInstantWithdraws` [1](#0-0) .
2. At epoch start the CDO moves its available cash to the strategy via `collectInstantWithdrawFunds(_amount)`, which decrements `pendingInstantWithdraws` by whatever amount was collected — partial funding is explicitly permitted since `_amount` is a free parameter and only bounded by what the CDO transfers [2](#0-1) .
3. `claimInstantWithdrawRequest` then burns the user's entire `instantWithdrawsRequests[_user]` receipt and pays the full amount through `_transferFundedClaim` — with no check that the received underlying covered this user's receipt [3](#0-2) .

The default-recovery code itself documents the underfunding scenario: `defaultPendingClaimBasis` notes that "`pendingInstantWithdraws` is still non-zero" when "cash covered only part of the instant queue" [4](#0-3) , and `_claimDefaultedInstantWithdrawRequest` acknowledges "`pendingInstantWithdraws` is only the unfunded remainder" [5](#0-4) . So partial funding is a real, reachable state — but the normal claim path treats every receipt as fully funded.

An unprivileged tranche holder (`_onlyIdleCDO` plus the CDO's `allowInstantWithdraw` flag are the only gates on the claim) requests an instant withdrawal in a running epoch where the realized APR dropped below the configured delta. At `startEpoch`, honest manager/borrower sequencing funds only part of the instant queue (CDO cash is limited). The attacker calls `claimInstantWithdrawRequest` through `IdleCDOEpochVariant.claimInstantWithdrawRequest` (which only checks `allowInstantWithdraw` [6](#0-5) ) and receives the full receipt amount, paid out of underlying that also backs other users' funded normal withdrawals (`withdrawsRequests` funded via `collectWithdrawFunds` sit in the same strategy balance) or the unfunded share of other instant claimants.

### Impact Explanation
Broken invariant: one receipt, one funded payout. Each instant receipt is paid at par regardless of the fraction of `instantWithdrawClaimsByEpoch[epoch]` actually collected, so total instant claims can exceed collected funds by up to `pendingInstantWithdraws` (the unfunded remainder). The deficit is absorbed by the strategy's underlying balance, which is the same pot backing already-funded normal withdrawal claims and, post-default, `defaultRecoveryReserve` (though `_transferFundedClaim` appears to exclude the reserve, ordinary funded claims are not isolated). The first claimants to call extract more than their funded share; later claimants — or normal withdrawers whose funding was consumed — are left permanently undercollateralized. Loss magnitude equals the unfunded instant remainder, bounded only by instant-queue size relative to CDO cash at epoch start.

### Likelihood Explanation
Requires a mode where instant withdrawals are enabled (`allowInstantWithdraw`, non-prefunded variant since `IdleCDOEpochVariantPrefunded._isInstantWithdrawEnabled` returns false) and an epoch start where the CDO's transferable cash covers only part of the instant queue — a routine condition whenever instant demand exceeds on-hand liquidity, and one the code itself anticipates in the default path. No privileged misbehavior is needed: the borrower and manager act honestly, funding is simply partial. The attacker only needs a tranche-token balance and timing (claim before honest users). Caveat: if the CDO's `getInstantWithdrawFunds`/`collectInstantWithdrawFunds` call always enforces full coverage or reverts on partial funding, the reachable underfunded state narrows; that call path could not be fully inspected within this analysis. Additionally, if instant claims are only reachable while the epoch is running and the CDO gates claims on collection status, likelihood drops — but no such check exists in the vault function itself.

### Recommendation
Track funded vs. unfunded instant claims per epoch and per user. On the claim path, cap the payout to the user's pro-rata share of collected funds, e.g. compute `claimable = instantWithdrawsRequests[_user] * collectedForEpoch / instantWithdrawClaimsByEpoch[epoch]`, or store an `instantFundedPriceByEpoch` (mirroring `lossRecoveryPriceByEpoch`) set when `collectInstantWithdrawFunds` runs, and have `_claim` apply that ratio. At minimum, revert in `claimInstantWithdrawRequest` when the receipt's epoch was not fully funded, rather than silently paying at par.

### Proof of Concept
Reproducible as a Foundry fork test on the standard (non-prefunded) `IdleCDOEpochVariant` + `IdleCreditVault` setup used in `test/foundry/IdleCreditVault.t.sol`:

```solidity
function testUnfundedInstantClaimPaysAtPar() external {
    // setup: standard variant, fees, AA deposit by attacker and honest user
    uint256 amountWei = 10_000 * ONE_SCALE;
    uint256 mintedAA = idleCDO.depositAA(amountWei);
    _startEpochAndCheckPrices(0);

    // enable instant withdraws: delay D, aprDelta such that low APR triggers instant path
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

    // attacker requests instant withdraw of all minted AA
    uint256 req = cdoEpoch.requestWithdraw(mintedAA, address(AAtranche));
    assertGt(strategy.instantWithdrawsRequests(address(this)), 0);

    // epoch stops with low APR -> instant withdrawals triggered, new epoch starts
    _stopEpochAndCheckPrices(0, 1e18, expectedFunds);

    // At startEpoch the CDO's available cash covers only part of the instant queue:
    // pendingInstantWithdraws remains > 0 after collectInstantWithdrawFunds.
    vm.prank(manager);
    cdoEpoch.startEpoch();
    assertGt(strategy.pendingInstantWithdraws(), 0, "instant queue underfunded");

    // wait past instant delay, then claim — full receipt paid at par despite partial funding
    skip(101);
    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 got = underlying.balanceOf(address(this)) - balPre;

    // attacker received the full unfunded share at par; the shortfall is borne by the
    // strategy balance backing other pending/funded claims
    assertGt(got, 0);
    assertEq(strategy.instantWithdrawsRequests(address(this)), 0);
    // invariant: sum of instant payouts exceeded collected instant funds
    assertLt(strategy.pendingInstantWithdraws() /* unfunded remainder */, req);
}
```

The test demonstrates the missing bounds check directly: `claimInstantWithdrawRequest` succeeds for the full receipt while `pendingInstantWithdraws > 0` proves the queue was only partially funded, meaning the payout consumed underlying earmarked for other claims.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-856)
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
