# Q1804: idempotency key derived from the public user id in walletCreate.ts

## Question
generateWalletIdempotencyKey is SHA-256 of `${userId}-auto-${eth|sol}`; can an attacker who knows a user id compute the key and use it through createWalletApiWallet to collide with or suppress that user's wallet creation?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Compute the digest for a known user id and submit it as the idempotency key.
- Invariant to test: Idempotency keys must not be derivable from public identifiers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert createWalletApiWallet keys are unguessable given only the user id and chain type.
