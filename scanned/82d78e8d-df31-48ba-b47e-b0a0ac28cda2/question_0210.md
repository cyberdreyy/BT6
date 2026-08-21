# Q0210: imported flag flips the root in embedded-wallets.ts

## Question
getRootWallet returns the account itself when imported is true; can an attacker present an account object with imported set through every wallet-selection helper and delegation check so isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) treats an arbitrary wallet as its own root?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Pass a crafted account with imported true.
- Invariant to test: Account flags used for delegation must come from server-confirmed state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass {imported:true} on a crafted account to isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) and assert re-validation.
