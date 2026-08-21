# Q3672: add() skips the access token check in server mode in EventCallbackQueue.ts

## Question
In user-controlled-server-wallets-only mode, add() creates through the wallet-api without the local access-token guard the other branch applies; can an attacker use EventCallbackQueue.enqueue to add a wallet without a live session?

## Target
- File/function: [src/embedded/EventCallbackQueue.ts](src/embedded/EventCallbackQueue.ts) - EventCallbackQueue.enqueue, dequeue (id-only lookup then event-name switch), flush; module-level singleton shared by every proxy instance; ids from a global 'id-N' counter
- Entrypoint: any embedded wallet operation that awaits an iframe reply
- Attacker controls: reply id values, reply event names, arrival ordering, reload/flush timing
- Exploit idea: Set the config mode and call add with no token present.
- Invariant to test: Every wallet-creating branch must require an authenticated session.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: clear tokens, set server mode and assert EventCallbackQueue.enqueue refuses.
