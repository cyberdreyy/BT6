# Q1582: first-wallet fallback for entropy in EventCallbackQueue.ts

## Question
getEntropyDetailsFromUser falls back to the first ethereum wallet, then the first solana wallet; can an attacker with multiple linked wallets cause EventCallbackQueue.enqueue to derive entropy from a wallet other than the one being signed with?

## Target
- File/function: [src/embedded/EventCallbackQueue.ts](src/embedded/EventCallbackQueue.ts) - EventCallbackQueue.enqueue, dequeue (id-only lookup then event-name switch), flush; module-level singleton shared by every proxy instance; ids from a global 'id-N' counter
- Entrypoint: any embedded wallet operation that awaits an iframe reply
- Attacker controls: reply id values, reply event names, arrival ordering, reload/flush timing
- Exploit idea: Sign with a wallet at index 1 and inspect the entropy identity used.
- Invariant to test: Entropy identity must correspond to the exact signing account.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call EventCallbackQueue.enqueue with a non-zero wallet_index account and assert the entropy matches that account.
