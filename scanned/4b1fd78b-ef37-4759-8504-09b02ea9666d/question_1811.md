# Q1811: idempotency key derived from the public user id in generateWalletIdempotencyKey.ts

## Question
generateWalletIdempotencyKey is SHA-256 of `${userId}-auto-${eth|sol}`; can an attacker who knows a user id compute the key and use it through generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex to collide with or suppress that user's wallet creation?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Compute the digest for a known user id and submit it as the idempotency key.
- Invariant to test: Idempotency keys must not be derivable from public identifiers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex keys are unguessable given only the user id and chain type.
