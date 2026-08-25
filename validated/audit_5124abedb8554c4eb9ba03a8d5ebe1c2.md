### Title
Global QUIC connection-rate limiter can be exhausted by an unprivileged attacker rotating across a single IPv6 /64 allocation, starving legitimate TPU ingest - (File: streamer/src/nonblocking/quic.rs, streamer/src/nonblocking/connection_rate_limiter.rs)

### Summary
`run_server` gates all incoming QUIC connections through a single, fixed-size global `TokenBucket` (`TOTAL_CONNECTIONS_PER_SECOND = 2500`, `MAX_CONNECTION_BURST = 1000`) that is shared by every client regardless of stake, while the only other defense, `ConnectionRateLimiter`, throttles connections strictly per individual `IpAddr`. Because IPv6 gives any unprivileged attacker trivial access to billions of distinct source addresses from a single `/64` allocation, the attacker can stay under the per-IP cap on each address while collectively driving aggregate connection attempts far past the fixed global budget, starving the shared token bucket that legitimate staked/unstaked clients also depend on.

### Finding Description
`ConnectionRateLimiter::is_allowed`/`register_connection` key exclusively on `IpAddr` [1](#0-0) , with no subnet-level (e.g., `/64` or `/56`) aggregation for IPv6. Meanwhile `run_server` maintains one global `overall_connection_rate_limiter` sized by fixed constants that the code comment itself acknowledges were only "heuristically taken from the default staked and unstaked connection limits" [2](#0-1) . Because this bucket is a single shared resource gating connection admission before any staked/unstaked classification downstream, an attacker distributing handshake attempts across many distinct IPv6 addresses (trivially obtained from one `/64`) defeats the per-IP throttle entirely — each address individually never breaches `max_connections_per_ipaddr_per_min` — while in aggregate the attacker can sustain a connection rate that saturates or exceeds `TOTAL_CONNECTIONS_PER_SECOND`/`MAX_CONNECTION_BURST`. This consumes the shared global budget that legitimate clients (including staked ones) compete for, causing their connection attempts to be rejected via `CONNECTION_CLOSE_CODE_TOO_MANY`/`CONNECTION_CLOSE_REASON_TOO_MANY` even though those legitimate clients played entirely within the rules [3](#0-2) .

### Impact Explanation
This is a non-RPC remote resource exhaustion / TPU ingest starvation issue: an unprivileged attacker with no stake requirement can degrade the validator's ability to accept legitimate transaction-carrying QUIC connections by exhausting the fixed global connection-admission budget, using only cheap, freely obtainable IPv6 address space rather than any privileged capability.

### Likelihood Explanation
Feasibility is high: IPv6 `/64` allocations are handed out by default to individual residential/cloud customers, giving an attacker effectively unlimited distinct source addresses at negligible cost. No stake, keys, or special configuration are required — only the ability to open QUIC handshakes to the TPU port, which is a baseline capability of any unprivileged network client. The attack is trivially repeatable and sustainable as long as the attacker maintains address rotation faster than any IP-level ban/cleanup in `ConnectionRateLimiter`.

### Recommendation
Add subnet-aware rate limiting for IPv6 (e.g., key `ConnectionRateLimiter` by `/56` or `/64` prefix in addition to full address) so address rotation within an allocation cannot bypass per-source throttling. Additionally, consider reserving a portion of the global `overall_connection_rate_limiter` budget specifically for staked connections (or applying the global limiter after staked/unstaked classification) so unstaked/anonymous flooding cannot fully deplete the budget available to staked, higher-priority clients.

### Proof of Concept
```rust
// streamer/src/nonblocking/quic.rs (test harness sketch)
#[tokio::test]
async fn test_ipv6_rotation_defeats_per_ip_limiter_and_exhausts_global_bucket() {
    // Spin up run_server with default quic_server_params.
    // Generate N distinct IPv6 addresses all within a single /64, e.g.
    // 2001:db8::0001 .. 2001:db8::ffff
    let base: u128 = 0x2001_0db8_0000_0000_0000_0000_0000_0000;
    let mut attacker_ips = Vec::new();
    for i in 1..=5000u128 {
        attacker_ips.push(IpAddr::V6(Ipv6Addr::from(base + i)));
    }

    // Each simulated client from a distinct IP opens connections at a rate
    // below `max_connections_per_ipaddr_per_min`, but the aggregate rate
    // across all `attacker_ips` exceeds TOTAL_CONNECTIONS_PER_SECOND (2500)
    // and MAX_CONNECTION_BURST (1000).
    for ip in &attacker_ips {
        // open 1 connection per IP within the same second window
        open_quic_connection_from(ip).await;
    }

    // Assert: overall_connection_rate_limiter statistic climbs
    assert!(stats.connection_rate_limited_across_all.load(Ordering::Relaxed) > 0);

    // Assert: a legitimate, well-behaved client (single IP, well under
    // per-IP limit, potentially staked) is refused admission despite
    // never breaching its own per-IP quota.
    let legit_result = open_quic_connection_from(&legit_ip).await;
    assert!(legit_result.is_err_due_to(CONNECTION_CLOSE_CODE_TOO_MANY));
}
```

### Citations

**File:** streamer/src/nonblocking/connection_rate_limiter.rs (L34-50)
```rust
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

**File:** streamer/src/nonblocking/quic.rs (L64-65)
```rust
const CONNECTION_CLOSE_CODE_TOO_MANY: u32 = 4;
const CONNECTION_CLOSE_REASON_TOO_MANY: &[u8] = b"too_many";
```

**File:** streamer/src/nonblocking/quic.rs (L70-76)
```rust
/// Total new connection counts per second. Heuristically taken from
/// the default staked and unstaked connection limits. Might be adjusted
/// later.
const TOTAL_CONNECTIONS_PER_SECOND: f64 = 2500.0;

/// Max burst of connections above sustained rate to pass through
const MAX_CONNECTION_BURST: u64 = 1000;
```
