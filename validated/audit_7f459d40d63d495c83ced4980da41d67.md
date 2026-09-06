### Title
Signer's local event-HTTP-listener trusts any network peer's `/proposal_response`, `/stackerdb_chunks`, `/new_burn_block`, `/new_block` POSTs with no host/origin/auth check, letting a network attacker forge a "validated" verdict for a malicious block - (File: libsigner/src/events.rs)

### Summary
`SignerEventReceiver` — the HTTP server every `stacks-signer` binary runs to receive events "from the node" — performs no host-header validation, no source-IP allowlist, and no authentication on any of its routes. It is commonly bound to `0.0.0.0` (the shipped reference config even sets `endpoint = "0.0.0.0:30000"`), so any host that can reach that port can POST a forged `BlockValidateResponse`, `StackerDBChunksEvent`, `BurnBlockEvent`, or `StacksBlockEvent` and have it fed straight into the signer's state machine as if the local trusted node had sent it. This is the direct analog of the Glances finding: a network-facing HTTP surface that lacks `Host`/origin validation and is trusted implicitly because it is "supposed" to only be reachable by a trusted peer.

### Finding Description
`SignerEventReceiver::bind` opens a plain `tiny_http`/`HttpServer` listener on the configured address with no additional access control: [1](#0-0) 

`next_event` dispatches purely on the URL path — there is no check of the `Host` header, no peer-IP allowlist, and no authentication token comparison anywhere in this function or in the trait: [2](#0-1) 

Each recognized path is deserialized directly into a strongly-typed event and handed to the signer runloop via `process_event`, with the request body trusted at face value: [3](#0-2) 

Critically, `/proposal_response` deserializes into `BlockValidateResponse` — the exact type the node uses to report the result of `postblock_proposal` validation — and forwards it unauthenticated into the signer's runloop: [4](#0-3) 

The shipped reference configuration confirms the endpoint is meant to be reachable broadly, defaulting to `0.0.0.0`: [5](#0-4) 

On the consumer side, `stacks-signer/src/v0/signer.rs` imports and processes `BlockValidateResponse`/`BlockValidateOk`/`BlockValidateReject` as the ground truth for whether a proposed block was validated by the node: [6](#0-5) 

According to the documented signer flow, only after `valid = true` is observed does the signer proceed to accumulate pre-commit weight and eventually sign the block: [7](#0-6) 

Because the event receiver applies no host/origin/auth check (mirroring exactly the missing `TrustedHostMiddleware`/allowed-hosts gap described in the Glances advisory for its REST/WebUI app), any network-reachable adversary — not just the co-located `stacks-node` — can synthesize a `BlockValidateResponse::Ok` for a block header the node never actually validated (or never received), and inject it directly into `next_event` → `process_event` → the signer runloop's `handle_block_response`/validation-handling path. This breaks the "signed vs validated" equality that the signer's whole state machine assumes: the signer believes the node validated the block, when in fact an attacker forged that verdict over the wire.

### Impact Explanation
This maps to the Critical impact category: a signer can be induced to sign an invalid, non-canonical, or never-actually-validated block, because the only gate between "block proposal" and "signer casts a signature" — the node's validation response — can be forged by anyone who can reach the signer's event port. If the signer's `endpoint` is bound non-locally (the shipped sample config defaults to `0.0.0.0`), this requires no majority of signers, no other signer's key, and no possession of the node's `auth_token`; a single attacker forging one HTTP POST against one signer's listener is sufficient to corrupt that signer's local validation state for a given block.

### Likelihood Explanation
Reachability depends on network exposure of the signer's bound `endpoint`. Given the shipped reference configuration explicitly defaults this to `0.0.0.0` (see `sample/conf/signer/mainnet-signer-conf.toml`), and given operators are told to configure it to match the node's `events_observer` endpoint without being told to restrict it to loopback or add authentication, exposure is plausible for any deployment that does not manually harden the bind address or wrap it behind network-level ACLs. There is no code-level mitigation (no `Host` check, no auth) regardless of exposure level, so the likelihood is bound entirely by network topology, which the codebase does nothing to enforce or even warn about at the HTTP layer.

### Recommendation
- Enforce that `SignerEventReceiver` only accepts connections from an explicit allowlist (e.g., loopback, or a configured `node_host` IP) at the `bind`/`next_event` layer.
- Require a shared-secret/auth-token check on `/proposal_response`, `/stackerdb_chunks`, `/new_burn_block`, `/new_block` similar to the node's `auth_token` scheme, rather than trusting any POST body that parses successfully.
- Validate the `Host` header (or equivalent) against the expected local bind address, analogous to `TrustedHostMiddleware` in the referenced Glances fix.
- Default the signer's `endpoint` to a loopback address in sample/reference configs, and document the security requirement explicitly for non-default binds.

### Proof of Concept
1. Deploy a `stacks-signer` with `endpoint = "0.0.0.0:30000"` (as in the shipped reference config).
2. From any host that can route to port 30000 (or via DNS-rebinding a victim browser co-located with the signer), send:
```
POST /proposal_response HTTP/1.1
Host: <attacker-controlled-or-rebound-host>
Content-Type: application/json
Content-Length: <n>

{ ...serialized BlockValidateResponse::Ok(BlockValidateOk{ signer_signature_hash: <hash-of-malicious-block>, ... }) ... }
```
3. `SignerEventReceiver::next_event` (libsigner/src/events.rs:439-440) accepts this without any host/auth check, deserializes it via `process_event::<T, BlockValidateResponse>`, and forwards `SignerEvent::BlockValidateResponse` into the signer runloop.
4. The signer's state machine treats this as genuine confirmation from its own node that the block is valid, advancing it toward pre-commit/signature issuance for a block that was never actually validated by the trusted node — breaking the signed-vs-validated equality documented in `docs/signer-flows.md`.

### Citations

**File:** libsigner/src/events.rs (L404-408)
```rust
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

**File:** libsigner/src/events.rs (L519-542)
```rust
fn process_event<T, E>(mut request: HttpRequest) -> Result<SignerEvent<T>, EventError>
where
    T: SignerEventTrait,
    E: serde::de::DeserializeOwned + TryInto<SignerEvent<T>, Error = EventError>,
{
    let mut body = String::new();

    if let Err(e) = request.as_reader().read_to_string(&mut body) {
        error!("Failed to read body: {:?}", &e);
        ack_dispatcher(request);
        return Err(EventError::MalformedRequest(format!(
            "Failed to read body: {:?}",
            e
        )));
    }
    // Regardless of whether we successfully deserialize, we should ack the dispatcher so they don't keep resending it
    ack_dispatcher(request);
    let json_event: E = serde_json::from_slice(body.as_bytes())
        .map_err(|e| EventError::Deserialize(format!("Could not decode body to JSON: {:?}", e)))?;

    let signer_event: SignerEvent<T> = json_event.try_into()?;

    Ok(signer_event)
}
```

**File:** sample/conf/signer/mainnet-signer-conf.toml (L37-39)
```text
# REQUIRED: Local endpoint this signer listens on for events from the node.
# Must match the endpoint in the node's [[events_observer]] section.
endpoint = "0.0.0.0:30000"
```

**File:** docs/signer-flows.md (L244-249)
```markdown
    ALREADY -- no --> VALID{"validated ok?<br/>valid = true"}
    VALID -- no --> N2(["wait for validation"])
    VALID -- yes --> TH{"pre-commit weight ≥ 70%?<br/>NakamotoBlockHeader::<br/>compute_voting_weight_threshold"}
    TH -- no --> N3(["wait for more pre-commits"])
    TH -- yes --> RECHECK{"chainstate checks still pass?<br/>check_block_against_signer_db_state<br/>→ section 7"}
    RECHECK -- no --> REJ["mark_locally_rejected,<br/>handle_block_rejection,<br/>broadcast rejection"]:::bad
```
