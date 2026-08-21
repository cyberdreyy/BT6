# Q1151: 15 second race leaves the callback registered in generateWalletIdempotencyKey.ts

## Question
The timeout helper rejects the caller but never dequeues the callback; can an attacker deliver a late reply through generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex that settles a callback whose caller already gave up, corrupting later state?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Let an operation time out, then deliver the reply.
- Invariant to test: A timed-out operation must remove its callback so late replies are discarded.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: time out an operation from generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex, deliver the late reply and assert it is ignored.
