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
