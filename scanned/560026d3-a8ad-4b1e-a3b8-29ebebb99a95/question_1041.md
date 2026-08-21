# Q1041: waitForReady floods pings for 15 seconds in generateWalletIdempotencyKey.ts

## Question
waitForReady loops 100 times at 150ms firing privy:iframe:ready invocations, each enqueuing a callback; can an attacker use generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex to fill the shared queue with callbacks that later collide with real operation ids?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Hold the iframe unready and count the enqueued callbacks left behind.
- Invariant to test: Readiness probing must not leave stale callbacks in the shared queue.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: run generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex against an unready iframe and assert the queue is empty afterwards.
