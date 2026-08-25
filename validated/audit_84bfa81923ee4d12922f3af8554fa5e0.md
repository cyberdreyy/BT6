### Title
Fixed-window per-connection stream throttle allows ~2x burst of QUIC streams at window boundary - (File: streamer/src/nonblocking/stream_throttle.rs)

### Summary
The QUIC stream-ingestion throttle used by both `SwQos` and `SimpleQos` is implemented as a **fixed-window counter** (`ConnectionStreamCounter`) rather than a continuously-refilling token bucket. Because the counter is reset to `0` only when the elapsed time since the last reset exceeds `STREAM_THROTTLING_INTERVAL` (100ms), a peer can send up to `max_streams_per_throttling_interval` streams at the very end of one window and another full batch immediately after the reset at the start of the next window, achieving roughly double the intended rate within a short span straddling the window boundary. This is the same bug class as the reported `RateLimit.sol` issue, where a caller could mint up to 2x `maxAllowance` by exploiting partial/boundary replenishment instead of a true fixed-interval enforcement.

### Finding Description
`ConnectionStreamCounter::reset_throttling_params_if_needed` only resets `stream_count` when `now - last_throttling_instant > STREAM_THROTTLING_INTERVAL`; otherwise it returns the existing window start unchanged and the counter keeps accumulating within that same window: [1](#0-0) 

`throttle_stream` compares `stream_counter.stream_count` against `max_streams_per_throttling_interval` and only sleeps (throttles) once that count is reached within the current window; it never accounts for streams accepted near the boundary of the *previous* window: [2](#0-1) 

This is invoked per accepted QUIC stream via `SwQos::on_new_stream` (and analogously in `SimpleQos`), i.e., directly gating TPU/QUIC transaction-stream ingest from an ordinary, unprivileged remote peer: [3](#0-2) 

Because the window boundary is not aligned to any global clock and is reset lazily per-connection on first access after expiry, an attacker fully controls the timing of their own sends: they can burst `max_streams_per_throttling_interval` streams just before `last_throttling_instant + STREAM_THROTTLING_INTERVAL` elapses, then immediately burst another full quota right after the reset fires on their very next stream. Within an arbitrarily small real-time window (bounded only by RTT/stream-open latency) they can push up to ~2x the configured `max_streams_per_ms`-derived quota, exactly mirroring the reported `RateLimit.sol` flaw where a caller could mint 2x `maxAllowance` by exploiting the boundary between "partial refill" and a subsequent refill instead of enforcing a strict per-interval cap.

By contrast, the general-purpose `TokenBucket` in `net-utils/src/token_bucket.rs` used elsewhere (connection-rate limiting, gossip budgets) implements continuous proportional refill capped at `max_tokens`, which is the standard, well-understood token-bucket algorithm and is not vulnerable to this specific boundary-doubling class in the way a naive fixed-window reset is: [4](#0-3) 

### Impact Explanation
This weakens the per-connection stream ingestion throttle that is meant to bound how fast a single (staked or unstaked) QUIC connection can open streams (i.e., submit transaction packets) to a validator's TPU. An attacker exploiting the boundary can roughly double their effective throughput budget relative to the configured `max_streams_per_ms` / stake-weighted allocation, increasing their share of TPU ingest capacity beyond what QoS is designed to permit, at the expense of other unstaked/low-stake or staked peers whose fair share is computed from `StakedStreamLoadEMA::available_load_capacity_in_throttling_duration`. This is a partial bypass of the ingest-limiting/QoS mechanism (ingest starvation for other peers), not a fund-loss or consensus-divergence bug.

### Likelihood Explanation
Exploitation only requires an ordinary network peer capable of opening a QUIC connection to the validator's TPU (staked or unstaked) and precisely timing stream opens around a 100ms window boundary, which is easily automatable client-side. No special privileges, node access, or protocol violation is needed. However, the gain is bounded to roughly 2x within a short window and is naturally smoothed out by `StakedStreamLoadEMA`'s longer-horizon load tracking and by connection/stream concurrency caps (`max_concurrent_uni_streams`), which limits sustained abuse.

### Recommendation
Replace the fixed-window reset in `ConnectionStreamCounter` with a sliding-window or continuous token-bucket style accounting (as already implemented in `net-utils::token_bucket::TokenBucket`) so that the effective quota available to a connection never exceeds `max_streams_per_throttling_interval` over *any* rolling `STREAM_THROTTLING_INTERVAL`-length window, rather than being reset in discrete jumps. Alternatively, track a proportional carry-over/decay of the previous window's count when computing whether the current request should be throttled.

### Proof of Concept
Conceptual reproduction based on the throttle logic in `stream_throttle.rs`:
1. Configure `max_streams_per_throttling_interval = N` for a connection (`STREAM_THROTTLING_INTERVAL_MS = 100`).
2. At `t = 99ms` (just under the 100ms window boundary since connection/counter creation), open N streams rapidly; `reset_throttling_params_if_needed` does not reset (elapsed ≤ 100ms), so all N are accepted without triggering throttling in `throttle_stream`.
3. At `t = 101ms`, open one more stream; `reset_throttling_params_if_needed` now resets `stream_count` to 0 and `last_throttling_instant` to now (elapsed > 100ms).
4. Immediately open N more streams; since the counter was just reset, all N are again accepted before throttling triggers.
5. Net effect: ~2N streams accepted within roughly a 2-3ms real-time span straddling the boundary, versus the intended N streams per 100ms — a ~2x burst of the configured rate limit, directly analogous to the `RateLimit.sol` double-mint scenario in the external report. [5](#0-4)

### Citations

**File:** streamer/src/nonblocking/stream_throttle.rs (L211-270)
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

**File:** net-utils/src/token_bucket.rs (L160-214)
```rust
    /// Updates internal state of the bucket by
    /// depositing new tokens (if appropriate)
    fn update_state(&self, now: u64) {
        // fetch last update time
        let last = self.last_update.load(Ordering::SeqCst);

        // If time has not advanced, nothing to do.
        if now <= last {
            return;
        }

        // Try to claim the interval [last, now].
        // If we can not claim it, someone else will claim [last..some other time] when they
        // touch the bucket.
        // If we can claim interval [last, now], no other thread can credit tokens for it anymore.
        // If [last, now] is too short to mint any tokens, spare time will be preserved in credit_time_us.
        match self.last_update.compare_exchange(
            last,
            now,
            Ordering::AcqRel,  // winner publishes new timestamp
            Ordering::Acquire, // loser observes updates
        ) {
            Ok(_) => {
                // This thread won the race and is responsible for minting tokens
                let elapsed = now.saturating_sub(last);

                // also add leftovers from previous conversion attempts.
                // we do not care about who uses the spare_time_us, so relaxed is ok here.
                let elapsed =
                    elapsed.saturating_add(self.credit_time_us.swap(0, Ordering::Relaxed));

                let new_tokens_f64 = elapsed as f64 * self.new_tokens_per_us;

                // amount of full tokens to be minted
                let new_tokens = new_tokens_f64.floor() as u64;

                let time_to_return = if new_tokens >= 1 {
                    // Credit tokens, saturating at max_tokens
                    self.add_tokens(new_tokens);
                    // Fractional remainder of elapsed time (not enough to mint a whole token)
                    // that will be credited to other minters
                    (new_tokens_f64.fract() / self.new_tokens_per_us) as u64
                } else {
                    // No whole tokens minted → return whole interval
                    elapsed
                };
                // Save unused elapsed time for other threads
                self.credit_time_us
                    .fetch_add(time_to_return, Ordering::Relaxed);
            }
            Err(_) => {
                // Another thread advanced last_update first → nothing we can do now.
            }
        }
    }
```
