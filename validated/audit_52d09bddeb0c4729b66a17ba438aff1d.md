### Title
Post-default instant-withdraw claims bypass the recovery haircut and drain `defaultRecoveryReserve` at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestWithdraw` has a dedicated post-finalization path that routes new requests into `postDefaultRequests` so they are paid only from (already-haircut) post-default basis. `requestInstantWithdraw`/`claimInstantWithdrawRequest` has no equivalent guard: after `finalizeDefaultRecovery`, a user can still open an instant receipt and claim it at par through `_transferFundedClaim`, drawing from the same underlying balance that backs the haircutted default recovery. This mirrors the ContextModule bug class: a secondary loading path that bypasses the enforcement applied to the primary path.

### Finding Description
In `requestWithdraw`, post-finalization requests are isolated into `postDefaultRequests` and explicitly kept out of borrower-facing `pendingWithdraws` [1](#0-0) . The instant path only calls `_ensureDefaultRecoveryInitialized` and then unconditionally burns CDO tokens and mints a full-value receipt into `instantWithdrawsRequests[_user]` [2](#0-1) .

On claim, the defaulted-haircut branch `_claimDefaultedInstantWithdrawRequest` only runs when `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`; the latter is true only if `pendingInstantWithdraws != 0` at finalization [3](#0-2) [4](#0-3) . A receipt created *after* finalization therefore falls through to `_transferFundedClaim(_user, amount)` at 100% of face value, while every defaulted-epoch and active claim was priced at `defaultRecoveryPrice`. The new receipt was never part of `totalBasis`, so its payout is paid entirely out of `defaultRecoveryReserve`/strategy balance earmarked for haircutted claimants [5](#0-4) [6](#0-5) .

### Impact Explanation
Direct theft of the defaulted recovery reserve. If a pool defaults with, e.g., 1000 underlying of claims and 100 recovered (10% `defaultRecoveryPrice`), an unprivileged user (tranche-token holder / KYC-passing lender) who requests an instant withdraw of, say, 50 after finalization can claim the full 50 from the reserve, while legitimate claimants recover ~9% less than entitled; repeated/drained claims make the reserve insolvent for entitled receipts. Broken invariant: one receipt one payout at the crystallized recovery ratio.

### Likelihood Explanation
Requires the pool to be in `defaultRecoveryFinalized` state with instant withdrawals enabled (`allowInstantWithdraw`) and residual strategy/underlying balance — i.e., `defaultInstantWithdrawsFinalized == false` (no unfunded instant bucket at finalization) yet underlying present (recovery pulled via `safeTransferFrom` in `finalizeDefaultRecovery` [7](#0-6) ). All roles stay honest; the attacker only needs tranche tokens / a prior deposit, which the unprivileged attacker model permits. Caveat: I could not fully verify whether `IdleCDOEpochVariant.requestWithdraw`/`requestInstantWithdraw` block post-default instant requests upstream (e.g., via `defaulted` or flag checks); if the CDO reverts before reaching the strategy, the exploit path is gated. The strategy itself has no `defaultRecoveryFinalized` revert in the instant path.

### Recommendation
Apply the same post-finalization routing as `requestWithdraw`: in `requestInstantWithdraw`, revert or route to `postDefaultRequests` when `defaultRecoveryFinalized` is true, and in `claimInstantWithdrawRequest` treat post-finalization receipts as post-default claims so they cannot pay at par from the reserve. Alternatively set a flag at finalization disabling new instant receipts entirely.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (pool already defaulted and finalized)
// 1. finalizeDefaultRecovery leaves defaultInstantWithdrawsFinalized == false
//    (pendingInstantWithdraws == 0 at finalization), recoveryPrice = R < RECOVERY_FULL.
// 2. Attacker (holds BB/AA tranche tokens, KYC-passed) with instant withdraws enabled:
//    - cdoEpoch.requestInstantWithdraw(amount) / requestWithdraw(amount, tranche)
//      -> strategy.requestInstantWithdraw mints `amount` receipt tokens to attacker,
//         burning attacker's strategy tokens at face value.
// 3. Attacker calls cdoEpoch.claimInstantWithdrawRequest()
//    -> skips _claimDefaultedInstantWithdrawRequest (flag false)
//    -> _transferFundedClaim pays `amount` underlying at par from
//       defaultRecoveryReserve instead of `amount * R / RECOVERY_FULL`.
// 4. Assert: underlying drained > entitled share; remaining claimants'
//    _claimDefaultedWithdrawRequest payouts fall short or revert on reserve.
```

Note: the Foundry PoC must confirm step 2 reaches the strategy — if `IdleCDOEpochVariant` reverts post-default instant requests upstream, this reduces to defense-in-depth. I was unable to fully trace `IdleCDOEpochVariant.requestWithdraw`'s instant routing and post-default gating within the available context.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-374)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L382-386)
```text
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-692)
```text
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L694-696)
```text
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L706-709)
```text
    if (_recoveredAmount != 0) {
      // Pull external recovery last: if the transfer fails, the whole finalization reverts.
      underlyingToken.safeTransferFrom(_recoverySource, address(this), _recoveredAmount);
    }
```
