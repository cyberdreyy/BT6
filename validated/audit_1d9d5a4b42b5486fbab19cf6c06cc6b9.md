Based on tracing the exact mechanics of `GlobalContractDistributionReceipt` forwarding, this scenario does not produce the described vulnerability.

**Key mechanism:** `use_global_contract` (in `runtime/runtime/src/global_contracts.rs:75-108`) does not tie contract-code availability to any particular account's shard membership. It only sets `AccountContract::Global(code_hash)` / `GlobalByAccount(id)` on the account, pointing to a shard-local copy of the code keyed by `TrieKey::GlobalContractCode { identifier }`. [1](#0-0) 

That per-shard code copy is populated by `apply_global_contract_distribution_receipt`, which is designed as a full broadcast to *every* shard in the current layout, not a single-account-targeted routing. `forward_distribution_next_shard` selects the next hop as the first shard in the **current, full `shard_layout.shard_ids()`** that is not yet in `already_delivered_shards`: [2](#0-1) 

`receiver_shard_id`/`resolve_to_current_shard` is only invoked when the receipt's `target_shard` field is stale relative to the current layout (i.e., the receipt was delayed across a resharding event), and it does pick only the first child (`children[0]`) when the stale target was itself split: [3](#0-2) [4](#0-3) 

However, this arbitrary "pick one child" choice does not orphan the sibling shard: after landing on that one child (say `S1`), `forward_distribution_next_shard` marks only `S1` as delivered and then re-scans **all current `shard_ids()`** for the next undelivered shard — which will include the sibling `S2`, since `S2` was never separately marked delivered. `S2`'s `target_shard` is then set directly from the current layout's shard list, so `receiver_shard_id`'s direct-membership branch matches it without needing `resolve_to_current_shard` again. The hop-by-hop full enumeration over the *current* shard set (not the stale split map) guarantees eventual delivery to every live shard regardless of how `resolve_to_current_shard` resolved the intermediate stale hop.

Because contract-code delivery is per-shard broadcast (not per-account/per-boundary routing), an account's ability to use the global contract depends only on whether its current shard has eventually received the code — which the design guarantees, not on which specific boundary_account split the receipt happened to be misrouted through at an intermediate step.

This exact scenario (receipts delayed across two sequential resharding events, including boundary-account splits) is already covered by existing tests confirming no accounts are orphaned and no panic occurs: [5](#0-4) [6](#0-5) 

#No vulnerability found for this question.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L90-97)
```rust
    clear_account_contract_storage_usage(state_update, account_id, account)?;
    if account.contract().is_local() {
        state_update.remove(TrieKey::ContractCode { account_id: account_id.clone() });
    }
    let contract = match contract_identifier {
        GlobalContractIdentifier::CodeHash(code_hash) => AccountContract::Global(*code_hash),
        GlobalContractIdentifier::AccountId(id) => AccountContract::GlobalByAccount(id.clone()),
    };
```

**File:** runtime/runtime/src/global_contracts.rs (L276-299)
```rust
fn forward_distribution_next_shard(
    receipt: &Receipt,
    global_contract_data: &GlobalContractDistributionReceipt,
    apply_state: &ApplyState,
    epoch_info_provider: &dyn EpochInfoProvider,
    state_update: &mut TrieUpdate,
    receipt_sink: &mut ReceiptSink,
    receipt_to_tx: &mut Vec<(CryptoHash, ReceiptToTxInfo)>,
) -> Result<(), RuntimeError> {
    let shard_layout = epoch_info_provider.shard_layout(&apply_state.epoch_id)?;
    let already_delivered_shards = BTreeSet::from_iter(
        global_contract_data
            .already_delivered_shards()
            .iter()
            .cloned()
            .chain(std::iter::once(apply_state.shard_id)),
    );
    let Some(next_shard) = shard_layout
        .shard_ids()
        .filter(|shard_id| !already_delivered_shards.contains(&shard_id))
        .next()
    else {
        return Ok(());
    };
```

**File:** core/primitives/src/shard_layout/v3.rs (L315-326)
```rust
    /// Resolve any historical shard ID to a current shard by walking the full
    /// split history in `shards_split_map`. Returns the shard itself if it is
    /// current, or follows the first child at each generation until a current
    /// shard is reached. Returns `None` only if the shard ID is absent from
    /// both the current layout and the split history.
    pub fn resolve_to_current_shard(&self, shard_id: ShardId) -> Option<ShardId> {
        if self.shard_ids.contains(&shard_id) {
            return Some(shard_id);
        }
        let children = self.shards_split_map.get(&shard_id)?;
        self.resolve_to_current_shard(children[0])
    }
```

**File:** core/primitives/src/receipt.rs (L447-463)
```rust
            ReceiptEnum::GlobalContractDistribution(receipt) => {
                let target_shard = receipt.target_shard();
                if shard_layout.shard_ids().contains(&target_shard) {
                    target_shard
                } else {
                    // The target shard may be from an arbitrarily old layout (the receipt could
                    // have been delayed across multiple resharding events). resolve_to_current_shard
                    // will find a shard descendant in the current layout.
                    let Some(current_shard) = shard_layout.resolve_to_current_shard(target_shard)
                    else {
                        return Err(EpochError::ShardingError(format!(
                            "Shard {target_shard} does not exist in the shard layout or its split history",
                        )));
                    };
                    current_shard
                }
            }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L30-65)
```rust
#[test]
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
fn test_stale_global_contract_distribution_after_double_resharding() {
    init_test_logger();

    // The fix only works with V3 shard layouts (dynamic resharding).
    // With static resharding, the shard layout doesn't maintain a full split history.
    if !ProtocolFeature::DynamicResharding.enabled(PROTOCOL_VERSION) {
        return;
    }

    let epoch_length: BlockHeightDelta = 10;
    let base_boundary_accounts = create_account_ids(["user2", "user3"]).to_vec();
    let base_shard_layout = ShardLayout::multi_shard_custom(base_boundary_accounts, 3);
    let deploy_user: AccountId = create_account_id("user0");
    let users = create_account_ids(["user0", "user1", "user2", "user3", "user4", "user5"]).to_vec();
    let validators_spec = create_validators_spec(1, 0);
    let clients = validators_spec_clients(&validators_spec);
    let chunk_producer = clients[0].clone();
    let gas_limit = Gas::from_teragas(300);
    let base_pv = PROTOCOL_VERSION - 1;

    // Configure dynamic resharding to force-split two shards sequentially.
    // The first split targets the shard containing deploy_user (user0), so the
    // GlobalContractDistribution receipt becomes stale after two layout transitions.
    let first_split_shard = base_shard_layout.account_id_to_shard_id(&deploy_user);
    let second_split_shard = base_shard_layout.account_id_to_shard_id(&create_account_id("user4"));
    assert_ne!(first_split_shard, second_split_shard);

    let dynamic_config = DynamicReshardingConfig {
        memory_usage_threshold: u64::MAX,
        min_child_memory_usage: u64::MAX,
        max_number_of_shards: 100,
        min_epochs_between_resharding: 1.try_into().unwrap(),
        force_split_shards: vec![first_split_shard, second_split_shard],
        block_split_shards: vec![],
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L253-266)
```rust
    // Wait for the distribution to reach all shards.
    env.env.test_loop.run_for(Duration::seconds(3));

    // Check that users on all shards in the new layout can use the contract.
    let mut use_txs = vec![];
    let node = env.chunk_producer_node();
    for user in &env.users {
        let tx =
            node.tx_use_global_contract(user, GlobalContractIdentifier::CodeHash(*code.hash()));
        use_txs.push(node.submit_tx(tx));
    }
    env.env.test_loop.run_for(Duration::seconds(2));
    check_txs(&mut env.env.test_loop.data, &env.env.node_datas, &env.chunk_producer, &use_txs);
}
```
