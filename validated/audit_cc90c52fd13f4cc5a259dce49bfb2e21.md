### Title
No read deadline on post-upgrade handshake-response read allows unauthenticated attacker to hold WS connections/goroutines open indefinitely - ([File: core/services/gateway/network/wsserver.go])

### Summary
In `webSocketServer.handleRequest`, after a successful `StartHandshake` and websocket `Upgrade`, the code calls `conn.ReadMessage()` (line 140) to receive the `ChallengeResponse` without ever calling `conn.SetReadDeadline`. An attacker who possesses (or replays, within the timestamp tolerance) one valid auth header can complete the HTTP upgrade and then simply never send a binary frame, blocking the handler goroutine and the socket forever, while an entry remains in `connectionManager.connAttempts`.

### Finding Description
`s.acceptor.StartHandshake(authBytes)` validates the signed auth header and creates a `connAttempt` entry keyed by `attemptId` in `connectionManager.connAttempts` (`core/services/gateway/connectionmanager.go` `newAttempt`, lines 226-239). `webSocketServer.handleRequest` then upgrades the HTTP connection to a websocket (`s.upgrader.Upgrade`, line 123) and immediately calls `conn.ReadMessage()` (line 140) to obtain the `ChallengeResponse`. No deadline is set on the connection at this point: [1](#0-0) 

A read deadline for the connection is only established later, inside `connectionManager.FinalizeHandshake`, which is invoked *after* `ReadMessage()` already returns successfully: [2](#0-1) 

Because `FinalizeHandshake` (and its `SetReadDeadline` call) is never reached until a message is actually read, an attacker who upgrades the connection and then never writes a `BinaryMessage` causes `conn.ReadMessage()` to block indefinitely (bounded only by TCP-level failure, not any application timeout). The `websocket.Upgrader.HandshakeTimeout` (`HandshakeTimeoutMillis`) only bounds the HTTP-level `Upgrade()` call itself, not this subsequent `ReadMessage()`. Likewise, `http.Server.ReadTimeout`/`ReadHeaderTimeout` are only reliably enforced for the original HTTP request/response cycle; once the connection is hijacked via websocket upgrade, the server relinquishes read/write deadline management to the application, and no library code re-applies a deadline before this specific read. As a result:
- The per-attempt entry in `m.connAttempts` (`connectionmanager.go` line 237) stays allocated until the client disconnects or the process restarts, since `AbortHandshake`/`FinalizeHandshake` are only called after `ReadMessage()` returns (error or success) - line 141-146 and 148-153 of `wsserver.go`.
- The goroutine spawned by `http.Server` to service the request, and the underlying TCP socket/file descriptor, remain occupied indefinitely.

The auth-header check does not stop this: it only validates that the header is a legitimately signed, non-expired header for a real node address/DON/gateway ID - it says nothing about liveness of the subsequent websocket traffic, so a single valid header replay (reusable within `AuthTimestampToleranceSec`) is sufficient to open and stall a connection.

### Impact Explanation
This is a gateway-side resource-exhaustion / denial-of-service issue: repeated stalled upgrades consume `connAttempts` map entries, goroutines, and listener sockets, degrading or blocking the ability of legitimate DON node/oracle connections to complete handshakes and authenticate to the gateway. This maps to the Chainlink bounty "Denial of Service" impact class (asset/service unavailability due to unbounded resource consumption reachable by an actor holding only a valid (replayable) auth header, not by an operator/admin).

### Likelihood Explanation
Precondition is possession of a single valid signed auth header, reusable for the duration of `AuthTimestampToleranceSec`. The attack requires no privileged access - any client capable of reaching the gateway's `wsServer` port and holding a header can complete `StartHandshake` + `Upgrade`, then simply refrain from writing. This is trivially repeatable and can be parallelized across many TCP connections (each with a distinct or same replayed header, generating a fresh `attemptId`/map entry each time via `connAttemptCounter`), making the exploit highly feasible.

### Recommendation
Set an explicit read (and ideally write) deadline on the upgraded connection immediately after `Upgrade()` succeeds and before calling `conn.ReadMessage()` in `handleRequest`, based on `HandshakeTimeoutMillis` (or a dedicated response-timeout config). On timeout, call `s.acceptor.AbortHandshake(attemptId)` and close the connection, exactly as already done for the `err != nil` case at line 141-146. This ensures the connection is force-closed within a bounded window if the client never returns a `ChallengeResponse`.

### Proof of Concept
Go integration test plan (extends `core/services/gateway/network/wsserver_test.go`):
1. Start a `webSocketServer` via `startNewWSServer` with a short `HandshakeTimeoutMillis`/read-deadline configuration (e.g., 500ms).
2. Configure the mocked `acceptor.StartHandshake` to succeed and return a valid challenge (no `AbortHandshake` expectation registered initially).
3. Manually perform the HTTP `Upgrade` via `gorilla/websocket.Dialer.Dial` (not the higher-level `network.WebSocketClient`, to avoid it auto-sending `ChallengeResponse`), then simply sleep without writing any binary frame.
4. Assert that within `HandshakeTimeoutMillis` (plus small margin), `acceptor.AbortHandshake` is invoked (verify via mock expectation/`waitCh`) and the server-side connection is closed - proving the server enforces a bound.
5. Currently (before fix), this test will fail/timeout because no deadline is ever applied, demonstrating that `AbortHandshake` is never called and the connection stays open indefinitely, confirming the vulnerability.

### Citations

**File:** core/services/gateway/network/wsserver.go (L133-146)
```go
	maxRequestBytes, err := s.config.MaxRequestBytesLimiter.Limit(r.Context())
	if err != nil {
		s.lggr.Errorw("failed to get request size limit", "err", err)
		w.WriteHeader(http.StatusInternalServerError)
		return
	}
	conn.SetReadLimit(int64(maxRequestBytes))
	msgType, response, err := conn.ReadMessage()
	if err != nil || msgType != websocket.BinaryMessage {
		s.lggr.Errorw("invalid handshake message", "msgType", msgType, "err", err, "remoteAddr", conn.RemoteAddr())
		conn.Close()
		s.acceptor.AbortHandshake(attemptId)
		return
	}
```

**File:** core/services/gateway/connectionmanager.go (L254-264)
```go
	// Set a read deadline so that half-open connections are detected and closed.
	// The deadline is reset every time a pong is received. If PongTimeoutSec is
	// 0, deadline enforcement is disabled.
	pongWait := time.Duration(m.config.PongTimeoutSec) * time.Second
	if conn != nil {
		if pongWait > 0 {
			if err := conn.SetReadDeadline(time.Now().Add(pongWait)); err != nil {
				m.lggr.Warnw("failed to set initial read deadline, connection may be unusable",
					"nodeAddress", attempt.nodeAddress, "err", err)
			}
		}
```
