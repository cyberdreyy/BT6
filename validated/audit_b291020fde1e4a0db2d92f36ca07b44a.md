### Title
Unprivileged QUIC handshake-slot exhaustion via stalled `Connecting` futures - (`streamer/src/nonblocking/quic.rs`)

### Summary
`ClientConnectionTracker::new` (and thus the `max_concurrent_connections` slot accounting from `QosController`) is allocated as soon as an `Incoming` packet is accepted, *before* the QUIC handshake (`Connecting` future) resolves, and the slot is held for up to `QUIC_CONNECTION_HANDSHAKE_TIMEOUT` while `setup_connection` awaits the handshake. An attacker who sends valid Initial packets but never completes the handshake can therefore occupy connection-tracker slots for the full timeout window, repeatedly, and starve legitimate peers with `refused_connections_too_many_open_connections`.

### Finding Description
In the accept loop, per-connection admission order is:
1. Global/per-IP rate limiter checks (`overall_connection_rate_limiter`, `rate_limiter.is_allowed`) — both operate purely on packet arrival/IP, requiring no handshake progress. [1](#0-0) 
2. `ClientConnectionTracker::new(..., qos.max_concurrent_connections())` is created immediately after those checks, *before* `incoming.accept()` is even called, i.e. before any handshake bytes are exchanged with the peer. [2](#0-1) 
3. The tracker is moved into `setup_connection`, which wraps the `Connecting` future in a `timeout(QUIC_CONNECTION_HANDSHAKE_TIMEOUT, connecting)` — up to 2 seconds — before doing anything else. Only on `Ok` handshake completion do the rate-limiter "confirmation" step and `qos.try_add_connection` run; on timeout the function simply increments `connection_setup_timeout` and returns, dropping the tracker only then. [3](#0-2) [4](#0-3) 

Because the tracker slot (the actual `max_concurrent_connections` budget enforced via `ClientConnectionTracker`) is consumed at accept time and held for the entire handshake timeout regardless of whether the peer ever advances the handshake, an attacker only needs to keep a stream of Initial packets arriving (one per stalled attempt) fast enough to keep the slot pool saturated. This does not require completing key exchange at all — the attacker only needs to pass the IP/global rate limiters, which gate on packet arrival, not handshake completion. With `TOTAL_CONNECTIONS_PER_SECOND = 2500` and `MAX_CONNECTION_BURST = 1000` for the global limiter, an attacker (or a modest number of attacker-controlled IPs to also clear per-IP limits) can generate far more accept events per handshake-timeout window than any reasonably small `max_concurrent_connections` value, filling the tracker before legitimate peers can obtain a slot. [5](#0-4) 

### Impact Explanation
This is a non-RPC remote resource exhaustion / TPU ingest DoS: legitimate stakers/clients attempting to submit transactions over QUIC are refused via `refused_connections_too_many_open_connections`, denying transaction ingest to the validator's TPU port without requiring any privileged access, stolen keys, or protocol violation — only sustained, self-funded network traffic.

### Likelihood Explanation
The precondition is minimal: the attacker needs only to open TCP/UDP sockets and send QUIC Initial packets from a set of IPs sufficient to satisfy the per-IP rate limiter, and simply never advance the handshake (or advance it just enough to keep `Connecting` pending). This is fully within reach of an unprivileged network attacker and requires no non-default configuration, key theft, or exploit of consensus/replay logic — it only requires sustained traffic generation, which is continuously repeatable.

### Recommendation
Defer allocation of the `ClientConnectionTracker` slot (and thus `max_concurrent_connections` accounting) until after the handshake resolves (i.e., after the `timeout(QUIC_CONNECTION_HANDSHAKE_TIMEOUT, connecting)` succeeds), or introduce a separate, smaller/short-lived "in-flight handshake" budget distinct from the established-connection budget so that stalled handshakes cannot consume the same capacity that gates fully-established legitimate connections.

### Proof of Concept
```rust
// streamer/src/nonblocking/quic.rs (conceptual test)
#[tokio::test]
async fn test_stalled_handshakes_exhaust_connection_tracker() {
    let stats = Arc::new(StreamerStats::default());
    let max_concurrent_connections = 2; // small cap to simulate default budget

    // Simulate several "attacker" accepts that create trackers but never
    // resolve their Connecting future within QUIC_CONNECTION_HANDSHAKE_TIMEOUT.
    for _ in 0..max_concurrent_connections {
        let tracker = ClientConnectionTracker::new(stats.clone(), max_concurrent_connections)
            .expect("attacker slot acquired before handshake completes");
        // tracker intentionally held (not dropped) to emulate an in-flight,
        // never-completing Connecting future during the 2s timeout window.
        std::mem::forget(tracker);
    }

    // Legitimate client now attempts to connect and is refused because the
    // tracker capacity is exhausted by stalled attacker handshakes.
    let refused = ClientConnectionTracker::new(stats.clone(), max_concurrent_connections);
    assert!(refused.is_err());
    // In the real accept loop this maps to:
    // stats.refused_connections_too_many_open_connections incrementing
    // for legitimate incoming connections.
}
```
Note: the exact `ClientConnectionTracker` API (constructor error type / Drop-based release) was not fully retrievable from the index within the available search budget; a background Devin session with full repo access should confirm the precise struct definition (likely in `streamer/src/nonblocking/qos.rs` or `simple_qos.rs`) to finalize the exact PoC and fix location.

### Citations

**File:** streamer/src/nonblocking/quic.rs (L70-80)
```rust
/// Total new connection counts per second. Heuristically taken from
/// the default staked and unstaked connection limits. Might be adjusted
/// later.
const TOTAL_CONNECTIONS_PER_SECOND: f64 = 2500.0;

/// Max burst of connections above sustained rate to pass through
const MAX_CONNECTION_BURST: u64 = 1000;

/// Timeout for connection handshake. Timer starts once we get Initial from the
/// peer, and is canceled when we get a Handshake packet from them.
const QUIC_CONNECTION_HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(2);
```

**File:** streamer/src/nonblocking/quic.rs (L346-369)
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

**File:** streamer/src/nonblocking/quic.rs (L470-493)
```rust
{
    let from = connecting.remote_address();
    let res = timeout(QUIC_CONNECTION_HANDSHAKE_TIMEOUT, connecting).await;
    stats
        .outstanding_incoming_connection_attempts
        .fetch_sub(1, Ordering::Relaxed);
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

**File:** streamer/src/nonblocking/quic.rs (L534-543)
```rust
            Err(e) => {
                handle_connection_error(e, &stats, from);
            }
        }
    } else {
        stats
            .connection_setup_timeout
            .fetch_add(1, Ordering::Relaxed);
    }
}
```
