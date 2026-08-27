### Title
Global single-slot WebAuthn challenge store allows concurrent login requests to overwrite/invalidate a pending MFA challenge, causing denial of MFA completion - (File: `core/sessions/webauthn.go`)

### Summary
`WebAuthnSessionStore` is instantiated once per node (`web.NewSessionsController`) and stores in-progress WebAuthn login challenges in a single map keyed only by `user.Email + "-authentication"`, with no per-attempt nonce or session-request binding. Because `BeginWebAuthnLogin` unconditionally overwrites this key via `SaveWebauthnSession`, any second concurrent (or subsequent) login POST for the same email silently replaces the previously issued challenge, invalidating it before the legitimate device can respond.

### Finding Description
`SessionsController.Create` (`core/web/sessions_controller.go:29-68`) handles unauthenticated `POST /sessions` requests. When the account has WebAuthn tokens registered, it passes a shared `*clsessions.WebAuthnSessionStore` (`sc.sessions`, created once in `NewSessionsController`) into `sr.SessionStore` and calls `AuthenticationProvider().CreateSession` [1](#0-0) , which in turn calls `sessions.BeginWebAuthnLogin(user, uwas, sr)` whenever `sr.WebAuthnData == ""` [2](#0-1) .

`BeginWebAuthnLogin` computes `userLoginIndexKey := user.Email + "-authentication"` and stores the freshly generated `sessionData` under that key via `sr.SessionStore.SaveWebauthnSession(userLoginIndexKey, sessionData)` [3](#0-2) . The underlying `put` simply does `store.inProgressRegistrations[key] = val` under a mutex — last write wins, no isolation between concurrent requests for the same key [4](#0-3) . `GetWebauthnSession`/`take` then removes and returns whatever value currently occupies that key [5](#0-4) .

Because the key contains no per-request/session identifier (e.g. no random login-attempt ID, no ticket returned to the client to bind the follow-up assertion to a specific challenge), any second `BeginWebAuthnLogin` call for the same `user.Email` — triggered by a second `POST /sessions` with `email`+`password` and empty `WebAuthnData` — overwrites the first challenge in the shared store. If the legitimate user's device is mid-flight (has received the first challenge and is about to submit the assertion), their subsequent `FinishWebAuthnLogin` call will fetch the second (attacker-triggered) challenge, and `webAuthn.ValidateLogin` will reject the assertion because it doesn't match the challenge the authenticator actually signed [6](#0-5) .

The precondition is that the attacker already knows the account's correct email and password (this is explicit in the scenario) — this is not required to know the WebAuthn/physical key, only the password. Nothing in the current code (rate limiter is applied per unauth group but does not prevent valid-credential requests from proceeding, and there's no per-login-attempt token binding) prevents this griefing.

### Impact Explanation
This is a denial-of-service against the second authentication factor (MFA) for a specific account: an attacker who already possesses (e.g., phished, reused, or otherwise obtained) the victim's password — but not their physical WebAuthn authenticator — can repeatedly call `POST /sessions` with the correct email/password to keep re-issuing (and thereby invalidating) challenges, preventing the legitimate owner from ever completing a WebAuthn-gated login. This does not grant the attacker account access (they still cannot produce a valid assertion without the physical key), so it is not an authentication bypass; it maps to a **denial-of-service / availability** impact class, not privilege escalation.

### Likelihood Explanation
Requires the attacker to already know the victim's correct password (not merely the email) — a real but nontrivial precondition. Given that, exploitation is straightforward and fully repeatable: no special timing precision is needed beyond winning the race for the same map key before the legitimate user's device round-trip completes, and the attacker can retry indefinitely since valid-credential login-challenge requests are not specially throttled beyond the general unauthenticated rate limiter (`core/web/router.go:210-215`).

### Recommendation
Bind each WebAuthn login challenge to the specific login attempt rather than solely to the user's email — e.g., generate a per-attempt random ticket/nonce, return it to the client (or store it in the client's session cookie) and require it to be echoed back with `WebAuthnData` in `FinishWebAuthnLogin`, storing/looking up sessionData keyed by that nonce instead of (or in addition to) the email. Alternatively, only allow a single outstanding challenge per email and reject/queue new `BeginWebAuthnLogin` calls while one is pending, with a short TTL to avoid permanent lockout.

### Proof of Concept
Go unit test in `core/sessions` (or table test extending `TestORM_WebAuthn`):
1. Create a `WebAuthnSessionStore`.
2. Goroutine A: call `BeginWebAuthnLogin(user, uwas, sr)` for `user.Email`, capture returned `protocol.CredentialAssertion` (`challengeA`).
3. Goroutine B: immediately call `BeginWebAuthnLogin` again for the same `user.Email`, capture `challengeB`.
4. Assert `challengeA.Response.Challenge != challengeB.Response.Challenge`.
5. Simulate the legitimate device finishing with an assertion built against `challengeA` (or directly call `sr.SessionStore.GetWebauthnSession(key)`), and assert the returned `sessionData.Challenge` equals `challengeB`'s challenge, not `challengeA`'s — proving `challengeA` is unrecoverable/lost, i.e., `FinishWebAuthnLogin` using the device response tied to `challengeA` will fail `webAuthn.ValidateLogin` because the stored session no longer matches.

### Citations

**File:** core/web/sessions_controller.go (L42-56)
```go
	userWebAuthnTokens, err := sc.App.AuthenticationProvider().GetUserWebAuthn(ctx, sr.Email)
	if err != nil {
		sc.App.GetLogger().Errorf("Error loading user WebAuthn data: %s", err)
		jsonAPIError(c, http.StatusInternalServerError, errors.New("internal Server Error"))
		return
	}

	// If the user has registered MFA tokens, then populate our session store and context
	// required for successful WebAuthn authentication
	if len(userWebAuthnTokens) > 0 {
		sr.SessionStore = sc.sessions
		sr.WebAuthnConfig = sc.App.GetWebAuthnConfiguration()
	}

	sid, err := sc.App.AuthenticationProvider().CreateSession(ctx, sr)
```

**File:** core/sessions/localauth/orm.go (L184-199)
```go
	if sr.WebAuthnData == "" {
		lggr.Warnf("Attempted login to MFA user. Generating challenge for user.")
		options, webauthnError := sessions.BeginWebAuthnLogin(user, uwas, sr)
		if webauthnError != nil {
			lggr.Errorf("Could not begin WebAuthn verification: %v", webauthnError)
			return "", pkgerrors.New("MFA Error")
		}

		j, jsonError := json.Marshal(options)
		if jsonError != nil {
			lggr.Errorf("Could not serialize WebAuthn challenge: %v", jsonError)
			return "", pkgerrors.New("MFA Error")
		}

		return "", pkgerrors.New(string(j))
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

**File:** core/sessions/webauthn.go (L133-161)
```go
func FinishWebAuthnLogin(user User, uwas []WebAuthn, sr SessionRequest) error {
	webAuthn, err := webauthn.New(&webauthn.Config{
		RPDisplayName: "Chainlink Operator",       // Display Name
		RPID:          sr.WebAuthnConfig.RPID,     // Generally the domain name
		RPOrigin:      sr.WebAuthnConfig.RPOrigin, // The origin URL for WebAuthn requests
	})

	if err != nil {
		return pkgerrors.Wrapf(err, "failed to create webAuthn structure with RPID: %s and RPOrigin: %s", sr.WebAuthnConfig.RPID, sr.WebAuthnConfig.RPOrigin)
	}

	credential, err := protocol.ParseCredentialRequestResponseBody(strings.NewReader(sr.WebAuthnData))
	if err != nil {
		return err
	}

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

**File:** core/sessions/webauthn.go (L254-258)
```go
func (store *WebAuthnSessionStore) put(key, val string) {
	store.mu.Lock()
	defer store.mu.Unlock()
	store.inProgressRegistrations[key] = val
}
```

**File:** core/sessions/webauthn.go (L260-281)
```go
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

// GetWebauthnSession unmarshals and returns the webauthn session information
// from the session cookie, which is removed.
func (store *WebAuthnSessionStore) GetWebauthnSession(key string) (data webauthn.SessionData, err error) {
	assertion, ok := store.take(key)
	if !ok {
		err = pkgerrors.New("assertion not in challenge store")
		return
	}
	err = json.Unmarshal([]byte(assertion), &data)
	return
}
```
