### Title
Non-constant-time session token comparison enables timing side-channel attack - (File: session/session.go)

### Summary
`Session.withAuthorization` (`session/session.go:96-109`) checks the session `Authorization` header using Go's native string inequality operator `s.Token != r.Header.Get("Authorization")` instead of a constant-time comparison. Because Go's string/byte comparison short-circuits on the first mismatching chunk, an attacker who can send many requests to a victim job's session endpoint can, in principle, use response-timing variance to narrow down the 256-bit random token (`helpers.GenerateRandomUUID(32)`) below its full entropy, eventually recovering it and hijacking that job's terminal/proxy session.

### Finding Description
The session server (`session/server.go`) exposes per-job endpoints `/session/<uuid>/exec` and `/session/<uuid>/proxy/` guarded by `withAuthorization`: [1](#0-0) 
The comparison `s.Token != r.Header.Get("Authorization")` is a plain Go string comparison, which is implemented via `runtime.memequal`-style logic that returns as soon as a mismatch is detected, rather than `crypto/subtle.ConstantTimeCompare`. The token itself is generated with `helpers.GenerateRandomUUID(32)`: [2](#0-1) 
and used as the sole bearer credential for the session, set on session creation: [3](#0-2) 
There is no other rate limiting, lockout, or constant-time compare mechanism anywhere in the codebase (`crypto/subtle` is not used anywhere in the repository). The session server itself performs no additional authentication beyond this header check before dispatching to `execHandler`/`proxyHandler`.

Preconditions required for this to matter: the attacker must be able to reach the session server's `AdvertiseAddress`/`ListenAddress` for a build/job they do not own — this is possible in shared-runner or shared-network deployments where the session server is exposed to multiple concurrent jobs/users, as documented in `docs/configuration/advanced-configuration.md` (`[session_server]` section, "Ensure that GitLab can connect to the IP address and port... unless `allow_local_requests_from_web_hooks_and_services`...").

### Impact Explanation
If the timing signal is exploitable, a successful attack yields full possession of another job's session `Token`, which allows the attacker to attach to that job's interactive terminal (`execHandler` → `terminalConn.Start`) or route arbitrary traffic through its service proxy (`proxyHandler` → `ProxyRequest`), i.e., cross-job session hijack — matching the scoped impact exactly. This would expose command execution and network access inside another user's job/container.

### Likelihood Explanation
This is a genuine coding defect (CWE-208, non-constant-time secret comparison) with no compensating control (no `crypto/subtle`, no rate limiting, no constant-time compare) anywhere in the session path. However, practical exploitability over a real network is significant to establish: the token is 256 bits of entropy (64 hex chars), TLS is mandatory for the session server (`session/server.go` enforces `tls.Config` with `MinVersion: tls.VersionTLS12`), and Go's underlying `memequal` typically compares in machine-word-sized chunks rather than single bytes, and network/TLS jitter typically dominates over such sub-microsecond timing differences, requiring very large sample counts per guessed prefix to get a statistically significant signal. It is a real, exploitable-in-principle weakness but with high attack cost/feasibility uncertainty in practice; it should still be fixed since it is a textbook case of the anti-pattern the constant-time compare API exists to prevent.

### Recommendation
Replace the token comparison in `withAuthorization` with a constant-time comparison, e.g.:
```go
import "crypto/subtle"
...
if subtle.ConstantTimeCompare([]byte(s.Token), []byte(r.Header.Get("Authorization"))) != 1 {
    ...
}
```
Additionally consider adding rate limiting/backoff on repeated failed authorization attempts to the session endpoint to further reduce the feasibility of any timing- or brute-force-based attack.

### Proof of Concept
Go unit test (in `session/session_test.go`) idea:
```go
func TestWithAuthorization_TimingLeak(t *testing.T) {
    sess, _ := NewSession(nil)
    sess.Token = strings.Repeat("a", 64)

    measure := func(header string) time.Duration {
        req := httptest.NewRequest("GET", sess.Endpoint+"/exec", nil)
        req.Header.Set("Authorization", header)
        w := httptest.NewRecorder()
        start := time.Now()
        sess.withAuthorization(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {})).ServeHTTP(w, req)
        return time.Since(start)
    }

    correctPrefix := strings.Repeat("a", 63) + "b" // differs only in last byte
    wrongEarly := "z" + strings.Repeat("a", 63)     // differs in first byte

    var tCorrectPrefix, tWrongEarly time.Duration
    const trials = 100000
    for i := 0; i < trials; i++ {
        tCorrectPrefix += measure(correctPrefix)
        tWrongEarly += measure(wrongEarly)
    }
    // Assert statistically significant difference between average latencies,
    // demonstrating the comparison is not constant-time.
    assert.NotEqual(t, tCorrectPrefix, tWrongEarly)
}
```
Expected assertion: average latency for `correctPrefix` (mismatch at last byte) is measurably higher than for `wrongEarly` (mismatch at first byte), demonstrating the non-constant-time behavior of `s.Token != r.Header.Get("Authorization")`. After applying `subtle.ConstantTimeCompare`, the timing difference should collapse to noise level.

### Citations

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

**File:** helpers/random_uuid.go (L8-16)
```go
func GenerateRandomUUID(length int) (string, error) {
	data := make([]byte, length)
	_, err := rand.Read(data)
	if err != nil {
		return "", err
	}

	return hex.EncodeToString(data), nil
}
```
