### Title
Vote-account withdrawer can redirect an entire epoch's already-earned inflation/block-revenue commission to a new collector with no delay or accrual snapshot - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The Stakehouse bug allowed a privileged role (DAO/LSD owner) to swap out the beneficiary of a smart wallet (the node runner) immediately before settlement, diverting funds that had already economically accrued to the old runner. The Agave analog is `update_commission_collector` (SIMD-0232) in the vote program: the vote account's `authorized_withdrawer` can repoint `inflation_rewards_collector` / `block_revenue_collector` at any time, and the reward-distribution code resolves the commission recipient using whatever collector is set **at distribution time**, not the collector that was in effect while credits/stake were earned during the epoch.

### Finding Description
`update_commission_collector` only requires the current `authorized_withdrawer` to sign, and unconditionally overwrites the collector field with no check on outstanding/pending commission, no epoch boundary restriction, and no snapshot of "who was collector while these credits were earned": [1](#0-0) 

Compare this to `authorize`/`update_commission`, which mutate voter/withdrawer/commission but do not touch already-accrued rewards state, and to `update_commission`'s explicit `is_commission_update_allowed` mid-epoch gate — no equivalent gate exists for the collector swap: [2](#0-1) 

At epoch-reward computation time, `redeem_delegation_rewards` reads the collector **currently** stored in the vote account (`vote_state.inflation_rewards_collector()`) to decide who receives the commission for the entire epoch's earned credits/stake — it does not use any epoch-snapshotted collector value the way it does for `commission_bps` (which has an explicit `delay_commission_updates` path using `snapshot_epoch_vote_accounts`): [3](#0-2) [4](#0-3) 

The commission is then actually credited to whichever `commission_pubkey` was resolved at distribution time: [5](#0-4) 

The existing test suite confirms the field can be swapped freely and directly changes who receives the *already-earned* epoch reward, with no requirement that the collector be the same party who "earned" it throughout the epoch: [6](#0-5) 

### Impact Explanation
The `authorized_withdrawer` of a vote account (an analog of the DAO/LSD owner in the reference finding) can arrange for a third party's arrangement — e.g., a delegated/custom `inflation_rewards_collector` or `block_revenue_collector` that has been receiving commission throughout the epoch based on an agreement with delegators or a staking pool — and then, just before the epoch reward-distribution boundary, submit a single signed `UpdateCommissionCollector` transaction to redirect the collector to an address they control. Because the credits/stake that earned the reward accrued over the whole epoch under the old collector, but the payout is computed and paid using the collector value read at distribution time, the withdrawer can capture the entire epoch's already-earned commission that should have gone to the prior collector. This is a real diversion of already-accrued protocol reward lamports (fund theft), directly analogous to the referenced report's "swap runner right before withdrawal" pattern.

### Likelihood Explanation
This requires only a normal, signed `UpdateCommissionCollector`/vote-authorize-style instruction from the account's existing `authorized_withdrawer` — a role that is otherwise treated as fully trusted for commission changes, but here has no counterpart to the `is_commission_update_allowed` mid-epoch restriction or a "one full epoch delay" analogous to `delay_commission_updates` for `commission_bps`. Any withdrawer who wants to divert third-party-expected rewards (e.g., a staking-pool operator swapping out a validator/collector arrangement) can do this deterministically and immediately before the distribution block, with no on-chain barrier.

### Recommendation
Apply the same delay/snapshot discipline used for `commission_bps` (`delay_commission_updates` / `snapshot_epoch_vote_accounts`) to the commission collector fields: resolve `inflation_rewards_collector` and `block_revenue_collector` from a snapshot taken at the start of the rewarded epoch (or delay effect of `update_commission_collector` by a full epoch), so that commission for credits earned under one collector cannot be redirected to a different collector before distribution.

### Proof of Concept
Conceptual PoC (mirrors the pattern used in the existing test harness for `test_inflation_collector_becomes_vote_account_burns_rewards`):
1. Create a vote account with `authorized_withdrawer = W`, delegate stake to it, and set `inflation_rewards_collector = A` via `UpdateCommissionCollector`, letting it earn credits for a full epoch.
2. Just before the epoch boundary/reward distribution runs (still within the same epoch, no waiting period enforced), have `W` sign another `UpdateCommissionCollector` instruction setting `inflation_rewards_collector = B` (an address controlled by `W`).
3. When `distribute_reward_commissions` / `load_and_reward_commission_accounts` executes for that epoch (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs:1097-1202`, `610-775`), the full epoch's commission lamports are credited to `B`, not `A`, even though `A` was the collector for essentially the entire period the rewards were earned.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L907-933)
```rust
/// Update the vote account's commission collector (SIMD-0232).
pub fn update_commission_collector<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    new_collector: NewCommissionCollector,
    kind: CommissionKind,
    signers: &HashSet<Pubkey, S>,
    rent: &Rent,
) -> Result<(), InstructionError> {
    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    // Require authorized withdrawer to sign.
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    let new_collector_key = new_collector.validate_and_resolve_key(vote_account, rent)?;

    match kind {
        CommissionKind::InflationRewards => {
            vote_state.set_inflation_rewards_collector(new_collector_key);
        }
        CommissionKind::BlockRevenue => {
            vote_state.set_block_revenue_collector(new_collector_key);
        }
    }

    vote_state.set_vote_account_state(vote_account)
}
```

**File:** programs/vote/src/vote_state/mod.rs (L990-1004)
```rust
/// Given the current slot and epoch schedule, determine if a commission change
/// is allowed
pub fn is_commission_update_allowed(slot: Slot, epoch_schedule: &EpochSchedule) -> bool {
    // always allowed during warmup epochs
    if let Some(relative_slot) = slot
        .saturating_sub(epoch_schedule.first_normal_slot)
        .checked_rem(epoch_schedule.slots_per_epoch)
    {
        // allowed up to the midpoint of the epoch
        relative_slot.saturating_mul(2) <= epoch_schedule.slots_per_epoch
    } else {
        // no slots per epoch, just allow it, even though this should never happen
        true
    }
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L703-724)
```rust
        // Fetch the voter commission from past epochs to attempt to
        // delay the effect of commission updates by at least one
        // full epoch.
        // When `commission_rate_in_basis_points` is true, use the new field
        // `inflation_rewards_commission_bps`; otherwise use the legacy
        // percentage field and convert to basis points by multiplying by 100.
        let commission_bps = if delay_commission_updates {
            let vote_state_for_commission = snapshot_epoch_vote_accounts
                .and_then(|eva| eva.get(&vote_pubkey))
                .or_else(|| rewarded_epoch_vote_accounts.and_then(|eva| eva.get(&vote_pubkey)))
                .map(|vote_account| vote_account.vote_state_view())
                .unwrap_or(vote_state);
            if commission_rate_in_basis_points {
                vote_state_for_commission.inflation_rewards_commission()
            } else {
                vote_state_for_commission.commission() as u16 * 100
            }
        } else if commission_rate_in_basis_points {
            vote_state.inflation_rewards_commission()
        } else {
            vote_state.commission() as u16 * 100
        };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L744-768)
```rust
            Ok((stake_reward, commission_lamports, stake)) => {
                let inflation = InflationReward {
                    stake,
                    stake_reward,
                    commission_bps: (!custom_commission_collector).then_some(commission_bps),
                };
                let (commission_pubkey, is_vote_account) = if custom_commission_collector {
                    let commission_pubkey = *vote_state
                        .inflation_rewards_collector()
                        .unwrap_or(&vote_pubkey);
                    (commission_pubkey, commission_pubkey == vote_pubkey)
                } else {
                    (vote_pubkey, true)
                };
                let reward_commission = RewardCommission {
                    commission_bps: (!custom_commission_collector).then_some(commission_bps),
                    commission_lamports,
                    burned_lamports: 0,
                    is_vote_account,
                };
                Some(InflationRewardWithCommission {
                    inflation,
                    commission_pubkey,
                    reward_commission,
                })
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1150-1162)
```rust
                        if *burned_lamports != 0 {
                            total_non_incinerator_burned_lamports
                                .fetch_add(*burned_lamports, Relaxed);
                        }
                        let pre_lamports = commission_account.lamports();
                        if let Err(err) =
                            commission_account.checked_add_lamports(*commission_lamports)
                        {
                            debug!("reward redemption failed for {commission_pubkey}: {err:?}");
                            total_non_incinerator_burned_lamports
                                .fetch_add(*commission_lamports, Relaxed);
                            return None;
                        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L4145-4183)
```rust
        // Transform the collector into a vote account, see that all rewards
        // are burned for this epoch
        let bank = apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch: 2,
                vote_operations: vec![
                    (
                        vote_address,
                        VoteOperations {
                            earned_credits: Some(1000),
                            expect_reward: true,
                            ..VoteOperations::default()
                        },
                    ),
                    (
                        collector_into_vote_address,
                        VoteOperations {
                            create_with_balance: Some(pre_balance),
                            new_commission: Some(100),
                            earned_credits: Some(1000),
                            delegate_stake_amount: Some(LAMPORTS_PER_SOL),
                            ..VoteOperations::default()
                        },
                    ),
                ],
            },
        );

        let vote_reward = bank
            .rewards
            .read()
            .unwrap()
            .iter()
            .find(|(address, _reward)| *address == collector_into_vote_address)
            .map(|(_address, reward)| *reward)
            .unwrap();
        assert_eq!(vote_reward.lamports, 0);
```
