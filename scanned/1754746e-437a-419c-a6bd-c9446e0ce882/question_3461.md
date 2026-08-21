# Q3461: wallet create returns before the user is refreshed in generateWalletIdempotencyKey.ts

## Question
create()/add() call refreshSession after the iframe returns; can an attacker interleave a session change through generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex so the created wallet is attributed to a different user object?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Change the active user between the iframe result and the refresh.
- Invariant to test: Wallet creation results must be attributed to the identity that requested them.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: switch users mid-call in generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex and assert the operation aborts.
