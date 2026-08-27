### Title
Missing `AbortHandshake` call (and connection cleanup) on `MaxRequestBytesLimiter.Limit` error leaves stale handshake attempt and open connection - (File: core/services/gateway/network/wsserver.go)

### Summary
In `webSocketServer.handleRequest`, after `StartHandshake` succeeds and the WebSocket upgrade completes, if `s.config.MaxRequestBytesLimiter.Limit(r.Context())` returns an error, the handler writes an HTTP 500 and returns without calling `s.acceptor.AbortHandshake(attemptId)` or closing the already-upgraded `conn`. Every other failure branch in the same function (`Upgrade` failure, invalid handshake message, `FinalizeHandshake` failure) explicitly calls `AbortHandshake` and closes the connection, making this branch an inconsistent, unguarded exception.

### Finding Description
The relevant code: [1](#0-0) 

Flow:
1. Attacker sends an auth header; `StartHandshake` succeeds, creating an entry keyed by `attemptId` and returning a challenge [2](#0-1) .
2. The HTTP connection is upgraded to a WebSocket (`s.upgrader.Upgrade`) [3](#0-2) .
3. `s.config.MaxRequestBytesLimiter.Limit(r.Context())` is called to determine the read-size limit for the connection [4](#0-3) . If this returns an error (e.g., limiter is exhausted, backing resource errors, or context issues), the handler writes `http.StatusInternalServerError` and returns — **without** calling `conn.Close()` or `s.acceptor.AbortHandshake(attemptId)`.

Contrast this with the three sibling error branches at lines 124-131, 141-146, and 148-153, which all explicitly close the connection and call `AbortHandshake`. This asymmetry is a clear coding defect: the cleanup call was omitted specifically for the `Limit()` error path.

Consequences:
- The already-upgraded `websocket.Conn` is never closed by the server, leaking a live connection/goroutine resource per triggered error.
- The `attemptId` entry created by `StartHandshake` is never explicitly invalidated via `AbortHandshake`. Whether this entry has a background expiry is implemented in `core/services/gateway/connectionmanager.go` (I was not able to fully verify the expiry/TTL logic for `connAttempts` there before running out of investigation budget), but regardless of any TTL, the omission itself is a defect: it makes cleanup timing depend on an unrelated background sweep rather than immediate, explicit invalidation like all sibling paths, and leaves the underlying WebSocket connection object dangling with no `Close()` call.

### Impact Explanation
This maps to a resource/state-leak class issue: repeated triggering of the limiter-error path leaks WebSocket connection objects (and any goroutines/buffers gorilla/websocket associates with `Upgrade`), which is a node-level Denial-of-Service risk under repeated attacker requests. Regarding authentication soundness, the missing `AbortHandshake` call means the failure path does not immediately invalidate the attempt the way every other failure path does; if there is no independent, sufficiently tight expiry for `connAttempts` entries, an attacker-known/leaked `attemptId` could remain finalize-able outside the intended handshake window. I could not fully confirm the exact expiry mechanics in `connectionmanager.go` in this pass, so the severity of the authentication-soundness angle specifically (as opposed to the confirmed resource-leak/DoS angle) carries that caveat.

### Likelihood Explanation
Any unauthenticated client reaching the gateway WebSocket endpoint can complete `StartHandshake` and the WebSocket `Upgrade` (both are pre-authentication steps by design), then needs only to cause the `MaxRequestBytesLimiter.Limit` call to return an error. This depends on the concrete limiter implementation (e.g., resource-quota exhaustion), which is feasible for a client capable of issuing many concurrent handshake attempts, and is fully repeatable since the code path is deterministic once the limiter is made to error.

### Recommendation
In `core/services/gateway/network/wsserver.go`, make the `MaxRequestBytesLimiter.Limit` error branch consistent with the other three branches:
```go
maxRequestBytes, err := s.config.MaxRequestBytesLimiter.Limit(r.Context())
if err != nil {
    s.lggr.Errorw("failed to get request size limit", "err", err)
    conn.Close()
    s.acceptor.AbortHandshake(attemptId)
    w.WriteHeader(http.StatusInternalServerError) // note: after Upgrade, writing header may be a no-op/error; consider removing or handling accordingly
    return
}
```
Additionally, verify/ensure `connAttempts` entries in `core/services/gateway/connectionmanager.go` have a bounded TTL/expiry independent of explicit `AbortHandshake` calls, as defense in depth.

### Proof of Concept
Go unit/handler-level test plan for `core/services/gateway/network/wsserver_test.go`:
1. Construct a `webSocketServer` with a mocked `ConnectionAcceptor` (from `core/services/gateway/network/mocks/connection_acceptor.go`) whose `StartHandshake` returns a fixed `attemptId` and challenge.
2. Configure `s.config.MaxRequestBytesLimiter` with a fake limiter whose `Limit` returns an error.
3. Issue a real HTTP request with a valid base64 auth header to `handleRequest` via `httptest` with a WebSocket-upgrade-capable client (so `Upgrade` succeeds).
4. Assert:
   - The mock's `AbortHandshake(attemptId)` was called (it currently is not — test should fail against current code, demonstrating the bug).
   - The response is `http.StatusInternalServerError`.
   - The upgraded connection is closed server-side.
5. As a regression guard, add a second assertion that a subsequent call to `FinalizeHandshake(attemptId, ...)` on the real acceptor implementation fails after this path, ensuring the attempt cannot be completed later.

### Citations

**File:** core/services/gateway/network/wsserver.go (L113-153)
```go
	attemptId, challenge, err := s.acceptor.StartHandshake(authBytes)
	if err != nil {
		s.lggr.Debugw("received invalid auth header", "err", err)
		w.WriteHeader(http.StatusUnauthorized)
		return
	}

	challengeStr := base64.StdEncoding.EncodeToString(challenge)
	hdr := make(http.Header)
	hdr.Add(WsServerHandshakeChallengeHeaderName, challengeStr)
	conn, err := s.upgrader.Upgrade(w, r, hdr)
	if err != nil {
		s.lggr.Errorw("failed websocket upgrade", "err", err)
		if conn != nil {
			conn.Close()
		}
		s.acceptor.AbortHandshake(attemptId)
		return
	}

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

	if err = s.acceptor.FinalizeHandshake(attemptId, response, conn); err != nil {
		s.lggr.Errorw("unable to finalize handshake", "err", err)
		conn.Close()
		s.acceptor.AbortHandshake(attemptId)
		return
	}
```
