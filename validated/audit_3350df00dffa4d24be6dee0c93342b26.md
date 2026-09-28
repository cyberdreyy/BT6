### Title
Claimed instant-withdraw receipts stay in `instantWithdrawClaimsByEpoch`, inflating the default-recovery basis and diluting all defaulted claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug class is "stale tally vs. shrinking denominator": votes of removed members still count while quorum uses the current member count. The idle-tranches analog lives in the instant-withdraw accounting of `IdleCreditVault`: the funded-claim path removes a user's claim from the per-user counter but never removes it from the per-epoch claim tally (`instantWithdrawsRequestsByEpoch` / `instantWithdrawClaimsByEpoch`). Those already-paid claims are later counted again in `defaultPendingClaimBasis()` during `finalizeDefaultRecovery`, so the recovery price is computed against an inflated basis.

### Finding Description
`requestInstantWithdraw` records each request in three places:

- `instantWithdrawsRequests[_user]` (per-user aggregate)
- `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`
- `instantWithdrawClaimsByEpoch[currentEpoch]` [1](#0-0) 

When the CDO funds the queue via `collectInstantWithdrawFunds`, `pendingInstantWithdraws` shrinks but the per-epoch claim basis correctly remains, because receipts are still outstanding.

The bug is in `claimInstantWithdrawRequest`: [2](#0-1) 

It burns the receipt tokens, zeroes `instantWithdrawsRequests[_user]`, and pays out — but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` and never decrements `instantWithdrawClaimsByEpoch[epoch]`. The already-paid claim remains in the epoch tally forever, exactly like votes of kicked oDAO members remaining in the tally.

Later, if the borrower defaults in that same epoch while `pendingInstantWithdraws != 0` (i.e., some other request was only partially funded), `defaultPendingClaimBasis()` adds the full `instantWithdrawClaimsByEpoch[epochNumber]` — including claims that were already paid out — into the recovery basis: [3](#0-2) 

`finalizeDefaultRecovery` then computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` with the inflated `totalBasis`: [4](#0-3) 

Additionally, `_defaultPrefundedInstantReserve()` computes the already-funded portion as `instantBasis - pendingInstant`; because stale claimed amounts remain in `instantBasis`, the prefunded reserve is overstated and counted as available reserve even though those underlyings were already transferred out — the strategy pulls/holds less than the accounted reserve, further corrupting `recoveryPrice`: [5](#0-4) 

A second-order effect: a user who claimed a funded receipt and then makes a new instant request in the same epoch accumulates `instantWithdrawsRequestsByEpoch[user][epoch]` larger than their receipt-token balance. After default finalization, `_claimDefaultedInstantWithdrawRequest` tries to `_burn(_user, claimBasis)` for the whole stale per-epoch amount, which exceeds their balance and reverts — their legitimate defaulted claim becomes permanently unclaimable, and their stale basis never leaves `instantWithdrawClaimsByEpoch`: [6](#0-5) 

### Impact Explanation
Direct, quantified loss to all defaulted-epoch claimants: the recovery price is divided by a `totalBasis` that includes already-paid claims, so every legitimate claimant (pending normal withdraws, unfunded instant receipts, and active AA/BB tranche holders via the `activeFinalNAV` burn/mint) receives a strictly smaller payout than funded. Example: with a real claim basis of 100 and stale claimed basis of 100, a reserve of 100 yields `defaultRecoveryPrice = 0.5e18` instead of `1e18` — a 50% haircut applied to real claimants, with the unspent excess reserve permanently stranded in the strategy (no function distributes leftover `defaultRecoveryReserve`; only the honest owner's `transferToken` could rescue it). Users who re-requested in the same epoch after claiming suffer permanent freezing of their real defaulted claim due to the over-burn revert.

### Likelihood Explanation
Requires only unprivileged actions: any KYC-passed lender/tranche holder can call `requestInstantWithdraw` (via the CDO), have it funded, claim it, and leave the stale basis behind. The trigger is a borrower default in the same epoch with a nonzero `pendingInstantWithdraws` remainder — a normal, expected flow (partial prefunding of the instant queue is explicitly supported by `_defaultPrefundedInstantReserve`). No privileged or malicious role is needed; the accounting defect activates whenever this sequence occurs.

### Recommendation
In `claimInstantWithdrawRequest`, determine the request epoch (or iterate/clear all per-epoch entries for the user) and symmetrically clear the per-epoch accounting: subtract the claimed amount from `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`. If claims can span a single current epoch only, storing the request epoch per user (like `lastWithdrawRequest`) suffices. Alternatively, settle per-epoch instant claims at claim time against `epochNumber`-keyed buckets the same way `withdrawsRequestsByEpoch` is cleared in `_clearWithdrawClaimForEpoch`.

### Proof of Concept
Foundry fork test extending `test/foundry/IdleCreditVault.t.sol` setup:

1. Deploy `IdleCreditVault` + `IdleCDOCreditVault`/`IdleCDOEpochVariant` on a mainnet fork with an ERC20 underlying; whitelist a lender via `KeyringIdleWhitelist`.
2. `startEpoch`, lender deposits, epoch runs.
3. Lender calls `requestInstantWithdraw(amount)` through the CDO; manager/borrower funds it; CDO calls `collectInstantWithdrawFunds(amount)` → `pendingInstantWithdraws` becomes 0 for that request.
4. Lender calls `claimInstantWithdrawRequest` → receives `amount`; assert `instantWithdrawsRequestsByEpoch[lender][epochNumber]` is still `amount` and `instantWithdrawClaimsByEpoch[epochNumber]` is still `amount` (bug).
5. A second lender requests an instant withdraw that remains unfunded (`pendingInstantWithdraws > 0`).
6. Borrower defaults in the same epoch; owner calls the default path → `finalizeDefaultRecovery`. Assert `defaultPendingClaimBasis()` includes the already-paid `amount`, `defaultRecoveryPrice` is correspondingly lower, and the second lender's claim pays less than funded; assert excess `defaultRecoveryReserve` remains stranded after all real claims.
7. Variant: after step 4, the lender requests a smaller instant withdraw in the same epoch, then post-finalization `claimInstantWithdrawRequest` reverts on `_burn` overflow — legitimate claim permanently frozen.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-696)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L844-855)
```text
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
