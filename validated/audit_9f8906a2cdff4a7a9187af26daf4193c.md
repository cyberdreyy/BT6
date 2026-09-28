### Title
Instant-withdraw receipts from pre-default epochs bypass the recovery haircut and drain funded claim liquidity at par — ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Analogous to the diffusers bug — where the `trust_remote_code` gate lived in `download()` instead of the actual load site, so any path that skipped `download()` skipped the check — the default-recovery haircut in `IdleCreditVault` is keyed to `defaultRecoveryEpoch` only. `_claimDefaultedInstantWithdrawRequest` clears exclusively `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`, so any unfunded instant receipt recorded under an earlier epoch survives finalization untouched and is then paid at 100% by the unconditional `_transferFundedClaim` fallback in `claimInstantWithdrawRequest`.

### Finding Description
`requestInstantWithdraw` records the receipt under the *request-time* `epochNumber` (`instantWithdrawsRequestsByEpoch[_user][epochNumber]`, line 371) and adds to the aggregate `instantWithdrawsRequests[_user]` (line 366) and `pendingInstantWithdraws` (line 374).

When `finalizeDefaultRecovery` runs (line 661), `defaultPendingClaimBasis` (line 644) only adds `instantWithdrawClaimsByEpoch[epochNumber]` — the *current* (default) epoch — to the recovery basis. Likewise `_defaultPrefundedInstantReserve` (line 716) only measures `instantWithdrawClaimsByEpoch[epochNumber]` against `pendingInstantWithdraws`, so a stale unfunded instant basis from an earlier epoch inflates `pendingInstantWithdraws` without being included in `totalBasis`, and the recovery price computed at line 688 never accounts for it.

On claim, `claimInstantWithdrawRequest` (line 380):
1. calls `_claimDefaultedInstantWithdrawRequest`, which only zeroes `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (lines 843–847) — a stale-epoch receipt keyed under `defaultRecoveryEpoch - 1` or older returns `claimBasis == 0` and is skipped;
2. then reads the *full remaining* `instantWithdrawsRequests[_user]` (line 387), burns the receipt tokens, and pays the entire amount at par via `_transferFundedClaim` (line 392).

`_transferFundedClaim` (line 897) only protects `defaultRecoveryReserve`; any strategy balance above the reserve — i.e., liquidity collected for *other* users' funded but unclaimed receipts — is freely spendable. The stale receipt was never haircutted, never included in `totalBasis`, and is paid 1:1.

### Impact Explanation
An attacker holding an unfunded instant-withdraw receipt from an epoch preceding the default epoch claims it at par after finalization, while every defaulted-epoch claimant (normal receipts, APR0 receipts, instant receipts, active LPs via tranche prices) is paid only `defaultRecoveryPrice`. The payout comes out of pooled funded liquidity belonging to other receipt holders — direct theft equal to the attacker's stale `instantWithdrawsRequests` balance, up to `balanceOf(strategy) - defaultRecoveryReserve`. Where no such excess liquidity exists, the same missing-epoch-key gap instead makes the legitimate claim permanently revert in `_transferFundedClaim`, freezing funded receipts — both outcomes are in scope (theft / permanent freezing of unclaimed funds).

The broken invariant is "one receipt, one haircut": receipt ownership is per-epoch but the recovery gate is single-epoch, exactly like a security check bound to the primary repo instead of the module actually being loaded.

### Likelihood Explanation
Reachable by any unprivileged user of the instant-withdraw flow (a KYC-passing lender is in-scope as attacker):

- The user calls `requestWithdraw` while the APR-drop condition routes to `requestInstantWithdraw` during epoch N; the borrower/manager (honest but not required to fully prefund) only partially funds instant requests at the next `startEpoch`/`collectInstantWithdrawFunds`, leaving `pendingInstantWithdraws > 0` and the user's basis keyed under epoch N.
- `stopEpoch` bumps `epochNumber` to N+1; the borrower then defaults during epoch N+1 (`_handleBorrowerDefault` in `IdleCDOEpochVariant`, line 577), and the CDO calls `finalizeDefaultRecovery`, setting `defaultRecoveryEpoch = N+1`.
- Because `defaultInstantWithdrawsFinalized` becomes true (`pendingInstantWithdraws != 0`), the claim path executes — it just clears the wrong epoch key.
- No privileged misbehavior is required; partial instant funding is an ordinary liquidity shortfall, and defaults are a designed flow. The only precondition is an instant receipt straddling a `stopEpoch` boundary while underfunded, which the protocol's own accounting (`pendingInstantWithdraws` surviving across `epochNumber` increments) explicitly permits.

Existing guards do not stop it: `_onlyIdleCDO` is satisfied (claim goes through the CDO), the reserve guard in `_transferFundedClaim` only ring-fences `defaultRecoveryReserve`, and `requestWithdraw`'s `lossRecoveryPriceByEpoch` re-request guard is never consulted because `lastWithdrawRequest` is not set by the instant path.

### Recommendation
Apply the recovery gate at the claim chokepoint rather than at a single epoch key — mirroring the upstream fix that moved `trust_remote_code` into `get_cached_module_file`. Concretely, in `IdleCreditVault.sol`:

- In `_claimDefaultedInstantWithdrawRequest`, iterate or aggregate `instantWithdrawsRequestsByEpoch[_user]` over *all* epochs with non-zero basis (or track a per-user aggregate receipt basis) so every outstanding instant receipt is haircutted at `defaultRecoveryPrice`, not only `defaultRecoveryEpoch`.
- Include all outstanding instant claim basis (sum over `instantWithdrawClaimsByEpoch`, not just `[epochNumber]`) in `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` so `defaultRecoveryPrice` and the reserve are computed over the true claim set.
- Revert in `claimInstantWithdrawRequest` if, after `defaultRecoveryFinalized`, any `instantWithdrawsRequests[_user]` remains that was not settled through the recovery path, instead of falling through to `_transferFundedClaim`.

### Proof of Concept
Foundry fork PoC sketch against `test/foundry/IdleCreditVault.t.sol` helpers (`_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`, `cdoEpoch`, `idleCDO`, `underlying`, `AAtranche`):

```solidity
function testStaleEpochInstantReceiptBypassesRecoveryHaircut() external {
    uint256 amount = 100 * ONE_SCALE;
    idleCDO.depositAA(amount);

    // Epoch N: APR drops so requestWithdraw routes to requestInstantWithdraw
    _requestInstantWithdrawViaAprDrop(aaTrancheBalance / 2); // sets instantWithdrawsRequestsByEpoch[me][N]

    // Borrower only partially funds instant queue at next startEpoch:
    // collectInstantWithdrawFunds covers < requested -> pendingInstantWithdraws > 0
    _startEpochWithPartialInstantFunding();

    // stopEpoch bumps epochNumber to N+1; attacker does NOT claim (no excess liquidity needed yet)
    _stopEpochAndCheckPrices(0, apr, expectedFunds);

    // Epoch N+1 runs; borrower defaults -> finalizeDefaultRecovery sets
    // defaultRecoveryEpoch = N+1, defaultRecoveryPrice < 1e18
    _forceBorrowerDefaultAndFinalize(recovered < totalBasis);

    // Attacker claims: _claimDefaultedInstantWithdrawRequest clears epoch N+1 basis (0 for
    // attacker), then pays FULL instantWithdrawsRequests[me] at par from funded liquidity
    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 got = underlying.balanceOf(address(this)) - balPre;

    // Paid at par instead of recoveryPrice
    assertEq(got, staleInstantBasis, "stale receipt bypassed haircut");
    assertLt(cdoEpoch.defaultRecoveryPriceEquivalent(), ONE_SCALE, "others were haircutted");
}
```

The key assertions: (1) `instantWithdrawsRequestsByEpoch[attacker][N] != 0` survives finalization because only `[N+1]` is cleared, and (2) the payout equals the stale basis while `defaultRecoveryPrice < RECOVERY_FULL`, proving par payment from liquidity pooled for other claimants.

**Uncertainty note:** index limits prevented confirming the exact partial-funding sequence inside `startEpoch`/`getInstantWithdrawFunds`/`collectInstantWithdrawFunds` in `IdleCDOEpochVariant.sol` beyond line 1000; the PoC assumes — consistent with the comments at lines 636–640 and 713–723 — that `pendingInstantWithdraws` can remain non-zero across an epoch boundary. If `stopEpoch`/`startEpoch` always forces `pendingInstantWithdraws` to zero, the attacker's stale-basis precondition cannot arise and this reduces to a permanent-freeze (DoS) scenario for the same missing-epoch-key reason.