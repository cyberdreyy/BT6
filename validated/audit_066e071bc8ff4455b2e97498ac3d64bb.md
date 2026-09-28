### Title
Post-default withdraw requests are paid 1:1 from the shared `defaultRecoveryReserve` while defaulted-epoch receipts are haircut, draining recovery funds meant for other claimants - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Analogous to the node-tar bug — where a PAX `size` override meant for the next *file* entry is wrongly applied to an intermediary metadata header, desyncing the parser — `IdleCreditVault` applies a **1:1 payout price intended only for already-haircut "post-default" receipts** to claims drawn from the same `defaultRecoveryReserve` that must also pay *defaulted-epoch* receipts at `defaultRecoveryPrice` and implicitly back active tranche holders at the same ratio. Two claim "types" consume one fixed reserve at two different prices, so an unprivileged user who requests a withdrawal after default finalization is overpaid relative to everyone else and can leave earlier (haircutted) claimants' recovery permanently unclaimable.

### Finding Description
In `requestWithdraw` (contracts/strategies/idle/IdleCreditVault.sol:247-257), once `defaultRecoveryFinalized` is set, the strategy burns `_amount` of the CDO's strategy tokens, mints `_amount` receipt tokens to the user, and records `postDefaultRequests[_user] = _amount`. In `_claimPostDefaultWithdrawRequest` (lines 760-767) that receipt is paid `_amount` at full 1:1 via `_transferDefaultRecovery`, which decrements the fixed `defaultRecoveryReserve`.

The reserve was sized in `finalizeDefaultRecovery` (lines 686-692) as `reserveAmount / totalBasis = defaultRecoveryPrice`, i.e. it contains only `totalBasis * recoveryPrice` underlying — enough to pay every finalized-epoch claim at the haircut ratio, not at par. When `defaultRecoveryPrice < 1`:

- A defaulted-epoch claimant receives `claimBasis * defaultRecoveryPrice / 1e18` (lines 772-783).
- A post-default claimant receives the full `_amount` (line 766) from the *same* reserve, even though each post-default burn only "frees" `_amount * defaultRecoveryPrice` worth of active-side basis from that reserve.

Arithmetic: reserve capacity is `(activeBasis + pendingBasis) * price`. If post-default requests approach `activeBasis`, total demand becomes `pendingBasis * price + activeBasis`, which exceeds capacity by `activeBasis * (1 - price)`. Each post-default claim therefore consumes `amount * (1 - price)` more than the basis it released. The excess is stolen from the pool backing defaulted-epoch receipts; once the reserve is exhausted, `defaultRecoveryReserve -= _amount` underflows and remaining claimants' recovery is permanently frozen.

This mirrors the advisory exactly: a value (payout price) scoped to one entry type is applied to a different entry type that shares the same stream cursor (`defaultRecoveryReserve`), desynchronizing accounting between claim classes.

### Impact Explanation
Direct theft plus permanent freezing of unclaimed yield. With `defaultRecoveryPrice = 0.5e18`, a post-default requester receives 2 underlying per unit of basis while a defaulted-epoch claimant receives 0.5; late or honest defaulted claimants are left with an underflowing reserve and can never claim. Loss is bounded by `activeBasis * (1 - defaultRecoveryPrice)` — up to the full unrecovered portion of active NAV.

### Likelihood Explanation
Requires a borrower default that finalizes with `defaultRecoveryPrice < 1e18` (loss already realized and crystallized — no attacker control needed beyond that precondition). The attacker is any unprivileged tranche holder who calls `requestWithdraw` after `finalizeDefaultRecovery`; no privileged action is required, and the existing guard at line 249 only requires claiming old receipts first, which the attacker can do. The `_transferFundedClaim` reserve guard (lines 899-905) does not apply because post-default claims route through `_transferDefaultRecovery`.

Uncertainty: I could not fully verify that `IdleCDOEpochVariant.requestWithdraw` remains reachable while `defaulted` (allowance flags such as `allowAAWithdrawRequest`/`skipDefaultCheck` are set during `_handleBorrowerDefault`, which I did not read). If the CDO blocks post-default requests, this path is unreachable and the finding reduces to the price asymmetry being dead code.

### Recommendation
Apply `defaultRecoveryPrice` when paying post-default claims, or exclude post-default payouts from `defaultRecoveryReserve` and fund them separately (e.g. via `_transferFundedClaim` against newly supplied borrower funds). Equivalently, gate the 1:1 claim in `_claimPostDefaultWithdrawRequest` on the receipt having been priced at par basis added to the reserve — mirroring the upstream fix of not applying an override intended for one entry type to a different entry type.

### Proof of Concept
Foundry fork scenario:

1. KYC'd users deposit into AA/BB; manager `startEpoch()`.
2. User A calls `requestWithdraw` → receipt recorded under `epochNumber` N.
3. Warp past `epochEndDate`; borrower underfunds → `stopEpoch` → `_handleBorrowerDefault`; `finalizeDefaultRecovery(recovered, source)` sets `defaultRecoveryPrice = 0.5e18`.
4. Attacker B (tranche holder) calls `requestWithdraw` post-default → `postDefaultRequests[B] = _amount` (minted at haircut-adjusted virtualPrice).
5. B calls `claimWithdrawRequest` → receives `_amount` at 1:1, consuming reserve worth `2 * _amount * price`.
6. User A calls `claimWithdrawRequest` → `defaultRecoveryReserve -= amount` reverts (underflow) or pays out less than entitled; A's defaulted-epoch recovery is permanently frozen.

Assert: `underlying.balanceOf(B) - balPre == _amount` while `defaultRecoveryPrice == 0.5e18`, and A's subsequent claim reverts on reserve underflow.