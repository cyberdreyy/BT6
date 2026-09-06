### Title
Unauthenticated signer event-receiver HTTP server allows forged `BlockValidateResponse` to bypass node validation and induce a pre-commit/signature on an unvalidated block - (File: `libsigner/src/events.rs`)

### Summary
The `SignerEventReceiver` HTTP server that the `stacks-signer` binds to receive events from the node has no authentication at all. Any of the four state-changing POST endpoints — `/stackerdb_chunks`, `/proposal_response`, `/new_burn_block`, `/new_block` — as well as `/shutdown`, are processed as trusted node input regardless of the caller's identity. When the signer's `endpoint` is bound to a non-loopback address (the shipped reference config does exactly this), a network-reachable attacker can POST a forged `/proposal_response` body that the signer treats identically to a genuine validation verdict from its own `stacks-node`.

### Finding Description
`SignerEventReceiver::bind` opens a plain `tiny_http` server with no TLS, no `Authorization` check, and no peer-address allow-list: [1](#0-0) 

`next_event` dispatches purely on the URL path, with `/status` responding unconditionally and `/proposal_response` deserializing attacker-supplied JSON straight into a `BlockValidateResponse`/`SignerEvent`, with zero authentication or signature check on the request: [2](#0-1) 

`process_event` only requires that the POST body deserialize into the expected type; there is no check that the sender is the configured `node_host`, no HMAC, and no reuse of the `auth_password`/`auth_token` value that secures the *reverse* channel (signer → node `/v3/block_proposal`). That token is only used by the node to validate calls arriving at the node's RPC endpoint, not by the signer to validate calls arriving at its own event endpoint: [3](#0-2) 

On the signer side, a `BlockValidateResponse::Ok` received via this channel is trusted as the node's validation verdict and drives the block straight to pre-commit (and ultimately, once the pre-commit weight threshold is reached, to a signature) without re-running the node's full block execution/validity checks — only a narrower local consistency check (`check_block_against_signer_db_state`) is performed: [4](#0-3) [5](#0-4) 

The reference/sample configuration explicitly binds the signer's event listener to all interfaces (`endpoint = "0.0.0.0:30000"`), mirroring the exact "non-loopback bind" precondition of the Tilt advisory: [6](#0-5) 

The equality broken: "block signed only after the *node* actually validated it" collapses into "block signed after *anyone reaching the event port* claims the node validated it," since the HTTP layer performs no authentication of the caller as the trusted `stacks-node`.

### Impact Explanation
This is a Critical-class break under the stated impact taxonomy: a signer can be induced to sign a block that the node's `/v3/block_proposal` execution/validity check never actually approved (e.g., a block with an invalid state root, exceeding execution budget, containing malicious/oversized transactions, or otherwise unsound), because the forged `BlockValidateResponse::Ok` substitutes for genuine node validation and `check_block_against_signer_db_state` does not re-run node-side block execution. It can also be used to force `BlockValidateResponse::Reject` and short-circuit rejection handling, or to inject spoofed `/new_burn_block`/`/new_block`/`/stackerdb_chunks` events that manipulate the signer's local state machine (miner-inactivity timers, tenure tracking) — a liveness wedge vector as well.

### Likelihood Explanation
Exploitation requires only network reachability to the signer's bound event-receiver port; no majority of signers, no signer key, and no `auth_token`/`auth_password` are needed, since that credential is never checked by this listener. The shipped reference configuration (`sample/conf/signer/mainnet-signer-conf.toml`) binds to `0.0.0.0`, and documentation elsewhere in the repo (`SpawnedSigner::new`'s runtime warning) already flags the risk of exposing the signer to non-local networks, indicating the exposure is a real, foreseeable operational condition rather than a purely theoretical one.

### Recommendation
Require the node to authenticate to the signer's event-receiver endpoints (e.g., reuse `auth_password`/a shared secret as a bearer token or HMAC over the body, verified in `next_event`/`process_event` before deserializing or acting on the payload) for all POST routes (`/stackerdb_chunks`, `/proposal_response`, `/new_burn_block`, `/new_block`, `/shutdown`). Additionally, bind the event receiver to loopback by default and require an explicit, documented opt-in (with warnings) to bind non-loopback, and re-run/cross-check the full node validation path rather than trusting a bare `BlockValidateResponse` for consequential state transitions like `mark_pre_committed`.

### Proof of Concept
1. Configure a signer with `endpoint = "0.0.0.0:30000"` (as in the shipped `mainnet-signer-conf.toml`) so the event receiver is reachable from the network.
2. Have the miner (or an attacker who has observed a `BlockProposal` broadcast over StackerDB, which is public gossip) send that block through the normal `handle_block_proposal` path so the signer stores a `BlockInfo` for it (as happens with any real proposal the signer sees).
3. Before the real `stacks-node` responds, an attacker sends:
   ```
   POST /proposal_response HTTP/1.1
   Host: <signer-ip>:30000
   Content-Type: application/json
   Content-Length: <n>

   {"Ok":{"signer_signature_hash":"<hash-of-target-block>","cost":{...},"size":0,"validation_time_ms":1,"replay_tx_hash":null,"replay_tx_exhausted":false}}
   ```
   as demonstrated by the equivalent legitimate flow in `stacks-signer/src/v0/tests.rs`'s `validate_ok` helper, which constructs exactly this `BlockValidateResponse::Ok` payload: [7](#0-6) 
4. `next_event` accepts the POST with no authentication check, parses it into `SignerEvent::BlockValidationResponse`, and the runloop's `handle_block_validate_ok` marks the block pre-committed and broadcasts a pre-commit — all without the real node ever having validated it.

### Citations

**File:** libsigner/src/events.rs (L401-408)
```rust
    /// Start listening on the given socket address.
    /// Returns the address that was bound.
    /// Errors out if bind(2) fails
    fn bind(&mut self, listener: SocketAddr) -> Result<SocketAddr, EventError> {
        self.http_server = Some(HttpServer::http(listener).expect("failed to start HttpServer"));
        self.local_addr = Some(listener);
        Ok(listener)
    }
```

**File:** libsigner/src/events.rs (L413-459)
```rust
    fn next_event(&mut self) -> Result<SignerEvent<T>, EventError> {
        self.with_server(|event_receiver, http_server, _is_mainnet| {
            // were we asked to terminate?
            if event_receiver.is_stopped() {
                return Err(EventError::Terminated);
            }
            debug!("Request handling");
            let request = http_server.recv()?;
            debug!("Got request"; "method" => %request.method(), "path" => request.url());

            if request.url() == "/status" {
                request
                .respond(HttpResponse::from_string("OK"))
                .expect("response failed");
                return Ok(SignerEvent::StatusCheck);
            }

            if request.method() != &HttpMethod::Post {
                return Err(EventError::MalformedRequest(format!(
                    "Unrecognized method '{}'",
                    request.method(),
                )));
            }
            debug!("Processing {} event", request.url());
            if request.url() == "/stackerdb_chunks" {
                process_event::<T, StackerDBChunksEvent>(request)
            } else if request.url() == "/proposal_response" {
                process_event::<T, BlockValidateResponse>(request)
            } else if request.url() == "/new_burn_block" {
                process_event::<T, BurnBlockEvent>(request)
            } else if request.url() == "/shutdown" {
                event_receiver.stop_signal.store(true, Ordering::SeqCst);
                Err(EventError::Terminated)
            } else if request.url() == "/new_block" {
                process_event::<T, StacksBlockEvent>(request)
            } else {
                let url = request.url().to_string();
                debug!(
                    "[{:?}] next_event got request with unexpected url {}, return OK so other side doesn't keep sending this",
                    event_receiver.local_addr,
                    url
                );
                ack_dispatcher(request);
                Err(EventError::UnrecognizedEvent(url))
            }
        })?
    }
```

**File:** stackslib/src/config/mod.rs (L3802-3816)
```rust
    /// HTTP auth password to use when communicating with stacks-signer binary.
    ///
    /// This token is used in the `Authorization` header for certain requests.
    /// Primarily, it secures the communication channel between this node and a
    /// connected `stacks-signer` instance.
    ///
    /// It is also used to authenticate requests to `/v2/blocks?broadcast=1`.
    /// ---
    /// @default: `None` (authentication disabled for relevant endpoints)
    /// @notes:
    ///   - This field **must** be configured if the node needs to receive
    ///     block proposals from a configured `stacks-signer` [[events_observer]]
    ///     via the `/v3/block_proposal` endpoint.
    ///   - The value must match the token configured on the signer.
    pub auth_token: Option<String>,
```

**File:** stacks-signer/src/v0/signer.rs (L1946-1975)
```rust
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            // The signer db state has changed. We no longer view this block as valid. Override the validation response.
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
        } else {
            if let Err(e) = block_info.mark_pre_committed() {
                // The block may have reached enough signatures before we validated the block so should fail to mark pre-committed
                // but still call to make sure the timestamps and validity are updated correctly.
                if !block_info.has_reached_consensus()
                    && block_info.state != BlockState::LocallyAccepted
                {
                    warn!("{self}: Failed to mark block as approved: {e:?}",);
                    return;
                }
            }

            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.send_block_pre_commit(signer_signature_hash.clone());
```

**File:** stacks-signer/src/v0/signer.rs (L2053-2071)
```rust
    /// Handle the block validate response returned from our prior calls to submit a block for validation
    fn handle_block_validate_response(
        &mut self,
        stacks_client: &StacksClient,
        block_validate_response: &BlockValidateResponse,
        sortition_state: &mut Option<SortitionsView>,
    ) {
        info!("{self}: Received a block validate response: {block_validate_response:?}");
        match block_validate_response {
            BlockValidateResponse::Ok(block_validate_ok) => {
                crate::monitoring::actions::record_block_validation_latency(
                    block_validate_ok.validation_time_ms,
                );
                self.handle_block_validate_ok(stacks_client, block_validate_ok, sortition_state);
            }
            BlockValidateResponse::Reject(block_validate_reject) => {
                self.handle_block_validate_reject(block_validate_reject, sortition_state);
            }
        };
```

**File:** sample/conf/signer/mainnet-signer-conf.toml (L37-39)
```text
# REQUIRED: Local endpoint this signer listens on for events from the node.
# Must match the endpoint in the node's [[events_observer]] section.
endpoint = "0.0.0.0:30000"
```

**File:** stacks-signer/src/v0/tests.rs (L580-589)
```rust
    fn validate_ok(hash: &Sha512Trunc256Sum) -> SignerEvent<SignerMessage> {
        SignerEvent::BlockValidationResponse(BlockValidateResponse::Ok(BlockValidateOk {
            signer_signature_hash: hash.clone(),
            cost: ExecutionCost::ZERO,
            size: 0,
            validation_time_ms: 1,
            replay_tx_hash: None,
            replay_tx_exhausted: false,
        }))
    }
```
