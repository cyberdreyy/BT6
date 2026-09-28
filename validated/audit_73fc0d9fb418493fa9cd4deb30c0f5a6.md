### Title
Post-default withdraw requests pay out at par and drain the shared recovery reserve meant for haircutted claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
After `finalizeDefaultRecovery`, the reserve `defaultRecoveryReserve` is sized against `totalBasis = activeBasis + pendingBasis` with `defaultRecoveryPrice = reserve / totalBasis < 1`. The `requestWithdraw` post-default branch in `IdleCreditVault` mints the caller a new `postDefaultRequests` receipt that is paid 1:1 from that same reserve via `_claimPostDefaultWithdrawRequest`, even though the reserve was never increased to cover it. A tranche holder can therefore convert an active claim worth `amount * defaultRecoveryPrice` into a par payout, over-drawing the reserve and leaving later, legitimately haircutted claimants unfunded. This mirrors CVE-2019-5778's "missing case for a special scheme": the post-default request path is a privileged claim class that bypasses the recovery-price haircut every other claimant is subject to.

### Finding Description
In `IdleCreditVault.requestWithdraw` (contracts/strategies/idle/IdleCreditVault.sol:243-258), when `defaultRecoveryFinalized` is true the function takes an early-return branch: [1](#0-0) 

It burns `_amount` strategy tokens from the CDO, mints them to `_user`, and records `postDefaultRequests[_user] = _amount`. The comment claims these are "already priced after the haircut" because `virtualPrice` was lowered at finalization — but the claim path does not apply `defaultRecoveryPrice` at all: [2](#0-1) 

Meanwhile `finalizeDefaultRecovery` computes the reserve to satisfy `totalBasis` only at ratio `recoveryPrice`, with no term for future post-default requests: [3](#0-2) 

The haircut argument only holds if tranche tokens burned in the post-default request were worth exactly `amount / recoveryPrice` of reserve claim elsewhere. But the claim basis for active holders is tracked through the CDO/tranche side (`DefaultDistributor` / saved NAV path), not through `postDefaultRequests` — so paying the new receipt at par spends `amount` of reserve while the finalized accounting only budgeted `amount * defaultRecoveryPrice` for that underlying economic claim. The pre-claim guard (`_hasWithdrawRequest`/`instantWithdrawsRequests`/`postDefaultRequests` nonzero → revert) prevents stacking multiple receipts for one user, but does nothing to bound the *aggregate* par payout across all users against the fixed reserve.

### Impact Explanation
Direct theft / insolvency of the recovery reserve, quantified as `postDefaultAmount * (1 - defaultRecoveryPrice)` per attacker claim. Example: recovery at 50% (`defaultRecoveryPrice = 5e17`), reserve 500k for 1M basis. An attacker holding AA tranche tokens worth 200k of active basis calls `requestWithdraw` post-finalization and immediately `claimWithdrawRequest`, receiving 200k at par instead of the 100k their claim economically justified. The extra 100k comes out of the reserve owed to defaulted-epoch pending-receipt claimants; once the reserve is exhausted, `_transferDefaultRecovery` either under-pays or reverts, permanently freezing other users' remaining recovery. Severity follows the reserve share that can be re-routed to par; with low recovery prices the haircut bypass approaches ~full claim value.

### Likelihood Explanation
Requires a borrower default that reaches `finalizeDefaultRecovery` with `defaultRecoveryPrice < 1` — a stressed but real mode the code explicitly supports, including the "recovery above/below par" comment and dust handling. The attacker is an ordinary tranche-token holder (unprivileged per threat model; defaulted receipt claims don't require KYC since `claimWithdrawRequest` performs no `isWalletAllowed` check at IdleCDOEpochVariant.sol:967-971). No privileged actor misbehavior is needed; owner/manager merely finalize a partial recovery honestly. The only friction is that the attacker must hold tranche tokens at default time, which any lender does.

### Recommendation
Either pay post-default receipts at `defaultRecoveryPrice` instead of par (apply `(amount * defaultRecoveryPrice) / RECOVERY_FULL` in `_claimPostDefaultWithdrawRequest`), or atomically deduct the requester's corresponding active-basis claim from the recovery accounting when the post-default request is created, so the reserve cannot be double-spent. If the intent is to migrate active holders into the pending-claim class, the amount recorded should be the claim's recovery-priced value, and `defaultPendingClaimBasis`/reserve accounting should reflect the conversion.

### Proof of Concept
Foundry fork PoC (block 23032567 as in `IdleCreditVaultWriteOffEscrow.t.sol`):

```solidity
// Setup: deposit AA, run an epoch, borrower defaults (stopEpoch reverts into
// _handleBorrowerDefault). Manager finalizes partial recovery:
//   finalizeDefault(recovered = 50% of totalBasis, recoverySource = manager)
// => defaultRecoveryPrice = 5e17, defaultRecoveryReserve = recovered.

// Attacker: holder of AA tranche tokens with active-basis value `A`.
uint256 balPre = underlying.balanceOf(attacker);
vm.startPrank(attacker);
AAtranche.approve(address(cdoEpoch), type(uint256).max);
cdoEpoch.requestWithdraw(amountTranche, address(AAtranche)); // post-default branch
cdoEpoch.claimWithdrawRequest();                             // -> _claimPostDefaultWithdrawRequest
vm.stopPrank();
uint256 got = underlying.balanceOf(attacker) - balPre;

// Attacker received `got == A` (par) while entitled to A * 5e17 / 1e18.
assertGt(got, A * strategy.defaultRecoveryPrice() / 1e18);

// Honest defaulted-epoch withdrawer now claims:
// revert or under-payment because reserve was drained by par payouts.
vm.prank(victim);
cdoEpoch.claimWithdrawRequest(); // pays less than claimBasis * recoveryPrice or reverts
```

Uncertainty note: the PoC assumes `_transferDefaultRecovery` debits `defaultRecoveryReserve` and reverts on insufficiency (lines 897+ were truncated in the index). If it instead pays from general strategy balance, the impact shifts to active-holder NAV but the haircut-bypass (par payout vs `defaultRecoveryPrice`) still stands. The invariant break — one claim class settling at par from a reserve sized for haircutted claims — is verifiable directly in the cited code regardless.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-688)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L760-767)
```text
  function _claimPostDefaultWithdrawRequest(address _user) internal returns (uint256 amount) {
    amount = postDefaultRequests[_user];
    if (amount == 0) return amount;
    postDefaultRequests[_user] = 0;
    // Post-default receipts are paid 1:1 because the haircut was applied when the request was made.
    _burn(_user, amount);
    _transferDefaultRecovery(_user, amount);
  }
```
