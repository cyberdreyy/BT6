### Title
Unbounded TCP Connection Acceptance in RPC PubSub WebSocket Service Enables File-Descriptor Exhaustion - (File: rpc/src/rpc_pubsub_service.rs)

### Summary
The RPC PubSub (WebSocket) listener accepts and spawns a handler task for every incoming TCP connection with no cap on the total number of concurrent connections and no per-IP admission control, unlike the hardened QUIC TPU ingest path and the `ip_echo_server`, both of which explicitly track and bound concurrent connections. An unprivileged remote client can open unbounded TCP connections to the RPC PubSub port to exhaust the validator's file descriptors.

### Finding Description
`listen()` in `rpc/src/rpc_pubsub_service.rs` runs a loop that calls `listener.accept()` and, for every accepted socket, unconditionally spawns a `tokio::spawn` task running `handle_connection`, regardless of how many connections are already open or how many originate from the same IP address: [1](#0-0) 

The only bookkeeping object involved, `TokenCounter`, is created via `TokenCounter::new("rpc_pubsub_connections")` and a token is created per connection purely for metrics reporting — there is no check anywhere in this file that compares an open-connection count (or per-IP count) against a configured maximum before accepting/spawning: [2](#0-1) 

`handle_connection` itself performs a WebSocket handshake (`server.receive_request().await?`) before any subscription logic runs, and does not enforce a connection cap, idle timeout admission gate, or per-IP throttling: [3](#0-2) 

This is in stark contrast to other network-facing entry points in the same codebase that were explicitly hardened against exactly this bug class:
- The QUIC TPU streamer enforces `max_concurrent_connections`, a global connection-rate limiter, and a per-IP `ConnectionRateLimiter` before ever accepting a connection: [4](#0-3) 
- `net-utils/src/ip_echo_server.rs` explicitly tracks `active_ips` and rejects connections once `MAX_CONCURRENT_CONNECTIONS` is reached or if the same IP already has an active connection: [5](#0-4) 
- `banks-server/src/banks_server.rs` explicitly limits to `max_channels_per_key(1, ...)` and `buffer_unordered(10)` total channels: [6](#0-5) 

The RPC PubSub configuration only limits the number of *active subscriptions* (`rpc_pubsub_max_active_subscriptions`, `queue_capacity_items`, `queue_capacity_bytes`) — none of which bound raw TCP connection/socket counts prior to or independent of any subscription being created: [7](#0-6) 

Each accepted TCP socket consumes one file descriptor (plus the JoinHandle/task overhead) for the lifetime of the connection. A remote unprivileged attacker can complete TCP handshakes (and does not even need to complete the WebSocket upgrade for the FD to be held, since `TcpStream` is accepted and the task spawned before the handshake completes) and simply hold connections open without sending data, since there is no read/handshake timeout visible in `handle_connection`.

### Impact Explanation
This is directly analogous to the reported "File Descriptor Attack" bug class: an attacker can hold an arbitrarily large number of file descriptors open against the RPC PubSub port, exhausting the process's `RLIMIT_NOFILE` (or OS-wide FD limits). Once file descriptors are exhausted, the validator process can fail to open new sockets/files needed for consensus-critical services (accounts-db, ledger store, gossip, TPU QUIC endpoints), effectively causing a denial-of-service against the node. Because RPC PubSub is commonly exposed publicly (it is the standard websocket subscription endpoint), this can be triggered by any unprivileged network client without needing stake, a valid transaction, or a deployed program.

### Likelihood Explanation
High for any validator/RPC node that exposes the RPC PubSub port (`--rpc-pubsub-port`, enabled whenever RPC is enabled with pubsub). No authentication, stake, or protocol-level cost is required to open a raw TCP connection to this listener; the accept loop performs no IP-based or global-count gating before spawning a handler and consuming a file descriptor. This matches the original report's POC pattern (opening many connections and letting them idle/time out).

### Recommendation
Add admission control to `listen()` in `rpc/src/rpc_pubsub_service.rs` mirroring the pattern already used by `ip_echo_server.rs` and the QUIC streamer: track a global open-connection counter and a per-IP counter (e.g., via a shared `Arc<Mutex<..>>`/`DashMap`), reject/close new connections once a configurable maximum concurrent-connections (and per-IP) limit is reached, and add a bounded handshake/read timeout so that established-but-idle sockets holding no active subscription are reaped. Expose corresponding CLI flags analogous to `tpu_max_connections_per_ipaddr_per_minute`/`tpu_max_unstaked_connections`.

### Proof of Concept
1. Start a validator/RPC node with RPC PubSub enabled (default bind).
2. From an unprivileged remote host, run a loop opening TCP connections to the pubsub port without completing (or slow-completing) the WebSocket handshake, e.g.:
   ```bash
   for i in $(seq 1 100000); do
     (exec 3<>/dev/tcp/TARGET_IP/PUBSUB_PORT &) 
   done
   ```
3. Observe via `/proc/<pid>/fd` that the validator's open file descriptor count grows unbounded and approaches `RLIMIT_NOFILE`, since `listen()` in `rpc/src/rpc_pubsub_service.rs` spawns a handler for every accepted socket with no cap.
4. Once the limit is reached, other subsystems needing new file descriptors (new gossip/TPU connections, ledger/accounts-db file opens) begin failing, degrading or crashing the node.

### Citations

**File:** rpc/src/rpc_pubsub_service.rs (L349-365)
```rust
async fn handle_connection(
    socket: TcpStream,
    subscription_control: SubscriptionControl,
    config: PubSubConfig,
    mut tripwire: Tripwire,
) -> Result<(), Error> {
    let mut server = Server::new(socket.compat());
    let request = server.receive_request().await?;
    let accept = server::Response::Accept {
        key: request.key(),
        protocol: None,
    };
    server.send_response(&accept).await?;
    let mut builder = server.into_builder();
    builder.set_max_message_size(4_096);
    builder.set_max_frame_size(4_096);
    let (mut sender, mut receiver) = builder.finish();
```

**File:** rpc/src/rpc_pubsub_service.rs (L429-468)
```rust
async fn listen(
    listen_address: SocketAddr,
    config: PubSubConfig,
    subscription_control: SubscriptionControl,
    mut tripwire: Tripwire,
) -> io::Result<()> {
    let listener = match tokio::net::TcpListener::bind(&listen_address).await {
        Ok(listener) => {
            info!("rpc_pubsub listening on {listen_address:?}");
            listener
        }
        Err(e) => {
            error!(
                "failed to bind rpc_pubsub listener on {listen_address:?}: {e}. Hint: is the port \
                 already in use?"
            );
            return Err(e);
        }
    };
    let counter = TokenCounter::new("rpc_pubsub_connections");
    loop {
        select! {
            result = listener.accept() => match result {
                Ok((socket, addr)) => {
                    debug!("new client ({addr:?})");
                    let subscription_control = subscription_control.clone();
                    let config = config.clone();
                    let tripwire = tripwire.clone();
                    let counter_token = counter.create_token();
                    tokio::spawn(async move {
                        let handle = handle_connection(
                            socket, subscription_control, config, tripwire
                        );
                        match handle.await {
                            Ok(()) => debug!("connection closed ({addr:?})"),
                            Err(err) => warn!("connection handler error ({addr:?}): {err}"),
                        }
                        drop(counter_token); // Force moving token into the task.
                    });
                }
```

**File:** streamer/src/nonblocking/quic.rs (L342-342)
```rust
            stats
```

**File:** net-utils/src/ip_echo_server.rs (L189-212)
```rust
    loop {
        let connection = tcp_listener.accept().await;
        match connection {
            Ok((socket, peer_addr)) => {
                let tracked_ip = (!peer_addr.ip().is_loopback()).then_some(peer_addr.ip());
                if let Some(ip) = tracked_ip {
                    let mut active_ip_set = active_ips
                        .lock()
                        .expect("active_ips lock poisoned while admitting");
                    if active_ip_set.len() >= MAX_CONCURRENT_CONNECTIONS {
                        debug!(
                            "dropping connection from {peer_addr:?}: max concurrent connections \
                             ({MAX_CONCURRENT_CONNECTIONS}) reached",
                        );
                        continue;
                    }
                    if !active_ip_set.insert(ip) {
                        debug!(
                            "dropping connection from {peer_addr:?}: max concurrent connections \
                             per IP (1) reached"
                        );
                        continue;
                    }
                }
```

**File:** banks-server/src/banks_server.rs (L495-530)
```rust
        .map(server::BaseChannel::with_defaults)
        // Limit channels to 1 per IP.
        .max_channels_per_key(1, |t| {
            t.as_ref()
                .peer_addr()
                .map(|x| x.ip())
                .unwrap_or_else(|_| Ipv4Addr::UNSPECIFIED.into())
        })
        // serve is generated by the service attribute. It takes as input any type implementing
        // the generated Banks trait.
        .map(move |chan| {
            let (sender, receiver) = unbounded();

            let client = create_client(None, tpu_addr, exit.clone());

            SendTransactionService::new(
                bank_forks.clone(),
                receiver,
                client,
                Config {
                    retry_rate_ms: 5_000,
                    ..Config::default()
                },
                exit.clone(),
            );

            let server = BanksServer::new(
                bank_forks.clone(),
                block_commitment_cache.clone(),
                sender,
                Duration::from_millis(200),
            );
            chan.execute(server.serve())
        })
        // Max 10 channels.
        .buffer_unordered(10)
```

**File:** validator/src/commands/run/args/pub_sub_config.rs (L33-49)
```rust
#[cfg_attr(test, qualifiers(pub(crate)))]
static DEFAULT_RPC_PUBSUB_NUM_NOTIFICATION_THREADS: LazyLock<String> =
    LazyLock::new(|| get_thread_count().to_string());

pub(crate) fn args<'a, 'b>(test_validator: bool) -> Vec<Arg<'a, 'b>> {
    let rpc_pubsub_notification_threads = Arg::with_name("rpc_pubsub_notification_threads")
        .long("rpc-pubsub-notification-threads")
        .takes_value(true)
        .value_name("NUM_THREADS")
        .validator(is_parsable::<usize>)
        .help(
            "The maximum number of threads that RPC PubSub will use for generating notifications. \
             0 will disable RPC PubSub notifications",
        );
    let (
        rpc_pubsub_notification_threads,
        default_rpc_pubsub_queue_capacity_items,
```
