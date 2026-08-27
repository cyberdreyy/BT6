### Title
Global WebAuthn login challenge store keyed only by email allows concurrent-request race that overwrites another session's in-flight MFA challenge - ([File: core/sessions/webauthn.go])

### Summary
`WebAuthnSessionStore` stores in-flight WebAuthn login challenges in a single process-wide map keyed solely by `user.Email + "-authentication"`, with no binding to the originating HTTP request or cookie. Two concurrent `POST /sessions` requests presenting the correct password for the same account (e.g. one from the legitimate user, one from an attacker performing credential stuffing/phishing who has the password but not the WebAuthn hardware key) will overwrite each other's stored `webauthn.SessionData`, causing the earlier request's `FinishWebAuthnLogin` to validate against the wrong challenge and fail.

### Finding Description
`CreateSession` in `core/sessions/localauth/orm.go` verifies the user's password via `utils.CheckPasswordHash` before reaching the MFA branch [1](#0-0)  . When `sr.WebAuthnData == ""`, it calls `sessions.BeginWebAuthnLogin(user, uwas, sr)` which stores the generated `webauthn.SessionData` under the key `user.Email + "-authentication"` via `SaveWebauthnSession` [2](#0-1)  . This key has no per-request or per-cookie component, and `WebAuthnSessionStore.put`/`take` operate on a single shared `map[string]string` protected only by a mutex for concurrency-safety, not for isolation [3](#0-2)  . When the user later completes the flow with `sr.WebAuthnData` populated, `FinishWebAuthnLogin` calls `GetWebauthnSession` on the same key, which "takes" (reads-and-deletes) whatever value is currently stored — not necessarily the value that corresponds to the specific `BeginWebAuthnLogin` call that produced the challenge the client actually signed [4](#0-3)  . If two logins for the same email race (both having passed the password check), the second `BeginWebAuthnLogin` overwrites the first's challenge; when the first client submits its WebAuthn assertion, `ValidateLogin` will be run against the second challenge, and will fail (signature/challenge mismatch), causing an unexpected MFA failure for the first (potentially legitimate) login attempt.

### Impact Explanation
This is a scoped availability/isolation issue: an attacker who possesses a victim's password (but not their hardware MFA key) can race the victim's legitimate login attempts to desynchronize/deny the victim's MFA challenge-response flow. It does not bypass `ValidateLogin`'s signature check and cannot be used to fully authenticate as the victim, matching the "MFA login desync/DoS" impact class described in the question rather than full authentication bypass.

### Likelihood Explanation
Exploitation requires the attacker to already know/guess the victim's correct password (a precondition beyond mere email knowledge, since `CreateSession` rejects mismatched passwords before reaching the WebAuthn branch [1](#0-0) ). Given that additional precondition, the race itself is trivial and fully repeatable: any client that can fire two concurrent unauthenticated `POST /sessions` requests with the correct email/password and no `WebAuthnData` can trigger the overwrite, since the store has no per-request isolation.

### Recommendation
Bind the WebAuthn challenge to a per-request/session identifier (e.g., a random nonce set in a short-lived cookie or returned alongside the challenge and required on `FinishWebAuthnLogin`) instead of keying solely by email, or use per-attempt keys (e.g., `email + "-authentication-" + requestID`) with a explicit correlation token returned to the client that must be echoed back in the follow-up request.

### Proof of Concept
Go unit test plan (in `core/sessions` package):
1. Construct two `User`/`WebAuthn` credential fixtures for the same `Email`.
2. Call `sessions.BeginWebAuthnLogin(user, uwas, sr1)` capturing `options1`, then immediately call `sessions.BeginWebAuthnLogin(user, uwas, sr2)` capturing `options2` (simulating two concurrent requests, e.g. via goroutines + `sync.WaitGroup`).
3. Assert that `store.inProgressRegistrations[email+"-authentication"]` equals the session data corresponding to `options2` only (the first is lost) — inspect via an exported test helper or by calling `GetWebauthnSession` and comparing challenge bytes to `options1.Response.Challenge` vs `options2.Response.Challenge`.
4. Simulate finishing the first flow: craft an assertion response signed against `options1`'s challenge and call `sessions.FinishWebAuthnLogin(user, uwas, sr1WithData)`; assert it returns an error (challenge mismatch), demonstrating the first flow was clobbered by the second, unrelated request.

### Citations

**File:** core/sessions/localauth/orm.go (L159-162)
```go
	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		o.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid password")
	}
```

**File:** core/sessions/webauthn.go (L119-128)
```go
	options, sessionData, err := webAuthn.BeginLogin(waUser)
	if err != nil {
		return nil, err
	}

	userLoginIndexKey := user.Email + "-authentication"
	err = sr.SessionStore.SaveWebauthnSession(userLoginIndexKey, sessionData)
	if err != nil {
		return nil, err
	}
```

**File:** core/sessions/webauthn.go (L149-161)
```go
	userLoginIndexKey := user.Email + "-authentication"
	sessionData, err := sr.SessionStore.GetWebauthnSession(userLoginIndexKey)
	if err != nil {
		return err
	}

	waUser, err := duoWebAuthUserFromUser(user, uwas)
	if err != nil {
		return err
	}

	_, err = webAuthn.ValidateLogin(waUser, sessionData, credential)
	return err
```

**File:** core/sessions/webauthn.go (L254-269)
```go
func (store *WebAuthnSessionStore) put(key, val string) {
	store.mu.Lock()
	defer store.mu.Unlock()
	store.inProgressRegistrations[key] = val
}

// take returns the val for key, as well as removing it.
func (store *WebAuthnSessionStore) take(key string) (val string, ok bool) {
	store.mu.Lock()
	defer store.mu.Unlock()
	val, ok = store.inProgressRegistrations[key]
	if ok {
		delete(store.inProgressRegistrations, key)
	}
	return
}
```
