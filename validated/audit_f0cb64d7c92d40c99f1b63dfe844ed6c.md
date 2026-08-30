[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** runtime/runtime/src/access_keys.rs (L17-29)
```rust
fn access_key_storage_usage(
    fee_config: &RuntimeFeesConfig,
    public_key: &PublicKey,
    access_key: &AccessKey,
) -> StorageUsage {
    let storage_usage_config = &fee_config.storage_usage_config;
    // Use the on-trie identifier length, not the borsh-serialized pubkey
    // length: ML-DSA-65 access keys live in the trie as a SHA3-256 hash
    // (33 bytes incl. type tag), not as a 1953-byte full pubkey.
    public_key.trie_id_len() as u64
        + borsh::object_length(access_key).unwrap() as u64
        + storage_usage_config.num_extra_bytes_record
}
```

**File:** runtime/runtime/src/access_keys.rs (L60-81)
```rust
    let access_key = get_access_key(state_update, account_id, &delete_key.public_key)?;
    if let Some(access_key) = access_key {
        if let Some(gas_key_info) = access_key.gas_key_info() {
            delete_gas_key(
                config,
                state_update,
                account,
                result,
                account_id,
                &delete_key.public_key,
                &access_key,
                gas_key_info,
            )?;
        } else {
            delete_regular_key(
                &config.fees,
                state_update,
                account,
                account_id,
                &delete_key.public_key,
                &access_key,
            );
```

**File:** runtime/runtime/src/access_keys.rs (L136-147)
```rust
fn delete_regular_key(
    fee_config: &RuntimeFeesConfig,
    state_update: &mut TrieUpdate,
    account: &mut Account,
    account_id: &AccountId,
    public_key: &PublicKey,
    access_key: &AccessKey,
) {
    let storage_usage = access_key_storage_usage(fee_config, public_key, access_key);
    remove_access_key(state_update, account_id.clone(), public_key.clone());
    account.set_storage_usage(account.storage_usage().saturating_sub(storage_usage));
}
```
