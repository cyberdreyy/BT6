### Title
Instant-withdraw claims never clear per-epoch accounting — already-paid receipts are double-counted in default recovery, stranding reserve funds and insolventing later claimants - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`claimInstantWithdrawRequest` burns the user's receipt and pays out, but unlike the normal-withdraw path (`_clearWithdrawClaimForEpoch`) it never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or decrements `instantWithdrawClaimsByEpoch[epoch]`. If a borrower default is later finalized in the same epoch while `pendingInstantWithdraws != 0`, `finalizeDefaultRecovery` counts the already-claimed instant basis again via `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve`, inflating both `totalBasis` and `defaultRecoveryReserve` by funds the strategy no longer holds. Recovery is paid out against a phantom claim, so the aggregate reserve is short by exactly the claimed amount and the last recovery claimant(s) cannot be paid.

### Finding Description
The funded instant-claim path clears only the aggregate counter: [1](#0-0) 

It leaves `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]` (set at `requestInstantWithdraw`, lines 371–372) untouched. Contrast with `_clearWithdrawClaimForEpoch`, which zeroes `withdrawsRequestsByEpoch` and shrinks the aggregate: [2](#0-1) 

At default finalization the stale entries re-enter accounting twice:

1. `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` (still including the claimed amount) whenever `pendingInstantWithdraws != 0` — inflating `totalBasis` and therefore dividing `recoveryPrice` by phantom claims.
2. `_defaultPrefundedInstantReserve` computes `instantBasis - pendingInstant` and treats it as underlying still held by the strategy — but the claimed portion was already transferred to the user, so `defaultRecoveryReserve` is credited for tokens that are gone. [3](#0-2) [4](#0-3) [5](#0-4) 

Additionally, `defaultInstantWithdrawsFinalized = true` routes the stale user entry into `_claimDefaultedInstantWithdrawRequest`, which reverts on `instantWithdrawsRequests[_user] -= claimBasis` (aggregate was zeroed) and `_burn(_user, claimBasis)` (receipt already burned) — so the phantom claim can never be drained out, it only poisons the price.

### Impact Explanation
If an attacker claims a funded instant withdrawal of amount `A` in epoch E, then leaves a second unfunded instant request `B`, and the borrower defaults during epoch E:

- `totalBasis` is inflated by `A` → `defaultRecoveryPrice` is depressed for every active LP and every other pending claimant (dilution of the recovery share).
- `defaultRecoveryReserve` is credited `A` tokens that are not in the contract → the last claimant(s) to call `claimWithdrawRequest`/`claimInstantWithdrawRequest` hit a revert in `_transferDefaultRecovery`/`safeTransfer` — permanent freezing of up to `A` underlying of recovery funds.
- The attacker has already extracted `A` once; the protocol-wide shortfall equals `A` (bounded only by instant-withdraw liquidity the attacker can route through the CDO as an ordinary tranche holder).

### Likelihood Explanation
Requires no privileged misbehavior: the attacker is a normal tranche holder using `requestInstantWithdraw`/`claimInstantWithdrawRequest` via `IdleCDOEpochVariant`; funding happens through the ordinary `getInstantWithdrawFunds`/`collectInstantWithdrawFunds` flow, and the trigger is an honest borrower default in the same epoch — an event the protocol is explicitly designed around. The only timing requirement is that a second instant request remains unfunded (`pendingInstantWithdraws != 0`) at finalization, which is precisely the scenario `defaultInstantWithdrawsFinalized` exists for. No existing guard stops it: `_transferFundedClaim`'s reserve check only applies post-finalization, and there is no sweep or reconciliation that would notice the missing `A`.

### Recommendation
In `claimInstantWithdrawRequest`, mirror `_clearWithdrawClaimForEpoch`: locate the user's per-epoch instant entries and zero `instantWithdrawsRequestsByEpoch[_user][epoch]`, decrementing `instantWithdrawClaimsByEpoch[epoch]` accordingly (a small FIFO or `lastInstantWithdrawRequest` marker analogous to `lastWithdrawRequest` suffices, since instant requests span at most the current epoch). Equivalently, make `requestInstantWithdraw` overwrite rather than accumulate per-epoch and clear on claim.

### Proof of Concept
Foundry fork sketch (modeled on `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`, `_checkDefault`):

```solidity
function testInstantClaimStaleEpochBasis() public {
    // setup: deposit as attacker (AA tranche holder), epoch running, instant withdraws enabled
    uint256 A = 100e6; // first instant request
    uint256 B = 50e6;  // second, left unfunded
    // 1) attacker requests instant withdraw A via cdoEpoch.requestInstantWithdraw
    // 2) manager flows funds: cdoEpoch.getInstantWithdrawFunds / strategy.collectInstantWithdrawFunds(A)
    //    -> pendingInstantWithdraws back to 0, strategy holds A
    // 3) attacker claims: cdoEpoch.claimInstantWithdrawRequest()
    //    -> receives A underlying; instantWithdrawsRequests[attacker] == 0
    //    -> BUG: instantWithdrawsRequestsByEpoch[attacker][E] == A,
    //           instantWithdrawClaimsByEpoch[E] == A  (stale)
    // 4) attacker requests instant withdraw B (unfunded):
    //    instantWithdrawClaimsByEpoch[E] == A + B, pendingInstantWithdraws == B
    // 5) borrower defaults (idleCDO defaulted()); manager calls
    //    cdoEpoch.finalizeDefault(recovered, recoverySource)
    uint256 E = strategy.epochNumber();
    // assertions:
    assertEq(strategy.instantWithdrawsRequestsByEpoch(attacker, E), A); // stale
    assertEq(strategy.instantWithdrawClaimsByEpoch(E), A + B);          // stale
    // defaultPendingClaimBasis includes A again:
    // basis = pendingWithdraws + (A + B) but only B is a real claim
    // _defaultPrefundedInstantReserve = (A + B) - B = A, but strategy holds 0 extra
    // -> defaultRecoveryReserve > underlying.balanceOf(strategy) share earmarked:
    //    reserveAmount overstated by A; recoveryPrice diluted by A / totalBasis
    // 6) other claimants claim; final claim reverts (balance < reserve) ->
    //    A underlying of recovery permanently frozen / insolvency == A
}
```

Broken invariant: one receipt one payout / solvency — `defaultRecoveryReserve` must never exceed underlying actually held for claimants.

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-696)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L815-820)
```text
    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    if (normalAmount != 0) {
      withdrawsRequestsByEpoch[_user][_claimEpoch] = 0;
      // The aggregate may also include older funded receipts; clear only this epoch's piece.
      withdrawsRequests[_user] -= normalAmount;
    }
```
