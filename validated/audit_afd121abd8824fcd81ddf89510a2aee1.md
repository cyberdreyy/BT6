Based on my investigation, I found a genuine analog: `From<BaseTypedTransaction> for BaseTransactionRequest` and `From<BaseTxEnvelope> for BaseTransactionRequest` both `unimplemented!()`-panic on the `Eip8130` variant, exactly mirroring the OpenQ bug class of "a function claims to work generically across all variants of a type but only actually works for a subset, and hits a hard failure on the unhandled variant."

### Title
Panic on EIP-8130 transactions in `BaseTypedTransaction`/`BaseTxEnvelope` → `BaseTransactionRequest`/`TransactionRequest` conversions - ([File: crates/common/rpc-types/src/transaction/request.rs], [File: crates/common/consensus/src/transaction/typed.rs])

### Summary
`impl From<BaseTypedTransaction> for BaseTransactionRequest` and `impl From<BaseTxEnvelope> for BaseTransactionRequest` in `crates/common/rpc-types/src/transaction/request.rs` are meant to convert any Base transaction type into the generic RPC `TransactionRequest`/`BaseTransactionRequest` shape, exactly like `impl From<BaseTypedTransaction> for alloy_rpc_types_eth::TransactionRequest` in `crates/common/consensus/src/transaction/typed.rs`. All three `match` blocks claim to cover every `BaseTypedTransaction`/`BaseTxEnvelope` variant, but the `Eip8130` arm calls `unimplemented!()` instead of returning a value. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
This is functionally the same defect class as the OpenQ `solvent()` bug: a function's signature/spec (an exhaustive `match` implementing a trait for "any transaction type") promises support for every variant of an enum, but one variant (`Eip8130`, the EIP-8130 account-abstraction transaction type) is unsupported and instead of returning an error or a best-effort projection, the code panics via `unimplemented!()`. Any code path that converts a `BaseTxEnvelope`/`BaseTypedTransaction` carrying an `Eip8130` payload into a `BaseTransactionRequest` will crash the calling thread. Because EIP-8130 transactions are a first-class, consensus-valid transaction type on Base (used throughout `crates/common/evm/src/eip8130.rs`, `crates/execution/txpool`, etc.), an attacker can trivially get such a transaction included in a block or the mempool, and any RPC/serialization code that generically converts arbitrary transactions (e.g., building "resend"/"replace" style requests, transaction-tracing endpoints, or any tooling that maps historical/pending transactions back into a request object) will panic when it encounters that valid transaction.

### Impact Explanation
If this conversion is reachable from a JSON-RPC handler (any endpoint that takes a decoded/pooled/mined transaction and re-projects it into `TransactionRequest`/`BaseTransactionRequest`, e.g. simulate/replace/trace helpers), a single crafted EIP-8130 transaction (submittable by any unprivileged account) is enough to panic the serving thread/process, denying the RPC endpoint to legitimate users — matching the required "RPC the node can no longer serve" impact bar. If the panic unwinds into a shared node process rather than a per-request task, it could also cause a broader node halt.

### Likelihood Explanation
EIP-8130 transactions are ordinary, permissionless L2 transactions (see `crates/common/evm/src/eip8130.rs`); no special privilege is required to submit one. The likelihood hinges on whether any RPC/query path in the deployed node actually calls `BaseTransactionRequest::from(BaseTxEnvelope)` / `From<BaseTypedTransaction> for BaseTransactionRequest` on attacker-supplied or attacker-mined transactions. I was not able to fully trace every call site of these `From` impls within the indexed portion of the repository to confirm a concrete reachable RPC method; the `crates/consensus/rpc/src/jsonrpsee.rs` file (which has many hits for related identifiers) could not be inspected in this pass due to tool-call budget, so likelihood should be validated by checking whether any `eth_*` handler or the `BaseTransactionRequest`/`TransactionBuilder` trait implementations feed live `Eip8130` transactions through this conversion.

### Recommendation
Replace the `unimplemented!()` arms with a fallible conversion (return `Result`/`Option`, or a best-effort projection using the fields EIP-8130 does have, e.g. `sender`, `payer`, `nonce_key`) instead of panicking, and audit every call site of these `From` impls to ensure none of them can be reached with an EIP-8130 envelope from an untrusted or attacker-controlled transaction.

### Proof of Concept
1. Submit (or have a block contain) a valid `TxEip8130`-typed transaction (`BaseTxEnvelope::Eip8130`), which any unprivileged account can construct per `crates/common/evm/src/eip8130.rs`.
2. Trigger any code path that converts that transaction/envelope into a `BaseTransactionRequest` (e.g. via the generic `From<BaseTxEnvelope> for BaseTransactionRequest` or `From<BaseTypedTransaction> for BaseTransactionRequest`), such as an RPC handler that echoes back or re-submits a pending/mined transaction as a request object.
3. The conversion hits the `Eip8130` arm and calls `unimplemented!()`, panicking the thread instead of returning a value, denying service for that request path. [4](#0-3) [5](#0-4)

### Citations

**File:** crates/common/rpc-types/src/transaction/request.rs (L412-425)
```rust
impl From<BaseTypedTransaction> for BaseTransactionRequest {
    fn from(tx: BaseTypedTransaction) -> Self {
        match tx {
            BaseTypedTransaction::Legacy(tx) => Into::<TransactionRequest>::into(tx).into(),
            BaseTypedTransaction::Eip2930(tx) => Into::<TransactionRequest>::into(tx).into(),
            BaseTypedTransaction::Eip1559(tx) => Into::<TransactionRequest>::into(tx).into(),
            BaseTypedTransaction::Eip7702(tx) => Into::<TransactionRequest>::into(tx).into(),
            BaseTypedTransaction::Eip8130(_) => unimplemented!(
                "BaseTypedTransaction::Eip8130 cannot be projected onto BaseTransactionRequest; AA transactions have no single sender/recipient/value"
            ),
            BaseTypedTransaction::Deposit(tx) => tx.into(),
        }
    }
}
```

**File:** crates/common/rpc-types/src/transaction/request.rs (L427-440)
```rust
impl From<BaseTxEnvelope> for BaseTransactionRequest {
    fn from(value: BaseTxEnvelope) -> Self {
        match value {
            BaseTxEnvelope::Legacy(tx) => tx.into(),
            BaseTxEnvelope::Eip2930(tx) => tx.into(),
            BaseTxEnvelope::Eip1559(tx) => tx.into(),
            BaseTxEnvelope::Eip7702(tx) => tx.into(),
            BaseTxEnvelope::Eip8130(_) => unimplemented!(
                "BaseTxEnvelope::Eip8130 cannot be projected onto BaseTransactionRequest; AA transactions have no single sender/recipient/value"
            ),
            BaseTxEnvelope::Deposit(tx) => tx.into(),
        }
    }
}
```

**File:** crates/common/consensus/src/transaction/typed.rs (L60-74)
```rust
#[cfg(feature = "alloy-compat")]
impl From<BaseTypedTransaction> for alloy_rpc_types_eth::TransactionRequest {
    fn from(tx: BaseTypedTransaction) -> Self {
        match tx {
            BaseTypedTransaction::Legacy(tx) => tx.into(),
            BaseTypedTransaction::Eip2930(tx) => tx.into(),
            BaseTypedTransaction::Eip1559(tx) => tx.into(),
            BaseTypedTransaction::Eip7702(tx) => tx.into(),
            BaseTypedTransaction::Eip8130(_) => unimplemented!(
                "BaseTypedTransaction::Eip8130 cannot be converted to an alloy TransactionRequest; AA transactions have no single sender/recipient/value to project into the legacy request shape"
            ),
            BaseTypedTransaction::Deposit(tx) => tx.into(),
        }
    }
}
```
