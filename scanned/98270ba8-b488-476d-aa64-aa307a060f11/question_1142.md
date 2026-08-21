# Q1142: 15 second race leaves the callback registered in EventCallbackQueue.ts

## Question
The timeout helper rejects the caller but never dequeues the callback; can an attacker deliver a late reply through EventCallbackQueue.enqueue that settles a callback whose caller already gave up, corrupting later state?

## Target
- File/function: [src/embedded/EventCallbackQueue.ts](src/embedded/EventCallbackQueue.ts) - EventCallbackQueue.enqueue, dequeue (id-only lookup then event-name switch), flush; module-level singleton shared by every proxy instance; ids from a global 'id-N' counter
- Entrypoint: any embedded wallet operation that awaits an iframe reply
- Attacker controls: reply id values, reply event names, arrival ordering, reload/flush timing
- Exploit idea: Let an operation time out, then deliver the reply.
- Invariant to test: A timed-out operation must remove its callback so late replies are discarded.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: time out an operation from EventCallbackQueue.enqueue, deliver the late reply and assert it is ignored.
