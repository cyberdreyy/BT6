### Title
Live network-stake denominator lets non-voting validators' unstaking lower the 2/3 transfer-unlock quorum, releasing lockup tokens early - (File: voting/src/lib.rs)

### Summary
`voting/src/lib.rs`'s `VotingContract` decides whether to permanently enable NEAR transfers by comparing `total_voted_stake` against a threshold computed live from `env::validator_total_stake()`. Because this denominator is recomputed at every `ping()`/`vote()` call from the current global validator stake set — not fixed at any point analogous to "proposal creation" — a coalition can lower the pass threshold mid-poll by unstaking, without needing to increase actual "yes" votes. Once the poll result is set, `lockup/src/owner_callbacks.rs`'s `on_get_result_from_transfer_poll` permanently flips `TransfersInformation` to `TransfersEnabled`, which (`lockup/src/getters.rs::get_locked_amount`) starts the lockup/vesting release schedule — i.e., an artificially-passed vote directly triggers early release of locked/unvested NEAR.

### Finding Description
The equality that should hold is: **quorum-crossing "yes" stake ÷ true, stable total network stake ≥ 2/3** at the moment the community intends the decision to be final. Instead, `check_result` uses a live total: [1](#0-0) 

and `ping()` recomputes `total_voted_stake` only from the stake of accounts that are *currently recorded voters*, while `total_stake = env::validator_total_stake()` reflects the stake of the *entire* validator set, including non-voters: [2](#0-1) 

If validators who have not voted (or the "no" side) reduce their stake by unstaking — a routine, permissionless action any validator/delegator can take through the staking-pool contracts — `total_stake` (the denominator) shrinks while `total_voted_stake` (the numerator, held by a coordinated "yes" minority) stays the same or shrinks less. The 2/3 threshold is thus lowered mid-poll, and `check_result` can flip `result` to `Some(...)` purely due to third-party stake exiting, not due to any increase in genuine support: [3](#0-2) 

Once `result` is set it is permanent — "After the voting is finished, no one can further modify the contract" — per the contract's own documented intent: [4](#0-3) 

This result is consumed by every deployed `LockupContract` via `check_transfers_vote` → `on_get_result_from_transfer_poll`, which irreversibly sets `TransfersEnabled` once `poll_result` is `Some`: [5](#0-4) [6](#0-5) 

That flag is what starts the lockup/release-schedule clock in `get_locked_amount`, so a premature/manipulated `Some` result advances `lockup_timestamp` and begins unlocking tokens that should still be locked: [7](#0-6) 

### Impact Explanation
This matches the Critical category "locked or unvested tokens released early." Every deployed lockup contract that is still in `TransfersDisabled` state at the time the poll flips would have its lockup/vesting schedule start ticking prematurely, allowing owners to withdraw/transfer NEAR that was meant to remain locked, and this is a network-wide, one-time, irreversible state change (the poll result can never be un-set).

### Likelihood Explanation
Exploitation requires a coordinated set of validators who already hold a large enough "yes" position and can induce or wait for a reduction in the network's total validator stake (via ordinary unstaking by non-participating validators/delegators), which is directly analogous to the reported attacker capability set ("multiple coordinating token holders," "patience to wait," "sufficient initial voting power"). This is economically costly (requires real staked NEAR) but requires no privileged role — no owner, foundation, or multisig involvement — only ordinary permissionless staking/unstaking actions available to any NEAR holder through the staking-pool contracts. I could not fully verify from the index whether `env::validator_total_stake()` in the historical protocol excludes stake that is in the process of unstaking within the same epoch (this affects exact timing), so the precise minimum coordination size and epoch timing needed for a real-world attack remain uncertain without deeper protocol-level analysis.

### Recommendation
Snapshot the quorum denominator (or the full validator stake distribution) at poll creation / first `ping()`, and freeze it thereafter for the life of the poll, rather than recomputing `env::validator_total_stake()` live on every check. Alternatively, require the 2/3 threshold to hold against a fixed baseline total stake that cannot be reduced by third-party unstaking during an active poll, mirroring the "freeze quorum at proposal creation" recommendation from the reference report.

### Proof of Concept
Conceptual sequence (mirroring `voting/src/lib.rs` tests such as `test_validator_stake_change`/`test_validator_kick_out`, which already show `total_stake`/`total_voted_stake` change with live validator set membership): [8](#0-7) 
1. Validators A, B (colluding, "yes") each stake X; validators C, D, E (uninvolved) each stake X, giving `validator_total_stake = 5X`. Quorum to pass: `2/3 * 5X ≈ 3.33X`.
2. A and B call `vote(true)`, contributing `total_voted_stake = 2X` — below quorum, poll stays open (`test_voting_simple` demonstrates this exact never-reached-quorum state at similar ratios).
3. C, D, E unstake to near zero via the staking pool (`staking-pool/src/lib.rs::unstake`), and the next `ping()`/`vote()` epoch transition recomputes `validator_total_stake ≈ 2X` (as shown by `test_validator_kick_out`, where removed validators cause `get_total_voted_stake` to reset/shrink on `ping`): [9](#0-8) 
4. New quorum: `2/3 * 2X ≈ 1.33X`, which `total_voted_stake = 2X` now exceeds, and `check_result` sets `result = Some(...)` — the poll passes solely because of third-party stake withdrawal, not increased "yes" support.
5. Any `LockupContract` calling `check_transfers_vote()` now observes `Some(timestamp)` and permanently transitions to `TransfersEnabled`, starting the lockup/vesting clock early per `get_locked_amount`.

### Citations

**File:** voting/src/lib.rs (L45-61)
```rust
    /// Ping to update the votes according to current stake of validators.
    pub fn ping(&mut self) {
        assert!(self.result.is_none(), "Voting has already ended");
        let cur_epoch_height = env::epoch_height();
        if cur_epoch_height != self.last_epoch_height {
            let votes = std::mem::take(&mut self.votes);
            self.total_voted_stake = 0;
            for (account_id, _) in votes {
                let account_current_stake = env::validator_stake(&account_id);
                self.total_voted_stake += account_current_stake;
                if account_current_stake > 0 {
                    self.votes.insert(account_id, account_current_stake);
                }
            }
            self.check_result();
            self.last_epoch_height = cur_epoch_height;
        }
```

**File:** voting/src/lib.rs (L64-74)
```rust
    /// Check whether the voting has ended.
    fn check_result(&mut self) {
        assert!(
            self.result.is_none(),
            "check result is called after result is already set"
        );
        let total_stake = env::validator_total_stake();
        if self.total_voted_stake > 2 * total_stake / 3 {
            self.result = Some(U64::from(env::block_timestamp()));
        }
    }
```

**File:** voting/src/lib.rs (L76-103)
```rust
    /// Method for validators to vote or withdraw the vote.
    /// Votes for if `is_vote` is true, or withdraws the vote if `is_vote` is false.
    pub fn vote(&mut self, is_vote: bool) {
        self.ping();
        if self.result.is_some() {
            return;
        }
        let account_id = env::predecessor_account_id();
        let account_stake = if is_vote {
            let stake = env::validator_stake(&account_id);
            assert!(stake > 0, "{} is not a validator", account_id);
            stake
        } else {
            0
        };
        let voted_stake = self.votes.remove(&account_id).unwrap_or_default();
        assert!(
            voted_stake <= self.total_voted_stake,
            "invariant: voted stake {} is more than total voted stake {}",
            voted_stake,
            self.total_voted_stake
        );
        self.total_voted_stake = self.total_voted_stake + account_stake - voted_stake;
        if account_stake > 0 {
            self.votes.insert(account_id, account_stake);
            self.check_result();
        }
    }
```

**File:** voting/src/lib.rs (L277-304)
```rust
    #[test]
    fn test_validator_stake_change() {
        let mut validators = HashMap::from_iter(vec![
            ("test1".to_string(), 40),
            ("test2".to_string(), 10),
            ("test3".to_string(), 10),
        ]);
        let context = get_context_with_epoch_height("test1".to_string(), 1);
        testing_env!(
            context,
            Default::default(),
            Default::default(),
            validators.clone()
        );

        let mut contract = VotingContract::new();
        contract.vote(true);
        validators.insert("test1".to_string(), 50);
        let context = get_context_with_epoch_height("test2".to_string(), 2);
        testing_env!(
            context,
            Default::default(),
            Default::default(),
            validators.clone()
        );
        contract.ping();
        assert!(contract.result.is_some());
    }
```

**File:** voting/src/lib.rs (L331-359)
```rust
    #[test]
    fn test_validator_kick_out() {
        let mut validators = HashMap::from_iter(vec![
            ("test1".to_string(), 40),
            ("test2".to_string(), 10),
            ("test3".to_string(), 10),
        ]);
        let context = get_context_with_epoch_height("test1".to_string(), 1);
        testing_env!(
            context,
            Default::default(),
            Default::default(),
            validators.clone()
        );

        let mut contract = VotingContract::new();
        contract.vote(true);
        assert_eq!((contract.get_total_voted_stake().0).0, 40);
        validators.remove(&"test1".to_string());
        let context = get_context_with_epoch_height("test2".to_string(), 2);
        testing_env!(
            context,
            Default::default(),
            Default::default(),
            validators.clone()
        );
        contract.ping();
        assert_eq!((contract.get_total_voted_stake().0).0, 0);
    }
```

**File:** voting/README.md (L1-6)
```markdown
# Voting Contract

The purpose of this contract is solely for validators to vote on whether to unlock
token transfer. Validators can call `vote` to vote for yes with the amount of stake they wish
to put on the vote. If there are more than 2/3 of the stake at any given moment voting for yes, the voting is done.
After the voting is finished, no one can further modify the contract.
```

**File:** lockup/src/owner.rs (L428-457)
```rust
    pub fn check_transfers_vote(&mut self) -> Promise {
        self.assert_owner();
        self.assert_transfers_disabled();
        self.assert_no_termination();

        let transfer_poll_account_id = match &self.lockup_information.transfers_information {
            TransfersInformation::TransfersDisabled {
                transfer_poll_account_id,
            } => transfer_poll_account_id,
            _ => unreachable!(),
        };

        env::log(
            format!(
                "Checking that transfers are enabled at the transfer poll contract @{}",
                transfer_poll_account_id,
            )
            .as_bytes(),
        );

        ext_transfer_poll::get_result(
            &transfer_poll_account_id,
            NO_DEPOSIT,
            gas::transfer_poll::GET_RESULT,
        )
        .then(ext_self_owner::on_get_result_from_transfer_poll(
            &env::current_account_id(),
            NO_DEPOSIT,
            gas::owner_callbacks::ON_VOTING_GET_RESULT,
        ))
```

**File:** lockup/src/owner_callbacks.rs (L253-278)
```rust
    /// Called after the transfer voting contract was checked for the vote result.
    pub fn on_get_result_from_transfer_poll(
        &mut self,
        #[callback] poll_result: PollResult,
    ) -> bool {
        assert_self();
        self.assert_transfers_disabled();

        if let Some(transfers_timestamp) = poll_result {
            env::log(
                format!(
                    "Transfers were successfully enabled at {}",
                    transfers_timestamp.0
                )
                .as_bytes(),
            );
            self.lockup_information.transfers_information =
                TransfersInformation::TransfersEnabled {
                    transfers_timestamp,
                };
            true
        } else {
            env::log(b"The transfers are not enabled yet");
            false
        }
    }
```

**File:** lockup/src/getters.rs (L65-113)
```rust
    pub fn get_locked_amount(&self) -> WrappedBalance {
        let lockup_amount = self.lockup_information.lockup_amount;
        if let TransfersInformation::TransfersEnabled {
            transfers_timestamp,
        } = &self.lockup_information.transfers_information
        {
            let lockup_timestamp = std::cmp::max(
                transfers_timestamp
                    .0
                    .saturating_add(self.lockup_information.lockup_duration),
                self.lockup_information.lockup_timestamp.unwrap_or(0),
            );
            let block_timestamp = env::block_timestamp();
            if lockup_timestamp <= block_timestamp {
                let unreleased_amount =
                    if let &Some(release_duration) = &self.lockup_information.release_duration {
                        let end_timestamp = lockup_timestamp.saturating_add(release_duration);
                        if block_timestamp >= end_timestamp {
                            // Everything is released
                            0
                        } else {
                            let time_left = U256::from(end_timestamp - block_timestamp);
                            let unreleased_amount = U256::from(lockup_amount) * time_left
                                / U256::from(release_duration);
                            // The unreleased amount can't be larger than lockup_amount because the
                            // time_left is smaller than total_time.
                            unreleased_amount.as_u128()
                        }
                    } else {
                        0
                    };

                let unvested_amount = match &self.vesting_information {
                    VestingInformation::VestingSchedule(vs) => self.get_unvested_amount(vs.clone()),
                    VestingInformation::Terminating(terminating) => terminating.unvested_amount,
                    // Vesting is private, so we can assume the vesting started before lockup date.
                    _ => U128(0),
                };
                return std::cmp::max(
                    unreleased_amount
                        .saturating_sub(self.lockup_information.termination_withdrawn_tokens),
                    unvested_amount.0,
                )
                .into();
            }
        }
        // The entire balance is still locked before the lockup timestamp.
        (lockup_amount - self.lockup_information.termination_withdrawn_tokens).into()
    }
```
