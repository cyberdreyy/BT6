### Title
Bounded `epoch_credits` history (`MAX_EPOCH_CREDITS_HISTORY`) permanently discards stake rewards for delegations that miss more than 64 epochs of reward processing - (File: `programs/vote/src/vote_state/handler.rs`, `runtime/src/inflation_rewards/points.rs`)

### Summary
The vote program's `epoch_credits` list is capped to `MAX_EPOCH_CREDITS_HISTORY` (64) entries, with older entries evicted on every `increment_credits()` call. Stake reward calculation (`calc_earned_credits`/`tower_epoch_credits_iter`) relies entirely on this bounded history to determine how many credits a delegation earned since its `credits_observed` checkpoint. If a stake's reward processing is skipped for more consecutive epochs than the vote account's retained history window (because the vote account temporarily falls out of the reward-eligible/distribution set), the comparison logic in `calc_earned_credits` treats the gap as "already observed," silently discarding all credits/rewards earned in the pruned epochs — an analog of the Olas `VoteWeighting` 53-week lookbehind bug (M-02), where nominee/voter data outside a fixed lookback window is unrecoverable.

### Finding Description
`VoteStateHandler::increment_credits` (and the migration variant in `runtime/src/block_component_processor/vote_reward.rs::increment_credits`) prunes the `epoch_credits` vector down to `MAX_EPOCH_CREDITS_HISTORY` entries every time new credits are recorded: [1](#0-0) 

During epoch-boundary reward calculation, `calculate_stake_points_and_credits` decides whether a delegation earned points by comparing `stake.credits_observed` against the vote account's `credits`: [2](#0-1) 

For the "Tower" path it walks the *currently retained* `epoch_credits_iter` via `tower_epoch_credits_iter`, delegating the actual per-entry accounting to `calc_earned_credits`: [3](#0-2) [4](#0-3) 

`calc_earned_credits` only ever sees the entries still present in `vote_state.epoch_credits` (at most 64). If `stake.credits_observed` is older than the *oldest retained* entry's `initial_epoch_credits`, the function assumes "the staker observed the entire epoch" for that first entry and awards only `final_epoch_credits - initial_epoch_credits` for it — it has no way to know about (and therefore cannot pay for) any credits that were earned and then pruned away *before* that oldest retained entry. This exactly mirrors the Olas bug: a bounded lookback structure (`epoch_credits`, capped at `MAX_EPOCH_CREDITS_HISTORY`) is used as the sole source of truth for "how much reward is owed since the last checkpoint," and any owed amount older than the window is unrecoverable once the window has rolled past it.

The gap that triggers this is created upstream in `redeem_delegation_rewards`, which explicitly skips advancing `credits_observed` when the delegated vote account is not present in that epoch's `distribution_epoch_vote_accounts` snapshot (e.g., when a validator's vote account temporarily drops out of the VAT-filtered/eligible distribution set): [5](#0-4) 

Every epoch that the vote account is excluded from this set, `credits_observed` is left frozen while the vote account's real `epoch_credits` (and its 64-entry retention window) continues to advance/prune in the background. Once the vote account re-enters the eligible set and reward calculation resumes for the delegation, if the exclusion lasted longer than 64 epochs, the pruned history means the stake permanently loses credit/reward for every epoch prior to the oldest entry still retained at that time.

### Impact Explanation
This causes silent, permanent loss of legitimately earned inflation staking rewards for delegators whose vote account experiences a sufficiently long gap (>64 epochs, i.e., roughly the same order of magnitude as Curve's 4-year vs 1-year mismatch that motivated the original finding) in reward-eligible processing. No error, revert, or visible signal is produced — the stake account's `credits_observed` is simply advanced past the missing region, and the corresponding rewards are never minted/paid to the staker or the validator's commission collector. This constitutes stake/reward accounting corruption (economic loss for the affected party) matching the accepted analog category.

### Likelihood Explanation
Likelihood is moderate: the triggering precondition (a vote account being excluded from `distribution_epoch_vote_accounts`/the VAT-filtered set for more than `MAX_EPOCH_CREDITS_HISTORY` (64) consecutive epochs while continuing to accrue vote credits, then re-entering) is a real, reachable condition for validators/vote accounts near admission thresholds, but is a narrower and less commonly hit condition than the Curve/Olas case (which is triggered by simple end-user inactivity). No malicious transaction is strictly required — any staker delegated to such a vote account is passively exposed.

### Recommendation
Either extend `MAX_EPOCH_CREDITS_HISTORY` to safely cover the maximum plausible gap between reward-processing passes for a delegation (analogous to the Olas fix of increasing `MAX_NUM_WEEKS`), or change `redeem_delegation_rewards`/`calculate_stake_points_and_credits` so that a stake whose `credits_observed` predates the oldest retained `epoch_credits` entry is flagged and handled explicitly (e.g., credited based on the total accumulated `credits()` delta rather than only the entries still present), rather than silently truncating the reward calculation to the retained window.

### Proof of Concept
1. Delegate stake to vote account `V` at epoch `E0`; `credits_observed` is set to `V`'s current total credits.
2. Cause `V` to be excluded from `distribution_epoch_vote_accounts` (e.g., via `clone_and_filter_for_vat` dropping it from the top-N Alpenglow distribution set) for more than `MAX_EPOCH_CREDITS_HISTORY` (64) consecutive epochs, while `V` continues to vote and accrue `epoch_credits` normally (pruning its own history each epoch via `increment_credits`).
3. During this window, `redeem_delegation_rewards` returns `None` for the delegation every epoch (vote account not found), so `stake.credits_observed` is never advanced.
4. Once `V` re-enters the distribution set, `calculate_stake_points_and_credits`/`tower_epoch_credits_iter`/`calc_earned_credits` compute earned credits only from the entries still retained in `V.epoch_credits` (at most 64 entries) — all credits earned by `V` before the oldest retained entry are unrecoverable, so the staker's rewards for those epochs are permanently lost with no error surfaced.

### Citations

**File:** programs/vote/src/vote_state/handler.rs (L425-447)
```rust
    pub fn increment_credits(&mut self, epoch: Epoch, credits: u64) {
        // increment credits, record by epoch

        // never seen a credit
        if self.epoch_credits().is_empty() {
            self.epoch_credits_mut().push((epoch, 0, 0));
        } else if epoch != self.epoch_credits().last().unwrap().0 {
            let (_, credits, prev_credits) = *self.epoch_credits().last().unwrap();

            if credits != prev_credits {
                // if credits were earned previous epoch
                // append entry at end of list for the new epoch
                self.epoch_credits_mut().push((epoch, credits, credits));
            } else {
                // else just move the current epoch
                self.epoch_credits_mut().last_mut().unwrap().0 = epoch;
            }

            // Remove too old epoch_credits
            if self.epoch_credits().len() > MAX_EPOCH_CREDITS_HISTORY {
                self.epoch_credits_mut().remove(0);
            }
        }
```

**File:** runtime/src/inflation_rewards/points.rs (L158-181)
```rust
fn calc_earned_credits(
    stake: &Stake,
    final_epoch_credits: u64,
    initial_epoch_credits: u64,
    new_credits_observed: &mut u64,
) -> u128 {
    let credits_in_stake = stake.credits_observed;

    // figure out how much this stake has seen that
    //   for which the vote account has a record
    let earned_credits = if credits_in_stake < initial_epoch_credits {
        // the staker observed the entire epoch
        final_epoch_credits - initial_epoch_credits
    } else if credits_in_stake < final_epoch_credits {
        // the staker registered sometime during the epoch, partial credit
        final_epoch_credits - *new_credits_observed
    } else {
        // the staker has already observed or been redeemed this epoch
        //  or was activated after this epoch
        0
    };
    *new_credits_observed = (*new_credits_observed).max(final_epoch_credits);
    u128::from(earned_credits)
}
```

**File:** runtime/src/inflation_rewards/points.rs (L187-234)
```rust
fn tower_epoch_credits_iter(
    stake: &Stake,
    epoch_credits_iter: impl Iterator<Item = (Epoch, u64, u64)>,
    stake_history: &StakeHistory,
    inflation_point_calc_tracer: Option<impl Fn(&InflationPointCalculationEvent)>,
    new_rate_activation_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> (u128, u64, bool) {
    let mut points = 0;
    let credits_in_stake = stake.credits_observed;
    let mut new_credits_observed = credits_in_stake;
    let mut saw_marker = false;

    for entry in epoch_credits_iter {
        if entry == AG_MIGRATION_EPOCH_CREDIT {
            saw_marker = true;
            break;
        }
        let (epoch, final_epoch_credits, initial_epoch_credits) = entry;
        let earned_credits = calc_earned_credits(
            stake,
            final_epoch_credits,
            initial_epoch_credits,
            &mut new_credits_observed,
        );
        let stake_amount = u128::from(delegation_effective_stake(
            &stake.delegation,
            epoch,
            stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        ));

        // finally calculate points for this epoch
        let earned_points = stake_amount * earned_credits;
        points += earned_points;

        if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
            inflation_point_calc_tracer(&InflationPointCalculationEvent::CalculatedPoints(
                epoch,
                stake_amount,
                earned_credits,
                earned_points,
            ));
        }
    }
    (points, new_credits_observed, saw_marker)
}
```

**File:** runtime/src/inflation_rewards/points.rs (L357-412)
```rust
pub(crate) fn calculate_stake_points_and_credits(
    stake: &Stake,
    vote_state: DelegatedVoteState,
    stake_history: &StakeHistory,
    inflation_point_calc_tracer: Option<impl Fn(&InflationPointCalculationEvent)>,
    new_rate_activation_epoch: Option<Epoch>,
    ag_epoch_type: &AlpenglowEpochType,
    use_fixed_point_stake_math: bool,
) -> CalculatedStakePoints {
    let credits_in_stake = stake.credits_observed;
    let credits_in_vote = vote_state.credits;
    // if there is no newer credits since observed, return no point
    match credits_in_vote.cmp(&credits_in_stake) {
        Ordering::Less => {
            if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
                inflation_point_calc_tracer(&SkippedReason::ZeroCreditsAndReturnRewound.into());
            }
            // Don't adjust stake.activation_epoch for simplicity:
            //  - generally fast-forwarding stake.activation_epoch forcibly (for
            //    artificial re-activation with re-warm-up) skews the stake
            //    history sysvar. And properly handling all the cases
            //    regarding deactivation epoch/warm-up/cool-down without
            //    introducing incentive skew is hard.
            //  - Conceptually, it should be acceptable for the staked SOLs at
            //    the recreated vote to receive rewards again immediately after
            //    rewind even if it looks like instant activation. That's
            //    because it must have passed the required warmed-up at least
            //    once in the past already
            //  - Also such a stake account remains to be a part of overall
            //    effective stake calculation even while the vote account is
            //    missing for (indefinite) time or remains to be pre-remove
            //    credits score. It should be treated equally to staking with
            //    delinquent validator with no differentiation.

            // hint with true to indicate some exceptional credits handling is needed
            return CalculatedStakePoints {
                tower_points: 0,
                ag_points: 0,
                new_credits_observed: credits_in_vote,
                force_credits_update_with_skipped_reward: true,
            };
        }
        Ordering::Equal => {
            if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
                inflation_point_calc_tracer(&SkippedReason::ZeroCreditsAndReturnCurrent.into());
            }
            // don't hint caller and return current value if credits remain unchanged (= delinquent)
            return CalculatedStakePoints {
                tower_points: 0,
                ag_points: 0,
                new_credits_observed: credits_in_stake,
                force_credits_update_with_skipped_reward: false,
            };
        }
        Ordering::Greater => {}
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L650-700)
```rust

        let Some(vote_account) = distribution_epoch_vote_accounts.get(&vote_pubkey) else {
            debug!("could not find vote account {vote_pubkey} in cache");
            // Even if the vote account doesn't exist, there might still be a
            // need to adjust the stake delegation
            if adjust_delegations_for_rent {
                let status = delegation_activation_status(
                    &stake.delegation,
                    rewarded_epoch,
                    stake_history,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
                if delegation_may_need_adjustment(
                    stake.delegation.stake,
                    stake.delegation.stake,
                    current_lamports,
                    minimum_lamports,
                    status,
                ) {
                    debug!(
                        "delegation for stake {stake_pubkey} may be adjusted at distribution, \
                         unless lamports are transferred before distribution block"
                    );
                    let inflation = InflationReward {
                        stake,
                        stake_reward: 0,
                        commission_bps: (!custom_commission_collector).then_some(0),
                    };
                    // Set `is_vote_account` to `false` in order to deliberately
                    // fail during commission collector checks. This avoids
                    // creating a reward entry during payout.
                    let reward_commission = RewardCommission {
                        commission_bps: (!custom_commission_collector).then_some(0),
                        commission_lamports: 0,
                        burned_lamports: 0,
                        is_vote_account: false,
                    };
                    return Some(InflationRewardWithCommission {
                        inflation,
                        commission_pubkey: vote_pubkey,
                        reward_commission,
                    });
                } else {
                    debug!("delegation for stake {stake_pubkey} will not be adjusted");
                    return None;
                }
            } else {
                return None;
            }
        };
```
