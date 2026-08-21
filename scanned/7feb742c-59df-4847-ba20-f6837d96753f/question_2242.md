# Q2242: remove clears every signer in EventCallbackQueue.ts

## Question
removeSessionSigners writes additional_signers: [] or revokes all delegations; can an attacker use EventCallbackQueue.enqueue to clear another party's legitimate signer while keeping their own access?

## Target
- File/function: [src/embedded/EventCallbackQueue.ts](src/embedded/EventCallbackQueue.ts) - EventCallbackQueue.enqueue, dequeue (id-only lookup then event-name switch), flush; module-level singleton shared by every proxy instance; ids from a global 'id-N' counter
- Entrypoint: any embedded wallet operation that awaits an iframe reply
- Attacker controls: reply id values, reply event names, arrival ordering, reload/flush timing
- Exploit idea: Call the remove path while multiple signers exist.
- Invariant to test: Signer removal must be scoped to the signer the user selected.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call EventCallbackQueue.enqueue with multiple signers present and assert only the requested one is removed.
