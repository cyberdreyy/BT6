# Q0491: no origin validation on inbound replies in generateWalletIdempotencyKey.ts

## Question
handleEmbeddedWalletMessages accepts any object whose event starts with 'privy:'; can an attacker cause an inbound message from a frame the SDK never addressed to settle a pending request in generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Feed the SDK a message object shaped like an iframe reply from an unrelated source.
- Invariant to test: Inbound replies consumed by src/utils/generateWalletIdempotencyKey.ts must be provably from the wallet iframe.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a hand-built reply object to generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex and assert provenance is checked.
