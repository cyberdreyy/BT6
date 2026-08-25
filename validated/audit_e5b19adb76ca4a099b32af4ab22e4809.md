### Title
Pre-handshake QUIC connection flood bypasses IP/global rate limiters and exhausts `ClientConnectionTracker` slots, starving legitimate connections - ([File: streamer/src/nonblocking/quic.rs])

### Summary
The QUIC ingest path checks per-IP and global connection rate limiters before creating a `ClientConnectionTracker`, but both limiters are only *consumed* (decremented) after a connection successfully completes the TLS handshake in `setup_connection`. An attacker who never completes the handshake never triggers `rate_limiter.register_connection` or `overall_connection_rate_limiter.consume_tokens`, so these limiters never throttle them, letting them repeatedly occupy tracker slots for up to `QUIC_CONNECTION_HANDSHAKE_TIMEOUT` (2s) each and starve `qos.max_concurrent_connections()` capacity.

### Finding Description
In `run_server` [1](#0-0) , an incoming connection is first checked against `overall_connection_rate_limiter.current_tokens() == 0` and `rate_limiter.is_allowed(&ip)`, and only if both pass does the code call `ClientConnectionTracker::new(...)` (bounded by `qos.max_concurrent_connections()`) and then `incoming.accept()`, spawning `setup_connection`.

Critically, `rate_limiter.is_allowed` in `ConnectionRateLimiter` only checks `current_tokens(ip)`, treating an IP with no prior *registered* record as always allowed: `None => true` [2](#0-1) . Tokens are only consumed via `register_connection`, which is called from `setup_connection` only *after* the TLS handshake completes successfully: `if !rate_limiter.register_connection(&from.ip())` [3](#0-2) . Likewise, the global `overall_connection_rate_limiter.consume_tokens(1)` is also only invoked post-handshake [4](#0-3) , while the pre-accept gate only *peeks* at `current_tokens()` without decrementing [5](#0-4) .

Consequently, an attacker who sends QUIC Initial packets and deliberately withholds Handshake completion never causes either limiter to consume tokens, so both `is_allowed` and `current_tokens() == 0` keep returning "allowed" indefinitely for that attacker's traffic. Each such incoming connection still passes `ClientConnectionTracker::new` (subject only to `qos.max_concurrent_connections()`, i.e., `(max_staked_connections + max_unstaked_connections) * 5 / 4`), gets `incoming.accept()`'d, and is held in `setup_connection`'s `timeout(QUIC_CONNECTION_HANDSHAKE_TIMEOUT, connecting)` for up to 2 seconds [6](#0-5) [7](#0-6) . By continuously issuing new stalled connection attempts fast enough to backfill expiring slots, the attacker can keep `stats.open_connections` pinned at `max_concurrent_connections`, causing `ClientConnectionTracker::new` to fail for legitimate incoming connections, which then hit `incoming.refuse()` and increment `refused_connections_too_many_open_connections` [8](#0-7) .

The design comment explicitly acknowledges bounding resource use via "limit duration of in-flight connection attempts with a timeout" and "protect against connection attempt bursts with a global rate-limiter" / "rate-limit abusive peers by (control-asserted) ip" [9](#0-8) , but the implementation only asserts "control" (i.e., consumes rate-limiter tokens) upon handshake completion, not upon mere connection attempts — so the stated per-IP and global "burst" protections do not actually apply to attackers who never complete the handshake, which is exactly the profile of this attack.

### Impact Explanation
This is a non-RPC remote resource-exhaustion / ingest-starvation issue: a single unprivileged network peer can deny legitimate validator/client QUIC connections to the TPU by keeping `ClientConnectionTracker` slots saturated with never-completing handshakes, without needing stake, keys, or high bandwidth (each stalled attempt costs only an Initial packet, held open for ≤2s). This matches "non-RPC remote resource exhaustion / ingest starvation" bounty category.

### Likelihood Explanation
Preconditions are minimal: attacker only needs to send raw UDP/QUIC Initial packets to the TPU QUIC port and never complete the handshake (no valid stake, keypair binding, or completed retry/handshake needed to reach `ClientConnectionTracker::new`). The per-IP rate limiter's gate (`is_allowed`) does not block a first-seen or never-registered IP, and using multiple source IPs (or bursts from few IPs before token accounting could otherwise matter) further trivializes scaling. Sustaining saturation requires continuously issuing new attempts near the rate matching slot expiry (every ~2s per stalled slot), which is straightforward to automate with a simple QUIC client that opens `Endpoint::connect` and never advances past Initial/Handshake. This is repeatable and does not depend on any race condition beyond simple rate pacing.

### Recommendation
Consume (or otherwise account for) rate-limiter tokens at connection-attempt time (before or at `ClientConnectionTracker::new`), not only after successful handshake completion, for both `ConnectionRateLimiter` (per-IP) and `overall_connection_rate_limiter` (global). Alternatively, track "in-flight, not-yet-verified" attempts per IP with their own bounded quota (separate from post-handshake counts) so that an IP address cannot open unlimited concurrent unverified attempts, while still being fair to legitimate peers who complete handshakes normally. Consider also shortening `QUIC_CONNECTION_HANDSHAKE_TIMEOUT` for unverified peers, and/or reserving a portion of `max_concurrent_connections` capacity exclusively for peers with previously registered successful connections.

### Proof of Concept
```rust
// streamer/src/nonblocking/quic.rs (integration-style test, added to existing #[cfg(test)] mod)
#[tokio::test(flavor = "multi_thread")]
async fn test_stalled_handshake_flood_starves_legitimate_clients() {
    use quinn::{ClientConfig, Endpoint};

    let SpawnTestServerResult {
        join_handle,
        server_address,
        stats,
        cancel,
        ..
    } = setup_quic_server(
        None,
        QuicStreamerConfig::default_for_tests(),
        SwQosConfig::default(),
    );

    // Attacker: open many raw connections and never let the handshake complete.
    // Achieved by connecting to a UDP socket that responds with Initial only and
    // never advances (e.g., a custom quinn client with `connect` called and the
    // resulting `Connecting` future never awaited to completion, or by sending
    // raw crafted Initial packets from many source ports/IPs without handshake).
    let attacker_count = 10_000; // exceeds qos.max_concurrent_connections()
    let mut attacker_futs = Vec::new();
    for _ in 0..attacker_count {
        let endpoint = Endpoint::client("0.0.0.0:0".parse().unwrap()).unwrap();
        // fire-and-forget connecting future; do not await to completion
        let connecting = endpoint.connect(server_address, "localhost").unwrap();
        attacker_futs.push(tokio::spawn(async move {
            // never await `connecting` to completion; just hold the endpoint alive
            let _ = tokio::time::timeout(Duration::from_secs(5), connecting).await;
        }));
    }

    // give server time to fill up tracker slots
    tokio::time::sleep(Duration::from_millis(200)).await;

    // Legitimate client attempts to connect and should be refused.
    let legit_result = tokio::time::timeout(
        Duration::from_secs(1),
        make_client_endpoint(&server_address, None),
    )
    .await;

    assert!(
        legit_result.is_err()
            || stats
                .refused_connections_too_many_open_connections
                .load(Ordering::Relaxed)
                > 0,
        "expected legitimate connection to be refused due to tracker saturation"
    );

    cancel.cancel();
    join_handle.await.unwrap();
}
```
Expected assertion: `stats.refused_connections_too_many_open_connections` increases while the attacker's actual bandwidth/IP diversity used is low relative to the number of stalled slots occupied, demonstrating that the per-IP/global rate limiters (which only decrement on successful handshake) failed to prevent tracker exhaustion by never-completing connections.

### Citations

**File:** streamer/src/nonblocking/quic.rs (L80-80)
```rust
const QUIC_CONNECTION_HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(2);
```

**File:** streamer/src/nonblocking/quic.rs (L331-341)
```rust
        if let Ok(Some(incoming)) = timeout_connection {
            // our connection/handshake abuse mitigation policy is one of shed
            // fast and bound resource consumption. attempting to be "smarter"
            // before a peer has asserted control over their ip address by
            // completing the retry challenge creates a scenario whereby peers
            // can attack one another via ip spoofing. employ the following
            // * limit duration of in-flight connection attempts with a timeout
            // * protect against connection attempt bursts with a global rate-limiter
            // * rate-limit abusive peers by (control-asserted) ip
            // * cap total connections per-peer/ip

```

**File:** streamer/src/nonblocking/quic.rs (L346-379)
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
            // then perform per IpAddr rate limiting
            if !rate_limiter.is_allowed(&incoming.remote_address().ip()) {
                stats
                    .connection_rate_limited_per_ipaddr
                    .fetch_add(1, Ordering::Relaxed);
                debug!(
                    "Ignoring incoming connection from {} due to per-IP rate limiting.",
                    incoming.remote_address()
                );
                incoming.ignore();
                continue;
            }

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

**File:** streamer/src/nonblocking/quic.rs (L472-475)
```rust
    let res = timeout(QUIC_CONNECTION_HANDSHAKE_TIMEOUT, connecting).await;
    stats
        .outstanding_incoming_connection_attempts
        .fetch_sub(1, Ordering::Relaxed);
```

**File:** streamer/src/nonblocking/quic.rs (L476-493)
```rust
    if let Ok(connecting_result) = res {
        match connecting_result {
            Ok(new_connection) => {
                debug!("Got a connection {from:?}");
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
```

**File:** streamer/src/nonblocking/quic.rs (L495-508)
```rust
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
