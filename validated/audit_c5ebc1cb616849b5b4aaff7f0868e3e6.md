### Title
Funded instant-withdraw receipts are never cleared from `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` on normal claim, enabling a double recovery payout after a same-epoch default - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The kernel bug is an imbalanced acquire/release: `streamon` increments `usage_count`, but the release path never decrements it, so the channel is leaked permanently. The analog in `IdleCreditVault` is the instant-withdraw receipt lifecycle. `requestInstantWithdraw` acquires three pieces of state — `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epoch]`, and `instantWithdrawClaimsByEpoch[epoch]` — but the normal release path `claimInstantWithdrawRequest` only clears the aggregate counter, leaving the per-epoch counters permanently elevated. If the borrower defaults later in that same epoch, `_claimDefaultedInstantWithdrawRequest` reads the stale per-epoch basis and pays the attacker a second time out of the default-recovery reserve.

### Finding Description
In `requestInstantWithdraw`, three counters are incremented:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:356-375
instantWithdrawsRequests[_user] += _amount;
instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
pendingInstantWithdraws += _amount;
```

But `claimInstantWithdrawRequest` (the funded, at-par release path) clears only one:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:387-392
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

`instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` are never decremented. They are only cleared in `_claimDefaultedInstantWithdrawRequest`, which is reachable after `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:842-856
claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
...
instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
instantWithdrawsRequests[_user] -= claimBasis;
pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
_burn(_user, claimBasis);
_transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
```

Attack sequence (buffer phase of epoch `E`, standard non-programmable variant with instant withdraws enabled and APR dropped):

1. Attacker (KYC-passed lender) deposits and calls `requestWithdraw` twice: once routed to `requestInstantWithdraw(X)` and once as a normal `requestWithdraw(Y)` with `Y >= X`. Both mint fungible strategy-token receipts to the attacker (`_mint(_user, _amount)` at lines 275 and 363), so the attacker holds `X + Y` receipt tokens.
2. Epoch `E` starts; `getInstantWithdrawFunds` (or `startEpoch`'s `collectInstantWithdrawFunds`) funds the instant request. Attacker calls `claimInstantWithdrawRequest`: burns `X`, receives `X` at par. `instantWithdrawsRequestsByEpoch[attacker][E]` still equals `X`.
3. Honest borrower fails to repay; `stopEpoch` → `_handleBorrowerDefault` sets `defaulted` with `defaultRecoveryEpoch == E`. Manager later calls `finalizeDefault`, which sets `defaultRecoveryFinalized`.
4. Attacker calls `claimInstantWithdrawRequest` again. The defaulted branch fires: `claimBasis = instantWithdrawsRequestsByEpoch[attacker][E] = X`. The `_burn(_user, X)` succeeds because the attacker still holds `Y >= X` receipt tokens from the normal request — the burn is over the fungible balance, not the per-epoch ledger. The contract pays `X * defaultRecoveryPrice / RECOVERY_FULL` from the recovery reserve a second time.

The stale `instantWithdrawClaimsByEpoch[E]` also keeps the epoch's instant-claim total inflated, so recovery accounting treats already-paid claims as outstanding.

### Impact Explanation
Direct theft of recovery funds: the attacker extracts `X` at par plus `X * defaultRecoveryPrice` from the reserve, double-spending one receipt. Because the recovery reserve is a fixed pool funded by `_recoveredAmount` in `finalizeDefaultRecovery`, every unit overpaid to the attacker is a unit deducted pro-rata from all other defaulted-epoch claimants (instant and normal). With a high recovery price (partial default), the stolen amount approaches a full extra `X`, bounded only by the attacker's deposited principal. This breaks the "one receipt, one payout" invariant.

### Likelihood Explanation
Requires: (a) a non-programmable deployment with `disableInstantWithdraw == false` and an APR decrease exceeding `instantWithdrawAprDelta` so requests route to the instant path (the intended public flow), (b) the borrower defaulting in the same epoch the instant request was funded — a real-world scenario, since default risk is precisely highest in the epoch where liquidity was stressed by instant redemptions, and (c) the attacker holding residual fungible receipt tokens, which any lender with two requests (or a queued/normal request alongside the instant one) naturally has. No privileged cooperation is needed; all privileged calls (startEpoch, getInstantWithdrawFunds, stopEpoch, finalizeDefault) are honest. Attacker cost is the capital locked for one epoch plus gas.

### Recommendation
In `claimInstantWithdrawRequest` (and equivalently on the queue's instant claim path), clear the per-epoch ledgers symmetrically with the acquire side — the "release" must decrement everything "acquire" incremented:

```solidity
uint256 currentEpoch = epochNumber; // or the epoch the request was recorded in
instantWithdrawsRequestsByEpoch[_user][currentEpoch] -= amount;
instantWithdrawClaimsByEpoch[currentEpoch] -= amount;
```

Because requests from multiple epochs can coexist in `instantWithdrawsRequests[_user]`, the clearing should iterate/zero the user's per-epoch entries (or track the funded epoch explicitly) rather than blindly subtracting from the current epoch. Additionally, `instantWithdrawClaimsByEpoch[defaultEpoch]` should only count *unfunded, unclaimed* receipts at finalization time, so funded-then-claimed receipts cannot re-enter recovery accounting.

### Proof of Concept
Foundry fork PoC sketch (extend `test/foundry/IdleCreditVault.t.sol` style harness):

```solidity
// Setup: standard IdleCDOEpochVariant, instant withdraws enabled,
// Keyring-allowlisted attacker with tranche tokens.
// 1. Buffer phase of epoch E: stopEpoch sets lower APR
//    (lastEpochApr > unscaledApr + instantWithdrawAprDelta).
// 2. attacker.requestWithdraw(X, AATranche)  -> instant receipt X minted
//    attacker.requestWithdraw(Y, AATranche)  -> normal receipt Y minted, Y >= X
// 3. manager.startEpoch(); warp past instantWithdrawDeadline;
//    manager.getInstantWithdrawFunds();  // borrower approves + funds X
// 4. attacker.claimInstantWithdrawRequest(); // +X underlying, balance = Y
//    assert strategy.instantWithdrawsRequestsByEpoch(attacker, E) == X; // BUG: stale
// 5. warp to epochEndDate; borrower underfunds repayment;
//    manager.stopEpoch(newApr, interest) -> defaulted == true
// 6. manager/owner finalizeDefault(recoveredAmount, recoverySource);
//    strategy.finalizeInstantWithdrawsDefault(); // if separate step exists
// 7. uint256 balPre = underlying.balanceOf(attacker);
//    attacker.claimInstantWithdrawRequest();
//    // expected: reverts or pays 0; actual: pays X * defaultRecoveryPrice / 1e18
//    assertGt(underlying.balanceOf(attacker) - balPre, 0); // stolen recovery
// 8. Compare DefaultDistributor/reserve outflows: other claimants' final
//    claims are short by the stolen amount (insolvency of recovery reserve).
```

Caveat: I was unable to fully read `finalizeDefaultRecovery`/`defaultInstantWithdrawsFinalized` initialization before iteration limits, so the exact gating of step 6–7 (whether a separate instant-default finalization call is required) should be confirmed when writing the PoC. The stale-counter mechanics and the `_burn` bypass via fungible receipts are confirmed in `contracts/strategies/idle/IdleCreditVault.sol:356-393, 842-856`.