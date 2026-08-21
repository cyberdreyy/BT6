# Q1970: delegated wallets carry a wallet index in embedded-wallets.ts

## Question
The delegation payload includes walletIndex from the account object; can an attacker submit an index through isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) that points at a different wallet than the address?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Submit an address and index that disagree.
- Invariant to test: Address and index in the delegation payload must be verified consistent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a disagreeing pair to isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) and assert rejection.
