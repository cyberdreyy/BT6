# Q0368: four attempts amplify code guessing in index.ts

## Question
The retry loop allows four attempts before max_attempts; can an attacker use throwIfInvalidRecoveryUpgradePath to obtain more verification attempts than the intended per-code budget by starting fresh operations?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Exhaust attempts, start a new operation, and count total submissions per code lifetime.
- Invariant to test: src/embedded/utils/index.ts must not let repeated operation starts multiply the MFA attempt budget.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run throwIfInvalidRecoveryUpgradePath repeatedly and assert the total submissions per issued code stay within the budget.
