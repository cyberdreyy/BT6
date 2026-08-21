# Q2676: recovery method chosen from the account object in RecoveryOAuthApi.ts

## Question
EmbeddedWalletApi._load selects the recovery branch from wallet.recovery_method; can an attacker supply a wallet object with a different recovery_method so RecoveryOAuthApi.generateURL attempts recovery with material they control?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Pass a hand-built wallet object into the provider/recovery path.
- Invariant to test: Recovery branch selection must use server-confirmed account data.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a crafted wallet object to RecoveryOAuthApi.generateURL and assert the account is re-validated against the session user.
