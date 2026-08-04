This is a genuine code-level issue: the token comparison at `session/session.go:101` uses Go's built-in `!=` string comparison rather than a constant-time comparison function (e.g. `crypto/subtle.ConstantTimeCompare`), which is the correct standard pattern for comparing security tokens against attacker-supplied input.

### Title
Non-constant-time authorization token comparison enables timing side-channel - (File: session/session.go)

### Summary
`Session.withAuthorization` at `session/session.go:96-109` compares the session token to the client-supplied `Authorization` header using the plain `!=` operator instead of a constant-time comparison. This is a textbook timing side-channel weakness in an authentication check that gates `execHandler` and `proxyHandler`.

### Finding Description
The check `if s.Token != r.Header.Get("Authorization")` at `session/session.go:101` relies on Go's default string equality, which short-circuits and returns on the first byte mismatch. An attacker who knows only the `/session/<uuid>` endpoint path (e.g., leaked via CI trace/logs, or predictable enumeration) but not the `Token` value could, in principle, use per-character timing differences across repeated requests to narrow down the correct token before reaching `execHandler` (`session/session.go:146`) or `proxyHandler` (`session/session.go:120`). [1](#0-0) 

### Impact Explanation
If exploitable, an attacker could recover the session `Token` (generated in `generateToken()` at `session/session.go:87-94`, a 32-byte random UUID string) and gain unauthorized access to the interactive terminal (`execHandler`) or service proxy (`proxyHandler`) of another job's session — i.e., session/job hijack and cross-job terminal/proxy access, matching the scoped impact. [2](#0-1) 

### Likelihood Explanation
Practical exploitability is low. The token is a 32-byte random value (`helpers.GenerateRandomUUID(32)`), and recovering it via network timing analysis of a Go string comparison requires resolving sub-microsecond/nanosecond-scale timing differences over HTTP across a WAN or even LAN, which is generally infeasible given normal network jitter, TLS overhead, and Go runtime scheduling noise, especially against a large random keyspace requiring many sequential per-byte guesses. There is no rate-limiting or observed mitigation, but the attack is a known theoretical class rather than a demonstrated practical exploit in this context; no PoC data suggests it has ever been practically demonstrated against Go's default string comparison over a network in this codebase. [3](#0-2) 

### Recommendation
Replace the direct string comparison with a constant-time comparison, e.g.:
```go
import "crypto/subtle"

if subtle.ConstantTimeCompare([]byte(s.Token), []byte(r.Header.Get("Authorization"))) != 1 {
    ...
}
```
This removes the timing side channel regardless of its practical exploitability, following standard defense-in-depth practice for token comparisons.

### Proof of Concept
Go benchmark/timing-differential test:
1. Generate a session with a known `Token` value of fixed length.
2. For N trials, send requests to `withAuthorization`-wrapped handler with headers that share increasing correct-prefix lengths (0, 1, 2, ... len(Token)-1 correct bytes) vs. fully incorrect tokens.
3. Measure `time.Since` around the call to `s.withAuthorization(next).ServeHTTP(...)` for each case, average over many trials to reduce noise.
4. Assert whether average latency correlates with matching-prefix length (statistically significant slope) — expected: on typical hardware/network with realistic HTTP overhead, no statistically significant, reliably exploitable correlation would be measurable remotely, though it may be locally measurable in a microbenchmark isolated from I/O, confirming the code-level weakness without demonstrating a practical remote exploit.

Given that the comparison bug is real and reachable at the exact cited location, but the network-based token-recovery impact described in the question is not practically demonstrated (long random token, WAN/LAN noise dominates any nanosecond-scale timing difference, no PoC exists showing a working recovery), this is reported as a valid low-severity hardening finding, not a proven high-impact session hijack. [1](#0-0)

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
