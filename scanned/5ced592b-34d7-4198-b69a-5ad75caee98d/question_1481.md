# Q1481: entropyId is just the wallet address in generateWalletIdempotencyKey.ts

## Question
getEntropyDetailsFromAccount uses the account address as the entropyId; can an attacker pass an address they merely know through generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex and cause the iframe to load or recover the wrong wallet?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Call the provider path with a foreign address as entropyId.
- Invariant to test: Entropy identifiers must be validated against the authenticated user's own accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign address into generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex and assert it is rejected before the proxy call.
