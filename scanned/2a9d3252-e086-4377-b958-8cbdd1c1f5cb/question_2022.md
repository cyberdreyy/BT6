# Q2022: session signers read-modify-write race in EventCallbackQueue.ts

## Question
addSessionSigners reads additional_signers via getWallet then writes the concatenated list; can an attacker interleave two calls through EventCallbackQueue.enqueue so one signer set overwrites the other or a removal is undone?

## Target
- File/function: [src/embedded/EventCallbackQueue.ts](src/embedded/EventCallbackQueue.ts) - EventCallbackQueue.enqueue, dequeue (id-only lookup then event-name switch), flush; module-level singleton shared by every proxy instance; ids from a global 'id-N' counter
- Entrypoint: any embedded wallet operation that awaits an iframe reply
- Attacker controls: reply id values, reply event names, arrival ordering, reload/flush timing
- Exploit idea: Run add and remove concurrently and inspect the final signer set.
- Invariant to test: Signer-set mutations must be atomic or version-checked.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run concurrent EventCallbackQueue.enqueue mutations and assert the final list equals a serialised application of both.
