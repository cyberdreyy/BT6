# Q2852: selection used to authorise operations in getAllUserEmbeddedEthereumWallets.ts

## Question
Callers frequently pass the result of getAllUserEmbeddedEthereumWallets: filter embedded + ethereum straight into signing and delegation calls; can an attacker exploit the absence of a re-check so an account chosen at render time authorises an action later?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Select an account, change the session, then act.
- Invariant to test: Authorisation must re-derive the account at action time.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: change the session between selection from getAllUserEmbeddedEthereumWallets: filter embedded + ethereum and the action, and assert refusal.
