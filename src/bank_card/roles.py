import hashlib
import random
from bank_card.models import (
    AppleGiftCard,
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
        self.region = region
        self.scheme = scheme
        self.default_funding = default_funding
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
    """Roogoo 平台（个人实名 KYC 消费 U 卡，带手机 App 交易账单日志）"""

    CARD_TIERS: dict[str, CardTierConfig] = {
        "ENJOY": CardTierConfig(
            tier_code="ENJOY",
            display_name="Roogoo·乐享卡(真实卡段454924·波多黎各Prepaid预付卡)",
            bin_prefix="454924",
            region="PR",
            funding_type="prepaid",
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
        # 模拟用户在 Roogoo 手机 App 里能看到的卡片交易流水（只有请求真正到达 Roogoo 才会留下记录！）
        self.app_transaction_logs: dict[str, list[str]] = {}

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
        self.app_transaction_logs.setdefault(card.card_number, []).append(
            f"[开卡充值] 初始转入 +${load_amount:.2f} | 当前可用余额: ${card.available_balance:.2f}"
        )
        print(
            f"[{self.name} 开卡成功] 持卡人: {card.owner} | 卡种: {tier.display_name}\n"
            f"  └─ 卡号: {card.card_number} (6位BIN:{card.card_number[:6]} | 地区:{tier.region} | "
            f"属性:{tier.funding_type} | Luhn:{verify_luhn(card.card_number)}) | 余额: ${card.available_balance:.2f}"
        )
        return card

    def handle_auth_webhook(self, card: VirtualCard, req: AuthRequest) -> AuthResponse:
        logs = self.app_transaction_logs.setdefault(card.card_number, [])
        if card.is_frozen:
            logs.append(f"[交易被拒·卡片已冻结] 商户: {req.merchant_name} | 金额: ${req.amount:.2f}")
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
            logs.append(
                f"[❌ 交易失败·余额不足] 商户: {req.merchant_name} (${req.amount:.2f}) | "
                f"扣除拒付罚金: -${penalty:.2f} | 余额: ${card.available_balance:.2f}"
            )
            return AuthResponse(approved=False, code="51", message=f"余额不足，已扣拒付罚金 ${penalty:.2f}")

        card.decline_count = 0
        card.available_balance = round(card.available_balance - total_needed, 2)
        card.frozen_balance = round(card.frozen_balance + req.amount, 2)
        self.revenue = round(self.revenue + roogoo_tx_fee, 2)

        auth_id = f"AUTH-{random.randint(1000, 9999)}"
        logs.append(
            f"[✅ 消费授权成功({auth_id})] 商户: {req.merchant_name} | 消费: -${req.amount:.2f} | "
            f"手续费: -${roogoo_tx_fee:.2f} | 余额: ${card.available_balance:.2f}"
        )
        print(
            f"  <- [{self.name} 授权通过({auth_id})] 冻结消费 ${req.amount:.2f} + "
            f"平台加收手续费({card.tier.tx_fee_rate * 100:.1f}%) ${roogoo_tx_fee:.2f} | 剩: ${card.available_balance:.2f}"
        )
        return AuthResponse(approved=True, code="00", message="Approved", auth_id=auth_id)

    def print_app_bill(self, card: VirtualCard) -> None:
        """模拟用户打开 Roogoo 手机 App 查看该卡的交易记录与拒付费扣除情况"""
        print(f"\n📱 --- 【用户打开 Roogoo 手机 App 查看卡 {card.card_number} 账单明细】 ---")
        for idx, entry in enumerate(self.app_transaction_logs.get(card.card_number, []), 1):
            print(f"  {idx}. {entry}")
        print(
            "  💡 [App 账单结论] 没有任何来自 Anthropic Claude Code 的失败交易记录，也未扣 $0.50 拒付费！\n"
            "     原因：请求在第一关 (Stripe Radar) 就被短路拦截了，根本没有穿过 VisaNet 到达 Roogoo 服务器！"
        )


class StripeRadar:
    """
    【Stripe Radar 独立风控引擎】
    部署在 Stripe 收单行最前端（进入 VisaNet 之前）。
    它自带全球 BIN 本地缓存表，可在几毫秒内直接识别：
    - 发卡国家/地区 (CN / HK / PR 波多黎各 / US 美国本土)
    - 卡片资金类型 (credit 信用卡 / debit 借记卡 / prepaid 预付充值卡)
    - 高风险虚拟卡 BIN 黑名单 (如 454924 等高频用于 AI 订阅的 U 卡号段)
    一旦命中拦截规则，直接在 Stripe 层短路返回 "Your card has been declined."，不发往 VisaNet！
    """

    def inspect(self, merchant: "Merchant", card_number: str, issuer: IssuerBank | None) -> tuple[bool, str]:
        if not issuer:
            return False, "Your card has been declined. (invalid_number)"

        bin_prefix = card_number[:6]
        card_region = issuer.region
        region_cn = REGION_NAMES.get(card_region, card_region)
        funding_type = issuer.default_funding

        # 1. 检查发卡国家/地区政策禁入 (如 CN、HK、RU)
        if merchant.blocked_regions and card_region in merchant.blocked_regions:
            print(
                f"  🚫 [Stripe Radar · 地区政策短路拦截] 本地查表 BIN={bin_prefix} 属于 [{issuer.name}]，发卡地为 【{region_cn} ({card_region})】！\n"
                f"     命中规则: 商户 [{merchant.name}] 禁止接收 {merchant.blocked_regions} 地区银行卡 (内部原因码: country_not_supported)"
            )
            return False, f"Your card has been declined. (政策限制: 不支持 {region_cn}[{card_region}] 发行的银行卡)"

        # 2. 检查是否命中商户拉黑的高频虚拟卡 BIN 黑名单 (如 Roogoo 的 454924)
        if merchant.blacklisted_bins and bin_prefix in merchant.blacklisted_bins:
            print(
                f"  🚫 [Stripe Radar · 虚拟卡 BIN 黑名单短路拦截] 本地查表识别出 BIN={bin_prefix} ({issuer.name} · {region_cn})\n"
                f"     命中规则 1: BIN [{bin_prefix}] 已被标记为高频 Web3 虚拟卡号段 (high_risk_virtual_card_bin)\n"
                f"     命中规则 2: 卡片资金属性为 '{funding_type}' (Prepaid 预付卡)，且发卡地为离岸金融区 '{card_region}' 而非美国本土 'US'\n"
                f"     ⚡ 动作: Stripe Radar 直接在收单层掐断请求（不发往 VisaNet，也不通知 Roogoo），向网页前端返回笼统报错: 'Your card has been declined.'"
            )
            return False, "Your card has been declined."

        # 3. 检查是否开启了一刀切拦截 Prepaid 预付卡
        if merchant.block_prepaid and funding_type == "prepaid":
            print(
                f"  🚫 [Stripe Radar · 预付卡属性短路拦截] 识别到 BIN={bin_prefix} 的资金属性为 【Prepaid (预付卡)】！\n"
                f"     命中规则: Block if ::card_funding:: == 'prepaid'"
            )
            return False, "Your card has been declined."

        print(
            f"  ✅ [Stripe Radar · 预检放行] BIN={bin_prefix} ({region_cn}·{funding_type}) 通过收单层风控，准备发往 VisaNet..."
        )
        return True, "OK"


class Acquirer:
    """收单机构（如 Stripe，内置 StripeRadar 风控引擎）"""

    def __init__(self, name: str, network: CardNetwork):
        self.name = name
        self.network = network
        self.radar = StripeRadar()
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
        issuer = self.network.get_issuer_by_bin(card_number)

        # 第一关：先过 Stripe Radar 本地风控（如果被拦截，直接短路返回，绝不触碰 VisaNet 和发卡行！）
        passed, decline_msg = self.radar.inspect(merchant, card_number, issuer)
        if not passed:
            return AuthResponse(approved=False, code="RADAR_BLOCK", message=decline_msg)

        # 第二关：通过 Stripe Radar 后，才打包 ISO 8583 报文发往 VisaNet 和上游发卡行
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
            print(f"  <- [{self.name}] 收到发卡行拒绝回执：[{resp.code}] {resp.message}")

        return resp


class AlipayGiftCardStore:
    """
    【支付宝 · 出境惠 / PockytShop 礼品卡小程序】
    允许中国大陆用户直接用人民币（支付宝余额/国内储蓄卡）购买美区 Apple Gift Card 兑换码，无需任何海外银行卡！
    """

    USD_TO_CNY_RATE = 7.25

    def buy_us_apple_gift_card(self, user_name: str, face_value_usd: float) -> AppleGiftCard:
        cny_cost = round(face_value_usd * self.USD_TO_CNY_RATE, 2)
        code = f"APPL-US-{random.randint(1000, 9999)}-{random.randint(1000, 9999)}"
        gift_card = AppleGiftCard(code=code, face_value=face_value_usd, region="US")
        print(
            f"\n[支付宝 · 苹果礼品卡小程序] 用户 [{user_name}] 使用支付宝人民币付款 ¥{cny_cost:.2f} "
            f"(折合 ${face_value_usd:.2f})\n"
            f"  └─ 🎁 成功出码！美区 Apple Gift Card 兑换码: 【{gift_card.code}】 (面值: ${gift_card.face_value:.2f})"
        )
        return gift_card


class AppleAppStore:
    """
    【美区 Apple App Store & In-App Purchase (IAP) 内购结算中心】
    核心破局原理：
    用户用礼品卡充值美区 Apple ID 余额后，在 iOS 端 Claude App 内发起订阅。
    扣款完全在苹果内部账本完成（根本不经过 Stripe Radar，也不经过 VisaNet，没有任何银行卡 BIN 校验！），
    随后苹果服务器直接向 Anthropic 发送加密签名凭据 (JWS Receipt) 通知开通会员！
    """

    APPLE_TAX_RATE = 0.30  # 苹果内购抽成 30%

    def __init__(self):
        # 美区 Apple ID -> 账户美元余额 (Apple Account Balance)
        self.apple_id_balances: dict[str, float] = {}
        self.revenue: float = 0.0

    def register_us_apple_id(self, apple_id: str) -> None:
        self.apple_id_balances[apple_id] = 0.0
        print(f"[Apple App Store (美区)] 用户已登录美区 Apple ID: [{apple_id}] (当前账户余额: $0.00)")

    def redeem_gift_card(self, apple_id: str, gift_card: AppleGiftCard) -> None:
        """将支付宝买到的美区 Apple Gift Card 充值进美区 Apple ID 余额"""
        gift_card.redeemed = True
        self.apple_id_balances[apple_id] = round(
            self.apple_id_balances.get(apple_id, 0.0) + gift_card.face_value, 2
        )
        print(
            f"[Apple App Store (美区)] 兑换码 [{gift_card.code}] 充值成功！\n"
            f"  └─ 🍎 美区 Apple ID [{apple_id}] 当前可用余额: ${self.apple_id_balances[apple_id]:.2f}"
        )

    def subscribe_via_iap(self, apple_id: str, merchant: "Merchant", plan_name: str, price: float) -> bool:
        """在 iOS 端 App 内通过 Apple ID 余额发起内购订阅 (In-App Purchase)"""
        print(
            f"\n[iOS 端 {merchant.name} App · Apple IAP 内购收银台] "
            f"用户 [{apple_id}] 点击订阅【{plan_name}】(${price:.2f}/月)"
        )
        balance = self.apple_id_balances.get(apple_id, 0.0)
        if balance < price:
            print(f"  <- [Apple IAP] 订阅失败：Apple ID 余额不足 (${balance:.2f} < ${price:.2f})")
            return False

        # 1. 直接从 Apple ID 余额扣款（彻底绕开 Stripe 与银行卡网络！）
        self.apple_id_balances[apple_id] = round(balance - price, 2)
        apple_commission = round(price * self.APPLE_TAX_RATE, 2)
        merchant_net = round(price - apple_commission, 2)
        self.revenue = round(self.revenue + apple_commission, 2)
        merchant.corporate_bank_balance = round(merchant.corporate_bank_balance + merchant_net, 2)

        jws_receipt = f"APPLE-JWS-RECEIPT-{random.randint(10000, 99999)}"
        print(
            f"  -> [Apple IAP 内部账本扣款] 已从 [{apple_id}] 的 Apple ID 余额扣除 ${price:.2f} "
            f"(剩余: ${self.apple_id_balances[apple_id]:.2f})\n"
            f"     ⚡ 绕过验证：全程未经过 Stripe Radar，无需校验任何银行卡 BIN 或 Prepaid 属性！\n"
            f"  -> [Apple 佣金结算] 苹果截留 30% 苹果税 (${apple_commission:.2f})，向 [{merchant.name}] 结算 ${merchant_net:.2f}\n"
            f"  -> [App Store Server Notification] 向 [{merchant.name}] 服务器发送苹果官方签名凭据: {jws_receipt}"
        )
        merchant.activate_by_apple_iap(apple_id, plan_name, jws_receipt)
        return True


class Merchant:
    """商户"""

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
        self.active_subscribers: list[str] = []
        acquirer.register_merchant(self)

    def checkout(self, card_number: str, cvv: str, expiry: str, amount: float, tx_minute: int = 0) -> bool:
        print(
            f"\n[{self.name} 网页端 Stripe 收银台 ({self.country}·MCC:{self.mcc}) | 时间:T+{tx_minute}m] "
            f"发起扣款: 卡号 {card_number[:6]}******{card_number[-4:]} | 金额 ${amount:.2f}"
        )
        resp = self.acquirer.process_payment(self, card_number, cvv, expiry, amount, tx_minute=tx_minute)
        if resp.approved:
            print(f"[{self.name} 网页端收银台] ✅ 支付成功！")
            return True
        else:
            print(f"[{self.name} 网页端收银台] ❌ 网页弹窗提示：{resp.message}")
            return False

    def activate_by_apple_iap(self, apple_id: str, plan_name: str, jws_receipt: str) -> None:
        """商户后端收到苹果 App Store 的内购成功通知，直接为用户激活订阅"""
        self.active_subscribers.append(apple_id)
        print(
            f"  <- [{self.name} 后端服务器] 校验苹果签名凭据 [{jws_receipt}] 通过！\n"
            f"     🎉 已为账号 [{apple_id}] 成功激活【{plan_name}】权限（可在终端直接登录使用 Claude Code）！"
        )


class User:
    """持卡人"""

    def __init__(self, identifier: str):
        self.identifier = identifier
        self.cards: dict[str, VirtualCard] = {}

    def pay(self, merchant: Merchant, card: VirtualCard, amount: float, tx_minute: int = 0) -> bool:
        print(
            f"\n=== [{self.identifier}] 使用 【{card.tier.display_name}】 在 [{merchant.name}] 刷卡消费 ${amount:.2f} ==="
        )
        return merchant.checkout(card.card_number, card.cvv, card.expiry, amount, tx_minute=tx_minute)
