### Title
Post-default withdraw requests pay 1:1 from the fixed default recovery reserve, draining funds owed to unclaimed defaulted-epoch claimants - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Analogous to CVE-2021-41054 — where a buffer sized for one component is overrun by the *combination* of data, OACK and option fields — `IdleCreditVault` sizes `defaultRecoveryReserve` at finalization to cover only the defaulted-epoch claim basis (active + pending receipts) at `defaultRecoveryPrice`. After finalization, `requestWithdraw` mints new receipts (`postDefaultRequests`) whose claims are paid **1:1 out of that same fixed reserve** via `_transferDefaultRecovery`, a component the reserve was never sized to fund. Each post-default claim therefore consumes recovery funds belonging to not-yet-claimed defaulted-epoch claimants, and late claimants are left insolvent.

### Finding Description
At default finalization, `finalizeDefaultRecovery` computes:

```solidity
uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;
defaultRecoveryReserve = reserveAmount;
defaultRecoveryPrice = recoveryPrice;
``` [1](#0-0) 

The reserve is exactly `totalBasis * recoveryPrice` — it can only pay every defaulted-epoch claimant (active NAV via CDO burn/mint at line 699–705, plus `pendingWithdraws` and defaulted instant claims) if each claim is haircut by `defaultRecoveryPrice`.

However, in the post-finalization branch of `requestWithdraw`:

```solidity
_burn(msg.sender, _amount);
_mint(_user, _amount);
postDefaultRequests[_user] = _amount;
``` [2](#0-1) 

the CDO's strategy tokens are burned but **no underlying is added to `defaultRecoveryReserve`**. Then `_claimPostDefaultWithdrawRequest` pays the full receipt from the reserve:

```solidity
amount = postDefaultRequests[_user];
postDefaultRequests[_user] = 0;
_burn(_user, amount);
_transferDefaultRecovery(_user, amount);   // pays 1:1 from reserve
``` [3](#0-2) 

```solidity
defaultRecoveryReserve -= _amount;
underlyingToken.safeTransfer(_user, _amount);
``` [4](#0-3) 

The comment claims post-default receipts "are already priced after the haircut" — true at the CDO layer (the user's tranche tokens were burned at the post-haircut `virtualPrice`), but the *payment source* is wrong: the reserve was funded to cover `totalBasis * recoveryPrice`, and no new collateral backs the post-default receipt. The burn of CDO strategy tokens is purely deflationary; it does not transfer underlying into the reserve.

Note the asymmetric guard: `_transferFundedClaim` explicitly protects the reserve (`balance - reserve < _amount` reverts), but `_transferDefaultRecovery` freely hands reserve to any post-default requester — confirming the invariant "reserve is exclusively for defaulted-epoch claims" is intended but violated.

**Attack sequence** (defaulted → finalized phase):
1. Borrower defaults; `finalizeDefaultRecovery` sets `defaultRecoveryPrice = 0.5e18`, `defaultRecoveryReserve = R`, covering all defaulted-epoch claims at 50%.
2. Attacker (any tranche holder with an open position) calls `requestWithdraw` on the CDO → post-default branch mints them a receipt of `_amount` and records `postDefaultRequests`.
3. Attacker calls `claimWithdrawRequest` → `_claimPostDefaultWithdrawRequest` transfers the full `_amount` of underlying from `defaultRecoveryReserve`.
4. Repeating across holders drains `R`. When honest defaulted-epoch claimants call `claimWithdrawRequest`/`claimInstantWithdrawRequest`, either `defaultRecoveryReserve` underflows (revert → permanent freeze of their recovery) or the strategy's underlying balance is exhausted → they receive less than `claimBasis * recoveryPrice` or nothing.

### Impact Explanation
Direct theft and permanent freezing of unclaimed default recovery. Quantified: every post-default claim of `_amount` removes `_amount` from a reserve budgeted at `defaultRecoveryPrice < RECOVERY_FULL`. If defaulted-epoch claim basis is `B` and reserve `R = B * p` (p < 1), then `R / 1` worth of post-default claims (vs the `R / p` they would need at par funding) leaves `R - claimed` for claims still owed `B - claimedBasis`, producing a shortfall exactly equal to all post-default payouts. Early post-default claimants get 100% while defaulted-epoch claimants — who were promised `recoveryPrice` — get less or revert on `defaultRecoveryReserve -= _amount` underflow, permanently freezing their recovery.

### Likelihood Explanation
Requires a borrower default followed by recovery finalization at `recoveryPrice < 1` (any partial recovery). Post-default withdraw requests are a supported flow (the branch exists precisely for it), so any KYC'd tranche holder can trigger it permissionlessly — no privileged role needed. The CDO-side burn does reduce `balanceOf(idleCDO)`, but the strategy's actual underlying (`defaultRecoveryReserve`) is the binding constraint, and it is never topped up. The only mitigating factor is that post-default requesters are limited to holders who still have tranche balances after finalization, which is typically most of the pool.

### Recommendation
Post-default claims must not spend `defaultRecoveryReserve`. Either:
- Fund them separately: on the post-default `requestWithdraw` path, transfer the corresponding underlying into a new reserve bucket (or require borrower funding), and have `_claimPostDefaultWithdrawRequest` use `_transferFundedClaim`-style accounting against a dedicated balance rather than `_transferDefaultRecovery`; or
- Track the post-default payout obligation by *increasing* the reserve at finalization when requests are anticipated, or scale `postDefaultRequests` payouts by `defaultRecoveryPrice` so they consume reserve proportionally to the basis they displaced.

### Proof of Concept
Foundry fork PoC outline (extend `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// Setup: deposit AA/BB, start epoch, borrower defaults (fails getFundsFromBorrower).
// Fund partial recovery: deal(underlying, recoverySource, recovered) with recovered < totalBasis.
vm.prank(manager);
cdoEpoch.finalizeDefaultRecovery(recovered, recoverySource); // sets price p < 1e18

// Honest victim V had a pending withdraw receipt in the defaulted epoch (claimBasis_V).
// Attacker A holds active tranches.
uint256 trancheBalA = AAtranche.balanceOf(attacker);
uint256 reserveBefore = strategy.defaultRecoveryReserve();

// Post-finalization request + claim pays A at 1:1 from the reserve.
vm.prank(attacker);
uint256 req = cdoEpoch.requestWithdraw(trancheBalA, address(AAtranche));
vm.prank(attacker);
cdoEpoch.claimWithdrawRequest();
assertEq(underlying.balanceOf(attacker), req);                  // paid 1:1
assertEq(strategy.defaultRecoveryReserve(), reserveBefore - req); // reserve drained, no new funding

// Victim's defaulted-epoch claim now underflows / pays short.
vm.prank(victim);
vm.expectRevert(); // defaultRecoveryReserve -= claimBasis*p underflow once reserve exhausted
cdoEpoch.claimWithdrawRequest();
```

Uncertainty note: I verified the reserve-sizing, post-default mint, and reserve-spend paths, but did not fully trace the CDO-side `finalizeDefault` to confirm `allowAAWithdrawRequest`/`allowBBWithdrawRequest` are re-enabled post-finalization; the existence of the post-default `requestWithdraw` branch strongly implies requests are meant to be permitted there, but the PoC should confirm the flag state and adjust sequencing if needed.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L252-257)
```text
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-692)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-917)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```
