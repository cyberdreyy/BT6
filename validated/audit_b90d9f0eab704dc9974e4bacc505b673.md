### Title
Unbounded, non-parameterized O(N) cost of `getVoteAccounts` scales with attacker-inflatable on-chain vote-account count and ignores single-key filtering - ([File: rpc/src/rpc.rs])

### Finding Description
`RpcSol::get_vote_accounts` (`rpc/src/rpc.rs`, the `get_vote_accounts` method) calls `bank.vote_accounts()` [1](#0-0) , which returns `Arc<VoteAccountsHashMap>` cloned from `self.stakes_cache.stakes().vote_accounts()` [2](#0-1) . The RPC handler then runs `.iter().filter_map(...).partition(...)` over the **entire** map on every call [3](#0-2) . Critically, when `config.vote_pubkey` is supplied to filter to a single account, the filtering is done *inside* the iteration closure (`if let Some(filter_by_vote_pubkey) = filter_by_vote_pubkey && *vote_pubkey != filter_by_vote_pubkey { return None; }` [4](#0-3) ) rather than via a direct `HashMap::get` lookup, so a single-account query still costs O(N) where N is the total number of live vote accounts, and additionally computes `vote_state_view()`, `epoch_credits_iter()`, etc. for every entry before discarding all but one.

`VoteAccounts` (the map backing `bank.vote_accounts()`) tracks every account owned by the vote program regardless of delegated stake — it is not filtered to only staked/active validators for this API (that stake-based/BLS filtering, `clone_and_filter_for_vat`, is only used for the separate Alpenglow/votor path [5](#0-4) , which is out of scope but confirms no equivalent cap exists on the plain `vote_accounts()` map used by RPC). An unprivileged attacker can grow N by repeatedly submitting `vote_instruction::create_account_with_config` transactions (each only needing the rent-exempt reserve for a vote account, recoverable later by withdrawing/closing), as demonstrated in `test_bank_vote_accounts` [6](#0-5)  and the staking_utils test helper `setup_vote_and_stake_accounts` [7](#0-6) . No delegated stake is required for the account to appear in `bank.vote_accounts()`.

There is no parameter/response-size limit, pagination, or per-call cost cap on `get_vote_accounts` analogous to `dataSlice`/filter-based bounding used elsewhere (e.g., `getProgramAccounts` secondary indexes, explicitly excluded from scope). The only bounded portion is per-account epoch-credits history (`MAX_RPC_VOTE_ACCOUNT_INFO_EPOCH_CREDITS_HISTORY`), which limits per-entry work but not the number of entries processed.

### Impact Explanation
This falls under "unbounded cost for a single low-rate call": a single `getVoteAccounts` RPC request (issued at or below the permitted rate of one call per `CLUSTER_SLOT_TIME_TARGET / 2`) forces the validator's JSON-RPC service to allocate and iterate a structure whose size is controlled by attacker-authored on-chain state rather than any protocol-enforced limit. Repeated inflation of N degrades RPC responsiveness/CPU and memory allocation for every future `getVoteAccounts` call (affecting all RPC clients of that node, not just the attacker), and the per-call cost does not shrink even when a caller requests a single `vote_pubkey`, defeating the intuitive expectation that a filtered query is O(1)/O(log N).

### Likelihood Explanation
Feasible and repeatable with only:
1. Enough SOL to fund the rent-exempt reserve of N vote accounts (recoverable via `withdraw`), no delegated stake required.
2. Ordinary `create_account_with_config`/`create_account_with_seed` transactions submitted at normal transaction rates (not restricted by the RPC call-rate limit, since these are write transactions, not RPC queries).
3. A single subsequent `getVoteAccounts` call to trigger the O(N) work.

No validator/leader/gossip control or config changes are needed, only capital proportional to N (recoverable), matching the "writing on-chain data later returned through those APIs" attacker model allowed by the rules.

### Recommendation
- Add an explicit cap / pagination (e.g., `limit`/`before`/`after` or a maximum returned/iterated vote-account count) to `get_vote_accounts` in `rpc/src/rpc.rs`.
- When `config.vote_pubkey` is set, perform a direct `vote_accounts.get(&pubkey)` lookup instead of iterating and filtering the full map.
- Consider bounding `bank.vote_accounts()` consumers used by public RPC to only vote accounts with nonzero stake (mirroring the filtering already used for `clone_and_filter_for_vat`) to prevent zero-stake accounts from inflating response cost.

### Proof of Concept
Integration test plan (Rust, using existing `RpcHandler` test harness in `rpc/src/rpc.rs` tests, e.g. `test_get_vote_accounts`):
```rust
#[test]
fn test_get_vote_accounts_cost_scales_with_zero_stake_accounts() {
    let rpc = RpcHandler::start();
    let bank = rpc.working_bank();

    // Baseline timing/memory with 1 vote account.
    let start = std::time::Instant::now();
    let _ = bank.vote_accounts();
    let baseline = start.elapsed();

    // Create N zero-stake vote accounts via vote_instruction::create_account_with_config,
    // funded only to rent-exempt minimum, no stake delegated.
    for _ in 0..100_000 {
        // sign & process a create_account_with_config transaction for a fresh Keypair
    }

    let start = std::time::Instant::now();
    let req = r#"{"jsonrpc":"2.0","id":1,"method":"getVoteAccounts","params":[{"votePubkey":"<single_target_pubkey>"}]}"#;
    let _ = rpc.io.handle_request_sync(req, rpc.meta.clone());
    let filtered_call = start.elapsed();

    // Assert cost of a single-pubkey-filtered call grows with N (should be ~O(1) but is O(N)).
    assert!(filtered_call > baseline * 1000, "single-key filter did not short-circuit; cost scaled with N");
}
```
Expected result confirming the bug: wall-clock time for a single filtered `getVoteAccounts` call grows roughly linearly with the number of on-chain vote accounts, rather than remaining constant, and there is no configuration (`RpcGetVoteAccountsConfig`) capable of bounding this cost.

### Citations

**File:** rpc/src/rpc.rs (L1167-1171)
```rust
        let bank = self.bank(config.commitment);
        let commission_rate_in_basis_points = bank
            .feature_set
            .is_active(&agave_feature_set::commission_rate_in_basis_points::id());
        let vote_accounts = bank.vote_accounts();
```

**File:** rpc/src/rpc.rs (L1181-1230)
```rust
        ) = vote_accounts
            .iter()
            .filter_map(|(vote_pubkey, (activated_stake, account))| {
                if let Some(filter_by_vote_pubkey) = filter_by_vote_pubkey
                    && *vote_pubkey != filter_by_vote_pubkey
                {
                    return None;
                }

                let vote_state_view = account.vote_state_view();
                let last_vote = vote_state_view.last_voted_slot().unwrap_or(0);
                let num_epoch_credits = vote_state_view.num_epoch_credits();
                let epoch_credits = vote_state_view
                    .epoch_credits_iter()
                    .skip(
                        num_epoch_credits
                            .saturating_sub(MAX_RPC_VOTE_ACCOUNT_INFO_EPOCH_CREDITS_HISTORY),
                    )
                    .map(Into::into)
                    .collect();

                Some(RpcVoteAccountInfo {
                    vote_pubkey: vote_pubkey.to_string(),
                    node_pubkey: vote_state_view.node_pubkey().to_string(),
                    activated_stake: *activated_stake,
                    commission: if commission_rate_in_basis_points {
                        // Derive percent from native bps, clamping to u8::MAX.
                        let bps = vote_state_view.inflation_rewards_commission();
                        bps.div_ceil(100).min(u8::MAX as u16) as u8
                    } else {
                        vote_state_view.commission()
                    },
                    inflation_rewards_commission_bps: Some(if commission_rate_in_basis_points {
                        vote_state_view.inflation_rewards_commission()
                    } else {
                        vote_state_view.commission() as u16 * 100
                    }),
                    root_slot: vote_state_view.root_slot().unwrap_or(0),
                    epoch_credits,
                    epoch_vote_account: epoch_vote_accounts.contains_key(vote_pubkey),
                    last_vote,
                })
            })
            .partition(|vote_account_info| {
                if bank.slot() >= delinquent_validator_slot_distance {
                    vote_account_info.last_vote > bank.slot() - delinquent_validator_slot_distance
                } else {
                    vote_account_info.last_vote > 0
                }
            });
```

**File:** runtime/src/bank.rs (L5796-5799)
```rust
    pub fn vote_accounts(&self) -> Arc<VoteAccountsHashMap> {
        let stakes = self.stakes_cache.stakes();
        Arc::from(stakes.vote_accounts())
    }
```

**File:** vote/src/vote_account.rs (L212-271)
```rust
    pub fn clone_and_filter_for_vat(
        &self,
        max_vote_accounts: usize,
        minimum_vote_account_balance: u64,
    ) -> VoteAccounts {
        assert!(max_vote_accounts > 0, "max_vote_accounts must be > 0");
        let capacity = max_vote_accounts.min(self.vote_accounts.len());
        let mut entries_to_sort: Vec<(&Pubkey, &VoteAccount, u64)> = Vec::with_capacity(capacity);
        for (pubkey, (stake, vote_account)) in self.vote_accounts.iter() {
            let has_bls = vote_account
                .vote_state_view()
                .bls_pubkey_compressed()
                .is_some();
            let has_stake = *stake != 0u64;
            let has_balance = vote_account.lamports() >= minimum_vote_account_balance;

            if !has_bls || !has_stake || !has_balance {
                continue;
            }
            entries_to_sort.push((pubkey, vote_account, *stake));
        }

        let valid_len = entries_to_sort.len();
        if entries_to_sort.len() > max_vote_accounts {
            // Find the cutoff stake using partial sort (more efficient than full sort).
            let (_, cutoff_entry, _) =
                entries_to_sort.select_nth_unstable_by(max_vote_accounts, |a, b| b.2.cmp(&a.2));
            let floor_stake = cutoff_entry.2;

            // Per SIMD 357, we remove all vote accounts with stake smaller or equal to
            // the first truncated one.
            entries_to_sort.retain(|(_, _, stake)| *stake > floor_stake);
        }

        let mut top_entries: HashMap<Pubkey, (u64, VoteAccount)> =
            HashMap::with_capacity(entries_to_sort.len());
        top_entries.extend(
            entries_to_sort
                .into_iter()
                .map(|(pubkey, vote_account, stake)| (*pubkey, (stake, vote_account.clone()))),
        );
        if top_entries.is_empty() {
            if cfg!(test) {
                info!("No valid vote accounts found");
            } else {
                error!("No valid vote accounts found");
            }
        }
        info!(
            "Out of {} vote accounts, {} are valid vote accounts after filtering, {} remain after \
             truncation",
            self.vote_accounts.len(),
            valid_len,
            top_entries.len()
        );
        VoteAccounts {
            vote_accounts: Arc::new(top_entries),
            staked_nodes: OnceLock::new(),
        }
    }
```

**File:** runtime/src/bank/tests.rs (L3231-3281)
```rust
#[test]
fn test_bank_vote_accounts() {
    let GenesisConfigInfo {
        genesis_config,
        mint_keypair,
        ..
    } = create_genesis_config_with_leader(500, &solana_pubkey::new_rand(), 1);
    let (bank, _bank_forks) = Bank::new_with_bank_forks_for_tests(&genesis_config);

    let vote_accounts = bank.vote_accounts();
    assert_eq!(vote_accounts.len(), 1); // bootstrap validator has
    // to have a vote account

    let vote_keypair = Keypair::new();
    let instructions = vote_instruction::create_account_with_config(
        &mint_keypair.pubkey(),
        &vote_keypair.pubkey(),
        &VoteInit {
            node_pubkey: mint_keypair.pubkey(),
            authorized_voter: vote_keypair.pubkey(),
            authorized_withdrawer: vote_keypair.pubkey(),
            commission: 0,
        },
        10,
        vote_instruction::CreateVoteAccountConfig {
            space: VoteStateV4::size_of() as u64,
            ..vote_instruction::CreateVoteAccountConfig::default()
        },
    );

    let message = Message::new(&instructions, Some(&mint_keypair.pubkey()));
    let transaction = Transaction::new(
        &[&mint_keypair, &vote_keypair],
        message,
        bank.last_blockhash(),
    );

    bank.process_transaction(&transaction).unwrap();

    let vote_accounts = bank.vote_accounts();

    assert_eq!(vote_accounts.len(), 2);

    assert!(vote_accounts.get(&vote_keypair.pubkey()).is_some());

    assert!(bank.withdraw(&vote_keypair.pubkey(), 10).is_ok());

    let vote_accounts = bank.vote_accounts();

    assert_eq!(vote_accounts.len(), 1);
}
```

**File:** ledger/src/staking_utils.rs (L31-118)
```rust
    pub(crate) fn setup_vote_and_stake_accounts(
        bank: &Bank,
        from_account: &Keypair,
        vote_account: &Keypair,
        validator_identity_account: &Keypair,
        amount: u64,
    ) {
        let vote_pubkey = vote_account.pubkey();
        fn process_instructions<T: Signers>(bank: &Bank, keypairs: &T, ixs: &[Instruction]) {
            let tx = Transaction::new_signed_with_payer(
                ixs,
                Some(&keypairs.pubkeys()[0]),
                keypairs,
                bank.last_blockhash(),
            );
            bank.process_transaction(&tx).unwrap();
        }

        process_instructions(
            bank,
            &[from_account, vote_account, validator_identity_account],
            &vote_instruction::create_account_with_config(
                &from_account.pubkey(),
                &vote_pubkey,
                &VoteInit {
                    node_pubkey: validator_identity_account.pubkey(),
                    authorized_voter: vote_pubkey,
                    authorized_withdrawer: vote_pubkey,
                    commission: 0,
                },
                amount,
                vote_instruction::CreateVoteAccountConfig {
                    space: VoteStateV4::size_of() as u64,
                    ..vote_instruction::CreateVoteAccountConfig::default()
                },
            ),
        );

        // Add BLS pubkey to the vote account using the authorize instruction with BLS.
        // This sets the authorized voter to the same pubkey but adds the BLS key.
        let bls_keypair =
            BLSKeypair::derive_from_signer(vote_account, BLS_KEYPAIR_DERIVE_SEED).unwrap();
        let (bls_pubkey, bls_proof_of_possession) =
            create_bls_proof_of_possession(&vote_pubkey, &bls_keypair);

        process_instructions(
            bank,
            &[from_account, vote_account],
            &[vote_instruction::authorize(
                &vote_pubkey,
                &vote_pubkey, // currently authorized voter
                &vote_pubkey, // new authorized voter (same, just adding BLS)
                VoteAuthorize::VoterWithBLS(VoterWithBLSArgs {
                    bls_pubkey,
                    bls_proof_of_possession,
                }),
            )],
        );

        let stake_account_keypair = Keypair::new();
        let stake_account_pubkey = stake_account_keypair.pubkey();

        let stake_account = StakeStateV2::Stake(
            Meta {
                authorized: Authorized::auto(&stake_account_pubkey),
                ..Meta::default()
            },
            Stake {
                delegation: Delegation {
                    voter_pubkey: vote_pubkey,
                    stake: amount,
                    ..Delegation::default()
                },
                ..Stake::default()
            },
            StakeFlags::default(),
        );

        let account = AccountSharedData::create_from_existing_shared_data(
            1,
            Arc::new(wincode::serialize(&stake_account).unwrap()),
            stake_program::id(),
            false,
            u64::MAX,
        );

        bank.store_account(&stake_account_pubkey, &account);
    }
```
