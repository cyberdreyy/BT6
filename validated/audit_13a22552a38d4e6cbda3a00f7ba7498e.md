### Title
TCP write back-pressure suppresses `TimeoutStream::poll_next` deadline check, allowing a single slow-read client to hold snapshot file handles and connection resources open indefinitely - ([File: rpc/src/rpc_service.rs])

### Summary
`TimeoutStream::poll_next` only checks whether `Instant::now() >= self.deadline` when the stream is actually polled by the hyper connection driver. Because hyper only polls the response body when it has TCP send-window capacity to write bytes to the client, a client that stops consuming bytes (advertises a full/zero receive window) causes hyper to stop polling the body entirely, so the deadline check in `TimeoutStream::poll_next` never executes and the underlying open file / task is never torn down.

### Finding Description
`TimeoutStream` is a thin wrapper that stores a `deadline: Instant` computed once in `TimeoutStream::new` and re-checked at the top of every `poll_next` call: [1](#0-0) . It is used to wrap the file stream served for `/snapshot.tar.bz2` and `/incremental-snapshot.tar.bz2` in `process_file_get`: [2](#0-1) .

The timeout is enforced lazily/opportunistically — it depends entirely on the stream being polled. It is not implemented as an independent timer future (e.g. `tokio::time::Sleep`/`tokio::select!` racing a sleep against the inner stream, or `tokio::time::timeout` wrapping each poll with its own wake source). The hyper `Body`/connection driver only calls `poll_next` on the response body stream when it has outbound TCP buffer capacity to write more bytes to the client socket. If a client opens the connection, reads a small amount of data, and then stops reading from the socket (e.g. by not issuing further TCP receive-window updates or simply not calling `read()` on its side), the OS-level TCP send buffer on the server fills, `poll_write` on the socket returns `Poll::Pending`, and hyper's connection task is parked waiting on socket writability — it will not re-poll `TimeoutStream::poll_next` until the socket becomes writable again. Since the client never reads, the socket never becomes writable, `poll_next` (and therefore the `Instant::now() >= self.deadline` check) is never invoked again, and the deadline is never enforced.

This means the open `tokio::fs::File` handle backing the `FramedRead`, the tokio task driving the connection, and the resources of the "solRpcEl" runtime worker remain allocated for as long as the client keeps the connection half-open without reading — well past `FALLBACK_FULL_SNAPSHOT_TIMEOUT_SECS` (12,000s) or the computed `snapshot_timeout` in `process_file_get`: [3](#0-2) . No other guard exists in the code path (`RequestMiddleware::on_request` → `is_file_get_path` → `process_file_get`) that enforces a wall-clock or idle-read timeout independently of stream polling: [4](#0-3) .

### Impact Explanation
A single unprivileged client can open one `GET /snapshot.tar.bz2` (or incremental) connection, read a small amount of data, then stop draining the socket (never call further `read()`), keeping the TCP connection alive at the transport layer (e.g. via TCP keepalive) without closing it. This ties up: (1) an open file descriptor for the snapshot archive, (2) a task/slot on the RPC's shared multi-threaded tokio runtime (`service_runtime`, `solRpcEl` threads) that also services all other JSON-RPC calls and pubsub for the validator: [5](#0-4) . Because `rpc_threads` is finite and shared with regular JSON-RPC handling, enough such stalled snapshot connections (each requiring only one low-rate client, no privileged access) can eventually starve the pool used to service unrelated RPC calls, matching the "unbounded cost for a single low-rate call" DoS bounty category.

### Likelihood Explanation
Preconditions are minimal and match the allowed attacker capability: the snapshot endpoint must be reachable (default validator RPC configuration), and the attacker needs only one connection performing at most one HTTP GET, well under the `CLUSTER_SLOT_TIME_TARGET / 2` rate limit since after the initial GET no further requests are needed — the resource hold is achieved by simply not reading further bytes, not by issuing more calls. This is fully client-side controllable (via raw socket manipulation, e.g., setting a tiny/zero receive buffer or simply not calling `read()`), requires no validator/operator misconfiguration, and is repeatable per-connection.

### Recommendation
Implement the timeout with an independent wake source instead of an opportunistic field check, e.g. wrap the body with `tokio::time::timeout(remaining, ...)` per read or race the inner stream against a `tokio::time::Sleep` via `futures::select!`/`tokio::select!` inside `poll_next`, registering the sleep's waker so the task is woken and the connection forcibly closed at `deadline` even if the socket write side never becomes ready. Alternatively, spawn a companion task/timer that calls `close_handle` or aborts the specific connection at `deadline` regardless of whether the body stream is polled.

### Proof of Concept
```rust
// Integration-test sketch (place near rpc/src/rpc_service.rs tests)
// 1. Start JsonRpcService with a snapshot_config pointing at a small dummy
//    "full" snapshot archive file and a short custom fallback (patch
//    FALLBACK_FULL_SNAPSHOT_TIMEOUT_SECS/pass a tiny SnapshotInterval for
//    the test, or use dependency injection so the timeout is a few seconds).
// 2. Connect a raw std::net::TcpStream to the RPC address, send:
//      "GET /snapshot.tar.bz2 HTTP/1.1\r\nHost: x\r\nConnection: keep-alive\r\n\r\n"
// 3. Read only the HTTP header + 1 byte of body, then do NOT read further
//    (do not close the socket; keep it open, e.g. `std::thread::sleep`).
//    Optionally shrink the socket's SO_RCVBUF or read via a stalled peer to
//    guarantee TCP backpressure on the server's write side.
// 4. Sleep past `deadline` (e.g. deadline + 5s).
// 5. Assert the server-side connection was NOT closed by the deadline
//    (e.g. attempt a subsequent request over a fresh connection and observe
//    it succeeds, but the stalled connection's underlying file/task is
//    still alive — instrument via an AtomicBool/Drop guard wrapped around
//    the test file's FramedRead stream to detect it hasn't been dropped
//    after `deadline` has passed), demonstrating the timeout enforcement in
//    TimeoutStream::poll_next never fired because it was never re-polled.
```
Expected assertion: the wrapped test stream's `Drop` guard fires only after the client eventually reads/closes, not at `deadline`, proving the enforced timeout is bypassed by stalling reads rather than being enforced by wall-clock independent of polling.

### Citations

**File:** rpc/src/rpc_service.rs (L84-113)
```rust
struct TimeoutStream<S> {
    inner: S,
    deadline: Instant,
}

impl<S> TimeoutStream<S> {
    fn new(inner: S, timeout: Duration) -> Self {
        Self {
            inner,
            deadline: Instant::now() + timeout,
        }
    }
}

impl<S> Stream for TimeoutStream<S>
where
    S: Stream<Item = std::io::Result<Bytes>> + Unpin,
{
    type Item = std::io::Result<Bytes>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        if Instant::now() >= self.deadline {
            return Poll::Ready(Some(Err(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                "snapshot transfer deadline exceeded",
            ))));
        }
        Pin::new(&mut self.inner).poll_next(cx)
    }
}
```

**File:** rpc/src/rpc_service.rs (L282-308)
```rust
        let snapshot_timeout = self.snapshot_config.as_ref().and_then(|config| {
            snapshot_type.map(|st| {
                let interval = match st {
                    SnapshotKind::Full => config.full_snapshot_archive_interval,
                    SnapshotKind::Incremental => config.incremental_snapshot_archive_interval,
                };
                let computed = match interval {
                    SnapshotInterval::Disabled => Duration::ZERO,
                    SnapshotInterval::Slots(slots) => {
                        let ns_per_slot = self
                            .bank_forks
                            .read()
                            .unwrap()
                            .root_bank()
                            .ns_per_slot
                            .try_into()
                            .unwrap_or(solana_clock::DEFAULT_MS_PER_SLOT * 1_000_000);
                        Duration::from_nanos(slots.get().saturating_mul(ns_per_slot))
                    }
                };
                let fallback = match st {
                    SnapshotKind::Full => FALLBACK_FULL_SNAPSHOT_TIMEOUT_SECS,
                    SnapshotKind::Incremental => FALLBACK_INCREMENTAL_SNAPSHOT_TIMEOUT_SECS,
                };
                std::cmp::max(computed, fallback)
            })
        });
```

**File:** rpc/src/rpc_service.rs (L310-334)
```rust
        RequestMiddlewareAction::Respond {
            should_validate_hosts: true,
            response: Box::pin(async move {
                match Self::open_no_follow(filename).await {
                    Err(err) => Ok(if err.kind() == std::io::ErrorKind::NotFound {
                        Self::not_found()
                    } else {
                        Self::internal_server_error()
                    }),
                    Ok(file) => {
                        let stream =
                            FramedRead::new(file, BytesCodec::new()).map_ok(|b| b.freeze());
                        let body = if let Some(timeout) = snapshot_timeout {
                            hyper::Body::wrap_stream(TimeoutStream::new(stream, timeout))
                        } else {
                            hyper::Body::wrap_stream(stream)
                        };
                        Ok(hyper::Response::builder()
                            .header(hyper::header::CONTENT_LENGTH, file_length)
                            .body(body)
                            .unwrap())
                    }
                }
            }),
        }
```

**File:** rpc/src/rpc_service.rs (L394-407)
```rust
        if let Some(path) = match_supply_path(request.uri().path()) {
            process_rest(self.bank_forks.clone(), path)
        } else if self.is_file_get_path(request.uri().path()) {
            self.process_file_get(request.uri().path())
        } else if request.uri().path() == "/health" {
            hyper::Response::builder()
                .status(hyper::StatusCode::OK)
                .body(hyper::Body::from(self.health_check()))
                .unwrap()
                .into()
        } else {
            request.into()
        }
    }
```

**File:** rpc/src/rpc_service.rs (L795-828)
```rust
pub fn service_runtime(
    rpc_threads: usize,
    rpc_blocking_threads: usize,
    rpc_niceness_adj: i8,
) -> Arc<TokioRuntime> {
    // The jsonrpc_http_server crate supports two execution models:
    //
    // - By default, it spawns a number of threads - configured with .threads(N) - and runs a
    //   single-threaded futures executor in each thread.
    // - Alternatively when configured with .event_loop_executor(executor) and .threads(1),
    //   it executes all the tasks on the given executor, not spawning any extra internal threads.
    //
    // We use the latter configuration, using a multi threaded tokio runtime as the executor. We
    // do this so we can configure the number of worker threads, the number of blocking threads
    // and then use tokio::task::spawn_blocking() to avoid blocking the worker threads on CPU
    // bound operations like getMultipleAccounts. This results in reduced latency, since fast
    // rpc calls (the majority) are not blocked by slow CPU bound ones.
    //
    // NB: `rpc_blocking_threads` shouldn't be set too high (defaults to num_cpus / 2). Too many
    // (busy) blocking threads could compete with CPU time with other validator threads and
    // negatively impact performance.
    let rpc_threads = 1.max(rpc_threads);
    let rpc_blocking_threads = 1.max(rpc_blocking_threads);
    Arc::new(
        TokioBuilder::new_multi_thread()
            .worker_threads(rpc_threads)
            .max_blocking_threads(rpc_blocking_threads)
            .on_thread_start(move || renice_this_thread(rpc_niceness_adj).unwrap())
            .thread_name("solRpcEl")
            .enable_all()
            .build()
            .expect("Runtime"),
    )
}
```
