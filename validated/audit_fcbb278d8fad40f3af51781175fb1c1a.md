### Title
QUIC Initial-only connection flood exhausts `ClientConnectionTracker`'s global concurrent-connection cap and starves new TPU connections - ([File: streamer/src/nonblocking/quic.rs])

### Summary
The QUIC server admits any `Incoming` connection into the global `ClientConnectionTracker` slot pool (capped at `max_concurrent_connections()`) before the handshake completes, and holds that slot for up to `QUIC_CONNECTION_HANDSHAKE_TIMEOUT` (2s) even if the client never sends a `Handshake` packet. Both the per-IP and global rate limiters only consume tokens *after* a successful handshake, so an attacker who never completes the handshake never gets rate-limited, allowing the global slot pool to be kept perpetually full from a single low-bandwidth source.

### Finding Description
In `run_server` (`streamer/src/nonblocking/quic.rs`), for every incoming QUIC `Initial` packet:
1. The overall token-bucket check `overall_connection_rate_limiter.current_tokens() == 0` only rejects when the bucket is already empty [1](#0-0) .
2. The per-IP check `rate_limiter.is_allowed(&ip)` returns `true` whenever the limiter has no existing record for that IP [2](#0-1) .
3. If both checks pass, a `ClientConnectionTracker` is created against the global cap `qos.max_concurrent_connections()`, and if the cap is exceeded the connection is refused with `refused_connections_too_many_open_connections` incremented [3](#0-2) .
4. The connection is then handed to `setup_connection`, which waits up to `QUIC_CONNECTION_HANDSHAKE_TIMEOUT = Duration::from_secs(2)` for the handshake to finish [4](#0-3) [5](#0-4) .
5. Crucially, `rate_limiter.register_connection(&from.ip())` (the call that actually *consumes* a per-IP token) and `overall_connection_rate_limiter.consume_tokens(1)` (the call that consumes the global token) are only invoked **after** the handshake succeeds [6](#0-5) .
6. `ClientConnectionTracker` is only decremented when it is dropped, i.e. when `setup_connection` returns (on success, error, or after the 2s timeout) [7](#0-6) .

Consequences: an attacker who sends `Initial` packets but never sends the corresponding `Handshake` packet never registers with the per-IP limiter and never consumes global-bucket tokens, so neither rate limiter throttles this behavior. Each such Initial packet still occupies one of the finite `max_concurrent_connections()` slots for up to 2 seconds. Because slots are consumed on `Incoming` acceptance (well before handshake completion) and QUIC connections are keyed by Connection ID rather than source 4-tuple, a single attacking host can open many pending "connections" concurrently from one IP/port by using distinct QUIC Connection IDs, sustaining a flood rate of roughly `max_concurrent_connections() / 2s` Initial packets/sec (e.g. with default `DEFAULT_MAX_STAKED_CONNECTIONS=2000` and `DEFAULT_MAX_UNSTAKED_CONNECTIONS=2000`, cap = `(2000+2000)*5/4 = 5000`, requiring only ~2500 Initial packets/sec) [8](#0-7) [9](#0-8) . Once the pool is saturated, every subsequent legitimate connection attempt (staked or unstaked) hits the `ClientConnectionTracker::new` failure branch and is refused via `incoming.refuse()`, incrementing `refused_connections_too_many_open_connections` [3](#0-2) .

I was unable to confirm within the available index whether the server invokes quinn's stateless `Incoming::retry()` address-validation path anywhere in the codebase (no `retry()` calls were found in `streamer/src/nonblocking/quic.rs`); if retry validation is not explicitly enabled, source-IP spoofing for the Initial-only flood may be feasible, further lowering attacker cost, but this could not be fully verified from the indexed code.

### Impact Explanation
This is a non-RPC remote resource exhaustion / ingest-starvation issue: a low-cost, unprivileged network attacker can keep the TPU QUIC server's global connection-slot pool (`ClientConnectionTracker`) continuously saturated with half-open connections, causing `refused_connections_too_many_open_connections` to fire for all new connection attempts — including from staked/leader nodes — for the duration of the attack. This matches the REPLAY_LIVENESS invariant (ingest starvation) scoped impact described in the question: complete starvation of new TPU connections.

### Likelihood Explanation
The attacker needs only the ability to send raw UDP packets that complete a QUIC `Initial` exchange but withhold `Handshake` packets — no stake, no valid keypair beyond a throwaway TLS identity, no privileged access. The required sustained packet rate (~1000s of packets/sec) is trivial for a single modest machine, since the flood only needs to precede the point where either rate limiter would start consuming tokens (which never happens for incomplete handshakes). The attack is fully repeatable and can be sustained indefinitely by continuously issuing new Initial packets as prior slots expire every 2 seconds.

### Recommendation
- Consume rate-limiter tokens (both global and per-IP) at `Incoming` acceptance time / `ClientConnectionTracker` creation time, not after handshake success, so that an incomplete handshake still costs the attacker rate-limiter budget.
- Alternatively/additionally, require quinn's stateless retry (`Incoming::retry()`) before consuming a `ClientConnectionTracker` slot, so attackers must demonstrate a real, non-spoofed round trip before occupying a global slot.
- Reduce `QUIC_CONNECTION_HANDSHAKE_TIMEOUT` for slots that have not shown any handshake progress, or maintain a separate, tighter cap for "pending handshake" connections distinct from the cap for fully-established connections, so a handshake-flood cannot displace budget reserved for legitimate staked traffic.

### Proof of Concept
```rust
// streamer/src/nonblocking/quic.rs (add to existing test module)
// Sketch: open many raw UDP "Initial"-only QUIC handshakes against setup_quic_server
// without completing the handshake, then assert refused_connections_too_many_open_connections
// increases and a legitimate make_client_endpoint() connection is refused.

#[tokio::test(flavor = "multi_thread")]
async fn test_handshake_flood_starves_legitimate_connections() {
    let SpawnTestServerResult { server_address, stats, cancel, .. } = setup_quic_server(
        None,
        QuicStreamerConfig::default_for_tests(),
        SwQosConfig {
            max_staked_connections: 4,
            max_unstaked_connections: 4,
            ..SwQosConfig::default_for_tests()
        },
    );
    // max_concurrent_connections() == (4+4)*5/4 == 10

    // Spawn >10 client endpoints that initiate a connection but never
    // complete the handshake (e.g. drop the client Connecting future
    // immediately after sending the Initial packet, or use a client
    // config with an unreachable/blackholed crypto flow).
    let mut floods = Vec::new();
    for _ in 0..20 {
        let ep = /* build endpoint w/ client cert */;
        let connecting = ep.connect(server_address, "localhost").unwrap();
        floods.push((ep, connecting)); // never await connecting to completion
    }

    // Give the server time to accept all Initials into ClientConnectionTracker.
    tokio::time::sleep(Duration::from_millis(200)).await;

    // A legitimate client should now be refused.
    let legit = make_client_endpoint(&server_address, None).await;
    // Expect refusal / no successful handshake within a short window.
    assert!(stats.refused_connections_too_many_open_connections.load(Ordering::Relaxed) > 0);

    cancel.cancel();
}
```
Expected assertion: `stats.refused_connections_too_many_open_connections` increases while the flood connections are pending, and legitimate connection attempts fail or are delayed until the 2-second timeout expires on the flooding connections.

### Citations

**File:** streamer/src/nonblocking/quic.rs (L78-80)
```rust
/// Timeout for connection handshake. Timer starts once we get Initial from the
/// peer, and is canceled when we get a Handshake packet from them.
const QUIC_CONNECTION_HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(2);
```

**File:** streamer/src/nonblocking/quic.rs (L229-251)
```rust
impl Drop for ClientConnectionTracker {
    /// When this is dropped, reduce the open connection count.
    fn drop(&mut self) {
        self.stats.open_connections.fetch_sub(1, Ordering::Relaxed);
    }
}

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
```

**File:** streamer/src/nonblocking/quic.rs (L346-357)
```rust
            // check overall connection request rate limiter
            if overall_connection_rate_limiter.current_tokens() == 0 {
                stats
                    .connection_rate_limited_across_all
                    .fetch_add(1, Ordering::Relaxed);
                debug!(
                    "Ignoring incoming connection from {} due to overall rate limit.",
                    incoming.remote_address()
                );
                incoming.ignore();
                continue;
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

**File:** streamer/src/nonblocking/quic.rs (L471-472)
```rust
    let from = connecting.remote_address();
    let res = timeout(QUIC_CONNECTION_HANDSHAKE_TIMEOUT, connecting).await;
```

**File:** streamer/src/nonblocking/quic.rs (L480-508)
```rust
                // now that we have observed the handshake we can be certain
                // that the initiator owns an IP address, we can update rate
                // limiters on the server
                if !rate_limiter.register_connection(&from.ip()) {
                    debug!("Reject connection from {from:?} -- rate limiting exceeded");
                    stats
                        .connection_rate_limited_per_ipaddr
                        .fetch_add(1, Ordering::Relaxed);
                    new_connection.close(
                        CONNECTION_CLOSE_CODE_DISALLOWED.into(),
                        CONNECTION_CLOSE_REASON_DISALLOWED,
                    );
                    return;
                }

                if overall_connection_rate_limiter.consume_tokens(1).is_err() {
                    debug!(
                        "Reject connection from {:?} -- total rate limiting exceeded",
                        from.ip()
                    );
                    stats
                        .connection_rate_limited_across_all
                        .fetch_add(1, Ordering::Relaxed);
                    new_connection.close(
                        CONNECTION_CLOSE_CODE_DISALLOWED.into(),
                        CONNECTION_CLOSE_REASON_DISALLOWED,
                    );
                    return;
                }
```

**File:** streamer/src/nonblocking/connection_rate_limiter.rs (L34-40)
```rust
    pub fn is_allowed(&self, ip: &IpAddr) -> bool {
        // Check if we have records in the rate limiter for the given IP address
        match self.limiter.current_tokens(ip) {
            Some(r) => r > 0, // we have a record, and rate is not exceeded
            None => true,     // if we have not seen IP, allow connection request
        }
    }
```

**File:** streamer/src/quic.rs (L46-48)
```rust
pub const DEFAULT_MAX_STAKED_CONNECTIONS: usize = 2000;

pub const DEFAULT_MAX_UNSTAKED_CONNECTIONS: usize = 2000;
```

**File:** streamer/src/nonblocking/swqos.rs (L518-522)
```rust
    fn max_concurrent_connections(&self) -> usize {
        // Allow 25% more connections than required to allow for handshake

        (self.config.max_staked_connections + self.config.max_unstaked_connections) * 5 / 4
    }
```
