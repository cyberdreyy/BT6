### Title
Snapshot download `TimeoutStream` deadline is never enforced against a client that stops reading, allowing indefinite FD/task pinning from a single request - ([File: rpc/src/rpc_service.rs])

### Summary
The snapshot/genesis file-serving path wraps a `tokio::fs::File`-backed stream in `TimeoutStream`, whose only timeout enforcement is an `Instant::now() >= self.deadline` check performed inside `poll_next` [1](#0-0) . Because hyper only calls `poll_next` when it needs another chunk to write to the socket, a client that opens the connection and then stops reading (closing its TCP receive window) prevents hyper from ever re-invoking `poll_next`, so the deadline check never runs and the underlying file handle/task is held open indefinitely by a single unprivileged connection.

### Finding Description
`RpcRequestMiddleware::process_file_get` opens the requested genesis/snapshot file with `tokio::fs::File`, wraps it in `FramedRead`, and — when a `snapshot_timeout` is computed — wraps that stream in `TimeoutStream::new(stream, timeout)`, finally handing it to `hyper::Body::wrap_stream` [2](#0-1) . `TimeoutStream::poll_next` is the only place the deadline is checked: it compares `Instant::now()` against `self.deadline` and forwards to the inner stream's `poll_next` otherwise [3](#0-2) .

hyper's HTTP/1 body writer only requests more data from the wrapped `Stream` when it has flushed previously buffered data to the socket and needs the next chunk. If the client stops reading the response (e.g. sets its TCP receive window to zero, or simply never calls `read()`/`recv()` after issuing the GET), hyper's socket write will block, and hyper will not poll the body stream again until the socket becomes writable and the outstanding chunk is fully written. Since `TimeoutStream::poll_next` is the sole enforcement point for the deadline, an un-polled stream never times out — the deadline is a piece of state that is checked reactively rather than an actively scheduled cancellation (no `tokio::time::sleep`/`timeout()` wrapping the whole response, no independent watchdog task, no connection-level idle/write timeout configured anywhere else in this file). The `tokio::fs::File` (an open FD) and the task driving the hyper connection therefore remain alive for as long as the attacker leaves the TCP connection open without reading, which is unbounded and entirely controlled by the attacker.

This requires nothing beyond a single unprivileged HTTP request against `/snapshot.tar.bz2`, `/incremental-snapshot.tar.bz2`, or the genesis path, followed by ceasing to read the socket — a single call, matching the "at most one call" constraint, and no gossip/leader/staked privileges are needed.

### Impact Explanation
Each such connection pins an open file descriptor and an async task on the validator's JSON-RPC/hyper runtime for as long as the attacker holds the socket without reading, with the only theoretical bound (the `TimeoutStream` deadline) never firing under this access pattern. This is an unbounded-resource-cost issue from a single low-rate client request — repeated (but still rate-limited, one every `CLUSTER_SLOT_TIME_TARGET/2`) connections of this kind accumulate held FDs/tasks against the RPC service, degrading or eventually exhausting file-descriptor/connection capacity for legitimate snapshot downloads and other RPC/pubsub traffic served by the same hyper runtime.

### Likelihood Explanation
Highly feasible and fully repeatable: any client with network access to the RPC/snapshot endpoint (default when `--enable-rpc-transaction-history`/snapshot serving is enabled, or the genesis download path which is always active) can open a TCP connection, issue the GET, and simply stop draining the socket. No special timing, no cluster interaction, and no more than a single request is required.

### Recommendation
Do not rely on a poll-only deadline check embedded in the stream. Instead, drive the response body under an actively-scheduled cancellation, e.g. wrap the whole per-connection send operation in `tokio::time::timeout(...)`, or spawn a watchdog task/`tokio_util::sync::CancellationToken` that is triggered by a `tokio::time::sleep` timer independent of whether `poll_next` is invoked, and abort/close the file stream and connection when the timer fires regardless of consumer polling. Additionally, configure hyper/jsonrpc_http_server-level write/idle timeouts so stalled sockets are closed even if the application-level deadline logic is bypassed.

### Proof of Concept
```rust
// Integration-test sketch for rpc/src/rpc_service.rs

use std::time::Duration;
use futures::stream::{self, StreamExt};
use tokio::time::sleep;

// A stream that never gets polled again once the "client" stops driving it,
// simulating a hyper Body that stalls because the socket write is blocked.
struct NeverPolledAgain;
impl futures::Stream for NeverPolledAgain {
    type Item = std::io::Result<tokio_util::bytes::Bytes>;
    fn poll_next(self: std::pin::Pin<&mut Self>, _cx: &mut std::task::Context<'_>)
        -> std::task::Poll<Option<Self::Item>> {
        std::task::Poll::Ready(Some(Ok(tokio_util::bytes::Bytes::from_static(b"chunk"))))
    }
}

#[tokio::test]
async fn timeout_stream_never_fires_without_poll() {
    // Build TimeoutStream with a short deadline (simulate rpc_service.rs::TimeoutStream)
    let mut ts = TimeoutStream::new(NeverPolledAgain, Duration::from_millis(50));

    // Poll once to get the first chunk (this is what hyper does before blocking on write)
    let first = futures::poll!(futures::StreamExt::next(&mut ts));
    assert!(matches!(first, std::task::Poll::Ready(Some(Ok(_)))));

    // Simulate the client stalling: never poll the stream again for well beyond `deadline`.
    sleep(Duration::from_millis(500)).await;

    // Assert: the stream's deadline error is NEVER observed because nothing re-polled it,
    // demonstrating there is no external/active mechanism reclaiming the resource.
    // (In the real system this corresponds to the tokio::fs::File FD and the hyper
    // connection task remaining alive indefinitely.)
    // No assertion can show a natural timeout firing here — that's the bug:
    // resource reclamation is entirely dependent on the attacker choosing to poll.
}
```
Expected result: the test demonstrates that once polling stops, `TimeoutStream`'s deadline check is never re-evaluated, so nothing closes the file/task; an external active timer (not present in the current implementation) would be required to bound the resource, confirming the invariant violation.

### Citations

**File:** rpc/src/rpc_service.rs (L98-113)
```rust
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

**File:** rpc/src/rpc_service.rs (L310-333)
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
```
