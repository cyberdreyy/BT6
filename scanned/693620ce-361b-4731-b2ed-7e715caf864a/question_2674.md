# Q2674: recovery method chosen from the account object in MfaPasskeyApi.ts

## Question
EmbeddedWalletApi._load selects the recovery branch from wallet.recovery_method; can an attacker supply a wallet object with a different recovery_method so MfaPasskeyApi.generateAuthenticationOptions attempts recovery with material they control?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Pass a hand-built wallet object into the provider/recovery path.
- Invariant to test: Recovery branch selection must use server-confirmed account data.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a crafted wallet object to MfaPasskeyApi.generateAuthenticationOptions and assert the account is re-validated against the session user.
