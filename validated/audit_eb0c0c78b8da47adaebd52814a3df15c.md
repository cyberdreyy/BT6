### Title
Unsanitized Sequencer/Builder RPC Transport Errors Leak Credential-Bearing URLs to Anonymous `eth_sendRawTransaction` Callers - (File: `crates/execution/rpc/src/error.rs`)

### Summary
When a Base RPC node forwards a raw transaction to its configured sequencer/builder endpoint (`--rollup.sequencer`) and that endpoint is unreachable or misconfigured, the resulting transport error is converted to a JSON-RPC error object using the error's raw `Display` string, without the credential-redaction that other RPC surfaces in this codebase (proof submission, prover client, dispute-game CLI) explicitly apply.

### Finding Description
`SequencerClientError` wraps `alloy_transport::RpcError<TransportErrorKind>` and forwards it to the JSON-RPC client: [1](#0-0) 

For the `ErrorResp` variant it forwards the server-provided payload, which is reasonable. But for any other variant — including plain transport-level failures such as connection refused/timeout — it falls through to `err.to_string()` as the JSON-RPC error message, with no redaction, and returns it via `INTERNAL_ERROR_CODE` directly to the caller.

The underlying `alloy_transport_http`/`reqwest` transport error `Display` implementation embeds the full request URL, including any userinfo (`user:password@host`) or query-string API keys, in messages of the form `"error sending request for url (https://user:password@host/...)"`. This exact string shape is used elsewhere in the codebase as the canonical example of a credential-leaking transport error that must be sanitized: [2](#0-1) 

The `sequencer.rs` client constructs its HTTP client directly from the operator-supplied sequencer/builder endpoint string, and any credentials embedded in that URL (basic auth or an API-key path/query segment, a supported way to authenticate to protected sequencer/builder RPC endpoints in production deployments) would appear verbatim in that error string: [3](#0-2) 

This is used on the `eth_sendRawTransaction` hot path — reachable by any anonymous transaction sender — via `send_pool_transaction`, which awaits `client.forward_raw_transaction(&tx)` and propagates the error straight through `Self::Error`: [4](#0-3) 

By contrast, every other place in this codebase that surfaces transport/RPC errors derived from an operator-configured endpoint URL (dispute-game CLI, proof submission, prover-service client, tx-manager) has an explicit sanitization step and regression test proving credentials never reach the error message or logs: [5](#0-4) [6](#0-5) [7](#0-6) 

The `SequencerClientError -> ErrorObject` conversion in `crates/execution/rpc/src/error.rs` has no equivalent sanitization, so it is the one credential-forwarding transport-error path in this codebase that is directly reachable from an untrusted, anonymous JSON-RPC caller (versus the CLI/operator-facing tools above, which are only reachable by an operator running `basectl`).

### Impact Explanation
If the operator configures the sequencer/builder forwarding endpoint with embedded credentials (basic-auth userinfo or an API key in the URL path/query — a normal way to protect an internal sequencer/builder RPC endpoint from an otherwise public-facing RPC node), any anonymous client can trigger `eth_sendRawTransaction` and, during a transient sequencer outage, DoS, or misconfiguration, receive the raw connection-error string back in the JSON-RPC response, disclosing the sequencer/builder's authentication secret. An attacker who has network visibility to cause (or simply wait for) a connection failure — or who can force one via a large volume of requests / a slow-loris style probe against the sequencer — obtains long-lived credentials to the privileged sequencer/builder ingress, which can be used to submit fraudulent/whitelisted transactions or otherwise abuse the trusted forwarding path. This satisfies "key disclosure" leading to unauthorized privileged operations against Base's sequencer ingress.

### Likelihood Explanation
Likelihood depends on whether operators embed secrets directly in the sequencer/builder URL rather than via the separate `sequencer_headers`/custom-header mechanism that the client also supports (`SequencerClient::new_with_headers`). Where headers are used instead of URL-embedded credentials, the error strings from reqwest typically do not include header values, reducing exposure. However, the codebase's own defensive pattern (redacting `user:password@host` and API-key path segments across every other RPC/CLI error surface, with tests asserting exactly this URL shape) indicates URL-embedded credentials are a real, anticipated configuration in this system, making this the single RPC-facing gap in an otherwise consistently-applied mitigation.

### Recommendation
Apply the same credential-redaction logic used in `crates/utilities/tx-manager/src/error.rs::RpcErrorClassifier::classify_rpc_error` (replace non-`ErrorResp` transport errors with a fixed, credential-free message) to `SequencerClientError`'s `From<SequencerClientError> for ErrorObject` conversion in `crates/execution/rpc/src/error.rs`, or route sequencer/builder-forwarding errors through `RpcErrorClassifier` before constructing the JSON-RPC response, and add a regression test mirroring `submitter_sanitizes_l1_endpoint_secrets_from_transport_errors` / `client_redacts_endpoint_secrets_from_logs_and_errors` for the `eth_sendRawTransaction` sequencer-forwarding path.

### Proof of Concept
1. Configure `--rollup.sequencer` (or the builder forwarding endpoint) with credentials embedded in the URL, e.g. `https://user:s3cr3t@sequencer.internal/api-key-123`.
2. Make the sequencer endpoint unreachable (network partition, sequencer restart, or a client-side connect-timeout race).
3. As an anonymous RPC client, call `eth_sendRawTransaction` with any validly-signed transaction.
4. `send_pool_transaction` awaits `client.forward_raw_transaction(&tx)` → `SequencerClient::request` → the HTTP transport returns `RpcError::Transport(TransportErrorKind::Custom(...))`/reqwest error containing `"...url (https://user:s3cr3t@sequencer.internal/api-key-123)..."`.
5. `SequencerClientError::from(...)` falls into the `err => ErrorObject::owned(INTERNAL_ERROR_CODE, err.to_string(), None)` branch, echoing the full URL — including `user:s3cr3t` and the API key — back to the caller in the JSON-RPC error response.

*(Note: I was unable to directly confirm from the indexed source whether production deployments commonly embed credentials in the sequencer URL versus using the separate header mechanism; this affects the practical likelihood but not the presence of the code-level gap, which is clearly demonstrated by contrast with the sanitization applied everywhere else in the codebase.)*

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

**File:** crates/execution/rpc/src/sequencer.rs (L55-120)
```rust
impl SequencerClient {
    /// Creates a new [`SequencerClient`] for the given URL.
    ///
    /// If the URL is a websocket endpoint we connect a websocket instance.
    pub async fn new(sequencer_endpoint: impl Into<String>) -> Result<Self, Error> {
        Self::new_with_headers(sequencer_endpoint, Default::default()).await
    }

    /// Creates a new `SequencerClient` for the given URL with the given headers
    ///
    /// This expects headers in the form: `header=value`
    pub async fn new_with_headers(
        sequencer_endpoint: impl Into<String>,
        headers: Vec<String>,
    ) -> Result<Self, Error> {
        let sequencer_endpoint = sequencer_endpoint.into();
        let endpoint = BuiltInConnectionString::from_str(&sequencer_endpoint)?;
        if let BuiltInConnectionString::Http(url) = endpoint {
            let mut builder = alloy_reqwest::Client::builder()
                // we force use tls to prevent native issues
                .use_rustls_tls();

            if !headers.is_empty() {
                let mut header_map = alloy_reqwest::header::HeaderMap::new();
                for header in headers {
                    if let Some((key, value)) = header.split_once('=') {
                        header_map.insert(
                            key.trim()
                                .parse::<alloy_reqwest::header::HeaderName>()
                                .map_err(|err| Error::InvalidHeader(err.to_string()))?,
                            value
                                .trim()
                                .parse::<alloy_reqwest::header::HeaderValue>()
                                .map_err(|err| Error::InvalidHeader(err.to_string()))?,
                        );
                    }
                }
                builder = builder.default_headers(header_map);
            }

            let client = builder.build()?;
            Self::with_http_client(url, client)
        } else {
            let client = ClientBuilder::default().connect_with(endpoint).await?;
            let inner = SequencerClientInner::new(sequencer_endpoint, client);
            Ok(Self { inner: Arc::new(inner) })
        }
    }

    /// Creates a new [`SequencerClient`] with http transport with the given http client.
    pub fn with_http_client(
        sequencer_endpoint: impl Into<String>,
        client: alloy_reqwest::Client,
    ) -> Result<Self, Error> {
        let sequencer_endpoint: String = sequencer_endpoint.into();
        let url = sequencer_endpoint
            .parse()
            .map_err(|_| Error::InvalidUrl(sequencer_endpoint.clone()))?;

        let http_client = Http::with_client(client, url);
        let is_local = http_client.guess_local();
        let client = ClientBuilder::default().transport(http_client, is_local);

        let inner = SequencerClientInner::new(sequencer_endpoint, client);
        Ok(Self { inner: Arc::new(inner) })
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

**File:** crates/infra/basectl/src/rpc/games.rs (L552-573)
```rust
    #[test]
    fn l1_transport_errors_redact_endpoint_secrets_from_source_chain() {
        let mut client = mocked_client(Asserter::new());
        client.endpoint =
            Url::parse("https://user:password@l1.example/v3/api-key?token=secret").unwrap();
        let transport = TransportErrorKind::custom_str(&format!(
            "request to {} failed: connection refused",
            client.endpoint
        ));

        let error = client.provider_error(transport);
        let message = error.to_string();
        let source = error.source().expect("L1 error should preserve a source").to_string();
        let debug = format!("{client:?}");

        assert!(message.contains("https://l1.example"));
        assert!(source.contains("L1 transport request failed"));
        for secret in ["user", "password", "api-key", "token=secret"] {
            assert!(!source.contains(secret));
            assert!(!debug.contains(secret));
        }
    }
```

**File:** crates/infra/basectl/src/rpc/submit.rs (L308-320)
```rust
    #[test]
    fn submitter_sanitizes_l1_endpoint_secrets_from_transport_errors() {
        let endpoint =
            Url::parse("https://user:password@l1.example/v3/api-key?token=secret").unwrap();
        let error = TxManagerError::Rpc(format!("request to {endpoint} failed"));
        let sanitized = ProposalProofSubmitter::sanitize_tx_manager_error(error);
        let message = sanitized.to_string();

        assert!(message.contains("L1 transport request failed"));
        for secret in ["user", "password", "api-key", "token=secret"] {
            assert!(!message.contains(secret));
        }
    }
```

**File:** crates/infra/basectl/src/rpc/prover.rs (L698-717)
```rust
    #[test]
    fn client_redacts_endpoint_secrets_from_logs_and_errors() {
        let endpoint =
            Url::parse("https://user:password@prover.example/rpc/api-key?token=secret").unwrap();
        let client = ProofsClient::connect(&endpoint).expect("client should build");
        let source = base_prover_service_client::ProverServiceClientError::RpcTransport(
            JsonRpcClientError::Transport(
                io::Error::other(format!("request to {endpoint} failed")).into(),
            ),
        );
        let error = client.rpc_error("prover_getProof", source);
        let source = error.source().expect("RPC error should preserve a source").to_string();
        let debug = format!("{client:?}");

        assert_eq!(client.endpoint, "https://prover.example");
        for secret in ["user", "password", "api-key", "token=secret"] {
            assert!(!source.contains(secret));
            assert!(!debug.contains(secret));
        }
    }
```
