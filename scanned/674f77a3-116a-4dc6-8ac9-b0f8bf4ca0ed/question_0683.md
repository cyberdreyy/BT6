# Q683: find_bank_vault_authority_pda: vault PDA helper and transfer path disagree on the canonical vault [a-replay-of-previously-valid] [amount-domain]

## Question
Can an unprivileged attacker exploit a replay of previously valid authority accounts on another bank so `find_bank_vault_authority_pda` and a downstream transfer path disagree on the canonical vault address, violating `vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers` and causing `Critical: unauthorized withdrawal of protocol-controlled assets`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_authority_pda`
- Entrypoint: `juplend_withdraw`
- Attacker controls: a replay of previously valid authority accounts on another bank
- Exploit idea: A mismatch between utility derivation and runtime constraints can redirect value even if each piece seems locally correct. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers
- Expected Immunefi impact: Critical: unauthorized withdrawal of protocol-controlled assets
- Fast validation: Cross-check helper output against every consuming instruction and assert only one canonical vault/address is ever accepted. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
