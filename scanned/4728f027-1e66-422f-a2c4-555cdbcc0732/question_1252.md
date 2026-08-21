# Q1252: access token embedded in every proxy payload in EventCallbackQueue.ts

## Question
Every proxy call carries accessToken alongside entropyId and entropyIdVerifier; can an attacker observe or replay one of those payloads through EventCallbackQueue.enqueue to authorise a wallet operation later?

## Target
- File/function: [src/embedded/EventCallbackQueue.ts](src/embedded/EventCallbackQueue.ts) - EventCallbackQueue.enqueue, dequeue (id-only lookup then event-name switch), flush; module-level singleton shared by every proxy instance; ids from a global 'id-N' counter
- Entrypoint: any embedded wallet operation that awaits an iframe reply
- Attacker controls: reply id values, reply event names, arrival ordering, reload/flush timing
- Exploit idea: Capture a posted payload and replay it into the same interface.
- Invariant to test: Wallet operation payloads must not be replayable outside their original request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: replay a captured payload into EventCallbackQueue.enqueue and assert it is rejected.
