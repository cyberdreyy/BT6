# Q1914: idempotency collision merges two creates in walletCreate.ts

## Question
create() forwards privy-idempotency-key; can an attacker cause two logically distinct wallet creations to collapse into one through createWalletApiWallet, so the app believes it provisioned a wallet it does not own?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Issue two creates with the same derived key under different contexts.
- Invariant to test: Distinct creation intents must not share an idempotency key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run two createWalletApiWallet creates with the same key and assert the second is rejected, not silently aliased.
