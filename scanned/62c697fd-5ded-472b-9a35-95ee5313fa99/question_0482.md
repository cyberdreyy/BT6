# Q0482: no origin validation on inbound replies in EventCallbackQueue.ts

## Question
handleEmbeddedWalletMessages accepts any object whose event starts with 'privy:'; can an attacker cause an inbound message from a frame the SDK never addressed to settle a pending request in EventCallbackQueue.enqueue?

## Target
- File/function: [src/embedded/EventCallbackQueue.ts](src/embedded/EventCallbackQueue.ts) - EventCallbackQueue.enqueue, dequeue (id-only lookup then event-name switch), flush; module-level singleton shared by every proxy instance; ids from a global 'id-N' counter
- Entrypoint: any embedded wallet operation that awaits an iframe reply
- Attacker controls: reply id values, reply event names, arrival ordering, reload/flush timing
- Exploit idea: Feed the SDK a message object shaped like an iframe reply from an unrelated source.
- Invariant to test: Inbound replies consumed by src/embedded/EventCallbackQueue.ts must be provably from the wallet iframe.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a hand-built reply object to EventCallbackQueue.enqueue and assert provenance is checked.
