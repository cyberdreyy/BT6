# Q5571: builtin_programs_filter::check_program_kind — signature-details miscount

## Question
Can an unprivileged attacker, through a transaction submitted by an unprivileged fee-payer, reach `builtin_programs_filter::check_program_kind` and craft signature/precompile-signature counts so fee or verification accounting under-charges, so that the invariant "signature counts used for fees match the signatures actually present" is violated, leading to Loss of Funds?

## Target
- File/function: `compute-budget-instruction/src/builtin_programs_filter.rs` -> `check_program_kind`
- Entrypoint: a transaction submitted by an unprivileged fee-payer
- Attacker controls: the number of signatures and precompile sig entries in its transaction
- Exploit idea: Craft signature/precompile-signature counts so fee or verification accounting under-charges.
- Invariant to test: signature counts used for fees match the signatures actually present.
- Expected Immunefi impact: Loss of Funds — High
- Fast validation: write a unit/fuzz test decoding the crafted message and assert sanitized privileges == enforced privileges.
