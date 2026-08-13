# Q3475: lending_pool_configure_bank_emode: partial config application survives a later authorization failure [two-banks-where-emode-entries] [cross-object]

## Question
Can an unprivileged attacker make `lending_pool_configure_bank_emode` reach `lending_pool_configure_bank_emode` with two banks where emode entries can be cross-applied by shape only so some protected fields are applied before a later auth/binding failure, breaking `emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions` and leading to `Critical: public privilege escalation causing unsafe borrowing or liquidations`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs` / `lending_pool_configure_bank_emode`
- Entrypoint: `lending_pool_configure_bank_emode`
- Attacker controls: two banks where emode entries can be cross-applied by shape only
- Exploit idea: Check for multi-field updates where validation may be interleaved with mutation instead of fully front-loaded. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions
- Expected Immunefi impact: Critical: public privilege escalation causing unsafe borrowing or liquidations
- Fast validation: Force the late failure branch and assert every protected field remains unchanged after rollback. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
