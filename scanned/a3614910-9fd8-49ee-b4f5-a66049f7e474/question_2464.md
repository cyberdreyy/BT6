# Q2464: root wallet chosen by index order in walletCreate.ts

## Question
getRootWallet returns the first ethereum wallet, else the first solana wallet; can an attacker influence linked-account ordering so createWalletApiWallet delegates under a root wallet the user did not intend?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Construct a user with several embedded wallets and observe the root chosen.
- Invariant to test: Root-wallet selection must be explicit, not positional.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a user with multiple wallets and assert createWalletApiWallet requires an explicit root selection.
