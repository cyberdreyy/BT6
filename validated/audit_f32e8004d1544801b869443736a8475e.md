No vulnerability found for this question.

**Analysis:**

The premise doesn't hold up against the actual code. `calculate_and_update_voter_total_voting_power` is a `#[view]` function that takes `voter: address` as a plain parameter and internally calls `update_and_borrow_mut_delegated_votes(pool, governance_records, voter)` — not `update_and_borrow_mut_delegator_vote_delegation`. This function only synchronizes the `DelegatedVotes` bucket already keyed by that `voter` address (applying `active_shares_next_lockup` if a lockup cycle has passed), and computes the voting power currently recorded there — it does not create, transfer, or redirect any delegator's shares into an arbitrary voter's bucket. [1](#0-0) 

The only function that mutates `VoteDelegation.voter`/`pending_voter` for a given delegator is `update_and_borrow_mut_delegator_vote_delegation`, invoked (for writes) exclusively through `delegate_voting_power`, which is gated by requiring the actual delegator's signer: [2](#0-1) [3](#0-2) 

A voter only accumulates `active_shares`/`pending_inactive_shares` in `DelegatedVotes` through `update_governance_records_for_buy_in_active_shares` / `update_governance_records_for_buy_in_pending_inactive_shares` / `update_governanace_records_for_redeem_*`, all of which read the `current_voter`/`pending_voter` from the shareholder's own `VoteDelegation` entry — i.e., shares only ever move to a voter that a delegator legitimately delegated to via their own signed `delegate_voting_power` call. [4](#0-3) 

Framework tests confirm that voting power only moves to a new voter after the actual delegator calls `delegate_voting_power` and a full lockup cycle elapses — an unrelated caller invoking the read-only `calculate_and_update_voter_total_voting_power` cannot alter the mapping. [5](#0-4) 

Since `calculate_and_update_voter_total_voting_power` never touches `vote_delegation` records and `VoteDelegation.voter` can only be changed by the delegator's own signed transaction, the described attack path does not exist in this codebase.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L723-735)
```text
    public fun calculate_and_update_voter_total_voting_power(
        pool_address: address,
        voter: address
    ): u64 acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        assert_partial_governance_voting_enabled(pool_address);
        // Delegation pool need to be synced to explain rewards(which could change the coin amount) and
        // commission(which could cause share transfer).
        synchronize_delegation_pool(pool_address);
        let pool = borrow_global<DelegationPool>(pool_address);
        let governance_records = borrow_global_mut<GovernanceRecords>(pool_address);
        let latest_delegated_votes = update_and_borrow_mut_delegated_votes(pool, governance_records, voter);
        calculate_total_voting_power(pool, latest_delegated_votes)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1160-1185)
```text











            return vote_delegation_table.borrow_mut_with_default(delegator, VoteDelegation {
                voter: delegator,
                last_locked_until_secs: locked_until_secs,
                pending_voter: delegator,
            })
        };

        let vote_delegation = vote_delegation_table.borrow_mut(delegator);
        // A lockup period has passed since last time `vote_delegation` was updated. Pending voter takes effect.
        if (vote_delegation.last_locked_until_secs < locked_until_secs) {
            vote_delegation.voter = vote_delegation.pending_voter;
            vote_delegation.last_locked_until_secs = locked_until_secs;
        };
        vote_delegation
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1344-1350)
```text
    /// Allows a delegator to delegate its voting power to a voter. If this delegator already has a delegated voter,
    /// this change won't take effects until the next lockup period.
    public entry fun delegate_voting_power(
        delegator: &signer,
        pool_address: address,
        new_voter: address
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L2015-2035)
```text
    fun update_governance_records_for_buy_in_active_shares(
        pool: &DelegationPool, pool_address: address, new_shares: u128, shareholder: address
    ) acquires GovernanceRecords {
        // <active shares> of <shareholder> += <new_shares> ---->
        // <active shares> of <current voter of shareholder> += <new_shares>
        // <active shares> of <next voter of shareholder> += <new_shares>
        let governance_records = borrow_global_mut<GovernanceRecords>(pool_address);
        let vote_delegation = update_and_borrow_mut_delegator_vote_delegation(pool, governance_records, shareholder);
        let current_voter = vote_delegation.voter;
        let pending_voter = vote_delegation.pending_voter;
        let current_delegated_votes =
            update_and_borrow_mut_delegated_votes(pool, governance_records, current_voter);
        current_delegated_votes.active_shares += new_shares;
        if (pending_voter == current_voter) {
            current_delegated_votes.active_shares_next_lockup += new_shares;
        } else {
            let pending_delegated_votes =
                update_and_borrow_mut_delegated_votes(pool, governance_records, pending_voter);
            pending_delegated_votes.active_shares_next_lockup += new_shares;
        };
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L4154-4175)
```text
        // Delegator1 delegates its voting power to voter1 but it takes 1 lockup cycle to take effects. So no voting power
        // change now.
        delegate_voting_power(delegator1, pool_address, voter1_address);
        assert!(calculate_and_update_voter_total_voting_power(pool_address, voter1_address) == 0, 1);
        assert!(calculate_and_update_voter_total_voting_power(pool_address, voter2_address) == 0, 1);
        assert!(calculate_and_update_voter_total_voting_power(pool_address, delegator1_address) == 10 * ONE_APT, 1);
        assert!(calculate_and_update_voter_total_voting_power(pool_address, delegator2_address) == 90 * ONE_APT, 1);

        // 1 epoch passed but the lockup cycle hasn't ended. No voting power change.
        end_aptos_epoch();
        assert!(calculate_and_update_voter_total_voting_power(pool_address, voter1_address) == 0, 1);
        assert!(calculate_and_update_voter_total_voting_power(pool_address, voter2_address) == 0, 1);
        assert!(calculate_and_update_voter_total_voting_power(pool_address, delegator1_address) == 10 * ONE_APT, 1);
        assert!(calculate_and_update_voter_total_voting_power(pool_address, delegator2_address) == 90 * ONE_APT, 1);

        // One cycle passed. The voter change takes effects.
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_aptos_epoch();
        assert!(calculate_and_update_voter_total_voting_power(pool_address, voter1_address) == 10 * ONE_APT, 1);
        assert!(calculate_and_update_voter_total_voting_power(pool_address, voter2_address) == 0, 1);
        assert!(calculate_and_update_voter_total_voting_power(pool_address, delegator1_address) == 0, 1);
        assert!(calculate_and_update_voter_total_voting_power(pool_address, delegator2_address) == 90 * ONE_APT, 1);
```
