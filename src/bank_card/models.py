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
    """待清算的已授权交易流水"""

    auth_id: str
    card_number: str
    merchant_name: str
    amount: float
    settled: bool = False


@dataclass
class MerchantStripeAccount:
    """商户在收单行（Stripe）内部的托管账户"""

    merchant_name: str
    pending_balance: float = 0.0
    available_balance: float = 0.0
