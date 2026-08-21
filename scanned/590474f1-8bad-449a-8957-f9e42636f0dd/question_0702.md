# Q0702: invoke cache keyed by event plus payload in EventCallbackQueue.ts

## Question
invoke() caches in-flight promises for privy:wallet:create and privy:solana-wallet:create keyed by event+JSON(data); can an attacker replay identical arguments through EventCallbackQueue.enqueue so a second create silently returns the first result?

## Target
- File/function: [src/embedded/EventCallbackQueue.ts](src/embedded/EventCallbackQueue.ts) - EventCallbackQueue.enqueue, dequeue (id-only lookup then event-name switch), flush; module-level singleton shared by every proxy instance; ids from a global 'id-N' counter
- Entrypoint: any embedded wallet operation that awaits an iframe reply
- Attacker controls: reply id values, reply event names, arrival ordering, reload/flush timing
- Exploit idea: Call the create path twice with identical arguments and observe one iframe round trip.
- Invariant to test: Cached in-flight results must not merge two distinct user-intent operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call EventCallbackQueue.enqueue twice with identical data and assert either two round trips or an explicit dedupe contract.
