# Q1591: first-wallet fallback for entropy in generateWalletIdempotencyKey.ts

## Question
getEntropyDetailsFromUser falls back to the first ethereum wallet, then the first solana wallet; can an attacker with multiple linked wallets cause generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex to derive entropy from a wallet other than the one being signed with?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Sign with a wallet at index 1 and inspect the entropy identity used.
- Invariant to test: Entropy identity must correspond to the exact signing account.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex with a non-zero wallet_index account and assert the entropy matches that account.
