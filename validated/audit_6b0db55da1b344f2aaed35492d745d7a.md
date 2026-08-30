### Title
`gas_key_storage_cost` undercounts `TrieKey::GasKeyNonce` byte length by `(account_id.len() + 2) * num_nonces`, letting gas-key holders under-pay storage stake - ([File: runtime/runtime/src/access_keys.rs])

### Summary
`gas_key_storage_cost` computes the storage-usage delta charged for each gas-key nonce record using `per_nonce_key_size = public_key.trie_id_len() + size_of::<NonceIndex>()`, but the actual trie key written by `set_gas_key_nonce` is `TrieKey::GasKeyNonce`, whose real length (`gas_key_nonce_key_len`) additionally includes `col::ACCESS_KEY.len() + account_id.len() + ACCESS_KEY_SEPARATOR.len()`. This causes `account.storage_usage()` to be increased by `(account_id.len() + 2) * num_nonces` bytes less than the bytes actually committed to the trie.

### Finding Description
`gas_key_storage_cost` in [1](#0-0)  computes:
```
per_nonce_key_size = public_key.trie_id_len() + size_of::<NonceIndex>()
```
and multiplies this by `num_nonces`, added to `access_key_storage_usage`.

The actual on-trie key for each nonce is `TrieKey::GasKeyNonce { account_id, key_handle, index }`, whose length is computed in `TrieKey::len()` via `gas_key_nonce_key_len`: [2](#0-1)  which equals `access_key_key_len(account_id.len(), key_handle.trie_id_len()) + size_of::<NonceIndex>()`, i.e. `col::ACCESS_KEY.len() (1) + account_id.len() + ACCESS_KEY_SEPARATOR.len() (1) + trie_id_len + size_of::<NonceIndex>()`, as confirmed by the byte layout in `append_into` for `GasKeyNonce`: [3](#0-2) .

`add_gas_key` writes `num_nonces` `TrieKey::GasKeyNonce` rows via `set_gas_key_nonce` and then increments `account.storage_usage()` by `gas_key_storage_cost(...)`: [4](#0-3) . Because `gas_key_storage_cost`'s per-nonce key size omits `account_id.len() + 2` bytes (the `col::ACCESS_KEY` column byte + the account id + the `ACCESS_KEY_SEPARATOR` byte, all of which are written for every nonce row, not just once for the whole key), the storage-usage credit given for `num_nonces` nonce rows is short by `(account_id.len() + 2) * num_nonces` bytes versus the real bytes persisted in the trie.

An attacker only needs to submit an ordinary `AddKey` action with `AccessKeyPermission::GasKeyFullAccess(GasKeyInfo { num_nonces, .. })` (via `AccessKey::gas_key_full_access(num_nonces)`, exercised in the existing test `add_gas_key_to_account`: [5](#0-4) ). No special privilege, validator access, or contract deployment is required - it is a standard signed transaction against a self-owned account. Using a maximally long (64-byte) `account_id` and the runtime-permitted maximum `num_nonces`, the shortfall scales linearly with `num_nonces`.

No other check catches this: there is no separate reconciliation between `account.storage_usage()` and the sum of real trie key lengths; storage staking (`account.amount() >= storage_usage * storage_byte_cost`) is evaluated purely off the (undercounted) `storage_usage` field.

### Impact Explanation
This is a fee-payment bypass / storage-stake underpayment: the attacker's account is charged for `num_nonces * (account_id.len() + 2)` fewer bytes than are actually written to state, allowing them to hold more on-chain state than their locked balance is supposed to cover for the same NEAR deposit. This matches the "Fee payment bypass" scoped impact in the prompt and falls into the funds/inflation category insofar as unpaid state growth is an economic loss to the network (state that is not backed by the storage stake it should require).

Note, however, that the identical arithmetic gap already exists in `access_key_storage_usage` for ordinary (non-gas) access keys - `public_key.trie_id_len() + object_length(access_key) + num_extra_bytes_record` also omits `col::ACCESS_KEY.len() + account_id.len() + ACCESS_KEY_SEPARATOR.len()`, relying on the flat `num_extra_bytes_record` constant as an approximation rather than an exact accounting of `account_id.len()`. What the `GasKeyNonce` change does is multiply that same pre-existing approximation gap by `num_nonces`, turning a single fixed-size approximation error into one that scales with the number of nonces requested, which is new incremental exposure introduced by the gas-key feature.

### Likelihood Explanation
- Preconditions: any funded account, a 64-byte account id (permitted account-id length), and `num_nonces` at whatever maximum the runtime currently permits for `GasKeyInfo::num_nonces` at `PROTOCOL_VERSION` 87.
- Cost to attacker: only the cost of one `AddKey` transaction; no special access needed.
- Feasibility/repeatability: fully reproducible and repeatable per account/key; the effect is deterministic and can be verified with a unit test comparing `gas_key_storage_cost`'s implied per-nonce size against `TrieKey::GasKeyNonce{..}.len()`.
- I could not verify from the code shown the exact maximum permitted value of `num_nonces` at protocol version 87 (the validation/config constant bounding `GasKeyInfo::num_nonces` was not located in the reviewed excerpts), so the precise magnitude of exploitable byte-shortfall per account is not fully confirmed here.

### Recommendation
Change `gas_key_storage_cost`'s `per_nonce_key_size` to use the real trie key length, e.g. call `near_primitives::trie_key::gas_key_nonce_key_len(account_id, &public_key.into())` (already imported and used elsewhere in this file, e.g. `runtime/runtime/src/access_keys.rs:118`) instead of manually recomputing `public_key.trie_id_len() + size_of::<NonceIndex>()`, so that the charged storage usage always matches `TrieKey::GasKeyNonce::len()` exactly.

### Proof of Concept
Add a unit test in `runtime/runtime/src/access_keys.rs` (or `core/primitives/src/trie_key.rs`) that:
1. Constructs a 64-byte `AccountId` and a `PublicKey`/`PublicKeyHandle`.
2. Computes `real_len = gas_key_nonce_key_len(&account_id, &key_handle)`.
3. Computes `charged_len = public_key.trie_id_len() + size_of::<NonceIndex>()` (mirroring `gas_key_storage_cost`'s `per_nonce_key_size`).
4. Asserts `real_len - charged_len == account_id.len() + 2` (i.e. `col::ACCESS_KEY.len() + ACCESS_KEY_SEPARATOR.len() + account_id.len()`).
5. For a chosen `num_nonces` (e.g. the runtime max), asserts that `gas_key_storage_cost(...)` differs from the sum of actual `TrieKey::GasKeyNonce{account_id, key_handle, index}.len()` + `AccessKey::NONCE_VALUE_LEN` over all `num_nonces` entries by exactly `(account_id.len() + 2) * num_nonces`, demonstrating the storage-usage shortfall recorded by `add_gas_key`.

### Citations

**File:** runtime/runtime/src/access_keys.rs (L31-44)
```rust
fn gas_key_storage_cost(
    fee_config: &RuntimeFeesConfig,
    public_key: &PublicKey,
    access_key: &AccessKey,
    num_nonces: NonceIndex,
) -> StorageUsage {
    let storage_config = &fee_config.storage_usage_config;
    let per_nonce_value_size = borsh::object_length(&(0 as Nonce)).unwrap() as u64;
    let per_nonce_key_size = public_key.trie_id_len() as u64 + size_of::<NonceIndex>() as u64;

    num_nonces as u64
        * (per_nonce_key_size + per_nonce_value_size + storage_config.num_extra_bytes_record)
        + access_key_storage_usage(fee_config, public_key, access_key)
}
```

**File:** runtime/runtime/src/access_keys.rs (L209-226)
```rust
    // Set up nonces for gas key
    let num_nonces = gas_key_info.num_nonces;
    let nonce = initial_nonce_value(block_height);
    for i in 0..num_nonces {
        set_gas_key_nonce(state_update, account_id.clone(), public_key.clone(), i, nonce);
    }

    account.set_storage_usage(
        account
            .storage_usage()
            .checked_add(gas_key_storage_cost(fee_config, public_key, &access_key, num_nonces))
            .ok_or_else(|| {
                StorageError::StorageInconsistentState(format!(
                    "Storage usage integer overflow for account {}",
                    account_id
                ))
            })?,
    );
```

**File:** runtime/runtime/src/access_keys.rs (L421-440)
```rust
    fn add_gas_key_to_account(
        state_update: &mut TrieUpdate,
        account: &mut Account,
        account_id: &AccountId,
        public_key: &PublicKey,
    ) -> AccessKey {
        let mut result = ActionResult::default();
        let apply_state = create_apply_state(TEST_GAS_KEY_BLOCK_HEIGHT);
        let action = AddKeyAction {
            public_key: public_key.clone(),
            access_key: AccessKey::gas_key_full_access(TEST_NUM_NONCES),
        };
        action_add_key(&apply_state, state_update, account, &mut result, account_id, &action)
            .unwrap();
        assert!(result.result.is_ok(), "result error: {:?}", result.result);

        get_access_key(state_update, account_id, public_key)
            .expect("could not find gas key")
            .unwrap()
    }
```

**File:** core/primitives/src/trie_key.rs (L323-326)
```rust
/// Returns the length of the trie key for a gas key nonce.
pub fn gas_key_nonce_key_len(account_id: &AccountId, key_handle: &PublicKeyHandle) -> usize {
    access_key_key_len(account_id.len(), key_handle.trie_id_len()) + size_of::<NonceIndex>()
}
```

**File:** core/primitives/src/trie_key.rs (L550-556)
```rust
            TrieKey::GasKeyNonce { account_id, key_handle, index: nonce_index } => {
                buf.push(col::ACCESS_KEY);
                buf.extend(account_id.as_bytes());
                buf.push(ACCESS_KEY_SEPARATOR);
                append_key_handle_trie_id(buf, key_handle);
                buf.extend(&nonce_index.to_le_bytes());
            }
```
