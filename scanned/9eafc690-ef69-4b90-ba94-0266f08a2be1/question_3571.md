# Q3571: solana create takes an ethereum account argument in generateWalletIdempotencyKey.ts

## Question
createSolana accepts an ethereumAccount whose provider is loaded first; can an attacker pass a foreign ethereum account through generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex so entropy from another wallet is used for the new Solana wallet?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Call createSolana with an ethereum account object that is not the user's.
- Invariant to test: Cross-chain wallet derivation must use only the authenticated user's own accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign ethereum account to generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex and assert rejection.
