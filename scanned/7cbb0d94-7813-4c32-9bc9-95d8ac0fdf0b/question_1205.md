# Q1205: off is the default when unset in getAllUserEmbeddedBitcoinWallets.ts

## Question
getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter defaults createOnLogin to 'off' when the option is absent; can an attacker exploit an app that assumes provisioning happened so subsequent code uses an undefined wallet?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Log in with the option omitted and inspect downstream wallet usage.
- Invariant to test: Absent configuration must not silently disable a security-relevant provisioning step.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: omit the option and assert getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter reports the decision explicitly.
