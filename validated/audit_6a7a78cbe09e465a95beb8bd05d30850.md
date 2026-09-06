### Title
Missing authorization on the signer's local event-receiver HTTP endpoints lets an unauthenticated network client forge a `BlockValidateOk` and cause a signer to sign an invalid/non-canonical block - (File: libsigner/src/events.rs)

### Summary
`SignerEventReceiver::next_event` (`libsigner/src/events.rs`) accepts unauthenticated HTTP `POST` requests on `/proposal_response`, `/new_block`, `/new_burn_block`, and `/stackerdb_chunks` and feeds their bodies directly into the signer's event pipeline. There is no `Authorization` header check, token comparison, or any other permission gate on these routes — only method (`POST`) and URL are checked before the body is deserialized and dispatched as a trusted `SignerEvent`. [1](#0-0)  This is a direct structural analog of the reported bug class: an HTTP endpoint that performs a state-relevant action (here, injecting a "node validated this block" verdict) with no permission/authorization check at all.

### Finding Description
The stacks-signer runs an HTTP server (`endpoint`, frequently configured to bind `0.0.0.0` per the shipped sample config) that is meant to receive events only from its paired stacks-node. [2](#0-1)  The receiver dispatches based purely on URL path, with zero authentication of the sender:

```
if request.url() == "/proposal_response" {
    process_event::<T, BlockValidateResponse>(request)
} ...
``` [3](#0-2) 

`process_event` simply reads the body and deserializes it into the target event type, with no signature, MAC, or shared-secret check anywhere in this file. [4](#0-3) 

Compare this to the node-side `/v3/block_proposal` endpoint, which explicitly requires and checks an `Authorization` header against a shared `auth_token`/`auth_password` before accepting a proposal for validation. [5](#0-4)  No equivalent check exists on the signer's inbound listener for `/proposal_response` (the event that carries the node's validation verdict, `BlockValidateOk`/`BlockValidateReject`).

Once a `BlockValidateOk` event arrives, `handle_block_validate_ok` in `stacks-signer/src/v0/signer.rs` treats it as authoritative: it clears the outstanding submission marker if the hash matches, records validation time, and if the chainstate re-check (`check_block_against_signer_db_state`) does not itself catch a problem, moves the block to `PreCommitted` and broadcasts a pre-commit — the first step toward the signer eventually producing a real cryptographic signature over the block. [6](#0-5)  Nothing in this handler verifies that the `BlockValidateOk` actually originated from the signer's own configured stacks-node (e.g., via the outbound `auth_password` credential or any signed/MACed response) — it is accepted purely because it arrived on the right TCP port and URL path.

This breaks the equality the design otherwise relies on throughout the flow: "a signature is only produced after *this signer's own node* validated the block" (as documented in `docs/signer-flows.md` section 4, "The stacks-node answers the `/v3/block_proposal` submission... A signature is not produced here [without it]"). [7](#0-6)  An attacker who can reach the signer's listening port can falsify that step entirely.

### Impact Explanation
An attacker who can reach the signer's event-receiver port (which, per the shipped reference config, may be bound to `0.0.0.0`) can POST a forged `/proposal_response` body claiming `BlockValidateOk` for an attacker-chosen `NakamotoBlock`/`signer_signature_hash` that the signer's real node never received or validated. Provided the block passes the signer's own (attacker-influenced, self-consistent) chainstate re-check (`check_block_against_signer_db_state`), the signer will mark it `PreCommitted` and broadcast a pre-commit, and ultimately move toward `mark_locally_accepted`/producing a real signature over an unvalidated, potentially invalid or non-canonical block — this satisfies the "Critical" bar defined for this scan: *a signer signing an invalid, non-canonical, or conflicting block*. Reaching full quorum still requires other signers to independently agree (out of scope per the rules), but the *individual signer's own safety guarantee* — "I never sign anything my own node has not actually validated" — is broken by a single unauthenticated network message, with no involvement of any other signer's key or a majority.

### Likelihood Explanation
Likelihood depends entirely on network exposure of the signer's event-receiver port. The project's own reference configuration examples bind this port to all interfaces (`endpoint = "0.0.0.0:30000"`), and the documentation frames the signer/node auth token as protecting the *outbound* (signer→node) proposal-validation call, not the inbound event channel — there is no mention anywhere that the inbound listener needs to be firewalled, and no code-level enforcement backs that assumption. Any operator who follows the sample config literally, or who runs signer and node on separate hosts (a supported and expected topology), exposes this endpoint to the network.

### Recommendation
Add an authorization check to the signer's `SignerEventReceiver` inbound HTTP handling — e.g., require the same shared `auth_password`/`auth_token` (or a dedicated secret) as an `Authorization` header on incoming POSTs to `/proposal_response`, `/new_block`, `/new_burn_block`, and `/stackerdb_chunks`, mirroring the check already present on the node's `/v3/block_proposal` endpoint. Reject unauthenticated requests before deserializing/dispatching them as trusted events.

### Proof of Concept
1. Configure a signer with `endpoint = "0.0.0.0:30000"` per the shipped sample config, and let it be reachable from an attacker-controlled network position (e.g., default cloud/VPC deployment, or split node/signer hosts as commonly recommended).
2. Craft a `BlockValidateResponse::Ok(BlockValidateOk { signer_signature_hash, cost: <nonzero>, validation_time_ms, replay_tx_hash: None, .. })` referencing an attacker-chosen `NakamotoBlock` for which the signer already has (or can be fed, e.g. via a prior spoofed/parked proposal) a `BlockInfo` entry with `valid: None`.
3. `POST` this JSON body to `http://<signer-ip>:30000/proposal_response` with no authentication headers.
4. Observe in `handle_block_validate_ok` that the signer records `valid = true`-equivalent state and — assuming its local chainstate view does not independently reject the block — calls `mark_pre_committed` and `send_block_pre_commit`, without the signer's actual stacks-node ever having validated the block. [8](#0-7)

### Citations

**File:** libsigner/src/events.rs (L410-458)
```rust
    /// Wait for the node to post something, and then return it.
    /// Errors are recoverable -- the caller should call this method again even if it returns an
    /// error.
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

**File:** sample/conf/signer/mainnet-signer-conf.toml (L37-50)
```text
# REQUIRED: Local endpoint this signer listens on for events from the node.
# Must match the endpoint in the node's [[events_observer]] section.
endpoint = "0.0.0.0:30000"

# REQUIRED: Network selection.
# Valid values: "mainnet", "testnet", "mocknet"
network = "mainnet"

# REQUIRED: Authorization password for the node's block proposal endpoint.
#
# WARNING: This MUST match the `auth_token` in the stacks-node's
# [connection_options] section. If they do not match, the signer
# cannot communicate with the node and will fail silently.
auth_password = "<YOUR_AUTH_PASSWORD>"
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L1135-1144)
```rust
        // If no authorization is set, then the block proposal endpoint is not enabled
        let Some(password) = &self.auth else {
            return Err(Error::Http(400, "Bad Request.".into()));
        };
        let Some(auth_header) = preamble.headers.get("authorization") else {
            return Err(Error::Http(401, "Unauthorized".into()));
        };
        if auth_header != password {
            return Err(Error::Http(401, "Unauthorized".into()));
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1888-1984)
```rust
    ) {
        crate::monitoring::actions::increment_block_validation_responses(true);
        let signer_signature_hash = &block_validate_ok.signer_signature_hash;
        if self
            .submitted_block_proposal
            .as_ref()
            .map(|(proposal_hash, _)| proposal_hash == signer_signature_hash)
            .unwrap_or(false)
        {
            self.submitted_block_proposal = None;
        }
        if let Some(replay_tx_hash) = block_validate_ok.replay_tx_hash {
            info!("Inserting block validated by replay tx";
                "signer_signature_hash" => %signer_signature_hash,
                "replay_tx_hash" => replay_tx_hash
            );
            self.signer_db
                .insert_block_validated_by_replay_tx(
                    signer_signature_hash,
                    replay_tx_hash,
                    block_validate_ok.replay_tx_exhausted,
                )
                .unwrap_or_else(|e| {
                    warn!("{self}: Failed to insert block validated by replay tx: {e:?}")
                });
        }
        // For mutability reasons, we need to take the block_info out of the map and add it back after processing
        let Some(mut block_info) = self.block_lookup_by_reward_cycle(signer_signature_hash) else {
            // We have not seen this block before. Why are we getting a response for it?
            debug!("{self}: Received a block validate response for a block we have are not tracking. Ignoring...");
            return;
        };

        // Record the block validation time but do not consider stx transfers or boot contract calls
        block_info.validation_time_ms = if block_validate_ok.cost.is_zero() {
            Some(0)
        } else {
            Some(block_validate_ok.validation_time_ms)
        };

        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));

        if block_info.valid.is_some() {
            // We should only have valid set if we have already processed a validation response for this block OR we locally marked it as rejected
            // and responded to it. If we received a new proposal for it that we wished to consider, we would have reset valid to None.
            // This is only really possible when a signer is sharing a node or we have timed out a pending validation and it suddenly arrives.
            warn!(
                "{self}: Already processed a block validate response for block {}. Ignoring validation response.", block_info.block.header.signer_signature_hash(); "valid" => ?block_info.valid,
            );
            return;
        }
        if !block_info.check_static_valid_block() {
            debug!("{self}: Block is syntatically invalid; will not store");
            return;
        }

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
            // have to save the signature _after_ the block info
            let address = self.stacks_address.clone();
            self.handle_block_pre_commit(
                stacks_client,
                sortition_state,
                &address,
                signer_signature_hash,
            );
        }
```
