### Title
Unstaked flood of idle QUIC connections can exhaust the global `open_connections` slot budget before stake-based admission runs - (streamer/src/nonblocking/quic.rs)

### Finding Description
`run_server` gates every inbound connection on a single global counter, `StreamerStats::open_connections`, via `ClientConnectionTracker::new(stats.clone(), qos.max_concurrent_connections())` at [1](#0-0) . This check happens strictly before the QUIC handshake completes and before any stake information is known — `build_connection_context`/`try_add_connection` (which apply per-peer-type/stake fairness and pruning) only run later in `setup_connection` after the handshake succeeds, at [2](#0-1) . This means the pre-handshake gate is stake-agnostic: any client, staked or not, can occupy one of the `max_concurrent_connections()` slots simply by completing the handshake.

Once `handle_connection` is running, the connection is kept alive as long as it is not idle at the QUIC transport level, and holding it open does not require ever opening a stream. The inner loop only awaits `connection.accept_uni()` (with no application-level idle timeout of its own) or a cancellation signal, at [3](#0-2) . The only bound on how long an idle connection can be held is the QUIC transport `max_idle_timeout`, configured to `QUIC_MAX_TIMEOUT = 30s` in `configure_server` at [4](#0-3)  and [5](#0-4) . Per QUIC semantics this timer resets on receipt of *any* ack-eliciting packet (e.g., PING frames), not just application stream data, so a client can keep a connection alive indefinitely by sending cheap keep-alive traffic well under 30s intervals, without ever opening a single unidirectional stream and without ever being subject to `throttle_stream`/`wait_for_chunk_timeout`, both of which only engage once a stream exists.

The `ClientConnectionTracker` slot is only released when `ConnectionEntry` (which owns it) is dropped, i.e., when the connection is fully removed from the `ConnectionTable`, which happens when `handle_connection`'s loop exits (`accept_uni()` erroring, e.g. after idle timeout, or cancellation), at [6](#0-5) . So as long as the attacker keeps sending trickle keep-alive packets, the slot is held indefinitely.

### Impact Explanation
This is a non-RPC remote resource exhaustion / ingest starvation issue. If an attacker (or distributed set of attacker-controlled IPs, since per-IP connection caps like `DEFAULT_MAX_QUIC_CONNECTIONS_PER_UNSTAKED_PEER = 8` limit any single IP) opens enough idle, stream-less connections to reach `qos.max_concurrent_connections()` (e.g. 5000 by default for `SwQos`, computed as `(max_staked_connections + max_unstaked_connections) * 5 / 4` at [7](#0-6) ), subsequent legitimate incoming connections — including from staked/leader peers — are refused at the pre-handshake gate with `refused_connections_too_many_open_connections` incremented, at [1](#0-0) , because that gate runs before any stake-aware admission or eviction logic can apply. This can starve the TPU ingest path of new connections for as long as the attacker sustains cheap keep-alive traffic.

### Likelihood Explanation
Requires the attacker to establish and maintain roughly `max_concurrent_connections()` simultaneous QUIC connections, respecting per-IP connection caps (`max_connections_per_peer`) and per-IP/global connection-rate limiters (`ConnectionRateLimiter`, `overall_connection_rate_limiter`), which bound how fast new connections can be opened but do not limit how long an established, otherwise-idle connection can be kept alive. Achieving thousands of concurrent connections from enough distinct source IPs requires meaningful distributed infrastructure (a botnet-scale set of IPs), which is a nontrivial but realistic capability for a determined unprivileged network attacker, and does not require any privileged, staked, or configuration-dependent capability. The exploit only needs standard QUIC connection establishment and periodic keep-alive traffic — no stream data, no valid transaction, and no stake are needed.

### Recommendation
- Do not decouple the global open-connection admission entirely from stake: consider retaining a reservation of concurrent-connection slots specifically for unstaked peers (already partially reflected in `max_unstaked_connections`), and make the pre-handshake `ClientConnectionTracker` budget stake-aware once the handshake is observed, evicting/pruning low-value unstaked idle connections to admit staked ones, similar to `ConnectionTable::prune_random`/`prune_oldest`.
- Add an explicit idle-connection watchdog inside `handle_connection` that closes connections which have not opened any stream (or produced any packet) within a bounded window shorter than `QUIC_MAX_TIMEOUT`, independent of QUIC-level keep-alive traffic.
- Consider lowering `max_idle_timeout` for unstaked/unauthenticated connections, or tying idle-timeout renewal to observed application-level activity (stream opens) rather than any transport-level packet.

### Proof of Concept
```rust
// Pseudocode / outline for a Rust integration test in streamer/src/nonblocking/quic.rs test module.
// 1. Spawn a QUIC server with a small `max_concurrent_connections` (e.g. via SwQosConfig with
//    max_staked_connections + max_unstaked_connections small, so C = (that sum) * 5 / 4).
// 2. From C distinct source IPs/ports (bound_to_localhost_unique or explicit bind IPs), complete
//    the QUIC handshake against the server but never call `open_uni()`.
// 3. Keep each connection alive by relying on QUIC's automatic ACK/PING keep-alive well under
//    QUIC_MAX_TIMEOUT (30s) — e.g., loop calling `connection.stats()` or issue no explicit
//    keep-alive if quinn auto keep-alives are enabled; otherwise call `connection.send_datagram`
//    is disabled, so rely on transport-level idle timer refresh from any ACK-eliciting frame.
// 4. Attempt one more legitimate connection from a fresh IP and open a stream with a small payload.
// 5. Assert: the (C+1)-th connection is refused — `stats.refused_connections_too_many_open_connections`
//    increments, and the legitimate client's `connecting.await` fails or the stream write errors —
//    while all C attacker connections remain in `open_connections` count.
```
Note: due to index size limits, the exact quinn API usage for forcing keep-alive/idle-timeout renewal without opening a stream should be validated directly against the vendored `quinn`/`quinn-proto` version in the repo; a full runnable reproduction should be built and executed in a Devin session with repo and terminal access to confirm exact timing assertions (`QUIC_CONNECTION_HANDSHAKE_TIMEOUT`, `QUIC_MAX_TIMEOUT`) against this codebase's actual constants. [8](#0-7) [9](#0-8) [4](#0-3)

### Citations

**File:** streamer/src/nonblocking/quic.rs (L236-252)
```rust
impl ClientConnectionTracker {
    /// Check the max_concurrent_connections limit and if it is within the limit
    /// create ClientConnectionTracker and increment open connection count. Otherwise returns Err
    fn new(stats: Arc<StreamerStats>, max_concurrent_connections: usize) -> Result<Self, ()> {
        let open_connections = stats.open_connections.fetch_add(1, Ordering::Relaxed);
        if open_connections >= max_concurrent_connections {
            stats.open_connections.fetch_sub(1, Ordering::Relaxed);
            debug!(
                "There are too many concurrent connections opened already: open: \
                 {open_connections}, max: {max_concurrent_connections}"
            );
            return Err(());
        }

        Ok(Self { stats })
    }
}
```

**File:** streamer/src/nonblocking/quic.rs (L371-379)
```rust
            let Ok(client_connection_tracker) =
                ClientConnectionTracker::new(stats.clone(), qos.max_concurrent_connections())
            else {
                stats
                    .refused_connections_too_many_open_connections
                    .fetch_add(1, Ordering::Relaxed);
                incoming.refuse();
                continue;
            };
```

**File:** streamer/src/nonblocking/quic.rs (L512-532)
```rust
                let mut conn_context = qos.build_connection_context(&new_connection);
                if let Some(cancel_connection) = qos
                    .try_add_connection(
                        client_connection_tracker,
                        &new_connection,
                        &mut conn_context,
                    )
                    .await
                {
                    tasks.spawn(handle_connection(
                        packet_sender.clone(),
                        from,
                        new_connection,
                        stats,
                        server_params.wait_for_chunk_timeout,
                        server_params.max_stream_data_bytes,
                        conn_context.clone(),
                        qos,
                        cancel_connection,
                    ));
                }
```

**File:** streamer/src/nonblocking/quic.rs (L583-622)
```rust
async fn handle_connection<Q, C>(
    packet_sender: Sender<PacketBatch>,
    remote_address: SocketAddr,
    connection: Connection,
    stats: Arc<StreamerStats>,
    wait_for_chunk_timeout: Duration,
    max_stream_data_bytes: u32,
    context: C,
    qos: Arc<Q>,
    cancel: CancellationToken,
) where
    Q: QosController<C> + Send + Sync + 'static,
    C: ConnectionContext + Send + Sync + 'static,
{
    let peer_type = context.peer_type();
    debug!(
        "quic new connection {} streams: {} connections: {}",
        remote_address,
        stats.active_streams.load(Ordering::Relaxed),
        stats.total_connections.load(Ordering::Relaxed),
    );
    stats.total_connections.fetch_add(1, Ordering::Relaxed);

    // cache the RTT to avoid grabbing lock for every stream.
    // we only use that for some stats here, so if it gets stale during connection lifetime
    // it is not the end of the world.
    let rtt = connection.rtt();
    'conn: loop {
        // Wait for new streams. If the peer is disconnected we get a cancellation signal and stop
        // the connection task.
        let mut stream = select! {
            stream = connection.accept_uni() => match stream {
                Ok(stream) => stream,
                Err(e) => {
                    debug!("stream error: {e:?}");
                    break;
                }
            },
            _ = cancel.cancelled() => break,
        };
```

**File:** streamer/src/nonblocking/quic.rs (L860-914)
```rust
struct ConnectionEntry<S: OpaqueStreamerCounter> {
    cancel: CancellationToken,
    peer_type: ConnectionPeerType,
    last_update: Arc<AtomicU64>,
    port: u16,
    // We do not explicitly use it, but its drop is triggered when ConnectionEntry is dropped.
    _client_connection_tracker: ClientConnectionTracker,
    connection: Option<Connection>,
    stream_counter: Arc<S>,
}

impl<S: OpaqueStreamerCounter> ConnectionEntry<S> {
    fn new(
        cancel: CancellationToken,
        peer_type: ConnectionPeerType,
        last_update: Arc<AtomicU64>,
        port: u16,
        client_connection_tracker: ClientConnectionTracker,
        connection: Option<Connection>,
        stream_counter: Arc<S>,
    ) -> Self {
        Self {
            cancel,
            peer_type,
            last_update,
            port,
            _client_connection_tracker: client_connection_tracker,
            connection,
            stream_counter,
        }
    }

    fn last_update(&self) -> u64 {
        self.last_update.load(Ordering::Relaxed)
    }

    fn stake(&self) -> u64 {
        match self.peer_type {
            ConnectionPeerType::Unstaked => 0,
            ConnectionPeerType::Staked(stake) => stake,
        }
    }
}

impl<S: OpaqueStreamerCounter> Drop for ConnectionEntry<S> {
    fn drop(&mut self) {
        if let Some(conn) = self.connection.take() {
            conn.close(
                CONNECTION_CLOSE_CODE_DROPPED_ENTRY.into(),
                CONNECTION_CLOSE_REASON_DROPPED_ENTRY,
            );
        }
        self.cancel.cancel();
    }
}
```

**File:** streamer/src/quic.rs (L36-38)
```rust
/// QUIC connection idle timeout. The connection will be closed if there are no activities on it
/// within the timeout window. The chosen value is default for quinn.
pub const QUIC_MAX_TIMEOUT: Duration = Duration::from_secs(30);
```

**File:** streamer/src/quic.rs (L119-120)
```rust
    let timeout = IdleTimeout::try_from(QUIC_MAX_TIMEOUT).unwrap();
    config.max_idle_timeout(Some(timeout));
```

**File:** streamer/src/nonblocking/swqos.rs (L518-522)
```rust
    fn max_concurrent_connections(&self) -> usize {
        // Allow 25% more connections than required to allow for handshake

        (self.config.max_staked_connections + self.config.max_unstaked_connections) * 5 / 4
    }
```
