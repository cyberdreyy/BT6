[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** runtime/runtime/src/verifier.rs (L269-277)
```rust
pub fn verify_and_charge_tx_ephemeral(
    config: &RuntimeConfig,
    account: &Account,
    access_key: &AccessKey,
    tx: &Transaction,
    transaction_cost: &TransactionCost,
    block_height: Option<BlockHeight>,
    pending: &PendingConstraints,
) -> TxVerdict {
```

**File:** runtime/runtime/src/verifier.rs (L278-290)
```rust
    // It's the caller's responsibility to NOT call this function for transactions with
    // nonce_index (i.e. gas key transactions).
    assert!(
        tx.nonce().nonce_index().is_none(),
        "verify_and_charge_tx_ephemeral called for gas key transaction"
    );
    // Gas keys must be used via gas key transaction path (with nonce_index)
    if let Some(gas_key_info) = access_key.gas_key_info() {
        return TxVerdict::Failed(InvalidTxError::InvalidNonceIndex {
            tx_nonce_index: None,
            num_nonces: gas_key_info.num_nonces,
        });
    }
```

**File:** runtime/runtime/src/verifier.rs (L380-384)
```rust
    // It's the caller's responsibility to ONLY call this function for transactions with
    // nonce_index (i.e. gas key transactions).
    let Some(nonce_index) = tx.nonce().nonce_index() else {
        panic!("verify_and_charge_gas_key_tx_ephemeral called for non-gas key transaction")
    };
```
