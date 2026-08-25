## Analog Vulnerability Found

### Title
Feature-gated stake-math parameter change causes `delegated_stakes` accumulator divergence and validator panic - (File: `runtime/src/stakes.rs`)

### Summary
The FrankenDAO bug stems from re-deriving a "voting power" value from *mutable global parameters* (`baseVotes`, `monsterMultiplier`) at `stake()` time and again at `unstake()` time, and assuming the two derivations are equal so the second can be subtracted from a running total. When the global parameters change between the two calls, the assumption breaks and the code reverts (or corrupts state).

Agave's `Stakes<StakeAccount>::delegated_stakes` accumulator has the same structural pattern: `delegation_effective_stake()` derives a stake amount from a delegation using two feature-gated global parameters — `new_rate_activation_epoch` (`Bank::new_warmup_cooldown_rate_epoch()`) and `use_fixed_point_stake_math` (`Bank::use_fixed_point_stake_math()`) — and this derived value is added to `delegated_stakes` on insert/upsert and subtracted on removal/re-upsert, using whatever the *current* bank's feature values happen to be at each call, not the values used at the original insertion.

### Finding Description
`Stakes::upsert_stake_delegation` and `Stakes::remove_stake_delegation` compute the "stake" contribution of a delegation via `delegation_effective_stake()`: [1](#0-0) 

Both add and remove paths independently recompute `delegation_effective_stake` from the parameters passed in at call time. The subtraction path assumes the recomputed `old_stake` equals whatever value was actually accumulated when the delegation was last added, and enforces this via a panicking assertion instead of tolerating drift: [2](#0-1) 

The parameters `new_rate_activation_epoch` and `use_fixed_point_stake_math` are not fixed per-delegation; they are derived from the *bank's live feature set* at the moment `check_and_store` runs for every stored account, on every processed transaction: [3](#0-2) [4](#0-3) 

`use_fixed_point_stake_math()` flips from `false` to `true` at the exact slot the `upgrade_bpf_stake_program_to_v5_1` feature activates — this is **not** epoch-aligned (unlike `new_warmup_cooldown_rate_epoch`, which is deliberately epoch-quantized specifically to avoid this class of issue). Consequently:

1. In slot N (pre-activation), an ordinary user's stake transaction (e.g. delegate/split/merge/partial withdraw) causes `check_and_store` → `upsert_stake_delegation` to add `effective_stake` to `delegated_stakes[voter]`, computed with the legacy `Delegation::stake()` path (`use_fixed_point_stake_math = false`).
2. In slot N+1 (post-activation, same epoch), another ordinary transaction touches the *same* stake account (any instruction that rewrites the stake account, including routine ones like `Deactivate`, `Withdraw`, `Split`, `Merge`, or even a `SetLockup`/reallocation), retriggering `check_and_store` → `upsert_stake_delegation`, which now recomputes `old_stake` using the new `Delegation::stake_v2()` fixed-point implementation.
3. If `stake_v2()` and the legacy `stake()` produce even a single-lamport-different result for the same delegation state and epoch (the entire motivation for introducing a distinct fixed-point implementation is to change rounding/behavior), `old_stake` no longer matches the value actually present in `delegated_stakes`.
4. `sub_delegated_stake` then executes `current_stake.checked_sub(stake).expect("subtraction value exceeds delegated stake")`, which panics when `old_stake > current_stake`.

This is a bank-processing panic, not a caught `TransactionError` — it is invoked directly from `Bank::store_accounts`/`Bank::update_stakes_cache`, so this crashes the validator process itself.

### Impact Explanation
A panic inside `store_accounts`/`check_and_store` occurs deep in transaction-commit/account-storage bank code, which is executed by every validator replaying the same block. This is not a per-transaction revert — the process crashes/aborts, taking the whole node down. Because the trigger (an ordinary stake instruction landing in the slot immediately following a stake-math feature activation) is deterministic given the same feature-activation slot and account state, **all validators processing that block would hit the same panic**, causing a cluster-wide liveness incident. This matches the accepted "replay-path panic" impact class.

### Likelihood Explanation
This requires no attacker privilege beyond submitting an ordinary stake-program instruction that rewrites a delegation already tracked in `delegated_stakes` right after a stake-math feature flips. Because feature activations are scheduled well in advance and publicly known, and stake accounts are actively managed by many participants (delegate/split/merge/withdraw happen constantly), the required "straddling" transaction is highly likely to occur naturally without any adversarial coordination — an attacker could also proactively submit a trivial stake-account-touching instruction at the activation slot to guarantee the trigger.

### Recommendation
Do not recompute the "old" contribution of a delegation using the *current* feature parameters when reversing a previous accumulation. Either:
- Cache the exact `effective_stake` value that was added for each `(pubkey, delegation)` entry (analogous to the FrankenDAO fix's `tokenVotingPower` mapping) and subtract that cached value on removal/update instead of recomputing it, or
- Replace `.expect()`/`checked_sub().expect()` in `sub_delegated_stake` with saturating arithmetic and re-derive `delegated_stakes` via a full recomputation pass (as already done in `Stakes::activate_epoch`/`refresh_delegated_stakes`) whenever a stake-math-affecting feature transitions, rather than relying on incremental per-transaction add/subtract to remain consistent across a feature-activation boundary.

### Proof of Concept
1. Configure a cluster/bank fixture where `agave_feature_set::upgrade_bpf_stake_program_to_v5_1` activates at slot `N`.
2. At slot `N-1`, submit a stake transaction (e.g. `StakeInstruction::Split`) referencing an existing delegated stake account `S`, causing `check_and_store`→`upsert_stake_delegation` to run with `use_fixed_point_stake_math=false`, adding `effective_stake_legacy(S)` to `delegated_stakes[voter]`.
3. At slot `N` (feature now active), submit any transaction that rewrites account `S` again (e.g. `StakeInstruction::Merge`/`Withdraw`/another `Split`), causing `upsert_stake_delegation` to run with `use_fixed_point_stake_math=true`; it recomputes `old_stake = effective_stake_v2(S)`.
4. If `effective_stake_v2(S) != effective_stake_legacy(S)` for the identical delegation/epoch (expected, since the two implementations exist precisely because they compute differently in edge cases), `sub_delegated_stake` panics via `.expect("subtraction value exceeds delegated stake")`, crashing the bank/validator processing that slot.

### Citations

**File:** runtime/src/stakes.rs (L562-576)
```rust
    fn sub_delegated_stake(&mut self, voter_pubkey: &Pubkey, stake: u64) {
        if stake == 0 {
            return;
        }
        let current_stake = self
            .delegated_stakes
            .get_mut(voter_pubkey)
            .expect("subtraction from missing delegated stake");
        *current_stake = current_stake
            .checked_sub(stake)
            .expect("subtraction value exceeds delegated stake");
        if *current_stake == 0 {
            self.delegated_stakes.remove(voter_pubkey);
        }
    }
```

**File:** runtime/src/stakes.rs (L620-660)
```rust
    fn upsert_stake_delegation(
        &mut self,
        stake_pubkey: Pubkey,
        stake_account: StakeAccount,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        debug_assert_ne!(stake_account.lamports(), 0u64);
        let delegation = stake_account.delegation();
        let voter_pubkey = delegation.voter_pubkey;
        let stake = delegation_effective_stake(
            delegation,
            self.epoch,
            &self.stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        );
        match self.stake_delegations.insert(stake_pubkey, stake_account) {
            None => {
                self.add_delegated_stake(voter_pubkey, stake);
                self.vote_accounts.add_stake(&voter_pubkey, stake);
            }
            Some(old_stake_account) => {
                let old_delegation = old_stake_account.delegation();
                let old_voter_pubkey = old_delegation.voter_pubkey;
                let old_stake = delegation_effective_stake(
                    old_delegation,
                    self.epoch,
                    &self.stake_history,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
                if voter_pubkey != old_voter_pubkey || stake != old_stake {
                    self.sub_delegated_stake(&old_voter_pubkey, old_stake);
                    self.add_delegated_stake(voter_pubkey, stake);
                    self.vote_accounts.sub_stake(&old_voter_pubkey, old_stake);
                    self.vote_accounts.add_stake(&voter_pubkey, stake);
                }
            }
        }
    }
```

**File:** runtime/src/bank.rs (L1711-1721)
```rust
    /// Epoch in which the new cooldown warmup rate for stake was activated
    pub fn new_warmup_cooldown_rate_epoch(&self) -> Option<Epoch> {
        self.feature_set
            .new_warmup_cooldown_rate_epoch(&self.epoch_schedule)
    }

    fn use_fixed_point_stake_math(&self) -> bool {
        self.feature_set
            .snapshot()
            .upgrade_bpf_stake_program_to_v5_1
    }
```

**File:** runtime/src/bank.rs (L4757-4785)
```rust
    pub fn store_accounts<'a>(
        &self,
        accounts: impl StorableAccounts<'a>,
        thread_pool_for_loading_accounts: Option<&ThreadPool>,
    ) {
        assert!(!self.freeze_started());
        let mut m = Measure::start("stakes_cache.check_and_store");
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let use_fixed_point_stake_math = self.use_fixed_point_stake_math();

        (0..accounts.len()).for_each(|i| {
            accounts.account(i, |account| {
                self.stakes_cache.check_and_store(
                    account.pubkey(),
                    &account,
                    new_warmup_cooldown_rate_epoch,
                    use_fixed_point_stake_math,
                )
            })
        });
        self.store_accounts_without_stakes_cache(accounts, thread_pool_for_loading_accounts);
        m.stop();
        self.rc
            .accounts
            .accounts_db
            .stats
            .stakes_cache_check_and_store_us
            .fetch_add(m.as_us(), Relaxed);
    }
```
