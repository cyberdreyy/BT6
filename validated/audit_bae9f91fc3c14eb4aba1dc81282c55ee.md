### Title
Edit-role user can delete OCR2 key bundle via GraphQL `deleteOCR2KeyBundle`, bypassing admin-only REST restriction - ([File: core/web/resolver/mutation.go])

### Finding Description
The REST endpoint for deleting an OCR2 key bundle is gated behind `auth.RequiresAdminRole` [1](#0-0) , meaning only admin-role sessions/tokens can delete a signing key bundle through the HTTP API. However, the GraphQL mutation resolver `DeleteOCR2KeyBundle` in `core/web/resolver/mutation.go` performs its authorization check using `authenticateUserCanEdit` rather than `authenticateUserIsAdmin`. The `authenticateUserCanEdit` helper only rejects `UserRoleView` and `UserRoleRun` sessions, and explicitly permits `UserRoleEdit` sessions through [2](#0-1) , while `authenticateUserIsAdmin` is the only helper that enforces `sessions.UserRoleAdmin` [3](#0-2) . Because the GraphQL layer uses the weaker `authenticateUserCanEdit` check for a destructive key-management operation, a user holding only the `edit` role — who is explicitly denied via the REST route — can invoke the equivalent action through `POST /query` with `mutation deleteOCR2KeyBundle(id: ...)` and have the key deleted, violating the "authorization is exact" invariant between the two API surfaces for the same underlying action (`OCR2KeysController.Delete` equivalent).

### Impact Explanation
Deleting an OCR2 key bundle used in active OCR2 rounds removes an oracle node's ability to sign/participate in consensus for jobs referencing that key bundle, disrupting oracle round participation and job execution — this is a role/authorization-bypass privilege escalation from edit to admin-gated destructive action, matching the "unauthorized action on another user's job/key" bounty class.

### Likelihood Explanation
Precondition is only a valid edit-role session (no admin) — a low bar since edit role is a common non-admin operator role in Chainlink node deployments. The GraphQL mutation is directly reachable via a single authenticated `POST /query` request with no other mitigating checks between the resolver auth call and the keystore deletion, making this trivially repeatable once an edit-role credential is obtained.

### Recommendation
Change the `DeleteOCR2KeyBundle` resolver in `core/web/resolver/mutation.go` to call `authenticateUserIsAdmin` instead of `authenticateUserCanEdit`, aligning it with the REST route's `auth.RequiresAdminRole` gate. Audit all other key-management mutations (CSA, P2P, VRF, OCR key bundles, etc.) in the same file for the same edit/admin mismatch against their REST counterparts, since this pattern of using `authenticateUserCanEdit` for key mutations appears repeated across `mutation.go`.

### Proof of Concept
Go handler-level integration test using the resolver test harness (pattern from `core/web/resolver/ocr2_keys_test.go`):
1. Set up a test GraphQL client authenticated with a session having `sessions.UserRoleEdit`.
2. Seed the keystore with an OCR2 key bundle (`ocr2key`) that is referenced by an active OCR2 job spec.
3. Execute `mutation { deleteOCR2KeyBundle(id: "<bundleID>") { ... } }` against the GraphQL endpoint.
4. Assert current (vulnerable) behavior: mutation succeeds (HTTP 200, no `RoleNotPermittedError`), and the key is removed from the keystore — confirm via `keystore.OCR2().Get(bundleID)` returning not-found.
5. In parallel, call `DELETE /v2/keys/ocr2/:keyID` with the same edit-role session/token and assert it is rejected with 401/403 due to `RequiresAdminRole`, demonstrating the inconsistency.
6. After applying the fix (switching to `authenticateUserIsAdmin`), re-run step 3 and assert the mutation returns a `RoleNotPermittedError` and the key remains present in the keystore.

### Citations

**File:** core/web/router.go (L1-1)
```go
package web
```

**File:** core/web/resolver/auth.go (L31-43)
```go
// Authenticates the user from the session cookie and asserts at least 'edit' role.
func authenticateUserCanEdit(ctx context.Context) error {
	session, ok := auth.GetGQLAuthenticatedSession(ctx)
	if !ok {
		return unauthorizedError{}
	}
	switch session.User.Role {
	case sessions.UserRoleView, sessions.UserRoleRun:
		return RoleNotPermittedError{session.User.Role}
	default:
	}
	return nil
}
```

**File:** core/web/resolver/auth.go (L45-55)
```go
// Authenticates the user from the session cookie and asserts has 'admin' role
func authenticateUserIsAdmin(ctx context.Context) error {
	session, ok := auth.GetGQLAuthenticatedSession(ctx)
	if !ok {
		return unauthorizedError{}
	}
	if session.User.Role != sessions.UserRoleAdmin {
		return RoleNotPermittedError{session.User.Role}
	}
	return nil
}
```
