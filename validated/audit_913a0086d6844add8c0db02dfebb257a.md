### Title
DeleteAccount action fee is a flat constant while `remove_account` performs O(N) trie work to purge access keys, gas keys, and contract-data entries - ([File: core/store/src/utils/mod.rs])

### Summary
`remove_account` in `core/store/src/utils/mod.rs` performs two full `locked_iter` trie-prefix scans over an account's access-key/gas-key space and its contract-data space, plus per-entry key parsing, to enumerate and delete every such entry. The gas charged for the `DeleteAccount` action (`delete_account_cost`) is a fixed constant in the `RuntimeConfigStore`, independent of how many keys/data entries the account holds, so a single `DeleteAccount` action can force validators/chunk producers to do unmetered, unbounded work for a flat fee.

### Finding Description
`remove_account` first removes the `Account` and `ContractCode` trie entries, then does two locked iterations:
- one over `trie_key_parsers::get_raw_prefix_for_access_keys(account_id)` to enumerate every `AccessKey`/gas-key-nonce entry, parsing each raw key via `parse_key_handle_from_access_key_key` and `parse_nonce_index_from_gas_key_key` before queuing it for removal,
- one over `trie_key_parsers::get_raw_prefix_for_contract_data(account_id, &[])` to enumerate every `ContractData` entry, parsing each via `parse_data_key_from_contract_data_key`. [1](#0-0) 

This scan-and-remove work is strictly proportional to the number of access keys, gas keys, and contract-data entries the account has accumulated (parameter N), but the protocol charges a single flat `delete_account_cost` for the `DeleteAccount` action regardless of N. This is confirmed across the shipped `RuntimeConfigStore` snapshots: `delete_account_cost` is `{ send_sir: 147489000000, send_not_sir: 147489000000, execution: 147489000000 }` in every observed protocol-version snapshot, with no per-key or per-byte multiplier. [2](#0-1) 

The runtime-params-estimator code that calibrates this cost explicitly measures only "delete an existing account" without varying the amount of associated state, and documentation confirms `DeleteAccount` uses only the flat `delete_account_cost` base fee (plus the fees for the follow-up beneficiary Transfer action, which are also flat/deposit-independent, not scaled by the deleted account's key/data count). [3](#0-2) [4](#0-3) 

Unlike WASM host-function storage operations (`storage_write`/`storage_read`), which are metered per call and per byte through `wasm_config.ext_costs` when a contract executes them, the `DeleteAccount` action's key/data purge is done natively inside `actions.rs`/`remove_account` outside of any VM gas-counter hook, so none of the per-node/per-key trie touching costs that apply to contract-driven storage access apply here. This means the attacker's one-time `delete_account_cost` payment does not scale with the actual trie work incurred by `remove_account`'s two `locked_iter` scans and per-key parsing.

An unprivileged attacker can:
1. Fund an account and submit many `AddKey` transactions (access keys / gas keys) and, if a `SetContractData`-style action or repeated `FunctionCall`s writing storage entries exist in this fork, populate many `ContractData` entries — each of these creation steps is individually gas-metered and storage-staked, so the attacker pays for creation.
2. Submit a single `DeleteAccount` action.
3. `remove_account` must scan and delete all N entries, but the attacker is charged only the fixed `delete_account_cost`, regardless of N.

### Impact Explanation
This is a metering-totality violation: the gas fee schedule does not charge for real work performed (`remove_account`'s O(N) scans), letting the attacker convert a bounded gas payment into unbounded validator/chunk-producer compute at deletion time. The direct bounty-relevant impact is resource-exhaustion/DoS-adjacent ("fee payment bypass") rather than direct fund theft — the attacker's storage-staked balance used to create the N entries is refunded to the `beneficiary_id` on deletion, so no NEAR is created or destroyed improperly; the harm is that the actual compute cost of removing N entries is not paid for, which could be leveraged to slow down block/chunk processing if N is made large enough, since there is no compute cost cap tied to N once accepted into the chunk under the flat action fee.

### Likelihood Explanation
Preconditions are entirely within an unprivileged attacker's control: fund an account, add many access/gas keys and/or contract data entries (each creation step individually gas-metered, so setup cost scales with N but is a one-time, attacker-controlled expense of gas plus temporarily locked storage stake that is refunded on deletion), then submit one `DeleteAccount` transaction. No special permissions, validator access, or race conditions are required, and the scenario is fully repeatable. The severity of impact scales with how large N can practically be grown while remaining within per-account storage/state limits and gas limits for the setup transactions, which was not independently confirmed in this investigation (no per-account cap on access-key or contract-data-entry count was found in the reviewed code).

### Recommendation
Charge `delete_account_cost` (or an additional component of it) proportionally to the number of access keys, gas keys, and contract-data entries actually removed — e.g., using the same `RemoveAccountResult::gas_key_nonce_count`/`gas_key_nonce_total_key_bytes` counters (and an analogous count for contract-data entries) returned by `remove_account`, and burn additional gas post-hoc based on the real counts, similar to how other per-item/per-byte actions (e.g. `AddKey`'s `function_call_cost_per_byte`) are metered.

### Proof of Concept
Integration/apply-path test plan:
1. Create account `alice.near` with a full-access key.
2. Submit N `SetContractData` (or equivalent storage-writing) transactions/actions to populate N distinct `ContractData` entries under `alice.near` (and/or N `AddKey` gas-key transactions), for N ∈ {10, 1000}.
3. Submit a single `DeleteAccount { beneficiary_id: bob.near }` transaction from `alice.near`.
4. Assert:
   - `gas_burnt` recorded for the `DeleteAccount` action is identical (flat) for both N=10 and N=1000 runs (matches `delete_account_cost` in the active `RuntimeConfigStore`).
   - `RemoveAccountResult::gas_key_nonce_count` / number of contract-data keys actually removed (observable via trie diff or a test hook around `remove_account`) grows linearly with N, demonstrating the work-to-fee mismatch.
   - Measure wall-clock/trie-node-touch count for `remove_account` scaling with N while gas charged stays constant.

### Citations

**File:** core/store/src/utils/mod.rs (L505-574)
```rust
pub fn remove_account(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
) -> Result<RemoveAccountResult, StorageError> {
    state_update.remove(TrieKey::Account { account_id: account_id.clone() });
    state_update.remove(TrieKey::ContractCode { account_id: account_id.clone() });

    let mut gas_key_nonce_count: usize = 0;
    let mut gas_key_nonce_total_key_bytes: usize = 0;

    // Removing access keys and gas key nonces
    let lock = state_update.trie().lock_for_iter();
    let mut keys_to_remove: Vec<TrieKey> = Vec::new();
    for raw_key in state_update
        .locked_iter(&trie_key_parsers::get_raw_prefix_for_access_keys(account_id), &lock)?
    {
        let raw_key = raw_key?;
        let key_handle = trie_key_parsers::parse_key_handle_from_access_key_key(
            &raw_key, account_id,
        )
        .map_err(|_e| {
            StorageError::StorageInconsistentState(
                "Can't parse key handle from raw key for AccessKey".to_string(),
            )
        })?;
        let nonce_index =
            trie_key_parsers::parse_nonce_index_from_gas_key_key(&raw_key, account_id, &key_handle)
                .map_err(|_e| {
                    StorageError::StorageInconsistentState(
                        "Can't parse nonce index from raw key for AccessKey".to_string(),
                    )
                })?;
        if let Some(index) = nonce_index {
            gas_key_nonce_count += 1;
            gas_key_nonce_total_key_bytes += raw_key.len();
            keys_to_remove.push(TrieKey::gas_key_nonce(
                account_id.clone(),
                key_handle.clone(),
                index,
            ));
        } else {
            keys_to_remove.push(TrieKey::access_key(account_id.clone(), key_handle.clone()));
        }
    }
    drop(lock);

    for trie_key in keys_to_remove {
        state_update.remove(trie_key);
    }

    // Removing contract data
    let lock = state_update.trie().lock_for_iter();
    let data_keys = state_update
        .locked_iter(&trie_key_parsers::get_raw_prefix_for_contract_data(account_id, &[]), &lock)?
        .map(|raw_key| {
            trie_key_parsers::parse_data_key_from_contract_data_key(&raw_key?, account_id)
                .map_err(|_e| {
                    StorageError::StorageInconsistentState(
                        "Can't parse data key from raw key for ContractData".to_string(),
                    )
                })
                .map(Vec::from)
        })
        .collect::<Result<Vec<_>, _>>()?;
    drop(lock);

    for key in data_keys {
        state_update.remove(TrieKey::ContractData { account_id: account_id.clone(), key });
    }
    Ok(RemoveAccountResult { gas_key_nonce_count, gas_key_nonce_total_key_bytes })
```

**File:** core/parameters/src/snapshots/near_parameters__config_store__tests__156.json.snap (L84-88)
```text
      "delete_account_cost": {
        "send_sir": 147489000000,
        "send_not_sir": 147489000000,
        "execution": 147489000000
      },
```

**File:** runtime/runtime-params-estimator/src/lib.rs (L476-495)
```rust
fn action_delete_account(ctx: &mut EstimatorContext) -> GasCost {
    let total_cost = {
        let mut make_transaction = |tb: &mut TransactionBuilder| -> SignedTransaction {
            let sender = tb.random_unused_account();
            let receiver = sender.clone();
            let beneficiary_id = tb.random_unused_account();

            let actions = vec![Action::DeleteAccount(DeleteAccountAction { beneficiary_id })];
            tb.transaction_from_actions(sender, receiver, actions)
        };
        let block_size = 100;
        // Deleting an account is initiated by an account that potentially is on a different shard. Thus, it executes over two blocks.
        let block_latency = 1;
        transaction_cost_ext(ctx, block_size, &mut make_transaction, block_latency).0
    };

    let base_cost = action_sir_receipt_creation(ctx);

    total_cost.saturating_sub(&base_cost, &NonNegativeTolerance::PER_MILLE)
}
```

**File:** docs/RuntimeSpec/Fees/Fees.md (L71-77)
```markdown
- [DeleteKey](/RuntimeSpec/Actions.md#deletekeyaction) uses
  - the base fee [`delete_key_cost`](/GenesisConfig/RuntimeFeeConfig/ActionCreationConfig.md#delete_key_cost)
- [DeleteAccount](/RuntimeSpec/Actions.md#deleteaccountaction) uses
  - the base fee [`delete_account_cost`](/GenesisConfig/RuntimeFeeConfig/ActionCreationConfig.md#delete_account_cost)
  - action receipt creation fee for creating Transfer to send remaining funds to `beneficiary_id`
  - full transfer fee described in the corresponding item
    
```
