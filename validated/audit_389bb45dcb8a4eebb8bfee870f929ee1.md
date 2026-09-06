### Title
Unbounded blocking HTTP calls in `StacksClient` (no request timeout) can wedge the signer's single-threaded event loop - ([File: stacks-signer/src/client/stacks_client.rs])

### Summary
`StacksClient`, the signer's only channel for talking to its configured stacks-node, is built from a bare `reqwest::blocking::Client::new()` with no `.timeout(...)` configured, and its retry wrapper caps total elapsed *retry* time but not the duration of any single in-flight `.send()` call. Every signer decision path (block validation submission, tenure-tip/sortition lookups used for conflict resolution, reward-set/account/peer-info fetches, block posting) goes through this client, and all of it runs synchronously inside the signer's single runloop thread. A stalled TCP connection or unresponsive node-side handler on any of these endpoints can therefore block the signer indefinitely — this is the same bug class as CVE-2024-12777 (CWE-1088, synchronous access of a remote resource without a timeout causing single-threaded server hang).

### Finding Description
`StacksClient::from`/`StacksClient::new` construct the HTTP client with no explicit timeout: [1](#0-0) 

That same `stacks_node_client` is reused for every RPC the signer makes to its node: `get_tenure_tip`, `get_peer_info`, `get_reward_set`, `get_account_entry`, `post_block`, and `submit_block_for_validation`, e.g.: [2](#0-1) [3](#0-2) [4](#0-3) 

The retry wrapper (`retry_with_exponential_backoff`) bounds total *retry* time via `BACKOFF_MAX_ELAPSED` (5s), but that only limits how many times a *failed* request is retried — it does nothing to bound the duration of a single `.send()` call that is still pending (e.g., TCP connected but the peer never writes a response, a classic slow-loris style stall): [5](#0-4) 

Meanwhile, the signer's entire business logic is single-threaded: `SignerRunLoop::main_loop` pulls one event at a time and calls `run_one_pass` synchronously, and there is no concurrency inside that call — proposal handling, pre-commit tallying, and rejection/acceptance logic (`handle_block_proposal`, `handle_block_pre_commit`, `check_block_against_signer_db_state`) all execute on this one thread and issue blocking `StacksClient` calls as part of that processing: [6](#0-5) 

If any of these node calls hangs (unresponsive socket, network stall, connection accepted but never answered), the runloop thread is blocked indefinitely inside `.send()`. Because this is the *only* thread executing signer logic, the signer stops entirely: it can no longer process `BlockValidationResponse`, `BlockPreCommit`, or new `BlockProposal` events, cannot broadcast `StateMachineUpdate`s, and cannot run its own protective timeout logic (`check_submitted_block_proposal`), which itself only runs as part of `process_event`'s per-pass housekeeping and therefore also stalls.

### Impact Explanation
This is a liveness wedge matching the "High" bar: "a signer wedged into never signing valid blocks." Once wedged, the affected signer contributes neither acceptances nor rejections nor pre-commits to any future block, effectively dropping out of the signer set until the underlying connection unblocks (which may never happen for a truly stalled socket, since the OS-level TCP timeout can be very long or, on some platforms/proxies, effectively indefinite). If this condition is reached across enough signers concurrently (e.g., correlated network conditions or a shared upstream proxy), it can push the network toward being unable to reach voting thresholds, though a single signer's wedge already satisfies the liveness-wedge criterion.

### Likelihood Explanation
The condition can be triggered by an ordinary network fault (partial connectivity, an intermediary proxy that accepts connections but stalls, DNS resolving to a dead/black-holed IP after a node restart, or degraded infra) since there is no code-level timeout defense — only whatever the OS TCP stack eventually enforces, which is not bounded to any operationally sane value. It does not require a majority of signers, another signer's key, or local access; it only requires the signer's configured `node_host` connection to become unresponsive, which is a plausible operational fault, not a purely theoretical one, given the request path is on the hot loop for every proposal decision.

### Recommendation
- Configure an explicit `.timeout(Duration::from_secs(N))` (and ideally a separate `.connect_timeout(...)`) on the `reqwest::blocking::Client` used by `StacksClient`, for every HTTP call the client makes.
- Ensure the timeout is short enough (e.g., a few seconds) relative to `block_proposal_validation_timeout_ms` so a stalled node connection surfaces as a fast, recoverable error (already handled via `ClientError::ReqwestError`/`RequestFailure`) rather than an indefinite block.
- Verify all call sites treat a client-side timeout the same as other transient connectivity errors (already the pattern for `RequestFailure`/`backoff::Error::transient`), so the signer can continue processing subsequent events and eventually recover via its existing rejection-on-timeout code path (`check_submitted_block_proposal`) rather than freezing before that logic can even run.

### Proof of Concept
1. Configure a signer against a `node_host` that accepts the TCP connection but never writes an HTTP response (e.g., `nc -l <port>` or an intercepting proxy that stalls all responses).
2. Trigger any decision path that calls `StacksClient` synchronously from the runloop thread — for example, propose a block whose pre-commit/rejection evaluation calls `get_tenure_tip`/`get_sortition_by_burn_hash`/`get_peer_info` against the stalled node, or simply let the signer poll `get_peer_info`/`get_reward_set` during normal operation.
3. Observe that `stacks_node_client.get(...).send()` (or `.post(...).send()`) never returns because no timeout is set (`stacks-signer/src/client/stacks_client.rs` lines 93/121, 168-176, 300-308).
4. Observe that the signer's single runloop thread (`libsigner/src/runloop.rs::main_loop`) is blocked inside `run_one_pass`, so no further `BlockValidationResponse`, `BlockPreCommit`, or `BlockProposal` events are processed and `check_submitted_block_proposal`'s own timeout logic never gets a chance to run — the signer is wedged until the OS-level connection eventually fails or the process is restarted.

Note: I was not able to fully trace every call site of `get_tenure_tip`/`get_sortition_by_burn_hash` inside `stacks-signer/src/chainstate/v1.rs`/`v2.rs` within the available iterations to enumerate every reachable trigger path from a crafted miner proposal; the core claim (no timeout on the shared blocking HTTP client, used synchronously on the single signer runloop thread) is directly supported by the cited code and is sufficient on its own to establish the liveness-wedge risk.

### Citations

**File:** stacks-signer/src/client/stacks_client.rs (L86-98)
```rust
impl From<&GlobalConfig> for StacksClient {
    fn from(config: &GlobalConfig) -> Self {
        Self {
            stacks_address: config.stacks_address.clone(),
            http_origin: format!("http://{}", config.node_host),
            tx_version: config.network.to_transaction_version(),
            chain_id: config.to_chain_id(),
            stacks_node_client: reqwest::blocking::Client::new(),
            mainnet: config.network.is_mainnet(),
            auth_password: config.auth_password.clone(),
        }
    }
}
```

**File:** stacks-signer/src/client/stacks_client.rs (L160-183)
```rust
    /// Get the header of the highest known block in the given tenure
    pub fn get_tenure_tip(
        &self,
        tenure_id: &ConsensusHash,
    ) -> Result<BlockHeaderWithMetadata, ClientError> {
        debug!("StacksClient: Getting tenure tip";
               "consensus_hash" => %tenure_id,
        );
        let send_request = || {
            self.stacks_node_client
                .get(self.tenure_tip_path(tenure_id))
                .send()
                .map_err(|e| {
                    warn!("Signer failed to request latest sortition"; "err" => ?e);
                    e
                })
        };
        let response = send_request()?;
        if !response.status().is_success() {
            return Err(ClientError::RequestFailure(response.status()));
        }
        let sortition_info = response.json()?;
        Ok(sortition_info)
    }
```

**File:** stacks-signer/src/client/stacks_client.rs (L280-316)
```rust
    /// Submit the block proposal to the stacks node. The block will be validated and returned via the HTTP endpoint for Block events.
    pub fn submit_block_for_validation(
        &self,
        block: NakamotoBlock,
        replay_txs: Option<Vec<StacksTransaction>>,
    ) -> Result<(), ClientError> {
        debug!("StacksClient: Submitting block for validation";
            "signer_signature_hash" => %block.header.signer_signature_hash(),
            "block_id" => %block.header.block_id(),
            "block_height" => %block.header.chain_length,
        );
        let block_proposal = NakamotoBlockProposal {
            block,
            chain_id: self.chain_id,
            replay_txs,
        };
        let timer = crate::monitoring::actions::new_rpc_call_timer(
            &self.block_proposal_path(),
            &self.http_origin,
        );
        let send_request = || {
            self.stacks_node_client
                .post(self.block_proposal_path())
                .header("Content-Type", "application/json")
                .header(AUTHORIZATION, self.auth_password.clone())
                .json(&block_proposal)
                .send()
                .map_err(backoff::Error::transient)
        };

        let response = retry_with_exponential_backoff(send_request)?;
        timer.stop_and_record();
        if !response.status().is_success() {
            return Err(ClientError::RequestFailure(response.status()));
        }
        Ok(())
    }
```

**File:** stacks-signer/src/client/stacks_client.rs (L472-492)
```rust
    /// Get the current peer info data from the stacks node
    pub fn get_peer_info(&self) -> Result<PeerInfo, ClientError> {
        debug!("StacksClient: Getting peer info");
        let timer = crate::monitoring::actions::new_rpc_call_timer(
            &self.core_info_path(),
            &self.http_origin,
        );
        let send_request = || {
            self.stacks_node_client
                .get(self.core_info_path())
                .send()
                .map_err(backoff::Error::transient)
        };
        let response = retry_with_exponential_backoff(send_request)?;
        timer.stop_and_record();
        if !response.status().is_success() {
            return Err(ClientError::RequestFailure(response.status()));
        }
        let peer_info_data = response.json::<PeerInfo>()?;
        Ok(peer_info_data)
    }
```

**File:** stacks-signer/src/client/mod.rs (L33-38)
```rust
/// Backoff timer initial interval in milliseconds
const BACKOFF_INITIAL_INTERVAL: u64 = 128;
/// Backoff timer max interval in milliseconds
const BACKOFF_MAX_INTERVAL: u64 = 16384;
/// Backoff timer max elapsed seconds
const BACKOFF_MAX_ELAPSED: u64 = 5;
```

**File:** libsigner/src/runloop.rs (L59-82)
```rust
    fn main_loop<EVST: EventStopSignaler>(
        &mut self,
        event_recv: Receiver<SignerEvent<T>>,
        result_send: Sender<R>,
        mut event_stop_signaler: EVST,
    ) -> Option<R> {
        info!("Signer runloop begin");
        loop {
            let poll_timeout = self.get_event_timeout();
            let next_event_opt = match event_recv.recv_timeout(poll_timeout) {
                Ok(event) => Some(event),
                Err(RecvTimeoutError::Timeout) => None,
                Err(RecvTimeoutError::Disconnected) => {
                    info!("Event receiver disconnected");
                    return None;
                }
            };
            if let Some(final_state) = self.run_one_pass(next_event_opt, &result_send) {
                info!("Runloop exit; signaling event-receiver to stop");
                event_stop_signaler.send();
                return Some(final_state);
            }
        }
    }
```
