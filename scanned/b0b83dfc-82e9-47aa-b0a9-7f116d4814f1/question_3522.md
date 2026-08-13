# Q3522: lending_pool_configure_bank_emode: initializer-like path can be re-entered or retargeted [an-attacker-signer-with-a] [rollback]

## Question
Can an unprivileged attacker call `lending_pool_configure_bank_emode` with an attacker signer with a victim bank and attacker-chosen emode entries so `lending_pool_configure_bank_emode` re-initializes or retargets protected state that should be one-time or one-object-only, breaking `emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions` and causing `Critical: public privilege escalation causing unsafe borrowing or liquidations`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs` / `lending_pool_configure_bank_emode`
- Entrypoint: `lending_pool_configure_bank_emode`
- Attacker controls: an attacker signer with a victim bank and attacker-chosen emode entries
- Exploit idea: Audit init/copy/clone paths for idempotence and one-time-use assumptions that are not fully enforced on-chain. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions
- Expected Immunefi impact: Critical: public privilege escalation causing unsafe borrowing or liquidations
- Fast validation: Repeat initialization/clone with attacker-controlled accounts and assert already-initialized or foreign-owned state cannot be hijacked. Force the late failure branch and assert every protected field fully rolls back.
