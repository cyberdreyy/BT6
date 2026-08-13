# Q3554: lending_pool_configure_bank_emode: config path trusts caller-chosen remaining accounts too much [an-attacker-signer-with-a] [rollback]

## Question
Can an unprivileged attacker use `lending_pool_configure_bank_emode` with an attacker signer with a victim bank and attacker-chosen emode entries so `lending_pool_configure_bank_emode` applies a protected configuration change using caller-chosen auxiliary accounts, violating `emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions` and leading to `Critical: public privilege escalation causing unsafe borrowing or liquidations`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs` / `lending_pool_configure_bank_emode`
- Entrypoint: `lending_pool_configure_bank_emode`
- Attacker controls: an attacker signer with a victim bank and attacker-chosen emode entries
- Exploit idea: Look for config flows that pull oracle, metadata, or derived objects from remaining accounts without fully binding them. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions
- Expected Immunefi impact: Critical: public privilege escalation causing unsafe borrowing or liquidations
- Fast validation: Swap candidate auxiliary accounts and assert the config path cannot mutate anything unless every auxiliary object matches the canonical target. Force the late failure branch and assert every protected field fully rolls back.
