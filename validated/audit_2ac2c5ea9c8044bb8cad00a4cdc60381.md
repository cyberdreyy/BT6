Based on my research, the closest legitimate analog to this "frequent-request undermines the delay window" bug class in the agave codebase is the **fixed-window stream throttling counter used for TPU/QUIC ingest**, not any DeFi withdrawal-delay logic (which doesn't exist in agave). This maps the external report's bug class ("malicious actor times actions to exploit a periodic reset boundary and defeat the intended rate limit") onto agave's per-connection QUIC stream throttle.

### Title
Fixed-window stream throttle counter allows boundary-straddling bursts to bypass intended per-connection ingest rate limit - (File: streamer/src/nonblocking/stream_throttle.rs)

### Summary
`ConnectionStreamCounter::reset_throttling_params_if_needed` and `throttle_stream` implement a classic *fixed-window counter* rate limiter: a per-connection stream count is compared against `max_streams_per_throttling_interval` and reset to zero only when `STREAM_THROTTLING_INTERVAL_MS` (100ms) has fully elapsed since the last reset [1](#0-0) . Because the window boundary is a hard reset rather than a sliding/continuously-refilling accounting scheme, a peer that times its stream opens can consume the full quota right before a reset and again immediately after, doubling its effective throughput across the boundary — the same "reset-timing" bug class described in the external LP-withdrawal report, applied here to QUIC transaction ingest instead of a withdrawal delay.

### Finding Description
`throttle_stream` gates admission of a new QUIC stream (i.e., a new transaction packet at the TPU ingest layer) using a counter that is only cleared when the window has elapsed: [2](#0-1) 

The reset check in `reset_throttling_params_if_needed` uses a coarse "has more than `STREAM_THROTTLING_INTERVAL` passed since last reset" test [1](#0-0) . This is a fixed-window counter, not a token bucket. In a fixed window scheme, the enforced limit only guarantees `max_streams_per_throttling_interval` per *discrete* 100ms window — it does not bound the number of streams admitted in any *arbitrary* 100ms sliding window. A peer can:
1. Open `max_streams_per_throttling_interval` streams at t = 99ms (just before the window's reset instant recorded at their connection's `last_throttling_instant`).
2. Immediately after the reset fires (as soon as `duration_since(last_throttling_instant) > STREAM_THROTTLING_INTERVAL`), open another `max_streams_per_throttling_interval` streams at t = 101ms.

This yields ~2x `max_streams_per_throttling_interval` streams within a ~2ms real-time span, and because the peer controls exactly when it dials in relative to its own `last_throttling_instant`, this boundary-straddling can be repeated every interval, sustaining roughly double the intended long-run rate indefinitely — directly analogous to the LP repeatedly re-arming a withdrawal request just outside a fixed delay window to defeat the intended pacing. This is used for QUIC-based transaction ingest via `swqos.rs`'s `on_new_stream` calling `throttle_stream` with the per-connection `ConnectionStreamCounter` [3](#0-2) . Note that the newer `simple_qos.rs` path uses a continuous-refill `TokenBucket` (`consume_tokens`) instead [4](#0-3) , which is immune to this boundary-gaming pattern, but the `swqos.rs`/`stream_throttle.rs` fixed-window path remains reachable and used for staked/unstaked QUIC connections.

### Impact Explanation
An attacker controlling one or more QUIC connections to a validator's TPU can sustain roughly double the intended per-connection stream (transaction) admission rate by aligning bursts to each throttling window boundary. Combined across many connections/IPs, this amplifies the effectiveness of a TPU ingest flood relative to what the throttle is meant to permit, contributing to ingest starvation for legitimate traffic — this is a metering-bypass class issue on the transaction ingest path, matching the "ingest starvation" acceptance criterion. It does not directly cause fund loss, consensus divergence, or memory-safety violations, so impact is bounded to resource/availability degradation.

### Likelihood Explanation
Likelihood is medium: the attack only requires precise local timing of stream opens relative to the target connection's own throttling window (no need to guess validator internal clocks — the peer only needs to probe boundaries by observing when throttling stops applying, e.g., via induced sleeps in `throttle_stream`), and no special privilege or stake is required beyond opening a QUIC connection. The gain (~2x sustained) is bounded and the base per-connection caps (`DEFAULT_MAX_STREAMS_PER_MS`, `max_connections_per_ipaddr_per_min`) still limit the absolute scale, and the separate `ConnectionRateLimiter`/`overall_connection_rate_limiter` token buckets add friction, so this compounds with, rather than fully defeats, the overall QoS system.

### Recommendation
Replace the fixed-window reset in `ConnectionStreamCounter` with a token-bucket / sliding-window accounting scheme (as already done in `simple_qos.rs` via `TokenBucket::consume_tokens`), so that admission is bounded over any rolling interval rather than only over discrete, attacker-observable windows. Alternatively, keep counts partially decayed across resets (e.g., carry over a fraction of the previous window's usage) instead of zeroing them outright at `reset_throttling_params_if_needed`.

### Proof of Concept
1. Establish a QUIC connection to the TPU that is routed through `swqos.rs`'s throttling path (`SwQos::on_new_stream`).
2. Observe throttling behavior (via induced delay) to infer `last_throttling_instant` for the connection.
3. At t ≈ (interval − ε), open `max_streams_per_throttling_interval` streams — all admitted since the window hasn't reset yet.
4. Immediately after the window resets (t ≈ interval + ε), open another `max_streams_per_throttling_interval` streams — again all admitted since the counter was just zeroed.
5. Net result: ~2× `max_streams_per_throttling_interval` streams admitted within ~2ε time, repeatable every `STREAM_THROTTLING_INTERVAL_MS`, confirming the fixed-window reset in `reset_throttling_params_if_needed`/`throttle_stream` [5](#0-4)  can be exploited to sustain roughly double the intended per-connection ingest rate.

### Citations

**File:** streamer/src/nonblocking/stream_throttle.rs (L211-271)
```rust
    /// Reset the counter and last throttling instant and
    /// return last_throttling_instant regardless it is reset or not.
    pub(crate) fn reset_throttling_params_if_needed(&self) -> tokio::time::Instant {
        let last_throttling_instant = *self.last_throttling_instant.read().unwrap();
        if tokio::time::Instant::now().duration_since(last_throttling_instant)
            > STREAM_THROTTLING_INTERVAL
        {
            let mut last_throttling_instant = self.last_throttling_instant.write().unwrap();
            // Recheck as some other thread might have done throttling since this thread tried to acquire the write lock.
            if tokio::time::Instant::now().duration_since(*last_throttling_instant)
                > STREAM_THROTTLING_INTERVAL
            {
                *last_throttling_instant = tokio::time::Instant::now();
                self.stream_count.store(0, Ordering::Relaxed);
            }
            *last_throttling_instant
        } else {
            last_throttling_instant
        }
    }
}

pub(crate) async fn throttle_stream(
    stats: &StreamerStats,
    peer_type: ConnectionPeerType,
    remote_addr: std::net::SocketAddr,
    stream_counter: &Arc<ConnectionStreamCounter>,
    max_streams_per_throttling_interval: u64,
) {
    let throttle_interval_start = stream_counter.reset_throttling_params_if_needed();
    let streams_read_in_throttle_interval = stream_counter.stream_count.load(Ordering::Relaxed);
    if streams_read_in_throttle_interval >= max_streams_per_throttling_interval {
        // The peer is sending faster than we're willing to read. Sleep for what's
        // left of this read interval so the peer backs off.
        let throttle_duration =
            STREAM_THROTTLING_INTERVAL.saturating_sub(throttle_interval_start.elapsed());

        if !throttle_duration.is_zero() {
            debug!(
                "Throttling stream from {remote_addr:?}, peer type: {peer_type:?}, \
                 max_streams_per_interval: {max_streams_per_throttling_interval}, \
                 read_interval_streams: {streams_read_in_throttle_interval} throttle_duration: \
                 {throttle_duration:?}"
            );
            stats.throttled_streams.fetch_add(1, Ordering::Relaxed);
            match peer_type {
                ConnectionPeerType::Unstaked => {
                    stats
                        .throttled_unstaked_streams
                        .fetch_add(1, Ordering::Relaxed);
                }
                ConnectionPeerType::Staked(_) => {
                    stats
                        .throttled_staked_streams
                        .fetch_add(1, Ordering::Relaxed);
                }
            }
            sleep(throttle_duration).await;
        }
    }
}
```

**File:** streamer/src/nonblocking/swqos.rs (L496-516)
```rust
    #[allow(clippy::manual_async_fn)]
    fn on_new_stream(&self, context: &SwQosConnectionContext) -> impl Future<Output = ()> + Send {
        async move {
            let peer_type = context.peer_type();
            let remote_addr = context.remote_address;
            let stream_counter: &Arc<ConnectionStreamCounter> =
                context.stream_counter.as_ref().unwrap();

            let max_streams_per_throttling_interval =
                self.max_streams_per_throttling_interval(context);

            throttle_stream(
                &self.stats,
                peer_type,
                remote_addr,
                stream_counter,
                max_streams_per_throttling_interval,
            )
            .await;
        }
    }
```

**File:** streamer/src/nonblocking/simple_qos.rs (L385-420)
```rust
    #[allow(clippy::manual_async_fn)]
    fn on_new_stream(
        &self,
        context: &SimpleQosConnectionContext,
    ) -> impl Future<Output = ()> + Send {
        async move {
            let peer_type = context.peer_type();
            let remote_addr = context.remote_address;
            let stream_counter = context
                .stream_counter
                .as_ref()
                .expect("This will always be populated before streams are opened");

            while stream_counter.consume_tokens(1).is_err() {
                debug!("Throttling stream from {remote_addr:?}");
                self.stats.throttled_streams.fetch_add(1, Ordering::Relaxed);
                match peer_type {
                    ConnectionPeerType::Unstaked => {
                        self.stats
                            .throttled_unstaked_streams
                            .fetch_add(1, Ordering::Relaxed);
                    }
                    ConnectionPeerType::Staked(_) => {
                        self.stats
                            .throttled_staked_streams
                            .fetch_add(1, Ordering::Relaxed);
                    }
                }
                let min_sleep = stream_counter.us_to_have_tokens(1).expect(
                    "Valid QoS configurations guarantee enough token bucket fits at least one \
                     token",
                );
                sleep(Duration::from_micros(min_sleep)).await;
            }
        }
    }
```
