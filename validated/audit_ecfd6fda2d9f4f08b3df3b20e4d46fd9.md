## Finding

### Title
Sequencer HTTP transport errors leak the raw request URL (including any embedded API key/credentials) to anonymous `eth_sendRawTransaction` callers - ([File: crates/execution/rpc/src/error.rs])

### Summary
`BaseEthApiError::Sequencer` wraps `SequencerClientError`, which is converted directly into the JSON-RPC error object returned to the client that called `eth_sendRawTransaction`. When the sequencer forwarding request fails at the transport layer (timeout, connection refused, DNS failure, TLS error, etc.) rather than with a structured JSON-RPC error response, the fallback conversion arm serializes the raw `Display` output of the underlying transport error directly into the RPC error message sent back to the caller.

### Finding Description
`SequencerClientError` is a transparent wrapper around `RpcError<TransportErrorKind>`: [1](#0-0) 

Its conversion into a `jsonrpsee` `ErrorObject` only special-cases the `RpcError::ErrorResp` variant (a structured JSON-RPC error from the sequencer). Every other variant — including `RpcError::Transport(TransportErrorKind::...)`, which is what a connection failure produces — falls into the catch-all arm that does `err.to_string()` and returns it verbatim with `INTERNAL_ERROR_CODE`.

This error is produced when forwarding a raw transaction to the sequencer: [2](#0-1) 
which is called from the public `send_pool_transaction` handler backing `eth_sendRawTransaction`: [3](#0-2) 

The underlying HTTP/transport error `Display` implementation used by this stack embeds the *full request URL*, including userinfo/API-key path segments, when a request fails — this is independently confirmed by an existing test elsewhere in the codebase that guards a different call path against exactly this behavior: [4](#0-3) 

Unlike that tx-manager path (and the analogous `basectl`, `load-tests`, and `registrar` paths, which were all explicitly patched/tested to sanitize such transport error strings before exposing them), the `SequencerClientError → ErrorObject` conversion in `crates/execution/rpc/src/error.rs` has no equivalent sanitization. The sequencer endpoint is configured operator-side (`--sequencer-http` / `sequencer_url`) and, following the same operational pattern seen throughout this codebase (Infura-style URLs with an embedded API key), commonly embeds a secret in the URL path or userinfo.

### Impact Explanation
Any anonymous RPC client can call `eth_sendRawTransaction` on a Base node configured with a raw-tx sequencer forwarder. If the sequencer endpoint is transiently unreachable (network blip, sequencer restart, DNS hiccup, TLS handshake failure, or an attacker-induced condition such as flooding/exhausting local sockets to force a connection failure), the resulting JSON-RPC error returned to the *unprivileged caller* contains the raw transport error string, which embeds the full sequencer URL — including any embedded API key or basic-auth credentials configured for that endpoint. This is a direct sensitive-information/key-disclosure vulnerability, analogous to CVE-2023-27587, reachable via the public `eth_sendRawTransaction` RPC surface.

### Likelihood Explanation
Exploitation only requires the sequencer connection to fail once while an attacker's `eth_sendRawTransaction` call is in flight — a condition attackers can wait for or potentially help trigger (e.g., via connection exhaustion) and repeatedly poll for. No privileged access is required.

### Recommendation
Sanitize the `SequencerClientError::HttpError` fallback arm in `crates/execution/rpc/src/error.rs` before it is converted into an `ErrorObject`: replace transport-level error variants (`RpcError::Transport(_)`, `RpcError::SerError`, etc.) with a fixed, non-sensitive message (e.g., `"sequencer request failed"`), mirroring the sanitization pattern already applied in `crates/utilities/tx-manager/src/error.rs` and the `basectl` RPC error paths, and keep the URL/credentials only in server-side logs.

### Proof of Concept
1. Configure a Base execution node with `--sequencer-http https://user:APIKEY@sequencer.example/forward` (or any URL with an embedded secret in the path/userinfo), enabling raw-tx forwarding.
2. As an anonymous RPC client, call `eth_sendRawTransaction` with any validly-formed transaction while the sequencer endpoint is unreachable (e.g., simulate by pointing at a closed port, or wait for a real outage).
3. Observe the JSON-RPC error response: the `message` field contains the raw transport error text, which includes the full sequencer request URL with the embedded API key/credentials, as produced by the `Display` impl exercised in [4](#0-3)  and propagated unmodified through [5](#0-4) .

### Citations

**File:** crates/execution/rpc/src/error.rs (L124-147)
```rust
/// Error type when interacting with the Sequencer
#[derive(Debug, thiserror::Error)]
pub enum SequencerClientError {
    /// Wrapper around an [`RpcError<TransportErrorKind>`].
    #[error(transparent)]
    HttpError(#[from] RpcError<TransportErrorKind>),
}

impl From<SequencerClientError> for jsonrpsee_types::error::ErrorObject<'static> {
    fn from(err: SequencerClientError) -> Self {
        match err {
            SequencerClientError::HttpError(RpcError::ErrorResp(ErrorPayload {
                code,
                message,
                data,
            })) => jsonrpsee_types::error::ErrorObject::owned(code as i32, message, data),
            err => jsonrpsee_types::error::ErrorObject::owned(
                INTERNAL_ERROR_CODE,
                err.to_string(),
                None::<String>,
            ),
        }
    }
}
```

**File:** crates/execution/rpc/src/sequencer.rs (L151-165)
```rust
    /// Forwards a transaction to the sequencer endpoint.
    pub async fn forward_raw_transaction(&self, tx: &[u8]) -> Result<B256, SequencerClientError> {
        let start = Instant::now();
        let rlp_hex = hex::encode_prefixed(tx);
        let tx_hash =
            self.request("eth_sendRawTransaction", (rlp_hex,)).await.inspect_err(|err| {
                warn!(
                    target: "rpc::eth",
                    %err,
                    "Failed to forward transaction to sequencer",
                );
            })?;
        SequencerMetrics::record_forward_latency(start.elapsed());
        Ok(tx_hash)
    }
```

**File:** crates/execution/rpc/src/eth/transaction.rs (L86-99)
```rust
        // On Base, transactions are forwarded directly to the sequencer to be included in
        // blocks that it builds.
        if let Some(client) = self.raw_tx_forwarder().as_ref() {
            debug!(target: "rpc::eth", hash = %pool_transaction.hash(), "forwarding raw transaction to sequencer");
            let hash = client.forward_raw_transaction(&tx).await.inspect_err(|err| {
                    debug!(target: "rpc::eth", error = %err, hash=% *pool_transaction.hash(), "failed to forward raw transaction");
                })?;

            // Retain tx in local tx pool after forwarding, for local RPC usage.
            let _ = self.inner.eth_api.add_pool_transaction(origin, pool_transaction).await.inspect_err(|err| {
                warn!(target: "rpc::eth", error = %err, %hash, "successfully sent tx to sequencer, but failed to persist in local tx pool");
            });

            return Ok(hash);
```

**File:** crates/utilities/tx-manager/src/error.rs (L427-435)
```rust
    #[test]
    fn classify_replaces_transport_error_messages() {
        // Transport errors may contain credential-bearing request URLs.
        let err: TransportError = TransportErrorKind::custom_str(
            "error sending request for url (https://user:password@l1.example/v3/api-key?token=secret)",
        );
        let classified = RpcErrorClassifier::classify_rpc_error(&err);
        assert_eq!(classified, TxManagerError::Rpc("transport request failed".to_string()));
    }
```
