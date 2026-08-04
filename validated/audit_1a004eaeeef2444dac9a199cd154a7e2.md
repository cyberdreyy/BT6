### Title
Unbounded goroutine/gRPC connection leak from unclosed `IamCredentialsClient` in GCS cache signing - (File: `cache/gcs/credentials_resolver.go`)

### Summary
`defaultCredentialsResolver.iamCredentialsClient` creates a new `credentialsapiv1.NewIamCredentialsClient(ctx)` gRPC client whenever a GCS cache adapter needs to sign a URL with instance credentials, but that client is never closed anywhere in the codebase. Because `cache.GetAdapter` constructs a brand-new `gcsAdapter`/`credentialsResolver` pair on every cache resolution call (one per job, and potentially multiple times per job for download/head/upload URLs), every such call permanently leaks a gRPC connection and its background goroutines in the long-lived `gitlab-runner` process.

### Finding Description
`cache.GetAdapter` (`cache/cache.go:27-80`) is invoked fresh for each cache operation and calls `createAdapter` → `gcs.New` (`cache/gcs/adapter.go:118-139`), which allocates a new `defaultCredentialsResolver` via `newDefaultCredentialsResolver` (`cache/gcs/credentials_resolver.go:142-154`). Nothing in the codebase caches or reuses `gcsAdapter`/`credentialsResolver` instances across cache calls or jobs.

When the runner is configured without a static `PrivateKey` (i.e. relying on instance/metadata credentials — a supported and common GCS cache configuration), `gcsAdapter.presignURL` (`cache/gcs/adapter.go:96-101`) calls `a.credentialsResolver.SignBytesFunc(ctx)`, which lazily creates the IAM client: [1](#0-0) 

`cr.credentialsClient` is stored on the per-request `defaultCredentialsResolver` struct, not on any shared/global object, and `IamCredentialsClient`/`credentialsapiv1.Client` implements `io.Closer` via `Close()`, but no code path in `cache/gcs/*.go` ever calls it. Once `presignURL` returns, the adapter (and its resolver, and the underlying `*credentialsapiv1.IamCredentialsClient`, which wraps a `grpc.ClientConn` with background keepalive/watcher goroutines) becomes unreferenced and is left for GC — but `grpc.ClientConn` does not register a finalizer to close itself, so the goroutines and underlying socket/file descriptor persist indefinitely until process exit.

Any pipeline author who triggers many distinct cache resolutions (e.g., matrix/parallel jobs each with a distinct `cache:key`, or repeated jobs) causes `gcs.New` → `iamCredentialsClient` to run repeatedly on the shared runner-manager process, each instantiating a brand-new leaked gRPC client. Since `GetAdapter`/`presignURL` are called for download, head-check, and upload URL generation, a single job can trigger multiple leaks, and this scales linearly with attacker-controlled job/cache-key count. No existing cleanup logic, timeout, or reference-counting mechanism guards against this — the leak is unconditional whenever metadata-based signing is used.

### Impact Explanation
Each leaked `IamCredentialsClient` holds an open gRPC connection to `iamcredentials.googleapis.com`, consuming a socket file descriptor and several background goroutines (transport reader/writer, keepalive, resolver watcher). An unprivileged user who can schedule many jobs with distinct cache keys (trivial via CI matrix/parallel jobs) can drive unbounded growth in goroutine count and open file descriptors in the single shared `gitlab-runner` manager process. Over time this exhausts process resources (FD limits, memory from goroutine stacks), causing job scheduling/cache operations to fail or the manager process to become unstable — a shared-runner-wide denial of service affecting other projects' jobs on the same runner, persisting beyond the life of the originating job (job cancellation does not close the client).

### Likelihood Explanation
This triggers under ordinary, common configuration: a GCS cache configured to use instance credentials (no static `PrivateKey`) — a documented, non-privileged and commonly recommended setup. Any authenticated pipeline author with `.gitlab-ci.yml` control can trivially generate many distinct `cache:key` values (matrix builds, parallel jobs, dynamic keys) to force repeated adapter/resolver construction. No special access, elevated GitLab permissions, or admin misconfiguration is needed beyond the standard GCS-cache-with-metadata-credentials setup. The leak is deterministic and repeatable on every triggering call.

### Recommendation
Close the `IamCredentialsClient` when the resolver/adapter's job-scoped work is done. Concretely:
- Add a `Close()` method to `credentialsResolver`/`defaultCredentialsResolver` that calls `cr.credentialsClient.Close()` if non-nil, and invoke it wherever `gcsAdapter` finishes its use (e.g., have `cache.Adapter` interface support cleanup, or close in `presignURL`'s caller after URL generation is complete).
- Alternatively, cache/reuse a single long-lived `IamCredentialsClient` at the process level (keyed by credentials config) instead of creating one per cache-adapter instance, with proper lifecycle management and idle-connection reaping.

### Proof of Concept
```go
func TestIamCredentialsClientLeak(t *testing.T) {
    startGoroutines := runtime.NumGoroutine()

    for i := 0; i < 500; i++ {
        cfg := &cacheconfig.CacheGCSConfig{BucketName: "b"} // no PrivateKey -> metadata signing path
        cr, err := newDefaultCredentialsResolver(cfg)
        require.NoError(t, err)
        cr.metadataClient = fakeMetadataClient{email: "sa@example.com"}
        require.NoError(t, cr.Resolve())

        signFn := cr.SignBytesFunc(context.Background())
        // fake/mock IamCredentialsClient injected via cr.credentialsClient in real test setup
        _, _ = signFn([]byte("payload"))
        // No Close() ever invoked on cr.credentialsClient
    }

    runtime.GC()
    time.Sleep(100 * time.Millisecond)
    endGoroutines := runtime.NumGoroutine()

    // Expect leak: goroutine count grows roughly linearly with N, proving no cleanup occurs
    assert.Greater(t, endGoroutines, startGoroutines+400,
        "expected goroutine leak from unclosed IamCredentialsClient instances")
}
```
Expected assertion: goroutine/FD count grows proportionally to the number of `gcs.New()`/`presignURL()` invocations and never decreases, confirming `Close()` is never called on any `credentialsClient`.

### Citations

**File:** cache/gcs/credentials_resolver.go (L130-140)
```go
func (cr *defaultCredentialsResolver) iamCredentialsClient(ctx context.Context) (IamCredentialsClient, error) {
	if cr.credentialsClient == nil {
		var err error
		cr.credentialsClient, err = credentialsapiv1.NewIamCredentialsClient(ctx)
		if err != nil {
			return nil, fmt.Errorf("creating iam credentials client: %w", err)
		}
	}

	return cr.credentialsClient, nil
}
```
