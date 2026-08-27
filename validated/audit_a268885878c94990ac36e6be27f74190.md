### Title
Login endpoint leaks account existence and MFA status via distinguishable error messages - ([File: core/web/sessions_controller.go], [File: core/sessions/localauth/orm.go])

### Summary
The unauthenticated `POST /sessions` endpoint returns distinct, attacker-visible error strings ("Invalid email", "Invalid password", "MFA Error", or a raw WebAuthn challenge JSON) depending on whether the submitted email exists, whether the password was wrong, and whether MFA is enabled. This allows an unauthenticated attacker to enumerate valid emails and identify which accounts lack MFA, which is a reconnaissance aid for follow-on credential stuffing against those non-MFA accounts.

### Finding Description
`SessionsController.Create` (`core/web/sessions_controller.go:29-68`) first calls `GetUserWebAuthn` for the submitted email and, if that user has registered WebAuthn tokens, sets `sr.SessionStore`/`sr.WebAuthnConfig` before calling `CreateSession`. It then propagates whatever error `CreateSession` returns directly to the HTTP client via `jsonAPIError(c, http.StatusUnauthorized, err)` [1](#0-0) .

`orm.CreateSession` (`core/sessions/localauth/orm.go:144-230`) produces three semantically distinct error strings depending on state:
- `"Invalid email"` when `constantTimeEmailCompare` fails (i.e., no matching user was found, since `FindUser` already succeeded/failed earlier and the lookup uses `lower(email)`) [2](#0-1) .
- `"Invalid password"` when the password hash doesn't match [3](#0-2) .
- `"MFA Error"` or a JSON WebAuthn challenge blob when the user has MFA enabled and no/invalid attestation is supplied [4](#0-3) .

Because these three outcomes map to three distinguishable response bodies, an attacker can:
1. Submit `{"email": candidate, "password": anything}` and check whether the response is `"Invalid email"` vs. `"Invalid password"`/MFA-challenge — distinguishing valid vs. invalid accounts (user enumeration).
2. For emails that don't return `"Invalid email"`, observe whether the response is a WebAuthn challenge JSON (from `sessions_controller.go`'s pre-check at lines 42-54, which runs unconditionally before password validation) — directly disclosing MFA enrollment status without even needing a correct password.

Note that the code comment in `orm.go` claims the email/password check ordering is done "to prevent extra database look up for MFA tokens leaking if an account has MFA tokens or not" — but this protection is moot because `sessions_controller.go` already performs the `GetUserWebAuthn` lookup and branches on it (setting `WebAuthnConfig`) *before* calling `CreateSession` at all, and the eventual response bodies for MFA-enabled vs. MFA-disabled accounts remain different regardless of order inside `orm.go`.

The `constantTimeEmailCompare` function itself only prevents *timing* differences in the email string comparison; it does nothing to prevent the *message* differences that leak email existence, since a mismatch always returns `"Invalid email"` regardless of comparison speed.

### Impact Explanation
This is an authentication information-disclosure issue (CWE-203/CWE-204-style user enumeration and account attribute disclosure) rather than a direct compromise. It does not by itself achieve credential/secret access, privilege escalation, or unauthorized action on another user's resources — it only assists an attacker in building a target list (valid emails, non-MFA accounts) for subsequent credential-stuffing or password-guessing attacks against the login endpoint. There is no code path here that returns secrets, bypasses authentication, or grants a session without correct credentials/MFA. Per Chainlink's bounty impact taxonomy this would classify at most as an **Informational/Low** finding (information disclosure aiding attacks), not the High severity implied in the prompt, since actual admin compromise still requires the attacker to separately guess/know a valid password and, for MFA accounts, defeat WebAuthn — which is unaffected by this issue.

### Likelihood Explanation
Precondition: none — the `/sessions` endpoint is intentionally unauthenticated. The enumeration technique is trivially repeatable via scripted POST requests with varying `email` values, and the response text differences are directly readable JSON error bodies (no timing side-channel needed). Rate limiting/lockout is not implemented in the reviewed code path, making mass enumeration feasible until the operator adds throttling at the reverse proxy or gateway layer (outside repo scope).

### Recommendation
- Return a single generic error/response (e.g., `"invalid credentials"`) for all three failure branches (unknown email, wrong password, and MFA-required-but-missing) instead of `"Invalid email"`, `"Invalid password"`, and `"MFA Error"`.
- Avoid performing the `GetUserWebAuthn` pre-check in `sessions_controller.go` before validating the password; only branch into the WebAuthn challenge flow after the password has been verified, and make the challenge-vs-non-challenge response shape identical when possible (or always request WebAuthn data on the first round trip regardless of whether MFA is enabled).
- Add rate limiting / account lockout / CAPTCHA at the login endpoint to reduce enumeration feasibility regardless of message content.

### Proof of Concept
Go handler-level integration test plan (`core/web/sessions_controller_test.go`):
1. Seed one user `known@example.com` with MFA disabled and a known password, and one user `mfa@example.com` with MFA enabled.
2. POST `/sessions` with `{"email": "unknown@example.com", "password": "x"}` → assert response body contains `"Invalid email"`.
3. POST `/sessions` with `{"email": "known@example.com", "password": "wrong"}` → assert response body contains `"Invalid password"` (different from step 2).
4. POST `/sessions` with `{"email": "mfa@example.com", "password": "wrong"}` → assert response differs again (WebAuthn challenge or `"MFA Error"`), distinguishing MFA-enabled accounts from non-MFA accounts without needing the correct password.
5. Assert that all three response bodies/status text are distinguishable strings, demonstrating the enumeration oracle described above.

### Citations

**File:** core/web/sessions_controller.go (L56-60)
```go
	sid, err := sc.App.AuthenticationProvider().CreateSession(ctx, sr)
	if err != nil {
		jsonAPIError(c, http.StatusUnauthorized, err)
		return
	}
```

**File:** core/sessions/localauth/orm.go (L152-157)
```go
	// Do email and password check first to prevent extra database look up
	// for MFA tokens leaking if an account has MFA tokens or not.
	if !constantTimeEmailCompare(strings.ToLower(sr.Email), strings.ToLower(user.Email)) {
		o.auditLogger.Audit(audit.AuthLoginFailedEmail, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid email")
	}
```

**File:** core/sessions/localauth/orm.go (L159-162)
```go
	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		o.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid password")
	}
```

**File:** core/sessions/localauth/orm.go (L181-199)
```go
	// Next check if this session request includes the required WebAuthn challenge data
	// if not, return a 401 error for the frontend to prompt the user to provide this
	// data in the next round trip request (tap key to include webauthn data on the login page)
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
