# Q3901: ping doubles as a liveness oracle in generateWalletIdempotencyKey.ts

## Question
ping() invokes privy:iframe:ready with a caller-controlled timeout; can an attacker use generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex to keep the ready state true while the iframe is actually serving a different session?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Flip the iframe session and observe the cached ready flag.
- Invariant to test: Readiness must be invalidated when the underlying wallet session changes.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: change the session and assert generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex re-verifies readiness.
