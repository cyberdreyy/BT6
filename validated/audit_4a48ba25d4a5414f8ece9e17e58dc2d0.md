### Title
Global connection-rate-limiter admits IP-keyed token buckets that a well-funded/distributed attacker can drain every window to starve legitimate transaction ingest on the TPU QUIC port - ([File: streamer/src/nonblocking/quic.rs])

### Summary
The QUIC ingest path in agave gates every new TPU/TPU-forward connection behind two rate limiters: a global, non-stake-weighted `TokenBucket` (`overall_connection_rate_limiter`) and a per-IP `ConnectionRateLimiter`. Both are simple fixed-capacity/fixed-refill-rate token buckets that reset on a wall-clock schedule, structurally identical to the Linea `RateLimiter` pattern described in the report: a protocol-wide/period-based cap that any actor who can generate enough "requests" (connections) can exhaust, denying the resource (TPU connection admission) to everyone else until the next refill period.

### Finding Description
`run_server` constructs a global `TokenBucket` sized by `MAX_CONNECTION_BURST` / `TOTAL_CONNECTIONS_PER_SECOND` and a per-IP `ConnectionRateLimiter` sized by `max_connections_per_ipaddr_per_min`: [1](#0-0) 

For every accepted incoming connection attempt, the code first checks the **global** bucket and, if it is empty, ignores the connection outright — before any per-peer/stake distinction is made: [2](#0-1) 

then applies the per-IP check: [3](#0-2) 

Both limiters are ordinary `TokenBucket`/`KeyedRateLimiter` instances (net-utils) with no notion of stake weight or reputation — they only track IP address and elapsed time: [4](#0-3) [5](#0-4) 

This is the same bug class as the Linea `RateLimiter`: a protocol-wide, period-reset limit whose capacity is a fixed public constant. In Linea, an attacker who could afford to move the rate-limit amount of ETH each period could permanently occupy the limiter and block legitimate bridge users. Here, an attacker who can generate `TOTAL_CONNECTIONS_PER_SECOND` worth of connection attempts per second (trivially cheap — a raw QUIC handshake attempt, no stake or fee required) from a rotating/distributed set of source IPs can keep `overall_connection_rate_limiter.current_tokens() == 0` continuously, since the check-then-consume happens before any handshake completes or client identity/stake is verified: [6](#0-5) 
Every subsequent legitimate connection (including from staked validators/RPC forwarders) is silently `incoming.ignore()`d at that point, regardless of the sender's stake, because the global bucket check precedes the per-IP and stake-based `SwQos`/`SimpleQos` admission logic entirely.

### Impact Explanation
If the global connection-rate limiter is kept permanently drained by an attacker, the validator's TPU/TPU-forward QUIC endpoint stops admitting *any* new connections — including from staked, high-priority senders — because the check in lines 346-357 is unconditional and happens ahead of the stake-aware `QosController` (`SwQos`/`SimpleQos`) logic that would normally prioritize staked traffic. This is a TPU ingest-starvation condition: ordinary users' and staked validators' transactions cannot even open a connection to be considered for QUIC stream throttling, cost-tracking, or inclusion, effectively denying transaction submission to the leader for the duration of the attack. This mirrors the Linea report's core harm ("users' funds/actions get stuck/blocked because the rate limiter is exhausted by an attacker"), translated to "legitimate transactions can't reach the leader because the connection admission limiter is exhausted by an attacker."

### Likelihood Explanation
Exploitation requires only the ability to generate cheap, unauthenticated QUIC connection attempts against the validator's public TPU port at a rate exceeding `TOTAL_CONNECTIONS_PER_SECOND` (with a burst allowance of `MAX_CONNECTION_BURST`). Since the global bucket is keyed by nothing (not even IP) and is checked before handshake completion or stake verification, an attacker does not need a completed handshake, does not need stake, and does not need to pay any protocol fee — only enough distinct source addresses/sockets (or connection attempts) to keep consuming the shared token supply. The per-IP limiter provides no protection against this because the global bucket is checked first and independently. This makes the attack likelihood moderate-to-high for any adversary willing to run a modest botnet or spoof multiple source addresses against a specific validator's TPU port, though real-world impact also depends on validator-side network mitigations (e.g. upstream firewalling) not modeled in this code path.

### Recommendation
- Make the global connection admission rate scale with, or otherwise exempt, staked/known-good traffic (i.e., perform at least a lightweight stake/identity check, or coalesce accounting per verified pubkey rather than pre-handshake per-IP/global) before applying the hard global cutoff at `streamer/src/nonblocking/quic.rs:346-357`.
- Increase `MAX_CONNECTION_BURST`/`TOTAL_CONNECTIONS_PER_SECOND` dynamically based on observed abuse patterns and cluster stake distribution rather than fixed constants that any external actor can budget against.
- Add monitoring/alerting on sustained `connection_rate_limited_across_all` stat spikes (already emitted at line 348-350) so operators can react to a global-bucket-exhaustion DoS in real time.
- Consider reserving a fraction of the global bucket exclusively for connections that present a valid client certificate mapped to a staked identity, so unstaked/anonymous flood cannot fully starve staked ingest.

### Proof of Concept
Not independently executed; based on static code reading. Conceptual PoC: run many concurrent QUIC connection attempts (`Endpoint::connect`) against a target validator's TPU/TPU-forward UDP port from a set of rotating source IPs/ports at a rate ≥ `TOTAL_CONNECTIONS_PER_SECOND` for a sustained period, observing (server-side) `connection_rate_limited_across_all` metrics climbing and `incoming.ignore()` being invoked for all connections, including ones from separately verified staked test clients attempted during the flood, confirming interruption of legitimate ingest at [2](#0-1) .

### Citations

**File:** streamer/src/nonblocking/quic.rs (L270-281)
```rust
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

**File:** streamer/src/nonblocking/quic.rs (L331-357)
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

            stats
                .total_incoming_connection_attempts
                .fetch_add(1, Ordering::Relaxed);

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

**File:** net-utils/src/token_bucket.rs (L19-60)
```rust
pub struct TokenBucket {
    new_tokens_per_us: f64,
    max_tokens: u64,
    /// bucket creation
    base_time: Instant,
    tokens: AtomicU64,
    /// time of last update in us since base_time
    last_update: AtomicU64,
    /// time unused in last token creation round
    credit_time_us: AtomicU64,
    /// Per-bucket time source for shuttle tests, replacing Instant::now().
    /// Shared via Arc so cloned buckets (e.g. in KeyedRateLimiter) use the same clock.
    #[cfg(feature = "shuttle-test")]
    pub time_us_override: Arc<AtomicU64>,
}

// If changing this impl, make sure to run benches and ensure they do not panic.
// much of the testing is impossible outside of real multithreading in release mode.
impl TokenBucket {
    /// Allocate a new TokenBucket
    pub fn new(initial_tokens: u64, max_tokens: u64, new_tokens_per_second: f64) -> Self {
        assert!(
            new_tokens_per_second > 0.0,
            "Token bucket can not have zero influx rate"
        );
        assert!(
            initial_tokens <= max_tokens,
            "Can not have more initial tokens than max tokens"
        );
        let base_time = Instant::now();
        TokenBucket {
            // recompute into us to avoid FP division on every update
            new_tokens_per_us: new_tokens_per_second / 1e6,
            max_tokens,
            tokens: AtomicU64::new(initial_tokens),
            last_update: AtomicU64::new(0),
            base_time,
            credit_time_us: AtomicU64::new(0),
            #[cfg(feature = "shuttle-test")]
            time_us_override: Arc::new(AtomicU64::new(0)),
        }
    }
```

**File:** streamer/src/nonblocking/connection_rate_limiter.rs (L6-29)
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
```
