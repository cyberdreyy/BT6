# Q3118: proxy may be unset when mfa is required in index.ts

## Question
MfaApi throws embedded_wallet_webview_not_loaded when proxy is absent; can an attacker arrange for the proxy to be missing so throwIfInvalidRecoveryUpgradePath fails open in the app's error handling?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Call the MFA path before the message poster is set and inspect the error class used by the app.
- Invariant to test: A missing proxy must be an unambiguous hard failure for MFA-gated operations.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call throwIfInvalidRecoveryUpgradePath without a proxy and assert the error cannot be confused with a benign outcome.
