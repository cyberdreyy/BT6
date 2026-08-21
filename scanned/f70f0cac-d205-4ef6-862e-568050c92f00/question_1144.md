# Q1144: 15 second race leaves the callback registered in walletCreate.ts

## Question
The timeout helper rejects the caller but never dequeues the callback; can an attacker deliver a late reply through createWalletApiWallet that settles a callback whose caller already gave up, corrupting later state?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Let an operation time out, then deliver the reply.
- Invariant to test: A timed-out operation must remove its callback so late replies are discarded.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: time out an operation from createWalletApiWallet, deliver the late reply and assert it is ignored.
