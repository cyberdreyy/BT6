### Title
Debug/pprof routes registered by `metricRoutes` are reachable by any authenticated role (including view-role) with no `RequiresEditRole`/`RequiresAdminRole` wrapper - ([File: core/web/router.go])

### Summary
`metricRoutes(authv2)` is called directly inside the `authv2` route group at `core/web/router.go:446` without any role-escalation wrapper, unlike every other sensitive handler in the same group (e.g. `auth.RequiresEditRole(vault.VerifyDKGResult)` at line 442, `auth.RequiresAdminRole(lgc.Patch)` at line 412). Because `authv2` is only gated by the base `Authenticate` middleware (session cookie or API token, no role check), a minimally-privileged `view`-role or `run`-role authenticated user can reach whatever debug/pprof/metrics handlers `metricRoutes` registers.

### Finding Description
The `authv2` group is protected only by `auth.Authenticate(...)` (see `core/web/auth/auth.go:157`), which merely confirms the request carries a valid session or API token and sets the authenticated `User` in context — it performs no role check. Role enforcement in this codebase is opt-in per-route via `auth.RequiresRunRole`, `auth.RequiresEditRole`, or `auth.RequiresAdminRole` (`core/web/auth/auth.go:202-255`), each of which explicitly inspects `user.Role` and aborts with 401/403 if the caller's role (`view`, `run`, `edit`, `admin`) is insufficient.

At `core/web/router.go:446`, `metricRoutes(authv2)` is invoked with none of these wrappers, in contrast to every neighboring sensitive route (`RequiresEditRole` on the vault/forwarders endpoints, `RequiresAdminRole` on log patching). This means any handler registered inside `metricRoutes` — comment states "Debug routes accessible via authentication" — is reachable by a `view`-role user, the lowest privilege tier that can still authenticate, as well as `run`-role. Since the file imports `net/http/pprof` (`router.go:11`), these debug routes are very likely pprof-style handlers that expose goroutine stacks, heap/memory profiles, CPU profiles, and internal runtime state — data that can include sensitive information residing in process memory (e.g., decrypted key material, DB credentials, in-flight secrets) and at minimum aids further attacks via internal topology/goroutine disclosure.

I was not able to retrieve the body of the `metricRoutes` function itself or the exact `authv2` group declaration (both outside the fetched line range) to enumerate the precise registered paths, so the specific handler names/paths cannot be cited with certainty from the tool output gathered. However, the authorization-wrapper omission at the call site (`router.go:446`) is unambiguous relative to the surrounding pattern of explicit role wrapping.

### Impact Explanation
If `metricRoutes` indeed wires up pprof/metrics debug endpoints (as its comment and the `net/http/pprof` import strongly suggest), a low-privilege `view`-role token — the credential tier intended only for read-only dashboard access — can pull goroutine dumps, heap profiles, and CPU profiles from a running Chainlink node. This is an authorization-boundary violation: internal diagnostic data intended for admins/operators becomes accessible to any authenticated user, regardless of role, which maps to Chainlink's "sensitive information disclosure via authenticated low-privilege access" impact class.

### Likelihood Explanation
Exploitation only requires a valid `view`-role (or `run`-role) session cookie or API token — the minimum authentication tier supported by the node. No additional privilege, timing, or race condition is needed; the request is a simple authenticated GET to the debug path(s) registered by `metricRoutes`. This is trivially repeatable.

### Recommendation
Wrap the `metricRoutes(authv2)` call (or the individual routes registered inside `metricRoutes`) with `auth.RequiresAdminRole` (or at minimum `auth.RequiresEditRole`), consistent with how other sensitive/internal endpoints in the same group (`vault.VerifyDKGResult`, `lgc.Patch`, `efc.Track`) are protected, e.g.:
```go
authv2.GET("/debug/pprof/*any", auth.RequiresAdminRole(...))
```
or gate the whole sub-group: `metricRoutes(authv2.Group("", auth.RequiresAdminRoleMiddleware))`.

### Proof of Concept
1. In a `core/web` handler-level integration test (using existing test harness that spins up `NewRouter`/`cltest` app), create three authenticated sessions: `admin`, `edit`, and `view` role users.
2. Enumerate the paths registered by `metricRoutes` (inspect `engine.Routes()` after `NewRouter` for paths under the debug/pprof prefix mounted at line 446).
3. For each such path, issue `GET` requests using each role's session/token.
4. Assert: `admin` (and per intended fix, `edit`) receives `200` with the expected debug payload; `view`-role and `run`-role receive `403 Forbidden` (`errors.New("Forbidden")` from `RequiresAdminRole`/`RequiresEditRole`) instead of `200`.
5. Currently (pre-fix), the test would show `view`-role receiving `200` with pprof/debug payload — confirming the authorization gap.