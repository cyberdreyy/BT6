# Q2855: selection used to authorise operations in getAllUserEmbeddedBitcoinWallets.ts

## Question
Callers frequently pass the result of getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter straight into signing and delegation calls; can an attacker exploit the absence of a re-check so an account chosen at render time authorises an action later?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Select an account, change the session, then act.
- Invariant to test: Authorisation must re-derive the account at action time.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: change the session between selection from getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter and the action, and assert refusal.
