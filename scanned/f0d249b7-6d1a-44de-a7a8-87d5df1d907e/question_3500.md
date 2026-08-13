# Q3500: lending_pool_configure_bank_emode: cross-group or cross-bank object passes role checks [replay-of-a-previously-valid] [rollback]

## Question
Can an unprivileged attacker supply replay of a previously valid emode-config layout under a new signer to `lending_pool_configure_bank_emode` so `lending_pool_configure_bank_emode` accepts a signer/object combination from the wrong group or bank, violating `emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions` and causing `Critical: public privilege escalation causing unsafe borrowing or liquidations`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs` / `lending_pool_configure_bank_emode`
- Entrypoint: `lending_pool_configure_bank_emode`
- Attacker controls: replay of a previously valid emode-config layout under a new signer
- Exploit idea: Probe whether role checks bind authority only to the signer and not also to the exact target object being mutated. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions
- Expected Immunefi impact: Critical: public privilege escalation causing unsafe borrowing or liquidations
- Fast validation: Create multiple groups/banks and assert authorized keys for one context cannot mutate another context through shared struct shape. Force the late failure branch and assert every protected field fully rolls back.
