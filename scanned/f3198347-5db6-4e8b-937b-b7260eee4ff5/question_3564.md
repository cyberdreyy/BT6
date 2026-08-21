# Q3564: solana create takes an ethereum account argument in walletCreate.ts

## Question
createSolana accepts an ethereumAccount whose provider is loaded first; can an attacker pass a foreign ethereum account through createWalletApiWallet so entropy from another wallet is used for the new Solana wallet?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Call createSolana with an ethereum account object that is not the user's.
- Invariant to test: Cross-chain wallet derivation must use only the authenticated user's own accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign ethereum account to createWalletApiWallet and assert rejection.
