# Q1468: passkey mfa options echo caller fields in index.ts

## Question
MfaPasskeyApi.generateAuthenticationOptions forwards the caller's input; can an attacker set relying-party or allowed-credential fields so the MFA ceremony accepts a credential they control?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Call throwIfInvalidRecoveryUpgradePath with crafted options and inspect the ceremony parameters returned.
- Invariant to test: MFA ceremony parameters must be derived server-side from the enrolled credentials.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass crafted options to throwIfInvalidRecoveryUpgradePath and assert they are not forwarded.
