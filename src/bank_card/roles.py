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


class PockytAlipayChain:
    """
    【支付宝“惠出境” + Pockyt (原 Yuansfer 源汇) 跨境收单网关 + PockytShop 商城 + InComm 一级总代】
    完整还原你在支付宝定位切到美国旧金山、进入 PockytShop 买 $20 美区苹果卡的真实链路：
    1. Pockyt (Yuansfer Inc.) 是总部位于美国纽约的跨境聚合支付网关（专门帮海外商户收中国人的支付宝/微信人民币）。
    2. Pockyt 自己开了一家数字礼品卡商城 PockytShop (shop.pockyt.io)，嵌入在支付宝“惠出境”小程序里。
    3. 当你付 ¥145.00 CNY 时：
       - [境内端] 支付宝扣除你的人民币，凭《跨境外汇支付牌照》把你的身份证号+订单号报送国家外汇管理局(SAFE)做真实跨境消费申报；
       - [购汇出境] 境内清算银行(如中行)在银行间外汇市场将 ¥145.00 CNY 购汇成 $20.00 USD (扣 $0.30 跨境清算费)，
         通过 SWIFT 电汇至 Pockyt (Yuansfer Inc.) 在美国摩根大通银行的美元清算账户；
       - [批发激活] Pockyt 截留 $0.30 分销毛利，调用苹果美国一级总代 (InComm / Blackhawk) 接口，
         向【美国苹果总公司 (Apple Inc. US)】支付 $19.40 USD 批发款，实时激活一张 $20.00 面值的美区兑换码发给你！
    """

    USD_TO_CNY_RATE = 7.25
    ALIPAY_CROSS_BORDER_FEE = 0.30  # 支付宝与境内清算行跨境购汇通道费 ($0.30)
    POCKYT_INCOMM_MARGIN = 0.30     # PockytShop 与上游总代 InComm 的分销毛利 ($0.30)

    def __init__(self, apple_us: "AppleAppStore"):
        self.apple_us = apple_us
        self.alipay_fx_revenue: float = 0.0
        self.pockyt_revenue: float = 0.0

    def buy_from_pockytshop(self, user_name: str, id_card_no: str, face_value_usd: float) -> AppleGiftCard:
        cny_paid = round(face_value_usd * self.USD_TO_CNY_RATE, 2)
        code = f"X9POCKYT{random.randint(10000000, 99999999)}"

        # 1. 支付宝向国家外汇管理局(SAFE)申报个人跨境电商购汇，境内银行将 CNY 换成 USD 汇给美国 Pockyt (Yuansfer)
        self.alipay_fx_revenue = round(self.alipay_fx_revenue + self.ALIPAY_CROSS_BORDER_FEE, 2)
        usd_settled_to_pockyt = round(face_value_usd - self.ALIPAY_CROSS_BORDER_FEE, 2)

        # 2. Pockyt (Yuansfer) 截留分销差价，通过一级总代 InComm 向美国苹果总公司支付美元批发款
        self.pockyt_revenue = round(self.pockyt_revenue + self.POCKYT_INCOMM_MARGIN, 2)
        wholesale_usd_to_apple = round(usd_settled_to_pockyt - self.POCKYT_INCOMM_MARGIN, 2)

        # 3. 美国苹果总公司收到真实美元现金，并在资产负债表上记下 $20.00 的“预收礼品卡负债(Deferred Revenue)”
        gift_card = self.apple_us.issue_wholesale_gift_card(
            code=code,
            face_value_usd=face_value_usd,
            cash_received_usd=wholesale_usd_to_apple,
        )

        print(
            f"\n[链路第 1~3 步：支付宝 ➔ Pockyt (Yuansfer) 跨境购汇 ➔ 美国苹果总公司]\n"
            f"  ① [支付宝切旧金山 · PockytShop 下单] [{user_name}] 选购 ${face_value_usd:.2f} 美区 Apple 礼品卡，"
            f"用支付宝人民币支付 ¥{cny_paid:.2f} CNY\n"
            f"  ② [SAFE 外管局申报 + 银行代客购汇] 支付宝凭跨境外汇牌照向国家外管局(SAFE)还原申报实名订单 (身份证:{id_card_no})，\n"
            f"     境内合作银行将 ¥{cny_paid:.2f} CNY 购汇为 ${face_value_usd:.2f} USD (扣除跨境清算费 ${self.ALIPAY_CROSS_BORDER_FEE:.2f})，\n"
            f"     通过 SWIFT 电汇 ${usd_settled_to_pockyt:.2f} USD 至【美国 Pockyt (Yuansfer Inc.) 纽约美元收单账户】\n"
            f"  ③ [Pockyt + 一级总代 InComm 批发激活] Pockyt 留存分销毛利 ${self.POCKYT_INCOMM_MARGIN:.2f}，"
            f"通过一级总代 InComm 向【美国苹果总公司 (Apple Inc.)】支付批发款 ${wholesale_usd_to_apple:.2f} USD，\n"
            f"     实时提取 16 位美区兑换码发至你的页面/邮箱: 【{gift_card.code}】"
        )
        return gift_card


class AppleAppStore:
    """
    【美国苹果总公司 (Apple Inc., Cupertino) —— 法定记录商户 (MoR) 与内部负债核销中心】
    核心金融机制：
    1. 预收款负债池 (deferred_liability_pool)：当礼品卡卖出时，美元真钱已经进了苹果美国银行账户，苹果记下 $20 预收负债。
    2. MoR (Merchant of Record，法定二道贩子)：用户内购订阅 Claude 时，合同双方是【用户 <-> 美国苹果公司】。
       苹果不做任何银行卡网络请求，只做【内部总账负债核销】：
       - 核销用户的 $20.00 Apple ID 负债余额；
       - 确认 30% 苹果税收入 ($6.00)；
       - 在《应付开发者账本 (developer_payable_ledger)》给 Anthropic 记上 +$14.00 USD。
    3. 苹果财月月度结算 (run_monthly_developer_payout)：月底通过美国本土 ACH 银行网将 $14.00 USD 电汇给 Anthropic！
    """

    APPLE_TAX_RATE = 0.30  # 苹果内购抽成 30%

    def __init__(self):
        self.apple_us_bank_cash: float = 0.0              # 美国苹果总公司银行账户里的真实美元现金
        self.unredeemed_card_liability: float = 0.0       # 尚未绑定到 Apple ID 的在外礼品卡负债
        self.apple_id_balances: dict[str, float] = {}     # 美区 Apple ID 账户余额（已绑定的预收款负债）
        self.developer_payables: dict[str, float] = {}    # 应付开发者账款 (如欠 Anthropic 的月度结算款)
        self.net_commission_revenue: float = 0.0          # 苹果净赚的平台佣金（扣除礼品卡批发折让成本）

    def issue_wholesale_gift_card(
        self, code: str, face_value_usd: float, cash_received_usd: float
    ) -> AppleGiftCard:
        """美国苹果收到分销商打来的美元批发款，激活一张面值 $20 的礼品卡，记入预收款负债"""
        self.apple_us_bank_cash = round(self.apple_us_bank_cash + cash_received_usd, 2)
        self.unredeemed_card_liability = round(self.unredeemed_card_liability + face_value_usd, 2)
        return AppleGiftCard(code=code, face_value=face_value_usd, region="US")

    def register_us_apple_id(self, apple_id: str) -> None:
        self.apple_id_balances[apple_id] = 0.0
        print(f"\n[Apple App Store (美区)] 用户已登录美区 Apple ID: [{apple_id}]")

    def redeem_gift_card(self, apple_id: str, gift_card: AppleGiftCard) -> None:
        """用户在美区 App Store 输入兑换码：苹果将负债从“不记名卡池”转移到“用户 Apple ID 账下”"""
        gift_card.redeemed = True
        self.unredeemed_card_liability = round(self.unredeemed_card_liability - gift_card.face_value, 2)
        self.apple_id_balances[apple_id] = round(
            self.apple_id_balances.get(apple_id, 0.0) + gift_card.face_value, 2
        )
        print(
            f"\n[链路第 4 步：美区 Apple ID 充卡 (负债转移)]\n"
            f"  ④ [Apple 内部记账] 兑换码 [{gift_card.code}] 已核验！美国苹果公司将 ${gift_card.face_value:.2f} 预收负债"
            f"绑定至美区账号 [{apple_id}] (当前账户美元余额: ${self.apple_id_balances[apple_id]:.2f} USD)"
        )

    def subscribe_via_iap(self, apple_id: str, merchant: "Merchant", plan_name: str, price: float) -> bool:
        """
        用户在 iOS 端 App 内点击订阅：
        苹果作为 MoR (Merchant of Record 法定卖方) 进行内部负债核销，并向 Anthropic 发送 JWS 签名凭据
        """
        balance = self.apple_id_balances.get(apple_id, 0.0)
        if balance < price:
            return False

        # 内部会计核销分录：借记 Apple ID 负债 -$20 -> 贷记 应付开发者账款 +$14.00 & 苹果佣金 +$6.00
        self.apple_id_balances[apple_id] = round(balance - price, 2)
        apple_gross_tax = round(price * self.APPLE_TAX_RATE, 2)
        developer_share = round(price - apple_gross_tax, 2)
        self.developer_payables[merchant.name] = round(
            self.developer_payables.get(merchant.name, 0.0) + developer_share, 2
        )

        jws_receipt = f"APPLE-JWS-RECEIPT-{random.randint(10000, 99999)}"
        print(
            f"\n[链路第 5 步：iOS 端内购订阅 —— 苹果作为 MoR (法定记录商户) 执行内部负债核销]\n"
            f"  ⑤ [为什么不用过 Stripe？] 在法律上，是【美国苹果公司】把 Claude 会员卖给用户，苹果自己承担100%反欺诈责任！\n"
            f"     · [Apple 内部会计核销] 扣减 [{apple_id}] 余额 -${price:.2f} (无需调用任何银行/卡组织网络，因为美元真钱早在第③步已进苹果美国银行账户)\n"
            f"     · [Apple 内部切分账本] 苹果截留 30% 苹果税 (${apple_gross_tax:.2f}) | 记入《应付开发者 [{merchant.name}] 月度账本》: +${developer_share:.2f} USD\n"
            f"     · [T+0 实时发货通知] 苹果服务器向 [{merchant.name}] 发送官方加密签名收据: {jws_receipt}"
        )
        merchant.activate_by_apple_iap(apple_id, plan_name, jws_receipt)
        return True

    def run_monthly_developer_payout(self, merchant: "Merchant") -> None:
        """【苹果财月月结 T+30~45天】美国苹果总公司通过美国本土 ACH 银行清算网，将 $14.00 USD 真正打入 Anthropic 美国银行卡"""
        payable = self.developer_payables.get(merchant.name, 0.0)
        if payable <= 0:
            return

        self.developer_payables[merchant.name] = 0.0
        self.apple_us_bank_cash = round(self.apple_us_bank_cash - payable, 2)
        merchant.corporate_bank_balance = round(merchant.corporate_bank_balance + payable, 2)

        print(
            f"\n[链路第 6 步：苹果财月月度结算 (T+30天) —— 美元最终流向 Anthropic]\n"
            f"  ⑥ [美国本土 ACH 跨行电汇] 美国苹果总公司 (Apple Inc. US) 从其美国银行账户划出 ${payable:.2f} USD，\n"
            f"     直接打入 [{merchant.name}] 的美国企业对公银行账户！\n"
            f"     📊 最终资金闭环盘点：你付的 ¥145.00 CNY ($20.00) ➔\n"
            f"        支付宝跨境通道拿走 $0.40 | 美国分销商 Pockyt 拿走 $0.20 | 苹果净留存 $5.40 (名义苹果税$6.00 - 批发折让$0.60) | "
            f"{merchant.name} 银行卡实收: ${merchant.corporate_bank_balance:.2f} USD！"
        )


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
