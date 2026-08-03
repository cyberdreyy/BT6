No vulnerability found for this question.

**Rationale:**

`AptosDB::get_persisted_state` returns the latest fully-committed `(HotStateView, State)` snapshot maintained by `PersistedState`/`StateStore`, which is advanced only via `PersistedState::set` after a transaction/chunk has been fully applied and merklized as a complete `StateWithSummary` at a specific version [1](#0-0) , called through `AptosDB`'s reader implementation which simply forwards to `state_store.get_persisted_state()` [2](#0-1)  and `StateStore`'s own `DbReader::get_persisted_state` [3](#0-2) .

There is no mechanism by which a caller can observe an intermediate state where one write from a transaction's write set is applied but another write from the same transaction is not: Move VM transaction execution produces a single atomic `WriteSet` per transaction, and storage commits (via `ChunkToCommit`/`save_transactions`) apply that write set as one unit to advance the persisted version, as seen in the test-only commit path that builds a `new_state` from the full `state_update_refs` before calling `save_transactions` [4](#0-3) . `get_persisted_state` can only return a snapshot at a completed version boundary — never a partially-applied one within a transaction.

Separately, on the Move logic side, the commission-record write (`request_commission`/`distribute_internal`) and the beneficiary-update write (`set_beneficiary_for_operator`) are distinct entry functions, each independently role-gated (operator-only for beneficiary changes) [5](#0-4) , and distribution always resolves the beneficiary by reading `beneficiary_for_operator(operator)` at the time funds are actually paid out, not at the time commission was recorded [6](#0-5) . This means even across separate transactions there is no window where recorded commission is paid to a stale beneficiary — the current beneficiary is looked up fresh at distribution time.

The premise of the question (a storage-layer race exposing a torn intermediate state via `get_persisted_state`) does not correspond to any real code path in this codebase, and the described scenario does not identify any unprivileged actor gaining control over funds they don't already have rights to — it assumes a storage atomicity violation that the architecture does not exhibit.

### Citations

**File:** storage/aptosdb/src/state_store/persisted_state.rs (L59-75)
```rust
    pub fn get_state(&self) -> (Arc<dyn HotStateView>, State) {
        self.hot_state.get_committed()
    }

    pub fn set(&self, persisted: StateWithSummary) {
        let (state, summary) = persisted.into_inner();

        // n.b. Summary must be updated before committing the hot state, otherwise in the execution
        // pipeline we risk having a state generated based on a persisted version (v2) that's newer
        // than that of the summary (v1). That causes issue down the line where we commit the diffs
        // between a later snapshot (v3) and a persisted snapshot (v1) to the JMT, at which point
        // we will not be able to calculate the difference (v1 - v3) because the state links only
        // to as far as v2 (code will panic)
        *self.summary.lock() = summary;

        self.hot_state.enqueue_commit(state);
    }
```

**File:** storage/aptosdb/src/db/aptosdb_reader.rs (L55-59)
```rust
    fn get_persisted_state(&self) -> Result<(Arc<dyn HotStateView>, State)> {
        gauged_api("get_persisted_state", || {
            self.state_store.get_persisted_state()
        })
    }
```

**File:** storage/aptosdb/src/state_store/mod.rs (L308-311)
```rust
impl DbReader for StateStore {
    fn get_persisted_state(&self) -> Result<(Arc<dyn HotStateView>, State)> {
        Ok(self.persisted_state.get_state())
    }
```

**File:** storage/aptosdb/src/db/aptosdb_testonly.rs (L134-164)
```rust
        let current = self.state_store.current_state_locked().clone();
        let (hot_state, persisted_state) = self.state_store.get_persisted_state()?;
        let (new_state, reads, hot_state_updates) = current.ledger_state().update_with_db_reader(
            &persisted_state,
            hot_state,
            transactions_to_keep.state_update_refs(),
            self.state_store.clone(),
        )?;
        let persisted_summary = self.state_store.get_persisted_state_summary()?;
        let new_state_summary = current.ledger_state_summary().update(
            &ProvableStateSummary::new(persisted_summary, self),
            &hot_state_updates,
            transactions_to_keep.state_update_refs(),
        )?;

        let chunk = ChunkToCommit {
            first_version,
            transactions: &transactions_to_keep.transactions,
            persisted_auxiliary_infos: &transactions_to_keep.persisted_auxiliary_infos,
            transaction_outputs: &transactions_to_keep.transaction_outputs,
            transaction_infos: &transaction_infos,
            state: &new_state,
            state_summary: &new_state_summary,
            state_update_refs: transactions_to_keep.state_update_refs(),
            state_reads: &reads,
            hot_state_updates: &hot_state_updates,
            position_state_summary: None,
            is_reconfig: transactions_to_keep.is_reconfig(),
        };

        self.save_transactions(chunk, ledger_info_with_sigs, sync_commit)
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L810-838)
```text
    public entry fun set_beneficiary_for_operator(
        operator: &signer, new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        assert!(
            features::operator_beneficiary_change_enabled(),
            std::error::invalid_state(EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED)
        );
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator =
                new_beneficiary;
        } else {
            move_to(
                operator,
                BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary }
            );
        };

        emit(
            SetBeneficiaryForOperator {
                operator: operator_addr,
                old_beneficiary,
                new_beneficiary
            }
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L895-900)
```text
            // If the recipient is the operator, send the commission to the beneficiary instead.
            if (recipient == operator) {
                recipient = beneficiary_for_operator(operator);
            };
            aptos_account::deposit_coins(
                recipient, coin::extract(&mut coins, amount_to_distribute)
```
