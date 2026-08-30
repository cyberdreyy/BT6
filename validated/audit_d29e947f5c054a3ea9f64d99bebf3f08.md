No vulnerability found for this question.

**Rationale:** `verify_path`/`ReceiptProof::verify_against_receipt_root` [1](#0-0)  is invoked exclusively during consensus-level validation paths — chunk part/receipt validation in `shards_manager_actor.rs` [2](#0-1) , stateless chunk-witness validation [3](#0-2) , state-sync header validation [4](#0-3) , and spice chunk execution [5](#0-4) . None of these are part of the user-facing gas-metering/fee model. The actual runtime gas accounting (`tx_cost`, `receipt_required_cost`, `action_receipt_required_cost` in `runtime/runtime/src/config.rs` and `runtime/runtime/src/actions.rs`) [6](#0-5) [7](#0-6)  charges fees per action/byte and has no notion of Merkle-proof depth or a "verify_path fee" at all — there is no fixed/constant gas charge tied to proof depth to bypass. `verify_path` cost is validator/node compute performed as part of ordinary block/chunk processing (not billed to any account), and its depth is bounded by `log2(number of outgoing receipts in a chunk)`, which itself is already constrained by the chunk's gas limit — an attacker cannot unboundedly inflate proof depth without paying the corresponding gas for creating that many outgoing receipts in the first place. There is no reachable mechanism by which an unprivileged attacker could cause fund loss, inflation, double-spend, consensus divergence, or a shard halt via this code path, so the premised "fee payment bypass" does not exist in this codebase.

### Citations

**File:** core/primitives/src/sharding.rs (L1059-1065)
```rust
impl ReceiptProof {
    pub fn verify_against_receipt_root(&self, receipt_root: CryptoHash) -> bool {
        let ReceiptProof(shard_receipts, receipt_proof) = self;
        let receipt_hash =
            CryptoHash::hash_borsh(ReceiptList(receipt_proof.to_shard_id, shard_receipts));
        verify_path(receipt_root, &receipt_proof.proof, &receipt_hash)
    }
```

**File:** chain/chunks/src/shards_manager_actor.rs (L1793-1808)
```rust
        // 1.e Checking receipts validity
        for proof in &prev_outgoing_receipts {
            // TODO: only validate receipts we care about
            // https://github.com/near/nearcore/issues/5885
            // we can't simply use prev_block_hash to check if the node tracks this shard or not
            // because prev_block_hash may not be ready
            //
            // from_shard_id is not covered by the receipts merkle root, so it must be checked
            // explicitly.
            if proof.1.from_shard_id != header.shard_id()
                || !proof.verify_against_receipt_root(*header.prev_outgoing_receipts_root())
            {
                byzantine_assert!(false);
                return Err(Error::ChainError(near_chain::Error::InvalidReceiptsProof));
            }
        }
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L532-566)
```rust
pub fn validate_receipt_proof(
    receipt_proof: &ReceiptProof,
    from_chunk: &ShardChunkHeader,
    target_chunk_shard_id: ShardId,
    outgoing_receipts_root: CryptoHash,
) -> Result<(), Error> {
    // Validate that from_shard_id is correct. The receipts must match the outgoing receipt root
    // for this shard, so it's impossible to fake it.
    if receipt_proof.1.from_shard_id != from_chunk.shard_id() {
        return Err(Error::InvalidChunkStateWitness(format!(
            "Receipt proof for chunk {:?} is from shard {}, expected shard {}",
            from_chunk.chunk_hash(),
            receipt_proof.1.from_shard_id,
            from_chunk.shard_id(),
        )));
    }
    // Validate that to_shard_id is correct. to_shard_id is also encoded in the merkle tree,
    // so it's impossible to fake it.
    if receipt_proof.1.to_shard_id != target_chunk_shard_id {
        return Err(Error::InvalidChunkStateWitness(format!(
            "Receipt proof for chunk {:?} is for shard {}, expected shard {}",
            from_chunk.chunk_hash(),
            receipt_proof.1.to_shard_id,
            target_chunk_shard_id
        )));
    }
    // Verify that (receipts, to_shard_id) belongs to the merkle tree of outgoing receipts.
    if !receipt_proof.verify_against_receipt_root(outgoing_receipts_root) {
        return Err(Error::InvalidChunkStateWitness(format!(
            "Receipt proof for chunk {:?} has invalid merkle path, doesn't match outgoing receipts root",
            from_chunk.chunk_hash()
        )));
    }
    Ok(())
}
```

**File:** chain/chain/src/state_sync/adapter.rs (L490-502)
```rust
                if !verify_path(*root, proof, &receipts_hash) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                }
                // 4f. Proving the outgoing_receipts_root matches that in the block
                if !verify_path(
                    *block_header.prev_chunk_outgoing_receipts_root(),
                    block_proof,
                    root,
                ) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                }
```

**File:** chain/client/src/spice/chunk_executor_actor/receipt_tracker.rs (L107-123)
```rust
fn verify_receipt_proof(
    receipt_proof: &ReceiptProof,
    execution_results: &HashMap<ShardId, Arc<ChunkExecutionResult>>,
) -> Result<(), Error> {
    let Some(execution_result) = execution_results.get(&receipt_proof.1.from_shard_id) else {
        debug_assert!(false, "execution results missing results when verifying receipts");
        tracing::error!(
            target: "chunk_executor",
            from_shard_id=?receipt_proof.1.from_shard_id,
            "execution results missing results when verifying receipts"
        );
        return Err(Error::InvalidShardId(receipt_proof.1.from_shard_id));
    };
    if !receipt_proof.verify_against_receipt_root(execution_result.outgoing_receipts_root) {
        return Err(Error::InvalidReceiptsProof);
    }
    Ok(())
```

**File:** runtime/runtime/src/actions.rs (L521-556)
```rust
/// Returns the cost required to execute the Receipt and all actions it contains
fn receipt_required_cost(
    apply_state: &ApplyState,
    receipt: &Receipt,
) -> Result<ParameterCost, RuntimeError> {
    Ok(match receipt.versioned_receipt() {
        VersionedReceiptEnum::Action(action_receipt)
        | VersionedReceiptEnum::PromiseYield(action_receipt) => {
            action_receipt_required_cost(apply_state, receipt, action_receipt.into())?
        }
        VersionedReceiptEnum::GlobalContractDistribution(_)
        | VersionedReceiptEnum::Data(_)
        | VersionedReceiptEnum::PromiseResume(_) => ParameterCost::ZERO,
    })
}

fn action_receipt_required_cost(
    apply_state: &ApplyState,
    receipt: &Receipt,
    action_receipt: VersionedActionReceipt,
) -> Result<ParameterCost, RuntimeError> {
    let mut required_gas = total_prepaid_exec_fees(
        &apply_state.config,
        &action_receipt.actions(),
        receipt.receiver_id(),
    )?;
    let attached_gas = total_prepaid_gas(&action_receipt.actions())?;
    // Gas attached to outgoing function calls have no associated compute costs.
    // Compute costs are only relevant when burning gas.
    let attached_gas_cost = ParameterCost { gas: attached_gas, compute: 0 };
    required_gas = required_gas.checked_add_result(attached_gas_cost)?;
    required_gas = required_gas.checked_add_result(
        apply_state.config.fees.fee(ActionCosts::new_action_receipt).exec_fee(),
    )?;
    Ok(required_gas)
}
```

**File:** runtime/runtime/src/config.rs (L415-500)
```rust
/// Returns the total cost of converting a `tx` into a receipt, including the
/// costs of the spawned receipts.
pub fn tx_cost(
    config: &RuntimeConfig,
    tx: &Transaction,
    current_gas_price: Balance,
) -> Result<TransactionCost, IntegerOverflowError> {
    calculate_tx_cost(
        tx.receiver_id(),
        tx.signer_id(),
        tx.public_key(),
        tx.actions(),
        config,
        current_gas_price,
    )
}

/// Like [`tx_cost`], for callers that have the transaction's fields but not a
/// `Transaction` (e.g. the indexer prices transaction views).
pub fn calculate_tx_cost(
    receiver_id: &AccountId,
    signer_id: &AccountId,
    signer_public_key: &PublicKey,
    actions: &[Action],
    config: &RuntimeConfig,
    current_gas_price: Balance,
) -> Result<TransactionCost, IntegerOverflowError> {
    let sender_is_receiver = receiver_id == signer_id;
    let fees = &config.fees;
    let mut burnt: ParameterCost =
        fees.fee(ActionCosts::new_action_receipt).send_fee(sender_is_receiver);
    burnt = burnt.checked_add_result(total_send_fees(
        config,
        sender_is_receiver,
        actions,
        receiver_id,
    )?)?;
    // Burn the signature-verification cost as part of converting the
    // transaction. This raises the gas the signer must buy (burnt_amount /
    // total_cost below) but never `gas_remaining` (the gas attached to / left
    // for the resulting receipts), so on-chain function-call gas budgets are
    // unaffected.
    burnt = burnt.checked_add_result(signature_verification_cost(
        fees,
        signer_public_key,
        actions,
        config.wasm_config.fix_ml_dsa_cost_charging,
    )?)?;

    // Calculate `gas_remaining`, which are all gas costs minus what is already
    // burnt in the sending step. Compute is not relevant here, as this gas will
    // be burnt later and has no effect on the current chunk capacity.
    // Gas attached to function calls
    let prepaid_gas = total_prepaid_gas(actions)?;
    // Send/Exec costs for actions inside the receipt
    let prepaid_send_fee = total_prepaid_send_fees(config, actions)?;
    let prepaid_exec_fee = total_prepaid_exec_fees(config, actions, receiver_id)?;
    // Exec cost for the receipt that wraps the actions
    let receipt_cost = fees.fee(ActionCosts::new_action_receipt).exec_fee();
    let gas_remaining = prepaid_gas
        .checked_add_result(prepaid_send_fee.gas)?
        .checked_add_result(receipt_cost.gas)?
        .checked_add_result(prepaid_exec_fee.gas)?;

    // Gas burned on converting the transaction to a receipt is burned at the current price.
    let burnt_amount = safe_gas_to_balance(current_gas_price, burnt.gas)?;

    // Gas attached to the receipt is purchased at a price which should be at least as large as
    // min_gas_purchase_price. Later it might be burned at a lower price, in which case the price
    // difference will be refunded.
    let receipt_gas_price = std::cmp::max(current_gas_price, config.min_gas_purchase_price);
    let remaining_gas_amount = safe_gas_to_balance(receipt_gas_price, gas_remaining)?;
    let gas_cost = safe_add_balance(burnt_amount, remaining_gas_amount)?;
    let deposit_cost = total_deposit(actions)?;
    let total_cost = safe_add_balance(gas_cost, deposit_cost)?;
    Ok(TransactionCost {
        gas_burnt: burnt.gas,
        compute_burnt: burnt.compute,
        gas_remaining,
        receipt_gas_price,
        burnt_amount,
        gas_cost,
        deposit_cost,
        total_cost,
    })
}
```
