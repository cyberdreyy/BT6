# Q2584: role wrapper omitted on a route in middleware.Exists

## Question
Is there a state-changing route reaching `Exists` from GET on any static asset path served by ServeGzippedAssets/GzipFileServer that is registered without RequiresEditRole/RequiresAdminRole, letting an unauthenticated HTTP client that can reach the node API port invoke it with only view or run rights?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Exists`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: percent-encoded and dot-segment path bytes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Enumerate registered routes and compare each handler's declared minimum role against its wrapper, then call the weakest one with `percent-encoded and dot-segment path bytes`.
- Invariant to test: every state-changing /v2 route must be wrapped by the role gate matching its side effect
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: reflective route-table test asserting each non-GET route carries a role middleware
