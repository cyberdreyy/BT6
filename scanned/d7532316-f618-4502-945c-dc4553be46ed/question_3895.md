# Q3895: ping doubles as a liveness oracle in session-signers.ts

## Question
ping() invokes privy:iframe:ready with a caller-controlled timeout; can an attacker use addSessionSigners (getWallet then updateWallet with additional_signers.concat) to keep the ready state true while the iframe is actually serving a different session?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Flip the iframe session and observe the cached ready flag.
- Invariant to test: Readiness must be invalidated when the underlying wallet session changes.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: change the session and assert addSessionSigners (getWallet then updateWallet with additional_signers.concat) re-verifies readiness.
