### Title
Funded instant-withdraw claims leave stale per-epoch buffers (`instantWithdrawsRequestsByEpoch` / `instantWithdrawClaimsByEpoch`), inflating default-recovery basis and permanently freezing later instant receipts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug zeroes a response buffer so stale bytes cannot leak into a new message. The analog here is a stale accounting buffer: `claimInstantWithdrawRequest` clears the aggregate `instantWithdrawsRequests[_user]` but never zeroes the per-epoch fields written at request time (`instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`). Those stale values are later re-read as if they were live: `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` into the recovery denominator, and `_claimDefaultedInstantWithdrawRequest` uses `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` as claim basis. An unprivileged user who requests, gets funded, claims, and requests again inside the same epoch leaves phantom basis that dilutes every default claimant and makes their own new funded receipt unclaimable.

### Finding Description
`requestInstantWithdraw` records three counters: `instantWithdrawsRequests[_user] += _amount`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount`, and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` (lines 366–374). The normal funded-claim path only resets the aggregate:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
``` [1](#0-0) 

Neither `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` nor `instantWithdrawClaimsByEpoch[currentEpoch]` is cleared (the "unzeroed rsp buffer"). Two consumers later trust those buffers:

- `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` to the haircut denominator whenever `pendingInstantWithdraws != 0` (lines 644–648). Already-claimed receipts are counted again, so `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is depressed by the stale amount; the matching reserve units stay locked as dust in `defaultRecoveryReserve`.
- `_claimDefaultedInstantWithdrawRequest` reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` as the claim basis and then does `instantWithdrawsRequests[_user] -= claimBasis` (lines 842–855). If the user already claimed in that epoch and opened a smaller new instant request in the same epoch, `claimBasis` (old+new) exceeds `instantWithdrawsRequests[_user]` (new) and the subtraction underflows — the call reverts forever, so the user's legitimately funded new receipt can never be claimed.

No guard prevents this: `requestInstantWithdraw` has no same-epoch re-request check, the funded-claim path has no per-epoch cleanup, and `defaultInstantWithdrawsFinalized` is set precisely when `pendingInstantWithdraws != 0` — i.e., exactly when the stale counters matter.

### Impact Explanation
Two quantified harms from the same unzeroed buffer:

1. **Recovery dilution**: if X underlying of instant receipts were claimed-and-funded in epoch N and a further Y remains pending when the pool defaults in epoch N, `totalBasis` includes X+Y instead of Y. With, e.g., X = 100k USDC stale claims against a 1M USDC real basis, every recovery claimant's price is reduced by ~9%, and the excess recovery reserve (X × recoveryPrice) is permanently locked.
2. **Permanent freezing of user funds**: a user who claims a funded instant receipt and re-requests in the same epoch loses the ability to claim the new receipt after default finalization — `claimInstantWithdrawRequest` reverts on underflow every time, permanently freezing their funded underlying in the strategy.

Both attacker and victims can be any unprivileged tranche holder; no privileged cooperation is needed (the honest borrower default is an in-scope trigger).

### Likelihood Explanation
Likelihood is moderate: it requires (a) an instant-withdraw claim funded and claimed within an epoch, (b) additional pending instant claims or a re-request in the same epoch, and (c) a borrower default finalized while `epochNumber` still equals that epoch — all ordinary, unprivileged flows. The accounting path is deterministic once the state is reached; the stale write is unconditional on every funded instant claim.

### Recommendation
Zero the per-epoch buffers at the same time the aggregate is cleared. In `claimInstantWithdrawRequest`, after computing `amount`, decrement `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` by the claimed amount (or delete the user entry and subtract from the epoch aggregate), mirroring how `_claimDefaultedInstantWithdrawRequest` already self-cleans via `instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis`. Alternatively, track funded-vs-pending instant receipts per epoch so `defaultPendingClaimBasis` only counts unfunded basis.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (contracts: IdleCreditVault + IdleCDOEpochVariant)
// 1. Epoch N running. User A requests instant withdraw X.
vm.prank(address(cdoEpoch));
strategy.requestInstantWithdraw(X, A);   // sets instantWithdrawsRequestsByEpoch[A][N] = X,
                                       //      instantWithdrawClaimsByEpoch[N] = X

// 2. CDO funds the instant request and A claims at par.
vm.prank(address(cdoEpoch));
strategy.collectInstantWithdrawFunds(X);
vm.prank(address(cdoEpoch));
strategy.claimInstantWithdrawRequest(A); // A paid X; instantWithdrawsRequests[A] = 0
// BUG: instantWithdrawsRequestsByEpoch[A][N] == X still; instantWithdrawClaimsByEpoch[N] == X

// 3. Same epoch N: A re-requests a smaller amount Y < X, B requests Z (unfunded).
vm.startPrank(address(cdoEpoch));
strategy.requestInstantWithdraw(Y, A);   // instantWithdrawsRequestsByEpoch[A][N] = X + Y
strategy.requestInstantWithdraw(Z, B);   // pendingInstantWithdraws = Y + Z
vm.stopPrank();

// 4. Borrower defaults in epoch N; manager finalizes with some recovery.
//    defaultPendingClaimBasis() counts instantWithdrawClaimsByEpoch[N] = X + Y + Z
//    => recoveryPrice diluted by phantom X basis.

// 5a. B's claim pays B at the diluted price; X*price worth of reserve is stranded dust.
// 5b. A's claim reverts: claimBasis = X + Y > instantWithdrawsRequests[A] = Y
//     => instantWithdrawsRequests[A] -= claimBasis underflows -> NotAllowed/panic revert
//     => A's funded receipt of Y is permanently frozen.
vm.prank(address(cdoEpoch));
strategy.claimInstantWithdrawRequest(A); // reverts (underflow)
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```
