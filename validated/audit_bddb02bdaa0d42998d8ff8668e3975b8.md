### Title
Internal ORM errors are leaked verbatim to API clients via jsonAPIError - ([File: core/web/jobs_controller.go], [File: core/web/helpers.go])

### Summary
`JobsController.Show` forwards any non-`sql.ErrNoRows` error returned by `FindJob`/`FindJobByExternalJobID` directly to `jsonAPIError`, which serializes `err.Error()` verbatim into the JSON response body without redaction. Any authenticated user permitted to call `GET /v2/jobs/:ID` (this endpoint has no elevated role gate beyond standard session auth) can potentially trigger and observe raw internal error text.

### Finding Description
In `core/web/jobs_controller.go`, `Show` does:
```go
jobSpec, err = jc.App.JobORM().FindJob(ctx, jobSpec.ID)
...
if err != nil {
    if errors.Is(errors.Cause(err), sql.ErrNoRows) {
        jsonAPIError(c, http.StatusNotFound, errors.New("job not found"))
    } else {
        jsonAPIError(c, http.StatusInternalServerError, err)
    }
    return
}
``` [1](#0-0) 

`jsonAPIError` (in `core/web/helpers.go`) does not redact the error unless it is already a `*models.JSONAPIErrors`; otherwise it calls `err.Error()` directly and returns it in the response body:
```go
func jsonAPIError(c *gin.Context, statusCode int, err error) {
	_ = c.Error(err).SetType(gin.ErrorTypePublic)
	var jsonErr *models.JSONAPIErrors
	if errors.As(err, &jsonErr) {
		c.JSON(statusCode, jsonErr)
		return
	}
	c.JSON(statusCode, models.NewJSONAPIErrorsWith(err.Error()))
}
``` [2](#0-1) 

This same pattern (raw `err` passed to `jsonAPIError` on internal errors) is repeated throughout `Index`, `Create`, `Delete`, `Update` in the same file, so it is a systemic pattern rather than isolated to `Show`.

However, I was unable to confirm within the available context whether the ORM's `FindJob`/`FindJobByExternalJobID` implementations (in `core/services/job` ORM package) actually wrap driver-level errors (e.g., raw `pgx`/`sql` driver errors containing connection strings or file paths) into the error chain returned to the controller, versus returning generic sentinel errors. I could not locate/inspect the ORM implementation or the router's role-gating middleware for `/v2/jobs/:ID` in this session to verify the exact role required and to confirm what content actual DB errors carry in this codebase version. This is a real limitation of what I could verify with available tools/index in this pass.

### Impact Explanation
If the underlying ORM error text does propagate driver/connection details (this is standard behavior for Go's `database/sql`/`pgx` wrapped errors, which often include query fragments, constraint names, or connection failure details, though typically not raw credentials since those live in the DSN configured server-side, not usually embedded in query errors), this constitutes an information-disclosure issue: internal implementation details, file paths from panics-as-errors, or internal identifiers could be returned to any caller able to hit `GET /v2/jobs/:ID`, including low-privileged/view-role users. This matches Chainlink bounty's "sensitive data exposure" / information disclosure class, though the severity is generally low-to-medium since it does not directly yield key material or credentials under normal operation — actual credential/DSN leakage would require a very unusual ORM error implementation (e.g., an error type whose `Error()` includes the DSN), which is not confirmed in this codebase.

### Likelihood Explanation
Reaching this code path requires only being authenticated to the node's HTTP API with permission to call `GET /v2/jobs/:ID`; based on the code reviewed, `Show` has no additional role check beyond what the router applies to the jobs routes generally. Triggering a "non-`sql.ErrNoRows`, non-nil" error deterministically (e.g., a DB connectivity blip, context deadline exceeded, or malformed but non-uuid identifier causing a driver-level error) is not something an attacker can reliably force under normal healthy-node conditions — it typically requires either an already-degraded backend (DB outage/timeout) or a bug in ID parsing that surfaces a driver error, neither of which is demonstrated as reliably attacker-triggerable in the code reviewed. This lowers likelihood: it's a defense-in-depth/hardening gap more than a reliably exploitable vulnerability with attacker-controlled trigger conditions.

### Recommendation
Do not return raw `err.Error()` for `http.StatusInternalServerError` cases in HTTP handlers. Log the full error server-side (`jc.App.GetLogger().Errorw(...)`) and return a generic, static message (e.g., `"internal server error"`) to the client for all `5xx` branches across `core/web/jobs_controller.go` and other controllers using this pattern. Consider adding a helper `jsonAPIErrorInternal(c, err)` that always logs internally and always returns a fixed public message, distinct from `jsonAPIError` used for `4xx` validation errors where returning the error text is intentional and safe.

### Proof of Concept
Go handler-level test plan:
1. Define a mock `job.ORM` (or use existing test mocks under `core/services/job/mocks`) whose `FindJob` and `FindJobByExternalJobID` methods return `errors.New("dial tcp 10.0.0.5:5432: connect: connection refused (dsn=postgres://user:FAKE_SECRET@host/db)")` — a fake but realistic wrapped driver error containing a marker secret string `FAKE_SECRET`.
2. Construct a `chainlink.Application` test double (or use `web_test` helpers such as `setupJobsControllerTests`) wired to this mock ORM.
3. Issue `GET /v2/jobs/00000000-0000-0000-0000-000000000000` (a valid UUID that doesn't match `sql.ErrNoRows` cause) through the test router with a valid session/API-token for a view-role (non-admin) user.
4. Assert response status is `500`.
5. Assert response body (parsed as JSON:API error) contains the substring `FAKE_SECRET`, proving the raw error text is returned to the caller — confirming the leak path in `jsonAPIError` at `core/web/helpers.go:28` reached from `core/web/jobs_controller.go:85`.
6. As a control, repeat with `sql.ErrNoRows`-wrapped error and assert the generic `"job not found"` message is returned instead (confirming the redaction only applies to the `ErrNoRows` branch).

### Citations

**File:** core/web/jobs_controller.go (L81-88)
```go
	if err != nil {
		if errors.Is(errors.Cause(err), sql.ErrNoRows) {
			jsonAPIError(c, http.StatusNotFound, errors.New("job not found"))
		} else {
			jsonAPIError(c, http.StatusInternalServerError, err)
		}
		return
	}
```

**File:** core/web/helpers.go (L21-29)
```go
func jsonAPIError(c *gin.Context, statusCode int, err error) {
	_ = c.Error(err).SetType(gin.ErrorTypePublic)
	var jsonErr *models.JSONAPIErrors
	if errors.As(err, &jsonErr) {
		c.JSON(statusCode, jsonErr)
		return
	}
	c.JSON(statusCode, models.NewJSONAPIErrorsWith(err.Error()))
}
```
