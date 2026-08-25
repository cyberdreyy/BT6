Based on my investigation, I found the key candidate: `cache_new_connection` in `streamer/src/nonblocking/simple_qos.rs`, which performs a multiply-before-divide computation on a value derived from an unprivileged, attacker-influenced input (connection RTT) without using checked/saturating arithmetic throughout the whole expression chain.

### Title
Unchecked multiply-then-divide-then-multiply order-of-operations in QUIC per-connection stream-limit calculation can wrap/truncate `max_streams_in_flight` - (File: streamer/src/nonblocking/simple_qos.rs)

### Summary
`SimpleQos::cache_new_connection` computes the per-connection concurrent-stream cap as `(max_streams_per_second as u32).saturating_mul(rtt) / 1000 * STREAMS_IN_FLIGHT_MARGIN`. The final `* STREAMS_IN_FLIGHT_MARGIN` step is a plain, non-saturating `u32` multiplication performed *after* the saturating multiply and division, so overflow protection is not applied consistently across the whole formula, unlike the `mulDiv`-style pattern that Sherlock issue #39 flagged as missing. [1](#0-0) 

### Finding Description
The relevant computation is:
```rust
let rtt = connection.rtt().clamp(MIN_RTT, MAX_RTT).as_millis() as u32;
let max_streams_in_flight = (self.config.max_streams_per_second as u32).saturating_mul(rtt)
    / 1000
    * STREAMS_IN_FLIGHT_MARGIN;
``` [2](#0-1) 
`saturating_mul` is used for the first multiplication (`max_streams_per_second * rtt`), which prevents overflow there, but the subsequent `* STREAMS_IN_FLIGHT_MARGIN` (a plain arithmetic `*`, not `saturating_mul`) is applied *after* the division, mixing checked and unchecked operators within a single fee/throughput-limit formula analogous to the `sharesExchangeRate()` pattern in the referenced report, where only some operations in the multiply/divide chain are protected. In a release build this wraps silently (producing an incorrect, possibly very small `max_streams_in_flight` due to `u32` wraparound) rather than panicking, and in a debug build it would panic (`overflow` panic), crashing the QUIC ingest thread. [3](#0-2) 

`max_streams_per_second` is an operator-configured value (`SimpleQosConfig`), not directly attacker-controlled, and `rtt` is clamped to `[MIN_RTT, MAX_RTT]` before use, and the first multiply already saturates before the division. Given the clamp on `rtt` and the divide-by-1000 preceding the final multiply, the practical range of values entering the final `* STREAMS_IN_FLIGHT_MARGIN` (margin = 2) is bounded well below `u32::MAX`, so an actual overflow is not reachable with the built-in `RTT`/config bounds I could verify from the code shown.

### Impact Explanation
If overflow were reachable (e.g., through misconfiguration of `max_streams_per_second` to a very large value), the wraparound would silently under- or over-compute `max_streams_in_flight`, affecting the QUIC per-connection concurrent-stream throttle used in `try_add_connection`/`cache_new_connection`, which is on the unprivileged QUIC ingest path. A drastically wrong value could either starve legitimate stream ingestion for a staked peer or (if wrapped small) inadvertently open way more concurrent streams than intended, affecting fairness of the QoS mechanism. This is a much weaker and more contained analog than the referenced Sherlock finding: it is bounded by configuration rather than freely attacker-controlled decimals, so I cannot substantiate exploitability with the evidence gathered.

### Likelihood Explanation
Low. `rtt` is clamped, `MAX_RTT`/`MIN_RTT` and `STREAMS_IN_FLIGHT_MARGIN` are small constants, and `max_streams_per_second` defaults to a modest constant (`DEFAULT_MAX_STREAMS_PER_MS * 1000`); I did not find a way for an ordinary, unprivileged remote peer to control `max_streams_per_second` or push `rtt` (already clamped) high enough to overflow `u32` in the observed configuration. I could not fully verify all possible configuration ranges for `max_streams_per_second` (whether operators can set arbitrarily large values via CLI/config) within the available index.

### Recommendation
Use `saturating_mul` (or `checked_mul` with explicit fallback) consistently for every multiplication in the formula, including the final `* STREAMS_IN_FLIGHT_MARGIN` step, mirroring the recommendation to use an overflow-safe multiply/divide (`mulDiv`-equivalent) throughout the whole calculation rather than only on the first operand pair.

### Proof of Concept
Not established. I could not construct a concrete, unprivileged-user-reachable input that drives `max_streams_per_second as u32 * rtt / 1000 * STREAMS_IN_FLIGHT_MARGIN` past `u32::MAX`, given the `rtt` clamp (`MIN_RTT..MAX_RTT`) and the fact that `max_streams_per_second` is an operator/validator-configured constant rather than data supplied by the connecting peer. Given the lack of a demonstrated overflow path, this should be treated as a defensive-coding inconsistency rather than a confirmed exploitable vulnerability.

### Citations

**File:** streamer/src/nonblocking/simple_qos.rs (L139-147)
```rust
impl Default for SimpleQosConfig {
    fn default() -> Self {
        SimpleQosConfig {
            max_streams_per_second: DEFAULT_MAX_STREAMS_PER_MS * 1000,
            max_staked_connections: DEFAULT_MAX_STAKED_CONNECTIONS,
            max_connections_per_peer: DEFAULT_MAX_QUIC_CONNECTIONS_PER_STAKED_PEER,
        }
    }
}
```

**File:** streamer/src/nonblocking/simple_qos.rs (L191-199)
```rust
        // this will never overflow u32 for reasonable MAX_RTT
        let rtt = connection.rtt().clamp(MIN_RTT, MAX_RTT).as_millis() as u32;
        let max_streams_in_flight = (self.config.max_streams_per_second as u32).saturating_mul(rtt)
            / 1000
            * STREAMS_IN_FLIGHT_MARGIN;
        // for very low values of max_streams_per_second, prevent connections from having zero
        // streams in flight
        let max_streams_in_flight = max_streams_in_flight.max(STREAMS_IN_FLIGHT_MARGIN);
        connection.set_max_concurrent_uni_streams(VarInt::from_u32(max_streams_in_flight));
```
