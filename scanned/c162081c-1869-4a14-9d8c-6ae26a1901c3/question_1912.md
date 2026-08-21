# Q1912: idempotency collision merges two creates in EventCallbackQueue.ts

## Question
create() forwards privy-idempotency-key; can an attacker cause two logically distinct wallet creations to collapse into one through EventCallbackQueue.enqueue, so the app believes it provisioned a wallet it does not own?

## Target
- File/function: [src/embedded/EventCallbackQueue.ts](src/embedded/EventCallbackQueue.ts) - EventCallbackQueue.enqueue, dequeue (id-only lookup then event-name switch), flush; module-level singleton shared by every proxy instance; ids from a global 'id-N' counter
- Entrypoint: any embedded wallet operation that awaits an iframe reply
- Attacker controls: reply id values, reply event names, arrival ordering, reload/flush timing
- Exploit idea: Issue two creates with the same derived key under different contexts.
- Invariant to test: Distinct creation intents must not share an idempotency key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run two EventCallbackQueue.enqueue creates with the same key and assert the second is rejected, not silently aliased.
