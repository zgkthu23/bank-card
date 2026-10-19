import hashlib
import random
from bank_card.models import (
    AuthRequest,
    AuthResponse,
    CardBehavioralProfile,
    CardTierConfig,
    MerchantStripeAccount,
    Transaction,
    VirtualCard,
)

REGION_NAMES = {
    "CN": "中国大陆",
    "HK": "中国香港",
    "PR": "美属波多黎各(离岸金融区)",
    "US": "美国本土",
    "RU": "俄罗斯",
}


def calculate_luhn_check_digit(partial_15_digits: str) -> str:
    """根据前 15 位数字，使用 Luhn (模10) 算法计算第 16 位校验位"""
    total = 0
    for idx, ch in enumerate(reversed(partial_15_digits)):
        d = int(ch)
        if idx % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - (total % 10)) % 10)


def verify_luhn(card_number: str) -> bool:
    """校验 16 位银行卡号是否符合国际 ISO/IEC 7812 Luhn 模 10 算法"""
    return calculate_luhn_check_digit(card_number[:-1]) == card_number[-1]


class CardNetwork:
    """全球支付网络 / 卡组织（如 VisaNet）"""

    SCHEME_FEE_RATE = 0.0015

    def __init__(self, name: str = "VisaNet"):
        self.name = name
        self.bin_table: dict[str, "IssuerBank"] = {}
        self.behavioral_profiles: dict[str, CardBehavioralProfile] = {}
        self.revenue: float = 0.0

    def register_issuer(self, bin_prefix: str, issuer: "IssuerBank") -> None:
        self.bin_table[bin_prefix] = issuer
        issuer.bin_prefix = bin_prefix
        region_cn = REGION_NAMES.get(issuer.region, issuer.region)
        print(
            f"[{self.name}] 已将 BIN [{bin_prefix}] ({region_cn}/{issuer.region} · {issuer.scheme} · {issuer.default_funding}) "
            f"分配给发卡行: {issuer.name}"
        )

    def get_issuer_by_bin(self, card_number: str) -> "IssuerBank | None":
        return self.bin_table.get(card_number[:6])

    @staticmethod
    def _hash_pan(card_number: str) -> str:
        return hashlib.sha256(card_number.encode()).hexdigest()[:12]

    def _evaluate_and_inject_vaa(self, req: AuthRequest) -> CardBehavioralProfile:
        pan_hash = self._hash_pan(req.card_number)
        masked = f"{req.card_number[:6]}******{req.card_number[-4:]}"
        profile = self.behavioral_profiles.get(pan_hash)

        if not profile:
            profile = CardBehavioralProfile(pan_hash=pan_hash, masked_pan=masked)
            self.behavioral_profiles[pan_hash] = profile
            print(
                f"  🧠 [{self.name} VAA 风控大脑] 首次观测到卡 {masked} 产生交易！"
                f"已新建行为画像 Key={pan_hash} (Visa 不知道持卡人姓名与余额)"
            )

        score = 10
        reasons: list[str] = []
        recent_1h = [t for t in profile.recent_tx_minutes if 0 <= (req.tx_minute - t) <= 60]

        if len(recent_1h) >= 2:
            score += 20
            reasons.append(f"1小时内高频交易(已刷{len(recent_1h)}次)")

        if req.amount <= 1.0:
            score += 15
            reasons.append("疑似$1小额探卡测试")
        elif profile.small_probe_count >= 1 and req.amount >= 50.0:
            score += 35
            reasons.append("高危模式:先$1小额探卡后立即大额消费")

        if profile.last_country and profile.last_country != req.merchant_country:
            delta_min = req.tx_minute - profile.last_tx_minute
            if delta_min <= 30:
                score += 45
                reasons.append(f"跨国时空瞬移({profile.last_country}->{req.merchant_country} 仅隔{delta_min}分钟)")

        if profile.approved_tx_count >= 1 and req.amount > profile.avg_amount * 5:
            score += 30
            reasons.append(f"金额突变(${req.amount:.0f} 远超历史均值 ${profile.avg_amount:.2f})")

        if req.mcc == "5999" and "5999" not in profile.usual_mccs:
            score += 15
            reasons.append("首次涉足高风险礼品卡/加密资产类目(MCC:5999)")

        req.vaa_score = min(score, 99)
        req.vaa_reasons = reasons if reasons else ["行为符合历史画像特征(正常)"]

        risk_level = "🔴极高危" if req.vaa_score >= 80 else ("🟡中风险" if req.vaa_score >= 40 else "🟢低风险")
        print(
            f"  🧠 [{self.name} VAA 评分注入] VAA 风险分: {req.vaa_score:02d}/99 ({risk_level}) "
            f"| 依据: {', '.join(req.vaa_reasons)}"
        )
        return profile

    def _update_profile_after_tx(self, profile: CardBehavioralProfile, req: AuthRequest, approved: bool) -> None:
        profile.total_tx_count += 1
        profile.recent_tx_minutes.append(req.tx_minute)
        profile.last_country = req.merchant_country
        profile.last_tx_minute = req.tx_minute
        if req.amount <= 1.0:
            profile.small_probe_count += 1

        if approved:
            prev_total = profile.avg_amount * profile.approved_tx_count
            profile.approved_tx_count += 1
            profile.avg_amount = round((prev_total + req.amount) / profile.approved_tx_count, 2)
            profile.usual_countries.add(req.merchant_country)
            profile.usual_mccs.add(req.mcc)

    def print_card_profiles(self) -> None:
        print(f"\n🔍 =================== 【{self.name} 大数据集群：卡片历史行为画像库】 ===================")
        if not self.behavioral_profiles:
            print("  (当前画像库为空：没有任何卡片发生过交易，VisaNet 甚至不知道市面上开了多少张卡)")
            return

        for pan_hash, p in self.behavioral_profiles.items():
            print(
                f"  📌 画像 Key(PAN Hash): {pan_hash} | 掩码卡号: {p.masked_pan}\n"
                f"     ├─ Visa 已知轨迹: 累计刷卡 {p.total_tx_count} 次 (成功 {p.approved_tx_count} 次) | "
                f"历史均额: ${p.avg_amount:.2f} | 常驻国: {sorted(p.usual_countries)} | 最近交易国: {p.last_country}(T={p.last_tx_minute}m)\n"
                f"     └─ Visa 未知盲区: 持卡人真实身份=[未知] | 卡内剩余可用余额=[未知] | 母账户总额度=[未知]"
            )

    def route_authorization(self, req: AuthRequest) -> AuthResponse:
        bin_prefix = req.card_number[:6]
        issuer = self.bin_table.get(bin_prefix)

        print(
            f"  -> [{self.name}] 收到 [{req.acquirer_name}] 授权请求 "
            f"(商户国:{req.merchant_country}, MCC:{req.mcc}, 时间:T+{req.tx_minute}m)"
        )
        if not issuer:
            return AuthResponse(approved=False, code="14", message="未知的卡号 BIN")

        profile = self._evaluate_and_inject_vaa(req)
        print(f"  -> [{self.name}] 携带 VAA 评分({req.vaa_score:02d}) 路由至发卡行 [{issuer.name} ({issuer.region})]...")
        resp = issuer.authorize(req)
        self._update_profile_after_tx(profile, req, resp.approved)
        return resp


class IssuerBank:
    """持牌发卡银行"""

    INTERCHANGE_FEE_RATE = 0.0175
    BIN_SUSPEND_THRESHOLD = 3
    VAA_BLOCK_THRESHOLD = 80

    def __init__(self, name: str, region: str, scheme: str = "Visa", default_funding: str = "credit"):
        self.name = name
        self.region = region                # "CN" | "HK" | "PR"(美属波多黎各) | "US"(美国本土)
        self.scheme = scheme
        self.default_funding = default_funding  # "credit"(信用卡) | "prepaid"(预付虚拟卡)
        self.bin_prefix: str = ""
        self.cards: dict[str, VirtualCard] = {}
        self.baas_platform = None
        self.revenue: float = 0.0
        self.bin_decline_count: int = 0
        self.bin_suspended: bool = False

    def issue_direct_card(self, owner: str, initial_balance: float, card_name: str = "") -> VirtualCard:
        region_cn = REGION_NAMES.get(self.region, self.region)
        display_name = card_name or f"{self.name}·{self.scheme}卡({region_cn})"
        tier = CardTierConfig(
            tier_code=f"DIRECT_{self.region}",
            display_name=display_name,
            bin_prefix=self.bin_prefix,
            region=self.region,
            funding_type=self.default_funding,
            open_fee=0.0,
            tx_fee_rate=0.0,
            support_3ds=True,
        )
        card = self.create_card_record(
            owner=owner, user_label=owner, tier=tier, initial_balance=initial_balance
        )
        print(
            f"[{self.name} 直发卡成功] 持卡人: {owner} | 卡名: {display_name} | "
            f"卡号: {card.card_number} (BIN:{self.bin_prefix}·{self.region}·{self.default_funding} | Luhn:{verify_luhn(card.card_number)})"
        )
        return card

    def create_card_record(
        self,
        owner: str,
        user_label: str,
        tier: CardTierConfig,
        initial_balance: float,
        custom_card_number: str | None = None,
    ) -> VirtualCard:
        if custom_card_number:
            card_number = custom_card_number.replace(" ", "")
        else:
            random_9_digits = "".join(str(random.randint(0, 9)) for _ in range(9))
            partial_15 = f"{self.bin_prefix}{random_9_digits}"
            check_digit = calculate_luhn_check_digit(partial_15)
            card_number = f"{partial_15}{check_digit}"

        cvv = f"{random.randint(100, 999)}"
        card = VirtualCard(
            card_number=card_number,
            cvv=cvv,
            expiry="10/29",
            owner=owner,
            user_label=user_label,
            tier=tier,
            available_balance=initial_balance,
        )
        self.cards[card_number] = card
        return card

    def authorize(self, req: AuthRequest) -> AuthResponse:
        if self.bin_suspended:
            return AuthResponse(approved=False, code="62", message=f"卡段 [{self.bin_prefix}] 已被发卡行整体封停")

        card = self.cards.get(req.card_number)
        if not card:
            return AuthResponse(approved=False, code="14", message="卡号不存在")

        if card.cvv != req.cvv or card.expiry != req.expiry:
            return AuthResponse(approved=False, code="N7", message="CVV 或有效期校验失败")

        if req.vaa_score >= self.VAA_BLOCK_THRESHOLD:
            print(
                f"  🛡️ [{self.name} 反欺诈熔断] 检测到报文中 Visa VAA 评分高达 {req.vaa_score}/99 "
                f"(阈值 {self.VAA_BLOCK_THRESHOLD})！\n"
                f"     命中高危特征: {req.vaa_reasons}\n"
                f"     尽管 CVV({req.cvv})正确且卡内余额(${card.available_balance:.2f})充足，"
                f"发卡行仍以 [59 疑似欺诈] 紧急拦截此交易！"
            )
            return AuthResponse(
                approved=False,
                code="59",
                message=f"发卡行反欺诈拦截 (VAA={req.vaa_score}: {', '.join(req.vaa_reasons)})",
            )

        if req.require_3ds:
            if not card.tier.support_3ds:
                return AuthResponse(approved=False, code="3D", message="卡片不支持 3DS 验证")
            print(f"  -> [{self.name} 3DS 验证通过] 持卡人 [{card.user_label}] 已完成 3DS 动态码验证")

        if self.baas_platform:
            return self.baas_platform.handle_auth_webhook(card, req)

        if card.available_balance < req.amount:
            return AuthResponse(approved=False, code="51", message="银行卡可用额度不足")

        card.available_balance = round(card.available_balance - req.amount, 2)
        card.frozen_balance = round(card.frozen_balance + req.amount, 2)
        auth_id = f"AUTH-{random.randint(1000, 9999)}"
        print(
            f"  <- [{self.name} 授权通过({auth_id})] 传统银行直联扣款冻结 ${req.amount:.2f} "
            f"(0附加手续费 | 剩余额度: ${card.available_balance:.2f})"
        )
        return AuthResponse(approved=True, code="00", message="Approved", auth_id=auth_id)


class RoogooPlatform:
    """Roogoo 平台（个人实名 KYC 消费 U 卡）"""

    CARD_TIERS: dict[str, CardTierConfig] = {
        "ENJOY": CardTierConfig(
            tier_code="ENJOY",
            display_name="Roogoo·乐享卡(真实卡段454924·波多黎各Prepaid预付卡)",
            bin_prefix="454924",
            region="PR",            # 真实 BIN 归属地为 PR (Puerto Rico 美属波多黎各)
            funding_type="prepaid", # 真实 BIN 卡片性质为 Prepaid (预付卡)
            open_fee=0.0,
            tx_fee_rate=0.01,
            decline_fee=0.50,
            support_3ds=False,
            require_kyc=True,
        ),
        "PREMIER": CardTierConfig(
            tier_code="PREMIER",
            display_name="Roogoo·尊享卡(香港538720·Prepaid预付卡)",
            bin_prefix="538720",
            region="HK",
            funding_type="prepaid",
            open_fee=4.99,
            tx_fee_rate=0.016,
            decline_fee=1.00,
            support_3ds=True,
            require_kyc=True,
        ),
    }

    MAX_DECLINE_LIMIT = 2

    def __init__(self, name: str = "Roogoo 平台"):
        self.name = name
        self.sponsor_banks: dict[str, IssuerBank] = {}
        self.user_usd_wallets: dict[str, float] = {}
        self.kyc_users: set[str] = set()
        self.revenue: float = 0.0

    def connect_sponsor_bank(self, bank: IssuerBank) -> None:
        self.sponsor_banks[bank.bin_prefix] = bank
        bank.baas_platform = self

    def complete_kyc(self, user_name: str) -> None:
        self.kyc_users.add(user_name)

    def deposit_usdt(self, user_name: str, usdt_amount: float) -> None:
        self.user_usd_wallets[user_name] = round(self.user_usd_wallets.get(user_name, 0.0) + usdt_amount, 2)

    def open_card(
        self,
        user_name: str,
        tier_code: str,
        load_amount: float,
        custom_card_number: str | None = None,
    ) -> VirtualCard:
        tier = self.CARD_TIERS[tier_code]
        total_cost = round(tier.open_fee + load_amount, 2)
        self.user_usd_wallets[user_name] = round(self.user_usd_wallets[user_name] - total_cost, 2)
        self.revenue = round(self.revenue + tier.open_fee, 2)

        sponsor_bank = self.sponsor_banks[tier.bin_prefix]
        card = sponsor_bank.create_card_record(
            owner=user_name,
            user_label=user_name,
            tier=tier,
            initial_balance=load_amount,
            custom_card_number=custom_card_number,
        )
        print(
            f"[{self.name} 开卡成功] 持卡人: {card.owner} | 卡种: {tier.display_name}\n"
            f"  └─ 卡号: {card.card_number} (6位BIN:{card.card_number[:6]} | 地区:{tier.region} | "
            f"属性:{tier.funding_type} | Luhn:{verify_luhn(card.card_number)}) | 余额: ${card.available_balance:.2f}"
        )
        return card

    def handle_auth_webhook(self, card: VirtualCard, req: AuthRequest) -> AuthResponse:
        if card.is_frozen:
            return AuthResponse(approved=False, code="62", message="卡片已被风控冻结")

        roogoo_tx_fee = round(req.amount * card.tier.tx_fee_rate, 2)
        total_needed = round(req.amount + roogoo_tx_fee, 2)

        if card.available_balance < total_needed:
            card.decline_count += 1
            penalty = min(card.available_balance, card.tier.decline_fee)
            card.available_balance = round(card.available_balance - penalty, 2)
            self.revenue = round(self.revenue + penalty, 2)
            if card.decline_count >= self.MAX_DECLINE_LIMIT:
                card.is_frozen = True
            return AuthResponse(approved=False, code="51", message=f"余额不足，已扣拒付罚金 ${penalty:.2f}")

        card.decline_count = 0
        card.available_balance = round(card.available_balance - total_needed, 2)
        card.frozen_balance = round(card.frozen_balance + req.amount, 2)
        self.revenue = round(self.revenue + roogoo_tx_fee, 2)

        auth_id = f"AUTH-{random.randint(1000, 9999)}"
        print(
            f"  <- [{self.name} 授权通过({auth_id})] 冻结消费 ${req.amount:.2f} + "
            f"平台加收手续费({card.tier.tx_fee_rate * 100:.1f}%) ${roogoo_tx_fee:.2f} | 剩: ${card.available_balance:.2f}"
        )
        return AuthResponse(approved=True, code="00", message="Approved", auth_id=auth_id)


class SuPayPlatform:
    """SuPay 平台（免实名 No-KYC 企业商务信用卡 Credit 卡段）"""

    CORPORATE_ENTITY = "SuPay Global Ads LLC"
    DEPOSIT_FEE_RATE = 0.02

    CARD_TIERS: dict[str, CardTierConfig] = {
        "SUPAY_MC": CardTierConfig(
            tier_code="SUPAY_MC",
            display_name="SuPay·美国本土企业商务信用卡(556789·US·Credit)",
            bin_prefix="556789",
            region="US",            # 纯正美国本土银行 Cross River Bank
            funding_type="credit",  # 企业商务信用卡 (Commercial Credit)，非 Prepaid！
            open_fee=8.00,
            tx_fee_rate=0.0,
            decline_fee=0.0,
            support_3ds=False,
            require_kyc=False,
        ),
    }

    def __init__(self, name: str = "SuPay 平台"):
        self.name = name
        self.sponsor_banks: dict[str, IssuerBank] = {}
        self.user_usd_wallets: dict[str, float] = {}
        self.revenue: float = 0.0

    def connect_sponsor_bank(self, bank: IssuerBank) -> None:
        self.sponsor_banks[bank.bin_prefix] = bank
        bank.baas_platform = self

    def deposit_usdt(self, email: str, usdt_amount: float) -> None:
        deposit_fee = round(usdt_amount * self.DEPOSIT_FEE_RATE, 2)
        net_usd = round(usdt_amount - deposit_fee, 2)
        self.revenue = round(self.revenue + deposit_fee, 2)
        self.user_usd_wallets[email] = round(self.user_usd_wallets.get(email, 0.0) + net_usd, 2)

    def open_card(self, email: str, tier_code: str, load_amount: float) -> VirtualCard:
        tier = self.CARD_TIERS[tier_code]
        total_cost = round(tier.open_fee + load_amount, 2)
        self.user_usd_wallets[email] = round(self.user_usd_wallets[email] - total_cost, 2)
        self.revenue = round(self.revenue + tier.open_fee, 2)

        sponsor_bank = self.sponsor_banks[tier.bin_prefix]
        card = sponsor_bank.create_card_record(
            owner=self.CORPORATE_ENTITY,
            user_label=email,
            tier=tier,
            initial_balance=load_amount,
        )
        print(
            f"[{self.name} 免KYC开卡成功] 邮箱: {email} | 卡种: {tier.display_name} | "
            f"卡号: {card.card_number} (BIN:{tier.bin_prefix}·{tier.region}·{tier.funding_type}) | 余额: ${card.available_balance:.2f}"
        )
        return card

    def handle_auth_webhook(self, card: VirtualCard, req: AuthRequest) -> AuthResponse:
        if card.available_balance < req.amount:
            return AuthResponse(approved=False, code="51", message="余额不足")

        card.available_balance = round(card.available_balance - req.amount, 2)
        card.frozen_balance = round(card.frozen_balance + req.amount, 2)
        auth_id = f"AUTH-{random.randint(1000, 9999)}"
        print(
            f"  <- [{self.name} 授权通过({auth_id})] 企业子卡扣款冻结 ${req.amount:.2f} "
            f"(消费手续费 $0.00 | 剩: ${card.available_balance:.2f})"
        )
        return AuthResponse(approved=True, code="00", message="Approved", auth_id=auth_id)


class Acquirer:
    """
    收单机构（如 Stripe）
    内置【Stripe Radar 风控规则引擎】，可在把报文发给 VisaNet 之前，根据：
    1. 发卡国家/地区 (blocked_regions)
    2. 卡片资金属性 (block_prepaid: 是否封杀 Prepaid 预付虚拟卡)
    3. 高危网红虚拟卡段黑名单 (blacklisted_bins: 如被大量滥用的 454924 等)
    直接拦截交易，并向前端统一展示模糊报错："Your card has been declined."
    """

    def __init__(self, name: str, network: CardNetwork):
        self.name = name
        self.network = network
        self.merchant_accounts: dict[str, MerchantStripeAccount] = {}
        self.pending_transactions: list[Transaction] = []

    def register_merchant(self, merchant: "Merchant") -> None:
        self.merchant_accounts[merchant.name] = MerchantStripeAccount(merchant_name=merchant.name)
        rules = []
        if merchant.blocked_regions:
            rules.append(f"禁入地区:{merchant.blocked_regions}")
        if merchant.block_prepaid:
            rules.append("拦截Prepaid预付卡")
        if merchant.blacklisted_bins:
            rules.append(f"封禁高危虚拟卡BIN:{merchant.blacklisted_bins}")
        rule_str = " | ".join(rules) if rules else "全球无限制"
        print(f"[{self.name}] 已为商户 [{merchant.name}] 开通收单服务 | Stripe Radar 规则: {rule_str}")

    def process_payment(
        self,
        merchant: "Merchant",
        card_number: str,
        cvv: str,
        expiry: str,
        amount: float,
        tx_minute: int = 0,
    ) -> AuthResponse:
        bin_prefix = card_number[:6]
        issuer = self.network.get_issuer_by_bin(card_number)
        if not issuer:
            return AuthResponse(approved=False, code="14", message="Your card has been declined (invalid_number)")

        card_region = issuer.region
        region_cn = REGION_NAMES.get(card_region, card_region)
        funding_type = issuer.default_funding

        # 规则 1：地区合规拦截 (如 CN/HK，或者严格区分美属波多黎各 PR 与美国本土 US)
        if merchant.blocked_regions and card_region in merchant.blocked_regions:
            print(
                f"  🚫 [{self.name} Radar · 地区政策拦截] BIN={bin_prefix} 发卡行为 [{issuer.name}]，发卡地为 【{region_cn} ({card_region})】！\n"
                f"     商户 [{merchant.name}] 拒收来自 {merchant.blocked_regions} 的银行卡 (Stripe 内部码: country_not_supported)"
            )
            return AuthResponse(
                approved=False,
                code="62",
                message=f"Your card has been declined. (原因: 商户政策禁入 {region_cn}[{card_region}])",
            )

        # 规则 2：Anthropic 等严苛商户专门针对【高危虚拟卡 BIN 黑名单】或【Prepaid 预付卡属性】的拦截
        if merchant.blacklisted_bins and bin_prefix in merchant.blacklisted_bins:
            print(
                f"  🚫 [{self.name} Radar · 虚拟卡段黑名单拦截] 命中高危虚拟卡 BIN [{bin_prefix}] ({issuer.name})！\n"
                f"     内部原因：该波多黎各离岸卡段 ({bin_prefix}) 被大量用户集中用于注册 AI 账号，已被 [{merchant.name}] 列入 BIN 黑名单！\n"
                f"     注意：请求在 Stripe 层直接被掐断，根本未发往 VisaNet 和 Roogoo，前端仅显示 'Your card has been declined.'"
            )
            return AuthResponse(
                approved=False,
                code="62",
                message=f"Your card has been declined. (Stripe Radar 内部拦截: 虚拟卡 BIN [{bin_prefix}] 已被拉黑)",
            )

        if merchant.block_prepaid and funding_type == "prepaid":
            print(
                f"  🚫 [{self.name} Radar · 卡片属性拦截] 识别到 BIN={bin_prefix} 的资金属性为 【Prepaid (预付充值卡)】！\n"
                f"     商户 [{merchant.name}] 启用了 'Block Prepaid Cards' 规则，仅允许 Credit(信用卡) 或 Debit(借记卡) 绑定订阅！"
            )
            return AuthResponse(
                approved=False,
                code="62",
                message="Your card has been declined. (Stripe Radar 内部拦截: 拒绝 Prepaid 预付属性虚拟卡)",
            )

        req = AuthRequest(
            card_number=card_number,
            cvv=cvv,
            expiry=expiry,
            amount=amount,
            merchant_name=merchant.name,
            acquirer_name=self.name,
            merchant_country=merchant.country,
            mcc=merchant.mcc,
            tx_minute=tx_minute,
            require_3ds=merchant.require_3ds,
        )
        resp = self.network.route_authorization(req)

        if resp.approved:
            account = self.merchant_accounts[merchant.name]
            account.pending_balance = round(account.pending_balance + amount, 2)
            self.pending_transactions.append(
                Transaction(
                    auth_id=resp.auth_id,
                    card_number=card_number,
                    merchant_name=merchant.name,
                    amount=amount,
                )
            )
        else:
            print(f"  <- [{self.name}] 收到拒绝回执：[{resp.code}] {resp.message}")

        return resp


class Merchant:
    """商户（可配置地区黑名单、是否拦截 Prepaid 预付卡、以及特定虚拟卡 BIN 黑名单）"""

    def __init__(
        self,
        name: str,
        acquirer: Acquirer,
        country: str = "US",
        mcc: str = "5734",
        blocked_regions: list[str] | None = None,
        block_prepaid: bool = False,
        blacklisted_bins: list[str] | None = None,
        require_3ds: bool = False,
    ):
        self.name = name
        self.acquirer = acquirer
        self.country = country
        self.mcc = mcc
        self.blocked_regions = blocked_regions
        self.block_prepaid = block_prepaid
        self.blacklisted_bins = blacklisted_bins
        self.require_3ds = require_3ds
        self.corporate_bank_balance: float = 0.0
        acquirer.register_merchant(self)

    def checkout(self, card_number: str, cvv: str, expiry: str, amount: float, tx_minute: int = 0) -> bool:
        print(
            f"\n[{self.name} 收银台 ({self.country}·MCC:{self.mcc}) | 时间:T+{tx_minute}m] "
            f"发起扣款: 卡号 {card_number[:6]}******{card_number[-4:]} | 金额 ${amount:.2f}"
        )
        resp = self.acquirer.process_payment(self, card_number, cvv, expiry, amount, tx_minute=tx_minute)
        if resp.approved:
            print(f"[{self.name} 收银台] ✅ 支付成功！")
            return True
        else:
            print(f"[{self.name} 收银台] ❌ 支付被拒：{resp.message}")
            return False


class User:
    """持卡人"""

    def __init__(self, identifier: str):
        self.identifier = identifier
        self.cards: dict[str, VirtualCard] = {}

    def pay(self, merchant: Merchant, card: VirtualCard, amount: float, tx_minute: int = 0) -> bool:
        print(
            f"\n=== [{self.identifier}] 使用 【{card.tier.display_name}】 在 [{merchant.name}] 消费 ${amount:.2f} ==="
        )
        return merchant.checkout(card.card_number, card.cvv, card.expiry, amount, tx_minute=tx_minute)
