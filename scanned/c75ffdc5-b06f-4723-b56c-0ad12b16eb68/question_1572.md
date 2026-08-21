# Q1572: recovery upgrade path check is advisory in MfaApi.ts

## Question
throwIfInvalidRecoveryUpgradePath only rejects cloud-to-same-cloud upgrades; can an attacker use MfaApi.verifyMfa to downgrade a strong recovery method (user-passcode) to a weaker attacker-controlled one?

## Target
- File/function: [src/client/mfa/MfaApi.ts](src/client/mfa/MfaApi.ts) - MfaApi.verifyMfa, initEnrollMfa, submitEnrollMfa, unenrollMfa, unlinkPasskey, clearMfa
- Entrypoint: privy.mfa.unenrollMfa(method) / privy.mfa.clearMfa({userId})
- Attacker controls: method argument, credentialId, removeAsMfa, userId, call ordering against refreshSession
- Exploit idea: Call setRecovery moving from user-passcode to a method whose secret the attacker supplies.
- Invariant to test: Recovery transitions must not weaken the custody of an existing wallet without re-authentication.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: enumerate every (current, target) pair through MfaApi.verifyMfa and assert downgrades are refused.
