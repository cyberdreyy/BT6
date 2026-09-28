### Title
Stale per-epoch instant-withdraw basis survives a par claim and is paid a second time from the default recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug is a lazy-GC walk that removes one half of an interval pair but leaks/mis-handles the other half (and misidentifies which element is an "end" interval). The direct analog lives in `IdleCreditVault`: receipt accounting is split into an aggregate counter (`instantWithdrawsRequests`) and a per-epoch map (`instantWithdrawsRequestsByEpoch`), but the funded-claim path `claimInstantWithdrawRequest` clears only the aggregate half. The per-epoch "end interval" is never released. If that stale epoch later becomes `defaultRecoveryEpoch`, `_claimDefaultedInstantWithdrawRequest` treats the already-paid receipt as an outstanding defaulted claim and pays it again — at recovery price — out of `defaultRecoveryReserve`, while also letting the attacker withdraw fresh unfunded principal at par from other users' funded claims.

### Finding Description
`requestInstantWithdraw` records two ledgers: `instantWithdrawsRequests[_user]` (aggregate) and `instantWithdrawsRequestsByEpoch[_user][epochNumber]` plus `instantWithdrawClaimsByEpoch[epochNumber]` [1](#0-0) .

`claimInstantWithdrawRequest` only zeroes the aggregate and transfers at par. It never touches `instantWithdrawsRequestsByEpoch` or `instantWithdrawClaimsByEpoch` [2](#0-1) . Unlike normal receipts, which are keyed to epochs that can never equal `defaultRecoveryEpoch` (a par claim requires `epochNumber > requestEpoch`, while `defaultRecoveryEpoch` is set to the *current* `epochNumber` at finalization [3](#0-2) ), instant receipts are claimable mid-epoch — including during the buffer of epoch `N`, which is exactly the epoch number that `finalizeDefaultRecovery` stores as `defaultRecoveryEpoch` when epoch `N` finalizes in default.

Sequence:
1. Epoch N-1 ends with borrower non-repayment pending finalization (or epoch N is the defaulted epoch); `epochNumber == N` during the buffer.
2. Attacker calls `requestInstantWithdraw(S)` → keyed under `N`. Manager (honest) funds it via `collectInstantWithdrawFunds`. Attacker claims at par — aggregate cleared, `instantWithdrawsRequestsByEpoch[attacker][N] = S` survives. Another user's instant request remains unfunded so `pendingInstantWithdraws != 0`.
3. `finalizeDefaultRecovery` sets `defaultRecoveryEpoch = N`, `defaultInstantWithdrawsFinalized = true` [4](#0-3) .
4. Attacker makes a post-default `requestInstantWithdraw(T)` with `T ≥ S`, then calls `claimInstantWithdrawRequest`. `_claimDefaultedInstantWithdrawRequest` reads the stale basis `S`, pays `S * defaultRecoveryPrice` from `defaultRecoveryReserve`, and burns `S` of the fresh receipt [5](#0-4) . The remainder `T - S` is then paid at par by `_transferFundedClaim` from non-reserve strategy balance — i.e., underlyings reserved for other users' funded receipts — because the attacker never supplied principal for the instant request (instant requests burn strategy tokens, not underlying).

Broken invariant: one receipt, one payout. The stale per-epoch entry is a phantom claim basis — the rbtree "end interval" that was never removed.

### Impact Explanation
Net attacker profit is `(T - S) * (1 - recoveryPrice)` plus the recovery-reserve payout `S * recoveryPrice` on a basis that was already paid once. The loss is borne by (a) other default-recovery claimants, whose `defaultRecoveryReserve` share is drained, and (b) holders of funded-but-unclaimed withdraw/instant receipts whose underlyings are paid out at par to the attacker. Quantified theft is bounded by the attacker's pre-default instant request size `S` and the non-reserve funded balance `B`: up to `S * recoveryPrice + min(T - S, B)`. This is direct theft of reserve/funded claims, not a rounding artifact.

### Likelihood Explanation
Requires: instant withdrawals enabled; a default whose `defaultRecoveryEpoch` coincides with the epoch in which the attacker's par-claimed instant receipt was recorded (naturally satisfied — instant requests made during the defaulted epoch's buffer carry exactly that `epochNumber`); at least one *other* unfunded instant receipt so `defaultInstantWithdrawsFinalized` is true; and post-default instant requests still being routable through the CDO (the strategy function itself has no `defaultRecoveryFinalized` gate [6](#0-5) ). The attacker is an ordinary KYC'd tranche holder — no privileged role needed. Medium likelihood: it needs a real borrower default plus instant-withdraw configuration, but every step uses normal user flows.

Caveat: I could not fully trace the CDO-side gating of `requestInstantWithdraw`/`claimInstantWithdrawRequest` in `IdleCDOEpochVariant` post-default within the available iterations; if the CDO hard-blocks instant requests after `defaulted()`, the window narrows to the pre-finalization buffer, which still allows the stale-basis double-pay via step 4 performed by any user holding `instantWithdrawsRequests ≥ S` at finalization.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch entry when the aggregate receipt is consumed: track the request epoch per user (or iterate `instantWithdrawsRequestsByEpoch` for epochs `≤ currentEpoch`) and decrement both `instantWithdrawsRequestsByEpoch[_user][e]` and `instantWithdrawClaimsByEpoch[e]`, mirroring how `_clearWithdrawClaimForEpoch` zeroes `withdrawsRequestsByEpoch` for normal receipts [7](#0-6) . Additionally, gate `requestInstantWithdraw` when `defaultRecoveryFinalized`, as `requestWithdraw` already does for post-default flows.

### Proof of Concept
Foundry fork PoC sketch (harness mirrors `test/foundry/IdleCDOEpochQueue.t.sol` / `ProgrammableBorrowerEpochInvariant.t.sol`):

```solidity
// Setup: standard IdleCDOEpochVariant + IdleCreditVault, instant withdrawals enabled
// (cdoEpoch.setInstantWithdrawParams(delay, minAprDelta, false)), manager/borrower honest.

// 1) Epoch N-1 running: userHonest requests instant H (will remain unfunded).
// 2) stopEpoch -> borrower underpays; epoch N buffer begins, epochNumber == N, default not yet finalized.
// 3) Attacker: cdoEpoch.requestInstantWithdraw(S)        // keyed under epoch N
//    manager: strategy.collectInstantWithdrawFunds(S)    // fund attacker's receipt
//    attacker: cdoEpoch.claimInstantWithdrawRequest()    // paid at par; byEpoch[N] = S survives
// 4) Finalize default: cdoEpoch.finalizeDefaultRecovery(...) // defaultRecoveryEpoch = N,
//    defaultInstantWithdrawsFinalized = true because pendingInstantWithdraws (H) != 0
// 5) Attacker: cdoEpoch.requestInstantWithdraw(T), T = S + drainable
//    attacker: cdoEpoch.claimInstantWithdrawRequest()
//    assert received == S * defaultRecoveryPrice / 1e18 + (T - S)   // phantom + par drain
//    assert defaultRecoveryReserve decreased by S*price while instantWithdrawClaimsByEpoch[N]
//    double-counted an already-claimed receipt.
```

If step 5's post-default `requestInstantWithdraw` is blocked at the CDO layer, pre-seed the attacker's aggregate so that `instantWithdrawsRequests[attacker] ≥ S` at finalization (e.g., a second unfunded instant request also keyed to `N`); the claim then still pays `S * recoveryPrice` twice-counted basis and reverts-or-drains on the remainder, demonstrating at minimum a reserve-accounting corruption / freezing of honest claims.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-363)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L646-648)
```text
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L693-696)
```text
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L815-820)
```text
    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    if (normalAmount != 0) {
      withdrawsRequestsByEpoch[_user][_claimEpoch] = 0;
      // The aggregate may also include older funded receipts; clear only this epoch's piece.
      withdrawsRequests[_user] -= normalAmount;
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
