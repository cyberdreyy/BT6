### Title
Stale `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` entries after a successful instant claim inflate the default-epoch claim basis and corrupt `defaultRecoveryPrice` — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
A buffer-overflow analog: the per-epoch instant-withdraw ledgers (`instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`) are incremented on `requestInstantWithdraw` but are never decremented when the receipt is funded and claimed through the normal `claimInstantWithdrawRequest` path. If the same epoch later defaults, the stale basis is counted again in `defaultPendingClaimBasis`/`instantWithdrawClaimsByEpoch[defaultEpoch]`, diluting `defaultRecoveryPrice` and permanently stranding recovery funds.

### Finding Description
In `requestInstantWithdraw` the vault records per-epoch receipt basis:

- `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount`
- `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` [1](#0-0) 

In the normal claim path only the aggregate `instantWithdrawsRequests[_user]` is zeroed and receipt tokens burned — neither per-epoch mapping nor the per-epoch aggregate is cleared: [2](#0-1) 

After a default is finalized for `defaultRecoveryEpoch`, the haircut claim path uses the still-populated per-epoch values and decrements the aggregate by the stale basis: [3](#0-2) 

Two corrupting effects follow:

1. `instantWithdrawClaimsByEpoch[defaultRecoveryEpoch]` still contains receipts already paid in full, so the total pending-claim basis used to compute `defaultRecoveryPrice` is overstated. Every claimant's recovery ratio is diluted below what the actually-recovered amount justifies, and the excess `defaultRecoveryReserve` is locked permanently with no withdrawal path.
2. A user who already claimed has `instantWithdrawsRequests[_user] == 0` but `instantWithdrawsRequestsByEpoch[_user][defaultEpoch] != 0`, so `instantWithdrawsRequests[_user] -= claimBasis` underflows/reverts — or, if the user has a newer funded receipt, the subtraction silently consumes part of the new receipt's aggregate while `_transferDefaultRecovery` pays out `claimBasis * defaultRecoveryPrice` for a receipt that was already redeemed, directly draining reserve meant for others.

### Impact Explanation
Like a heap overflow corrupting adjacent accounting, the un-cleared per-epoch ledger writes past the logical end of a claim's lifecycle. An attacker who front-runs a same-epoch default (request instant withdraw → get funded → claim at par → borrower fails at `stopEpoch`) causes `defaultRecoveryPrice` to be computed against an inflated basis. Result: honest defaulted-epoch claimants are underpaid by the attacker's already-paid amount, and the corresponding `defaultRecoveryReserve` is stranded forever. Loss = attacker claim basis × recovery shortfall spread across all claimants; reserve dust equals the over-reserved amount.

### Likelihood Explanation
Requires an epoch that both funded instant withdrawals (success path of `getInstantWithdrawFunds` or startEpoch surplus) and subsequently defaults at `stopEpoch`/`getInstantWithdrawFunds`. The attacker only needs to have claimed a funded instant receipt in the epoch that defaults — no privileged role needed. Triggering depends on a borrower payment failure, which is an external precondition, but the accounting corruption is deterministic once it occurs.

### Recommendation
In `claimInstantWithdrawRequest`, clear `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` for each settled epoch (or track funded-vs-unfunded basis separately). Ensure `defaultPendingClaimBasis`/`instantWithdrawClaimsByEpoch[defaultEpoch]` reflect only *unclaimed* receipts when `defaultRecoveryPrice` and `defaultRecoveryReserve` are computed in `finalizeDefaultRecovery`.

### Proof of Concept
Foundry fork sketch (POC outline — exact harness mirrors `test/foundry/IdleCreditVault.t.sol` helpers `_startEpochAndCheckPrices`, `_depositWithUser`, `_getInstantFunds`, `_checkDefault`):

```solidity
// 1. Attacker and victim deposit AA; APR drops so instant withdraws enable.
// 2. Epoch E starts; attacker requestWithdraw(...) -> instant request.
// 3. getInstantWithdrawFunds() succeeds; attacker claimInstantWithdrawRequest()
//    -> paid at par. instantWithdrawsRequestsByEpoch[attacker][E] stays set.
// 4. Same epoch E: borrower fails at stopEpoch -> _handleBorrowerDefault.
//    defaultRecoveryEpoch == E.
// 5. finalizeDefault(recovered) computes basis including stale
//    instantWithdrawClaimsByEpoch[E] -> defaultRecoveryPrice is diluted.
// 6. Victim's claimDefaultedInstantWithdrawRequest pays
//    claimBasis * dilutedPrice / 1e18 < entitlement.
// 7. defaultRecoveryReserve retains stranded balance equal to the
//    over-reserved amount; attacker instant claim reverts on underflow or
//    double-spends reserve if they hold a newer receipt.
// Assertions: defaultRecoveryPrice < recovered*1e18/unfundedBasis;
//   leftover reserve > 0 after all claims; victim payout shortfall.
```

Note: I could not fully read `defaultPendingClaimBasis`/`finalizeDefaultRecovery` in the available iterations to confirm whether they consume `instantWithdrawClaimsByEpoch` directly or subtract already-claimed amounts; if the implementation already excludes funded/claimed receipts from the recovery basis, this finding does not apply and should be treated as unconfirmed.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L371-374)
```text
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
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
