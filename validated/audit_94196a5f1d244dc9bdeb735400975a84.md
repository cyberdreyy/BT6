### Title
Frontrunning deposit shifts `trancheAPRSplitRatio` to steal interest from same-block `requestWithdraw` receipts — (File: contracts/IdleCDOEpochVariant.sol)

### Summary
In `IdleCDOEpochVariant`, the amount of underlying locked into a withdrawal receipt at `requestWithdraw` time depends on the live `trancheAPRSplitRatio` and tranche NAVs, which are updated by any deposit in the same block (`_deposit` → `_updateSplitRatio(_getAARatio(true))`). An unprivileged KYC-passed lender can frontrun a victim's `requestWithdraw` with a large BB deposit, collapsing the AA interest share used by `_calcInterestWithdrawRequest`, so the victim's receipt — fixed forever at request time — is minted for materially less underlying. The diverted interest accrues to the BB tranche, where the attacker's fresh deposit makes them the dominant holder, so the yield is captured rather than burned.

### Finding Description
During the buffer phase (after `stopEpoch` succeeds, before `startEpoch`), both deposits and withdrawal requests are enabled simultaneously: `stopEpoch` unpauses the contract and sets `allowAAWithdrawRequest`/`allowBBWithdrawRequest` to `true` at `contracts/IdleCDOEpochVariant.sol:480-482`, and `_deposit` only requires `whenNotPaused` plus `isWalletAllowed` (`contracts/IdleCDOEpochVariant.sol:644-649`, `contracts/IdleCDOCreditVault.sol:191-212`).

`requestWithdraw` prices the receipt off current state (`contracts/IdleCDOEpochVariant.sol:739-790`):

- `_underlyings = principal + interest - totalFees` where `interest` comes from `_calcInterestWithdrawRequest`.
- `_calcInterestWithdrawRequest` computes `totTrancheInterest = totInterest * trancheAPRSplitRatio / FULL_ALLOC` for AA, then `_interest = _amount * totTrancheInterest / _trancheBal` (`contracts/IdleCDOEpochVariant.sol:856-886`).
- Any preceding deposit in the same block rewrites `trancheAPRSplitRatio` via `_updateSplitRatio(_getAARatio(true))` (`contracts/IdleCDOCreditVault.sol:208`, `386-400`). With `isAYSActive`, a sufficiently large BB deposit pushes `tvlAARatio` below `minAprSplitAYS`/`AA_RATIO_LIM_DOWN`, driving `trancheAPRSplitRatio ≈ _minSplit * tvlAARatio / FULL_ALLOC → ~0`.

So the victim's AA interest component collapses toward zero, while the corresponding `(FULL_ALLOC - ratio)` share of total epoch interest is redirected to the BB tranche, which the attacker now dominates. There is no max-fee/min-received parameter on `requestWithdraw` — the same missing slippage protection as the external report — and the existing same-block guard (`_checkSameBlock` in `IdleCDO._withdraw`, `contracts/IdleCDO.sol:479`) only restricts a single caller, not sequencing between different users. `_skimDonatedAssets` does not apply because the manipulation uses a legitimate deposit, not a donation.

### Impact Explanation
Direct theft of yield with a quantified loss. The victim's `creditVault.requestWithdraw(_underlyings, ...)` receipt is fixed at request time (`contracts/IdleCDOEpochVariant.sol:776-788`), so the shortfall is permanent: with, e.g., 10% APR, 50/50 AA/BB pool, and an attacker depositing ~19× TVL into BB, the AA split ratio falls from ~25% to ~1.25%, cutting the victim's receipted interest by ~95%. The displaced interest is socialized to BB holders where the attacker holds ~95% share — the attacker recovers most of the stolen interest at epoch end and can exit via a normal `requestWithdraw` in the next buffer (capital cost is time-locking, not loss). Loss scales linearly with victim size and pool APR.

### Likelihood Explanation
Requires `isAYSActive` (so the split ratio floats with TVL) and a non-programmable, non-minted epoch pool — a supported configuration (`isAYSActive` is owner-settable and AYS is the flagship mechanism in the base `IdleCDO`). The attacker needs only to pass Keyring KYC (explicitly an allowed attacker profile) and have capital; no privileged role, no oracle, no default timing. The window is the entire buffer period, which is days long, making same-block or same-phase sequencing trivial. Inflation attack cost is bounded by the clamp only in the ratio's lower bound, and the attacker's deposit itself earns BB yield, partially subsidizing the attack.

### Recommendation
Add user-specified slippage protection to `requestWithdraw` (e.g., `minUnderlyings` parameter reverting if `_underlyings < minUnderlyings`), analogous to the "max fee" fix suggested in the external report. Alternatively, compute the receipt from a snapshot taken at `stopEpoch` (frozen `trancheAPRSplitRatio`/NAV per epoch) rather than live state, or disallow ratio updates between `stopEpoch` and `startEpoch` by snapshotting `_getAARatio` once per epoch boundary.

### Proof of Concept
Foundry fork PoC (buffer phase, fixed-APR + AYS active):

```solidity
// Setup: pool with AA and BB deposits, isAYSActive = true, fee > 0
// Epoch 0 stopped; buffer period active; allowAAWithdrawRequest == true

address victim = makeAddr('victim');   // holds AA tranche tokens
address attacker = makeAddr('attacker'); // KYC-passed lender

// Baseline: victim receipt with no manipulation
uint256 expectedReceipt = previewRequestWithdraw(victimAABalance);

// ATTACK (same block, before victim tx):
uint256 tvl = cdoEpoch.getContractValue();
uint256 bbDeposit = tvl * 20; // push tvlAARatio below AA_RATIO_LIM_DOWN
deal(underlying, attacker, bbDeposit);
vm.startPrank(attacker);
IERC20(underlying).approve(address(idleCDO), bbDeposit);
idleCDO.depositBB(bbDeposit); // _updateSplitRatio drives trancheAPRSplitRatio ~0
vm.stopPrank();

vm.prank(victim);
uint256 victimReceipt = cdoEpoch.requestWithdraw(0, address(AAtranche));

// Victim receipt lost ~all AA interest share
assertLt(victimReceipt, expectedReceipt * X / 100);

// After next epoch, attacker (dominant BB holder) claims the redirected interest
// via requestWithdraw/claimWithdrawRequest, netting > the interest the victim lost
// minus dilution to pre-existing BB holders.
```

Steps: deposit → `startEpoch` → warp → `stopEpoch` → attacker `depositBB` → victim `requestWithdraw` → compare `_underlyings` against a requestWithdraw executed in a clean block; then `startEpoch`/`stopEpoch` and measure attacker's BB claim to show net profit from the redirected `totTrancheInterest` share.