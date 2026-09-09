import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'ethereum/consensus-specs'
# todo: the name of the repository
REPO_NAME = 'consensus-specs'

run_number = os.environ.get('GITHUB_RUN_NUMBER', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"


scope_files = [
    # =================================================================================
    # LENS: STATE TRANSITION, FORK CHOICE AND VALIDATOR ACCOUNTING (Ethereum consensus
    # specs). The specs are markdown; the Python in each ```python block is the
    # executable spec that every client must match. Untrusted input enters through what
    # an unprivileged participant can put on chain or on the wire with its own keys:
    # execution-layer requests (deposit, withdrawal, consolidation, builder deposit and
    # exit), signed blocks and payload envelopes for slots it is assigned, attestations,
    # slashings, exits, BLS-to-execution changes, payload attestations, inclusion lists
    # and sync/light-client messages. The files below sit on the path from those inputs
    # to one of five decisions: is every Gwei conserved and paid to the right address, is
    # every change to a validator or builder authorised by its own key, do all honest
    # nodes compute one head and one finalized checkpoint, is only an equivocator ever
    # slashable, and is the payload executed the payload the block committed to and paid
    # for once. A question belongs here only if it can be closed by an equality between a
    # value the participant supplied and a value the spec produced.
    # =================================================================================

    # -- phase0: base state transition, fork choice, deposit contract, p2p, validator ----
    "specs/phase0/beacon-chain.md",
    "specs/phase0/deposit-contract.md",
    "specs/phase0/fast-confirmation.md",
    "specs/phase0/fork-choice.md",
    "specs/phase0/p2p-interface.md",
    "specs/phase0/validator.md",
    "specs/phase0/weak-subjectivity.md",

    # -- altair: participation flags, sync committees, light client ---------------------
    "specs/altair/beacon-chain.md",
    "specs/altair/bls.md",
    "specs/altair/fork-choice.md",
    "specs/altair/fork.md",
    "specs/altair/light-client/full-node.md",
    "specs/altair/light-client/light-client.md",
    "specs/altair/light-client/p2p-interface.md",
    "specs/altair/light-client/sync-protocol.md",
    "specs/altair/p2p-interface.md",
    "specs/altair/validator.md",

    # -- bellatrix: execution payload, optimistic sync -----------------------------------
    "specs/bellatrix/beacon-chain.md",
    "specs/bellatrix/fast-confirmation.md",
    "specs/bellatrix/fork-choice.md",
    "specs/bellatrix/fork.md",
    "specs/bellatrix/optimistic-sync.md",
    "specs/bellatrix/p2p-interface.md",
    "specs/bellatrix/validator.md",

    # -- capella: withdrawals, BLS-to-execution changes ---------------------------------
    "specs/capella/beacon-chain.md",
    "specs/capella/fork-choice.md",
    "specs/capella/fork.md",
    "specs/capella/light-client/fork.md",
    "specs/capella/light-client/full-node.md",
    "specs/capella/light-client/p2p-interface.md",
    "specs/capella/light-client/sync-protocol.md",
    "specs/capella/p2p-interface.md",
    "specs/capella/validator.md",

    # -- deneb: blobs, KZG commitments, blob sidecars -----------------------------------
    "specs/deneb/beacon-chain.md",
    "specs/deneb/fork-choice.md",
    "specs/deneb/fork.md",
    "specs/deneb/light-client/fork.md",
    "specs/deneb/light-client/full-node.md",
    "specs/deneb/light-client/p2p-interface.md",
    "specs/deneb/light-client/sync-protocol.md",
    "specs/deneb/p2p-interface.md",
    "specs/deneb/validator.md",

    # -- electra: execution requests, consolidations, pending deposits/withdrawals ------
    "specs/electra/beacon-chain.md",
    "specs/electra/fork.md",
    "specs/electra/light-client/fork.md",
    "specs/electra/light-client/p2p-interface.md",
    "specs/electra/light-client/sync-protocol.md",
    "specs/electra/p2p-interface.md",
    "specs/electra/validator.md",
    "specs/electra/weak-subjectivity.md",

    # -- fulu: PeerDAS, custody, proposer lookahead, blob schedule ----------------------
    "specs/fulu/beacon-chain.md",
    "specs/fulu/das-core.md",
    "specs/fulu/fork-choice.md",
    "specs/fulu/fork.md",
    "specs/fulu/p2p-interface.md",
    "specs/fulu/partial-columns/p2p-interface.md",
    "specs/fulu/validator.md",

    # -- gloas: ePBS - builders, bids, envelopes, PTC, payload-aware fork choice --------
    "specs/gloas/beacon-chain.md",
    "specs/gloas/builder.md",
    "specs/gloas/fast-confirmation.md",
    "specs/gloas/fork-choice.md",
    "specs/gloas/fork.md",
    "specs/gloas/light-client/fork.md",
    "specs/gloas/light-client/full-node.md",
    "specs/gloas/light-client/p2p-interface.md",
    "specs/gloas/light-client/sync-protocol.md",
    "specs/gloas/p2p-interface.md",
    "specs/gloas/partial-columns/p2p-interface.md",
    "specs/gloas/validator.md",
    "specs/gloas/weak-subjectivity.md",

    # -- heze: FOCIL inclusion lists ----------------------------------------------------
    "specs/heze/beacon-chain.md",
    "specs/heze/builder.md",
    "specs/heze/fork-choice.md",
    "specs/heze/fork.md",
    "specs/heze/inclusion-list.md",
    "specs/heze/optimistic-sync.md",
    "specs/heze/p2p-interface.md",
    "specs/heze/validator.md",

    # -- _features: draft EIPs layered on the forks above -------------------------------
    "specs/_features/eip8025/beacon-chain.md",
    "specs/_features/eip8025/fork-choice.md",
    "specs/_features/eip8025/p2p-interface.md",
    "specs/_features/eip8025/proof-engine.md",
    "specs/_features/eip8025/prover.md",
    "specs/_features/eip8148/beacon-chain.md",
    "specs/_features/eip8148/fork.md",
    "specs/_features/eip8148/p2p-interface.md",
    "specs/_features/eip8148/validator.md",
    "specs/_features/eip8205/beacon-chain.md",
    "specs/_features/eip8205/fork.md",
    "specs/_features/eip8205/p2p-interface.md",
    "specs/_features/eip8205/validator.md",
    "specs/_features/eip8321/beacon-chain.md",
    "specs/_features/eip8321/fork.md",
    "specs/_features/eip8321/p2p-interface.md",
    "specs/_features/eip8321/validator.md",

    # =================================================================================
    # NOT AUDITED (excluded from every variant): tests/ (pyspec tests, generators,
    # formats), the generated Python spec packages, pysetup/ and scripts/ (the spec
    # builder), presets/ and configs/ yaml, Makefile, pyproject.toml, uv.lock,
    # zensical.toml, renovate.json, README, SECURITY.md and docs tooling. A defect in any
    # of these is only in scope when it is reachable from the spec text above.
    # =================================================================================
]


target_scopes = [
    "Critical. EVERY GWEI MUST BE CONSERVED AND LAND WHERE ITS OWNER'S CREDENTIALS SAY. `process_withdrawal_request` caps `to_withdraw` at `balance - MIN_ACTIVATION_BALANCE - pending_balance_to_withdraw` and calls `compute_exit_epoch_and_update_churn`; `process_consolidation_request` exits the source through `compute_consolidation_epoch_and_update_churn` and appends a `PendingConsolidation`; `process_pending_consolidations` moves the source balance to the target via `switch_to_compounding_validator` and `queue_excess_active_balance`; `apply_pending_deposit` skips signature checks for top-ups; `process_pending_deposits` gates on `get_activation_exit_churn_limit`, `deposit_balance_to_consume` and `is_valid_deposit_signature`; `get_expected_withdrawals` pairs `get_pending_partial_withdrawals` with the sweep and `process_withdrawals` asserts them against `payload.withdrawals`; `initiate_validator_exit` and `slash_validator` set `withdrawable_epoch`. Probe every path where an execution-layer request from an EOA or an on-chain operation from a validator's own key moves a Gwei that is not that validator's, moves it twice, or moves it nowhere: a partial withdrawal of the same balance queued by consolidation and withdrawal request in one block; a consolidation whose source is slashed or exited between queueing and processing; a top-up deposit to a pubkey with foreign withdrawal credentials; a full-exit request whose pending withdrawals drain below `MIN_ACTIVATION_BALANCE` after exit; a sweep withdrawal and a pending partial withdrawal paying the same balance. Identity: sum of `state.balances` + `state.builders[*].balance` + queued pending deposits/withdrawals/payments + emitted `Withdrawal.amount` after the transition == the same sum before plus deposits and rewards minus penalties, and each `Withdrawal.address` == the 20 bytes in that validator's `withdrawal_credentials`.",

    "Critical. NO STATE CHANGE TO A VALIDATOR OR BUILDER WITHOUT THAT PARTY'S KEY OR WITHDRAWAL ADDRESS. `process_withdrawal_request` and `process_consolidation_request` authorise by `withdrawal_credentials[12:] == source_address`; `is_valid_switch_to_compounding_request` requires source == target; `process_bls_to_execution_change` binds `from_bls_pubkey` to the 0x00 credential; `process_voluntary_exit` and `process_proposer_slashing` verify over `DOMAIN_VOLUNTARY_EXIT` / `DOMAIN_BEACON_PROPOSER`; `apply_deposit` verifies `is_valid_deposit_signature` only for new pubkeys under `compute_domain(DOMAIN_DEPOSIT)` with no fork version; `process_builder_deposit_request` registers on `is_valid_builder_deposit_signature` (`DOMAIN_BUILDER_DEPOSIT`) and top-ups an existing builder pubkey with no signature; `process_builder_exit_request` authorises by `execution_address == source_address`; `get_index_for_new_builder` reuses indices of exited, swept builders; `convert_builder_index_to_validator_index` maps builders into the validator index space used by `Withdrawal.validator_index`. Show a request or operation, built only from the attacker's own keys and addresses, that exits, consolidates, changes credentials of, deposits into, or withdraws from an account the attacker does not control, or that resurrects an index: a builder deposit for an exited pubkey whose index is now reassigned; a validator deposit signed at genesis fork replayed on another network sharing `GENESIS_FORK_VERSION`; a consolidation targeting a validator whose credentials were switched in the same block; a BLS-to-execution change accepted for a validator already holding 0x01/0x02 credentials; a `Withdrawal.validator_index` that collides between a builder and a validator. Identity: for every field of `state.validators[i]`, `state.balances[i]`, `state.builders[j]` that differs after the transition, the input that changed it carries a valid signature from that party's pubkey or comes from that party's `withdrawal_credentials[12:]` / `execution_address`.",

    "Critical. A BUILDER PAYS EXACTLY ITS WINNING BID, ONCE, TO THE PROPOSER THAT INCLUDED IT. `process_execution_payload_bid` asserts `can_builder_cover_bid` (balance minus `MIN_DEPOSIT_AMOUNT` minus `get_pending_balance_to_withdraw_for_builder`), checks `bid.slot`, `parent_block_hash`, `parent_block_root`, `prev_randao`, then writes a `BuilderPendingPayment` at `SLOTS_PER_EPOCH + slot % SLOTS_PER_EPOCH`; `process_attestation` accumulates `weight` on that payment; `process_builder_pending_payments` settles payments above `get_builder_payment_quorum_threshold` through `settle_builder_payment` into `builder_pending_withdrawals`; `get_builder_withdrawals` and `get_builders_sweep_withdrawals` emit them with `MAX_WITHDRAWALS_PER_PAYLOAD - 1` limits; `process_builder_exit_request` refuses exit while pending balance is non-zero; `initiate_builder_exit` sets `withdrawable_epoch` without zeroing pending payments; `BUILDER_INDEX_SELF_BUILD` bids must be zero-valued with an infinity signature. Show a builder, proposer or attester using only its own stake that gets paid without delivering, pays without winning, pays twice, pays a different proposer, or escapes a payment: a bid whose payment slot index is overwritten by a later bid in the same epoch window; a payment settled when the payload was never revealed and `execution_payload_availability` stayed false; an exited builder's pending payment surviving the sweep so a later builder at the reused index pays it; `can_builder_cover_bid` passing while the same balance backs two bids across consecutive slots; a proposer including its own builder's bid with `fee_recipient` pointed elsewhere. Identity: for each `BuilderPendingWithdrawal` emitted, there exists exactly one `ExecutionPayloadBid` with equal `builder_index`, `value` and `fee_recipient` whose envelope was revealed and attested for that slot, and no builder balance ever drops below `MIN_DEPOSIT_AMOUNT` plus its pending obligations.",

    "Critical. THE PAYLOAD EXECUTED MUST BE THE PAYLOAD THE BLOCK COMMITTED TO, AND EMPTY SLOTS MUST STAY EMPTY. `verify_execution_payload_envelope` and `verify_execution_payload_envelope_signature` bind the envelope to `latest_execution_payload_bid` (block_hash, builder_index, blob commitments) and to `beacon_block_root`; `process_parent_execution_payload` / `apply_parent_execution_payload` replay the parent's envelope in the child block, updating `latest_block_hash`, `execution_payload_availability`, `payload_expected_withdrawals` and `execution_requests`; `process_slot` unsets availability for the next slot; `update_payload_expected_withdrawals` and `process_withdrawals` compare the expected list against what the payload paid; `get_execution_requests_list` orders deposit, withdrawal, consolidation, builder deposit and builder exit requests; `on_execution_payload_envelope` stores `store.payloads[beacon_block_root]` after `is_data_available`; `is_data_available` and `get_custody_column_bits` decide availability from sampled columns. Show a builder or proposer using its own slot that gets a different payload, a different set of withdrawals, a different set of execution requests, or two payloads accepted for one block, or a stale payload carried into a slot that was empty: an envelope whose `execution_requests` differ from what the bid committed while the signature still verifies; withdrawals computed in the bid slot but applied in the child under different state; a self-build envelope with `BUILDER_INDEX_SELF_BUILD` revealing a payload the proposer never bid; `latest_block_hash` advancing on a payload whose `parent_block_hash` was for another branch; execution requests processed twice across parent and child. Identity: (block_hash, withdrawals_root, execution_requests, blob commitments) applied by `process_parent_execution_payload` == the same fields in the unique `ExecutionPayloadEnvelope` whose `beacon_block_root` is the parent's root and whose bid the parent's `process_execution_payload_bid` accepted, and `execution_payload_availability[slot]` is true exactly when that envelope was applied.",

    "Critical. EVERY HONEST NODE MUST COMPUTE ONE HEAD FROM ONE SET OF VOTES. `get_head` walks `get_node_children` where each block root now splits into FULL and EMPTY `ForkChoiceNode`s ranked by `get_weight` and `get_payload_status_tiebreaker`; `get_ancestor`, `is_ancestor` and `get_checkpoint_block` compare `(root, payload_status)`; `should_apply_proposer_boost`, `is_head_weak`, `is_parent_strong`, `get_proposer_head` and `should_build_on_full` / `should_extend_payload` decide reorgs; `validate_on_attestation` and `update_latest_messages` accept an attester's `AttestationData.index` as a payload-availability vote with `is_attestation_same_slot`; `on_payload_attestation_message` and `notify_ptc_messages` count PTC votes per `data.beacon_block_root`; `record_block_timeliness` and `update_proposer_boost_root` set timeliness. Show a single validator, PTC member or proposer, casting only messages its keys allow at times of its choosing, that makes two honest nodes following the spec disagree on `get_head`, or makes one node's head flip without new majority votes: an attestation whose `index` bit votes FULL for a block whose payload arrives after the vote; a PTC message counted for a block at another slot; a latest message updated by an attestation for an ancestor with a different payload status than the one already recorded; proposer boost applied to a child whose parent is EMPTY while `should_build_on_full` said otherwise; `get_checkpoint_block` resolving one root to two nodes. Identity: for any two `Store`s that received the same set of blocks, envelopes, attestations and PTC messages in any order, `get_head(store)` returns the same `(root, payload_status)`, and every unit of `get_weight` traces to one distinct validator's latest message.",

    "High. ONLY AN EQUIVOCATOR IS EVER SLASHABLE. `is_slashable_attestation_data` (double vote, surround vote) feeds `process_attester_slashing`, which now runs `is_valid_indexed_attestation` over Gloas `Attestation` with the `index` field repurposed as payload availability and `get_attesting_indices` reading committee bits; `is_attestation_same_slot` decides which flag indices an attestation earns; `process_proposer_slashing` compares two `BeaconBlockHeader`s at one slot; `is_valid_indexed_payload_attestation` verifies PTC messages under `DOMAIN_PTC_ATTESTER`; `process_inclusion_list` marks `store.equivocators[key]` on any differing `InclusionList` for the same `(slot, dependent_root, validator_index)`; `slash_validator` sets `slashed`, applies the penalty and the whistleblower reward; `process_slashings` scales penalties by total slashed balance. Show a validator that follows validator.md exactly and still becomes slashable, loses balance, or is excluded as an equivocator because of what another unprivileged participant did: two attestations that differ only in the payload-availability `index` yet share slot, source and target; an honest re-broadcast of an inclusion list with a different `dependent_root` counted as equivocation; a PTC vote and a beacon attestation from the same key at the same slot treated as a double vote; proposer slashing built from a block and its own re-signed header with a different `state_root`; a validator penalised for a surround vote whose source is the same checkpoint at a different payload status. Identity: for every validator entering `process_attester_slashing`, `process_proposer_slashing` or `store.equivocators`, there exist two messages signed by that validator's key that validator.md forbids it to produce together; otherwise its `slashed` flag, balance and inclusion in committees are unchanged.",

    "Critical. FINALITY MUST ONLY MOVE ON REAL SUPERMAJORITY PARTICIPATION. `process_justification_and_finalization` compares `get_unslashed_participating_indices(..., TIMELY_TARGET_FLAG_INDEX)` balance against `total_active_balance * 2 // 3`; `get_attestation_participation_flag_indices` now awards flags conditional on `is_attestation_same_slot` and the payload status the attestation voted; `process_attestation` records flags in `current_epoch_participation` / `previous_epoch_participation`; `get_flag_index_deltas` and `process_inactivity_updates` pay or penalise from those flags; `process_rewards_and_penalties` and `process_effective_balance_updates` feed the next epoch's `total_active_balance`; `process_epoch` orders these against `process_pending_deposits`, `process_pending_consolidations`, `process_builder_pending_payments` and `process_ptc_window`. Show an attester or proposer, using only its own committee assignments, that gets one validator's balance counted more than once toward justification, gets flags for a vote that did not attest the target, or gets rewards or penalties the honest strategy would not earn: the same validator earning a target flag from two attestations at different `index` values; an attestation whose `data.index` claims a payload that was never available still earning the head flag; a builder's balance entering `total_active_balance`; a pending deposit activated mid-epoch shifting the two-thirds threshold retroactively; inactivity scores frozen by an attestation the spec should reject. Identity: the balance counted for a checkpoint in `process_justification_and_finalization` == sum of `effective_balance` over distinct, unslashed validators whose signed `AttestationData.target` equals that checkpoint and whose attestation was included in the window validator.md allows, and rewards paid == `get_flag_index_deltas` over those same flags.",

    "High. DUTY ASSIGNMENT MUST BE UNPREDICTABLE, UNIQUE PER SLOT AND IDENTICAL ON EVERY NODE. `compute_proposer_indices` and `compute_balance_weighted_selection` sample by effective balance with `MAX_RANDOM_VALUE = 2**16 - 1` against `MAX_EFFECTIVE_BALANCE_ELECTRA`; `get_beacon_proposer_indices` fills `state.proposer_lookahead` in `process_proposer_lookahead`; `compute_ptc` / `get_ptc` and `get_inclusion_list_committee` derive from `get_seed` with `DOMAIN_PTC_ATTESTER` and the inclusion-list domain; `get_next_sync_committee_indices` is modified; `get_randao_mix` and `process_randao` mix the proposer's reveal; `is_valid_dependent_root`, `compute_shuffling_lookahead_start_slot` and `get_shuffling_dependent_root` decide which state a node uses to recompute a committee. Show a proposer or validator, using only its own randao reveal, its own balance changes, or the timing of its own blocks, that steers which validator is selected, gets itself selected twice, or makes two nodes derive different committees for one slot: a top-up deposit or consolidation raising `effective_balance` between lookahead computation and the slot; a randao reveal withheld to choose between two lookahead outcomes; `compute_balance_weighted_selection` returning duplicates into a committee whose bits assume uniqueness; a dependent root at a slot where `process_slots` on one node advanced an epoch boundary the other did not. Identity: `proposer_lookahead`, `get_ptc(state, slot)` and `get_inclusion_list_committee(state, slot)` computed by any node from any valid `dependent_root` for that slot are equal, each index appears with the multiplicity the spec defines, and no single participant's action after the seed is fixed changes the selection.",

    "High. AN INCLUSION LIST MUST CONSTRAIN THE PAYLOAD IT WAS BUILT FOR AND NOTHING ELSE. `on_inclusion_list` accepts lists for `slot <= current_slot` within `MIN_SLOTS_FOR_INCLUSION_LISTS_REQUESTS`, checks `dependent_root` against `is_valid_dependent_root`, membership via `get_inclusion_list_committee`, signature via `is_valid_inclusion_list_signature`, and computes `is_timely`; `process_inclusion_list` stores one entry per `(slot, dependent_root, validator_index)`; `get_inclusion_list_transactions` and `get_inclusion_list_bits` drop equivocators and untimely lists; `is_inclusion_list_bits_inclusive` compares a block's `inclusion_list_bits` to the local view; `is_inclusion_list_satisfied`, `record_payload_inclusion_list_satisfaction` and `is_payload_inclusion_list_satisfied` decide in `should_extend_payload` and `on_execution_payload_envelope` whether a payload is extended; `ExecutionPayloadBid` carries the bits the builder committed to. Show a committee member, builder or proposer, using only its own list, bid or block, that makes honest nodes reject a payload that included everything it should, accept one that censored, or split on satisfaction: a list received timely by one node and late by another so `only_timely` views differ; a bid whose `inclusion_list_bits` names a member whose list no node stored; a valid list under a different `dependent_root` for the same slot ignored by satisfaction; the store keyed by `(slot, dependent_root)` while the committee is computed from another state; an envelope judged satisfied against transactions from equivocators. Identity: `is_payload_inclusion_list_satisfied` on every honest node == whether the payload contains every transaction from the non-equivocating, timely lists of the committee for `(slot, dependent_root)` the block committed to, and a payload satisfying that is never demoted by `should_extend_payload`.",

    "Critical. THE MISSING INVARIANT - what nobody wrote down. No assertion ties the sum of balances, builder balances, pending queues and emitted withdrawals across a full `state_transition`; nothing checks that a `PendingConsolidation` source still holds the balance it was queued with; `process_parent_execution_payload` trusts that the envelope stored for the parent is the only one the parent's bid could match; `get_builder_withdrawals` never reconciles a `BuilderPendingWithdrawal` against a settled bid; `Withdrawal.validator_index` shares one space between validators and converted builder indices; the fork-choice `Store` and the `InclusionListStore` are keyed by different notions of the same slot; light-client `process_light_client_update` still assumes the sync-committee signature covers the same header a Gloas block produces. Identify the FIRST place one of these unstated conservation or uniqueness assumptions is violated by an EOA sending execution requests, a validator or builder using its own keys in an assigned role, or a participant ordering its own messages, prove it with a pyspec test run through `make test` that asserts both sides (balance sum before and after, authoriser versus mutated account, payload applied versus bid committed, head per store versus head per store, slashed set versus equivocator set) and show that no later epoch transition, fork-choice tick or slashing can detect or reverse it.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate state-transition / fork-choice / accounting audit questions for one consensus-specs target.

    ```
    target_file format:
    "'File Name: specs/gloas/beacon-chain.md -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate consensus-layer security audit questions for this exact consensus-specs
    target:

    {target_file}

    Project focus:
    The Ethereum consensus specs define, in the ```python blocks of each markdown file,
    the state transition, fork choice, validator duties and p2p validation every client
    must implement identically. Untrusted input enters through what an unprivileged
    participant can put on chain or on the wire with its own keys: execution-layer
    requests (deposit, withdrawal, consolidation, builder deposit, builder exit), blocks
    and payload envelopes for slots it is assigned, attestations, slashings, exits,
    BLS-to-execution changes, PTC messages, inclusion lists and sync messages. The
    protocol decides (a) whether every Gwei is conserved and paid to its owner; (b)
    whether every change to a validator or builder was authorised by that party; (c)
    whether all honest nodes compute one head and one finalized checkpoint; (d) whether
    only an equivocator is slashable; (e) whether the payload executed is the payload the
    block committed to, paid once. Anything moved, changed, finalized, slashed or
    executed that the spec's own rules did not authorise is the bug.

    Rules:
    * Treat `File Name:` as the exact file. Reason over the python blocks in it and the
      functions it inherits unchanged from the previous fork.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact spec symbols (function, container, field, constant, domain, preset) as
      they appear in the file.
    * EVERY question must close on an equality that must hold across a transition or
      handler call. State it explicitly. Narrative questions are rejected.
    * Attacker is unprivileged only: an EOA sending execution-layer requests; one or
      more validators it funded itself, in any role the protocol assigns them
      (proposer, attester, aggregator, sync committee, PTC, inclusion-list committee);
      a builder registered with its own stake. They may produce any correctly signed
      message their own keys allow, any block or envelope for their assigned slot, and
      order or time their own messages.
    * Attacker is NOT a malicious peer or node, a client implementation bug, a network
      partition, a supermajority or 1/3 coalition, a compromised key, an execution
      client, or a social engineer. No DoS, gossip flooding or eclipse assumptions.
    * PROGRAM EXCLUSIONS - a question landing in any of these wastes the whole batch:
      - tests/, pysetup/, scripts/, presets/, configs/, generated Python, Makefile,
        pyproject, lockfiles, README and SECURITY.md are OUT OF SCOPE.
      - Denial of service, resource exhaustion, unbounded lists or memory, message
        rate, bandwidth and timing-only liveness delays are OUT OF SCOPE.
      - Economic or governance attacks needing a large stake share (51%, 33%) are OUT.
      - Bugs inside a client, the execution layer, KZG/BLS libraries, or the deposit
        contract bytecode with no path through the spec text are OUT OF SCOPE; a spec
        rule that steers them wrong is fully IN scope.
      - Also excluded: known issues, best-practice notes, feature requests, wording
        nits, centralisation risk, and theoretical findings without a state to show.
    * IN-SCOPE IMPACTS - every question must land on one and name it:
      Critical: finality or safety break (two conflicting finalized checkpoints, or an
      invalid transition accepted / valid one rejected so spec-following nodes split);
      Gwei created, destroyed or paid to an address other than the owner's; an honest
      validator slashed; a payload executed or paid that the block did not commit to.
      High: a consensus split or reorg forced by one participant without majority
      stake; a validator or builder exited, consolidated or re-credentialed without its
      authority; a builder payment or withdrawal misdirected, doubled or escaped; a duty
      selection a single participant can steer.
    * Every question must be a concrete real-world scenario an unprivileged participant
      can trigger with its own stake, keys and requests.
    * A failed assert is a finding only when it rejects a transition validator.md tells
      an honest node to produce, or lets an unauthorised one through - say which.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must land on a Critical impact rather than a High one.
    * Every question must be testable locally with a pyspec test run through
      `make test` on the minimal preset. Never propose testing on mainnet or a public
      testnet.
    * Avoid generic checklist questions and repeated root causes.
    * Prefer questions that name TWO values that must be equal and ask whether they are:
      balance sum before and after, authoriser and mutated account, head on node A and
      head on node B, slashed set and equivocator set, payload applied and bid
      committed, flags counted and votes cast.

    Known dead ends - do NOT generate questions about these:
    * Anything needing a malicious peer, node, client bug, execution client, or a
      coalition holding 1/3 or more of stake.
    * A bug in a client, library or contract bytecode with no path through the spec.
    * DoS, memory, message size, timing-only delays, or a participant harming only its
      own balance.
    * Findings only reproducible through test tooling or preset edits.

    Core equalities (each question must close on one):
    * BALANCE CONSERVATION: balances + builder balances + queues + withdrawals after ==
      before + deposits + rewards - penalties, each Gwei paid to its owner's address.
    * AUTHORITY: every mutated validator or builder == a party whose key or withdrawal
      address signed the input that mutated it.
    * SINGLE HEAD: get_head and finalized checkpoint on any two spec-following stores
      fed the same messages == equal.
    * ACCOUNTABILITY: slashed or equivocator set == set of validators that signed two
      messages validator.md forbids together.
    * PAYLOAD BINDING: payload, withdrawals and requests applied == those in the one
      envelope matching the accepted bid, paid exactly once.
    * DUTY TRUTH: committee or proposer derived by any node from any valid dependent
      root == identical, unsteerable after the seed is fixed.

    Each question must include:
    1. target function, container field or constant;
    2. attacker input (the concrete request, block, envelope, attestation, list or
       message fields that matter);
    3. preconditions (fork, epoch position, queue state, balances, payload status);
    4. call sequence through the state transition, epoch processing or store handlers;
    5. the equality that breaks, written explicitly;
    6. scoped impact and whose stake or finality is exposed;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Method: function_name] Can an unprivileged ATTACKER_INPUT under PRECONDITIONS trigger CALL_SEQUENCE, breaking the equality EQUALITY, causing scoped impact: SCOPE_IMPACT against PARTY? Proof idea: pyspec test PARAMETERS asserting BALANCE_CONSERVATION, AUTHORITY, SINGLE_HEAD, ACCOUNTABILITY, PAYLOAD_BINDING, or DUTY_TRUTH.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a state-transition / fork-choice exploit-validation prompt for consensus-specs.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only: the ```python blocks in specs/**/*.md and what each fork inherits. Analyze only this question and scoped impact.
- Attacker is unprivileged only: an EOA sending execution-layer requests; validators it funded itself in any assigned role (proposer, attester, aggregator, sync committee, PTC, inclusion-list committee); a builder registered with its own stake. They may produce any correctly signed message their keys allow and order their own messages.
- Reject anything requiring a malicious peer or node, a client bug, a network partition, a 1/3 or majority coalition, a compromised key, the execution client, or social engineering.
- OUT OF SCOPE, reject on sight: tests/, pysetup/, scripts/, presets/, configs/, generated Python, build files, README, SECURITY.md; denial of service, resource exhaustion, unbounded lists or memory, message rate, timing-only delays; large-stake economic attacks; bugs inside clients, the execution layer, BLS/KZG libraries or contract bytecode with no path through the spec text; known issues; wording nits; best-practice notes; theoretical findings.
- The impact must be one of: Critical - finality or safety break, an invalid transition accepted or a valid one rejected so spec-following nodes split, Gwei created, destroyed or paid to a non-owner, an honest validator slashed, a payload executed or paid that the block did not commit to; High - a split or reorg forced by one participant without majority stake, a validator or builder exited, consolidated or re-credentialed without its authority, a builder payment or withdrawal misdirected, doubled or escaped, a duty selection one participant can steer.
- Focus on real impact: something moved, changed, finalized, slashed or executed that the spec's own rules did not authorise.

## Validate
- Write the equality the question claims is broken between two named values BEFORE tracing any code.
- Trace the exact reachable path from the attacker's input and record every read and write of `state.balances`, `state.validators[i]`, `state.builders[j]`, `pending_*` queues, `builder_pending_payments` / `builder_pending_withdrawals`, `execution_payload_availability`, `latest_execution_payload_bid`, `latest_block_hash`, participation flags, `store.latest_messages`, `store.payloads` and `store.equivocators`.
- Evaluate both sides of the equality before and after. If they still match, output no vulnerability.
- Check whether the asserts in `process_block`, `process_operations`, the signature domains, `is_valid_indexed_attestation`, `is_slashable_attestation_data`, `can_builder_cover_bid`, `verify_execution_payload_envelope`, `validate_on_attestation`, `is_valid_dependent_root`, the churn limits, or the honest behaviour in validator.md already prevent the divergence.
- State what the attacker gains per transition and whether it is repeatable.
- Require exact file/function support and a reproducible pyspec test run through `make test` on the minimal preset.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[The broken equality, the code path, root cause, the attacker's exact input, exploit flow, and why existing guards fail]

### Impact Explanation
[What is moved, changed, finalized, slashed or executed, which party, repeatability, matching severity category]

### Likelihood Explanation
[Preconditions, fork and state required, attacker stake and cost, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[pyspec test plan with the exact assertions on both sides of the equality]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for consensus-specs claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- A claim is only valid if the report states the broken equality between two named values and shows both sides concretely on a real `BeaconState` or `Store`. Reject prose-only claims.
- Reject anything requiring a malicious peer or node, a client bug, a network partition, a 1/3 or majority coalition, a compromised or foreign key, the execution client, or social engineering.
- OUT OF SCOPE, reject on sight: tests/, pysetup/, scripts/, presets/, configs/, generated Python, build files, README, SECURITY.md; denial of service, resource exhaustion, unbounded lists or memory, message rate, timing-only delays; large-stake economic attacks; bugs inside clients, the execution layer, BLS/KZG libraries or contract bytecode with no path through the spec text; known issues; centralisation risk; wording nits; best-practice notes; feature requests; theoretical findings.
- The impact must be one of: Critical - finality or safety break, an invalid transition accepted or a valid one rejected so spec-following nodes split, Gwei created, destroyed or paid to a non-owner, an honest validator slashed, a payload executed or paid that the block did not commit to; High - a split or reorg forced by one participant without majority stake, a validator or builder exited, consolidated or re-credentialed without its authority, a builder payment or withdrawal misdirected, doubled or escaped, a duty selection one participant can steer.
- Reject claims where the only loss is the attacker's own stake.
- Reject if the bug was already fixed, publicly disclosed, or covered by a known-issues list.
- A valid report must be triggerable by an unprivileged participant against the current spec text with its own stake, keys and requests.
- A PoC is mandatory. Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function/container/constant, and line references.
2. The equality written explicitly, with both sides shown before and after.
3. Clear root cause: which balance drift, authority gap, head or finality divergence, slashing of a non-equivocator, payload or payment mismatch, or duty steering causes it.
4. Reachable exploit path: preconditions -> attacker input -> state transition / epoch processing / store handler sequence -> observed divergence.
5. The block and operation asserts, signature domains, `is_valid_indexed_attestation`, `is_slashable_attestation_data`, `can_builder_cover_bid`, `verify_execution_payload_envelope`, `validate_on_attestation`, `is_valid_dependent_root`, churn limits and validator.md honest behaviour reviewed and shown insufficient.
6. Impact stated concretely: which stake, whose, which finality, and whether it is repeatable.
7. Reproducible proof: pyspec test run through `make test` on the minimal preset, with the asserted values.

## Silent Triage Questions
Before output, internally answer:
- What exactly is the equality, and does it actually fail on a concrete state?
- Can an EOA, a self-funded validator or a self-staked builder trigger it with no coalition, no foreign key and no malicious node?
- Is the flaw in the spec text, not in a client, library or contract?
- What is moved, changed, finalized, slashed or executed, whose stake, and can it be repeated?
- Would the Ethereum Foundation bounty triage accept the exploit path under the consensus-layer program?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the broken equality and impact]

## Finding Description
[Exact code path, the equality, root cause, exploit flow, and why existing guards fail]

## Impact Explanation
[What is moved, changed, finalized, slashed or executed, affected party, repeatability, severity category]

## Likelihood Explanation
[Attacker capability, preconditions, state required, cost, feasibility]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or pyspec test plan with concrete assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for consensus-specs.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope repo context only (the ```python blocks in `specs/**/*.md` and what each fork inherits, excluding tests/, pysetup/, scripts/, presets/, configs/ and generated Python). Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged analogs that break an equality: a Gwei created, destroyed or paid to a non-owner; a validator or builder mutated without its authority; two spec-following stores disagreeing on head or finality; a non-equivocator slashed; a payload or payment applied that the block did not commit to; a duty selection one participant can steer.
- OUT OF SCOPE, reject on sight: tests/, pysetup/, scripts/, presets/, configs/, generated Python, build files, README; denial of service, resource exhaustion, unbounded lists or memory, message rate, timing-only delays; large-stake economic attacks; bugs inside clients, the execution layer, BLS/KZG libraries or contract bytecode with no path through the spec text; anything requiring a malicious peer, node, client bug, partition, coalition or foreign key; known issues; wording nits; best-practice notes; theoretical findings.
- The impact must be one of: Critical - finality or safety break, an invalid transition accepted or a valid one rejected so spec-following nodes split, Gwei created, destroyed or paid to a non-owner, an honest validator slashed, a payload executed or paid that the block did not commit to; High - a split or reorg forced by one participant without majority stake, a validator or builder exited, consolidated or re-credentialed without its authority, a builder payment or withdrawal misdirected, doubled or escaped, a duty selection one participant can steer.
- Reject analogs where the only loss is the attacker's own stake.

## Validate
- Map the bug class to the strongest reachable path in this repo and state the equality it would break.
- Evaluate both sides before and after the attacker's input on a concrete state.
- Prove root cause with exact file/function support.
- Accept only concrete balance loss, unauthorised mutation, head or finality divergence, wrongful slashing, payload or payment mismatch, or steerable selection.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt
