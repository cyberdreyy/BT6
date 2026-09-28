### Title
Stale `instantWithdrawClaimsByEpoch`/`instantWithdrawsRequestsByEpoch` entries inflate the default recovery reserve and let early claimants drain defaulted-recovery funds - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Analogous to CVE-2017-13769 (an out-of-bounds read where `WriteTHUMBNAILImage` reads past the end of a JPEG buffer), `IdleCreditVault` reads a stale per-epoch ledger when computing the default recovery basis. `instantWithdrawClaimsByEpoch[epochNumber]` and `instantWithdrawsRequestsByEpoch[user][epoch]` are incremented at request time but are never decremented when an instant withdraw is successfully claimed pre-default, so `finalizeDefaultRecovery` "over-reads" claim data that was already settled and inflates `defaultRecoveryReserve` with underlying that already left the contract.

### Finding Description
When a user calls `requestInstantWithdraw`, the vault records the request in two places: the aggregate `instantWithdrawsRequests[user]`/`pendingInstantWithdraws`, and the per-epoch `instantWithdrawsRequestsByEpoch[user][epoch]`/`instantWithdrawClaimsByEpoch[epoch]` (lines 366–374). On a successful pre-default claim, `claimInstantWithdrawRequest` only zeroes the aggregate counter (lines 387–392) — the per-epoch entries are never cleared; they are only cleared inside `_claimDefaultedInstantWithdrawRequest` (lines 847–853), which only runs after a default.

During `finalizeDefaultRecovery`, the recovery basis is computed by `defaultPendingClaimBasis`, which adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` (lines 644–649), and the already-held prefunded portion is added to the reserve via `_defaultPrefundedInstantReserve` = `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` (lines 716–723). Both computations assume every entry in `instantWithdrawClaimsByEpoch[epochNumber]` is still an outstanding claim. If any instant receipt for the current epoch was already claimed, that amount was paid out but is still counted as (a) outstanding claim basis and (b) underlying already held by the strategy.

`defaultRecoveryReserve` is therefore set to `reserveAmount` (line 686/691) that exceeds the contract's real underlying balance by the already-paid amount. `_transferDefaultRecovery` decrements this phantom bookkeeping and `safeTransfer`s real tokens (lines 912–917), so the first claimants are paid from other participants' recovery money and later claims revert either on `defaultRecoveryReserve -= _amount` underflow or on an insufficient balance `safeTransfer`. The same stale read also means a user's `_claimDefaultedInstantWithdrawRequest` pays recovery on receipts that were already paid at par, as long as the aggregate `instantWithdrawsRequests[user]` (refilled by any other instant request) is large enough for the subtraction at line 848 to not underflow.

### Impact Explanation
Direct theft plus permanent freezing: an unprivileged user who claimed an instant withdrawal and still holds any remaining/defaulted instant or withdraw receipt can call `claimInstantWithdrawRequest`/`claimWithdrawRequest` and be paid an inflated `defaultRecoveryPrice` on phantom basis, extracting `alreadyPaidInstant * defaultRecoveryPrice / RECOVERY_FULL` more than their fair share. Equivalently-sized recovery claims of other users (normal defaulted withdraw receipts, post-default requests, and defaulted instant receipts of other users) permanently revert at the end of the reserve, so a portion of the borrower recovery is unclaimable. Loss is bounded by the already-claimed instant amount in the defaulted epoch, which can be arbitrarily large.

### Likelihood Explanation
Requires (i) at least one instant withdrawal requested and successfully claimed in an epoch, (ii) additional instant requests in the same epoch that remain at least partially unfunded at `stopEpoch` (`pendingInstantWithdraws != 0`), and (iii) the borrower defaulting in that epoch so `finalizeDefaultRecovery` runs. Instant withdrawals are a normal user flow, epoch overlap of funded and unfunded instant requests is realistic during liquidity stress (precisely when defaults happen), and no privileged-role misbehavior is needed — the CDO calls `finalizeDefaultRecovery` honestly. An attacker can also deliberately create the condition by requesting and claiming a large instant withdrawal, then leaving a small unfunded instant request outstanding in the same epoch before the epoch is stopped.

### Recommendation
When an instant withdrawal is claimed pre-default, also clear the per-epoch records: in `claimInstantWithdrawRequest`, decrement `instantWithdrawClaimsByEpoch[epochOfClaim]` and zero `instantWithdrawsRequestsByEpoch[user][epoch]` (or otherwise track only outstanding per-epoch amounts). Because the aggregate claim may span multiple epochs, keep a per-epoch outstanding counter that is decremented on both funding (`collectInstantWithdrawFunds`) and claiming, so `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` only see unsettled receipts. Alternatively, snapshot `instantWithdrawClaimsByEpoch[epoch]` only for the unfunded remainder at default time rather than treating cumulative requests as outstanding claims.

### Proof of Concept
Foundry fork sketch (contracts: `IdleCDOEpochVariant` as `cdo`, `IdleCreditVault` as `vault`, honest `borrower`/`manager`):

```solidity
// Epoch N is running; unscaledApr > 0, instant withdraws enabled.
// 1) Attacker (KYC'd lender) deposits and requests an instant withdraw of X.
cdo.deposit(X, trancheAA);
cdo.requestInstantWithdraw(X, trancheAA);

// 2) CDO funds the instant queue; attacker claims X at par.
//    instantWithdrawsRequests[attacker] = 0 but
//    instantWithdrawsRequestsByEpoch[attacker][N] and
//    instantWithdrawClaimsByEpoch[N] still contain X.
vm.prank(manager);
cdo.claimInstantWithdrawRequest(); // -> vault pays attacker X

// 3) Victim requests a small instant withdraw Y that stays partially unfunded.
cdo.deposit(Y, trancheAA);           // by victim
cdo.requestInstantWithdraw(Y, trancheAA);
// pendingInstantWithdraws > 0 with only part of Y collected.

// 4) Epoch ends; borrower returns nothing -> default.
vm.prank(manager);
cdo.stopEpoch(0, 0);                 // getFundsFromBorrower fails -> defaulted

// 5) Owner/manager finalizes with the honest recovery amount R.
//    defaultPendingClaimBasis() = pendingWithdraws + instantWithdrawClaimsByEpoch[N]
//    which still includes the attacker's already-paid X.
//    _defaultPrefundedInstantReserve() counts X as strategy-held cash that
//    was actually transferred to the attacker in step 2.
vault.finalizeDefaultRecovery(R, recoverySource); // via cdo.finalizeDefaultRecovery

// defaultRecoveryReserve is overstated by X while the real balance is not.
// 6) Attacker calls cdo.claimInstantWithdrawRequest()/claimWithdrawRequest()
//    and receives defaultRecoveryPrice on phantom basis, draining real tokens.
// 7) Victim's defaulted claim later reverts:
//    either `defaultRecoveryReserve -= _amount` underflows or
//    underlyingToken.safeTransfer fails on insufficient balance.
```

Assertion targets: after step 5, `vault.defaultRecoveryReserve() > underlying.balanceOf(address(vault))` by exactly the already-claimed instant amount `X`; after the attacker's claim in step 6, a subsequent legitimate `claimWithdrawRequest`/`claimInstantWithdrawRequest` reverts.