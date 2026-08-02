[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/mvhashmap/src/versioned_delayed_fields.rs (L225-253)
```rust
    fn read_latest_predicted_value(
        &self,
        next_idx_to_commit: TxnIndex,
    ) -> Result<DelayedFieldValue, MVDelayedFieldsError> {
        use VersionEntry::*;

        self.versioned_map
            .range(0..next_idx_to_commit)
            .next_back()
            .map_or_else(
                || match &self.base_value {
                    Some(value) => Ok(value.clone()),
                    None => match self.versioned_map.first_key_value() {
                        Some((_, entry)) => match entry.as_ref().deref() {
                            Value(v, _) => Ok(v.clone()),
                            Apply(_) | Estimate(_) => Err(MVDelayedFieldsError::NotFound),
                        },
                        None => Err(MVDelayedFieldsError::NotFound),
                    },
                },
                |(_, entry)| match entry.as_ref().deref() {
                    Value(v, _) => Ok(v.clone()),
                    Apply(_) => {
                        unreachable!("Apply entries may not exist for committed txn indices")
                    },
                    Estimate(_) => unreachable!("Committed entry may not be an Estimate"),
                },
            )
    }
```

**File:** aptos-move/mvhashmap/src/versioned_delayed_fields.rs (L548-560)
```rust
    pub fn try_commit(
        &self,
        idx_to_commit: TxnIndex,
        ids_iter: impl Iterator<Item = K>,
    ) -> Result<(), CommitError> {
        // we may not need to return values here, we can just read them.
        use DelayedApplyEntry::*;

        if idx_to_commit != self.next_idx_to_commit.load(Ordering::SeqCst) {
            return Err(CommitError::CodeInvariantError(
                "idx_to_commit must be next_idx_to_commit".to_string(),
            ));
        }
```

**File:** aptos-move/mvhashmap/src/versioned_delayed_fields.rs (L580-582)
```rust
                VersionEntry::Apply(AggregatorDelta { delta }) => {
                    let prev_value = versioned_value.read_latest_predicted_value(idx_to_commit)
                        .map_err(|e| CommitError::CodeInvariantError(format!("Cannot read latest committed value for Apply(AggregatorDelta) during commit: {:?}", e)))?;
```
