### No Vulnerability found for this question.

The behavior described is the expected, documented design of global contract distribution: `initiate_distribution` bumps a per-identifier nonce and writes it synchronously on the origin shard [1](#0-0) , while `apply_global_contract_distribution_receipt`/`forward_distribution_next_shard` propagate the code to remaining shards one hop per apply via a chained receipt [2](#0-1) . This is an intentional eventual-consistency mechanism, not a bug: any account or contract's data on other shards is inherently proprogated asynchronously in NEAR's sharded runtime, and callers of `UseGlobalContract` on a shard where the code hasn't arrived yet correctly get `GlobalContractDoesNotExist` [3](#0-2) .

Burning gas for a failed action is standard, universal NEAR fee semantics — gas pays for attempted computation/validation performed by the receiving shard, not for a guaranteed successful outcome, and this applies identically to any action that can fail for state-dependent reasons (e.g., calling a method on an account that doesn't exist yet, insufficient balance discovered only at execution, etc.). The deployer paying `storage_cost` via `action_deploy_global_contract` [4](#0-3)  pays for their own deploy/storage, which is unrelated to the gas fee a third party pays for their own `UseGlobalContract` attempt on a different shard.

There is no theft or permanent freezing of funds, no double-spend, no authorization escalation, and no consensus divergence or halt: each account only ever loses gas proportional to work it itself requested, consistent with normal NEAR fee mechanics. This is a known, self-consistent eventual-consistency characteristic of the global contracts feature rather than an exploitable flaw, and it does not meet the bar of "concrete loss or freezing of funds" required by the audit rules.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L34-50)
```rust
    let storage_cost = apply_state
        .config
        .fees
        .storage_usage_config
        .global_contract_storage_amount_per_byte
        .saturating_mul(deploy_contract.code.len() as u128);
    let Some(updated_balance) = account.amount().checked_sub(storage_cost) else {
        result.result = Err(ActionErrorKind::LackBalanceForState {
            account_id: account_id.clone(),
            amount: storage_cost,
        }
        .into());
        return Ok(());
    };
    result.tokens_burnt =
        result.tokens_burnt.checked_add(storage_cost).ok_or(IntegerOverflowError)?;
    account.set_amount(updated_balance);
```

**File:** runtime/runtime/src/global_contracts.rs (L83-89)
```rust
    if !state_update.contains_key(&key, AccessOptions::DEFAULT)? {
        result.result = Err(ActionErrorKind::GlobalContractDoesNotExist {
            identifier: contract_identifier.clone(),
        }
        .into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/global_contracts.rs (L158-169)
```rust
    // Increment the nonce and write it to state immediately to prevent multiple
    // distributions with the same nonce from being initiated. This requires
    // allowing the same nonce in the freshness check when applying the
    // distribution receipt.
    let nonce = increment_nonce(state_update, &id)?;
    let distribution_receipt =
        GlobalContractDistributionReceipt::new(id, current_shard_id, vec![], contract_code, nonce);
    let distribution_receipts =
        Receipt::new_global_contract_distribution(account_id, distribution_receipt);
    // No need to set receipt_id here, it will be generated as part of apply_action_receipt
    result.new_receipts.push(distribution_receipts);
    Ok(())
```

**File:** runtime/runtime/src/global_contracts.rs (L276-320)
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
```
