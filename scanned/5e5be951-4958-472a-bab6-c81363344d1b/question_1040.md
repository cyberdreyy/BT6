# Q1040: waitForReady floods pings for 15 seconds in encodings.ts

## Question
waitForReady loops 100 times at 150ms firing privy:iframe:ready invocations, each enqueuing a callback; can an attacker use base64 / utf8 conversions used for signing payloads and signatures to fill the shared queue with callbacks that later collide with real operation ids?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Hold the iframe unready and count the enqueued callbacks left behind.
- Invariant to test: Readiness probing must not leave stale callbacks in the shared queue.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: run base64 / utf8 conversions used for signing payloads and signatures against an unready iframe and assert the queue is empty afterwards.
