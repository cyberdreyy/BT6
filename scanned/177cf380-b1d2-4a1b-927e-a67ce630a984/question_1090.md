# Q1090: chain type restricted to two values in embedded-wallets.ts

## Question
delegateWallet only permits ethereum and solana; can an attacker pass a chainType through isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) that matches a wallet of a different chain family with the same address form?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Pass 'ethereum' for a wallet that is actually on another EVM-like family.
- Invariant to test: Chain type must be taken from the wallet record, not the argument.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: cross chainType and wallet in isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) and assert rejection.
