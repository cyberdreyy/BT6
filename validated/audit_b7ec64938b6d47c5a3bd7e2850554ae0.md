`new_rpc_call_timer` is purely a Prometheus metrics helper — it starts a `HistogramTimer` (or a no-op timer when the `monitoring_prom` feature is disabled) labeled by the RPC path with the origin stripped out.### No vulnerability found for this question.

`new_rpc_call_timer` in `stacks-signer/src/monitoring/mod.rs` is purely a Prometheus instrumentation helper: it strips the origin from an RPC path and starts a `HistogramTimer` (or returns a `NoOpTimer` when the `monitoring_prom` feature is disabled) to record RPC latency metrics. [1](#0-0) [2](#0-1) 

It is invoked from `stacks-signer/src/client/stacks_client.rs` purely to time outbound RPC calls, and has no read or write access to `signer_signature_hash`, the validated block id, the signerdb equivocation record, the reward set/threshold, or `BlockResponse` aggregation logic. [3](#0-2) 

None of the code in this function or its call sites touches block-validation equality, hash computation, or the state machine's decision to sign. There is no reachable path by which a crafted `BlockProposal` or gossiped signer message could cause this timer function to affect what hash gets signed versus what block was validated — it's a metrics-only utility. The question's premise (that this function is where the signed-hash/validated-block divergence occurs) doesn't hold; the actual signing/validation equality logic lives in `stacks-signer/src/v0/signer.rs` around `signer_signature_hash` handling, not in `monitoring/mod.rs`. [4](#0-3)

### Citations

**File:** stacks-signer/src/monitoring/mod.rs (L109-116)
```rust
    /// Start a new RPC call timer.
    /// The `origin` parameter is the base path of the RPC call, e.g. `http://node.com`.
    /// The `origin` parameter is removed from `full_path` when storing in prometheus.
    pub fn new_rpc_call_timer(full_path: &str, origin: &str) -> HistogramTimer {
        let path = super::remove_origin_from_path(full_path, origin);
        let histogram = SIGNER_RPC_CALL_LATENCIES_HISTOGRAM.with_label_values(&[&path]);
        histogram.start_timer()
    }
```

**File:** stacks-signer/src/monitoring/mod.rs (L223-233)
```rust
    /// NoOp timer uses for monitoring when the monitoring feature is not enabled.
    pub struct NoOpTimer;
    impl NoOpTimer {
        /// NoOp method to stop recording when the monitoring feature is not enabled.
        pub fn stop_and_record(&self) {}
    }

    /// Stop and record the no-op timer.
    pub fn new_rpc_call_timer(_full_path: &str, _origin: &str) -> NoOpTimer {
        NoOpTimer
    }
```

**File:** stacks-signer/src/client/stacks_client.rs (L1-1)
```rust
// Copyright (C) 2013-2020 Blockstack PBC, a public benefit corporation
```

**File:** stacks-signer/src/v0/signer.rs (L1-1)
```rust
// Copyright (C) 2020-2026 Stacks Open Internet Foundation
```
