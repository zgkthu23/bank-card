from dataclasses import dataclass, field


@dataclass
class CardTierConfig:
    """虚拟卡平台内部不同卡种/卡段的配置参数"""

    tier_code: str
    display_name: str
    bin_prefix: str
    region: str
    open_fee: float
    tx_fee_rate: float
    decline_fee: float
    support_3ds: bool
    require_kyc: bool = True
    require_roog_stake: int = 0


@dataclass
class VirtualCard:
    """虚拟银行卡实体（仅保存在发卡行与虚拟卡平台数据库中，卡组织并不持有此表）"""

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
    """
    【VisaNet 大数据风控集群：卡片历史行为画像库】
    注意：Visa 只以卡号哈希为 Key 记录历史消费轨迹，它【完全不知道】持卡人真名、卡内余额或总额度！
    只有当某张卡第一次刷卡路过 VisaNet 时，才会在这里留下第一条记录。
    """

    pan_hash: str                                  # 卡号脱敏哈希标识
    masked_pan: str                                # 掩码卡号，如 "428800******1234"
    total_tx_count: int = 0                        # 历史累计刷卡次数
    approved_tx_count: int = 0                     # 历史成功交易次数
    avg_amount: float = 0.0                        # 历史平均消费金额 ($)
    usual_countries: set[str] = field(default_factory=set)  # 历史常驻消费国家集合
    usual_mccs: set[str] = field(default_factory=set)       # 历史常去商户类别 (MCC)
    last_country: str = ""                         # 上一次刷卡所在国家
    last_tx_minute: int = -999                     # 上一次刷卡的时间戳（模拟分钟数）
    recent_tx_minutes: list[int] = field(default_factory=list)  # 最近刷卡时间窗口（算高频频次）
    small_probe_count: int = 0                     # 疑似黑客 $1 小额探卡计数


@dataclass
class AuthRequest:
    """T+0 交易授权请求报文（简化版 ISO 8583 报文）"""

    card_number: str
    cvv: str
    expiry: str
    amount: float
    merchant_name: str
    acquirer_name: str
    merchant_country: str = "US"   # 商户所在国家 (如 "US", "HK", "RU")
    mcc: str = "5734"              # 商户类别码 (5734=软件订阅, 5999=高风险礼品卡/加密货币)
    tx_minute: int = 0             # 交易发生时间（模拟相对分钟数）
    require_3ds: bool = False
    # --- 以下字段由 VisaNet 在路由途中实时计算并塞入报文（ISO 8583 风险评分域） ---
    vaa_score: int = 0             # Visa Advanced Authorization 反欺诈评分 (01~99，越高越危险)
    vaa_reasons: list[str] = field(default_factory=list)  # VAA 风险命中原因标签


@dataclass
class AuthResponse:
    """T+0 交易授权响应报文"""

    approved: bool
    code: str         # "00" 成功，"51" 余额不足，"14" 卡号无效，"N7" CVV错误，"62" 冻结，"59" VAA反欺诈拦截
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
