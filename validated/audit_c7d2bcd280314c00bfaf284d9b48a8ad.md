### Title
Non-constant-time session token comparison in `withAuthorization` - ([File: session/session.go])

### Summary
`Session.withAuthorization` compares the session `Token` to the client-supplied `Authorization` header using Go's native string inequality operator (`s.Token != r.Header.Get("Authorization")`), which is a byte-by-byte, early-exit comparison rather than a constant-time comparison. This is a real code-level weakness, but exploiting it to recover a 32-byte (256-bit) random token over the network is not practically feasible given the entropy involved and network timing noise, and it does not by itself defeat the additional secrecy of the per-session `Endpoint` path (also a 32-byte random UUID) that an attacker would need to know first.

### Finding Description
`generateEndpoint`/`generateToken` derive both the session URL path and the `Authorization` token from `helpers.GenerateRandomUUID(32)`, i.e., 32 random bytes hex-encoded to 64 characters (256 bits of entropy each) [1](#0-0) . The `withAuthorization` middleware then performs `if s.Token != r.Header.Get("Authorization") { ... }`, which in Go compiles to a length check followed by a byte-wise comparison that returns as soon as a mismatch is found [2](#0-1) . There is no use of `crypto/subtle.ConstantTimeCompare` anywhere in the codebase, confirming this pattern is not intentionally hardened elsewhere either.

However, to reach `withAuthorization` for a target build's session at all, the attacker must already know that build's `Endpoint` (`"/session/" + sessionUUID`), which is an independent, unrelated 256-bit random value not derivable from the token comparison timing side channel [3](#0-2) . The scoped attack assumes the attacker can already reach "a running job's session endpoint," but for a job the attacker does not own, this endpoint path is not disclosed to them by any code path found in the repo — it is only returned to the owning job/backend via the session server plumbing (`session/server.go`, `common/build.go`). Without the correct endpoint, requests hit `http.ServeMux`'s own routing (404) before `withAuthorization` is ever invoked, so there is no exploitable timing oracle to attack in the first place for an unrelated job.

Even granting endpoint knowledge, extracting a 64-character token via network timing requires distinguishing sub-microsecond CPU-level differences in a byte comparison against millisecond-scale HTTP/TLS/websocket round-trip jitter, across a network-adjacent link. This is a well-known class of theoretical vulnerability, but the signal-to-noise ratio for a short in-memory string compare via HTTP is far weaker than classical timing attacks (e.g. padding oracles with cryptographic operations), making practical, repeatable exploitation not demonstrated and highly improbable to yield a usable oracle over a real network within any reasonable number of trials.

### Impact Explanation
If it were exploitable, the impact would be cross-job session/terminal hijack — an attacker could impersonate the owning job's client and attach to `/exec` (interactive terminal) or `/proxy/` (service proxy) endpoints of another running job. However, this impact requires both (a) knowledge of the target's random 256-bit `Endpoint` path (not available through any code path we can find) and (b) a practically exploitable timing oracle against the token comparison, which is not demonstrated feasible given the entropy and comparison characteristics involved.

### Likelihood Explanation
Low/theoretical. The precondition "attacker can reach the session server for a build they do not own" does not by itself grant knowledge of that build's unique, unguessable `Endpoint` path, which is generated independently with the same 256-bit entropy as the token. Absent that, there is no request to send that would ever invoke `withAuthorization` for a target job. Even with endpoint knowledge, recovering a 64-char token via network-observable timing differences in a single string comparison is not a well-established, repeatable attack against this specific code shape, unlike documented timing attacks on cryptographic primitives.

### Recommendation
As defense-in-depth, replace the token comparison with a constant-time comparison, e.g.:
```go
import "crypto/subtle"
...
if subtle.ConstantTimeCompare([]byte(s.Token), []byte(r.Header.Get("Authorization"))) != 1 {
```
This removes the theoretical timing side channel at negligible cost, even though the practical exploitability given the endpoint-secrecy precondition is very low.

### Proof of Concept
Not applicable as a demonstrable end-to-end exploit: no PoC can be constructed that shows an attacker recovering another job's token via timing without first assuming knowledge of that job's independently-random `Endpoint`, which is not disclosed by any code path found. A statistical timing-fuzz test could be written to measure `withAuthorization`'s comparison latency for correct-prefix vs. random tokens in-process (not over network) to characterize the compare-time leak in isolation, but this would only validate the code-quality issue, not the scoped cross-job hijack impact claimed in the question.

### Citations

**File:** session/session.go (L46-71)
```go
func NewSession(logger *logrus.Entry) (*Session, error) {
	endpoint, token, err := generateEndpoint()
	if err != nil {
		return nil, err
	}

	if logger == nil {
		logger = logrus.NewEntry(logrus.StandardLogger())
	}

	logger = logger.WithField("uri", endpoint)

	sess := &Session{
		Endpoint:      endpoint,
		Token:         token,
		DisconnectCh:  make(chan error),
		TimeoutCh:     make(chan error),
		terminalSetCh: make(chan struct{}),

		log: logger,
	}

	sess.setMux()

	return sess, nil
}
```

**File:** session/session.go (L73-94)
```go
func generateEndpoint() (string, string, error) {
	sessionUUID, err := helpers.GenerateRandomUUID(32)
	if err != nil {
		return "", "", err
	}

	token, err := generateToken()
	if err != nil {
		return "", "", err
	}

	return "/session/" + sessionUUID, token, nil
}

func generateToken() (string, error) {
	token, err := helpers.GenerateRandomUUID(32)
	if err != nil {
		return "", err
	}

	return token, nil
}
```

**File:** session/session.go (L96-109)
```go
func (s *Session) withAuthorization(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		logger := s.log.WithField("uri", r.RequestURI)
		logger.Debug("Endpoint session request")

		if s.Token != r.Header.Get("Authorization") {
			logger.Error("Authorization header is not valid")
			http.Error(w, http.StatusText(http.StatusUnauthorized), http.StatusUnauthorized)
			return
		}

		next.ServeHTTP(w, r)
	})
}
```
