# Q3232: digest injected through constructor options in EventCallbackQueue.ts

## Question
Privy accepts a crypto option that supplies digest; can an attacker pass an implementation through EventCallbackQueue.enqueue that returns a fixed challenge so PKCE binding is defeated?

## Target
- File/function: [src/embedded/EventCallbackQueue.ts](src/embedded/EventCallbackQueue.ts) - EventCallbackQueue.enqueue, dequeue (id-only lookup then event-name switch), flush; module-level singleton shared by every proxy instance; ids from a global 'id-N' counter
- Entrypoint: any embedded wallet operation that awaits an iframe reply
- Attacker controls: reply id values, reply event names, arrival ordering, reload/flush timing
- Exploit idea: Construct the client with a crypto object returning constant digests.
- Invariant to test: A caller-supplied crypto implementation must not weaken PKCE or key derivation.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a constant-digest crypto to EventCallbackQueue.enqueue and assert the flow refuses or the challenge stays unique.
