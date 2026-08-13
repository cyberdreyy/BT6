# Q3584: lending_pool_configure_bank_emode: clone or copy helper can duplicate privileged state into the wrong object [a-bank-with-existing-emode] [rollback]

## Question
Can an unprivileged attacker make `lending_pool_configure_bank_emode` reach `lending_pool_configure_bank_emode` with a bank with existing emode-dependent user positions already live so protected state is cloned or copied into the wrong destination, violating `emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions` and causing `Critical: public privilege escalation causing unsafe borrowing or liquidations`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs` / `lending_pool_configure_bank_emode`
- Entrypoint: `lending_pool_configure_bank_emode`
- Attacker controls: a bank with existing emode-dependent user positions already live
- Exploit idea: Attack any helper that duplicates config, fee state, emode, or metadata across objects and must bind source and destination tightly. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions
- Expected Immunefi impact: Critical: public privilege escalation causing unsafe borrowing or liquidations
- Fast validation: Use mixed-validity source/destination objects and assert no protected state lands on an attacker-selected destination. Force the late failure branch and assert every protected field fully rolls back.
