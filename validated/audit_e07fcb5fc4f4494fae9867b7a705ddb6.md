This confirms the analysis. The `RawTrieNode::Leaf`/`BranchWithValue` variants store a `ValueRef { length, hash }` where `length` is computed via `ValueRef::new(value)` as `value.len() as u32` — the exact same raw byte length that was passed into `storage_write`/`storage_remove`. `FlatStateValue::should_inline`/`INLINE_DISK_VALUE_THRESHOLD` only decides whether flat storage stores the value bytes inline or as a `ValueRef` for lookup-performance purposes; it does not alter the value's length or how it is hashed/committed into the trie.

### Title
No vulnerability found for this question.

### Summary
The claimed discrepancy does not exist: `ValueRef.length` embedded in `RawTrieNode` (via `ValueRef::new`) is always `value.len()` of the exact bytes passed to `storage_write`/`storage_remove`, matching the accounting increment/decrement in `runtime/near-vm-runner/src/logic/logic.rs`. The `INLINE_DISK_VALUE_THRESHOLD`/`FlatStateValue::should_inline` mechanism only changes whether the value is inlined in flat storage or referenced by hash for read-path performance — it does not change the value length recorded in the trie or the storage-usage accounting.

### Finding Description
`storage_write` reads `value` and `key` from guest memory/registers and passes them unchanged to `self.ext.storage_set` [1](#0-0) , then adjusts `current_storage_usage` by `value.len() as u64 + key.len() as u64 + num_extra_bytes_record` [2](#0-1) . `storage_remove` mirrors this with a subtraction using the removed value's actual length [3](#0-2) .

On the trie side, `ValueRef::new(value)` sets `length: value.len() as u32` and `hash: hash(value)` [4](#0-3) , and this is exactly what gets embedded in `RawTrieNode::Leaf`/`BranchWithValue` and hashed into `RawTrieNodeWithSize::hash()` [5](#0-4) . `FlatStateValue::on_disk` and `should_inline` only pick between `FlatStateValue::Inlined(value.to_vec())` and `FlatStateValue::Ref(ValueRef::new(value))` [6](#0-5) , but `to_value_ref()` computes `ValueRef::new(value)` off the identical bytes in either branch, so `length` is unaffected by which representation is chosen. The threshold only affects flat-storage lookup performance (whether an extra disk read of `DBCol::State` is needed), not the number of bytes hashed into consensus state or charged for storage staking.

### Impact Explanation
None — the premise (that persisted trie footprint length can differ from `value.len()` at write time) is false for this codebase; `ValueRef.length` is always derived from the same raw bytes charged in `storage_write`/`storage_remove`.

### Likelihood Explanation
Not applicable — there is no code path where the inline/reference decision changes the byte length used for the `ValueRef`.

### Recommendation
No fix required.

### Proof of Concept
Not applicable.

### Citations

**File:** runtime/near-vm-runner/src/logic/logic.rs (L4326-4336)
```rust
        let value = get_memory_or_register!(self, value_ptr, value_len)?;
        if value.len() as u64 > self.config.limit_config.max_length_storage_value {
            return Err(HostError::ValueLengthExceeded {
                length: value.len() as u64,
                limit: self.config.limit_config.max_length_storage_value,
            }
            .into());
        }
        self.result_state.gas_counter.pay_per(storage_write_key_byte, key.len() as u64)?;
        self.result_state.gas_counter.pay_per(storage_write_value_byte, value.len() as u64)?;
        let evicted = self.ext.storage_set(&mut self.result_state.gas_counter, &key, &value)?;
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L4361-4374)
```rust
            None => {
                // Inner value can't overflow, because the key/value length is limited.
                self.result_state.current_storage_usage = self
                    .result_state
                    .current_storage_usage
                    .checked_add(
                        value.len() as u64
                            + key.len() as u64
                            + storage_config.num_extra_bytes_record,
                    )
                    .ok_or(InconsistentStateError::IntegerOverflow)?;
                Ok(0)
            }
        }
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L4477-4488)
```rust
        match removed {
            Some(value) => {
                // Inner value can't overflow, because the key/value length is limited.
                self.result_state.current_storage_usage = self
                    .result_state
                    .current_storage_usage
                    .checked_sub(
                        value.len() as u64
                            + key.len() as u64
                            + storage_config.num_extra_bytes_record,
                    )
                    .ok_or(InconsistentStateError::IntegerOverflow)?;
```

**File:** core/primitives/src/state.rs (L53-59)
```rust
impl ValueRef {
    /// Create serialized value reference by the value.
    /// Resulting array stores 4 bytes of length and then 32 bytes of hash.
    /// TODO (#7327): consider passing hash here to avoid double computation
    pub fn new(value: &[u8]) -> Self {
        Self { length: value.len() as u32, hash: hash(value) }
    }
```

**File:** core/primitives/src/state.rs (L112-151)
```rust
impl FlatStateValue {
    pub const INLINE_DISK_VALUE_THRESHOLD: usize =
        near_primitives_core::config::INLINE_DISK_VALUE_THRESHOLD;

    pub fn on_disk(value: &[u8]) -> Self {
        if Self::should_inline(value.len()) { Self::inlined(value) } else { Self::value_ref(value) }
    }

    pub fn value_ref(value: &[u8]) -> Self {
        Self::Ref(ValueRef::new(value))
    }

    pub fn inlined(value: &[u8]) -> Self {
        Self::Inlined(value.to_vec())
    }

    pub fn to_value_ref(&self) -> ValueRef {
        match self {
            Self::Ref(value_ref) => *value_ref,
            Self::Inlined(value) => ValueRef::new(value),
        }
    }

    pub fn value_len(&self) -> usize {
        match self {
            Self::Ref(value_ref) => value_ref.len(),
            Self::Inlined(value) => value.len(),
        }
    }

    pub fn size(&self) -> usize {
        match self {
            Self::Ref(_) => size_of::<Self>(),
            Self::Inlined(value) => size_of::<Self>() + value.capacity(),
        }
    }

    pub fn should_inline(value_len: usize) -> bool {
        value_len <= Self::INLINE_DISK_VALUE_THRESHOLD
    }
```

**File:** core/store/src/trie/raw_node.rs (L16-36)
```rust
impl RawTrieNodeWithSize {
    pub fn hash(&self) -> CryptoHash {
        CryptoHash::hash_bytes(&borsh::to_vec(self).unwrap())
    }
}

/// Trie node.
#[derive(BorshSerialize, BorshDeserialize, Clone, Debug, PartialEq, Eq, ProtocolSchema)]
#[allow(clippy::large_enum_variant)]
#[borsh(use_discriminant = true)]
#[repr(u8)]
pub enum RawTrieNode {
    /// Leaf(key, value_length, value_hash)
    Leaf(Vec<u8>, ValueRef) = 0,
    /// Branch(children)
    BranchNoValue(Children) = 1,
    /// Branch(children, value)
    BranchWithValue(ValueRef, Children) = 2,
    /// Extension(key, child)
    Extension(Vec<u8>, CryptoHash) = 3,
}
```
