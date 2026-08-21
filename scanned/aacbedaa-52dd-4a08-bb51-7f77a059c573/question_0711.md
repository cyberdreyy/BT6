# Q0711: invoke cache keyed by event plus payload in generateWalletIdempotencyKey.ts

## Question
invoke() caches in-flight promises for privy:wallet:create and privy:solana-wallet:create keyed by event+JSON(data); can an attacker replay identical arguments through generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex so a second create silently returns the first result?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Call the create path twice with identical arguments and observe one iframe round trip.
- Invariant to test: Cached in-flight results must not merge two distinct user-intent operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex twice with identical data and assert either two round trips or an explicit dedupe contract.
