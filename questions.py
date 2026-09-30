import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 30
# todo: the path from https://github.com/tronprotocol/java-tron
SOURCE_REPO = "tronprotocol/java-tron"
# todo: the name of the repository
REPO_NAME = "java-tron"
run_number = os.environ.get('GITHUB_RUN_NUMBER') or os.environ.get('CI_PIPELINE_IID', '0')


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
    # Transaction admission: signature, permission, tapos and duplicate checks every
    # broadcast transaction passes before it is executed
    # =================================================================================
    "chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/BlockCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/TransactionInfoCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/TransactionResultCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/TransactionRetCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/utils/TransactionUtil.java",
    "chainbase/src/main/java/org/tron/core/db/TransactionTrace.java",
    "chainbase/src/main/java/org/tron/core/db/TransactionStore.java",
    "chainbase/src/main/java/org/tron/core/db/TransactionCache.java",
    "chainbase/src/main/java/org/tron/core/db/RecentTransactionStore.java",
    "chainbase/src/main/java/org/tron/core/db/RecentBlockStore.java",
    "chainbase/src/main/java/org/tron/core/db2/common/TxCacheDB.java",
    "actuator/src/main/java/org/tron/core/utils/TransactionUtil.java",
    "actuator/src/main/java/org/tron/core/utils/TransactionRegister.java",

    # =================================================================================
    # Block pipeline: the code every honest node runs on a block carrying the
    # attacker's transaction - divergence or a throw here halts or splits the chain
    # =================================================================================
    "framework/src/main/java/org/tron/core/db/Manager.java",
    "framework/src/main/java/org/tron/core/db/PendingManager.java",
    "framework/src/main/java/org/tron/core/db/HistoryBlockHashUtil.java",
    "framework/src/main/java/org/tron/core/db/accountstate/callback/AccountStateCallBack.java",
    "framework/src/main/java/org/tron/core/db/accountstate/TrieService.java",
    "chainbase/src/main/java/org/tron/core/ChainBaseManager.java",
    "chainbase/src/main/java/org/tron/core/db/KhaosDatabase.java",
    "chainbase/src/main/java/org/tron/core/db/BlockStore.java",
    "chainbase/src/main/java/org/tron/core/db/BlockIndexStore.java",
    "chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java",
    "chainbase/src/main/java/org/tron/core/db/TronDatabase.java",
    "chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java",
    "chainbase/src/main/java/org/tron/core/db2/core/SnapshotImpl.java",
    "chainbase/src/main/java/org/tron/core/db2/core/SnapshotRoot.java",
    "chainbase/src/main/java/org/tron/core/db2/core/AbstractSnapshot.java",
    "chainbase/src/main/java/org/tron/core/db2/core/Chainbase.java",
    "chainbase/src/main/java/org/tron/common/utils/ForkController.java",
    "chainbase/src/main/java/org/tron/common/utils/ForkUtils.java",
    "chainbase/src/main/java/org/tron/core/capsule/utils/BlockUtil.java",
    "chainbase/src/main/java/org/tron/core/capsule/utils/MerkleTree.java",
    "common/src/main/java/org/tron/common/utils/MerkleRoot.java",

    # =================================================================================
    # Consensus and reward accounting reachable from an ordinary vote, delegation
    # or withdrawal transaction
    # =================================================================================
    "consensus/src/main/java/org/tron/consensus/dpos/DposService.java",
    "consensus/src/main/java/org/tron/consensus/dpos/DposSlot.java",
    "consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java",
    "consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java",
    "consensus/src/main/java/org/tron/consensus/dpos/StatisticManager.java",
    "consensus/src/main/java/org/tron/consensus/dpos/StateManager.java",
    "consensus/src/main/java/org/tron/consensus/ConsensusDelegate.java",
    "consensus/src/main/java/org/tron/consensus/pbft/PbftManager.java",
    "consensus/src/main/java/org/tron/consensus/pbft/PbftMessageHandle.java",
    "consensus/src/main/java/org/tron/consensus/pbft/message/PbftBaseMessage.java",
    "consensus/src/main/java/org/tron/consensus/pbft/message/PbftMessage.java",
    "chainbase/src/main/java/org/tron/core/capsule/PbftSignCapsule.java",
    "chainbase/src/main/java/org/tron/core/service/MortgageService.java",
    "chainbase/src/main/java/org/tron/core/service/RewardViCalService.java",
    "chainbase/src/main/java/org/tron/core/store/DelegationStore.java",
    "chainbase/src/main/java/org/tron/core/store/WitnessStore.java",
    "chainbase/src/main/java/org/tron/core/store/WitnessScheduleStore.java",
    "chainbase/src/main/java/org/tron/core/store/VotesStore.java",
    "chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/WitnessCapsule.java",
    "framework/src/main/java/org/tron/core/consensus/ProposalController.java",
    "framework/src/main/java/org/tron/core/consensus/ProposalService.java",

    # =================================================================================
    # Actuators: one per broadcastable contract type - the value-moving surface any
    # funded address reaches directly
    # =================================================================================
    "actuator/src/main/java/org/tron/core/actuator/AbstractActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ActuatorCreator.java",
    "actuator/src/main/java/org/tron/core/actuator/ActuatorFactory.java",
    "actuator/src/main/java/org/tron/core/actuator/TransferActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/CreateAccountActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UnfreezeAssetActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java",
    "actuator/src/main/java/org/tron/core/actuator/CancelAllUnfreezeV2Actuator.java",
    "actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UpdateBrokerageActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/WitnessUpdateActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ProposalCreateActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ProposalApproveActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ProposalDeleteActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ClearABIContractActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java",
    "actuator/src/main/java/org/tron/core/utils/ProposalUtil.java",

    # =================================================================================
    # Bancor exchange and the on-chain market: attacker-chosen quantities drive the
    # pricing and order-matching arithmetic directly
    # =================================================================================
    "actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java",
    "chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java",
    "chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java",
    "chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java",
    "chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/MarketAccountOrderCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/MarketOrderIdListCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/MarketPriceCapsule.java",
    "chainbase/src/main/java/org/tron/core/store/MarketOrderStore.java",
    "chainbase/src/main/java/org/tron/core/store/MarketAccountStore.java",
    "chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java",
    "chainbase/src/main/java/org/tron/core/store/MarketPairToPriceStore.java",
    "platform/src/main/java/common/org/tron/common/utils/MarketComparator.java",
    "platform/src/main/java/common/org/tron/common/utils/MarketOrderPriceComparatorForLevelDB.java",

    # =================================================================================
    # TVM: interpreter, metering and stateful native contracts an attacker reaches by
    # deploying and calling their own contract
    # =================================================================================
    "actuator/src/main/java/org/tron/core/actuator/VMActuator.java",
    "actuator/src/main/java/org/tron/core/vm/VM.java",
    "actuator/src/main/java/org/tron/core/vm/Op.java",
    "actuator/src/main/java/org/tron/core/vm/Operation.java",
    "actuator/src/main/java/org/tron/core/vm/OperationActions.java",
    "actuator/src/main/java/org/tron/core/vm/OperationRegistry.java",
    "actuator/src/main/java/org/tron/core/vm/JumpTable.java",
    "actuator/src/main/java/org/tron/core/vm/EnergyCost.java",
    "actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java",
    "actuator/src/main/java/org/tron/core/vm/program/Program.java",
    "actuator/src/main/java/org/tron/core/vm/program/Memory.java",
    "actuator/src/main/java/org/tron/core/vm/program/Stack.java",
    "actuator/src/main/java/org/tron/core/vm/program/Storage.java",
    "actuator/src/main/java/org/tron/core/vm/program/ContractState.java",
    "actuator/src/main/java/org/tron/core/vm/program/ProgramPrecompile.java",
    "actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvokeFactory.java",
    "actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvokeImpl.java",
    "actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java",
    "actuator/src/main/java/org/tron/core/vm/repository/Key.java",
    "actuator/src/main/java/org/tron/core/vm/repository/Value.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceV2Processor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/CancelAllUnfreezeV2Processor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawExpireUnfreezeProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawRewardProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java",
    "actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java",
    "actuator/src/main/java/org/tron/core/vm/utils/MUtil.java",
    "actuator/src/main/java/org/tron/core/vm/VMUtils.java",
    "actuator/src/main/java/org/tron/core/vm/config/ConfigLoader.java",
    "common/src/main/java/org/tron/core/vm/config/VMConfig.java",
    "common/src/main/java/org/tron/common/runtime/vm/DataWord.java",
    "common/src/main/java/org/tron/common/runtime/vm/LogInfo.java",
    "framework/src/main/java/org/tron/common/runtime/RuntimeImpl.java",
    "chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java",
    "chainbase/src/main/java/org/tron/common/runtime/ProgramResult.java",

    # =================================================================================
    # Resource model: bandwidth and energy metering that decides whether an attacker
    # pays for what they consume
    # =================================================================================
    "chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java",
    "chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java",
    "chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java",
    "chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java",
    "chainbase/src/main/java/org/tron/core/store/AccountStore.java",
    "chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java",
    "chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java",
    "chainbase/src/main/java/org/tron/core/store/AccountIndexStore.java",
    "chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java",
    "chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java",
    "chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/ContractStateCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/AssetIssueCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/utils/AssetUtil.java",
    "chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java",
    "chainbase/src/main/java/org/tron/core/store/AssetIssueV2Store.java",
    "chainbase/src/main/java/org/tron/core/store/ContractStore.java",
    "chainbase/src/main/java/org/tron/core/store/ContractStateStore.java",
    "chainbase/src/main/java/org/tron/core/store/CodeStore.java",
    "chainbase/src/main/java/org/tron/core/store/AbiStore.java",
    "chainbase/src/main/java/org/tron/core/store/StorageRowStore.java",
    "chainbase/src/main/java/org/tron/core/store/ProposalStore.java",
    "chainbase/src/main/java/org/tron/core/store/ExchangeStore.java",
    "chainbase/src/main/java/org/tron/core/store/ExchangeV2Store.java",
    "chainbase/src/main/java/org/tron/common/utils/Commons.java",

    # =================================================================================
    # Cryptographic primitives behind signature recovery, address derivation and hashing
    # =================================================================================
    "crypto/src/main/java/org/tron/common/crypto/ECKey.java",
    "crypto/src/main/java/org/tron/common/crypto/Rsv.java",
    "crypto/src/main/java/org/tron/common/crypto/SignUtils.java",
    "crypto/src/main/java/org/tron/common/crypto/Hash.java",
    "crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java",
    "crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java",
    "crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G1.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/Fp12.java",
    "crypto/src/main/java/org/tron/common/crypto/cryptohash/Keccak256.java",
    "common/src/main/java/org/tron/common/utils/Sha256Hash.java",
    "common/src/main/java/org/tron/common/utils/DecodeUtil.java",
    "common/src/main/java/org/tron/common/utils/Base58.java",
    "common/src/main/java/org/tron/common/utils/Bech32.java",
    "common/src/main/java/org/tron/common/utils/ByteArray.java",
    "common/src/main/java/org/tron/common/utils/ByteUtil.java",
    "common/src/main/java/org/tron/common/utils/BIUtil.java",
    "common/src/main/java/org/tron/common/utils/CompactEncoder.java",
    "common/src/main/java/org/tron/common/math/Maths.java",
    "common/src/main/java/org/tron/common/math/StrictMathWrapper.java",
    "framework/src/main/java/org/tron/core/trie/TrieImpl.java",
    "framework/src/main/java/org/tron/core/trie/TrieKey.java",
    "framework/src/main/java/org/tron/core/capsule/utils/RLP.java",

    # =================================================================================
    # Shielded transaction path: note commitments, nullifiers and merkle vouchers an
    # attacker supplies wholesale
    # =================================================================================
    "framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java",
    "framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java",
    "framework/src/main/java/org/tron/core/zen/note/Note.java",
    "framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java",
    "framework/src/main/java/org/tron/core/zen/address/KeyIo.java",
    "framework/src/main/java/org/tron/core/zen/address/SpendingKey.java",
    "chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java",
    "chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java",
    "chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleVoucherContainer.java",
    "chainbase/src/main/java/org/tron/common/zksnark/MerklePath.java",
    "chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java",
    "chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java",
    "chainbase/src/main/java/org/tron/core/store/NullifierStore.java",
    "chainbase/src/main/java/org/tron/core/store/IncrementalMerkleTreeStore.java",

    # =================================================================================
    # Public query and broadcast API: HTTP, gRPC and JSON-RPC surfaces any anonymous
    # client can call on a public FullNode
    # =================================================================================
    "framework/src/main/java/org/tron/core/Wallet.java",
    "framework/src/main/java/org/tron/core/services/RpcApiService.java",
    "framework/src/main/java/org/tron/core/services/NodeInfoService.java",
    "framework/src/main/java/org/tron/core/services/WalletOnCursor.java",
    "framework/src/main/java/org/tron/core/services/interfaceOnSolidity/WalletOnSolidity.java",
    "framework/src/main/java/org/tron/core/services/interfaceOnPBFT/WalletOnPBFT.java",
    "framework/src/main/java/org/tron/core/services/http/Util.java",
    "framework/src/main/java/org/tron/core/services/http/PostParams.java",
    "framework/src/main/java/org/tron/core/services/http/JsonFormat.java",
    "framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java",
    "framework/src/main/java/org/tron/core/services/http/BroadcastServlet.java",
    "framework/src/main/java/org/tron/core/services/http/BroadcastHexServlet.java",
    "framework/src/main/java/org/tron/core/services/http/TriggerSmartContractServlet.java",
    "framework/src/main/java/org/tron/core/services/http/TriggerConstantContractServlet.java",
    "framework/src/main/java/org/tron/core/services/http/EstimateEnergyServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionSignWeightServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionApprovedListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetBlockByLimitNextServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetPaginatedExchangeListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetPaginatedProposalListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetMarketOrderListByPairServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexServlet.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/types/BuildArguments.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/types/CallArguments.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/types/BlockResult.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionResult.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionReceipt.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilterWrapper.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogBlockQuery.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogMatch.java",
    "framework/src/main/java/org/tron/core/services/filter/HttpApiAccessFilter.java",
    "framework/src/main/java/org/tron/core/services/filter/LiteFnQueryHttpFilter.java",
    "framework/src/main/java/org/tron/core/services/filter/LiteFnQueryGrpcInterceptor.java",
    "framework/src/main/java/org/tron/core/services/filter/CachedBodyRequestWrapper.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterContainer.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/GlobalRateLimiter.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/RpcApiAccessInterceptor.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/adapter/IPQPSRateLimiterAdapter.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/adapter/GlobalPreemptibleAdapter.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/adapter/QpsRateLimiterAdapter.java",

    # =================================================================================
    # Event and log derivation driven by attacker-authored contract output
    # =================================================================================
    "framework/src/main/java/org/tron/common/logsfilter/ContractEventParser.java",
    "framework/src/main/java/org/tron/common/logsfilter/ContractEventParserAbi.java",
    "framework/src/main/java/org/tron/common/logsfilter/ContractEventParserJson.java",
    "framework/src/main/java/org/tron/common/logsfilter/capsule/ContractTriggerCapsule.java",
    "framework/src/main/java/org/tron/common/logsfilter/capsule/TransactionLogTriggerCapsule.java",
    "framework/src/main/java/org/tron/common/runtime/LogEventWrapper.java",
    "framework/src/main/java/org/tron/core/services/event/BlockEventGet.java",
    "framework/src/main/java/org/tron/core/services/event/BlockEventCache.java",
    "chainbase/src/main/java/org/tron/common/bloom/Bloom.java",
    "chainbase/src/main/java/org/tron/core/store/SectionBloomStore.java",

    # =================================================================================
    # Storage engine and iteration primitives every unbounded query bottoms out in
    # =================================================================================
    "chainbase/src/main/java/org/tron/common/storage/leveldb/LevelDbDataSourceImpl.java",
    "chainbase/src/main/java/org/tron/common/storage/rocksdb/RocksDbDataSourceImpl.java",
    "chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java",
    "chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java",
    "chainbase/src/main/java/org/tron/core/db/common/iterator/DBIterator.java",
    "chainbase/src/main/java/org/tron/core/db2/common/LevelDB.java",
    "chainbase/src/main/java/org/tron/core/db2/common/RocksDB.java",
    "chainbase/src/main/java/org/tron/core/db2/common/WrappedByteArray.java",
    "common/src/main/java/org/tron/common/cache/TronCache.java",
    "common/src/main/java/org/tron/common/utils/SlidingWindowCounter.java",
    "common/src/main/java/org/tron/common/utils/StringUtil.java",
    "common/src/main/java/org/tron/common/utils/JsonUtil.java",
    "common/src/main/java/org/tron/json/JSONObject.java",
    "common/src/main/java/org/tron/json/JSONArray.java",
]


target_scopes = [
    "Critical. An unprivileged attacker gets a transaction accepted against an account whose key they do not hold, performing an unauthorized account operation: validateSignature, checkWeight, getWeight, getPermission and getApprovedList in chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java, validate in actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java, signatureToKeyBytes, recoverFromSignature, verify and validateComponents in crypto/src/main/java/org/tron/common/crypto/ECKey.java, fromSignature in Rsv.java, SignUtils.java dispatch, or validateMultiSign and ecRecover in actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java accept a malleable, over-length, duplicated or reordered signature so the weight threshold is met without the owner's key.",
    "Critical. A single broadcast transaction makes honest FullNodes disagree on the resulting state or block hash, forcing an unintended chain split that needs a hard fork: processTransaction, applyBlock, pushBlock, consumeBandwidth and validateTapos in framework/src/main/java/org/tron/core/db/Manager.java, pass and passNew in chainbase/src/main/java/org/tron/common/utils/ForkController.java, the getEnergyLimit/hasEnergy paths in chainbase/src/main/java/org/tron/core/db/TransactionTrace.java, VMConfig feature flags read in actuator/src/main/java/org/tron/core/vm/config/ConfigLoader.java, or the pow/round/multiplyAndDivide helpers in common/src/main/java/org/tron/common/math/Maths.java and StrictMathWrapper.java produce a version-, JDK- or ordering-dependent result that only some nodes reproduce.",
    "Critical. A transaction or a contract call an attacker broadcasts throws an unhandled error inside block application, so every node that processes the containing block crashes, wedges or stops confirming new transactions: processTransaction and applyBlock in framework/src/main/java/org/tron/core/db/Manager.java, push and getBlock in chainbase/src/main/java/org/tron/core/db/KhaosDatabase.java, merge, flush and revoke in chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java and SnapshotImpl.java, execute in actuator/src/main/java/org/tron/core/actuator/VMActuator.java, or the exception mapping in TransactionTrace.java and framework/src/main/java/org/tron/common/runtime/RuntimeImpl.java turn attacker-chosen contract data into a node-fatal throw rather than a rejected transaction.",
    "Critical. An attacker mints, duplicates or destroys balance that was never backed, breaking TRX or TRC10 supply conservation: execute and validate in TransferActuator.java, TransferAssetActuator.java, ParticipateAssetIssueActuator.java, AssetIssueActuator.java and UnfreezeAssetActuator.java under actuator/src/main/java/org/tron/core/actuator/, addBalance, setBalance, addAssetAmountV2 and reduceAssetAmountV2 in chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java, adjustBalance and adjustAssetBalanceV2 in chainbase/src/main/java/org/tron/common/utils/Commons.java, or the exchange/withdraw arithmetic in chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java let a chosen amount, precision or asset id overflow, truncate or credit twice.",
    "Critical. The stake, delegation and reward accounting pays an attacker value they never staked or permanently freezes a victim's principal: execute and validate in FreezeBalanceV2Actuator.java, UnfreezeBalanceV2Actuator.java, CancelAllUnfreezeV2Actuator.java, WithdrawExpireUnfreezeActuator.java, DelegateResourceActuator.java, UnDelegateResourceActuator.java and WithdrawBalanceActuator.java under actuator/src/main/java/org/tron/core/actuator/, the mirrored processors in actuator/src/main/java/org/tron/core/vm/nativecontract/, getCanDelegatedMaxSize and getCanWithdrawUnfreezeAmount in actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java, and withdrawReward, queryReward, computeReward and adjustAllowance in chainbase/src/main/java/org/tron/core/service/MortgageService.java miscount unfreezing entries, delegation locks, vote weight or cycle boundaries.",
    "Critical. A contract an attacker deploys and calls executes work the TVM never charges for, or charges differently across nodes, letting them consume block capacity for free or break gas determinism: the opcode handlers in actuator/src/main/java/org/tron/core/vm/OperationActions.java and OperationRegistry.java, spendEnergy, memoryExpand, getMemSize and the CALL/CREATE frames in actuator/src/main/java/org/tron/core/vm/program/Program.java and Memory.java, the cost functions in actuator/src/main/java/org/tron/core/vm/EnergyCost.java, getEnergyLimit and checkEnergyLimit in chainbase/src/main/java/org/tron/core/db/TransactionTrace.java, useEnergy in chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java, or the storage and refund accounting in actuator/src/main/java/org/tron/core/vm/program/Storage.java and vm/repository/RepositoryImpl.java undercharge or double-refund a reachable execution path.",
    "Critical. An attacker escapes bandwidth and fee accounting or evades the duplicate-transaction and expiration checks, so they can flood mainnet with free transactions or replay one: consumeBandwidth, useTransactionFee, useAssetAccountNet and consumeForCreateNewAccount in chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java, consumeBandwidth in ResourceProcessor.java, validateTapos, validateDup, validateCommon and pushTransaction in framework/src/main/java/org/tron/core/db/Manager.java, has and put in chainbase/src/main/java/org/tron/core/db2/common/TxCacheDB.java, chainbase/src/main/java/org/tron/core/db/RecentTransactionStore.java and RecentBlockStore.java, or getTransactionId and getSerializedSize in chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java let two distinct payloads share an id or one payload bypass the size and expiration limits.",
    "Critical. A single anonymous HTTP, gRPC or JSON-RPC request to a public FullNode exhausts memory or blocks the service thread pool until the node stops answering and stops confirming transactions: countVote, getAssetIssueList, getPaginatedAssetIssueList, getPaginatedProposalList, getPaginatedExchangeList, getMarketOrderListByPair, getMarketPairList, getDelegatedResourceAccountIndex, getBlockByLimitNext, triggerConstantContract and estimateEnergy in framework/src/main/java/org/tron/core/Wallet.java, getLogs, ethCall, ethEstimateGas and buildTransaction in framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java with filters/LogFilterWrapper.java and LogBlockQuery.java, parse in framework/src/main/java/org/tron/core/services/http/JsonFormat.java and Util.java, or the limiters in framework/src/main/java/org/tron/core/services/ratelimiter/ perform an unbounded store scan or unchecked allocation driven by one request parameter.",
    "High. An attacker permanently corrupts or wedges shared on-chain state that other users depend on, censoring their transactions or stranding their assets: the order and price indexes in chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java, MarketOrderCapsule.java, MarketAccountOrderCapsule.java and the MarketPairPriceToOrderStore/MarketPairToPriceStore under chainbase/src/main/java/org/tron/core/store/, matching order in MarketSellAssetActuator.java and MarketCancelOrderActuator.java with platform/src/main/java/common/org/tron/common/utils/MarketComparator.java, the nullifier and voucher bookkeeping in chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java and IncrementalMerkleTreeContainer.java with NullifierStore.java, or the pending queue handling in framework/src/main/java/org/tron/core/db/PendingManager.java leave an entry no owner can ever cancel, withdraw or re-broadcast.",
    "Critical/High blind spot. An ordinary funded account or anonymous API client abuses an assumption java-tron never wrote down: two distinct payloads that serialize to the same transaction id or the same store key under ByteArray, WrappedByteArray or a capsule's key builder, a protobuf field that validate() reads but execute() re-reads after mutation, an address accepted by DecodeUtil.addressValid but rejected or normalized elsewhere, a limit enforced on the HTTP servlet but not on the gRPC or JSON-RPC path to the same Wallet method, a proposal-gated feature flag whose old and new branches disagree on stored state, a value that survives the actuator but overflows only once ContractStateCapsule, DelegationStore or SectionBloomStore reads it back, a revoking-session error path that commits half its writes, or a cache in TxCacheDB, TronCache or KhaosDatabase that answers differently from the store it fronts - yielding an unauthorized account operation, unbacked balance, permanently frozen funds, a node crash on block application, an unintended chain split, or an RPC-API the node can no longer serve.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one java-tron target.

    ```
    target_file format:
    "'File Name: actuator/src/main/java/org/tron/core/actuator/TransferActuator.java -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact java-tron target:

    {target_file}

    Project focus:
    java-tron is the TRON mainnet FullNode. Focus only on what an ordinary user reaches: signing and broadcasting any Transaction contract type (Transfer, TransferAsset, AssetIssue, ParticipateAssetIssue, FreezeBalanceV2, UnfreezeBalanceV2, CancelAllUnfreezeV2, WithdrawExpireUnfreeze, DelegateResource, UnDelegateResource, WithdrawBalance, VoteWitness, AccountPermissionUpdate, UpdateAccount, SetAccountId, ExchangeCreate/Inject/Withdraw/Transaction, MarketSellAsset, MarketCancelOrder, CreateSmartContract, TriggerSmartContract, ShieldedTransfer) through a public FullNode, deploying and calling their own TVM contract, and calling the public HTTP, gRPC and JSON-RPC endpoints anonymously. Downstream of that: actuator validate/execute, TVM execution and energy metering, bandwidth and energy consumption, stake/delegation/reward accounting, store writes and indexes, block application on every honest node, and the query paths those endpoints reach.

    Rules:
    * Treat `File Name:` as the exact file/class.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact symbols (Java class, method, field, enum constant, or capsule/store name) when possible.
    * Attacker is unprivileged only: anyone who funds a TRON address and broadcasts signed transactions, deploys and calls their own smart contract, creates their own asset, exchange or market order, or sends anonymous HTTP/gRPC/JSON-RPC requests to a public FullNode. They control only their own keys.
    * Attacker is NOT a super representative, witness, block producer, committee member, node operator or database operator, and holds no other user's key. Never assume a malicious peer, malicious node, malicious SR, p2p/gossip/sync attacker, network-level DoS or flooding, leaked key, non-default config, or social engineering.
    * Out of scope, never ask about: p2p networking and peer handling, block production and witness scheduling by an SR, the toolkit/CLI plugins, node startup and config parsing, metrics and logging, deployment and infra, dependency versions.
    * Ignore test files, mocks, benchmarks, docs, generated protobuf classes, and config-only findings.
    * Every question must describe a real signed transaction, contract deployment, contract call, or single API request the attacker actually submits through a valid entrypoint. No generic unbounded-allocation, memory-growth or resource-exhaustion speculation; no "what if the input is huge" without a concrete submitted payload and a concrete broken invariant.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must target an unauthorized account operation, direct theft or permanent freezing of funds, unbacked balance or supply inflation, a node crash or halt on block application, an unintended chain split between honest nodes, or a public API a FullNode can no longer serve.
    * Every question must be testable by a `./gradlew :actuator:test`, `:chainbase:test`, `:consensus:test`, `:crypto:test`, `:common:test` or `:framework:test` JUnit test, or a single-node block-application flow test.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Authorization: state changes to an account happen only when signatures meeting that account's active permission threshold, counted once per distinct key, are present.
    * Value conservation: TRX and TRC10 debited on one side are credited exactly once on the other; fees, rewards, stake and delegated resources are never created, duplicated or stranded.
    * Metering integrity: every byte and every opcode an attacker causes to execute is charged to a resource they actually own, identically on every node.
    * Determinism: given the same block, every honest node at the same fork version reaches the same state root, receipt and block hash.
    * Availability: no single submitted transaction or API request can crash a node, stop block application, or make a public endpoint permanently unable to answer other users.

    Each question must include:
    1. target class/method;
    2. attacker action (a concrete transaction, contract deployment, contract call or API request: contract type, fields, calldata, parameters);
    3. preconditions (accounts, TRX balance, staked resources, deployed contract, issued asset or created order the attacker relies on);
    4. execution sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger EXECUTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: gradle JUnit test / single-node block-application test PARAMETERS and assert AUTHORIZATION, VALUE_CONSERVATION, METERING_INTEGRITY, DETERMINISM, or AVAILABILITY.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused java-tron exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: anyone who funds a TRON address and broadcasts signed transactions, deploys and calls their own smart contract, issues their own asset or order, or sends anonymous HTTP/gRPC/JSON-RPC requests to a public FullNode. No SR, witness, committee member, node operator, database access, or foreign-key access.
- Reject malicious-SR, malicious-witness, malicious-committee, malicious-peer, malicious-node, p2p/gossip/sync, network-level DoS or request flooding, leaked-key, and misconfiguration-only paths.
- Reject 51%-style, sybil and centralization claims, economic-design critique, self-harm (attacker only damages their own account), and monitoring, CLI/toolkit, logging, deployment, dependency-only, and test/mock/generated/config-only findings.
- Reject generic unbounded-allocation or storage-growth claims with no concrete submitted payload and no broken invariant.
- Focus on real chain impact: an unauthorized operation on an account whose key the attacker lacks, direct theft or permanent freezing of user funds, unbacked balance or supply inflation, a node crash or halt while applying a block, an unintended chain split between honest nodes, private-key or secret disclosure, remote code execution, or a public RPC/HTTP API the node can no longer serve.

## Validate
- Trace the exact reachable path from the attacker's signed transaction, contract call or API request into the affected method.
- Check whether signature and permission verification, actuator validate(), tapos/expiration/duplicate checks, bandwidth and energy metering, fork-version gating via ForkController and VMConfig, store key construction, rate limiters, or existing exception handling already stop it.
- Confirm the path is reachable on current mainnet configuration and the active proposal/fork parameters, not only behind a disabled flag.
- Accept only concrete unauthorized account operation, fund loss or freezing, unbacked balance, node crash or halt, chain split, key disclosure, RCE, or lasting API unavailability.
- Require exact file/method support and a reproducible gradle JUnit or single-node block-application PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker payload, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and severity: Critical (unauthorized account operation, direct theft or permanent freezing of funds, unbacked balance or supply inflation, node takeover or RCE, private-key disclosure, network unable to confirm new transactions, unintended chain split requiring a hard fork) or High (RPC-API or protocol-implementation DoS from a single request or transaction, transaction-origination censorship, corruption of shared on-chain indexes)]

### Likelihood Explanation
[Preconditions, accounts and balance needed, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[gradle JUnit test / single-node block-application test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for java-tron.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only analogs an unprivileged transaction broadcaster, contract deployer, asset issuer, order placer or anonymous API client can reach: signature and permission verification, actuator validate/execute for any broadcastable contract type, TVM opcodes, precompiles and energy metering, bandwidth accounting, stake/delegation/reward math, exchange and market order handling, capsule and store key construction, block application in Manager, or the HTTP/gRPC/JSON-RPC query paths into Wallet and TronJsonRpcImpl.
- Reject malicious-SR, malicious-witness, malicious-committee, malicious-peer, malicious-node, p2p/sync, network-DoS, leaked-key, monitoring, CLI/toolkit, deployment, mocked-only paths, dependency-only bugs, and no-impact analogs.
- Medium, High and Critical only; no low, or resource-only analogs.
- Known team rejection, do not report anything like it: "The PBFT related code has not been put into use on the main TRON network, and it will not be enabled in the future. It will have little impact on the chain. So it is not supposed to be a valid issue, and the report is closed for now." Reject any analog in PBFT code (consensus/pbft, PbftManager, PbftMessageHandle, PBFT messages, interfaceOnPBFT/WalletOnPBFT, PBFT-gated solidity/confirmation paths) or in any other code that is not enabled or used on TRON mainnet.

## Validate
- Map the bug class to the strongest reachable java-tron path from a single signed transaction, contract call or API request.
- Confirm the code path is live on TRON mainnet; if it depends on PBFT or any disabled, unused or never-activated feature, output #NoVulnerability.
- Prove root cause with exact file/method support.
- Accept only concrete unauthorized account operation, theft or permanent freezing of funds, unbacked balance, node crash or halt, chain split, key disclosure, RCE, or an API the node can no longer serve.

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


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for java-tron security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Focus on High and Critical; reject informational, best-practice, and resource-only reports.
- Reject malicious-SR, malicious-witness, malicious-committee, malicious-peer, malicious-node, p2p/gossip/sync, network-level DoS or request flooding, monitoring endpoints, CLI and toolkit plugins, logging, deployment and infra, dependency-only, docs/style, generated-protobuf, and test/mock/config-only issues.
- Reject if the exploit needs super-representative, witness, committee, node-operator, database or privileged access, another user's key, victim social engineering, a non-default config, a disabled proposal flag, or anything outside what an unprivileged user can put in a signed transaction, a contract call, or an anonymous API request.
- Reject 51%-style majority attacks, sybil and centralization claims, economic-design critique, and self-harm where the attacker only damages their own account.
- Reject if the bug was fixed, acknowledged, or publicly disclosed already, per the eligibility rules.
- A valid report must be triggerable by an unprivileged transaction broadcaster, contract deployer or anonymous API client, unless the claim proves escalation from that starting point.
- The final impact must map to an in-scope category: Critical - remote code execution or node takeover, private-key or secret disclosure, unauthorized operation on an account whose key the attacker lacks, direct theft or permanent freezing of user funds, unbacked balance or supply inflation, the network unable to confirm new transactions, or an unintended chain split requiring a hard fork; High - DoS of the RPC/HTTP/JSON-RPC API or of the TRON protocol implementation from a single request or transaction, transaction-origination censorship, or corruption of shared on-chain state other users depend on.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, class, method, and line/code references.
2. Clear root cause and broken authorization, value-conservation, metering-integrity, determinism, or availability invariant.
3. Reachable exploit path: preconditions (attacker accounts, TRX balance, staked resources, deployed contract, issued asset) -> signed transaction, contract call or API request -> trigger -> bad result.
4. Existing signature and permission verification, actuator validate(), tapos/expiration/duplicate checks, bandwidth and energy metering, fork-version and VMConfig gating, store key construction, rate limiters, and exception handling reviewed and shown insufficient.
5. Concrete in-scope High/Critical impact with realistic likelihood.
6. Reproducible proof path: gradle JUnit PoC against the real classes, or exact steps in a single-node block-application flow.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an ordinary user trigger this by broadcasting a transaction, deploying or calling a contract, or sending one anonymous API request, without SR, committee, node-operator, or foreign-key access?
- Does the code actually behave as claimed on current mainnet configuration and active fork parameters?
- Is the impact caused by this code, not by a privileged actor, a peer, or a dependency?
- Is the fund loss, unauthorized operation, crash, split or API outage concrete rather than hypothetical, and does it harm someone other than the attacker?
- Would a TRON triager accept the proof-of-concept?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete in-scope impact, severity rationale, and TRON bounty category]

## Likelihood Explanation
[Attacker capability, accounts and balance required, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or gradle JUnit / single-node block-application test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
