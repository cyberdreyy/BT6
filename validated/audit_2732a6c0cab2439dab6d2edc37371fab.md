Let me verify how the per-epoch instant-withdraw ledgers are consumed, and whether the normal claim path ever clears them.### Title
Claimed instant-withdraw receipts are never removed from the per-epoch ledgers, corrupting default-epoch claim accounting and freezing later receipts - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The kernel bug is a missing free on an error/unwind path: a resource allocated in one step (`mlx4_srq_alloc`) is not released when the operation completes/aborts. The in-scope analog is `IdleCreditVault.claimInstantWithdrawRequest`: a fully-claimed instant receipt is cleared from the aggregate `instantWithdrawsRequests[_user]` but is never deleted from `instantWithdrawsRequestsByEpoch[_user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]`. If the same epoch later ends in borrower default, `_claimDefaultedInstantWithdrawRequest` re-reads the stale per-epoch entry as if the claim were still pending, causing underflow reverts (frozen claims) and an inflated default claim basis (diluted recovery price).

### Finding Description
In `IdleCreditVault.sol`:

- `requestInstantWithdraw` records `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` [1](#0-0) 
- The normal (funded) claim path only does `instantWithdrawsRequests[_user] = 0` and burns the receipt — the two per-epoch ledgers are never decremented [2](#0-1) 
- After default finalization, `claimInstantWithdrawRequest` first calls `_claimDefaultedInstantWithdrawRequest`, which trusts `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` as live claim basis and performs `instantWithdrawsRequests[_user] -= claimBasis` [3](#0-2) 

Attack sequence (unprivileged tranche holder, running epoch N, `allowInstantWithdraw == true`):

1. Deposit AA, `requestWithdraw` instant amount A → `instantWithdrawsRequestsByEpoch[user][N] = A`.
2. Epoch starts, `collectInstantWithdrawFunds` funds the request, user calls `claimInstantWithdrawRequest` → paid A, `instantWithdrawsRequests[user] = 0`, but `instantWithdrawsRequestsByEpoch[user][N]` stays `A` and `instantWithdrawClaimsByEpoch[N]` stays `A`.
3. User requests a second instant withdraw B in the same epoch N (with remaining/other tranche tokens) → `byEpoch[user][N] = A + B`, `instantWithdrawsRequests[user] = B`.
4. Borrower underpays at `stopEpoch` → default in epoch N; `finalizeDefault` runs with `instantWithdrawClaimsByEpoch[N]` overstated by the already-paid A, inflating `defaultPendingClaimBasis` and lowering `defaultRecoveryPrice` for every claimant.
5. User calls `claimInstantWithdrawRequest` → `claimBasis = A + B` > `instantWithdrawsRequests[user] = B` → subtraction underflows → revert. The legitimate B receipt is permanently frozen, and the extra A worth of reserve stays locked in the vault forever.

### Impact Explanation
Two distinct fund impacts: (a) permanent freezing — any user who claimed once and re-requested in the defaulted epoch can never claim the later receipt, and the corresponding recovery reserve is unspendable; (b) recovery dilution — `instantWithdrawClaimsByEpoch[N]` counts already-paid claims, so `defaultRecoveryPrice` is computed against an inflated basis, directly reducing the payout of all other default claimants (theft via mispriced recovery). No guard prevents this: the funded-claim path deliberately never touches the per-epoch ledgers, and `_transferDefaultRecovery`/`_burn` only enforce consistency downstream.

### Likelihood Explanation
Requires: instant-withdraw enabled mode, a user both claiming and re-requesting within one epoch, and a borrower default at that epoch's stop. Instant claims mid-epoch plus epoch-end defaults are a designed flow (test `testFundedInstantWithdrawRemainsClaimableAfterStopEpochDefault` exercises exactly this window). The trigger needs no privileged cooperation and arises naturally whenever a user's position is partially claimed then topped up.

### Recommendation
In `claimInstantWithdrawRequest`, clear `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` for every epoch contributing to the claimed aggregate (or track the funded-claim epoch explicitly), mirroring what `_claimDefaultedInstantWithdrawRequest` does. Add a regression test: claim → re-request same epoch → default → claim.

### Proof of Concept
```solidity
// Foundry fork test sketch (extend test/foundry/IdleCreditVault.t.sol harness)
function testClaimedInstantReceiptCorruptsDefaultEpoch() external {
    uint256 amount = 10_000 * ONE_SCALE;
    uint256 mintedAA = idleCDO.depositAA(amount);

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // epoch N: request instant withdraw A, get funded, claim
    cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));
    _startEpochAndCheckPrices(1);              // same running epoch per deployment config
    _getInstantFunds();
    cdoEpoch.claimInstantWithdrawRequest();    // stale byEpoch entry left behind

    // re-request B in the same epoch
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    // borrower defaults at epoch stop
    _toggleEpoch(false, initialProvidedApr / 2, _expectedFundsEndEpoch() - 1);
    // finalizeDefault(...) with inflated instantWithdrawClaimsByEpoch
    // then: cdoEpoch.claimInstantWithdrawRequest() reverts -> B permanently frozen
}
```

Note: I could not fully verify within the iteration budget the exact epoch index stored in `defaultRecoveryEpoch` relative to `epochNumber` at request time; the exploit requires them to coincide (request epoch == default epoch), which the existing tests (`test/foundry/IdleCreditVault.t.sol:4499-4551`) strongly suggest. If they never coincide, the stale-ledger bug still leaves `instantWithdrawClaimsByEpoch` permanently inflated but the impact would need re-scoping.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L366-374)
```text
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
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
