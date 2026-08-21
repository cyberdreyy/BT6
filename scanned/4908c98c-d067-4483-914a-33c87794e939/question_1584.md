# Q1584: first-wallet fallback for entropy in walletCreate.ts

## Question
getEntropyDetailsFromUser falls back to the first ethereum wallet, then the first solana wallet; can an attacker with multiple linked wallets cause createWalletApiWallet to derive entropy from a wallet other than the one being signed with?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Sign with a wallet at index 1 and inspect the entropy identity used.
- Invariant to test: Entropy identity must correspond to the exact signing account.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call createWalletApiWallet with a non-zero wallet_index account and assert the entropy matches that account.
