import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'stacks-network/stacks-core'
# todo: the name of the repository
REPO_NAME = 'stacks-core'

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
    # LENS: THE SIGNER'S DECISION - WHAT GETS SIGNED, WHAT GETS REJECTED.
    # A Nakamoto block is final only when enough signers sign it. Each signer runs the
    # code below to decide, from a miner-supplied block proposal and the chainstate it
    # can see, whether to sign. The files sit on the path from an attacker-influenced
    # proposal - block contents, tenure/burn view, sortition, reorg claim - to one of
    # three decisions: sign only a block that is actually valid and canonical, reject
    # every invalid or non-canonical block, and never sign two conflicting blocks at the
    # same height. A question belongs here only if it closes on an equality between what
    # the signer approved and what is actually valid, canonical and unique.
    # =================================================================================
    # -- clarity-types: Clarity value, type and effect model -------------------------------
    "clarity-types/src/effects/asset_map.rs",
    "clarity-types/src/effects/mod.rs",
    "clarity-types/src/errors/mod.rs",
    "clarity-types/src/lib.rs",
    "clarity-types/src/representations.rs",
    "clarity-types/src/types/mod.rs",
    "clarity-types/src/types/serialization.rs",
    "clarity-types/src/types/signatures.rs",
    "clarity-types/src/version.rs",

    # -- clarity: the Clarity language, analyser, interpreter, costs and database ----------
    "clarity/src/libclarity.rs",
    "clarity/src/vm/analysis/analysis_db.rs",
    "clarity/src/vm/analysis/arithmetic_checker/mod.rs",
    "clarity/src/vm/analysis/contract_interface_builder/mod.rs",
    "clarity/src/vm/analysis/errors.rs",
    "clarity/src/vm/analysis/mod.rs",
    "clarity/src/vm/analysis/read_only_checker/mod.rs",
    "clarity/src/vm/analysis/trait_checker/mod.rs",
    "clarity/src/vm/analysis/type_checker/contexts.rs",
    "clarity/src/vm/analysis/type_checker/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/contexts.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/assets.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/maps.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/options.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/sequences.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/contexts.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/assets.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/conversions.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/maps.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/options.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/post_conditions.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/sequences.rs",
    "clarity/src/vm/analysis/types.rs",
    "clarity/src/vm/ast/definition_sorter/mod.rs",
    "clarity/src/vm/ast/errors.rs",
    "clarity/src/vm/ast/expression_identifier/mod.rs",
    "clarity/src/vm/ast/mod.rs",
    "clarity/src/vm/ast/parser/mod.rs",
    "clarity/src/vm/ast/parser/v1.rs",
    "clarity/src/vm/ast/parser/v2/lexer/error.rs",
    "clarity/src/vm/ast/parser/v2/lexer/mod.rs",
    "clarity/src/vm/ast/parser/v2/lexer/token.rs",
    "clarity/src/vm/ast/parser/v2/mod.rs",
    "clarity/src/vm/ast/stack_depth_checker.rs",
    "clarity/src/vm/ast/sugar_expander/mod.rs",
    "clarity/src/vm/ast/traits_resolver/mod.rs",
    "clarity/src/vm/ast/types.rs",
    "clarity/src/vm/callables.rs",
    "clarity/src/vm/clarity.rs",
    "clarity/src/vm/contexts.rs",
    "clarity/src/vm/contracts.rs",
    "clarity/src/vm/costs/constants.rs",
    "clarity/src/vm/costs/cost_functions.rs",
    "clarity/src/vm/costs/costs_1.rs",
    "clarity/src/vm/costs/costs_2.rs",
    "clarity/src/vm/costs/costs_2_testnet.rs",
    "clarity/src/vm/costs/costs_3.rs",
    "clarity/src/vm/costs/costs_4.rs",
    "clarity/src/vm/costs/costs_5.rs",
    "clarity/src/vm/costs/errors.rs",
    "clarity/src/vm/costs/execution_cost.rs",
    "clarity/src/vm/costs/mod.rs",
    "clarity/src/vm/database/caching/mod.rs",
    "clarity/src/vm/database/caching/weight_limited_fifo.rs",
    "clarity/src/vm/database/clarity_db.rs",
    "clarity/src/vm/database/clarity_store.rs",
    "clarity/src/vm/database/key_value_wrapper.rs",
    "clarity/src/vm/database/mod.rs",
    "clarity/src/vm/database/sqlite.rs",
    "clarity/src/vm/database/structures.rs",
    "clarity/src/vm/diagnostic.rs",
    "clarity/src/vm/errors.rs",
    "clarity/src/vm/events.rs",
    "clarity/src/vm/functions/arithmetic.rs",
    "clarity/src/vm/functions/assets.rs",
    "clarity/src/vm/functions/bitcoin.rs",
    "clarity/src/vm/functions/boolean.rs",
    "clarity/src/vm/functions/conversions.rs",
    "clarity/src/vm/functions/crypto.rs",
    "clarity/src/vm/functions/database.rs",
    "clarity/src/vm/functions/define.rs",
    "clarity/src/vm/functions/mod.rs",
    "clarity/src/vm/functions/options.rs",
    "clarity/src/vm/functions/post_conditions.rs",
    "clarity/src/vm/functions/principals.rs",
    "clarity/src/vm/functions/sequences.rs",
    "clarity/src/vm/functions/tuples.rs",
    "clarity/src/vm/hooks/internals.rs",
    "clarity/src/vm/hooks/mod.rs",
    "clarity/src/vm/hooks/trace.rs",
    "clarity/src/vm/mod.rs",
    "clarity/src/vm/representations.rs",
    "clarity/src/vm/resource_limiter.rs",
    "clarity/src/vm/tooling/mod.rs",
    "clarity/src/vm/types/mod.rs",
    "clarity/src/vm/types/serialization.rs",
    "clarity/src/vm/types/signatures.rs",
    "clarity/src/vm/variables.rs",
    "clarity/src/vm/version.rs",

    # -- stacks-codec: transaction and message wire encoding -------------------------------
    "stacks-codec/src/lib.rs",
    "stacks-codec/src/strings.rs",
    "stacks-codec/src/transaction.rs",

    # -- crates/stacks-transactions: standalone transaction and post-condition checks ------
    "crates/stacks-transactions/src/lib.rs",

    # -- stacks-common: addresses, hashing, secp256k1, codec and shared utils --------------
    "stacks-common/src/address/b58.rs",
    "stacks-common/src/address/c32.rs",
    "stacks-common/src/address/c32_old.rs",
    "stacks-common/src/address/mod.rs",
    "stacks-common/src/alloc_tracker.rs",
    "stacks-common/src/bitvec.rs",
    "stacks-common/src/codec/macros.rs",
    "stacks-common/src/codec/mod.rs",
    "stacks-common/src/libcommon.rs",
    "stacks-common/src/types/chainstate.rs",
    "stacks-common/src/types/mod.rs",
    "stacks-common/src/types/net.rs",
    "stacks-common/src/types/sqlite.rs",
    "stacks-common/src/util/chunked_encoding.rs",
    "stacks-common/src/util/db.rs",
    "stacks-common/src/util/ed25519.rs",
    "stacks-common/src/util/hash.rs",
    "stacks-common/src/util/log.rs",
    "stacks-common/src/util/lru_cache.rs",
    "stacks-common/src/util/macros.rs",
    "stacks-common/src/util/mod.rs",
    "stacks-common/src/util/pair.rs",
    "stacks-common/src/util/pipe.rs",
    "stacks-common/src/util/retry.rs",
    "stacks-common/src/util/secp256k1/mod.rs",
    "stacks-common/src/util/secp256k1/native.rs",
    "stacks-common/src/util/secp256k1/wasm.rs",
    "stacks-common/src/util/secp256r1.rs",
    "stacks-common/src/util/serde_serializers.rs",
    "stacks-common/src/util/uint.rs",
    "stacks-common/src/util/vrf.rs",

    # -- libsigner: signer transport, events and v0 messages -------------------------------
    "libsigner/src/error.rs",
    "libsigner/src/events.rs",
    "libsigner/src/http.rs",
    "libsigner/src/libsigner.rs",
    "libsigner/src/runloop.rs",
    "libsigner/src/session.rs",
    "libsigner/src/signer_set.rs",
    "libsigner/src/v0/messages.rs",
    "libsigner/src/v0/mod.rs",
    "libsigner/src/v0/signer_state.rs",

    # -- libstackerdb: StackerDB chunk signing and verification ----------------------------
    "libstackerdb/src/libstackerdb.rs",

    # -- pox-locking: the Rust side that locks and unlocks STX for PoX/stacking ------------
    "pox-locking/src/events.rs",
    "pox-locking/src/events_24.rs",
    "pox-locking/src/lib.rs",
    "pox-locking/src/pox_1.rs",
    "pox-locking/src/pox_2.rs",
    "pox-locking/src/pox_3.rs",
    "pox-locking/src/pox_4.rs",
    "pox-locking/src/pox_5.rs",

    # -- stacks-signer: the Nakamoto signer decision logic and chainstate view -------------
    "stacks-signer/src/chainstate/mod.rs",
    "stacks-signer/src/chainstate/v1.rs",
    "stacks-signer/src/chainstate/v2.rs",
    "stacks-signer/src/cli.rs",
    "stacks-signer/src/client/mod.rs",
    "stacks-signer/src/client/stackerdb.rs",
    "stacks-signer/src/client/stacks_client.rs",
    "stacks-signer/src/config.rs",
    "stacks-signer/src/lib.rs",
    "stacks-signer/src/main.rs",
    "stacks-signer/src/monitor_signers.rs",
    "stacks-signer/src/monitoring/mod.rs",
    "stacks-signer/src/monitoring/prometheus.rs",
    "stacks-signer/src/monitoring/server.rs",
    "stacks-signer/src/runloop.rs",
    "stacks-signer/src/signerdb.rs",
    "stacks-signer/src/utils.rs",
    "stacks-signer/src/v0/mod.rs",
    "stacks-signer/src/v0/signer.rs",
    "stacks-signer/src/v0/signer_state.rs",

    # -- stacks-node: the node binary, run loops, miner, burnchain and event dispatch ------
    "stacks-node/src/burnchains/bitcoin/core_controller.rs",
    "stacks-node/src/burnchains/bitcoin/mod.rs",
    "stacks-node/src/burnchains/bitcoin_regtest_controller.rs",
    "stacks-node/src/burnchains/mod.rs",
    "stacks-node/src/burnchains/rpc/bitcoin_rpc_client/mod.rs",
    "stacks-node/src/burnchains/rpc/mod.rs",
    "stacks-node/src/burnchains/rpc/rpc_transport/mod.rs",
    "stacks-node/src/event_dispatcher.rs",
    "stacks-node/src/event_dispatcher/db.rs",
    "stacks-node/src/event_dispatcher/payloads.rs",
    "stacks-node/src/event_dispatcher/stacker_db.rs",
    "stacks-node/src/event_dispatcher/worker.rs",
    "stacks-node/src/globals.rs",
    "stacks-node/src/keychain.rs",
    "stacks-node/src/main.rs",
    "stacks-node/src/monitoring/mod.rs",
    "stacks-node/src/monitoring/prometheus.rs",
    "stacks-node/src/nakamoto_node.rs",
    "stacks-node/src/nakamoto_node/miner.rs",
    "stacks-node/src/nakamoto_node/miner_db.rs",
    "stacks-node/src/nakamoto_node/peer.rs",
    "stacks-node/src/nakamoto_node/relayer.rs",
    "stacks-node/src/nakamoto_node/signer_coordinator.rs",
    "stacks-node/src/nakamoto_node/stackerdb_listener.rs",
    "stacks-node/src/neon_node.rs",
    "stacks-node/src/node.rs",
    "stacks-node/src/operations.rs",
    "stacks-node/src/run_loop/boot_nakamoto.rs",
    "stacks-node/src/run_loop/helium.rs",
    "stacks-node/src/run_loop/mod.rs",
    "stacks-node/src/run_loop/nakamoto.rs",
    "stacks-node/src/run_loop/neon.rs",
    "stacks-node/src/syncctl.rs",
    "stacks-node/src/tenure.rs",

    # -- stackslib: consensus, chainstate, the Clarity VM host, burn ops and the P2P/RPC network ----
    "stackslib/src/burnchains/bitcoin/address.rs",
    "stackslib/src/burnchains/bitcoin/bits.rs",
    "stackslib/src/burnchains/bitcoin/blocks.rs",
    "stackslib/src/burnchains/bitcoin/indexer.rs",
    "stackslib/src/burnchains/bitcoin/keys.rs",
    "stackslib/src/burnchains/bitcoin/messages.rs",
    "stackslib/src/burnchains/bitcoin/mod.rs",
    "stackslib/src/burnchains/bitcoin/network.rs",
    "stackslib/src/burnchains/bitcoin/spv.rs",
    "stackslib/src/burnchains/burnchain.rs",
    "stackslib/src/burnchains/db.rs",
    "stackslib/src/burnchains/indexer.rs",
    "stackslib/src/burnchains/mod.rs",
    "stackslib/src/chainstate/burn/atc.rs",
    "stackslib/src/chainstate/burn/db/mod.rs",
    "stackslib/src/chainstate/burn/db/processing.rs",
    "stackslib/src/chainstate/burn/db/sortdb.rs",
    "stackslib/src/chainstate/burn/distribution.rs",
    "stackslib/src/chainstate/burn/mod.rs",
    "stackslib/src/chainstate/burn/operations/delegate_stx.rs",
    "stackslib/src/chainstate/burn/operations/leader_block_commit.rs",
    "stackslib/src/chainstate/burn/operations/leader_key_register.rs",
    "stackslib/src/chainstate/burn/operations/mod.rs",
    "stackslib/src/chainstate/burn/operations/stack_stx.rs",
    "stackslib/src/chainstate/burn/operations/transfer_stx.rs",
    "stackslib/src/chainstate/burn/operations/vote_for_aggregate_key.rs",
    "stackslib/src/chainstate/burn/sortition.rs",
    "stackslib/src/chainstate/coordinator/comm.rs",
    "stackslib/src/chainstate/coordinator/mod.rs",
    "stackslib/src/chainstate/mod.rs",
    "stackslib/src/chainstate/nakamoto/coordinator/mod.rs",
    "stackslib/src/chainstate/nakamoto/keys.rs",
    "stackslib/src/chainstate/nakamoto/miner.rs",
    "stackslib/src/chainstate/nakamoto/mod.rs",
    "stackslib/src/chainstate/nakamoto/shadow.rs",
    "stackslib/src/chainstate/nakamoto/signer_set.rs",
    "stackslib/src/chainstate/nakamoto/staging_blocks.rs",
    "stackslib/src/chainstate/nakamoto/tenure.rs",
    "stackslib/src/chainstate/stacks/address.rs",
    "stackslib/src/chainstate/stacks/auth.rs",
    "stackslib/src/chainstate/stacks/block.rs",
    "stackslib/src/chainstate/stacks/boot/bns.clar",
    "stackslib/src/chainstate/stacks/boot/contract_tests.rs",
    "stackslib/src/chainstate/stacks/boot/cost-voting.clar",
    "stackslib/src/chainstate/stacks/boot/costs-2.clar",
    "stackslib/src/chainstate/stacks/boot/costs-3.clar",
    "stackslib/src/chainstate/stacks/boot/costs-4.clar",
    "stackslib/src/chainstate/stacks/boot/costs.clar",
    "stackslib/src/chainstate/stacks/boot/docs.rs",
    "stackslib/src/chainstate/stacks/boot/genesis.clar",
    "stackslib/src/chainstate/stacks/boot/lockup.clar",
    "stackslib/src/chainstate/stacks/boot/mod.rs",
    "stackslib/src/chainstate/stacks/boot/pox-2.clar",
    "stackslib/src/chainstate/stacks/boot/pox-3.clar",
    "stackslib/src/chainstate/stacks/boot/pox-4.clar",
    "stackslib/src/chainstate/stacks/boot/pox-5.clar",
    "stackslib/src/chainstate/stacks/boot/pox-mainnet.clar",
    "stackslib/src/chainstate/stacks/boot/pox.clar",
    "stackslib/src/chainstate/stacks/boot/pox_2_tests.rs",
    "stackslib/src/chainstate/stacks/boot/pox_3_tests.rs",
    "stackslib/src/chainstate/stacks/boot/pox_4_tests.rs",
    "stackslib/src/chainstate/stacks/boot/signers-0-xxx.clar",
    "stackslib/src/chainstate/stacks/boot/signers-1-xxx.clar",
    "stackslib/src/chainstate/stacks/boot/signers-voting.clar",
    "stackslib/src/chainstate/stacks/boot/signers.clar",
    "stackslib/src/chainstate/stacks/boot/signers_tests.rs",
    "stackslib/src/chainstate/stacks/boot/sip-031.clar",
    "stackslib/src/chainstate/stacks/db/accounts.rs",
    "stackslib/src/chainstate/stacks/db/blocks.rs",
    "stackslib/src/chainstate/stacks/db/contracts.rs",
    "stackslib/src/chainstate/stacks/db/headers.rs",
    "stackslib/src/chainstate/stacks/db/mod.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/blocks.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/burnchain.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/clarity.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/common.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/fork_storage.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/index.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/mod.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/sortition.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/spv.rs",
    "stackslib/src/chainstate/stacks/db/transactions.rs",
    "stackslib/src/chainstate/stacks/db/unconfirmed.rs",
    "stackslib/src/chainstate/stacks/events.rs",
    "stackslib/src/chainstate/stacks/index/bits.rs",
    "stackslib/src/chainstate/stacks/index/blob_layout.rs",
    "stackslib/src/chainstate/stacks/index/cache.rs",
    "stackslib/src/chainstate/stacks/index/file.rs",
    "stackslib/src/chainstate/stacks/index/marf.rs",
    "stackslib/src/chainstate/stacks/index/mod.rs",
    "stackslib/src/chainstate/stacks/index/node.rs",
    "stackslib/src/chainstate/stacks/index/profile.rs",
    "stackslib/src/chainstate/stacks/index/proofs.rs",
    "stackslib/src/chainstate/stacks/index/squash.rs",
    "stackslib/src/chainstate/stacks/index/squash/node_store.rs",
    "stackslib/src/chainstate/stacks/index/squash/stream.rs",
    "stackslib/src/chainstate/stacks/index/storage.rs",
    "stackslib/src/chainstate/stacks/index/trie.rs",
    "stackslib/src/chainstate/stacks/index/trie_sql.rs",
    "stackslib/src/chainstate/stacks/miner.rs",
    "stackslib/src/chainstate/stacks/mod.rs",
    "stackslib/src/chainstate/stacks/sbtc.rs",
    "stackslib/src/chainstate/stacks/transaction.rs",
    "stackslib/src/clarity_vm/clarity.rs",
    "stackslib/src/clarity_vm/database/ephemeral.rs",
    "stackslib/src/clarity_vm/database/marf.rs",
    "stackslib/src/clarity_vm/database/mod.rs",
    "stackslib/src/clarity_vm/mod.rs",
    "stackslib/src/clarity_vm/special.rs",
    "stackslib/src/config/chain_data.rs",
    "stackslib/src/config/mod.rs",
    "stackslib/src/core/mempool.rs",
    "stackslib/src/core/mod.rs",
    "stackslib/src/core/nonce_cache.rs",
    "stackslib/src/cost_estimates/fee_medians.rs",
    "stackslib/src/cost_estimates/fee_rate_fuzzer.rs",
    "stackslib/src/cost_estimates/fee_scalar.rs",
    "stackslib/src/cost_estimates/metrics.rs",
    "stackslib/src/cost_estimates/mod.rs",
    "stackslib/src/cost_estimates/pessimistic.rs",
    "stackslib/src/deps/mod.rs",
    "stackslib/src/lib.rs",
    "stackslib/src/monitoring/mod.rs",
    "stackslib/src/monitoring/prometheus.rs",
    "stackslib/src/net/api/blockreplay.rs",
    "stackslib/src/net/api/blocksimulate.rs",
    "stackslib/src/net/api/callreadonly.rs",
    "stackslib/src/net/api/fastcallreadonly.rs",
    "stackslib/src/net/api/get_tenure_tip_meta.rs",
    "stackslib/src/net/api/get_tenures_fork_info.rs",
    "stackslib/src/net/api/getaccount.rs",
    "stackslib/src/net/api/getattachment.rs",
    "stackslib/src/net/api/getattachmentsinv.rs",
    "stackslib/src/net/api/getblock.rs",
    "stackslib/src/net/api/getblock_v3.rs",
    "stackslib/src/net/api/getblockbyheight.rs",
    "stackslib/src/net/api/getclaritymarfvalue.rs",
    "stackslib/src/net/api/getclaritymetadata.rs",
    "stackslib/src/net/api/getconstantval.rs",
    "stackslib/src/net/api/getcontractabi.rs",
    "stackslib/src/net/api/getcontractsrc.rs",
    "stackslib/src/net/api/getdatavar.rs",
    "stackslib/src/net/api/getheaders.rs",
    "stackslib/src/net/api/gethealth.rs",
    "stackslib/src/net/api/getinfo.rs",
    "stackslib/src/net/api/getistraitimplemented.rs",
    "stackslib/src/net/api/getmapentry.rs",
    "stackslib/src/net/api/getmicroblocks_confirmed.rs",
    "stackslib/src/net/api/getmicroblocks_indexed.rs",
    "stackslib/src/net/api/getmicroblocks_unconfirmed.rs",
    "stackslib/src/net/api/getneighbors.rs",
    "stackslib/src/net/api/getpoxinfo.rs",
    "stackslib/src/net/api/getsigner.rs",
    "stackslib/src/net/api/getsortition.rs",
    "stackslib/src/net/api/getstackerdbchunk.rs",
    "stackslib/src/net/api/getstackerdbmetadata.rs",
    "stackslib/src/net/api/getstackers.rs",
    "stackslib/src/net/api/getstxtransfercost.rs",
    "stackslib/src/net/api/gettenure.rs",
    "stackslib/src/net/api/gettenureblocks.rs",
    "stackslib/src/net/api/gettenureblocksbyhash.rs",
    "stackslib/src/net/api/gettenureblocksbyheight.rs",
    "stackslib/src/net/api/gettenureinfo.rs",
    "stackslib/src/net/api/gettenuretip.rs",
    "stackslib/src/net/api/gettransaction.rs",
    "stackslib/src/net/api/gettransaction_unconfirmed.rs",
    "stackslib/src/net/api/liststackerdbreplicas.rs",
    "stackslib/src/net/api/mod.rs",
    "stackslib/src/net/api/postblock.rs",
    "stackslib/src/net/api/postblock_proposal.rs",
    "stackslib/src/net/api/postblock_v3.rs",
    "stackslib/src/net/api/postfeerate.rs",
    "stackslib/src/net/api/postmempoolquery.rs",
    "stackslib/src/net/api/postmicroblock.rs",
    "stackslib/src/net/api/poststackerdbchunk.rs",
    "stackslib/src/net/api/posttransaction.rs",
    "stackslib/src/net/api/read_only/mod.rs",
    "stackslib/src/net/api/read_only/parse.rs",
    "stackslib/src/net/api/txsimulate.rs",
    "stackslib/src/net/asn.rs",
    "stackslib/src/net/atlas/db.rs",
    "stackslib/src/net/atlas/download.rs",
    "stackslib/src/net/atlas/mod.rs",
    "stackslib/src/net/chat.rs",
    "stackslib/src/net/codec.rs",
    "stackslib/src/net/connection.rs",
    "stackslib/src/net/db.rs",
    "stackslib/src/net/dns.rs",
    "stackslib/src/net/download/epoch2x.rs",
    "stackslib/src/net/download/mod.rs",
    "stackslib/src/net/download/nakamoto/download_state_machine.rs",
    "stackslib/src/net/download/nakamoto/mod.rs",
    "stackslib/src/net/download/nakamoto/tenure.rs",
    "stackslib/src/net/download/nakamoto/tenure_downloader.rs",
    "stackslib/src/net/download/nakamoto/tenure_downloader_set.rs",
    "stackslib/src/net/download/nakamoto/tenure_downloader_unconfirmed.rs",
    "stackslib/src/net/http/common.rs",
    "stackslib/src/net/http/error.rs",
    "stackslib/src/net/http/mod.rs",
    "stackslib/src/net/http/request.rs",
    "stackslib/src/net/http/response.rs",
    "stackslib/src/net/http/stream.rs",
    "stackslib/src/net/httpcore.rs",
    "stackslib/src/net/inv/epoch2x.rs",
    "stackslib/src/net/inv/mod.rs",
    "stackslib/src/net/inv/nakamoto.rs",
    "stackslib/src/net/mempool/mod.rs",
    "stackslib/src/net/mod.rs",
    "stackslib/src/net/neighbors/comms.rs",
    "stackslib/src/net/neighbors/db.rs",
    "stackslib/src/net/neighbors/mod.rs",
    "stackslib/src/net/neighbors/neighbor.rs",
    "stackslib/src/net/neighbors/rpc.rs",
    "stackslib/src/net/neighbors/walk.rs",
    "stackslib/src/net/p2p.rs",
    "stackslib/src/net/poll.rs",
    "stackslib/src/net/prune.rs",
    "stackslib/src/net/relay.rs",
    "stackslib/src/net/rpc.rs",
    "stackslib/src/net/server.rs",
    "stackslib/src/net/stackerdb/config.rs",
    "stackslib/src/net/stackerdb/db.rs",
    "stackslib/src/net/stackerdb/mod.rs",
    "stackslib/src/net/stackerdb/sync.rs",
    "stackslib/src/net/unsolicited.rs",
    "stackslib/src/util_lib/bloom.rs",
    "stackslib/src/util_lib/boot.rs",
    "stackslib/src/util_lib/db.rs",
    "stackslib/src/util_lib/mod.rs",
    "stackslib/src/util_lib/signed_structured_data.rs",
    "stackslib/src/util_lib/strings.rs",

    # =================================================================================
    # NOT AUDITED (excluded from every variant): tests, mocks and *test* files; fuzz and
    # bench harnesses; test_util and the hooks/testing render helpers; docs/ and README;
    # config, *.toml and CHANGELOG; generated tables (stx-genesis, genesis_data.rs) and
    # build.rs; vendored third-party code under deps_common/ (bitcoin, httparse, bech32,
    # ctrlc); the contrib/ tools and stacks-profiler; sample/ example contracts; and the
    # *-testnet / *.tests.clar network- and test-only contract bodies. A defect in any of
    # these is only in scope when it is reachable from the audited code above.
    # =================================================================================
]


target_scopes = [
    "Critical. A SIGNER MUST SIGN ONLY A BLOCK THAT IS ACTUALLY VALID. `v0/signer.rs` validates a miner's `BlockProposal` by calling the node's block-proposal check (`postblock_proposal.rs`) and its own `chainstate` rules, then signs the `signer_signature_hash`. Show a miner-crafted proposal a signer signs though it is invalid: a proposal whose `signer_signature_hash` covers different bytes than the block the node validated, a block whose transactions pass the proposal endpoint but violate a rule the signer assumed the node enforced, a validation result cached against the wrong block id, a `BlockResponse::Accepted` produced before validation completes (a stall/timeout path that defaults to accept). Identity: the block a signer's signature authenticates == the exact block the validation it relied on proved valid.",

    "Critical. NEVER TWO SIGNATURES AT ONE HEIGHT. `signerdb.rs` and `v0/signer_state.rs` record what this signer has already signed so it never signs two conflicting blocks for the same tenure/height (equivocation). Show a signer induced to sign two different blocks at the same height: a signerdb key that omits a distinguishing field so a second block looks already-decided or looks new, a reorg claim (`chainstate` v2) that resets the signer's state and lets a competing block be signed, a restart that loses the last-signed record, a proposal whose height/tenure the signer reads from the miner instead of the canonical view. Identity: for each (reward cycle, tenure, height), the number of distinct blocks this signer signs == at most one.",

    "Critical. THE SIGNER'S CANONICAL VIEW MUST NOT BE STEERED BY THE MINER. `chainstate/v1.rs` and `chainstate/v2.rs` decide whether a proposed block is a valid continuation of the canonical tip - the tenure it extends, whether a claimed reorg is allowed, the burn view it assumes. The miner supplies the proposal; the signer must judge it against its own node's view. Show a proposal that makes the signer accept a block building on a non-canonical parent, a reorg deeper than the rules permit, or a tenure the miner did not win: a burn-block or sortition field trusted from the proposal, a reorg-depth or time-based rule (`chainstate` v2) an attacker satisfies with a stalled or forked burn view, a parent tenure id the signer does not re-derive. Identity: the parent and tenure the signer approves for a block == the parent and tenure the canonical sortition and chain actually establish.",

    "Critical. THE VALIDATION AUTHORIZATION MUST FAIL CLOSED. `postblock_proposal.rs` gates block-proposal validation behind a configured `auth_token`; the signer submits proposals with it, and a test fault-injection stall (`fault_injection_validation_stall`) exists. Show a path where validation is bypassed or defaults open: a missing `auth_token` treated as 'allow' rather than 'disabled', a stall or timeout in validation that returns an accept-like result to the signer, a proposal whose validation is skipped because a cache hit matches on an insufficient key, the fault-injection hook reachable in a release build. Identity: every block a signer treats as node-validated == a block the node's proposal endpoint actually ran full validation on and returned valid for.",

    "Critical. THE SIGNATURE DOMAIN MUST BIND CHAIN, CYCLE AND MESSAGE. The signer signs over a hash built from the SIP-018 domain in `signed_structured_data.rs` and the block's `signer_signature_hash`; `signers.clar` / `signers-voting.clar` and `libsigner/v0/messages.rs` define the message and slot semantics. Show a signature valid in one context reused in another: a domain that omits `chain-id` or reward cycle so a testnet or prior-cycle signature counts, a `SignerMessage` whose type is not bound into the hash so a rejection is replayed as an acceptance, an aggregate-key or vote message reused across rounds, a block-response message whose slot the signer writes without binding the current tenure. Identity: every signer signature == valid for exactly one (chain, reward cycle, tenure, block, message-type).",

    "High. THE SIGNER MUST NOT BE WEDGED INTO NEVER SIGNING A VALID BLOCK. `runloop.rs`, `v0/signer.rs` and `v0/signer_state.rs` move the signer through per-block states; a stuck state means the signer stops signing and, if enough signers stick, the chain stalls (liveness). Show a miner-reachable proposal or message sequence that permanently wedges a signer's state machine for a tenure: a malformed proposal that leaves the state neither accepted nor rejected, a reorg handler that loops, a signerdb write that fails and is treated as success so the signer waits forever, a timeout that never fires. Name the impact as temporary or permanent liveness loss. Identity: for every valid canonical block proposed, the signer reaches a terminal sign-or-reject decision in bounded time.",

    "High. THE SIGNER SET AND WEIGHT THE SIGNER ASSUMES MUST MATCH CONSENSUS. `libsigner/src/signer_set.rs`, `stacks-signer` config and `nakamoto/signer_set.rs` tell each signer its index, the current reward set and the weight threshold. Show a signer acting on a stale or wrong set: signing for a cycle it is no longer in, computing the aggregate/threshold from a reward set that differs from the node's, or a slot index that maps to another signer so its response is attributed wrongly. Name the impact (a block finalized with mis-counted weight, or a signer's vote miscredited). Identity: the reward set, index and threshold the signer acts under == the ones consensus derived for that cycle.",

    "Critical. A REJECTION MUST NOT BE CONVERTIBLE INTO AN ACCEPTANCE. `libsigner/v0/messages.rs` defines `BlockResponse` (accepted/rejected) and its serialization; `stackerdb_listener` / `signer_coordinator` (node side) aggregate them into the block's signature set. Show a signer's rejection or abstention that the aggregation counts as a signature, or an acceptance for block A counted toward block B: a `BlockResponse` whose signature covers a hash shared by two blocks, a rejection message whose bytes deserialize to an acceptance under a lenient parser, a signature slot overwritten so a later rejection does not undo an earlier accept. Identity: the weight aggregated toward finalizing a block == the summed weight of accept responses whose signatures verify over exactly that block's hash.",

    "High. SIGNER STATE PERSISTED MUST SURVIVE RESTART CONSISTENTLY. `signerdb.rs` persists prior decisions, block info and burn state; a signer restarts often. Show a restart or migration where the persisted state is read back inconsistently so the signer re-signs, forgets a rejection, or reprocesses a tenure: a schema/migration that drops the equivocation guard, a serialized block info that round-trips to a different id, a burn-height cursor read stale so the signer validates against an old view. Identity: the decisions and view a signer holds after restart == the decisions and view it held before, for every tenure not yet finalized.",

    "Critical. THE MISSING INVARIANT - what nobody built. Nothing external forces a signer's signature to be over the same bytes the node validated; the equivocation guard relies on a signerdb key assumed to distinguish every conflicting block; the canonical view the signer judges against is assumed independent of miner-supplied proposal fields; validation is assumed to fail closed on stall; a rejection is assumed impossible to recount as acceptance. Identify the FIRST place one of these unstated signing-safety assumptions is violated by a miner or peer an unprivileged party can be (winning one slot, gossiping proposals/messages), prove it with a Rust test in `stacks-signer` or `libsigner` that drives the signer state machine with crafted proposals and asserts either the signed-versus-validated equality or the at-most-one-per-height guard before and after, and show the impact is a signer signing an invalid or non-canonical block, signing two conflicting blocks, or being wedged - any of which threatens chain safety or liveness once enough signers share it.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate signer-decision and block-proposal-validation audit questions for one
    stacks-core target.

    ```
    target_file format:
    "'File Name: stacks-signer/src/v0/signer.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate Nakamoto-signer security audit questions for this exact stacks-core target:

    {target_file}

    Project focus:
    A Nakamoto block is final only when signers holding enough reward-set weight sign it. Each
    signer runs `stacks-signer` (`runloop.rs`, `v0/signer.rs`, `v0/signer_state.rs`,
    `signerdb.rs`) and its `chainstate` v1/v2 rules to decide, from a miner-supplied
    `BlockProposal` and its own node's view (`stacks_client.rs`, `postblock_proposal.rs`),
    whether to sign the `signer_signature_hash`. The signer must (a) sign only a block that is
    actually valid; (b) sign only a canonical continuation the miner did not fabricate; (c)
    never sign two conflicting blocks at one height. Anything that makes a signer sign an
    invalid or non-canonical block, sign twice, or get wedged into never signing a valid one,
    is the bug.

    Rules:
    * Treat `File Name:` as the exact file.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Rust and Clarity symbols (function, struct, enum variant like BlockResponse,
      constant, trait, define-* name) as they appear in the file.
    * EVERY question must close on an equality that must hold across the signer's decision -
      signed-versus-validated, one-per-height, approved-parent-versus-canonical - or name a
      precise state-machine wedge with a liveness impact. State it explicitly.
    * Attacker is unprivileged only: a party who can win a single miner slot (with their own
      BTC) and thus craft `BlockProposal`s, and who can gossip signer/StackerDB messages a
      signer consumes. They run at most one honest signer's worth of the set, not a majority.
    * Attacker is NOT a majority of signers, not a node operator or the victim signer, and
      holds no other signer's private key or the validation `auth_token`. No compromised
      dependency; no social engineering; no local access to a signer host.
    * PROGRAM EXCLUSIONS - a question landing in any of these wastes the whole batch:
      - The P2P/RPC transport and StackerDB sync mechanics, and node-side consensus block
        acceptance, are other variants and OUT OF SCOPE here (use them only as the channel);
        so are README, tests, benches and config.
      - Pure volumetric DoS and resource flooding are OUT OF SCOPE; a single-proposal wedge or
        a safety violation IS in scope (name it).
      - Defects in secp256k1, serde or rusqlite with no path through the signer's logic are
        OUT OF SCOPE; a weakness here that misuses them is IN scope.
      - Also excluded: leaked signer keys, privileged accounts, centralization risk,
        best-practice notes, feature requests, price assumptions, and theoretical findings.
    * IN-SCOPE IMPACTS - every question must land on one and name it:
      Critical: a signer signing an invalid block, a non-canonical block, or two conflicting
      blocks at one height (chain safety) - anything that, shared across enough signers,
      finalizes a bad block or splits the chain; a rejection recounted as an acceptance; a
      signature valid across chain/cycle/tenure boundaries.
      High: a signer wedged into never signing valid blocks (liveness); a signer acting on a
      stale reward set/threshold; a restart that loses the equivocation guard.
    * Every question must be a concrete real-world scenario a party with one miner slot (and
      gossip access) can execute against honest signers running current code.
    * A rejection or stall is a finding only when it wedges liveness or converts into an
      unsafe signature - say which.
    * Generate 20 to 40 high-signal questions.
    * At least 70% must land on a Critical impact rather than a High one.
    * Every question must be testable with a Rust test in `stacks-signer` or `libsigner`
      driving the signer state machine locally. Never propose testing on mainnet or a public
      testnet.
    * Avoid generic checklist questions and repeated root causes.
    * Prefer questions that name TWO values that must be equal (signed vs validated, approved
      parent vs canonical, aggregated weight vs verified accepts) or a precise wedge site.

    Known dead ends - do NOT generate questions about these:
    * Anything needing a majority of signers, another signer's key, or the validation auth_token.
    * The transport/StackerDB sync internals or node consensus acceptance as the flaw itself.
    * Volumetric DoS or flooding.
    * A dependency CVE with no path through the signer's logic, or findings only in tests/tooling.

    Core equalities (each question must close on one):
    * VALIDITY: the block a signature authenticates == the block the relied-on validation proved valid.
    * UNIQUENESS: distinct blocks a signer signs per (cycle, tenure, height) == at most one.
    * CANONICITY: the parent/tenure the signer approves == the canonical sortition's, not the
      miner's claim.
    * FAIL-CLOSED: every block treated as validated == one the node actually fully validated;
      a rejection never counts as an accept.
    * LIVENESS/DOMAIN: bounded decision for every valid block; each signature bound to one
      (chain, cycle, tenure, block, message-type).

    Each question must include:
    1. target function, struct, enum variant or define-* name;
    2. attacker action (a concrete proposal or message with the fields that matter);
    3. preconditions (cycle, reward set, tip, prior signer state);
    4. call sequence through validation, chainstate rules and signerdb;
    5. the equality or wedge, written explicitly;
    6. scoped impact and which safety/liveness property breaks;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Method: function_or_struct] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, breaking the equality/wedge EQUALITY, causing scoped impact: SCOPE_IMPACT against PARTY? Proof idea: Rust signer test PARAMETERS asserting VALIDITY, UNIQUENESS, CANONICITY, FAIL_CLOSED, or LIVENESS_DOMAIN.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a signer-decision exploit-validation prompt for stacks-core.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: a party who can win a single miner slot with their own BTC and craft BlockProposals, and gossip signer/StackerDB messages a signer consumes, running at most one signer's weight. They are not a majority of signers, not a node operator or the victim signer, and hold no other signer's key or the validation auth_token, with no local access to a signer host.
- Reject majority-signer, compromised-dependency, social-engineering and local-access assumptions, and any path requiring a privileged role or the auth_token.
- OUT OF SCOPE, reject on sight: P2P/RPC transport and StackerDB sync mechanics, node-side consensus acceptance (as the flaw itself); README, tests, benches, config; volumetric DoS and flooding; secp256k1/serde/rusqlite defects with no path through the signer's logic; price assumptions; best-practice notes; theoretical findings.
- The impact must be one of: Critical - a signer signing an invalid, non-canonical, or conflicting block (chain safety), a rejection recounted as acceptance, a signature valid across chain/cycle/tenure boundaries; High - a signer wedged into never signing valid blocks (liveness), acting on a stale reward set/threshold, or losing the equivocation guard on restart.
- Focus on real impact: a safety property (validity, uniqueness, canonicity, fail-closed) broken, or a bounded-liveness guarantee lost.

## Validate
- Write the equality or wedge the question claims BEFORE tracing any code.
- Trace the exact reachable path from the crafted proposal/message and record every read and write of the validated block id, the `signer_signature_hash`, the signerdb equivocation record, the canonical parent/tenure the chainstate rules derive, the reward set/threshold, and the BlockResponse aggregation.
- Evaluate the equality before and after, or locate the exact wedge state. If the guard holds, output no vulnerability.
- Check whether the node validation call, the chainstate v1/v2 reorg rules, the signerdb key, the auth gate, the signature domain, or the state-machine timeouts already prevent it.
- State what the attacker achieves and whether it needs only one slot plus gossip, and whether it is repeatable.
- Require exact file/function support and a reproducible Rust test driving the signer state machine.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[The broken equality or wedge, the code path, root cause, the attacker's exact proposal/message, exploit flow, and why existing guards fail]

### Impact Explanation
[Which safety or liveness property breaks, what a shared exploit finalizes or stalls, repeatability, matching severity category]

### Likelihood Explanation
[Preconditions, cycle/tip/state required, attacker cost (one slot plus gossip), feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Rust signer test plan with the exact assertions on both sides of the equality or the wedge]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for stacks-core signer claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- A claim is only valid if the report states the broken equality (signed vs validated, one-per-height, approved-parent vs canonical, aggregated-weight vs verified-accepts) or names a precise state-machine wedge, and shows it concretely. Reject prose-only claims.
- Reject anything requiring a majority of signers, another signer's key, the validation auth_token, a node operator or the victim signer, local access, a compromised dependency, or social engineering.
- OUT OF SCOPE, reject on sight: P2P/RPC transport and StackerDB sync mechanics, node-side consensus acceptance as the flaw itself; README, tests, benches, config; volumetric DoS and flooding; secp256k1/serde/rusqlite defects with no path through the signer's logic; price assumptions; centralization risk; best-practice notes; feature requests; theoretical findings.
- The impact must be one of: Critical - a signer signing an invalid, non-canonical, or conflicting block, a rejection recounted as acceptance, a cross-context-valid signature; High - a signer wedged into never signing valid blocks, acting on a stale reward set/threshold, or losing the equivocation guard on restart.
- Reject claims needing a majority of signers or with no safety/liveness consequence when shared across the set.
- Reject if the bug was already fixed, publicly disclosed, or covered by a known-issues list.
- A valid report must be triggerable by a party with one miner slot plus gossip access against honest signers on current code.
- A PoC is mandatory. Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function/struct/enum/define-*, and line references.
2. The equality or wedge written explicitly, with both sides or the stuck state shown.
3. Clear root cause: which validity, uniqueness, canonicity, fail-closed, domain or state-machine gap causes it.
4. Reachable exploit path: preconditions -> crafted proposal/message -> validation, chainstate rules and signerdb sequence -> observed divergence or wedge.
5. The node validation call, chainstate v1/v2 rules, the signerdb key, the auth gate, the signature domain and the timeouts reviewed and shown insufficient.
6. Impact stated concretely: which property breaks and what it finalizes or stalls when shared, and repeatability.
7. Reproducible proof: Rust test driving the signer state machine with the asserted values.

## Silent Triage Questions
Before output, internally answer:
- What exactly is the equality or wedge, and does it actually occur?
- Can a one-slot miner plus gossip trigger it with no other signer's key and no auth_token?
- Is the flaw in the signer's own decision logic, not in transport, node consensus or a dependency?
- Which safety/liveness property breaks, and what happens when enough signers share it?
- Would an Immunefi triager accept it under the Blockchain/DLT severity system?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the broken equality/wedge and impact]

## Finding Description
[Exact code path, the equality or wedge, root cause, exploit flow, and why existing guards fail]

## Impact Explanation
[Which safety/liveness property breaks, what a shared exploit finalizes or stalls, affected party, repeatability, severity category]

## Likelihood Explanation
[Attacker capability, preconditions, state required, cost, feasibility]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or Rust signer test plan with concrete assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for the stacks-core signer subsystem.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope repo context only (`stacks-signer/src/**` including chainstate v1/v2 and signerdb, the `libsigner/v0` message and state types, and node-side `postblock_proposal.rs` / signer_set / coordinator). Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only analogs a one-slot miner (plus gossip) can trigger that break an equality (signed vs validated, one-per-height, approved-parent vs canonical, aggregated-weight vs verified-accepts) or wedge the state machine: a signer signing an invalid/non-canonical/conflicting block, a rejection recounted as an accept, a cross-context-valid signature, or a liveness wedge.
- OUT OF SCOPE, reject on sight: transport/StackerDB sync mechanics, node consensus acceptance as the flaw itself; README, tests, benches, config; volumetric DoS and flooding; secp256k1/serde/rusqlite defects with no path through the signer's logic; anything requiring a majority of signers, another signer's key, the auth_token or local access; price assumptions; best-practice notes; theoretical findings.
- The impact must be one of: Critical - a signer signing an invalid, non-canonical, or conflicting block, a rejection recounted as acceptance, a cross-context-valid signature; High - a signer wedged into never signing valid blocks, acting on a stale reward set/threshold, or losing the equivocation guard on restart.
- Reject analogs needing a majority or with no safety/liveness consequence.

## Validate
- Map the bug class to the strongest reachable path in this repo and state the equality or wedge it would break.
- Evaluate both sides before and after the crafted proposal/message, or locate the wedge.
- Prove root cause with exact file/function support.
- Accept only a concrete safety break (invalid/non-canonical/conflicting signature, miscounted response, cross-context signature) or a liveness wedge.

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
