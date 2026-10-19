from dataclasses import dataclass, field


@dataclass
class CardTierConfig:
    """卡片类型与费率配置（传统银行卡或虚拟卡平台卡种）"""

    tier_code: str
    display_name: str
    bin_prefix: str
    region: str                   # 发卡行国家/地区代码: "CN"(中国大陆) | "HK"(中国香港) | "US"(美国)
    open_fee: float = 0.0
    tx_fee_rate: float = 0.0
    decline_fee: float = 0.0
    support_3ds: bool = True
    require_kyc: bool = True
    require_roog_stake: int = 0


@dataclass
class VirtualCard:
    """银行卡实体（支持传统银行直发 Visa 卡 & 虚拟卡平台生成的子卡）"""

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
    # --- VisaNet 在路由途中实时计算并塞入报文的 VAA 风险分 ---
    vaa_score: int = 0
    vaa_reasons: list[str] = field(default_factory=list)


@dataclass
class AuthResponse:
    """T+0 交易授权响应报文"""

    approved: bool
    code: str         # "00" 成功，"51" 余额不足，"14" 卡号无效，"N7" CVV错误，"62" 政策地区限制/冻结，"59" VAA反欺诈拦截
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
