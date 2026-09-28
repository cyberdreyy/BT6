### Title
Default recovery only haircuts instant-withdraw receipts from `defaultRecoveryEpoch`, letting earlier-epoch unfunded receipts bypass the haircut or permanently freeze claims - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault` tracks instant-withdraw receipts per epoch (`instantWithdrawsRequestsByEpoch[user][epoch]`), but both the default-claim-basis calculation and the post-default claim path key the lookup only on the *current/default* epoch. A receipt recorded in an epoch earlier than `defaultRecoveryEpoch` is invisible to the recovery logic — the equivalent of a cache lookup that ignores the credential. Depending on free balance, such a receipt either gets paid at par out of funds that should back haircut claims (theft from other claimants) or permanently reverts via the reserve guard, freezing the user's claim and any same-call default-epoch recovery claim.

### Finding Description
The external bug (CVE-2020-2301) is a cache keyed on user identity that ignores the request-specific credential. The analog here is epoch-keyed accounting that ignores the receipt's actual epoch:

- `defaultPendingClaimBasis()` adds only `instantWithdrawClaimsByEpoch[epochNumber]` — claims of the *current* epoch — to the haircut basis, while `pendingInstantWithdraws` is an aggregate that can carry unfunded remainders from earlier epochs (`collectInstantWithdrawFunds` decrements it only by what was actually funded, and `requestInstantWithdraw` / stopEpoch funding can leave a remainder). [1](#0-0) [2](#0-1) 
- `_defaultPrefundedInstantReserve()` only counts `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` when positive; an earlier-epoch unfunded remainder inflates `pendingInstantWithdraws` without contributing claim basis or prefunded reserve. [3](#0-2) 
- `finalizeDefaultRecovery` then sets `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0`, so the stale remainder *enables* the default-instant path but the per-user lookup `_claimDefaultedInstantWithdrawRequest` reads only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` — zero for an old-epoch receipt. [4](#0-3) [5](#0-4) 
- The old receipt therefore falls through to the par claim `instantWithdrawsRequests[_user]` in `claimInstantWithdrawRequest`. [6](#0-5) 

Two outcomes, both broken:

1. **Haircut bypass (theft).** If the strategy holds non-reserve underlying ≥ the stale receipt (e.g., late borrower repayments, residual skim, or over-par recovery), `_transferFundedClaim` pays the old-epoch receipt at 100% while default-epoch claimants receive only `defaultRecoveryPrice`. Since `totalBasis` excluded this receipt, paying it at par is funded by value that was priced for the haircut claimants — direct dilution/theft of the recovery reserve's backing.
2. **Permanent freeze.** If `balance - defaultRecoveryReserve < amount`, the guard `if (balance < reserve || balance - reserve < _amount) revert NotAllowed()` reverts. Because the revert happens after `_claimDefaultedInstantWithdrawRequest` ran in the same transaction, even a *valid* default-epoch instant receipt belonging to the same user can never be claimed — one receipt's payout poisons all of that user's claims. [7](#0-6) 

### Impact Explanation
Unprivileged tranche holder impact: either (a) receipt paid at par post-default, extracting underlying at the expense of haircut claimants' recovery reserve backing, or (b) permanent freezing of the user's instant-withdraw claims — including valid recovery claims — with no admin-free path to clear them (receipts are address-bound; `requestWithdraw` post-default also reverts while `instantWithdrawsRequests[user] != 0`, so the user is locked out of the post-default flow entirely). [8](#0-7) 

### Likelihood Explanation
Requires: (1) an instant-withdraw request that remains partially unfunded at `stopEpoch` (partial `collectInstantWithdrawFunds`), (2) a subsequent epoch, and (3) a borrower default in a later epoch — all reachable through normal manager/borrower sequencing with the attacker acting only as a KYC'd lender making an instant-withdraw request. The stale remainder persists indefinitely because `pendingInstantWithdraws` is never epoch-scoped.

### Recommendation
Track the epoch of each pending instant claim (e.g., store the unfunded epoch alongside `pendingInstantWithdraws`, or iterate `instantWithdrawsRequestsByEpoch` for all epochs ≤ `defaultRecoveryEpoch`) so `defaultPendingClaimBasis` and `_claimDefaultedInstantWithdrawRequest` include *every* unfunded instant receipt, not just `epochNumber`'s. Alternatively, carry a per-user set of pending epochs and clear/haircut each at finalization.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (IdleCreditVault.t.sol harness)
// 1. Epoch N running: user calls cdoEpoch.requestInstantWithdraw(X)
//    -> instantWithdrawsRequestsByEpoch[user][N] = X, pendingInstantWithdraws = X
// 2. stopEpoch: borrower/cdo funds only part -> collectInstantWithdrawFunds(X - r)
//    -> pendingInstantWithdraws = r (stale, epoch N)
// 3. startEpoch (epoch N+1), borrower defaults mid-epoch
// 4. finalizeDefaultRecovery:
//    defaultPendingClaimBasis = pendingWithdraws + instantWithdrawClaimsByEpoch[N+1]
//    -> user's r (epoch N) NOT in basis; no reserve priced for it
//    defaultInstantWithdrawsFinalized = true (pendingInstantWithdraws != 0)
// 5. user calls cdoEpoch.claimInstantWithdrawRequest()
//    _claimDefaultedInstantWithdrawRequest reads [user][defaultRecoveryEpoch=N+1] = 0
//    -> falls through to par claim of r:
//       balance - defaultRecoveryReserve < r  => revert NotAllowed()
//    => user's receipt permanently unclaimable; if non-reserve balance >= r,
//       paid at par => haircut bypass stealing recovery backing.
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L247-251)
```text
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L690-696)
```text
    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-723)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
  }
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
