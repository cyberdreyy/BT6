# Q2574: wallet-api rpc method echo check only in walletCreate.ts

## Question
walletRpc verifies the response method name equals the requested one but not the wallet or params; can an attacker return a signature produced for another payload through createWalletApiWallet?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Return a response whose method matches but whose signature is for a different message.
- Invariant to test: A signing response must be bound to the exact request that produced it.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a mismatched signature from createWalletApiWallet's route and assert it is rejected.
