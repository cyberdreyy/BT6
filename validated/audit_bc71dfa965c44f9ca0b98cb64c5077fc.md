No max-message/frame-size configuration exists in the builder's `Listener` (`crates/builder/publish/src/listener.rs`), confirming that `accept_hdr_async` uses tokio-tungstenite defaults (64 MiB message / 16 MiB frame) with no connection cap, unlike the sibling `websocket-proxy` crate which enforces both.

### Title
Unbounded WebSocket Connections to the Flashblocks Publisher Enable CPU/Memory Exhaustion DoS - (File: crates/builder/publish/src/listener.rs)

### Summary
The `base-builder-publish` crate's `Listener::run` accepts every incoming TCP connection on the flashblocks WebSocket port and unconditionally spawns a new task and `BroadcastLoop` for it, with no limit on the number of concurrent connections, no per-IP throttling, and no inbound message/frame size cap. Every ~`flashblocks.block-time` milliseconds (default 250ms) the builder calls `WebSocketPublisher::publish`, which fans a new flashblock out to every currently subscribed connection. An attacker who can reach the flashblocks WebSocket endpoint can open an unbounded number of connections, forcing the builder to spawn unbounded tasks/sockets and to perform an unbounded number of concurrent sends on every publish tick, exhausting builder CPU/memory/file descriptors and starving legitimate block-building work — directly analogous to the Textream `DirectorServer` CVE-2026-28412 bug class (uncapped concurrent WebSocket connections + periodic broadcast-to-all timer).

### Finding Description
`WebSocketPublisher::new`/`with_capacity` binds a `TcpListener` and spawns a `Listener` (`crates/builder/publish/src/publisher.rs:77-90`). The `Listener::run` loop accepts connections and, for each one, spawns a task that performs the WebSocket handshake and then drives a `BroadcastLoop` for the lifetime of the connection, with no cap on the number of simultaneous connections: [1](#0-0) 

Each accepted connection independently `resubscribe()`s to the shared broadcast channel and reads from the shared ring buffer, then the spawned task drives `BroadcastLoop::run`, which is retained until the client disconnects, errors, or lags: [2](#0-1) 

There is no `RateLimit`/semaphore analog to the one implemented in the sibling `websocket-proxy` crate (`InMemoryRateLimit`, which enforces a global semaphore and per-IP limits before allowing an upgrade): [3](#0-2) [4](#0-3) 

The `websocket-proxy` handler additionally caps inbound frame/message sizes to 4 KiB to prevent large-allocation abuse: [5](#0-4) 

None of these protections exist in `crates/builder/publish/src/listener.rs`, whose `accept_hdr_async` call passes no `WebSocketConfig`, meaning tokio-tungstenite's defaults (up to 64 MiB messages / 16 MiB frames) apply per connection, per client, with no limit on total connections.

Every flashblock publication broadcasts to all currently-subscribed receivers via `self.pipe.send(...)` on a fixed periodic cadence driven by `flashblocks_block_time` (default 250 ms, `crates/builder/cli/src/args.rs:36`), and the builder actually invokes `publish` on this cadence: [6](#0-5) [7](#0-6) 

This is the same bug class as the reported Textream CVE: an uncapped set of WebSocket connections combined with a timer that broadcasts to *all* connected clients, letting a client-count-driven flood degrade the server hosting the broadcast.

### Impact Explanation
The flashblocks publisher runs inside the block-builder process itself (`FlashblocksServiceBuilder::spawn_payload_builder_service`, `crates/builder/core/src/flashblocks/service.rs:68-76`), on the same node responsible for producing blocks. If an attacker can reach the configured `flashblocks.addr:flashblocks.port` endpoint (whose default bind is loopback but which operators may need to expose to peer RPC/replica nodes), they can open a very large number of concurrent WebSocket connections. Because there is no cap:
- Each connection spawns a task and a `BroadcastLoop`, growing memory and OS thread/task scheduling overhead unbounded.
- Every flashblock publish (every ~250 ms) now must push a copy of the payload out to every one of the attacker's sockets, multiplying send-syscall and buffering overhead by the connection count on a tight interval.
- This can starve CPU/memory on the builder host, delaying or halting the builder's payload-building loop — a resource exhaustion / potential node halt condition, matching the CVSS 3.1 AV:N/AC:L/PR:N/UI:R/S:U/C:N/I:N/A:H profile of the referenced advisory (availability impact only).

### Likelihood Explanation
Exploitation requires only opening many WebSocket connections to the flashblocks endpoint — no authentication, special privileges, or valid transaction is needed once the port is reachable. The publisher performs periodic, unconditional broadcast fanout regardless of connection count, so the attack scales linearly with trivial client-side effort (opening sockets). The main mitigating factor is that the default bind address is `127.0.0.1`, limiting exposure to configurations where the endpoint is intentionally exposed beyond localhost (e.g., to remote RPC replicas), which is a realistic and documented deployment pattern for flashblocks distribution.

### Recommendation
Add the same protections already present in `crates/infra/websocket-proxy` to `crates/builder/publish`:
- Enforce a global connection semaphore and per-IP connection limits before completing the WebSocket upgrade in `Listener::run` (mirroring `InMemoryRateLimit`/`RateLimit::try_acquire`).
- Configure `WebSocketConfig` (max message size, max frame size) on `accept_hdr_async` to bound per-connection memory usage.
- Consider capping the number of concurrent broadcast subscribers and/or moving the fanout onto a bounded worker pool so a burst of connections cannot directly scale builder-loop latency.

### Proof of Concept
1. Configure/deploy a Base builder node with the flashblocks WebSocket port reachable from the attacker's network position (e.g., `--flashblocks.addr 0.0.0.0`, or from any position with loopback/internal access if using the default).
2. From the attacker host, open a very large number of concurrent WebSocket connections to `ws://<flashblocks_addr>:<flashblocks_port>/` using a simple client loop (no authentication is required per `crates/builder/publish/src/listener.rs`).
3. Observe that `WebSocketPublisher::publish` (invoked every `flashblocks.block-time` ms from `BasePayloadBuilder`) must fan out to all N attacker connections; as N grows, CPU/memory usage and task-scheduling latency on the builder process increase without bound, since `Listener::run` (lines 56-72) never rejects new connections.
4. Measure builder block-production latency/CPU under load to confirm degradation, analogous to the Textream `DirectorServer` freeze/crash behavior described in CVE-2026-28412.

### Citations

**File:** crates/builder/publish/src/listener.rs (L56-72)
```rust
        loop {
            tokio::select! {
                _ = cancel.cancelled() => {
                    return;
                }

                result = listener.accept() => {
                    let Ok((connection, peer_addr)) = result else {
                        continue;
                    };

                    let cancel = cancel.clone();
                    let receiver = receiver.resubscribe();
                    let ring_buffer = Arc::clone(&ring_buffer);
                    let metrics = Arc::clone(&metrics);

                    tokio::spawn(async move {
```

**File:** crates/builder/publish/src/listener.rs (L94-123)
```rust
                        // Resubscribe after handshake so the receiver starts
                        // from the broadcast tail now, not from when the TCP
                        // connection was accepted. The ring buffer snapshot
                        // covers everything before this point.
                        let receiver = receiver.resubscribe();

                        let resume_from = pos_rx.await.ok().flatten();
                        if let Some(ref pos) = resume_from {
                            debug!(
                                peer_addr = %peer_addr,
                                block_number = pos.block_number,
                                flashblock_index = pos.flashblock_index,
                                "Client requesting replay"
                            );
                        }

                        metrics.on_connection_opened();
                        let connected_at = std::time::Instant::now();
                        debug!(peer_addr = %peer_addr, "WebSocket connection established");

                        BroadcastLoop::new(
                            stream,
                            Arc::clone(&metrics),
                            cancel,
                            receiver,
                            Arc::clone(&ring_buffer),
                            resume_from,
                        )
                        .run()
                        .await;
```

**File:** crates/infra/websocket-proxy/src/rate_limit.rs (L78-118)
```rust
impl InMemoryRateLimit {
    /// Create a new in-memory rate limiter with the given limits.
    pub fn new(instance_limit: usize, per_ip_limit: usize) -> Self {
        Self {
            per_ip_limit,
            inner: Mutex::new(Inner {
                active_connections: HashMap::new(),
                semaphore: Arc::new(Semaphore::new(instance_limit)),
            }),
        }
    }
}

impl RateLimit for InMemoryRateLimit {
    fn try_acquire(self: Arc<Self>, addr: IpAddr) -> Result<Ticket, RateLimitError> {
        let mut inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());

        let permit = Arc::clone(&inner.semaphore).try_acquire_owned().map_err(|_| {
            RateLimitError::Limit {
                reason: "Global limit".to_owned(),
                limit_type: RateLimitType::Global,
            }
        })?;

        let current_count = inner.active_connections.get(&addr).copied().unwrap_or(0);

        if current_count + 1 > self.per_ip_limit {
            debug!(message = "Rate limit exceeded, trying to acquire", client = addr.to_string());
            return Err(RateLimitError::Limit {
                reason: String::from("IP limit exceeded"),
                limit_type: RateLimitType::PerIp,
            });
        }

        let new_count = current_count + 1;

        inner.active_connections.insert(addr, new_count);

        let rate_limiter: Arc<dyn RateLimit> = Arc::clone(&self) as Arc<dyn RateLimit>;
        Ok(Ticket { addr, _permit: permit, rate_limiter })
    }
```

**File:** crates/infra/websocket-proxy/src/server.rs (L223-255)
```rust
fn websocket_handler(
    state: ServerState,
    ws: WebSocketUpgrade,
    addr: SocketAddr,
    headers: HeaderMap,
    filter: FilterType,
) -> Response {
    let connect_addr = addr.ip();
    let client_addr = state.trusted_proxy_config.client_ip(connect_addr, &headers);

    let ticket = match state.rate_limiter.try_acquire(client_addr) {
        Ok(ticket) => ticket,
        Err(RateLimitError::Limit { reason, limit_type }) => {
            match limit_type {
                RateLimitType::PerIp => {
                    info!(
                        message = "per-IP rate limit exceeded",
                        client_ip = client_addr.to_string(),
                        reason = reason
                    );
                    Metrics::per_ip_rate_limited_requests().increment(1);
                }
                RateLimitType::Global => {
                    Metrics::global_rate_limited_requests().increment(1);
                }
            }

            return Response::builder()
                .status(StatusCode::TOO_MANY_REQUESTS)
                .body(Body::from(json!({"message": reason}).to_string()))
                .unwrap();
        }
    };
```

**File:** crates/infra/websocket-proxy/src/server.rs (L257-266)
```rust
    // Downstream clients are primarily receive-only (flashblock broadcast). Limit inbound
    // frame and message sizes to prevent clients from forcing large allocations
    // that the proxy never uses. Only small control frames (ping, pong, close) are
    // expected from clients.
    const MAX_CLIENT_MESSAGE_SIZE: usize = 4 * 1024;
    const MAX_CLIENT_FRAME_SIZE: usize = 4 * 1024;

    ws.max_message_size(MAX_CLIENT_MESSAGE_SIZE)
        .max_frame_size(MAX_CLIENT_FRAME_SIZE)
        .on_failed_upgrade(move |e: Error| {
```

**File:** crates/builder/publish/src/publisher.rs (L102-134)
```rust
    pub fn publish(
        &self,
        payload: &impl Serialize,
        block_number: u64,
        flashblock_index: u64,
    ) -> Result<usize, serde_json::Error> {
        let publish_span = span!(
            Level::INFO,
            "publish_flashblock",
            block_number,
            flashblock_index,
            byte_size = tracing::field::Empty,
        );
        let _publish_span_guard = publish_span.enter();

        let json = serde_json::to_string(payload)?;
        let size = json.len();
        publish_span.record("byte_size", size);
        let utf8_bytes = Utf8Bytes::from(json);
        let position = FlashblockPosition { block_number, flashblock_index };

        {
            let mut buf = self.ring_buffer.write();
            buf.push(position, utf8_bytes.clone());
        }

        // Ignore SendError — there may be zero receivers when no clients
        // are connected. The entry is already stored in the ring buffer
        // for replay on future connections.
        let _ = self.pipe.send((position, utf8_bytes));
        self.metrics.on_payload_size(size);
        Ok(size)
    }
```

**File:** crates/builder/core/src/flashblocks/payload.rs (L750-756)
```rust
                        let size = self
                            .outputs
                            .ws_pub
                            .publish(&fb_payload, ctx.block_number(), flashblock_index)
                            .wrap_err("failed to publish flashblock via websocket")?;
                        self.record_emitted_flashblock(ctx.block_number(), flashblock_index);
                        (false, size)
```
