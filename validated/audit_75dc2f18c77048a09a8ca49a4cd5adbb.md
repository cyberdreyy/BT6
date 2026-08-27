### Title
CreateSession leaks user existence via timing side channel due to bcrypt only running on the found-user path - ([File: core/sessions/localauth/orm.go])

### Summary
`CreateSession` returns immediately when `FindUser` fails to locate the email (`sql.ErrNoRows`), before any password hashing occurs, but performs a full `bcrypt.CompareHashAndPassword` call (via `utils.CheckPasswordHash`) when the email does exist. This produces a measurable latency gap between "email exists, wrong password" and "email does not exist," enabling user enumeration by an unauthenticated caller. Note this is not actually caused by `constantTimeEmailCompare`'s short-circuit as hypothesized in the question — that function only executes after `FindUser` has already succeeded (the emails will always match at that point) — the real leak is the early-return before `CheckPasswordHash`/bcrypt is even invoked.

### Finding Description
`CreateSession` in `core/sessions/localauth/orm.go` looks up the user by email first: [1](#0-0) 
If `FindUser` fails (e.g. unknown email → `sql.ErrNoRows`), the function returns immediately without ever calling `constantTimeEmailCompare` or `utils.CheckPasswordHash`/bcrypt — this is a fast, single indexed DB lookup.

If the email does exist, execution proceeds to `constantTimeEmailCompare` (a fast constant-time byte compare against the just-fetched matching email, which will always succeed at this point since `FindUser` matched on `lower(email)`), then to `utils.CheckPasswordHash`: [2](#0-1) 
`utils.CheckPasswordHash` invokes `bcrypt.CompareHashAndPassword`, a deliberately slow, tunable-cost KDF (tens to hundreds of milliseconds depending on cost factor), against a valid stored hash.

The attacker-observable difference is therefore not from `constantTimeEmailCompare`'s internal short-circuiting (as posited in the question) but from the fact that the expensive bcrypt computation is skipped entirely on the "unknown email" path and always executed on the "known email, wrong password" path. There is no dummy/constant-cost hash comparison performed for non-existent users to normalize timing (e.g. no fallback `bcrypt.CompareHashAndPassword` against a fixed dummy hash when `FindUser` fails).

This is reachable by any unauthenticated caller of the login/session HTTP endpoint that invokes `CreateSession`, with no auth or role required — matching the "unprivileged attacker" threat model.

### Impact Explanation
This is a low-severity information-disclosure primitive: user/email enumeration precursor to targeted credential stuffing (matching Chainlink's informational/low disclosure class for account-existence timing side channels). It does not itself grant authentication bypass, key/secret disclosure, or fund movement — it only helps an attacker narrow down valid admin emails, which is a supporting step for further attacks such as credential stuffing or targeted phishing.

### Likelihood Explanation
Fully feasible for any unauthenticated network client with reasonably precise timing measurement (many trials averaging out network jitter). No credentials or special role are required — only the ability to submit `SessionRequest` POSTs to the login endpoint repeatedly and measure response latency statistically.

### Recommendation
Perform a constant-cost password verification regardless of whether the user was found — e.g. run `utils.CheckPasswordHash` (or an equivalent bcrypt comparison) against a fixed dummy hash when `FindUser` returns `sql.ErrNoRows`, before returning the "Invalid email"-style error, so that both code paths take approximately the same wall-clock time.

### Proof of Concept
Go benchmark/integration test in `core/sessions/localauth/orm_test.go`:
1. Seed one valid user with a bcrypt-hashed password.
2. Case A: call `orm.CreateSession` with the valid email and a wrong password; record elapsed time over N iterations.
3. Case B: call `orm.CreateSession` with a random/non-existent email and any password; record elapsed time over N iterations.
4. Assert that the mean/median latency in Case A is significantly higher (roughly equal to bcrypt cost, e.g. >50ms) than Case B (sub-millisecond DB lookup miss), demonstrating the enumeration side channel.
5. Fix validation: after adding a dummy-hash comparison on the not-found path, re-run and assert the latency distributions converge within noise.

### Citations

**File:** core/sessions/localauth/orm.go (L144-148)
```go
func (o *orm) CreateSession(ctx context.Context, sr sessions.SessionRequest) (string, error) {
	user, err := o.FindUser(ctx, sr.Email)
	if err != nil {
		return "", err
	}
```

**File:** core/sessions/localauth/orm.go (L154-162)
```go
	if !constantTimeEmailCompare(strings.ToLower(sr.Email), strings.ToLower(user.Email)) {
		o.auditLogger.Audit(audit.AuthLoginFailedEmail, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid email")
	}

	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		o.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid password")
	}
```
