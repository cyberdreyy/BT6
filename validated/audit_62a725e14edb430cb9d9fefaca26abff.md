## Analysis

The Iroha fix (`[fix] #1480: Shut down on panic`) addressed a specific failure mode: without a process-wide panic hook that forces `process::exit`, a panic in a background thread would leave the process alive in a corrupted/inconsistent state instead of terminating loudly for automatic recovery.

**stacks-node has this protection, stacks-signer does not.**

`stacks-node/src/main.rs` installs a global panic hook that forces `process::exit(1)` (plus a core dump) on *any* thread panic: [1](#0-0) 

No equivalent hook exists anywhere in `stacks-signer`. `stacks-signer/src/main.rs::handle_run` simply spawns the signer and blocks on `join()`; when the runloop thread panics, `RunningSigner::join()` swallows the panic (`.inspect_err` logs it, `.unwrap_or(None)`) and returns normally: [2](#0-1) [3](#0-2) 

Since `handle_run` returns and `main()` then falls off the end of the `match` with no explicit `process::exit`, the process terminates with exit code **0**, exactly as if it had shut down cleanly — even though its core signing logic died mid-flight.

The signer's business logic runs entirely inside the single `signer_runloop` thread spawned by `Signer::spawn`: [4](#0-3) 

That thread's message-handling path already contains several `panic!()`/`unwrap_or_else(panic!)` calls directly inside gossip-driven code — e.g. in `store_and_process_block_signature`, invoked whenever a peer's `BlockResponse::Accepted` is processed (`handle_block_response` → `handle_block_signature` → `store_and_process_block_signature`), a normal single-signer-triggerable/gossip path, not requiring a majority: [5](#0-4) 

If any of those calls panics (DB I/O failure on `add_block_signature`/`get_block_signatures`, or a failure in `NakamotoBlockHeader::compute_voting_weight_threshold`), the runloop thread dies. The event-receiver thread keeps running momentarily but its `forward_event` calls start failing once the channel is disconnected, so it too exits its `main_loop`: [6](#0-5) 

The whole signer then goes silent, but the process exits with code 0 — indistinguishable from a graceful shutdown to any external supervisor (systemd `Restart=on-failure`, container orchestration health checks) watching for non-zero exit codes. The signer is permanently wedged out of participation (never signs, never pre-commits, never contributes to the 70% threshold) with no operator-visible failure signal and no automatic restart, unlike a node crash under the `stacks-node` panic hook, which is loud and gets restarted.

I was not able to fully verify (within tool budget) whether `NakamotoBlockHeader::compute_voting_weight_threshold` can be driven to actually panic on adversarial `total_weight` values — that part is a plausible but unconfirmed trigger. The root architectural gap — the *absence* of a signer-side panic hook analogous to the stacks-node one, combined with `join()` silently discarding thread panics — is confirmed by direct code inspection.

### Title
Missing process-wide panic hook in `stacks-signer` lets a panicked runloop thread exit as "clean" (code 0), silently wedging the signer out of consensus - (File: `stacks-signer/src/main.rs`)

### Summary
`stacks-node` installs a panic hook that force-exits the process on any thread panic (`stacks-node/src/main.rs`), guaranteeing loud, restart-triggering failure. `stacks-signer` has no such hook. Its entire signing logic runs in one thread spawned by `libsigner::Signer::spawn`; a panic in that thread is swallowed by `RunningSigner::join()`, and `stacks-signer/src/main.rs::main` then exits with code 0, masking the crash from process supervisors.

### Finding Description
The signer runloop thread executes all block-proposal/pre-commit/signature-response handling, including `store_and_process_block_signature`, which is reachable purely from gossip (`BlockResponse::Accepted` messages from any peer signer) and already contains several `panic!()`/`unwrap_or_else(|_| panic!(...))` calls on DB and threshold-computation failures. If that thread panics for any reason, `RunningSigner::join()` catches the join error, logs it, and returns `None` — no propagation, no process exit code change. `stacks-signer/src/main.rs::handle_run` simply discards the join result, and `main()` returns normally, so the OS sees a normal exit(0). There is no equivalent of the stacks-node panic hook (`process::exit(1)` + core dump) anywhere in the signer binary.

### Impact Explanation
This is a High-severity liveness issue: a crashed signer looks, from the outside, like a signer that shut down cleanly. Any deployment relying on exit-code-based restart policies (systemd `Restart=on-failure`, k8s liveness/restart-on-crash) will not restart it, permanently removing that signer's weight from the 70% pre-commit/signature threshold until an operator manually notices and intervenes — a wedge into never signing valid blocks again.

### Likelihood Explanation
Any panic already present in the gossip/message-handling path of `signer.rs` (several exist today in `store_and_process_block_signature` alone) is sufficient to trigger this; no majority collusion or privileged access is required, only a code path that reaches an existing `panic!`/`unwrap` in normal or adversarial operation.

### Recommendation
Add a process-wide panic hook in `stacks-signer/src/main.rs` (mirroring `stacks-node/src/main.rs`) that logs the panic and calls `process::exit(1)` (or equivalent) so any thread panic terminates the process loudly and restart supervisors can react, instead of letting `join()` absorb it into a clean-looking exit.

### Proof of Concept
1. Run a signer normally participating in a reward cycle.
2. Trigger any of the existing panics in `store_and_process_block_signature` (e.g., simulate a `signer_db.add_block_signature` I/O failure, or a `get_block_signatures` failure) via a peer's `BlockResponse::Accepted` gossip message.
3. Observe: the signer runloop thread panics; `RunningSigner::join()` logs the panic and returns `None`; `stacks-signer` process exits with code 0.
4. Confirm no restart occurs under a standard `Restart=on-failure` supervisor, while the signer's weight remains silently absent from all subsequent votes.

### Citations

**File:** stacks-node/src/main.rs (L304-324)
```rust
fn main() {
    panic::set_hook(Box::new(|panic_info| {
        error!("Process abort due to thread panic: {panic_info}");
        let bt = Backtrace::new();
        error!("Panic backtrace: {bt:?}");

        // force a core dump
        #[cfg(unix)]
        {
            let pid = process::id();
            eprintln!("Dumping core for pid {}", std::process::id());

            use libc::{kill, SIGQUIT};

            // *should* trigger a core dump, if you run `ulimit -c unlimited` first!
            unsafe { kill(pid.try_into().unwrap(), SIGQUIT) };
        }

        // just in case
        process::exit(1);
    }));
```

**File:** libsigner/src/runloop.rs (L107-138)
```rust
impl<EV: EventReceiver<T>, R, T: SignerEventTrait> RunningSigner<EV, R, T> {
    /// Stop the signer, and get the final state
    pub fn stop(mut self) -> Option<R> {
        // kill event receiver
        self.stop_signal.send();

        self.join()
    }

    /// Wait for the signer to terminate, and get the final state.
    /// WARNING: This will hang forever if the event receiver stop signal was never sent/no error occurs.
    pub fn join(self) -> Option<R> {
        debug!("Try join event loop...");
        // wait for event receiver join
        let _ = self.event_join.join().inspect_err(|thread_panic| {
            error!("Event thread panicked with: '{:?}'", &thread_panic);
        });
        info!("Event receiver thread joined");

        // wait for runloop to join
        debug!("Try join signer loop...");
        let result_opt = self
            .signer_join
            .join()
            .inspect_err(|thread_panic| {
                error!("Event thread panicked with: '{:?}'", &thread_panic);
            })
            .unwrap_or(None);

        info!("Signer thread joined");
        result_opt
    }
```

**File:** libsigner/src/runloop.rs (L228-256)
```rust
        // start a thread for the event receiver
        let event_thread = thread::Builder::new()
            .name(format!("event_receiver:{bind_port}"))
            .stack_size(THREAD_STACK_SIZE)
            .spawn(move || event_receiver.main_loop())
            .map_err(|e| {
                error!("EventReceiver failed to start: {:?}", &e);
                EventError::FailedToStart
            })?;

        // start receiving events and doing stuff with them
        let runloop_thread = thread::Builder::new()
            .name(format!("signer_runloop:{bind_port}"))
            .stack_size(THREAD_STACK_SIZE)
            .spawn(move || signer_loop.main_loop(event_recv, result_sender, stop_signaler))
            .map_err(|e| {
                error!("SignerRunLoop failed to start: {:?}", &e);
                ret_stop_signaler.send();
                EventError::FailedToStart
            })?;

        let running_signer = RunningSigner {
            signer_join: runloop_thread,
            event_join: event_thread,
            stop_signal: ret_stop_signaler,
        };

        Ok(running_signer)
    }
```

**File:** stacks-signer/src/main.rs (L115-122)
```rust
fn handle_run(args: RunSignerArgs) {
    debug!("Running signer...");
    let config = GlobalConfig::try_from(&args.config).unwrap();
    let spawned_signer = SpawnedSigner::new(config);
    println!("Signer spawned successfully. Waiting for messages to process...");
    // Wait for the spawned signer to stop (will only occur if an error occurs)
    let _ = spawned_signer.join();
}
```

**File:** stacks-signer/src/v0/signer.rs (L2442-2538)
```rust
    /// Store the block acceptance signature and check if we have reached a consensus decision on the block because of it. If we have, update the block state accordingly and broadcast the block if accepted.
    fn store_and_process_block_signature(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &mut BlockInfo,
        signer_address: &StacksAddress,
        signature: &MessageSignature,
    ) {
        let block_hash = &block_info.signer_signature_hash();
        // signature is valid! store it.
        // if this returns false, it means the signature already exists in the DB, so just return.
        if !self
            .signer_db
            .add_block_signature(block_hash, signer_address, signature)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block signature"))
        {
            return;
        }

        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
            return;
        }

        if block_info.signed_group.is_some() {
            // We have already processed this block to the accepted state. Adding more signatures will not change anything so nothing to check.
            return;
        }
        // do we have enough signatures to broadcast?
        // i.e. is the threshold reached?
        let signatures = self
            .signer_db
            .get_block_signatures(block_hash)
            .unwrap_or_else(|_| panic!("{self}: Failed to load block signatures"));

        // put signatures in order by signer address (i.e. reward cycle order)
        let addrs_to_sigs: HashMap<_, _> = signatures
            .into_iter()
            .filter_map(|sig| {
                let Ok(public_key) = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                    block_hash.bits(),
                    &sig,
                ) else {
                    return None;
                };
                let addr = StacksAddress::p2pkh(self.mainnet, &public_key);
                Some((addr, sig))
            })
            .collect();

        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_signature_weight = self.compute_signature_signing_weight(addrs_to_sigs.keys());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

        if min_weight > total_signature_weight {
            info!("{self}: Received block acceptance, but have not yet reached the acceptance threshold.";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_approved" => total_signature_weight,
                "total_weight" => total_weight,
                "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
        }
        info!("{self}: have reached the block acceptance threshold";
            "signer_signature_hash" => %block_hash,
            "signature_weight" => signature_weight,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "total_weight_approved" => total_signature_weight,
            "total_weight" => total_weight,
            "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
        );

        // have enough signatures to broadcast!
        // move block to LOCALLY accepted state.
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(true) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}");
            }
        }
        let _ = self.signer_db.insert_block(block_info).map_err(|e| {
            warn!("Failed to set group threshold signature timestamp for {block_hash}: {e:?}");
            panic!("{self} Failed to write block to signerdb: {e}");
        });
        self.broadcast_signed_block(stacks_client, block_info.block.clone(), &addrs_to_sigs);
    }
```

**File:** libsigner/src/events.rs (L466-490)
```rust
    /// Forward an event
    /// Return true on success; false on error.
    /// Returning false terminates the event receiver.
    fn forward_event(&mut self, ev: SignerEvent<T>) -> bool {
        if self.out_channels.is_empty() {
            // nothing to do
            error!("No channels connected to event receiver");
            false
        } else if self.out_channels.len() == 1 {
            // avoid a clone
            if let Err(e) = self.out_channels[0].send(ev) {
                error!("Failed to send to signer runloop: {:?}", &e);
                return false;
            }
            true
        } else {
            for (i, out_channel) in self.out_channels.iter().enumerate() {
                if let Err(e) = out_channel.send(ev.clone()) {
                    error!("Failed to send to signer runloop #{}: {:?}", i, &e);
                    return false;
                }
            }
            true
        }
    }
```
