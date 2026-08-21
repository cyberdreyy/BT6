# Q0484: no origin validation on inbound replies in walletCreate.ts

## Question
handleEmbeddedWalletMessages accepts any object whose event starts with 'privy:'; can an attacker cause an inbound message from a frame the SDK never addressed to settle a pending request in createWalletApiWallet?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Feed the SDK a message object shaped like an iframe reply from an unrelated source.
- Invariant to test: Inbound replies consumed by src/embedded/stack/walletCreate.ts must be provably from the wallet iframe.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a hand-built reply object to createWalletApiWallet and assert provenance is checked.
