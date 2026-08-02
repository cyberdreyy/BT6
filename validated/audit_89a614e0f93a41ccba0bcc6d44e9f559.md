[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/mvhashmap/src/versioned_delayed_fields.rs (L453-471)
```rust
    /// Must be called when an delayed field creation with a given ID and initial value is
    /// observed in the outputs of txn_idx.
    pub fn initialize_delayed_field(
        &self,
        id: K,
        txn_idx: TxnIndex,
        value: DelayedFieldValue,
    ) -> Result<(), PanicError> {
        let mut created = VersionedValue::new(None);
        created.insert_speculative_value(txn_idx, VersionEntry::Value(value, None))?;

        if self.values.insert(id, created).is_some() {
            Err(code_invariant_error(
                "VersionedValue when initializing delayed field may not already exist for same id",
            ))
        } else {
            Ok(())
        }
    }
```

**File:** aptos-move/mvhashmap/src/versioned_delayed_fields.rs (L654-677)
```rust
        for (id, base_snapshot, formula) in todo_derived {
            let new_entry = {
                let prev_value = self.values
                    .get_mut(&base_snapshot)
                    .ok_or_else(|| CommitError::CodeInvariantError("Cannot find base_aggregator for Apply(SnapshotDelta) during commit".to_string()))?
                    // Read values committed in this commit
                    .read_latest_predicted_value(idx_to_commit + 1)
                    .map_err(|e| CommitError::CodeInvariantError(format!("Cannot read latest committed value for base aggregator for ApplySnapshotDelta) during commit: {:?}", e)))?;

                if let DelayedFieldValue::Snapshot(base) = prev_value {
                    let new_value = formula.apply_to(base);
                    DelayedFieldValue::Derived(new_value)
                } else {
                    return Err(CommitError::CodeInvariantError(
                        "Cannot apply delta to non-DelayedField::Aggregator".to_string(),
                    ));
                }
            };

            let mut versioned_value = self
                .values
                .get_mut(&id)
                .expect("Value in commit needs to be in the HashMap");
            versioned_value.insert_final_value(idx_to_commit, new_entry);
```

**File:** aptos-move/mvhashmap/src/versioned_delayed_fields.rs (L938-1000)
```rust
    #[should_panic]
    #[test_case(NO_ENTRY)]
    #[test_case(VALUE_AGGREGATOR)]
    #[test_case(VALUE_SNAPSHOT)]
    #[test_case(VALUE_DERIVED)]
    #[test_case(APPLY_AGGREGATOR)]
    #[test_case(APPLY_SNAPSHOT)]
    #[test_case(APPLY_DERIVED)]
    #[test_case(ESTIMATE_NO_BYPASS)]
    // Insert all possible entries at a wrong txn_idx, ensure mark_estimate panics.
    fn mark_estimate_no_entry(type_index: usize) {
        let mut v = VersionedValue::new(None);
        if let Some(entry) = aggregator_entry(type_index) {
            v.insert_speculative_value(10, entry).unwrap();
        }
        if let Some(entry) = aggregator_entry(type_index) {
            v.insert_speculative_value(3, entry).unwrap();
        }
        v.mark_estimate(5);
    }

    #[should_panic]
    // Inserting estimates isn't allowed, must use mark_estimate.
    #[test]
    fn insert_estimate() {
        let mut v = VersionedValue::new(None);
        v.insert_speculative_value(3, aggregator_entry(ESTIMATE_NO_BYPASS).unwrap())
            .unwrap();
    }

    #[test]
    fn estimate_bypass() {
        let mut v = VersionedValue::new(None);
        v.insert_speculative_value(2, aggregator_entry(VALUE_AGGREGATOR).unwrap())
            .unwrap();
        v.insert_speculative_value(
            3,
            aggregator_entry_aggregator_value_and_delta(15, test_delta()),
        )
        .unwrap();
        v.insert_speculative_value(4, aggregator_entry(APPLY_AGGREGATOR).unwrap())
            .unwrap();
        v.insert_speculative_value(
            10,
            aggregator_entry_aggregator_value_and_delta(15, test_delta()),
        )
        .unwrap();

        // Delta + Value(15)
        assert_read_aggregator_value!(v.read(5), 45);

        v.mark_estimate(3);
        let val_bypass = v.versioned_map.get(&3);
        assert_some!(val_bypass);
        assert_matches!(
            val_bypass.unwrap().as_ref().deref(),
            VersionEntry::Estimate(EstimatedEntry::Bypass(
                DelayedApplyEntry::AggregatorDelta { .. }
            ))
        );
        // Delta(30) + Value delta bypass(30) + Value(10)
        assert_read_aggregator_value!(v.read(5), 70);

```
