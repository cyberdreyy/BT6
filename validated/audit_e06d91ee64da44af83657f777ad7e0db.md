### Title
Per-IP QUIC connection rate limiting in the TPU ingest path throttles all clients sharing an IP once the aggregate quota is exhausted, denying legitimate distinct senders behind NAT/proxies - (File: streamer/src/nonblocking/quic.rs, streamer/src/nonblocking/connection_rate_limiter.rs)

### Summary
The C4 finding shows that `Mailbox.requestL2Transaction()` tracked a per-depositor limit keyed by `msg.sender` (the bridge contract), so once the *aggregate* usage of that shared identifier reached the cap, every real depositor behind it was denied service even though their own individual usage never reached the limit. The same "limit enforced on the wrong/shared identifier" pattern exists in Agave's QUIC ingest path: `ConnectionRateLimiter` in `streamer/src/nonblocking/connection_rate_limiter.rs` enforces `max_connections_per_ipaddr_per_minute` keyed solely by source IP address, and `run_server`/`setup_connection` in `streamer/src/nonblocking/quic.rs` reject *all* new connections from that IP once the shared token bucket is drained - regardless of which distinct client (validator identity, RPC user, or bot) is actually behind that address.

### Finding Description
`run_server` constructs one `ConnectionRateLimiter` keyed by `IpAddr` for the whole QUIC endpoint: [1](#0-0) 

For every incoming connection, the code checks the limiter *before* completing the handshake, using only the IP: [2](#0-1) 

The underlying primitive, `ConnectionRateLimiter::is_allowed`/`register_connection`, is a `KeyedRateLimiter<IpAddr>` — a single token bucket per IP address, shared by every distinct process/client behind that address: [3](#0-2) 

The comments in `quic.rs` acknowledge that many logically distinct entities can share one IP ("NAT", "geo-distributed forwarders"), yet the enforced limit (`DEFAULT_MAX_CONNECTIONS_PER_IPADDR_PER_MINUTE`) is still an aggregate counted against that single shared IP key, not against each distinct client's own consumption: [4](#0-3) 

This mirrors the C4 M-15 root cause precisely: a resource limit intended to bound abuse by an individual actor is instead enforced against an aggregator/shared identifier (there, `L1WethBridge`'s address; here, the shared source IP), so once the aggregate is exhausted, unrelated well-behaved actors sharing that identifier are denied even though none of them individually exceeded any reasonable per-actor threshold.

### Impact Explanation
When many independent, legitimate transaction senders operate behind a shared IP (corporate NAT, cloud NAT gateway, VPN, load balancer, or a popular RPC/forwarder), a burst of connections from a handful of these senders exhausts the shared token bucket for that IP. All subsequent legitimate senders behind that same IP are then rejected at the QUIC handshake stage (`incoming.ignore()`/`new_connection.close(...)`) for the remainder of the throttling window, even though their own individual transaction submission rate is well within any reasonable per-user bound. This is an ingest-starvation condition: valid transactions from unrelated users cannot reach the TPU, purely because of how another, unrelated tenant of the same IP consumed the shared quota.

### Likelihood Explanation
This requires no privileged access, key leakage, or malicious node behavior — it triggers under normal, expected network topologies (NAT/proxy/shared egress IP) that Agave's own code comments already anticipate. Any moderately popular shared IP (e.g., a large corporate network, a cloud provider NAT range, or a public RPC forwarder) reaching `max_connections_per_ipaddr_per_minute` will incidentally starve all co-tenants of that IP, making this a naturally occurring rather than adversarially-engineered condition, though an adversary could also deliberately pre-consume the bucket for a target IP to deny service to other users sharing it.

### Recommendation
Where possible, layer the shared-IP rate limit with a secondary limit keyed by the client's asserted/staked identity (as `ConnectionTableKey::Pubkey` already does for connection admission in `try_add_connection`) rather than relying solely on IP-based throttling for the initial connection-rate gate, so that one heavy or malicious tenant behind a shared IP cannot exhaust the quota for unrelated legitimate tenants sharing that address. Consider making the per-IP rate limit adaptive/generous enough (or exempting verified stake-weighted identities from the IP-level pre-handshake gate) to avoid collateral denial of unrelated senders.

### Proof of Concept
1. Configure a validator with default `tpu_max_connections_per_ipaddr_per_minute` (8 per minute, burst 80) as set in `streamer/src/nonblocking/quic.rs` (`DEFAULT_MAX_CONNECTIONS_PER_IPADDR_PER_MINUTE`).
2. Simulate multiple independent clients behind one NAT/shared IP: have client A open/close connections repeatedly to consume the burst allotment for that IP (e.g., 80 connect attempts within the window), as the `register_connection`/`consume_tokens` logic in `connection_rate_limiter.rs` allows.
3. Have an unrelated, legitimate client B (sharing the same public IP) attempt to open a QUIC connection to submit a transaction.
4. Observe in `streamer/src/nonblocking/quic.rs::run_server` that `rate_limiter.is_allowed(&incoming.remote_address().ip())` returns `false`, causing `incoming.ignore()` and incrementing `connection_rate_limited_per_ipaddr` — client B's legitimate, individually-well-behaved transaction submission is denied purely due to client A's aggregate usage of the shared IP key.

### Citations

**File:** streamer/src/nonblocking/quic.rs (L40-56)
```rust
        // This is done so that sync code can also access the stake table.
        // Make sure we don't hold a sync lock across an await - including the await to
        // lock an async Mutex. This does not happen now and should not happen as long as we
        // don't hold an async Mutex and sync RwLock at the same time (currently true)
        // but if we do, the scope of the RwLock must always be a subset of the async Mutex
        // (i.e. lock order is always async Mutex -> RwLock). Also, be careful not to
        // introduce any other awaits while holding the RwLock.
        select,
        task::JoinHandle,
        time::timeout,
    },
    tokio_util::{sync::CancellationToken, task::TaskTracker},
};

pub const DEFAULT_WAIT_FOR_CHUNK_TIMEOUT: Duration = Duration::from_secs(2);

pub const ALPN_TPU_PROTOCOL_ID: &[u8] = b"solana-tpu";
```

**File:** streamer/src/nonblocking/quic.rs (L268-281)
```rust
    let quic_server_params = Arc::new(quic_server_params);
    let num_shards = (quic_server_params.num_threads.get() * 2).next_power_of_two();
    let rate_limiter = Arc::new(ConnectionRateLimiter::new(
        quic_server_params.max_connections_per_ipaddr_per_min,
        // allow for 10x burst to make sure we can accommodate legitimate
        // bursts from container environments running multiple pods on same IP
        quic_server_params.max_connections_per_ipaddr_per_min * 10,
        num_shards,
    ));
    let overall_connection_rate_limiter = Arc::new(TokenBucket::new(
        MAX_CONNECTION_BURST,
        MAX_CONNECTION_BURST,
        TOTAL_CONNECTIONS_PER_SECOND,
    ));
```

**File:** streamer/src/nonblocking/quic.rs (L358-369)
```rust
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

**File:** streamer/src/nonblocking/connection_rate_limiter.rs (L6-50)
```rust
/// Limits the rate of connections per IP address.
pub struct ConnectionRateLimiter {
    limiter: KeyedRateLimiter<IpAddr>,
}

/// The threshold of the size of the connection rate limiter map. When
/// the map size is above this, we will trigger a cleanup of older
/// entries used by past requests.
const CONNECTION_RATE_LIMITER_CLEANUP_SIZE_THRESHOLD: usize = 100_000;

impl ConnectionRateLimiter {
    /// Create a new rate limiter per IpAddr. The rate is specified as the count per minute to allow for
    /// less frequent connections. Higher limit also allows higher bursts.
    /// num_shards controls how many shards are used in the underlying dashmap,
    /// should be set >= number of contending threads.
    pub fn new(limit_per_minute: u64, max_burst: u64, num_shards: usize) -> Self {
        Self {
            limiter: KeyedRateLimiter::new(
                CONNECTION_RATE_LIMITER_CLEANUP_SIZE_THRESHOLD,
                TokenBucket::new(limit_per_minute, max_burst, limit_per_minute as f64 / 60.0),
                num_shards,
            ),
        }
    }

    /// Check if the connection from the said `ip` is allowed.
    /// Here we assume that only IPs with actual confirmed connections are stored in it,
    /// since we should only modify server state once source IP is verified
    pub fn is_allowed(&self, ip: &IpAddr) -> bool {
        // Check if we have records in the rate limiter for the given IP address
        match self.limiter.current_tokens(ip) {
            Some(r) => r > 0, // we have a record, and rate is not exceeded
            None => true,     // if we have not seen IP, allow connection request
        }
    }

    pub fn register_connection(&self, ip: &IpAddr) -> bool {
        if self.limiter.consume_tokens(*ip, 1).is_ok() {
            debug!("Request from IP {ip:?} allowed");
            true // Request allowed
        } else {
            debug!("Request from IP {ip:?} blocked");
            false // Request blocked
        }
    }
```
