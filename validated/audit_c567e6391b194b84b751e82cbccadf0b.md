### Title
Stake-weighted QUIC ingest QoS grants a flat per-connection minimum stream/slot allocation that is not scaled down for Sybil-split stake, allowing low-stake identities to multiply guaranteed TPU ingest capacity - (File: streamer/src/nonblocking/swqos.rs)

### Summary
Agave's QUIC TPU ingest QoS layer (`SwQos`) assigns each *staked* connection a **flat, non‑proportional minimum** of `QUIC_MIN_STAKED_CONCURRENT_STREAMS` (128) concurrent streams and a dedicated slot in the shared `staked_connection_table` (capacity `max_staked_connections`, default 2000), as long as the connecting pubkey's stake fraction clears a very low bar. Because these floors are granted **per pubkey/connection** rather than scaled to an entity's *aggregate* real stake, an actor can split a fixed amount of stake across many low‑stake identities to accumulate strictly more guaranteed concurrent-stream capacity and table slots than the same stake held by a single identity would receive — the same "guaranteed allocation, ungated by real entitlement, multiplied across addresses" root cause described in the referenced TokenSale finding.

### Finding Description
`build_connection_context` classifies an incoming QUIC peer as `Staked` if its stake fraction of total stake is at or above `min_stake_ratio`: [1](#0-0) 

`min_stake_ratio` is `1 / (max_streams_per_ms * STREAM_THROTTLING_INTERVAL_MS)`. With defaults `DEFAULT_MAX_STREAMS_PER_MS = 500` and `STREAM_THROTTLING_INTERVAL_MS = 100`: [2](#0-1) [3](#0-2) 

this threshold is `1/50000` (0.002%) of total network stake — trivial to clear with a small amount of real, delegated stake spread across many validator identities.

Once classified `Staked(stake)`, the connection is admitted into `staked_connection_table` (separate, larger-priority table from the unstaked one) and is granted a concurrent-uni-stream ceiling computed here: [4](#0-3) 

Note that for *any* stake fraction above zero (as long as it wasn't already downgraded to `Unstaked`), the result is clamped to a **floor of `QUIC_MIN_STAKED_CONCURRENT_STREAMS` (128)** — this floor does not shrink as the number of identities used to split a fixed total stake grows. This is applied per admitted connection in `cache_new_connection`: [5](#0-4) 

Each such low-stake pubkey is additionally allowed up to `max_connections_per_staked_peer` (default 16) simultaneous connections: [6](#0-5) [7](#0-6) 

giving a single low-stake identity up to `128 * 16 = 2048` guaranteed concurrent uni-streams, and every additional Sybil identity above the 0.002% threshold adds another slot in the shared `staked_connection_table` (up to the global `max_staked_connections` cap) plus another 128-stream floor — none of which is deducted from, or proportional to, the actor's real *aggregate* stake share the way a single, unsplit high-stake connection is capped (`QUIC_MAX_STAKED_CONCURRENT_STREAMS`, 512, regardless of stake share above the interpolation range): [8](#0-7) 

This mirrors the reported bug class exactly: `calculateMaxAllocation` in TokenSale grants a flat `maxAllocation` regardless of a user's real (zero) tier entitlement, and since the grant is per-address rather than per-aggregate-identity, splitting across addresses multiplies the total obtainable resource. Here, the flat `QUIC_MIN_STAKED_CONCURRENT_STREAMS` floor and the per-pubkey `staked_connection_table` slot play the same role as `maxAllocation`, and QUIC identity pubkeys play the role of "addresses."

The rate-based EMA throttle (`available_load_capacity_in_throttling_duration`) does scale proportionally to `stake/total_stake` and only adds a trivial `+1` floor over the unstaked baseline, so it is not by itself exploitable: [9](#0-8) 
The exploitable surface is specifically the **connection-admission and concurrent-stream-ceiling path** (`staked_connection_table` slot count and `max_uni_streams`), which are granted per identity with a non-scaling floor.

### Impact Explanation
An actor controlling even a small amount of real stake, if split across enough validator/forwarder identities each individually above the ~0.002%-of-total-stake bar, can occupy a disproportionate share of the fixed `max_staked_connections` (2000) table capacity and accumulate many multiples of the guaranteed 128-stream-per-connection floor (up to 2048 concurrent streams per identity via the 16-connections-per-peer allowance). This crowds out legitimate staked validators/RPC nodes from TPU QUIC ingest capacity (a form of ingest starvation for others), and lets the Sybil actor push a disproportionately large volume of transactions into the leader's TPU relative to their real proportional stake — undermining the stake-weighted QoS design intent that "resources should be proportional to stake."

### Likelihood Explanation
Exploitation requires only enough SOL to satisfy the trivial `min_stake_ratio` threshold once split across multiple validator identities (a cost, but a low and controllable one relative to the resource gained, since the floor/slot benefit does not scale down per split identity). No special access, leaked keys, or consensus-level fault is needed — a normal network participant with a modest, split stake and standard QUIC client behavior can reach this path.

### Recommendation
Scale the guaranteed floor (`QUIC_MIN_STAKED_CONCURRENT_STREAMS`) and the staked-connection-table admission logic by the connecting entity's real, aggregate stake share rather than granting a flat per-pubkey minimum, or track and cap total granted floor-resources per staking identity cluster (e.g., proportionally reduce the per-connection floor as the number of concurrently-admitted low-stake connections grows, similar to how `calculateMaxAllocation` should return 0 rather than a flat minimum when the user's real tier entitlement is 0).

### Proof of Concept
Conceptually:
1. Compute `min_stake_ratio = 1/(max_streams_per_ms * STREAM_THROTTLING_INTERVAL_MS)` (0.002% with defaults) as in `build_connection_context` (`swqos.rs:318-329`).
2. Generate `N` validator identities, each delegated stake slightly above `min_stake_ratio * total_stake` (aggregate real cost ≈ `N * 0.002%` of total stake).
3. Open up to `max_connections_per_staked_peer` (16) QUIC connections per identity to the target TPU.
4. Each connection is classified `Staked`, admitted into `staked_connection_table` (space permitting up to `max_staked_connections`), and assigned `max_uni_streams >= QUIC_MIN_STAKED_CONCURRENT_STREAMS` (128) via `compute_max_allowed_uni_streams_with_rtt` (`swqos.rs:147-179`) and `cache_new_connection` (`swqos.rs:196-224`).
5. Total guaranteed concurrent-stream capacity obtained (`N * up to 2048`) and table-slot occupancy scale linearly with the number of split identities `N`, not with the actor's real aggregate stake fraction, demonstrating the same "flat guaranteed allocation bypassed via multiple addresses" pattern as the TokenSale finding.

### Citations

**File:** streamer/src/nonblocking/swqos.rs (L36-48)
```rust
// Empirically found max number of concurrent streams
// that seems to maximize TPS on GCE (higher values don't seem to
// give significant improvement or seem to impact stability)
pub const QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS: u32 = 128;
pub const QUIC_MIN_STAKED_CONCURRENT_STREAMS: u32 = 128;

// Set the maximum concurrent stream numbers to avoid excessive streams.
// The value was lowered from 2048 to reduce contention of the limited
// receive_window among the streams which is observed in CI bench-tests with
// forwarded packets from staked nodes.
pub const QUIC_MAX_STAKED_CONCURRENT_STREAMS: u32 = 512;

pub const QUIC_TOTAL_STAKED_CONCURRENT_STREAMS: u32 = 100_000;
```

**File:** streamer/src/nonblocking/swqos.rs (L147-179)
```rust
fn compute_max_allowed_uni_streams_with_rtt(
    rtt_millis: u32,
    peer_type: ConnectionPeerType,
    total_stake: u64,
) -> u32 {
    let streams = match peer_type {
        ConnectionPeerType::Staked(peer_stake) => {
            // No checked math for f64 type. So let's explicitly check for 0 here
            if total_stake == 0 || peer_stake > total_stake {
                warn!(
                    "Invalid stake values: peer_stake: {peer_stake:?}, total_stake: \
                     {total_stake:?}"
                );

                QUIC_MIN_STAKED_CONCURRENT_STREAMS
            } else {
                let delta = (QUIC_TOTAL_STAKED_CONCURRENT_STREAMS
                    - QUIC_MIN_STAKED_CONCURRENT_STREAMS) as f64;

                (((peer_stake as f64 / total_stake as f64) * delta) as u32
                    + QUIC_MIN_STAKED_CONCURRENT_STREAMS)
                    .clamp(
                        QUIC_MIN_STAKED_CONCURRENT_STREAMS,
                        QUIC_MAX_STAKED_CONCURRENT_STREAMS,
                    )
            }
        }
        ConnectionPeerType::Unstaked => QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS,
    };
    // scale amount of streams based on RTT if RTT is larger than REFERENCE_RTT_MS
    // multiply first then divide to avoid rounding errors.
    (streams.saturating_mul(rtt_millis.clamp(REFERENCE_RTT_MS, MAX_RTT_MS))) / REFERENCE_RTT_MS
}
```

**File:** streamer/src/nonblocking/swqos.rs (L196-224)
```rust
        // get current RTT and limit it to MAX_RTT_MS right away
        let rtt_millis = connection.rtt().as_millis().min(MAX_RTT_MS as u128) as u32;
        let max_uni_streams = VarInt::from_u32(compute_max_allowed_uni_streams_with_rtt(
            rtt_millis,
            conn_context.peer_type(),
            conn_context.total_stake,
        ));
        let remote_addr = conn_context.remote_address;

        let max_connections_per_peer = match conn_context.peer_type() {
            ConnectionPeerType::Unstaked => self.config.max_connections_per_unstaked_peer,
            ConnectionPeerType::Staked(_) => self.config.max_connections_per_staked_peer,
        };
        if let Some((last_update, cancel_connection, stream_counter)) = connection_table_l
            .try_add_connection(
                ConnectionTableKey::new(remote_addr.ip(), conn_context.remote_pubkey),
                remote_addr.port(),
                client_connection_tracker,
                Some(connection.clone()),
                conn_context.peer_type(),
                conn_context.last_update.clone(),
                max_connections_per_peer,
                || Arc::new(ConnectionStreamCounter::new()),
            )
        {
            update_open_connections_stat(&self.stats, &connection_table_l);
            drop(connection_table_l);

            connection.set_max_concurrent_uni_streams(max_uni_streams);
```

**File:** streamer/src/nonblocking/swqos.rs (L314-329)
```rust
            |(pubkey, stake, total_stake)| {
                // The heuristic is that the stake should be large enough to have 1 stream pass through within one throttle
                // interval during which we allow max (MAX_STREAMS_PER_MS * STREAM_THROTTLING_INTERVAL_MS) streams.

                let peer_type = {
                    let max_streams_per_ms = self.staked_stream_load_ema.max_streams_per_ms();
                    let min_stake_ratio =
                        1_f64 / (max_streams_per_ms * STREAM_THROTTLING_INTERVAL_MS) as f64;
                    let stake_ratio = stake as f64 / total_stake as f64;
                    if stake_ratio < min_stake_ratio {
                        // If it is a staked connection with ultra low stake ratio, treat it as unstaked.
                        ConnectionPeerType::Unstaked
                    } else {
                        ConnectionPeerType::Staked(stake)
                    }
                };
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L16-23)
```rust
/// Max TPS allowed for unstaked connection
const MAX_UNSTAKED_TPS: u64 = 200;
/// Expected fraction of max TPS to be consumed by unstaked connections
const EXPECTED_UNSTAKED_STREAMS_RATIO: f64 = 0.20;

pub const STREAM_THROTTLING_INTERVAL_MS: u64 = 100;
pub const STREAM_THROTTLING_INTERVAL: Duration =
    Duration::from_millis(STREAM_THROTTLING_INTERVAL_MS);
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L167-188)
```rust
    pub(crate) fn available_load_capacity_in_throttling_duration(
        &self,
        peer_type: ConnectionPeerType,
        total_stake: u64,
    ) -> u64 {
        match peer_type {
            ConnectionPeerType::Unstaked => self.max_unstaked_load_in_throttling_window,
            ConnectionPeerType::Staked(stake) => {
                if self.staked_throttling_enabled.load(Ordering::Relaxed) {
                    // 1 is added to `max_unstaked_load_in_throttling_window` to guarantee that staked
                    // clients get at least 1 more number of streams than unstaked connections.
                    self.max_staked_load_in_throttling_window
                        .saturating_mul(stake)
                        .checked_div(total_stake)
                        .unwrap_or(self.max_unstaked_load_in_throttling_window + 1)
                        .max(self.max_unstaked_load_in_throttling_window + 1)
                } else {
                    self.max_staked_load_in_throttling_window
                }
            }
        }
    }
```

**File:** streamer/src/quic.rs (L40-48)
```rust
// allow multiple connections for NAT and any open/close overlap
pub const DEFAULT_MAX_QUIC_CONNECTIONS_PER_UNSTAKED_PEER: usize = 8;

// allow multiple connections per ID for geo-distributed forwarders
pub const DEFAULT_MAX_QUIC_CONNECTIONS_PER_STAKED_PEER: usize = 16;

pub const DEFAULT_MAX_STAKED_CONNECTIONS: usize = 2000;

pub const DEFAULT_MAX_UNSTAKED_CONNECTIONS: usize = 2000;
```

**File:** streamer/src/quic.rs (L50-52)
```rust
/// Limit to 500K PPS
pub const DEFAULT_MAX_STREAMS_PER_MS: u64 = 500;

```
