### Title
In-memory `RequestReplayGuard` allows replay of allowlisted/authorized Vault requests across gateway instances and node restarts - ([File: core/capabilities/vault/authorizer.go])

### Summary
`authorizer.AuthorizeRequest` delegates replay protection entirely to `RequestReplayGuard.CheckAndRecord`, which stores seen digests only in a process-local, in-memory Go map with no persistence. Any restart of the node/gateway process, or any deployment topology with multiple independent gateway/vault node instances (each constructing its own `RequestReplayGuard` via `NewAuthorizer`), resets or fails to share this state, allowing an identical previously-authorized request (same digest, same `ExpiresAt`) to be re-authorized and re-executed as long as it is still within its original expiry window.

### Finding Description
`authorizer.AuthorizeRequest` (core/capabilities/vault/authorizer.go:99-119) obtains an `AuthResult` from either the allowlist-based or JWT-based auth path, then calls `a.replayGuard.CheckAndRecord(authResult.Digest(), authResult.ExpiresAt())` to prevent replay of the identical signed request. `RequestReplayGuard` (core/capabilities/vault/request_replay_guard.go:16-47) is a bare in-memory struct: `seen map[string]int64` protected by a `sync.Mutex`, initialized fresh in `NewRequestReplayGuard()`, and instantiated fresh per `authorizer` object created via `NewAuthorizer` (authorizer.go:90-97). There is no shared/persistent store (e.g., DB, distributed cache) backing this map.

Consequently:
- On process restart, `seen` is empty, so a previously-consumed digest with a still-unexpired `ExpiresAt` is accepted again by `CheckAndRecord`, since the only checks are presence in the local map and expiry-time comparison (request_replay_guard.go:41-46).
- If multiple gateway/vault node processes each run their own `Authorizer`/`RequestReplayGuard` instance (as constructed independently via `NewAuthorizer`), the same signed/allowlisted request replayed against a second instance is treated as novel, because each instance's `seen` map is disjoint.

The `validateSecretOwnersMatchAuthorized` check that follows (authorizer.go:113-116) only verifies that secret owner fields in the request body match the authorized owner — it does not perform any additional replay/idempotency check, so it does not stop this class of replay.

### Impact Explanation
An attacker (or a legitimate signer who lost control of one prior authorized request, e.g. captured on the wire or reused from a log) can resubmit the exact same signed/allowlisted secrets-create/update/delete request to a second gateway instance, or to the same instance after it restarts, causing the DON to execute the operation a second time within the original owner's authorization window. Depending on the targeted method, this can duplicate secret writes/deletes attributable to the original owner, i.e., unauthorized repeated execution of an operation the owner only authorized once — matching a "request/replay-protection bypass leading to unauthorized duplicate DON operation" impact class. The impact is bounded to whatever operations are gated purely by digest+expiry replay protection (secrets create/update/delete), not to fund movement directly, since Vault's scope here is secrets management.

### Likelihood Explanation
Exploitation requires only: (1) possession of one previously valid signed/allowlisted request (which the "attacker" by definition already has, per the threat model — a legitimate holder of one allowlisted digest), and (2) the ability to resend that exact request to a second gateway/vault node instance, or to await/trigger a node restart of the target instance, before `ExpiresAt` elapses. No credential escalation, admin access, or additional signing capability is needed — the replay guard is the only defense against reuse of a digest, and it is trivially bypassed by targeting a different in-memory instance or timing a restart. This is deterministic and repeatable in any multi-instance gateway deployment or whenever a node process restarts.

### Recommendation
Back `RequestReplayGuard` with a persistent, shared store (e.g., a database table or distributed cache keyed by digest with TTL matching `ExpiresAt`) so that "seen" state survives restarts and is consistent across all gateway/vault node instances that can authorize requests, rather than each process maintaining an independent in-memory map.

### Proof of Concept
Go unit test in `core/capabilities/vault/request_replay_guard_test.go`:
```go
func TestRequestReplayGuard_ReplayAcrossRestart(t *testing.T) {
    digest := "same-digest-value"
    expiresAt := time.Now().Add(time.Hour).UTC().Unix()

    // Simulate node/gateway instance #1
    guard1 := NewRequestReplayGuard()
    err := guard1.CheckAndRecord(digest, expiresAt)
    require.NoError(t, err) // first use accepted

    // Simulate the same request replayed against guard1 (should be rejected)
    err = guard1.CheckAndRecord(digest, expiresAt)
    require.ErrorIs(t, err, ErrRequestAlreadySeen)

    // Simulate a restart (or a second independent gateway instance) — new in-memory guard
    guard2 := NewRequestReplayGuard()
    err = guard2.CheckAndRecord(digest, expiresAt)
    // BUG: succeeds, allowing replay of the same authorized digest post-restart / on another instance
    require.NoError(t, err, "replay guard should have rejected an already-used digest but did not persist state across instances")
}
```
Expected (fixed) behavior: the second `CheckAndRecord` call against `guard2` should return `ErrRequestAlreadySeen` if replay state were shared/persisted; the current implementation returns `nil`, confirming the vulnerability.