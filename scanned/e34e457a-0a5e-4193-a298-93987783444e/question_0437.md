# Q0437: spec fields reach command or process execution in loop_registry.NewLoopRegistryServer

## Question
Can an authenticated node user holding only the 'view' role submit a spec/parameter through `NewLoopRegistryServer` at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers) whose fields are passed to a process, plugin loader, template or shell, achieving execution on the node host?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `NewLoopRegistryServer`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: pprof query parameters (seconds, debug) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `pprof query parameters (seconds, debug)` with command/path/plugin fields.
- Invariant to test: no user-supplied spec field may reach process, loader or template execution
- Expected Immunefi impact: Critical - arbitrary system command execution on the node host
- Fast validation: unit test asserting spec fields are never interpolated into exec/loader arguments
