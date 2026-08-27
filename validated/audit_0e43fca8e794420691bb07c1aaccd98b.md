### Title
Race between `wsConnectionWrapper.Close()` and in-flight `FinalizeHandshake()` leaks an unmanaged, unclosed raw `net.Conn` carrying an authenticated node session - ([File: core/services/gateway/network/wsconnection.go])

### Summary
`wsConnectionWrapper.Reset()` swaps in a new connection and only starts a `readPump` (and thus only takes ownership of closing it later) if `c.wg.TryAdd(1)` succeeds. If `Close()` has already begun and called `wg.Wait()` on the wrapper's internal `WaitGroup`, a concurrently-finalizing handshake's call to `Reset(conn)` will still swap `conn` into `c.conn`, but `TryAdd(1)` fails (the WaitGroup is sealed post-`Wait()`), so `Reset()` returns `nil` without closing `conn` and without starting anything to manage it. The socket is left open, unread, and unmanaged, while carrying a fully-authenticated oracle session.

### Finding Description
`connectionManager.Close()` (`core/services/gateway/connectionmanager.go:179-194`) calls `nodeState.conn.Close()` for each node, which invokes `wsConnectionWrapper.Close()` (`core/services/gateway/network/wsconnection.go:147-154`):
```go
func (c *wsConnectionWrapper) Close() error {
	return c.StopOnce("WSConnectionWrapper", func() error {
		close(c.shutdownCh)
		c.Reset(nil)
		c.wg.Wait()
		return nil
	})
}
```
Concurrently, a client that already passed `StartHandshake` can call `FinalizeHandshake(attemptId, response, conn)` (`core/services/gateway/connectionmanager.go:241-291`), which ends with `attempt.nodeState.conn.Reset(conn)` — this is not gated by any shutdown-state check on the `connectionManager` or the `wsConnectionWrapper`.

Inside `Reset()` (`core/services/gateway/network/wsconnection.go:103-119`):
```go
func (c *wsConnectionWrapper) Reset(newConn *websocket.Conn) <-chan error {
	oldConn := c.conn.Swap(newConn)
	if oldConn != nil {
		oldConn.Close()
	}
	if newConn == nil {
		return nil
	}
	if err := c.wg.TryAdd(1); err != nil {
		return nil
	}
	closeCh := make(chan error, 1)
	go c.readPump(newConn, closeCh)
	return closeCh
}
```
If the handshake's `Reset(conn)` races with `Close()`'s internal `Reset(nil)` and executes after `wg.Wait()` has already sealed the wait group (i.e., `Close()`'s `Reset(nil)` already ran, swapping a possibly-nil old conn, and `wg.Wait()` returned because no other goroutine was pending), then:
- `oldConn = c.conn.Swap(newConn)` succeeds and stores the newly-handshaken conn into `c.conn`.
- `oldConn` is nil (nothing was previously set, or was already cleared by Close's own `Reset(nil)`), so no close happens there.
- `wg.TryAdd(1)` fails because the WaitGroup has already been drained/sealed by the `Close()` call's `wg.Wait()`.
- The function returns `nil` immediately — **`newConn` is never closed**, no `readPump` is spawned to manage or eventually close it, and `writePump` has already exited (it returned on `<-c.shutdownCh`).

The result: `c.conn` field now holds a pointer to a live `*websocket.Conn` that passed full signature/challenge verification in `FinalizeHandshake` (a legitimately authenticated oracle session), but no goroutine reads from it, no goroutine will ever close it via the wrapper's normal lifecycle, and the wrapper is already in `Closed` state (`StopOnce` completed). The raw socket remains open at the OS level, outside of `ConnectionManager`'s tracked lifecycle, until the process eventually forces a TCP-level teardown or the peer disconnects.

No existing check in `FinalizeHandshake` or `Reset` verifies that the wrapper (or the owning `connectionManager`) is not shutting down/shut down before committing the new connection.

### Impact Explanation
This produces a dangling, live, authenticated websocket connection that bypasses the gateway's managed connection lifecycle. Practically: after a logical `Close()` (e.g., during a rolling deploy, credential/config rotation, or graceful shutdown), an attacker who timed a handshake to straddle the shutdown window retains an open socket that the intended cleanup was supposed to terminate. Depending on how the surrounding process handles listener/port teardown, this could allow continued read/write on a channel presented as closed, undermining assumptions that "shutdown" revokes connectivity, and enabling receipt/injection of messages after config/credential rotation which admin intended to invalidate. This is an isolation/lifecycle-soundness bug rather than a direct secret-disclosure primitive by itself, since the conn was already legitimately handshaken by a node signer — but it defeats the "Close() must fully tear down all managed sockets" invariant.

### Likelihood Explanation
Requires precise timing: the attacker (a legitimate DON node holding a valid signing key — not a totally unprivileged party, since `FinalizeHandshake` requires passing `common.ExtractSigner` against the challenge signed with a node's private key) must complete `StartHandshake`/`FinalizeHandshake` exactly as `connectionManager.Close()`/`wsConnectionWrapper.Close()` executes. This is a narrow, timing-dependent race, only exploitable by an entity with a valid registered node keypair for the DON — not an arbitrary unauthenticated internet client. It is reproducible in a unit test by directly manipulating `wsConnectionWrapper` internals (`sync/atomic` conn pointer and the internal `WaitGroup`) to force the ordering, but doing so via genuine network timing against a live process is comparatively difficult, though not impossible for a compromised/malicious node.

### Recommendation
In `wsConnectionWrapper.Reset()`, close `newConn` explicitly when `wg.TryAdd(1)` fails instead of silently discarding it:
```go
if err := c.wg.TryAdd(1); err != nil {
    newConn.Close()
    c.conn.CompareAndSwap(newConn, nil)
    return nil
}
```
Additionally, `FinalizeHandshake` in `connectionmanager.go` should check the `connectionManager`'s stop state (e.g., via `c.IfStarted`/`c.State()` guard) before calling `Reset(conn)`, and close `conn` immediately if the manager is already stopping/stopped, so an in-flight handshake finalized during shutdown never installs a live socket into an already-closed wrapper.

### Proof of Concept
Go unit test in `core/services/gateway/network/wsconnection_test.go`:
1. Construct a `wsConnectionWrapper` via `NewWSConnectionWrapper`, call `Start(ctx)`.
2. Spin a goroutine that blocks briefly, then call `cw.Close()`.
3. Concurrently (racing intentionally, or using a hook/instrumented build to force ordering: swap `c.conn` to nil then call `wg.Wait()` synchronously before letting `Reset` proceed), call `cw.Reset(fakeConn)` with a fake `*websocket.Conn` double (backed by an in-memory `net.Pipe()` pair) after `Close()`'s internal `wg.Wait()` has already returned.
4. Assert: `cw.Reset(fakeConn)` returns `nil` (as documented), AND assert the fake conn's underlying pipe/socket is **not** closed (e.g., check by writing to the peer end and confirming no `io.ErrClosedPipe`), demonstrating the leaked open socket.
5. Expected (fixed) behavior: after the fix, the same sequence should result in `fakeConn`/its underlying pipe being closed, verified by asserting subsequent reads/writes on the peer return `io.EOF` or `net.ErrClosed`.