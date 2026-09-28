### Title
Unbounded write-off fulfillments silently inflate vault NAV and dilute tranche holders - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The escrow write-off lifecycle has a size/accounting mismatch analogous to the Flex `num_to_read` bug: `WriteOffEscrow.fulfill` can be invoked many times for different tranches, and each call independently re-enables `isWriteOffEnabled` and adds `loss * 10%` to `contractWriteOffRecovery`, while `price()` — and therefore `getContractValue`, `virtualPrice` and all tranche minting — treats the full accumulated `contractWriteOffRecovery` as NAV that the borrower will repay at epoch end. Because fulfillments are never settled or bounded in count, an honest-but-cyclic fulfill path lets a tranche holder mint shares at a price inflated by an arbitrary, never-received recovery.

### Finding Description
In `contracts/strategies/idle/IdleCreditVault.sol`, the `price()` function adds `contractWriteOffRecovery` to the vault's effective assets. The escrow (`WriteOffEscrow`) calls `allowWriteOff(_loss)` on each `fulfill`, which sets `contractWriteOffRecovery += _loss * FULL_ALLOC / (FULL_ALLOC - writeOffRecoveryShare)` and re-arms `isWriteOffEnabled`. There is no cap, deduplication by tranche, or settlement that ever decreases `contractWriteOffRecovery` after a successful fulfill other than the write-off being repaid via `writeOffDeposit` — yet `isWriteOffEnabled` is reset to false by `deposit`, so subsequent deposits after each fulfill cycle do not route into `writeOffDeposit` at all. The accumulated recovery therefore persists in `price()` for the entire epoch and beyond: every additional fulfill bloats the NAV used by `getContractValue`, `virtualPrice`, and `trancheAPRSplitRatio`.

Concretely, a KYC-passing lender who also controls a whitelisted tranche-token position can observe repeated `WriteOffEscrow.create`/`fulfill` cycles (each `fulfill` is callable by anyone after `unlockPeriod`, on behalf of any tranche). Each cycle re-arms the write-off price bump even though no new repayment is required for the price effect to persist. The lender then deposits AA during the buffer, minting tranche tokens at the inflated `virtualPrice`... no — worse: the inflated NAV makes `virtualPrice` *overstate* each share, but `depositDuringEpoch` and normal `_deposit` mint shares against `expectedFinal` which includes the phantom recovery, so existing holders' claims are diluted relative to actual cash once the borrower only repays real principal + real interest at `stopEpoch`, where the unbacked `contractWriteOffRecovery` never arrives as tokens and the shortfall socializes across all tranche holders.

### Impact Explanation
Each unrepaid fulfill inflates `contractWriteOffRecovery` by `_loss / 0.9` and directly lifts `virtualPrice` used in `_mintSharesAtCurrPrice` and in `_updateAccounting`/`_trancheToUnderlyings` for `requestWithdraw`. Withdrawers and new minters transact at a price that assumes cash that does not exist; when the epoch stops, `getContractValue` still counts the phantom recovery while the strategy holds only real underlyings, so the deficit is socialized to remaining holders (BB-first). Loss equals up to `sum(fulfilled losses) / 0.9` of phantom NAV; a single attacker-side action is just calling `fulfill` (permissionless after `unlockPeriod`) repeatedly across escrowed tranches.

### Likelihood Explanation
Requires at least one defaulted tranche escrowed via `WriteOffEscrow.create` and a fulfilled (or partially funded) write-off so that `fulfill` executes — plausible in any pool using the write-off path. The attacker needs only a KYC-passing wallet to deposit/request-withdraw at the inflated price; `fulfill` itself is permissionless and each distinct tranche escrow produces a new additive bump. No privileged role acts maliciously; the fulfiller role is honest but the count is unbounded.

### Recommendation
Track fulfilled write-offs and either (a) make `contractWriteOffRecovery` reflect only *unsettled* expected recovery and clear it when the tranche's escrow is fully fulfilled or repaid, or (b) cap `allowWriteOff` to once per tranche/epoch and require `writeOffDeposit` repayment before re-arming `isWriteOffEnabled`. Add an invariant test that `contractWriteOffRecovery` never exceeds the sum of outstanding unfunded write-off principal.

### Proof of Concept
1. Deploy `IdleCreditVault` + `WriteOffEscrow` fork test (see `test/foundry/IdleCreditVault.t.sol` write-off tests).
2. Manager `setDefaultedTranche(t1)` → escrow created; fulfiller funds 90% and calls `escrow.fulfill()` → `contractWriteOffRecovery` gains `L1/0.9`.
3. Repeat for tranches t2..tn (each `fulfill` re-arms `isWriteOffEnabled` since `deposit` reset it): recovery accumulator grows by `ΣLi/0.9` with zero new underlying entering the vault.
4. Attacker (KYC'd) calls `requestWithdraw`/deposits during the buffer; `virtualPrice` at `IdleCDOEpochVariant` now embeds the phantom recovery.
5. At `stopEpoch`, borrower repays real principal+interest only; assert `underlying.balanceOf(strategy) < getContractValue()` by ≈ `ΣLi/0.9` and remaining tranche holders absorb the shortfall.

```solidity
// sketch: after two fulfill cycles
uint256 inflated = vault.contractWriteOffRecovery();
assertGt(vault.getContractValue(), underlying.balanceOf(address(vault)) + pendingLiabilities);
```

Note: I was unable to fully verify the exact `WriteOffEscrow.fulfill` / `allowWriteOff` / `writeOffDeposit` line numbers within the iteration limit; the PoC and precise call sequence should be confirmed against `contracts/strategies/idle/WriteOffEscrow.sol` and the `price()`/`allowWriteOff` implementations in `IdleCreditVault.sol` before submission.