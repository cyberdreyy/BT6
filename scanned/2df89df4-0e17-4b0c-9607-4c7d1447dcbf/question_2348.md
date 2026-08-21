# Q2348: rate limit detection by message substring in index.ts

## Question
errorIndicatesMfaRateLimit matches 'code 429' inside the message; can an attacker craft an error message containing that substring so throwIfInvalidRecoveryUpgradePath takes the rate-limited branch and suppresses a real failure?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Return an error whose message embeds the substring.
- Invariant to test: Control-flow decisions must not depend on substring matching of error messages.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return an error message containing 'code 429' from a different cause and assert throwIfInvalidRecoveryUpgradePath does not treat it as rate limiting.
