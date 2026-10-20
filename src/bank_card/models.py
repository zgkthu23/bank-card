from dataclasses import dataclass, field


@dataclass
class CardTierConfig:
    """卡片类型与 BIN 元数据配置"""

    tier_code: str
    display_name: str
    bin_prefix: str
    region: str                   # 发卡国家/地区代码: "CN" | "HK" | "PR"(美属波多黎各) | "US"(美国本土)
    funding_type: str = "credit"  # 卡片资金属性: "credit"(信用卡) | "debit"(借记卡) | "prepaid"(预付卡/U卡)
    open_fee: float = 0.0
    tx_fee_rate: float = 0.0
    decline_fee: float = 0.0
    support_3ds: bool = True
    require_kyc: bool = True
    require_roog_stake: int = 0


@dataclass
class VirtualCard:
    """银行卡实体"""

    card_number: str
    cvv: str
    expiry: str
    owner: str
    user_label: str
    tier: CardTierConfig
    available_balance: float
    frozen_balance: float = 0.0
    decline_count: int = 0
    is_frozen: bool = False


@dataclass
class AppleGiftCard:
    """美区 Apple Gift Card 电子礼品卡（可通过支付宝小程序购买）"""

    code: str            # 16位兑换码，如 "ALIPAY-AGC-8899"
    face_value: float    # 面值 ($20.00)
    region: str = "US"   # 适用苹果商店地区 ("US" 美区)
    redeemed: bool = False


@dataclass
class SAFEDeclaration:
    """【国家外汇管理局 (SAFE) · 支付机构跨境外汇业务“逐笔还原申报单”】"""

    declaration_no: str       # 外管局申报单号
    payment_institution: str  # 申报持牌机构 ("支付宝(中国)网络技术有限公司")
    buyer_name: str           # 境内买家真实姓名
    buyer_id_card: str        # 境内买家身份证号 (用于校验真实性与个人年度外汇监测)
    sub_order_id: str         # 还原后的境外商户真实子订单号 (严禁只报汇总包，必须还原到单笔订单)
    overseas_merchant: str    # 境外收款主体 ("Yuansfer Inc. / PockytShop US")
    bop_code: str             # 国际收支交易编码 ("122030" - 跨境电商网络购物/数字商品)
    cny_amount: float         # 境内扣款人民币金额 (¥145.00)
    fx_usd_amount: float      # 申请购汇美元金额 ($20.00)
    approved_token: str = ""  # 外管局核验通过后签发的电子核准令牌 (银行凭此令牌才允许购汇)


@dataclass
class SwiftMT103Message:
    """【SWIFT MT103 跨境单笔客户汇款电文 (境内清算行 -> 纽约收款行)】"""

    uetr: str                     # SWIFT 全球唯一追踪号 (Unique End-to-End Transaction Reference)
    sender_bic: str               # 汇出行 BIC ("BKCHCNBJ - 中国银行境内跨境清算中心")
    receiver_bic: str             # 收款行 BIC ("CHASUS33 - 美国纽约摩根大通银行 JPMorgan Chase")
    beneficiary_name: str         # 收款人 ("Yuansfer Inc. DBA Pockyt")
    safe_approval_token: str      # 关联的外管局核准令牌
    cny_debited: float            # 购汇消耗人民币 (¥145.00)
    fx_rate: float                # 银行即期售汇牌价 (7.2500)
    gross_usd_purchased: float    # 购得美元本金 ($20.00)
    correspondent_fee_usd: float  # 跨境清算与代理行手续费 ($0.30)
    net_settled_usd: float        # 实际到账美元净额 ($19.70)


@dataclass
class InCommPOSARequest:
    """【美国一级总代 InComm · POSA (Point-of-Sale Activation) 实时点卡激活请求报文】"""

    request_id: str         # 请求流水号
    retailer_id: str        # 下游分销商标识 ("POCKYT-SHOP-US")
    sku_code: str           # 商品 SKU ("APPLE-US-GC-20")
    face_value_usd: float   # 激活面值 ($20.00)
    b2b_payment_usd: float  # Pockyt 向 InComm 支付的 B2B 结算款 ($19.60)


@dataclass
class CardBehavioralProfile:
    """【VisaNet 大数据风控集群：卡片历史行为画像库】"""

    pan_hash: str
    masked_pan: str
    total_tx_count: int = 0
    approved_tx_count: int = 0
    avg_amount: float = 0.0
    usual_countries: set[str] = field(default_factory=set)
    usual_mccs: set[str] = field(default_factory=set)
    last_country: str = ""
    last_tx_minute: int = -999
    recent_tx_minutes: list[int] = field(default_factory=list)
    small_probe_count: int = 0


@dataclass
class AuthRequest:
    """T+0 交易授权请求报文（简化版 ISO 8583 报文）"""

    card_number: str
    cvv: str
    expiry: str
    amount: float
    merchant_name: str
    acquirer_name: str
    merchant_country: str = "US"
    mcc: str = "5734"
    tx_minute: int = 0
    require_3ds: bool = False
    vaa_score: int = 0
    vaa_reasons: list[str] = field(default_factory=list)


@dataclass
class AuthResponse:
    """T+0 交易授权响应报文"""

    approved: bool
    code: str
    message: str
    auth_id: str = ""


@dataclass
class Transaction:
    """T+0 待清算的已授权交易流水（BASE I 实时授权层记录）"""

    auth_id: str
    card_number: str
    merchant_name: str
    amount: float
    is_auth_only_probe: bool = False  # 是否仅为 $1 小额探卡/预授权（商户最终不会发起 BASE II 实际请款）
    settled: bool = False


@dataclass
class BaseIIClearingRecord:
    """【VisaNet BASE II 系统 · TC05 批量请款清分记录 (Clearing Presentment Record)】"""

    clearing_id: str          # BASE II 清分流水号 ("TC05-...")
    auth_id: str              # 关联的 T+0 授权号 ("AUTH-...")
    card_number: str          # 16位卡号 (PAN)
    issuer_bin: str           # 发卡行 6位 BIN
    merchant_name: str        # 请款商户名称
    acquirer_name: str        # 送呈收单行名称
    authorized_usd: float     # T+0 原始授权冻结金额
    captured_usd: float       # T+1 商户实际提交请款金额 (Capture Amount)
    interchange_fee_usd: float  # 发卡行交换费分润 (1.75%，由收单行让渡给发卡行)
    visa_scheme_fee_usd: float  # Visa 卡组织网络清算费 (0.15%)


@dataclass
class VSSSettlementReport:
    """【Visa Settlement Service (VSS-110) 每日机构净头寸清算报表】"""

    report_id: str                      # 报表编号 ("VSS-110-...")
    member_bank_name: str               # 成员银行名称
    nostro_account_bic: str             # 该银行在美国纽约的美元往来账户 (Nostro Account)
    local_auth_total_usd: float         # 银行本地 T+0 实时授权流水总额 (仅供参考，不作为打款依据)
    base_ii_issuer_payable_usd: float   # BASE II 实际请款应付本金 (发卡侧支出)
    base_ii_acquirer_receivable_usd: float  # BASE II 实际收单应收本金 (收单侧收入，用于多边轧差抵消)
    interchange_fee_net_usd: float      # 交换费净收支 (+为发卡收入，-为收单支出)
    visa_scheme_fee_usd: float          # 应付 Visa 网络清算服务费
    net_settlement_position_usd: float  # 最终轧差净头寸 (负数代表银行需通过 Fedwire 向 Visa 付款，正数代表收款)
    unmatched_auth_ids: list[str] = field(default_factory=list)  # T+0 已授权但商户未请款的预授权单号


@dataclass
class MerchantStripeAccount:
    """商户在收单行（Stripe）内部的托管账户"""

    merchant_name: str
    pending_balance: float = 0.0
    available_balance: float = 0.0


@dataclass
class PassportDocument:
    """【用户提交给 Persona 弹窗的真实国际旅行证件与 3D 活体人脸特征】"""

    holder_name: str          # 持照人姓名 ("Alice" / "灰产张老板")
    nationality: str          # 护照签发国籍代码 ("CN" 中国护照 / "US" 美国护照 / "IR" 伊朗护照 / "KP" 朝鲜护照)
    passport_no: str          # 护照号码 ("E88990011")
    face_biometric_id: str    # 摄像头采集的 3D 活体面部生物特征源 ID ("FACE-3D-ALICE")
    is_live_human: bool = True  # 是否通过摄像头转头眨眼 3D 活体检测 (拦截模拟器假视频/静态网图)


@dataclass
class PersonaVerificationResult:
    """【美国第三方身份核验平台 Persona (withpersona.com) 审核回执】"""

    inquiry_id: str           # Persona 核验工单号 ("inq_...")
    account_email: str        # 关联的 Claude 账号邮箱
    client_ip_region: str     # 客户端当前访问的网络物理位置 ("US" / "CN")
    passport_nationality: str  # 证件国籍 ("CN")
    passport_hash: str        # 护照号单向哈希 (用于防一人多号去重)
    face_embedding_hash: str  # 3D 人脸特征向量哈希 (用于防换证件/防女巫攻击去重)
    passed: bool              # 是否通过核验
    decision_code: str        # 决策代码 ("APPROVED_HUMAN" | "SYBIL_DUPLICATE_BLOCK" | "OFAC_SANCTION_BLOCK" | "GEOFENCE_IP_BLOCK")
    reason: str               # 详细合规与风控判定原因


