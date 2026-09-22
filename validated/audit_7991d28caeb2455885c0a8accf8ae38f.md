### Title
Missing upper-bound validation of `inflation_rewards_commission_bps` / `block_revenue_commission_bps` in `VoteInitV2` initialization allows commission rates >100% - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
`initialize_account_v2` (used by the `InitializeAccountV2` vote instruction, reachable by any unprivileged transaction sender creating their own vote account) accepts attacker-supplied `inflation_rewards_commission_bps` and `block_revenue_commission_bps` values (each a `u16`, max `65535`) with no validation that they are `<= 10_000` (100%), unlike the CLI-side `is_valid_basis_points` check which only constrains the client tooling, not the on-chain instruction handler.

### Finding Description
`VoteInitV2` carries `inflation_rewards_commission_bps: u16` and `block_revenue_commission_bps: u16`, intended to represent basis points in the range `0..=10_000` (100%), as documented by the CLI help text: "Commission rate in basis points (0-10000)" [1](#0-0) .

The on-chain path, `initialize_account_v2`, only validates the node signer and the collector accounts (per SIMD-0232/SIMD-0464 checks) and verifies the BLS proof of possession — it never bounds-checks the commission bps fields before writing them into vote state via `VoteStateHandler::init_vote_account_state_v2`: [2](#0-1) 

`init_vote_account_state_v2` in turn constructs a `VoteStateV4` directly from the caller-supplied `vote_init` and commits it to the account with no range check on the two commission fields: [3](#0-2) 

The existing test `test_init_vote_account_state_v2` even demonstrates initialization succeeding with commission values (`1_234` bps and `5_678` bps) that happen to be in-range, but nothing in the code path rejects out-of-range values such as `65535` (655%): [4](#0-3) 

This mirrors the GSP.sol bug class exactly: the update path for the old, `u8`-bounded v1 commission is naturally capped at 0-255 percent by its type, but the newer, wider `u16` basis-point fields introduced for `VoteInitV2` are never validated against the `10_000` maximum at initialization — the same "validation exists on the runtime state size but not on the initializer" gap described in the reference report (`adjustMtFeeRate` bounds `_MT_FEE_RATE_ <= 10**18` on updates, but `init` does not bound `mtFeeRate`).

### Impact Explanation
`inflation_rewards_commission_bps` and `block_revenue_commission_bps` are subsequently consumed during reward distribution to split staking/block rewards between the commission collector and stakers (`runtime/src/inflation_rewards/mod.rs`, `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`, `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`). A commission value above 100% (`10_000` bps) means the commission computed from a reward pool can exceed the pool itself, which either:
- diverts stakers' entire reward allocation to the commission collector (stake/reward corruption), or
- causes an arithmetic underflow/overflow when computing the staker's remaining share, depending on whether the subtraction is checked, saturating, or wrapping.

Either outcome corrupts reward accounting for stakers delegated to that vote account, which is one of the accepted analog impacts (stake/reward corruption). I was not able to fully confirm from the available index whether the downstream subtraction is checked (panic → potential validator crash) or saturating (silent reward loss for delegators) — this needs to be verified directly against `runtime/src/inflation_rewards/mod.rs`.

### Likelihood Explanation
High reachability: any unprivileged user can create their own vote account and call `InitializeAccountV2` with an arbitrarily large `u16` commission bps value; no elevated privileges, signer beyond the vote account's own node key, or special conditions are required. The bug is a pure missing-validation gap, not dependent on race conditions or specific cluster state.

### Recommendation
Add an explicit bounds check in `initialize_account_v2` (`programs/vote/src/vote_state/mod.rs`) before calling `VoteStateHandler::init_vote_account_state_v2`, rejecting `vote_init.inflation_rewards_commission_bps > 10_000` or `vote_init.block_revenue_commission_bps > 10_000` with `InstructionError::InvalidInstructionData` (or a dedicated custom error), matching the bound already enforced conceptually by `is_valid_basis_points` in the CLI and by the implicit `u8` cap on the legacy v1 `commission` field.

### Proof of Concept
1. Any unprivileged actor creates a fresh vote account (system-program allocate + assign to vote program).
2. Actor signs and submits `VoteInstruction::InitializeAccountV2` with `VoteInitV2 { inflation_rewards_commission_bps: 65535, block_revenue_commission_bps: 65535, .. }`.
3. `initialize_account_v2` (`programs/vote/src/vote_state/mod.rs:1139-1186`) performs signer/collector/BLS checks only and calls `VoteStateHandler::init_vote_account_state_v2` (`programs/vote/src/vote_state/handler.rs:344-364`), which commits the out-of-range commission bps into `VoteStateV4` with no rejection.
4. At the next epoch reward distribution, the corrupted commission fields are read from this vote account by the reward-distribution code path (`runtime/src/inflation_rewards/mod.rs`), producing an over-100% commission split and corrupting the affected delegators' reward accounting.

### Citations

**File:** cli/src/vote.rs (L124-133)
```rust
                    Arg::with_name("inflation_rewards_commission_bps")
                        .long("inflation-rewards-commission-bps")
                        .value_name("BASIS_POINTS")
                        .takes_value(true)
                        .validator(is_valid_basis_points)
                        .help(
                            "Commission rate in basis points (0-10000) for inflation rewards. 100 \
                             basis points = 1%. Only valid with VoteInitV2 (--use-v2-instruction \
                             or when SIMD-0464 feature is active). [default: 10000 (100%)]",
                        ),
```

**File:** programs/vote/src/vote_state/mod.rs (L1139-1186)
```rust
pub fn initialize_account_v2<S: std::hash::BuildHasher, F>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    vote_init: &VoteInitV2,
    inflation_rewards_collector: NewCommissionCollector,
    block_revenue_collector: NewCommissionCollector,
    signers: &HashSet<Pubkey, S>,
    clock: &Clock,
    rent: &Rent,
    consume_pop_compute_units: F,
) -> Result<(), InstructionError>
where
    F: FnOnce() -> Result<(), InstructionError>,
{
    VoteStateHandler::check_vote_account_length(vote_account, target_version)?;
    let versioned = vote_account.get_state::<VoteStateVersions>()?;

    if !versioned.is_uninitialized() {
        return Err(InstructionError::AccountAlreadyInitialized);
    }

    // node must agree to accept this vote account
    verify_authorized_signer(&vote_init.node_pubkey, signers)?;

    // Per SIMD-0464, validate the collector accounts using the same checks as
    // `UpdateCommissionCollector` (SIMD-0232).
    let inflation_rewards_collector_key =
        inflation_rewards_collector.validate_and_resolve_key(vote_account, rent)?;
    let block_revenue_collector_key =
        block_revenue_collector.validate_and_resolve_key(vote_account, rent)?;

    // verify the BLS pubkey proof of possession
    verify_bls_proof_of_possession(
        vote_account.get_key(),
        &vote_init.authorized_voter_bls_pubkey,
        &vote_init.authorized_voter_bls_proof_of_possession,
        consume_pop_compute_units,
    )?;

    VoteStateHandler::init_vote_account_state_v2(
        vote_account,
        vote_init,
        &inflation_rewards_collector_key,
        &block_revenue_collector_key,
        clock,
        target_version,
    )
}
```

**File:** programs/vote/src/vote_state/handler.rs (L344-364)
```rust
    pub fn init_vote_account_state_v2(
        vote_account: &mut BorrowedInstructionAccount,
        vote_init: &VoteInitV2,
        inflation_rewards_collector: &Pubkey,
        block_revenue_collector: &Pubkey,
        clock: &Clock,
        target_version: VoteStateTargetVersion,
    ) -> Result<(), InstructionError> {
        let handler = match target_version {
            VoteStateTargetVersion::V4 => {
                let vote_state = VoteStateV4::new(
                    vote_init,
                    inflation_rewards_collector,
                    block_revenue_collector,
                    clock,
                );
                Self::new_v4(vote_state)
            }
        };
        handler.set_vote_account_state(vote_account)
    }
```

**File:** programs/vote/src/vote_state/handler.rs (L1793-1861)
```rust
    #[test]
    fn test_init_vote_account_state_v2() {
        let vote_pubkey = Pubkey::new_unique();
        let inflation_rewards_collector = Pubkey::new_unique();
        let block_revenue_collector = Pubkey::new_unique();
        let vote_init = VoteInitV2 {
            node_pubkey: Pubkey::new_unique(),
            authorized_voter: Pubkey::new_unique(),
            authorized_voter_bls_pubkey: [7u8; BLS_PUBLIC_KEY_COMPRESSED_SIZE],
            authorized_voter_bls_proof_of_possession: [8u8;
                BLS_PROOF_OF_POSSESSION_COMPRESSED_SIZE],
            authorized_withdrawer: Pubkey::new_unique(),
            inflation_rewards_commission_bps: 1_234,
            block_revenue_commission_bps: 5_678,
        };
        let clock = Clock::default();
        let rent = Rent::default();

        let v4_size = VoteStateV4::size_of();
        let lamports = rent.minimum_balance(v4_size);
        let vote_account = AccountSharedData::new(lamports, v4_size, &id());

        let transaction_context = mock_transaction_context(vote_pubkey, vote_account, rent);
        let instruction_context = transaction_context.get_next_instruction_context().unwrap();
        let mut vote_account = instruction_context
            .try_borrow_instruction_account(0)
            .unwrap();

        VoteStateHandler::init_vote_account_state_v2(
            &mut vote_account,
            &vote_init,
            &inflation_rewards_collector,
            &block_revenue_collector,
            &clock,
            VoteStateTargetVersion::V4,
        )
        .unwrap();

        let VoteStateVersions::V4(v4) = vote_account.get_state::<VoteStateVersions>().unwrap()
        else {
            panic!("should be v4");
        };

        assert_eq!(v4.node_pubkey, vote_init.node_pubkey);
        assert_eq!(
            v4.authorized_voters.get_authorized_voter(clock.epoch),
            Some(vote_init.authorized_voter),
        );
        assert_eq!(v4.authorized_withdrawer, vote_init.authorized_withdrawer);
        assert_eq!(
            v4.bls_pubkey_compressed,
            Some(vote_init.authorized_voter_bls_pubkey),
        );
        assert_eq!(
            v4.inflation_rewards_commission_bps,
            vote_init.inflation_rewards_commission_bps,
        );
        assert_eq!(
            v4.block_revenue_commission_bps,
            vote_init.block_revenue_commission_bps,
        );
        assert_eq!(v4.inflation_rewards_collector, inflation_rewards_collector);
        assert_eq!(v4.block_revenue_collector, block_revenue_collector);
        assert_eq!(v4.pending_delegator_rewards, 0);
        assert!(v4.votes.is_empty());
        assert_eq!(v4.root_slot, None);
        assert!(v4.epoch_credits.is_empty());
        assert_eq!(v4.last_timestamp, BlockTimestamp::default());
    }
```
