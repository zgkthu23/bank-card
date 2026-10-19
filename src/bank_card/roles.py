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


class CardNetwork:
    """
    全球支付网络 / 卡组织（如 VisaNet）
    核心职责：
    1. BIN 路由表寻址
    2. 【VAA (Visa Advanced Authorization) AI 反欺诈引擎】：
       以卡号哈希为 Key 维护《卡片历史行为画像库》，在报文路过时几毫秒内算出风险分塞进报文提醒发卡行。
    3. T+1 跨行清结算
    """

    SCHEME_FEE_RATE = 0.0015  # 卡组织网络费率：0.15%

    def __init__(self, name: str = "VisaNet"):
        self.name = name
        self.bin_table: dict[str, "IssuerBank"] = {}
        # Visa 大数据集群：pan_hash -> CardBehavioralProfile（刚开卡时这里完全没有记录！）
        self.behavioral_profiles: dict[str, CardBehavioralProfile] = {}
        self.revenue: float = 0.0

    def register_issuer(self, bin_prefix: str, issuer: "IssuerBank") -> None:
        self.bin_table[bin_prefix] = issuer
        issuer.bin_prefix = bin_prefix
        print(
            f"[{self.name}] 已将 BIN [{bin_prefix}] ({issuer.region}·{issuer.scheme}) "
            f"分配给持牌发卡行: {issuer.name}"
        )

    def get_bin_region(self, card_number: str) -> str:
        issuer = self.bin_table.get(card_number[:6])
        return issuer.region if issuer else "UNKNOWN"

    @staticmethod
    def _hash_pan(card_number: str) -> str:
        return hashlib.sha256(card_number.encode()).hexdigest()[:12]

    def _evaluate_and_inject_vaa(self, req: AuthRequest) -> CardBehavioralProfile:
        """
        【核心机制：VAA 实时反欺诈评分计算】
        报文路过 VisaNet 时，根据该卡号过去的历史行为画像计算风险分 (01~99)，并塞进 AuthRequest 报文中
        """
        pan_hash = self._hash_pan(req.card_number)
        masked = f"{req.card_number[:6]}******{req.card_number[-4:]}"
        profile = self.behavioral_profiles.get(pan_hash)

        if not profile:
            profile = CardBehavioralProfile(pan_hash=pan_hash, masked_pan=masked)
            self.behavioral_profiles[pan_hash] = profile
            print(
                f"  🧠 [{self.name} VAA 风控大脑] 首次观测到卡 {masked} 产生交易！\n"
                f"     已在大数据集群新建行为画像 Key={pan_hash} (注: {self.name} 不知道持卡人姓名与卡内余额)"
            )

        score = 10  # 基础低风险分
        reasons: list[str] = []

        # 清理超过 60 分钟窗口的历史时间戳，统计最近 1 小时内的刷卡频次
        recent_1h = [t for t in profile.recent_tx_minutes if 0 <= (req.tx_minute - t) <= 60]

        # 规则 1：最近 1 小时高频刷卡检测
        if len(recent_1h) >= 2:
            score += 20
            reasons.append(f"1小时内高频交易(已刷{len(recent_1h)}次)")

        # 规则 2：黑客 $1 小额探卡 (Carding Probe) 与“先小额测卡、后大额盗刷”模式识别
        if req.amount <= 1.0:
            score += 15
            reasons.append("疑似$1小额探卡测试")
        elif profile.small_probe_count >= 1 and req.amount >= 50.0:
            score += 35
            reasons.append("高危模式:先$1小额探卡后立即大额消费")

        # 规则 3：跨国时空瞬移检测 (Impossible Travel：短时间内在不同国家的商户发生交易)
        if profile.last_country and profile.last_country != req.merchant_country:
            delta_min = req.tx_minute - profile.last_tx_minute
            if delta_min <= 30:
                score += 45
                reasons.append(f"跨国时空瞬移({profile.last_country}->{req.merchant_country} 仅隔{delta_min}分钟)")

        # 规则 4：消费金额相比历史均值剧烈突变
        if profile.approved_tx_count >= 1 and req.amount > profile.avg_amount * 5:
            score += 30
            reasons.append(f"金额突变(${req.amount:.0f} 远超历史均值 ${profile.avg_amount:.2f})")

        # 规则 5：高风险商户类别 (如礼品卡/暗网常洗钱品类 MCC=5999)
        if req.mcc == "5999" and "5999" not in profile.usual_mccs:
            score += 15
            reasons.append("首次涉足高风险礼品卡/加密资产类目(MCC:5999)")

        req.vaa_score = min(score, 99)
        req.vaa_reasons = reasons if reasons else ["行为符合历史画像特征(正常)"]

        risk_level = "🔴极高危" if req.vaa_score >= 80 else ("🟡中风险" if req.vaa_score >= 40 else "🟢低风险")
        print(
            f"  🧠 [{self.name} VAA 评分注入] 耗时 3ms 算出 VAA 风险分: {req.vaa_score:02d}/99 ({risk_level}) "
            f"| 依据: {', '.join(req.vaa_reasons)} ➔ 已塞入 ISO 8583 报文第 48 域！"
        )
        return profile

    def _update_profile_after_tx(self, profile: CardBehavioralProfile, req: AuthRequest, approved: bool) -> None:
        """交易结束后更新 VisaNet 内部的卡片历史行为画像"""
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
        """打印 VisaNet 大数据集群中当前记录的所有卡片画像（展示 Visa 视角能看到什么、看不到什么）"""
        print(f"\n🔍 =================== 【{self.name} 大数据集群：卡片历史行为画像库】 ===================")
        if not self.behavioral_profiles:
            print("  (当前画像库为空：没有任何卡片发生过交易，VisaNet 甚至不知道市面上开了多少张虚拟卡)")
            return

        for pan_hash, p in self.behavioral_profiles.items():
            print(
                f"  📌 画像 Key(PAN Hash): {pan_hash} | 掩码卡号: {p.masked_pan}\n"
                f"     ├─ Visa 已知轨迹: 累计刷卡 {p.total_tx_count} 次 (成功 {p.approved_tx_count} 次) | "
                f"历史均额: ${p.avg_amount:.2f} | 常驻国: {sorted(p.usual_countries)} | 最近交易国: {p.last_country}(T={p.last_tx_minute}m)\n"
                f"     └─ Visa 未知盲区: 持卡人真实身份=[未知] | 卡内剩余可用余额=[未知] | 母账户总额度=[未知]"
            )

    def route_authorization(self, req: AuthRequest) -> AuthResponse:
        """【T+0 实时授权】解析 BIN 寻址 + 实时计算 VAA 风险分塞入报文 + 转发给发卡行"""
        bin_prefix = req.card_number[:6]
        issuer = self.bin_table.get(bin_prefix)

        print(
            f"  -> [{self.name}] 收到 [{req.acquirer_name}] 授权请求 "
            f"(商户国:{req.merchant_country}, MCC:{req.mcc}, 时间:T+{req.tx_minute}m)"
        )
        if not issuer:
            return AuthResponse(approved=False, code="14", message="未知的卡号 BIN")

        # 1. 在路由途中，VisaNet 毫秒级算出 VAA 反欺诈评分并塞进 req 报文
        profile = self._evaluate_and_inject_vaa(req)

        # 2. 将带有 vaa_score 的报文转发给上游发卡行
        print(f"  -> [{self.name}] 携带 VAA 评分({req.vaa_score:02d}) 转发给发卡行 [{issuer.name}]...")
        resp = issuer.authorize(req)

        # 3. 更新该卡在 VisaNet 的历史行为画像
        self._update_profile_after_tx(profile, req, resp.approved)
        return resp

    def clear_and_settle(self, tx: Transaction) -> tuple[float, float]:
        bin_prefix = tx.card_number[:6]
        issuer = self.bin_table[bin_prefix]

        interchange_fee = issuer.settle_frozen_funds(tx.card_number, tx.amount)
        network_fee = round(tx.amount * self.SCHEME_FEE_RATE, 2)
        self.revenue = round(self.revenue + network_fee, 2)

        net_to_acquirer = round(tx.amount - interchange_fee - network_fee, 2)
        print(
            f"  -> [{self.name} 清算中心] 交易 {tx.auth_id} (${tx.amount:.2f}) 跨行划拨完成: "
            f"发卡行分润 ${interchange_fee:.2f} | {self.name} 网络费 ${network_fee:.2f} | "
            f"汇入收单行 ${net_to_acquirer:.2f}"
        )
        return interchange_fee, network_fee


class IssuerBank:
    """上游持牌发卡银行（BIN Sponsor）"""

    INTERCHANGE_FEE_RATE = 0.0175
    BIN_SUSPEND_THRESHOLD = 3
    VAA_BLOCK_THRESHOLD = 80  # 当 VisaNet 传来的 VAA 评分 >= 80 时，发卡行触发反欺诈熔断拦截

    def __init__(self, name: str, region: str, scheme: str = "Visa"):
        self.name = name
        self.region = region
        self.scheme = scheme
        self.bin_prefix: str = ""
        self.cards: dict[str, VirtualCard] = {}
        self.baas_platform = None
        self.revenue: float = 0.0
        self.bin_decline_count: int = 0
        self.bin_suspended: bool = False

    def create_card_record(
        self, owner: str, user_label: str, tier: CardTierConfig, initial_balance: float
    ) -> VirtualCard:
        random_digits = "".join(str(random.randint(0, 9)) for _ in range(10))
        card_number = f"{self.bin_prefix}{random_digits}"
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

    def record_bin_decline(self) -> None:
        self.bin_decline_count += 1
        if self.bin_decline_count >= self.BIN_SUSPEND_THRESHOLD and not self.bin_suspended:
            self.bin_suspended = True
            print(
                f"  🚨 [{self.name} 紧急风控] 警告！卡段 BIN [{self.bin_prefix}] 累计恶意拒付达 "
                f"{self.bin_decline_count} 次，已将整个 [{self.bin_prefix}] 卡段永久封停（全员连坐死卡）！"
            )

    def authorize(self, req: AuthRequest) -> AuthResponse:
        if self.bin_suspended:
            return AuthResponse(approved=False, code="62", message=f"卡段 [{self.bin_prefix}] 已被发卡行整体封停")

        card = self.cards.get(req.card_number)
        if not card:
            return AuthResponse(approved=False, code="14", message="卡号不存在")

        if card.cvv != req.cvv or card.expiry != req.expiry:
            print(f"  <- [{self.name}] HSM 校验失败：CVV 或有效期错误")
            return AuthResponse(approved=False, code="N7", message="CVV 或有效期校验失败")

        # 【核心联动：读取报文中由 VisaNet 塞入的 VAA 反欺诈评分】
        if req.vaa_score >= self.VAA_BLOCK_THRESHOLD:
            print(
                f"  🛡️ [{self.name} 反欺诈熔断] 检测到报文中 Visa VAA 评分高达 {req.vaa_score}/99 "
                f"(阈值 {self.VAA_BLOCK_THRESHOLD})！\n"
                f"     命中高危特征: {req.vaa_reasons}\n"
                f"     尽管黑客提交的 CVV({req.cvv})完全正确且卡内余额(${card.available_balance:.2f})充足，"
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

        return self.baas_platform.handle_auth_webhook(card, req)

    def settle_frozen_funds(self, card_number: str, amount: float) -> float:
        card = self.cards[card_number]
        card.frozen_balance = round(card.frozen_balance - amount, 2)
        interchange_fee = round(amount * self.INTERCHANGE_FEE_RATE, 2)
        self.revenue = round(self.revenue + interchange_fee, 2)
        return interchange_fee


class RoogooPlatform:
    """Roogoo 平台（个人实名 KYC 消费 U 卡）"""

    CARD_TIERS: dict[str, CardTierConfig] = {
        "ENJOY": CardTierConfig(
            tier_code="ENJOY",
            display_name="Roogoo·乐享卡(美国Visa)",
            bin_prefix="428800",
            region="US",
            open_fee=0.0,
            tx_fee_rate=0.01,
            decline_fee=0.50,
            support_3ds=False,
            require_kyc=True,
        ),
        "PREMIER": CardTierConfig(
            tier_code="PREMIER",
            display_name="Roogoo·尊享卡(香港3DS卡)",
            bin_prefix="538720",
            region="HK",
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

    def open_card(self, user_name: str, tier_code: str, load_amount: float) -> VirtualCard:
        tier = self.CARD_TIERS[tier_code]
        total_cost = round(tier.open_fee + load_amount, 2)
        self.user_usd_wallets[user_name] = round(self.user_usd_wallets[user_name] - total_cost, 2)
        self.revenue = round(self.revenue + tier.open_fee, 2)

        sponsor_bank = self.sponsor_banks[tier.bin_prefix]
        card = sponsor_bank.create_card_record(
            owner=user_name, user_label=user_name, tier=tier, initial_balance=load_amount
        )
        print(
            f"[{self.name} 开卡成功] 持卡人: {card.owner} | 卡种: {tier.display_name} | "
            f"卡号: {card.card_number} | CVV: {card.cvv} | 初始余额: ${card.available_balance:.2f}"
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
            f"消费手续费 ${roogoo_tx_fee:.2f} | 卡内剩: ${card.available_balance:.2f}"
        )
        return AuthResponse(approved=True, code="00", message="Approved", auth_id=auth_id)


class SuPayPlatform:
    """SuPay 平台（免实名 No-KYC 企业采购子卡分销台）"""

    CORPORATE_ENTITY = "SuPay Global Ads LLC"
    DEPOSIT_FEE_RATE = 0.02

    CARD_TIERS: dict[str, CardTierConfig] = {
        "SUPAY_MC": CardTierConfig(
            tier_code="SUPAY_MC",
            display_name="SuPay·万事达免实名卡",
            bin_prefix="556789",
            region="US",
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
        return sponsor_bank.create_card_record(
            owner=self.CORPORATE_ENTITY,
            user_label=email,
            tier=tier,
            initial_balance=load_amount,
        )

    def handle_auth_webhook(self, card: VirtualCard, req: AuthRequest) -> AuthResponse:
        sponsor_bank = self.sponsor_banks[card.tier.bin_prefix]
        if card.available_balance < req.amount:
            sponsor_bank.record_bin_decline()
            return AuthResponse(approved=False, code="51", message="余额不足")

        card.available_balance = round(card.available_balance - req.amount, 2)
        card.frozen_balance = round(card.frozen_balance + req.amount, 2)
        auth_id = f"AUTH-{random.randint(1000, 9999)}"
        return AuthResponse(approved=True, code="00", message="Approved", auth_id=auth_id)


class Acquirer:
    """收单机构（如 Stripe）"""

    STRIPE_PERCENT_FEE = 0.029
    STRIPE_FIXED_FEE = 0.30

    def __init__(self, name: str, network: CardNetwork):
        self.name = name
        self.network = network
        self.merchant_accounts: dict[str, MerchantStripeAccount] = {}
        self.pending_transactions: list[Transaction] = []
        self.revenue: float = 0.0

    def register_merchant(self, merchant: "Merchant") -> None:
        self.merchant_accounts[merchant.name] = MerchantStripeAccount(merchant_name=merchant.name)

    def process_payment(
        self,
        merchant: "Merchant",
        card_number: str,
        cvv: str,
        expiry: str,
        amount: float,
        tx_minute: int = 0,
    ) -> AuthResponse:
        card_region = self.network.get_bin_region(card_number)
        if merchant.allowed_regions and card_region not in merchant.allowed_regions:
            return AuthResponse(approved=False, code="62", message=f"不支持 {card_region} 地区银行卡")

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
    """商户（带有所在国家 country 与商户类别码 mcc，供 Visa VAA 风控建立行为画像）"""

    def __init__(
        self,
        name: str,
        acquirer: Acquirer,
        country: str = "US",
        mcc: str = "5734",
        allowed_regions: list[str] | None = None,
        require_3ds: bool = False,
    ):
        self.name = name
        self.acquirer = acquirer
        self.country = country
        self.mcc = mcc
        self.allowed_regions = allowed_regions
        self.require_3ds = require_3ds
        self.corporate_bank_balance: float = 0.0
        acquirer.register_merchant(self)

    def checkout(self, card_number: str, cvv: str, expiry: str, amount: float, tx_minute: int = 0) -> bool:
        print(
            f"\n[{self.name} 收银台 ({self.country}·MCC:{self.mcc}) | 时间:T+{tx_minute}m] "
            f"发起扣款: 卡尾号 {card_number[-4:]} | 金额 ${amount:.2f}"
        )
        resp = self.acquirer.process_payment(self, card_number, cvv, expiry, amount, tx_minute=tx_minute)
        if resp.approved:
            print(f"[{self.name} 收银台] 支付成功！")
            return True
        else:
            print(f"[{self.name} 收银台] 支付被拒：{resp.message}")
            return False


class User:
    """持卡人"""

    def __init__(self, identifier: str):
        self.identifier = identifier
        self.cards: dict[str, VirtualCard] = {}

    def pay(self, merchant: Merchant, card: VirtualCard, amount: float, tx_minute: int = 0) -> bool:
        print(
            f"\n=== [{self.identifier}] 在 [{merchant.name}] 刷卡消费 ${amount:.2f} (时间: T+{tx_minute} 分钟) ==="
        )
        return merchant.checkout(card.card_number, card.cvv, card.expiry, amount, tx_minute=tx_minute)
