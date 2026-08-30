No vulnerability found for this question.

The scenario described conflates two things that are architecturally independent: (1) determinism of a shard's own state transition function, and (2) the deliberately asynchronous, shard-by-shard propagation of `GlobalContractDistribution` receipts.

`apply_distribution_current_shard` writes the code to `TrieKey::GlobalContractCode { identifier }` only after `check_and_update_nonce` confirms the incoming nonce is not stale for *that shard's* local state, which prevents any given shard from ever regressing to older code once it has seen newer code. [1](#0-0) 

`UseGlobalContractAction` never copies contract bytes into the account; it only stores a reference (`AccountContract::GlobalByAccount(id)`) that is resolved against the local shard's `GlobalContractCode` trie key at the time each `FunctionCall` actually executes, via `RuntimeContractIdentifier::resolve`. [2](#0-1) [3](#0-2) 

Because each shard resolves code from its own local trie at call time, and distribution receipts are forwarded shard-by-shard (`forward_distribution_next_shard`) so that different shards can legitimately hold the old vs. new code for some period, this is by design, not a divergence bug — the invariant "identical pre-state and chunk produce identical outgoing receipts" is a per-shard guarantee, and each shard's own apply is a pure deterministic function of its own state and its own chunk's receipts (which every replica of that shard processes identically). Different shards are not required, and never guaranteed, to observe the same global-contract code at the same block height; the tests explicitly wait for propagation to complete before asserting all shards can use the contract, confirming eventual (not immediate) consistency is the intended behavior. [4](#0-3) [5](#0-4) 

No attacker-controlled input can force a single shard to non-deterministically diverge from its own replicas, and no fund loss, double-spend, authorization escalation, or state-root divergence within a shard's own consensus set is produced by this behavior.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L75-107)
```rust
pub(crate) fn use_global_contract(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
    account: &mut Account,
    contract_identifier: &GlobalContractIdentifier,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let key = TrieKey::GlobalContractCode { identifier: contract_identifier.clone().into() };
    if !state_update.contains_key(&key, AccessOptions::DEFAULT)? {
        result.result = Err(ActionErrorKind::GlobalContractDoesNotExist {
            identifier: contract_identifier.clone(),
        }
        .into());
        return Ok(());
    }
    clear_account_contract_storage_usage(state_update, account_id, account)?;
    if account.contract().is_local() {
        state_update.remove(TrieKey::ContractCode { account_id: account_id.clone() });
    }
    let contract = match contract_identifier {
        GlobalContractIdentifier::CodeHash(code_hash) => AccountContract::Global(*code_hash),
        GlobalContractIdentifier::AccountId(id) => AccountContract::GlobalByAccount(id.clone()),
    };
    account.set_storage_usage(
        account.storage_usage().checked_add(contract_identifier.len() as u64).ok_or_else(|| {
            StorageError::StorageInconsistentState(format!(
                "Storage usage integer overflow for account {}",
                account_id
            ))
        })?,
    );
    account.set_contract(contract).or_inconsistent_state(account_id)?;
    Ok(())
```

**File:** runtime/runtime/src/global_contracts.rs (L203-211)
```rust
    let is_nonce_fresh = check_and_update_nonce(global_contract_data, &identifier, state_update)?;
    if !is_nonce_fresh {
        return Ok(0);
    }

    let config = apply_state.config.wasm_config.clone();
    let trie_key = TrieKey::GlobalContractCode { identifier };
    let code_len = global_contract_data.code().len() as u64;
    state_update.set(trie_key, global_contract_data.code().to_vec());
```

**File:** runtime/runtime/src/global_contracts.rs (L276-321)
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
    let already_delivered_shards = Vec::from_iter(already_delivered_shards);
    let predecessor_id = receipt.predecessor_id().clone();
    let next_receipt = global_contract_data.forward(next_shard, already_delivered_shards);
    let mut next_receipt = Receipt::new_global_contract_distribution(predecessor_id, next_receipt);
    let receipt_id = apply_state.create_receipt_id(receipt.receipt_id(), 0);
    next_receipt.set_receipt_id(receipt_id);
    if apply_state.save_receipt_to_tx {
        receipt_to_tx.push((
            receipt_id,
            ReceiptToTxInfo::V1(ReceiptToTxInfoV1 {
                origin: ReceiptOrigin::FromReceipt(ReceiptOriginReceipt {
                    parent_receipt_id: *receipt.receipt_id(),
                    parent_predecessor_id: receipt.predecessor_id().clone(),
                }),
                receiver_account_id: next_receipt.receiver_id().clone(),
                shard_id: apply_state.shard_id,
            }),
        ));
    }
    receipt_sink.forward_or_buffer_receipt(next_receipt, apply_state, state_update)?;
    Ok(())
}
```

**File:** runtime/runtime/src/lib.rs (L629-639)
```rust
            Action::FunctionCall(function_call) => {
                metrics::ACTION_CALLED_COUNT.function_call.inc();
                let account = account.as_mut().expect(EXPECT_ACCOUNT_EXISTS);
                let account_contract = account.contract().into_owned();
                let contract_id = RuntimeContractIdentifier::resolve(
                    account_id,
                    account_contract,
                    &state_update,
                    &epoch_info_provider.chain_id(),
                    AccessOptions::DEFAULT,
                )?;
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
