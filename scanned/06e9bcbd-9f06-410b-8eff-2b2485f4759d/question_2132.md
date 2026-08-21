# Q2132: signer list concatenated without validation in EventCallbackQueue.ts

## Question
addSessionSigners concatenates the caller's signers array onto the existing list with no dedupe or ownership check; can an attacker add a signer key they control through EventCallbackQueue.enqueue?

## Target
- File/function: [src/embedded/EventCallbackQueue.ts](src/embedded/EventCallbackQueue.ts) - EventCallbackQueue.enqueue, dequeue (id-only lookup then event-name switch), flush; module-level singleton shared by every proxy instance; ids from a global 'id-N' counter
- Entrypoint: any embedded wallet operation that awaits an iframe reply
- Attacker controls: reply id values, reply event names, arrival ordering, reload/flush timing
- Exploit idea: Call the add path with an attacker-held signer entry.
- Invariant to test: Session signers must be validated and require explicit user approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an arbitrary signer to EventCallbackQueue.enqueue and assert an approval gate is enforced.
