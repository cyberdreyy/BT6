# Q3563: lending_pool_configure_bank_emode: config path trusts caller-chosen remaining accounts too much [replay-of-a-previously-valid] [cross-object]

## Question
Can an unprivileged attacker use `lending_pool_configure_bank_emode` with replay of a previously valid emode-config layout under a new signer so `lending_pool_configure_bank_emode` applies a protected configuration change using caller-chosen auxiliary accounts, violating `emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions` and leading to `Critical: public privilege escalation causing unsafe borrowing or liquidations`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs` / `lending_pool_configure_bank_emode`
- Entrypoint: `lending_pool_configure_bank_emode`
- Attacker controls: replay of a previously valid emode-config layout under a new signer
- Exploit idea: Look for config flows that pull oracle, metadata, or derived objects from remaining accounts without fully binding them. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions
- Expected Immunefi impact: Critical: public privilege escalation causing unsafe borrowing or liquidations
- Fast validation: Swap candidate auxiliary accounts and assert the config path cannot mutate anything unless every auxiliary object matches the canonical target. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
