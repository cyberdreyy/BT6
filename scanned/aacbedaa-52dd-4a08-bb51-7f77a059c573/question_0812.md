# Q0812: bigint and undefined fields collapse the cache key in EventCallbackQueue.ts

## Question
The cache key is built with JSON.stringify, which drops undefined values and functions; can an attacker craft two different payloads that produce the same key inside EventCallbackQueue.enqueue?

## Target
- File/function: [src/embedded/EventCallbackQueue.ts](src/embedded/EventCallbackQueue.ts) - EventCallbackQueue.enqueue, dequeue (id-only lookup then event-name switch), flush; module-level singleton shared by every proxy instance; ids from a global 'id-N' counter
- Entrypoint: any embedded wallet operation that awaits an iframe reply
- Attacker controls: reply id values, reply event names, arrival ordering, reload/flush timing
- Exploit idea: Pass payloads differing only by an undefined field and observe the shared cache entry.
- Invariant to test: Cache keys must be injective over the payloads they represent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert EventCallbackQueue.enqueue produces different keys for payloads differing only in undefined-valued fields.
