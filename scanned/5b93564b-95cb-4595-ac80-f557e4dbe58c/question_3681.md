# Q3681: add() skips the access token check in server mode in generateWalletIdempotencyKey.ts

## Question
In user-controlled-server-wallets-only mode, add() creates through the wallet-api without the local access-token guard the other branch applies; can an attacker use generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex to add a wallet without a live session?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Set the config mode and call add with no token present.
- Invariant to test: Every wallet-creating branch must require an authenticated session.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: clear tokens, set server mode and assert generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex refuses.
