### Title
Withdraw receipts requested before the first epoch are treated as "closed-pool" receipts and can be claimed instantly at a principal-plus-interest price — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`IdleCreditVault.requestWithdraw` and `_claimFundedWithdrawRequest` both decode `epochEndDate() == 0` as "the pool was successfully closed and all funds recalled". That sentinel is indistinguishable from the initial state "no epoch has ever started", which is the direct analog of the ReactPHP bug: an attacker-supplied value decodes to a privileged/security marker. As a result, a withdraw receipt created before the first `startEpoch` is (a) never added to `pendingWithdraws`, and (b) claimable immediately, bypassing the mandatory one-epoch wait and paying out full epoch interest that was never earned.

### Finding Description
Two code paths key off the same sentinel:

- In `requestWithdraw` (`contracts/strategies/idle/IdleCreditVault.sol:259,277`), `isClosed = IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0`. When `isClosed` is true, the function still burns the CDO's strategy tokens and mints the user a receipt for `_amount` (which the CDO computes as principal **plus** one-epoch interest net of fees via `_calcInterestWithdrawRequest`), but skips `pendingWithdraws += _amount` under the assumption the vault already recalled all borrower funds and no future `stopEpoch` will fund receipts. Before the first epoch this assumption is false — deposits are sitting in the strategy and a `startEpoch` will still run.
- In `_claimFundedWithdrawRequest` (`contracts/strategies/idle/IdleCreditVault.sol:326`), the one-epoch-wait guard `epochNumber <= lastWithdrawRequest[_user]` is only enforced when `epochEndDate() != 0`. Pre-first-epoch, `epochEndDate == 0` (and `epochNumber == lastWithdrawRequest[user] == 0`), so the guard is skipped entirely and `_transferFundedClaim` pays the receipt instantly.

Sequence (buffer phase before any epoch, standard fixed-APR mode):

1. Honest lenders deposit into AA/BB tranches; funds sit in the strategy as `totEpochDeposits`.
2. Attacker (KYC'd lender) deposits `D` underlying via `IdleCDOEpochVariant._deposit` (`isWalletAllowed` passes).
3. Attacker calls `requestWithdraw`. The CDO passes `_amount = D + interestForOneEpoch(D) - fees`; `requestWithdraw` mints that receipt to the attacker but, because `epochEndDate() == 0`, does **not** add it to `pendingWithdraws`.
4. Attacker immediately calls `claimWithdrawRequest` → `_claimFundedWithdrawRequest` burns the receipt and transfers `D + interest` underlying from the strategy's balance.

The attacker exits with principal plus a full epoch of interest after zero exposure, paid out of the pooled deposits of honest lenders.

### Impact Explanation
Direct theft of unearned yield. Each wei of the attacker's receipt above `D` is backed by other depositors' principal, since the borrower has not yet received or repaid anything. With e.g. a 10% scaled APR, an attacker cycling large deposits into pre-first-epoch withdraw requests extracts ~10%/epoch-equivalent of underlying from the vault on a capital base of arbitrary size (repeatable across fresh pools, or amplified by depositing right before the first `startEpoch`). Additionally, because `pendingWithdraws` was never incremented, the stolen receipt is invisible to `startEpoch`'s funding math: the vault begins epoch 1 already insolvent by the stolen interest amount, which surfaces as a socialized loss to remaining LPs at the first `stopEpoch`.

### Likelihood Explanation
Requires only a KYC-passing lender acting between vault deployment (or between `stopEpoch` closing the pool — but there funds are genuinely recalled, which is the intended case) and the first `startEpoch`. The vulnerable window is exactly the initial buffer period, which for newly deployed vaults is the normal operating state. No privileged cooperation, no oracle manipulation, no timing race — a single deposit → requestWithdraw → claimWithdrawRequest sequence. Guards that do not stop it: `allowAAWithdrawRequest`/`allowBBWithdrawRequest` are true in the buffer, `_skimDonatedAssets` is irrelevant (these are accounted funds), and `isWalletAllowed` only requires KYC. One caveat: if the owner always starts epoch 1 before opening deposits, the window may be small in practice, but the code permits the sequence whenever deposits are open pre-first-epoch.

### Recommendation
Distinguish "never started" from "successfully closed". Options:

- In `requestWithdraw`, only treat `epochEndDate() == 0` as closed when the pool actually ran (e.g. `epochNumber > 0 || defaulted`), otherwise follow the normal `pendingWithdraws += _amount` path so `startEpoch` funds the receipt.
- In `_claimFundedWithdrawRequest`, enforce the epoch-wait guard whenever the pool is not in the true closed state (same discriminator), so pre-first-epoch receipts still wait one epoch.
- Alternatively, initialize `epochEndDate` to a non-zero sentinel at `initialize` and reserve `0` strictly for the post-close state, mirroring the upstream fix of rejecting ambiguous encodings.

### Proof of Concept
Foundry fork PoC sketch (run against the repo's existing fork setup, e.g. `test/foundry/`):

```solidity
// Setup: deploy IdleCDOEpochVariant + IdleCreditVault via factory with
// keyring whitelist allowing attacker; underlying = USDC; apr = 10% (scaled).
// epochDuration set, NO startEpoch has ever run.

address attacker = kycUser;
uint256 D = 1_000_000e6;

deal(address(underlying), attacker, D);
vm.startPrank(attacker);
underlying.approve(address(cdo), D);
cdo.depositAA(D);                       // funds land in strategy as totEpochDeposits

// Request withdraw before first epoch: receipt includes one-epoch interest
cdo.requestWithdraw(AA, cdo.balanceOfAA(attacker)); // amount minted = D + interest - fees

// Claim immediately: epochEndDate()==0 skips the epoch-wait revert
uint256 balBefore = underlying.balanceOf(attacker);
cdo.claimWithdrawRequest();
uint256 gained = underlying.balanceOf(attacker) - balBefore;

assertGt(gained, D);                    // attacker exits with principal + unearned interest
vm.stopPrank();

// The strategy's underlying balance is now short by (gained - D):
// pendingWithdraws == 0, so startEpoch sends borrower TVL as if the
// theft never happened — loss is socialized to remaining LPs at stopEpoch.
```

Key assertions: `strategy.pendingWithdraws() == 0` after the request despite a live receipt, and `gained > D` proves unearned interest extraction funded by honest depositors.