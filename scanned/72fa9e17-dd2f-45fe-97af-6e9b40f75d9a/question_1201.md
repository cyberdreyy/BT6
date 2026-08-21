# Q1201: off is the default when unset in getUserEmbeddedEthereumWallet.ts

## Question
getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 defaults createOnLogin to 'off' when the option is absent; can an attacker exploit an app that assumes provisioning happened so subsequent code uses an undefined wallet?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Log in with the option omitted and inspect downstream wallet usage.
- Invariant to test: Absent configuration must not silently disable a security-relevant provisioning step.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: omit the option and assert getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 reports the decision explicitly.
