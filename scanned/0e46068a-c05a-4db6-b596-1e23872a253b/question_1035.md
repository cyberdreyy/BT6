# Q1035: waitForReady floods pings for 15 seconds in session-signers.ts

## Question
waitForReady loops 100 times at 150ms firing privy:iframe:ready invocations, each enqueuing a callback; can an attacker use addSessionSigners (getWallet then updateWallet with additional_signers.concat) to fill the shared queue with callbacks that later collide with real operation ids?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Hold the iframe unready and count the enqueued callbacks left behind.
- Invariant to test: Readiness probing must not leave stale callbacks in the shared queue.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: run addSessionSigners (getWallet then updateWallet with additional_signers.concat) against an unready iframe and assert the queue is empty afterwards.
