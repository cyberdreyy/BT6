### Title
Unbounded `connAttempts` map growth via repeated `StartHandshake` calls with a replayed signed auth header enables gateway memory-exhaustion DoS - ([File: core/services/gateway/connectionmanager.go])

### Summary
`connectionManager.StartHandshake` inserts a new entry into `m.connAttempts` on every successful call, keyed by a monotonically incrementing counter, with no cap on outstanding attempts per node/signer and no time-based sweep of stale entries. An attacker who can replay one previously valid, signed `AuthHeaderElems` (signature does not bind to a single-use nonce, only to `timestamp`/`gatewayId`/`donId`) can repeatedly hit the WS handshake endpoint within `AuthTimestampToleranceSec` and leave each attempt un-finalized to grow the map without bound.

### Finding Description
`StartHandshake` [1](#0-0)  validates the DON/node/gateway/timestamp fields of the signed header and then calls `newAttempt`, which always creates a brand-new map entry keyed by `nodeAddress_counter` [2](#0-1) . Because the key includes an ever-incrementing `connAttemptCounter`, repeated calls with the exact same signed header never collide/overwrite — each call adds a fresh entry. The only removal paths are `FinalizeHandshake` (on success) and `AbortHandshake` (called by `wsserver.go` only when the websocket upgrade fails, the first read fails, or finalize fails) [3](#0-2) . There is no background goroutine or timestamp-based sweep that prunes `connAttempts` for entries whose peer never completes or aborts the handshake. In `wsserver.go`, after the HTTP upgrade succeeds, `conn.ReadMessage()` blocks waiting for the challenge response with no read deadline set at that point (the deadline is only configured inside `FinalizeHandshake` after success) [4](#0-3) , so an attacker can keep the pending connection open (or perform a real upgrade and simply stop) and repeat this from new connections to keep adding entries. Signature verification and timestamp-window checks only gate whether a call can start a new attempt — they do not gate how many concurrently-pending attempts a single signer/node may have.

### Impact Explanation
Each `connAttempt` is small in memory, but the map is fully attacker-controlled in growth rate (unbounded, no per-node/per-signer cap, no expiry), matching the "memory exhaustion / resource-exhaustion denial of service against the gateway process" impact class. This is a availability/DoS issue on the gateway component, not an authentication or fund-movement bypass.

### Likelihood Explanation
Exploitation requires the attacker to already possess one validly-signed `AuthHeaderElems` for a DON member (obtained via replay/capture, not forgery), which is one of the explicitly allowed attacker capabilities in this audit's scope ("or any address sending signed gateway requests"). Given that header, replay is trivial and repeatable as long as the timestamp stays within `AuthTimestampToleranceSec`, and the attacker only needs to avoid completing/aborting the handshake (e.g., stall after WS upgrade) to keep entries alive indefinitely. No operator/admin access is needed.

### Recommendation
- Track outstanding `connAttempts` per `nodeAddress` (or per signer) with a small fixed cap, rejecting/evicting when exceeded.
- Add a TTL/expiry sweep (based on `connAttempt.timestamp` or an attempt-creation clock) that periodically purges attempts whose challenge has expired past `AuthTimestampToleranceSec`, independent of `FinalizeHandshake`/`AbortHandshake` being called.
- Set a read deadline on the raw `conn` immediately after upgrade in `wsserver.go`, before waiting for the handshake response, and call `AbortHandshake` on timeout.

### Proof of Concept
Go unit test in `core/services/gateway/connectionmanager_test.go`:
1. Construct a `connectionManager` with a single DON/node via `NewConnectionManager`, using a fake `clockwork.Clock`.
2. Build one validly-signed `authHeader` byte slice for a node in `donConfig.Members` (reuse existing test helpers used elsewhere in this file for `StartHandshake`/`FinalizeHandshake` tests).
3. Loop `N=1000` times calling `m.StartHandshake(authHeader)` without ever calling `FinalizeHandshake` or `AbortHandshake` for the returned `attemptId`.
4. Assert `len(m.connAttempts) == N` after the loop, confirming unbounded growth.
5. Advance the fake clock well past `AuthTimestampToleranceSec` and assert `len(m.connAttempts)` is unchanged (no pruning occurs), demonstrating the missing TTL/cap.

### Citations

**File:** core/services/gateway/connectionmanager.go (L196-224)
```go
func (m *connectionManager) StartHandshake(authHeader []byte) (attemptId string, challenge []byte, err error) {
	m.lggr.Debug("StartHandshake")
	authHeaderElems, signer, err := network.UnpackSignedAuthHeader(authHeader)
	if err != nil {
		return "", nil, errors.Join(network.ErrAuthHeaderParse, err)
	}
	nodeAddress := "0x" + hex.EncodeToString(signer)
	donConnMgr, ok := m.dons[authHeaderElems.DonId]
	if !ok {
		return "", nil, network.ErrAuthInvalidDonId
	}
	nodeState, ok := donConnMgr.nodes[nodeAddress]
	if !ok {
		return "", nil, network.ErrAuthInvalidNode
	}
	if authHeaderElems.GatewayId != m.config.AuthGatewayId {
		return "", nil, network.ErrAuthInvalidGateway
	}
	nowTs := uint32(m.clock.Now().Unix())
	ts := authHeaderElems.Timestamp
	if ts < nowTs-m.config.AuthTimestampToleranceSec || nowTs+m.config.AuthTimestampToleranceSec < ts {
		return "", nil, network.ErrAuthInvalidTimestamp
	}
	attemptId, challenge, err = m.newAttempt(nodeState, nodeAddress, ts)
	if err != nil {
		return "", nil, err
	}
	return attemptId, challenge, nil
}
```

**File:** core/services/gateway/connectionmanager.go (L226-239)
```go
func (m *connectionManager) newAttempt(nodeSt *nodeState, nodeAddress string, timestamp uint32) (string, []byte, error) {
	challengeBytes := make([]byte, m.config.AuthChallengeLen)
	_, err := rand.Read(challengeBytes)
	if err != nil {
		return "", nil, err
	}
	challenge := network.ChallengeElems{Timestamp: timestamp, GatewayId: m.config.AuthGatewayId, ChallengeBytes: challengeBytes}
	m.connAttemptsMu.Lock()
	defer m.connAttemptsMu.Unlock()
	m.connAttemptCounter++
	newId := fmt.Sprintf("%s_%d", nodeAddress, m.connAttemptCounter)
	m.connAttempts[newId] = &connAttempt{nodeState: nodeSt, nodeAddress: nodeAddress, challenge: challenge, timestamp: timestamp}
	return newId, network.PackChallenge(&challenge), nil
}
```

**File:** core/services/gateway/connectionmanager.go (L241-298)
```go
func (m *connectionManager) FinalizeHandshake(attemptId string, response []byte, conn *websocket.Conn) error {
	m.lggr.Debugw("FinalizeHandshake", "attemptId", attemptId)
	m.connAttemptsMu.Lock()
	attempt, ok := m.connAttempts[attemptId]
	delete(m.connAttempts, attemptId)
	m.connAttemptsMu.Unlock()
	if !ok {
		return network.ErrChallengeAttemptNotFound
	}
	signer, err := common.ExtractSigner(response, network.PackChallenge(&attempt.challenge))
	if err != nil || attempt.nodeAddress != "0x"+hex.EncodeToString(signer) {
		return network.ErrChallengeInvalidSignature
	}
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
		conn.SetPongHandler(func(data string) error {
			if pongWait > 0 {
				if err := conn.SetReadDeadline(time.Now().Add(pongWait)); err != nil {
					m.lggr.Warnw("failed to reset read deadline on pong",
						"nodeAddress", attempt.nodeAddress, "err", err)
				}
			}
			m.lggr.Debugw("received keepalive pong from node", "nodeAddress", attempt.nodeAddress)
			m.gMetrics.RecordKeepalivePongsReceived(context.Background(), attempt.nodeAddress, attempt.nodeState.name)
			return nil
		})
	}
	attempt.nodeState.conn.Reset(conn)
	if conn != nil && pongWait > 0 {
		// Send an immediate ping so the first pong arrives quickly (within
		// milliseconds on a healthy connection) rather than waiting up to one
		// full heartbeat interval for the keepalive ticker to fire.
		ctx := context.Background()
		if err := attempt.nodeState.conn.Write(ctx, websocket.PingMessage, []byte{}); err != nil {
			m.lggr.Debugw("unable to send post-handshake ping to node",
				"nodeAddress", attempt.nodeAddress, "name", attempt.nodeState.name, "err", err)
		}
	}
	m.lggr.Infof("node %s connected", attempt.nodeAddress)
	m.gMetrics.RecordNodeConnectedEvent(context.Background(), attempt.nodeAddress, attempt.nodeState.name)
	return nil
}

func (m *connectionManager) AbortHandshake(attemptId string) {
	m.lggr.Debugw("AbortHandshake", "attemptId", attemptId)
	m.connAttemptsMu.Lock()
	defer m.connAttemptsMu.Unlock()
	delete(m.connAttempts, attemptId)
}
```

**File:** core/services/gateway/network/wsserver.go (L100-154)
```go
func (s *webSocketServer) handleRequest(w http.ResponseWriter, r *http.Request) {
	authHeader := r.Header.Get(WsServerHandshakeAuthHeaderName)
	if len(authHeader) > HandshakeEncodedAuthHeaderMaxLen {
		s.lggr.Debugw("received auth header is too large", "len", len(authHeader))
		w.WriteHeader(http.StatusBadRequest)
		return
	}
	authBytes, err := base64.StdEncoding.DecodeString(authHeader)
	if err != nil {
		s.lggr.Debugw("received auth header can't be base64-decoded", "err", err)
		w.WriteHeader(http.StatusBadRequest)
		return
	}
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
}
```
