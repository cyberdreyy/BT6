# Q1034: waitForReady floods pings for 15 seconds in walletCreate.ts

## Question
waitForReady loops 100 times at 150ms firing privy:iframe:ready invocations, each enqueuing a callback; can an attacker use createWalletApiWallet to fill the shared queue with callbacks that later collide with real operation ids?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Hold the iframe unready and count the enqueued callbacks left behind.
- Invariant to test: Readiness probing must not leave stale callbacks in the shared queue.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: run createWalletApiWallet against an unready iframe and assert the queue is empty afterwards.
