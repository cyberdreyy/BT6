[1](#0-0) [2](#0-1) [1](#0-0) [1](#0-0) [2](#0-1)

### Citations

**File:** crates/sui-types/src/transaction.rs (L3742-3746)
```rust
    pub fn new(tx_data: TransactionData, tx_signatures: Vec<GenericSignature>) -> Self {
        Self(SizeOneVec::new(SenderSignedTransaction {
            intent_message: IntentMessage::new(Intent::sui_transaction(), tx_data),
            tx_signatures,
        }))
```

**File:** crates/sui-types/src/crypto.rs (L775-778)
```rust
        let mut hasher = DefaultHash::default();
        bcs::serialize_into(&mut hasher, &value).expect("Message serialization should not fail");

        Signer::sign(secret, &hasher.finalize().digest)
```
