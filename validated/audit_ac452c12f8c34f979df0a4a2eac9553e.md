### Title
Slow-trickle stream attack bypasses `wait_for_chunk_timeout` to indefinitely occupy bounded QUIC connection slots - ([File: streamer/src/nonblocking/quic.rs])

### Summary
`handle_connection`'s stream-read loop re-arms `wait_for_chunk_timeout` on every successful `read_chunks` call rather than enforcing any bound on total connection/stream lifetime. An attacker who completes the handshake and then sends a trickle of bytes (e.g., 1 byte) just before each timeout expires can keep the read loop alive indefinitely without ever completing a transaction payload, tying up a `ClientConnectionTracker` slot and the connection's `ConnectionTable` entry for as long as they want.

### Finding Description
In `handle_connection` [1](#0-0) , each iteration of the inner stream-reading loop calls:
```rust
chunk = tokio::time::timeout(wait_for_chunk_timeout, stream.read_chunks(&mut chunks))
```
This timeout is local to a single `read_chunks` call. As long as at least one chunk (even a single byte) arrives before `wait_for_chunk_timeout` elapses, the `Ok(Ok(chunk))` branch is taken and the loop continues waiting for the *next* chunk with a freshly-armed timeout. There is no cumulative deadline on the stream or on the outer `'conn: loop` in `handle_connection` [2](#0-1)  that bounds total connection/stream duration independent of per-chunk liveness.

The connection itself was already accounted for against the bounded `ClientConnectionTracker`/`qos.max_concurrent_connections()` budget at accept time [3](#0-2) , and that slot (plus the `ConnectionTable` entry created via `try_add_connection` [4](#0-3) ) is only released when the connection task exits — which for a trickling attacker never happens on the `wait_for_chunk_timeout` path, since that timeout is reset by any read, however small.

`QUIC_CONNECTION_HANDSHAKE_TIMEOUT` only bounds the initial handshake window [5](#0-4)  and has no relevance after the connection is established; it does not create any post-handshake cap on connection lifetime. No `max_idle_timeout`/overall connection-lifetime transport setting was found configured for the QUIC endpoint in this file, so the only per-connection liveness gate is the per-chunk `wait_for_chunk_timeout`, which is defeated by low-rate but non-zero traffic.

Existing mitigations (per-IP connection rate limiting, `max_connections_per_peer`, global connection rate limiter, `prune_random`/`stream_load_ema` throttling) all bound the *number* of connections/streams accepted or evict based on stake, but none of them detect or evict connections/streams that are alive-but-idle-ish (slow trickle) once accepted, since such connections continue to look "active" from the timeout's perspective.

### Impact Explanation
An unstaked/unprivileged attacker can occupy up to `qos.max_concurrent_connections()` connection slots by completing cheap handshakes and then trickling 1 byte per stream just under `wait_for_chunk_timeout`, forcing legitimate connections to be refused via `refused_connections_too_many_open_connections` [3](#0-2) . This matches the "non-RPC remote resource exhaustion / ingest starvation" bounty category — it degrades TPU ingest availability for legitimate transaction senders without requiring any privileged capability.

### Likelihood Explanation
Preconditions are default configuration (`wait_for_chunk_timeout`, `max_concurrent_connections`) and only require an attacker capable of opening QUIC connections to the TPU, which is explicitly within the unprivileged threat model. The attack is fully repeatable and requires no stake, no signed transaction, and no protocol-level exploit — only precise timing of small writes, well within reach of a simple client script. Per-IP and global connection-rate limits bound how fast new connections can be opened but do not prevent already-accepted connections from being held open indefinitely via trickling once inside the table.

### Recommendation
Bound total connection/stream lifetime independent of per-chunk liveness, e.g., track an overall deadline (or maximum total idle-adjusted duration) per stream/connection starting from stream/connection creation, and forcibly close/reset streams or connections exceeding it regardless of trickling activity. Additionally, consider requiring a minimum aggregate throughput (bytes over a longer window) rather than resetting the timeout on any nonzero read, or applying QUIC's `max_idle_timeout` at the transport-config level while still fixing an outer cap on total per-stream duration in `handle_connection`.

### Proof of Concept
```rust
// streamer/src/nonblocking/quic.rs (test module)
#[tokio::test(flavor = "multi_thread")]
async fn test_slow_trickle_occupies_connection_slot() {
    agave_logger::setup();
    let SpawnTestServerResult {
        join_handle,
        receiver,
        server_address,
        stats,
        cancel,
    } = setup_quic_server(
        None,
        QuicStreamerConfig::default_for_tests(), // default wait_for_chunk_timeout
        SwQosConfig { max_concurrent_connections: 1, ..Default::default() },
    );

    // Attacker: complete handshake, open a stream, trickle bytes just under
    // wait_for_chunk_timeout to keep the connection alive without finishing
    // a transaction.
    let attacker_conn = make_client_endpoint(&server_address, None).await;
    let mut send_stream = attacker_conn.open_uni().await.unwrap();
    for _ in 0..5 {
        send_stream.write_all(&[0u8; 1]).await.unwrap();
        tokio::time::sleep(QuicStreamerConfig::default_for_tests().wait_for_chunk_timeout
            - Duration::from_millis(50)).await;
    }

    // Legitimate client attempts to connect while attacker occupies the sole slot.
    let legit_conn = make_client_endpoint(&server_address, None).await;
    // Expect refusal since max_concurrent_connections == 1 and attacker slot
    // is still held.
    assert!(legit_conn.closed().await.is_err() ||
        stats.refused_connections_too_many_open_connections.load(Ordering::Relaxed) >= 1);

    cancel.cancel();
    drop(receiver);
    join_handle.await.unwrap();
}
```
Expected assertion: `stats.refused_connections_too_many_open_connections` increments while the attacker's connection (and its `ClientConnectionTracker` slot) remains alive throughout the trickle loop, confirming the slot was held without delivering a complete transaction payload.

### Citations

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

**File:** streamer/src/nonblocking/quic.rs (L472-472)
```rust
    let res = timeout(QUIC_CONNECTION_HANDSHAKE_TIMEOUT, connecting).await;
```

**File:** streamer/src/nonblocking/quic.rs (L610-622)
```rust
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

**File:** streamer/src/nonblocking/quic.rs (L647-677)
```rust
        loop {
            // Read the next chunks, waiting up to `wait_for_chunk_timeout`. If we don't get chunks
            // before then, we assume the stream is dead. This can only happen if there's severe
            // packet loss or the peer stops sending for whatever reason.
            let n_chunks = match tokio::select! {
                chunk = tokio::time::timeout(
                    wait_for_chunk_timeout,
                    stream.read_chunks(&mut chunks)) => chunk,

                // If the peer gets disconnected stop the task right away.
                _ = cancel.cancelled() => break,
            } {
                // read_chunk returned success
                Ok(Ok(chunk)) => chunk.unwrap_or(0),
                // read_chunk returned error
                Ok(Err(e)) => {
                    debug!("Received stream error: {e:?}");
                    stats
                        .total_stream_read_errors
                        .fetch_add(1, Ordering::Relaxed);
                    break;
                }
                // timeout elapsed
                Err(_) => {
                    debug!("Timeout in receiving on stream");
                    stats
                        .total_stream_read_timeouts
                        .fetch_add(1, Ordering::Relaxed);
                    break;
                }
            };
```

**File:** streamer/src/nonblocking/quic.rs (L1008-1041)
```rust
    pub(crate) fn try_add_connection<F: FnOnce() -> Arc<S>>(
        &mut self,
        key: ConnectionTableKey,
        port: u16,
        client_connection_tracker: ClientConnectionTracker,
        connection: Option<Connection>,
        peer_type: ConnectionPeerType,
        last_update: Arc<AtomicU64>,
        max_connections_per_peer: usize,
        stream_counter_factory: F,
    ) -> Option<(Arc<AtomicU64>, CancellationToken, Arc<S>)> {
        let connection_entry = self.table.entry(key).or_default();
        let has_connection_capacity = connection_entry
            .len()
            .checked_add(1)
            .map(|c| c <= max_connections_per_peer)
            .unwrap_or(false);
        if has_connection_capacity {
            let cancel = self.cancel.child_token();
            let stream_counter = connection_entry
                .first()
                .map(|entry| entry.stream_counter.clone())
                .unwrap_or_else(stream_counter_factory);
            connection_entry.push(ConnectionEntry::new(
                cancel.clone(),
                peer_type,
                last_update.clone(),
                port,
                client_connection_tracker,
                connection,
                stream_counter.clone(),
            ));
            self.total_size += 1;
            Some((last_update, cancel, stream_counter))
```
