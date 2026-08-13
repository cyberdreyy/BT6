# Q3531: lending_pool_configure_bank_emode: initializer-like path can be re-entered or retargeted [replay-of-a-previously-valid] [cross-object]

## Question
Can an unprivileged attacker call `lending_pool_configure_bank_emode` with replay of a previously valid emode-config layout under a new signer so `lending_pool_configure_bank_emode` re-initializes or retargets protected state that should be one-time or one-object-only, breaking `emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions` and causing `Critical: public privilege escalation causing unsafe borrowing or liquidations`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs` / `lending_pool_configure_bank_emode`
- Entrypoint: `lending_pool_configure_bank_emode`
- Attacker controls: replay of a previously valid emode-config layout under a new signer
- Exploit idea: Audit init/copy/clone paths for idempotence and one-time-use assumptions that are not fully enforced on-chain. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions
- Expected Immunefi impact: Critical: public privilege escalation causing unsafe borrowing or liquidations
- Fast validation: Repeat initialization/clone with attacker-controlled accounts and assert already-initialized or foreign-owned state cannot be hijacked. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
